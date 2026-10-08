"""R37.4 短输入垫场：≤4 字玩家输入自动垫 1-2 句环境/氛围描写。

走旁白通道（actor=director, kind=scene）先于路由落账——被路由到的角色
回合上下文自动包含它（逐回合重组红利）。便宜模型 + fail-open。
"""
from __future__ import annotations

from mrp.llm import LlmCall, chat_text_with_usage, judge_config
from mrp.shared.models import TokenUsage

_PADDING_MAX_TOKENS = 128
_PADDING_TIMEOUT_S = 6.0

_SYSTEM_PROMPT = (
    "你是环境氛围描写生成器。根据当前场景与最近对话的氛围，写 1-2 句环境/氛围描写。"
    "要求：不出现任何角色的动作与对话，不推进剧情；只输出描写本身，不要括号，不要解释。"
)


class PaddingGenerator:
    """LLM 三态：network=True → 便宜模型；llm_call 注入 → 测试；皆无 → 返回 ""（跳过）。"""

    def __init__(self, llm_call: LlmCall | None = None, *, network: bool = False) -> None:
        self._llm_call = llm_call
        self._network = network
        self.usage = TokenUsage()

    def generate(self, scene_title: str, scene_desc: str, recent: list[str]) -> str:
        """返回垫场文本（空串=跳过）。任何异常 → 空串（fail-open）。"""
        try:
            return self._generate(scene_title, scene_desc, recent)
        except Exception:  # noqa: BLE001 —— fail-open
            return ""

    def _generate(self, scene_title: str, scene_desc: str, recent: list[str]) -> str:
        user = "\n".join(
            [
                f"当前场景：{scene_title or '未指定'}" + (f"——{scene_desc}" if scene_desc else ""),
                "最近对话：\n" + ("\n".join(recent[-4:]) or "（无）"),
            ]
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        if self._llm_call is not None:
            text = self._llm_call(messages)
        elif self._network:
            text, usage = chat_text_with_usage(
                messages, judge_config(),
                max_tokens=_PADDING_MAX_TOKENS, timeout=_PADDING_TIMEOUT_S,
                no_thinking=True,  # R48：轻量通道关思考
            )
            self.usage = TokenUsage(**usage)
        else:
            return ""  # 未配置（fake 模式默认）→ 跳过
        return (text or "").strip()[:200]
