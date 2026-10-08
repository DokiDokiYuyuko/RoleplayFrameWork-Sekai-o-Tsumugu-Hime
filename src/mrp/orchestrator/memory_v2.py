"""R36 记忆分层 v2：增量情景固化 + 场景摘要。

范式对齐 HygieneJudge/OptionsGenerator（注入式 LlmCall + fail-open 降级）：
- EpisodicConsolidator：窗口增量固化（左开右闭结构性无重复），便宜模型结构化输出
- SceneSummarizer：场景关闭后按角色视角生成场景摘要（R36.3 远期压缩的原料）
- llm_call=None（fake 模式/未配置）→ 确定性本地降级，不阻塞
"""
from __future__ import annotations

from typing import Callable

from mrp.llm import LlmCall, chat_text_with_usage, extract_json, judge_config
from mrp.shared.models import MemoryRecord, SessionState, TokenUsage

_SCENE_MAX_TOKENS = 400
_LLM_TIMEOUT_S = 10.0

_SCENE_SYSTEM = """你是场景摘要生成器。把给定场景的全部对话（某角色的视角）压缩为场景摘要。
要求 100-150 字；必须保留：发生的关键事件、人物关系变化、埋下的伏笔或约定。
只输出 JSON：{"summary": "摘要文本"}"""


def _call_llm_channel(
    llm_call: LlmCall | None, network: bool, messages, *, max_tokens: int,
) -> tuple[str | None, TokenUsage | None]:
    """两个固化器共用的 LLM 三态通道（W5 去重，行为不变）：

    llm_call 注入（测试替身）→ 网络（真模式）→ None（本地确定性降级）。
    返回 (text, usage)：usage 仅网络通道非 None，调用方据此更新记账。
    """
    if llm_call is not None:
        return llm_call(messages), None
    if network:
        text, usage = chat_text_with_usage(
            messages, judge_config(), max_tokens=max_tokens, timeout=_LLM_TIMEOUT_S,
        )
        return text, TokenUsage(**usage)
    return None, None


from mrp.orchestrator.important_memory import ImportantMemoryConsolidator


class EpisodicConsolidator(ImportantMemoryConsolidator):
    """Compatibility import for the complete important-memory extractor."""


class SceneSummarizer:
    """R36.3 场景摘要：场景关闭后按角色视角生成（远期历史压缩的原料）。
    LLM 通道三态同 EpisodicConsolidator。"""

    def __init__(self, llm_call: LlmCall | None = None, *, network: bool = False) -> None:
        self._llm_call = llm_call
        self._network = network
        self.usage = TokenUsage()

    def _call_llm(self, messages) -> str | None:
        text, usage = _call_llm_channel(
            self._llm_call, self._network, messages, max_tokens=_SCENE_MAX_TOKENS,
        )
        if usage is not None:
            self.usage = usage
        return text

    def summarize_scene(self, state: SessionState, character_id: str, scene) -> MemoryRecord | None:
        """对某角色视角的整个场景生成摘要。scene: shared.models.Scene。"""
        msgs = [
            m for m in state.visible_messages_for(character_id)
            if m.status == "final" and m.scene_id == scene.id
        ]
        if not msgs:
            return None
        transcript = "\n".join(f"{m.actor}: {m.content}" for m in msgs)

        summary = transcript[:300]  # 降级：截断
        text = self._call_llm([
            {"role": "system", "content": _SCENE_SYSTEM},
            {"role": "user", "content": (
                f"场景「{scene.title}」（回合 {scene.turn_start}-{scene.turn_end or '进行中'}）：\n"
                f"{transcript[:6000]}"
            )},
        ])
        if text is not None:
            try:
                parsed = extract_json(text)
                if isinstance(parsed, dict) and parsed.get("summary"):
                    summary = str(parsed["summary"])[:400]
            except Exception:  # noqa: BLE001 —— 解析失败保持降级值
                pass

        return MemoryRecord(
            character_id=character_id,
            session_id=state.meta.id,
            turn_start=scene.turn_start,
            turn_end=scene.turn_end if scene.turn_end is not None else scene.turn_start,
            kind="scene",
            content=summary,
            source_message_ids=[m.id for m in msgs],
            participants=scene.member_ids,
            scene_id=scene.id,
            importance=3,
        )
