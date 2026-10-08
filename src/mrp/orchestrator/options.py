"""mrp 剧情选项生成（R27 纯逻辑模块）。

回合后给玩家 3-5 个下一步行动选项：
- 最近 8 条玩家可见消息拼对话文本 + 角色名单，交给轻量 LLM 生成
- LLM 返回的 mention（角色名）归一为角色 id（R27.5：@ 前缀的数据源），
  名字/别名精确匹配，匹配不上置 None
- 不足 3 条补被动项、超 5 条截断、text 重复去重
- LLM 调用或解析失败一律降级固定三选项——选项生成永远不能阻塞回合
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from mrp import llm
from mrp.llm import LlmCall, LlmConfig, extract_json
from mrp.shared.models import Character, Message

MIN_OPTIONS = 3
MAX_OPTIONS = 5
CONTEXT_MESSAGES = 8  # 进入 prompt 的最近消息条数

# 风格 → system prompt 指令（SessionMeta.options_style，R27）
_STYLE_INSTRUCTIONS = {
    "action": "偏行动与场景互动（如移动探索、观察环境、使用物品）",
    "dialogue": "偏对话与追问（如询问、回应、表达观点与情绪）",
    "mixed": "行动与对话混合，兼顾场景互动与人物交流",
}

# LLM 失败时的降级三选项（必须可用，绝不抛出）
_FALLBACK_OPTIONS = ["继续观察", "主动发起话题", "暂时离开"]

# 不足 3 条时的补齐池（被动项，按序取不重复的）
_PASSIVE_POOL = ["继续观察", "沉默片刻", "环顾四周"]


class OptionChoice(BaseModel):
    """一个玩家行动选项。text 不含 @ 前缀；@ 的目标角色在 mention_character_id。"""

    text: str
    mention_character_id: str | None = None


class OptionsGenerator:
    """注入式选项生成器（llm_call 为 messages -> str 的单参替身签名）。"""

    def __init__(self, llm_call: LlmCall):
        self._llm_call = llm_call

    def generate(
        self,
        recent_messages: list[Message],
        characters: list[Character],
        style: str = "mixed",
    ) -> list[OptionChoice]:
        """生成 3-5 个选项。调用方负责把 recent_messages 过滤为玩家可见。"""
        try:
            raw = self._llm_call(self._build_messages(recent_messages, characters, style))
            choices = self._parse(raw, characters)
        except Exception:  # 网络/网关/解析任何失败——降级，不阻塞回合
            choices = [OptionChoice(text=t) for t in _FALLBACK_OPTIONS]

        # 去重：text 相同丢，保首个（含其 mention）
        seen: set[str] = set()
        unique: list[OptionChoice] = []
        for c in choices:
            if c.text not in seen:
                seen.add(c.text)
                unique.append(c)

        # 不足 3 条补被动项
        for text in _PASSIVE_POOL:
            if len(unique) >= MIN_OPTIONS:
                break
            if text not in seen:
                seen.add(text)
                unique.append(OptionChoice(text=text))

        # 超 5 条截断
        return unique[:MAX_OPTIONS]

    # ---------------------------------------------------------------- prompt

    def _display_name(self, actor: str, characters: list[Character]) -> str:
        if actor == "player":
            return "玩家"
        for c in characters:
            if c.id == actor:
                return c.card.name
        return actor  # 查不到就用原始 id，不丢消息

    def _build_messages(
        self,
        recent_messages: list[Message],
        characters: list[Character],
        style: str,
    ) -> list[dict[str, str]]:
        tail = recent_messages[-CONTEXT_MESSAGES:]
        dialogue = "\n".join(
            f"{self._display_name(m.actor, characters)}: {m.content}" for m in tail
        )
        roster = (
            "\n".join(f"- {c.id}（{c.card.name}）" for c in characters) or "-（无）"
        )
        style_instruction = _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["mixed"])

        system = (
            "你是剧情选项生成器。根据对话情境为\"玩家\"生成 3-5 个下一步行动选项。\n"
            f"风格要求：{style_instruction}。\n"
            "生成规则：\n"
            "- 每条 8-20 字中文，动词开头\n"
            "- 具体不空泛，结合对话中出现的人、物、话题\n"
            "- 必须包含一个被动项（如\"继续观察\"\"沉默片刻\"）\n"
            "- 部分选项可指定回应对象（mention 填角色名，其余填 null）\n"
            "只输出 JSON 数组，不要任何多余文本：\n"
            '[{"text": "选项文本", "mention": "角色名或null"}]'
        )
        user = (
            "最近对话：\n"
            f"{dialogue}\n\n"
            f"可用角色：\n{roster}\n\n"
            "请生成玩家的下一步行动选项。"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # ---------------------------------------------------------------- 解析

    def _parse(self, raw: str, characters: list[Character]) -> list[OptionChoice]:
        data = _extract_option_array(raw)

        # 名字/别名 -> 角色 id（精确匹配；先入优先，防重名抢注）
        by_name: dict[str, str] = {}
        for c in characters:
            for n in c.mention_names:
                by_name.setdefault(n, c.id)

        choices: list[OptionChoice] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            mention = item.get("mention")
            mention_id: str | None = None
            if isinstance(mention, str):
                mention_id = by_name.get(mention.strip())  # 匹配不上即 None
            choices.append(OptionChoice(text=text.strip(), mention_character_id=mention_id))
        return choices


def make_llm_call(config: LlmConfig) -> LlmCall:
    """包装 llm.chat_text 为注入式单参签名（messages -> str）。"""

    def call(messages: list[dict[str, str]]) -> str:
        return llm.chat_text(messages, config)

    return call


def _extract_option_array(raw: str) -> list:
    """提取选项数组：extract_json 为主，数组优先平衡扫描兜底。

    extract_json 的 { 分支先于 [ 分支——裸 JSON 数组（无围栏）会被截成
    首个 {...} 字典返回。选项生成约定返回数组，故此处兜底一次。
    """
    data = extract_json(raw)
    if isinstance(data, list):
        return data
    arr = _balanced_array(raw)
    if arr is not None:
        return arr
    raise ValueError(f"选项生成回复不是 JSON 数组: {type(data).__name__}")


def _balanced_array(raw: str) -> list | None:
    """字符串感知的 [...] 平衡扫描（忽略字符串字面量内的括号）。"""
    start = raw.find("[")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if escaped:
            escaped = False
        elif in_str and ch == "\\":
            escaped = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except ValueError:
                        return None
    return None
