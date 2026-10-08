"""引擎层单测：profile 生成、prompt 组装、FakeEngine、签名缓存。"""
from __future__ import annotations

from pathlib import Path

from mrp.engines.dsh.profile import (
    character_home,
    write_character_profile,
)
from mrp.engines.fake import FakeEngine
from mrp.shared.models import (
    Character,
    CharacterCard,
    Injection,
    Message,
    TurnContext,
)
from mrp.shared.prompt import compose_prompt, estimate_tokens, persona_from_card


def make_char(name: str = "测试甲") -> Character:
    return Character(id="char-a", card=CharacterCard(name=name, description="合成角色甲简介"))


def test_profile_files(tmp_path: Path):
    home = character_home("char-a", root=tmp_path)
    pdir = write_character_profile(home)
    assert (pdir / "package.json").exists()
    assert (pdir / "cordis.patch.yml").exists()
    patch = (pdir / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "persistent-bash" in patch and "disabled: true" in patch
    pkg = (pdir / "package.json").read_text(encoding="utf-8")
    assert "dsh-sdk-minimal" in pkg


def test_persona_from_card():
    card = CharacterCard(
        name="测试甲", description="合成角色甲属性", personality="合成性格标记", scenario="合成测试场所"
    )
    persona = persona_from_card(card)
    assert "测试甲" in persona and "合成角色甲属性" in persona and "合成性格标记" in persona
    assert "扮演卡片中的角色" in persona  # 工具边界与身份边界保持明确


def test_compose_prompt_sections_and_budget():
    msgs = [
        Message(session_id="s", seq=i, turn=0, actor="player" if i % 2 == 0 else "char-b", content=f"消息{i}" * 30)
        for i in range(20)
    ]
    injections = [
        Injection(source="lorebook", entry_id="e1", content="世界观" * 80, anchor="system", order=100),
        Injection(source="memory", entry_id="m1", content="上周他失约了" * 50),
        Injection(source="lorebook", entry_id="e2", content="临别赠言" * 10, anchor="near"),
    ]
    ctx = TurnContext(
        session_id="s", character_id="char-a", turn=3,
        visible_messages=msgs, injections=injections, budget_tokens=2048,
    )
    out = compose_prompt(ctx)
    assert "[世界设定]" in out.text and "相关记忆" in out.text and "=== 对话记录 ===" in out.text
    assert "=== 现在轮到你 ===" in out.text
    # 世界书预算 25% = 512 tokens；这条设定在预算内，应进入 prompt。
    assert out.tokens_by_section["history"] > 0
    assert out.total_tokens > 0
    # 注入 token 回填
    assert injections[0].tokens > 0


def test_compose_prompt_drops_oldest_history():
    """历史超预算从最旧丢弃并插入占位符。"""
    msgs = [
        Message(session_id="s", seq=i, turn=0, actor="player", content=f"很长的一条消息{i}" * 15)
        for i in range(30)
    ]
    ctx = TurnContext(session_id="s", character_id="char-a", turn=1, visible_messages=msgs, budget_tokens=1024)
    out = compose_prompt(ctx)
    assert "[更早对话已省略]" in out.text
    assert "消息29" in out.text  # 最新消息必须保留


def test_fake_engine_records_context():
    import asyncio

    async def run():
        eng = FakeEngine(replies=["你好", "再见"], delay=0.01)
        ch = make_char()
        await eng.start(ch)
        ctx = TurnContext(session_id="s", character_id=ch.id, turn=1)
        r1 = await eng.generate(ctx)
        r2 = await eng.generate(ctx)
        await eng.stop()
        assert r1.content == "你好" and r2.content == "再见"
        assert len(eng.calls) == 2  # TurnContext 被记录——集成断言依据
        assert not await eng.is_alive()

    asyncio.run(run())


# ---------- R45：重跑同回合的 dsh 会话 id 冲突回退 ----------


def test_dsh_engine_session_collision_fallback():
    """重跑同回合（swipe/续写/重新生成）→ dsh 报 already exists → 自动加后缀重试。"""
    import asyncio
    from types import SimpleNamespace

    from mrp.engines.dsh.engine import DshEngine

    class Harness:
        def __init__(self) -> None:
            self.ids: list[str] = []

        def run(self, text: str, session_id: str):
            self.ids.append(session_id)
            if len(self.ids) == 1:  # 首次：模拟 dsh 会话已存在
                raise RuntimeError(f'session "{session_id}" already exists')
            return SimpleNamespace(final_response="回退后的回复", finish_reason="stop", events=[])

    async def run_case():
        engine = DshEngine()
        harness = Harness()
        engine._harness = harness
        engine._character = make_char()
        ctx = TurnContext(session_id="sess-x", character_id="char-a", turn=7)
        reply = await engine.generate(ctx)
        return harness, reply

    harness, reply = asyncio.run(run_case())
    base = "sess-x-char-a-0007"
    assert reply.content == "回退后的回复"
    assert harness.ids[0] == base
    assert len(harness.ids) == 2
    assert harness.ids[1].startswith(base + "-") and harness.ids[1] != base
    assert reply.engine_session_id == harness.ids[1]


def test_dsh_engine_other_errors_not_retried():
    """非会话冲突的异常不触发重试（单次调用即上抛）。"""
    import asyncio

    from mrp.engines.dsh.engine import DshEngine

    class Boom:
        def __init__(self) -> None:
            self.n = 0

        def run(self, text: str, session_id: str):
            self.n += 1
            raise ValueError("boom")

    async def run_case() -> int:
        engine = DshEngine()
        boom = Boom()
        engine._harness = boom
        engine._character = make_char()
        try:
            await engine.generate(TurnContext(session_id="s", character_id="char-a", turn=1))
        except ValueError:
            pass
        return boom.n

    assert asyncio.run(run_case()) == 1
