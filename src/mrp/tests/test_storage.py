"""W2 存储层测试：原子写 / 索引重建与惰性失效 / 磁盘格式兼容 / 并发 / 迁移。

线程与 IO 全部走真实文件（tmp_path），不 mock 文件系统；
唯一 mock 的场景是"os.replace 失败"中断原子写。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import shutil
from pathlib import Path

import pytest

from mrp.orchestrator.migration import migrate_save_dict, migrate_state_dict
from mrp.shared.models import (
    Character,
    CharacterCard,
    Lorebook,
    LorebookEntry,
    Message,
    SaveFile,
    SessionMeta,
    SessionState,
)
from mrp.storage import (
    AppPaths,
    CharacterRegistry,
    LorebookRegistry,
    SaveRepo,
    SessionRepo,
    read_json,
    write_json_atomic,
    default_data_root,
    validate_data_root,
)
from mrp.storage.session_repo import _CrossLoopLock
from mrp.storage import atomic as atomic_mod
from mrp.storage import session_repo as session_repo_mod
from mrp.storage.json_store import FileStamp

FIXTURES = Path(__file__).resolve().parent / "fixtures"

V1_SESSION_FIXTURE = FIXTURES / "synthetic-session-v1.json"


# ---------- 工具 ----------


def make_state(sid: str = "sess-t1", n: int = 3, title: str = "标题", persona: str = "人设") -> SessionState:
    meta = SessionMeta(id=sid, title=title, character_ids=["char-a"], player_persona=persona)
    msgs = [
        Message(
            id=f"msg-{i:04d}",
            session_id=sid,
            seq=i,
            turn=i // 2 + 1,
            actor="player" if i % 2 == 0 else "char-a",
            content=f"内容 {i}",
        )
        for i in range(n)
    ]
    return SessionState(schema_version=2, meta=meta, messages=msgs)


def make_v1_session_raw(sid: str = "sess-old", n: int = 2) -> dict:
    """v1 会话原始 dict（与 app.py 迁移前的真实磁盘格式一致：无 scenes/scene_id）。"""
    raw = json.loads(make_state(sid=sid, n=n).model_dump_json())
    raw["schema_version"] = 1
    raw.pop("scenes", None)
    raw.pop("active_scene_id", None)
    for m in raw["messages"]:
        m.pop("scene_id", None)
    return raw


@pytest.fixture()
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "data").ensure()


# ---------- 1. paths ----------


def test_paths_layout_matches_legacy_constants(tmp_path: Path) -> None:
    root = tmp_path / "data"
    p = AppPaths(root)
    assert p.characters_dir == root / "characters"
    assert p.lorebooks_dir == root / "lorebooks"
    assert p.sessions_dir == root / "sessions"
    assert p.saves_dir == root / "saves"
    assert p.memory_dir == root / "memories"
    assert p.avatars_dir == p.characters_dir  # 头像落在 characters/<id>/
    assert p.settings_file == root / "settings.json"
    assert p.sessions_index_file == root / "sessions" / "index.json"
    assert p.saves_index_file == root / "saves" / "index.json"
    assert not root.exists()
    assert p.ensure() is p
    for d in (p.characters_dir, p.lorebooks_dir, p.sessions_dir, p.saves_dir, p.memory_dir):
        assert d.is_dir()


def test_default_data_root_respects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MRP_DATA_ROOT", str(tmp_path / "custom"))
    assert AppPaths.default().data_root == tmp_path / "custom"


def test_default_data_root_without_override_is_outside_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MRP_DATA_ROOT", raising=False)
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    elif sys.platform == "darwin":
        monkeypatch.setenv("HOME", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    root = default_data_root()
    if sys.platform == "darwin":
        assert root.is_relative_to((tmp_path / "Library" / "Application Support").resolve())
    else:
        assert root.is_relative_to(tmp_path.resolve())
    assert not root.is_relative_to(Path(__file__).resolve().parents[3])


def test_data_root_inside_code_is_rejected(tmp_path: Path) -> None:
    code = tmp_path / "code"
    with pytest.raises(ValueError, match="outside the code directory"):
        validate_data_root(code / "data", code)


# ---------- 2. 原子写 ----------


def test_atomic_write_creates_parents_and_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.json"  # 父目录不存在 → 自动建
    write_json_atomic(target, {"x": 1, "中文": ["值"]}, indent=None)
    assert read_json(target) == {"x": 1, "中文": ["值"]}
    write_json_atomic(target, {"x": [1, 2]}, indent=2)
    assert "\n" in target.read_text(encoding="utf-8")
    assert read_json(target) == {"x": [1, 2]}


def test_atomic_write_failure_keeps_old_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "s.json"
    write_json_atomic(target, {"v": 1})
    before = target.read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_mod.os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(target, {"v": 2})
    assert target.read_bytes() == before  # 旧内容完好
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []  # 无半文件/tmp 残留


def test_read_json_bad_or_missing_returns_default(tmp_path: Path) -> None:
    assert read_json(tmp_path / "nope.json", default={"d": 1}) == {"d": 1}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_json(bad, default=None) is None
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"\xff\xfe\x00")
    assert read_json(raw, default="fallback") == "fallback"


# ---------- 3. 会话仓储：格式兼容 ----------


async def test_session_roundtrip_is_legacy_byte_compatible(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    state = make_state(n=4)
    summary = await repo.save_state(state)
    path = paths.sessions_dir / "sess-t1.json"
    saved_mtime = path.stat().st_mtime
    # 新代码写出的正文与旧 app.py 的 persist_session 逐字节一致
    assert path.read_text(encoding="utf-8") == state.model_dump_json()
    # 旧代码（无索引）可直接读
    legacy = SessionState.model_validate_json(path.read_text(encoding="utf-8"))
    assert legacy == state
    loaded = await repo.load_state("sess-t1")
    assert loaded is not None and loaded.schema_version == 3
    assert [m.id for m in loaded.messages] == [m.id for m in state.messages]
    assert summary.messages == 4
    assert summary.turn == state.current_turn()
    assert summary.updated_at == saved_mtime
    assert await repo.load_state("sess-不存在") is None


async def test_session_save_targets_succeed_in_plain_mode(paths: AppPaths) -> None:
    card = CharacterCard(name="甲")
    char = Character(card=card)
    reg = CharacterRegistry(paths)
    await reg.save(char)
    assert (paths.characters_dir / f"{char.id}.json").read_text(encoding="utf-8") == char.model_dump_json()

    book = Lorebook(name="书", entries=[LorebookEntry(uid=1, content="内容")])
    lreg = LorebookRegistry(paths)
    await lreg.save(book)
    assert (paths.lorebooks_dir / f"{book.id}.json").read_text(encoding="utf-8") == book.model_dump_json()


# ---------- 4. 索引：重建 / 惰性失效 / 不解析正文 ----------


async def test_list_does_not_parse_session_bodies(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(n=3))

    def boom(_raw):
        raise AssertionError("列表路径不应解析会话体")

    monkeypatch.setattr(session_repo_mod, "validate_state", boom)
    rows = await repo.list_summaries()
    assert len(rows) == 1
    assert rows[0].messages == 3
    assert rows[0].title == "标题"


async def test_index_rebuild_after_delete(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(sid="sess-a", n=2, title="A"))
    await repo.save_state(make_state(sid="sess-b", n=5, title="B"))
    index = paths.sessions_index_file
    assert index.is_file()
    index.unlink()

    rows = await SessionRepo(paths).list_summaries()  # 新实例，强制回退扫目录重建
    assert {r.id for r in rows} == {"sess-a", "sess-b"}
    assert {r.id: r.messages for r in rows} == {"sess-a": 2, "sess-b": 5}
    assert index.is_file()  # 已写回
    # 列表按 updated_at 倒序（与旧 list_sessions 一致）
    assert [r.updated_at for r in rows] == sorted((r.updated_at for r in rows), reverse=True)


async def test_index_rebuild_after_corruption_and_legacy_migrator_safe(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(sid="sess-a", n=3))
    paths.sessions_index_file.write_text("{oops", encoding="utf-8")

    rows = await repo.list_summaries()
    assert [r.id for r in rows] == ["sess-a"]

    # 旧 app.py 把 index.json 当会话加载时：迁移器见 schema_version>=2 直接跳过，
    # 不会改写索引（也不会生成 index.json.v1.bak）
    raw = json.loads(paths.sessions_index_file.read_text(encoding="utf-8"))
    migrated, changed = migrate_state_dict(raw)
    assert changed is False and migrated is raw
    assert not paths.sessions_index_file.with_suffix(".json.v1.bak").exists()
    assert all(r.id != "index" for r in rows)


async def test_index_lazy_invalidation_on_external_write(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(n=2))
    assert (await repo.list_summaries())[0].messages == 2

    # 外部（旧代码/手改）改文件：内容更长 → size/mtime_ns 变化
    bigger = make_state(n=7, title="外部改过")
    (paths.sessions_dir / "sess-t1.json").write_text(bigger.model_dump_json(), encoding="utf-8")

    rows = await SessionRepo(paths).list_summaries()
    assert len(rows) == 1
    assert rows[0].messages == 7
    assert rows[0].title == "外部改过"
    # 索引已回写修正（重新读盘验证）
    on_disk = json.loads(paths.sessions_index_file.read_text(encoding="utf-8"))
    assert on_disk["sessions"]["sess-t1"]["messages"] == 7
    assert on_disk["index_schema"] == 3
    assert on_disk["schema_version"] == 2  # 兼容守卫


async def test_index_not_rewritten_when_fresh(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    """索引命中时列表纯读：第二次调用不得触发任何索引写（否则每次列表都是写放大）。"""
    repo = SessionRepo(paths)
    await repo.save_state(make_state(n=3))
    rows = await repo.list_summaries()
    assert len(rows) == 1

    def boom(*_a, **_k):
        raise AssertionError("索引新鲜时不应写盘")

    monkeypatch.setattr(session_repo_mod, "write_json_atomic", boom)
    again = await repo.list_summaries()
    assert [r.id for r in again] == ["sess-t1"]


async def test_index_drops_row_when_file_deleted(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(sid="sess-a"))
    (paths.sessions_dir / "sess-a.json").unlink()
    rows = await SessionRepo(paths).list_summaries()
    assert rows == []
    on_disk = json.loads(paths.sessions_index_file.read_text(encoding="utf-8"))
    assert on_disk["sessions"] == {}


async def test_list_tolerates_corrupt_session_file(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(sid="sess-good", n=2))
    (paths.sessions_dir / "sess-broken.json").write_text("{broken", encoding="utf-8")
    rows = await repo.list_summaries()
    assert [r.id for r in rows] == ["sess-good"]


async def test_delete_state_removes_body_and_row(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    await repo.save_state(make_state(sid="sess-a"))
    assert await repo.delete_state("sess-a") is True
    assert not (paths.sessions_dir / "sess-a.json").exists()
    assert await repo.list_summaries() == []
    assert await repo.delete_state("sess-a") is False


# ---------- 5. 迁移（v1→v2，版本化备份） ----------


async def test_v1_session_migrates_with_versioned_backup(paths: AppPaths) -> None:
    dst = paths.sessions_dir / "sess-synthetic-v1.json"
    shutil.copyfile(V1_SESSION_FIXTURE, dst)
    original = dst.read_bytes()

    state = await SessionRepo(paths).load_state("sess-synthetic-v1")
    assert state is not None
    assert state.schema_version == 3
    assert state.scenes and state.active_scene_id == state.scenes[0].id
    assert all(m.scene_id == state.active_scene_id for m in state.messages)
    # 备份名带版本号，内容为迁移前原文
    bak = paths.sessions_dir / "sess-synthetic-v1.json.v1.bak"
    assert bak.read_bytes() == original
    # 迁移结果已落盘且符合 models.py
    assert json.loads(dst.read_text(encoding="utf-8"))["schema_version"] == 3


async def test_migration_backup_never_overwritten(paths: AppPaths) -> None:
    dst = paths.sessions_dir / "sess-synthetic-v1.json"
    shutil.copyfile(V1_SESSION_FIXTURE, dst)
    bak = paths.sessions_dir / "sess-synthetic-v1.json.v1.bak"
    bak.write_text("SENTINEL", encoding="utf-8")

    await SessionRepo(paths).load_state("sess-synthetic-v1")
    assert bak.read_text(encoding="utf-8") == "SENTINEL"


async def test_migrate_if_needed_reports_change(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    dst = paths.sessions_dir / "sess-old.json"
    dst.write_text(json.dumps(make_v1_session_raw("sess-old")), encoding="utf-8")
    assert await repo.migrate_if_needed("sess-old") is True
    assert await repo.migrate_if_needed("sess-old") is False  # 幂等
    assert await repo.migrate_if_needed("sess-missing") is False


# ---------- 6. 存档仓储 ----------


async def test_save_create_list_load_delete(paths: AppPaths) -> None:
    repo = SaveRepo(paths)
    state = make_state(n=3)
    save_id, save = await repo.create(state, "我的存档")
    assert save_id == "save-sess-t1-3"  # 旧 id 语义
    assert (paths.saves_dir / f"{save_id}.json").exists()
    # 旧代码可读

    legacy = SaveFile.model_validate_json((paths.saves_dir / f"{save_id}.json").read_text(encoding="utf-8"))
    assert legacy.state == state and legacy.name == "我的存档"

    rows = await repo.list(session_id="sess-t1")
    assert len(rows) == 1
    row = rows[0]
    assert (row.id, row.session_id, row.name) == (save_id, "sess-t1", "我的存档")
    assert row.turn == state.current_turn() and row.message_count == 3
    assert row.created_at == save.created_at  # create 返回值即 list 行
    assert await repo.list(session_id="sess-other") == []

    loaded = await repo.load(save_id)
    assert loaded is not None and loaded.state.schema_version == 3
    assert [m.id for m in loaded.state.messages] == [m.id for m in state.messages]
    assert await repo.delete(save_id) is True
    assert await repo.load(save_id) is None
    assert await repo.list() == []


async def test_save_duplicate_length_never_overwrites(paths: AppPaths) -> None:
    repo = SaveRepo(paths)
    id1, _ = await repo.create(make_state(n=2, title="A"), "甲")
    id2, _ = await repo.create(make_state(n=2, title="B"), "乙")
    id3, _ = await repo.create(make_state(n=2, title="C"), "丙")
    assert (id1, id2, id3) == ("save-sess-t1-2", "save-sess-t1-2-2", "save-sess-t1-2-3")
    assert (await repo.load(id1)).name == "甲"  # 第一份未被覆盖
    assert (await repo.load(id2)).state.meta.title == "B"
    assert {r.id for r in await repo.list()} == {id1, id2, id3}


async def test_save_list_tolerates_bad_files_and_stale_rows(paths: AppPaths) -> None:
    repo = SaveRepo(paths)
    good_id, _ = await repo.create(make_state(n=2), "好档")
    # 坏文件（无索引行）→ 跳过，不抛
    (paths.saves_dir / "save-broken-1.json").write_text("{broken", encoding="utf-8")
    # 幽灵索引行（文件不存在）→ 清理，不抛
    idx = json.loads(paths.saves_index_file.read_text(encoding="utf-8"))
    idx["saves"]["save-ghost-9"] = dict(idx["saves"][good_id], id="save-ghost-9")
    paths.saves_index_file.write_text(json.dumps(idx), encoding="utf-8")

    rows = await SaveRepo(paths).list()
    assert [r.id for r in rows] == [good_id]
    on_disk = json.loads(paths.saves_index_file.read_text(encoding="utf-8"))
    assert "save-ghost-9" not in on_disk["saves"]
    assert "save-broken-1" not in on_disk["saves"]


async def test_save_load_migrates_v1_with_backup(paths: AppPaths) -> None:
    raw = {
        "schema_version": 1,
        "name": "旧档",
        "state": make_v1_session_raw("sess-old", n=2),
        "saved_at": "2026-09-23T00:00:00Z",
    }
    text = json.dumps(raw, ensure_ascii=False)
    dst = paths.saves_dir / "save-old-1.json"
    dst.write_text(text, encoding="utf-8")

    save = await SaveRepo(paths).load("save-old-1")
    assert save is not None
    assert save.schema_version == 3 and save.state.schema_version == 3
    assert save.state.scenes
    assert (paths.saves_dir / "save-old-1.json.v1.bak").read_text(encoding="utf-8") == text
    migrated, changed = migrate_save_dict(json.loads(dst.read_text(encoding="utf-8")))
    assert changed is False  # 已是 v2


async def test_api_dict_matches_legacy_response_keys(paths: AppPaths) -> None:
    """api_dict()保留 worldline 会话元数据，同时不泄漏索引内部 stamp 字段。"""
    repo = SessionRepo(paths)
    state = make_state(n=2)
    await repo.save_state(state)
    sess = (await repo.list_summaries())[0].api_dict()
    assert list(sess) == [
        "id", "title", "created_at", "source_world_id", "prompt_preset_id", "story_id",
        "branch_name", "parent_branch_id", "branch_revision", "archived",
        "character_ids", "character_names", "persona", "player_character_id", "reply_max_tokens",
        "turn", "messages",
        "options_enabled", "options_style", "options_direct_send", "streaming_enabled",
        "hygiene_enabled", "director_mode", "narrative_pov", "response_style_id", "response_style_overrides", "narrative_density",
        "short_input_padding", "proactive_turn_limit", "updated_at",
    ]
    assert sess["created_at"] == state.meta.created_at.isoformat()
    assert sess["updated_at"] == (paths.sessions_dir / "sess-t1.json").stat().st_mtime

    srepo = SaveRepo(paths)
    save_id, _ = await srepo.create(state, "档")
    row = (await srepo.list())[0].api_dict()
    assert list(row) == ["id", "session_id", "name", "created_at", "turn", "message_count"]
    assert row["id"] == save_id and row["name"] == "档"


async def test_cross_loop_lock_releases_worker_acquisition_after_cancel() -> None:
    lock = _CrossLoopLock()
    held = asyncio.Event()
    release = asyncio.Event()

    async def owner() -> None:
        async with lock:
            held.set()
            await release.wait()

    first = asyncio.create_task(owner())
    await held.wait()
    waiter = asyncio.create_task(lock.__aenter__())
    await asyncio.sleep(0.02)  # allow the worker thread to block on the held lock
    waiter.cancel()
    release.set()
    await first
    with pytest.raises(asyncio.CancelledError):
        await waiter
    # A cancelled to_thread acquisition must not leave the underlying lock held.
    async with asyncio.timeout(1):
        async with lock:
            pass


async def test_save_empty_name_falls_back_to_session_title(paths: AppPaths) -> None:
    repo = SaveRepo(paths)
    state = make_state(n=1, title="会话标题")
    save_id, save = await repo.create(state, "   ")
    assert save.name == "会话标题"
    assert (await repo.list())[0].name == "会话标题"


# ---------- 7. 注册表（quarantine / 头像清理） ----------


async def test_character_registry_quarantine_and_good_items(paths: AppPaths, caplog: pytest.LogCaptureFixture) -> None:
    reg = CharacterRegistry(paths)
    good = Character(card=CharacterCard(name="好人"))
    await reg.save(good)
    (paths.characters_dir / "char-broken.json").write_text("{oops", encoding="utf-8")
    (paths.characters_dir / "char-empty.json").write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="mrp.storage.registry"):
        items = await reg.load_all()
    assert [c.id for c in items] == [good.id]
    assert reg.by_id[good.id].card.name == "好人"
    assert len(reg.quarantine) == 2
    assert {p.name for p, _ in reg.quarantine} == {"char-broken.json", "char-empty.json"}
    assert "已隔离" in caplog.text


async def test_lorebook_registry_quarantine(paths: AppPaths) -> None:
    reg = LorebookRegistry(paths)
    good = Lorebook(name="好书", entries=[LorebookEntry(uid=1, content="c")])
    await reg.save(good)
    (paths.lorebooks_dir / "book-bad.json").write_text("[1,2,3]", encoding="utf-8")
    items = await reg.load_all()
    assert [b.id for b in items] == [good.id]
    assert len(reg.quarantine) == 1
    # reload_one：好条目更新，坏条目进隔离且不影响 by_id
    assert (await reg.reload_one(good.id)) is not None
    (paths.lorebooks_dir / "book-bad.json").write_text("null", encoding="utf-8")
    assert await reg.reload_one("book-bad") is None
    assert len(reg.quarantine) == 2
    assert good.id in reg.by_id


async def test_character_delete_cleans_avatar_dir(paths: AppPaths) -> None:
    reg = CharacterRegistry(paths)
    char = Character(card=CharacterCard(name="头像人"))
    char.card.avatar_path = f"{char.id}/avatar.png"
    await reg.save(char)
    avatar_dir = reg.avatar_dir(char.id)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    (avatar_dir / "avatar.png").write_bytes(b"\x89PNG")
    assert await reg.reload_one(char.id) is not None

    assert await reg.delete(char.id) is True
    assert not (paths.characters_dir / f"{char.id}.json").exists()
    assert not avatar_dir.exists()
    assert char.id not in reg.by_id
    assert await reg.delete(char.id) is False


def test_registry_rejects_traversal_ids(paths: AppPaths) -> None:
    reg = CharacterRegistry(paths)
    with pytest.raises(ValueError):
        reg.delete_sync("../escape")
    char = Character(card=CharacterCard(name="x"))
    char.id = "../escape"
    with pytest.raises(ValueError):
        reg.save_sync(char)
    repo = SessionRepo(paths)
    with pytest.raises(ValueError):
        repo.path_for("../escape")


async def test_load_state_rejects_traversal_id(paths: AppPaths) -> None:
    assert await SessionRepo(paths).load_state("../escape") is None
    assert await SaveRepo(paths).load("../escape") is None


# ---------- 8. 并发 ----------


async def test_concurrent_save_state_same_session_no_interleave(paths: AppPaths) -> None:
    repo = SessionRepo(paths)
    states = [make_state(n=i + 1, title=f"t{i}") for i in range(10)]
    await asyncio.gather(*(repo.save_state(s) for s in states))

    body = paths.sessions_dir / "sess-t1.json"
    loaded = SessionState.model_validate_json(body.read_text(encoding="utf-8"))  # 完整可解析
    assert len(loaded.messages) in {len(s.messages) for s in states}  # 某个完整状态，非交错
    assert [p.name for p in paths.sessions_dir.iterdir() if p.name.endswith(".tmp")] == []
    # 索引行与实际文件一致（stamp 匹配 → 下次列表无需重读）
    rows = await repo.list_summaries()
    assert len(rows) == 1
    assert rows[0].messages == len(loaded.messages)
    assert rows[0].mtime_ns == FileStamp.of(body).mtime_ns


async def test_concurrent_save_create_same_session_unique_ids(paths: AppPaths) -> None:
    repo = SaveRepo(paths)
    results = await asyncio.gather(
        *(repo.create(make_state(n=2, title=f"t{i}"), f"n{i}") for i in range(6))
    )
    ids = [sid for sid, _ in results]
    assert len(set(ids)) == 6  # 全唯一，无覆盖
    assert {r.id for r in await repo.list()} == set(ids)


