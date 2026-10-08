"""M5 后端核心测试：mentions 确定性路由（R30）、导出往返硬标准（R23.3/R29.4/R31.9）。

（R27 自动选项钩子已退役，2026-09-24 起候选全部手动触发——覆盖见 test_m12_r41.py。）"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-m5-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

import pytest  # noqa: E402

from mrp.engines.dsh.process import EngineManager  # noqa: E402
from mrp.engines.fake import FakeEngine  # noqa: E402
from mrp.importers.character_card import import_card_json  # noqa: E402
from mrp.importers.exporters import (  # noqa: E402
    card_to_ccv2_dict,
    export_card_png,
    lorebook_to_st_dict,
)
from mrp.importers.lorebook import import_lorebook_st  # noqa: E402
from mrp.orchestrator.session import SessionRunner  # noqa: E402
from mrp.shared.models import (  # noqa: E402
    Character,
    CharacterCard,
    Lorebook,
    LorebookEntry,
    SessionMeta,
    SessionState,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, session_id: str, event: str, payload: dict, *, lossy: bool = False) -> None:
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


def make_runner(char_b_present: bool = True):
    char_a = Character(
        id="char-a",
        card=CharacterCard(name="测试甲", first_mes="来了。"),
        talkativeness=0.8,
    )
    char_b = Character(
        id="char-b",
        card=CharacterCard(name="测试乙", first_mes="欢迎。"),
        present=char_b_present,
        talkativeness=0.3,
    )
    state = SessionState(
        meta=SessionMeta(id="sess-m5", character_ids=["char-a", "char-b"]),
        characters=[char_a, char_b],
    )
    sink = RecordingSink()
    runner = SessionRunner(
        state,
        EngineManager(engine_factory=lambda: FakeEngine(replies=["好的。"])),
        sink=sink,
        rng_seed=7,
    )
    return runner, sink


# ---------- R30：mentions 确定性路由 ----------


async def test_mentions_route_deterministically():
    runner, sink = make_runner()
    created = await runner.player_say("你们觉得呢？", mentions=["char-b"])
    replies = [m for m in created if m.actor != "player"]
    assert [m.actor for m in replies] == ["char-b"]  # 只有被 @ 的回应
    decision = runner.state.director_log[-1]
    assert decision.trigger == "explicit_mention"
    assert decision.chosen == ["char-b"]


async def test_mentions_order_and_dedup():
    runner, _ = make_runner()
    created = await runner.player_say("都说说", mentions=["char-b", "char-a", "char-b"])
    replies = [m.actor for m in created if m.actor != "player"]
    assert replies == ["char-b", "char-a"]  # 按 @ 顺序、去重
    assert runner.state.director_log[-1].chosen == ["char-b", "char-a"]


async def test_mentions_absent_character_skipped():
    """R30.4：@ 离席角色照选但回合执行时跳过（消息对其不可见）。"""
    runner, _ = make_runner(char_b_present=False)
    created = await runner.player_say("测试乙你怎么看？", mentions=["char-b"])
    replies = [m for m in created if m.actor != "player"]
    assert replies == []  # 离席者不回应
    assert runner.state.director_log[-1].chosen == ["char-b"]  # 决策仍留痕


async def test_no_mentions_falls_back_to_implicit():
    """R30.3：无显式 @ 时退回隐式提及检测。"""
    runner, _ = make_runner()
    created = await runner.player_say("测试甲，今晚排练几点？")  # 文本提及，非 @
    replies = [m.actor for m in created if m.actor != "player"]
    assert replies == ["char-a"]
    assert runner.state.director_log[-1].trigger == "mention"


# ---------- 导出往返硬标准（R23.3 / R29.4 / R31.9） ----------


def test_card_export_json_roundtrip():
    """归一化卡 → CCv2 → 重新导入：核心字段无损（R31.9）。"""
    card = CharacterCard(
        name="测试甲",
        description="合成角色甲简介",
        personality="合成性格标记",
        scenario="合成场景标记",
        first_mes="来了？",
        mes_example="<START>{{user}}: 好 {{char}}: 嗯",
        alternate_greetings=["第二开场白"],
        system_prompt="特殊提示",
        post_history_instructions="约束",
        tags=["校园", "话剧"],
        creator="tester",
        character_version="1.0",
        extensions={"character_book": {"entries": [{"keys": ["x"], "content": "y"}]}},
    )
    exported = card_to_ccv2_dict(card)
    restored = import_card_json(exported)
    for f in ("name", "description", "personality", "scenario", "first_mes", "mes_example"):
        assert getattr(restored, f) == getattr(card, f), f
    assert restored.alternate_greetings == ["第二开场白"]
    assert restored.system_prompt == "特殊提示"
    assert restored.post_history_instructions == "约束"
    assert restored.tags == ["校园", "话剧"]
    # extensions（含 character_book 原文）保留
    assert restored.extensions.get("character_book", {}).get("entries")


def test_lorebook_st_export_roundtrip():
    """统一世界书 → ST → 重新导入：触发字段无损（R29.4 硬标准）。"""
    book = Lorebook(
        id="b1",
        name="测试书",
        entries=[
            LorebookEntry(uid=0, keys=["魔法"], content="存在魔法。", constant=True),
            LorebookEntry(
                uid=1, keys=["邮局"], secondary_keys=["信"], content="七号信箱。",
                anchor="at_depth", depth=2, order=50, probability=80,
            ),
            LorebookEntry(uid=2, keys=["旁白"], content="近端条目。", anchor="near", enabled=False),
        ],
    )
    st = lorebook_to_st_dict(book)
    restored = import_lorebook_st(st)
    assert len(restored.entries) == 3
    e0, e1, e2 = restored.entries
    assert e0.keys == ["魔法"] and e0.constant is True
    assert e1.secondary_keys == ["信"] and e1.anchor == "at_depth" and e1.depth == 2
    assert e1.probability == 80
    assert e2.anchor == "near" and e2.enabled is False  # disable 反向


def test_card_png_export_contains_card():
    """PNG 导出：tEXt 'chara' 可被导入器读回（R23.3）。"""
    import base64
    import io

    from PIL import Image

    card = CharacterCard(name="PNG测试", description="往返")
    character = Character(id="char-x", card=card)
    png_bytes = export_card_png(character)
    img = Image.open(io.BytesIO(png_bytes))
    raw = img.text.get("chara") or img.info.get("chara")
    assert raw, "PNG 缺少 chara tEXt"
    obj = __import__("json").loads(base64.b64decode(raw))
    assert obj["data"]["name"] == "PNG测试"
