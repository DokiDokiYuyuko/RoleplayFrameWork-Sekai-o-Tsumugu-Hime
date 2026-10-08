"""迁移包（bundle.py）单测：zip 打包/扫描往返、坏条目隔离、忽略规则与截断。

全程纯内存 zip（io.BytesIO），不落盘不联网。
"""
from __future__ import annotations

import io
import json
import zipfile

from PIL import Image

from mrp.importers.bundle import build_bundle, scan_bundle
from mrp.importers.exporters import export_card_png
from mrp.shared.models import Character, CharacterCard, Lorebook, LorebookEntry

# ---------- 工具 ----------


def _character(name: str, **card_kwargs) -> Character:
    return Character(card=CharacterCard(name=name, **card_kwargs))


def _card_json(name: str, first_mes: str | None = None) -> bytes:
    """最简 CCv2 卡 JSON（与 export_card_json 同构，供手工造 zip 用）。"""
    return json.dumps(
        {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": name,
                "description": f"{name}的描述",
                "first_mes": first_mes or f"我是{name}。",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _lorebook(n: int = 3) -> Lorebook:
    entries = [
        LorebookEntry(
            uid=i,
            keys=[f"键{i}"],
            content=f"内容{i}",
            enabled=i != 1,  # 留一条禁用条目验证 disable↔enabled 往返
            anchor="at_depth" if i == 1 else "system",
            depth=8 if i == 1 else 4,
        )
        for i in range(n)
    ]
    return Lorebook(name="校园设定", entries=entries)


def _plain_png() -> bytes:
    """无 chara tEXt 的纯图片（模拟普通头像）。"""
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    """手工造 zip：用于注入坏条目 / 被忽略的文件。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for filename, data in files.items():
            zf.writestr(filename, data)
    return buf.getvalue()


# ---------- 往返 ----------


def test_round_trip_two_characters_and_one_lorebook():
    c1 = _character("测试甲", description="合成角色甲简介", first_mes="你好。", aliases=["测试别名甲"])
    c2 = _character("测试乙", description="合成角色乙简介", first_mes="……")
    book = _lorebook(3)

    entries = scan_bundle(build_bundle([c1, c2], [book]))

    assert all(e.error is None for e in entries)
    cards = {e.card.name: e.card for e in entries if e.kind == "character"}
    assert set(cards) == {"测试甲", "测试乙"}
    assert cards["测试甲"].description == "合成角色甲简介"
    assert cards["测试甲"].first_mes == "你好。"
    assert cards["测试乙"].description == "合成角色乙简介"
    assert cards["测试乙"].first_mes == "……"

    books = [e.lorebook for e in entries if e.kind == "lorebook"]
    assert len(books) == 1
    # 书条目数/条目字段一致（ST 导出器不含书 name/description，故只断言条目）
    assert len(books[0].entries) == 3
    e1 = books[0].entries[1]
    assert e1.uid == 1
    assert e1.keys == ["键1"]
    assert e1.content == "内容1"
    assert e1.enabled is False  # disable=True → enabled=False 往返
    assert e1.anchor == "at_depth"
    assert e1.depth == 8


def test_runtime_state_not_part_of_bundle():
    """包内只有卡面 JSON：aliases 等运行时字段不随包迁移（接线层按需侧车）。"""
    character = _character("测试甲", aliases=["测试别名甲", "测试别名甲二"])
    entries = scan_bundle(build_bundle([character], []))

    assert len(entries) == 1
    card = entries[0].card
    assert isinstance(card, CharacterCard)
    assert card.name == "测试甲"
    assert not hasattr(card, "aliases")
    assert "测试别名甲" not in card.model_dump_json()


def test_empty_bundle_is_valid_zip_and_scans_empty():
    assert scan_bundle(build_bundle([], [])) == []


def test_sidecar_is_attached_even_at_scan_item_boundary():
    character = _character("合成边界角色")
    character.aliases = ["合成别名"]
    entries = scan_bundle(build_bundle([character], []), max_items=1)
    assert len(entries) == 1
    assert entries[0].sidecar["aliases"] == ["合成别名"]


# ---------- PNG ----------


def test_png_card_in_zip_yields_card_and_original_bytes():
    character = _character("阿岚", description="旅人")
    png = export_card_png(character)

    entries = scan_bundle(_zip_bytes({"cards/阿岚.png": png}))

    assert len(entries) == 1
    e = entries[0]
    assert e.kind == "character"
    assert e.error is None
    assert e.card.name == "阿岚"
    assert e.card.description == "旅人"
    assert e.avatar_png == png  # 原字节原样携带


def test_build_bundle_with_avatar_png_round_trips():
    character = _character("阿岚", description="旅人")
    png = export_card_png(character)

    entries = scan_bundle(build_bundle([character], [], avatars={character.id: png}))
    by_path = {e.filename: e for e in entries}

    assert set(by_path) == {
        f"characters/{character.id}.json",
        f"characters/{character.id}/avatar.png",
    }
    card_entry = by_path[f"characters/{character.id}.json"]
    assert card_entry.card.name == "阿岚"
    assert card_entry.avatar_png is None  # JSON 卡条目不带头像字节
    avatar_entry = by_path[f"characters/{character.id}/avatar.png"]
    assert avatar_entry.avatar_png == png
    assert avatar_entry.card is not None  # 头像位恰好是 PNG 卡时顺带产出卡


def test_plain_avatar_without_card_is_asset_not_error():
    """自带包里的纯头像 PNG（无 chara chunk）：unsupported + 原字节，不报 error。"""
    character = _character("阿岚")
    plain = _plain_png()

    entries = scan_bundle(_zip_bytes({f"characters/{character.id}/avatar.png": plain}))

    assert len(entries) == 1
    e = entries[0]
    assert e.kind == "unsupported"
    assert e.error is None
    assert e.avatar_png == plain


def test_stray_png_without_card_is_an_error():
    entries = scan_bundle(_zip_bytes({"cards/photo.png": _plain_png()}))

    assert len(entries) == 1
    assert entries[0].error
    assert entries[0].card is None


# ---------- 坏条目隔离 / 坏 zip ----------


def test_bad_entries_isolated_from_good_ones():
    data = _zip_bytes(
        {
            "cards/good.json": _card_json("好卡"),
            "cards/broken.json": b"{ not json at all",
            "cards/not-a-card.json": b'{"foo": 1}',
            "cards/broken.png": b"\x89PNG\r\n\x1a\n truncated garbage",
        }
    )
    entries = {e.filename: e for e in scan_bundle(data)}

    assert len(entries) == 4
    good = entries["cards/good.json"]
    assert good.error is None
    assert good.card.name == "好卡"
    for name in ("cards/broken.json", "cards/not-a-card.json", "cards/broken.png"):
        bad = entries[name]
        assert bad.error, name
        assert bad.card is None and bad.lorebook is None


def test_bad_zip_returns_empty_list():
    assert scan_bundle(b"not a zip") == []
    assert scan_bundle(b"") == []


# ---------- 忽略规则 / 截断 / manifest ----------


def test_ignored_files_produce_no_entries():
    files = {
        "__MACOSX/._hero.json": _card_json("影子"),
        "cards/.hidden.json": _card_json("隐藏"),
        "cards/.DS_Store": b"junk",
        ".dotdir/card.json": _card_json("点目录"),
        "../escape.json": _card_json("zip-slip"),
        "readme.txt": b"hello",
        "cards/hero.png.bak": b"junk",
        "cards/a.json": _card_json("甲"),
        "cards/b.json": _card_json("乙"),
    }

    names = [e.filename for e in scan_bundle(_zip_bytes(files))]

    assert names == ["cards/a.json", "cards/b.json"]  # 排序稳定；忽略项不产生条目


def test_max_items_truncates():
    files = {f"cards/{i}.json": _card_json(f"角色{i}") for i in range(5)}
    data = _zip_bytes(files)

    assert len(scan_bundle(data)) == 5
    assert [e.card.name for e in scan_bundle(data, max_items=2)] == ["角色0", "角色1"]
    assert scan_bundle(data, max_items=0) == []


def test_manifest_is_package_metadata_not_scanned():
    character = _character("甲")
    data = build_bundle([character], [])

    # 先确认 manifest 确实在包里（否则本测试是空转）
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "manifest.json" in zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["version"] == 1
    assert manifest["characters"] == 1
    assert manifest["lorebooks"] == 0
    assert manifest["exported_at"]

    # 根 manifest 不产生条目：自带包导出再导入不该多一条"无法识别"噪音
    entries = scan_bundle(data)
    assert [e.filename for e in entries] == [f"characters/{character.id}.json"]


def test_sidecar_roundtrip_preserves_runtime_fields():
    """侧车无损（2026-09-25 补）：别名/主动性/采样配置/世界书绑定、书 name 往返；侧车不产生独立条目。"""
    char = Character(
        id="char-x",
        card=CharacterCard(name="甲", description="描述", first_mes="嗨"),
        aliases=["小甲", "甲甲"],
        talkativeness=0.8,
        interject_enabled=True,
        followup_enabled=True,
        bound_lorebook_ids=["book-y"],
    )
    book = Lorebook(
        id="book-y",
        name="设定集",
        description="世界观背景",
        entries=[LorebookEntry(uid=0, key=["魔法"], content="失传的魔法。")],
    )
    entries = scan_bundle(build_bundle([char], [book]))

    assert len(entries) == 2  # 侧车不产生独立条目
    ce = next(e for e in entries if e.kind == "character")
    assert ce.sidecar is not None
    assert ce.sidecar["aliases"] == ["小甲", "甲甲"]
    assert ce.sidecar["talkativeness"] == 0.8
    assert ce.sidecar["interject_enabled"] is True
    assert ce.sidecar["followup_enabled"] is True
    assert ce.sidecar["llm"]  # 采样配置整体带上
    assert ce.sidecar["bound_lorebook_ids"] == ["book-y"]

    be = next(e for e in entries if e.kind == "lorebook")
    assert be.sidecar is not None
    assert be.sidecar["name"] == "设定集"
    assert be.sidecar["description"] == "世界观背景"


def test_bad_sidecar_ignored():
    """坏侧车静默忽略：主条目照常解析（迁移不因侧车损坏而失败）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("characters/c1.json", _card_json("丙"))
        zf.writestr("characters/c1.mrp.json", b"{not json")
    entries = scan_bundle(buf.getvalue())
    assert len(entries) == 1
    assert entries[0].kind == "character" and entries[0].card.name == "丙"
    assert entries[0].sidecar is None
