"""M12-R41/R39 辅助候选生成器（点击触发的玩家发言代笔）。

统一内核对四种上下文各出一批 **3 条可直接发送的完整玩家消息**：
- `open`  开场（first_mes 落账后）        —— 冷启动
- `scene` 场景切换之后（过渡 + 首发言）   —— 冷启动
- `turn`  对话进行中（接住最近话头）      —— 原 R27 选项轨升级（R39 核心）
- `draft` 意图代笔（R40）：玩家写一小句意思 → 3 条"同一意思的三种表达方式"

设计原则（design/v4.0/m12-r41-kickoff.md，2026-09-24 修订）：
- **只由玩家点击触发**（POST /assist/candidates），后端零自动生成、零后台任务；
- 与既有轻量通道同纪律：便宜模型通道 / fail-open / 注入式 llm_call；
- 解析硬约束：单条 ≤120 字、mention 只认在场角色、去重、最多 3 条；
  任何异常或 0 条 → 空列表（失败静默，由调用方处置）。
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from mrp.llm import LlmCall, chat_text_with_usage, extract_json, options_config
from mrp.shared.models import TokenUsage

MAX_CANDIDATES = 3
MAX_TEXT_LEN = 120  # 单条上限
MAX_TOKENS = 800
TIMEOUT_S = 20.0

# 材料预算
FIRST_MES_MAX = 400
OPENING_BUDGET = 1400
LINE_MAX = 120
RECENT_MAX = 6
SAMPLES_MAX = 3

_KINDS = ("hook", "push", "soft")  # ① 接住 / ② 推进 / ③ 软接入

# 风格 → prompt 指令（会话级 options_style，R27 沿用）
_STYLE_INSTRUCTIONS = {
    "action": "偏行动与场景互动（如移动探索、观察环境、使用物品）",
    "dialogue": "偏对话与追问（如询问、回应、表达观点与情绪）",
    "mixed": "行动与对话混合，兼顾场景互动与人物交流",
}

_KIND_HEADER = {
    "open": "玩家刚进入一个多角色角色扮演场景（开场），需要 3 条他/她可以直接发送的消息作为开场。",
    "scene": "玩家刚经历一次场景切换、进入新场景，需要 3 条他/她可以直接发送的消息。",
    "turn": "玩家正在与角色对话，需要 3 条他/她可以直接接着发送的消息。",
    "draft": "玩家用一句话写下了自己想表达的意思，需要 3 条把它表达完整、可直接发送的消息。",
}

# 「三种姿态」块：draft 的多样性轴是"表达方式"（同一意思的三种说法），其余三 kind 是"进入姿态"
_POSTURE_BLOCK = {
    "open": (
        "  ① hook：接住具体元素——回应开场白里的人名、地点、物件、情绪\n"
        "  ② push：主动推进——提出行动或新话题，把剧情往前推\n"
        "  ③ soft：软接入——观察、内心活动或日常动作，低强度安全选项"
    ),
    "scene": (
        "  ① hook：接住具体元素——回应过渡描写里的人名、地点、物件、情绪\n"
        "  ② push：主动推进——提出行动或新话题，把剧情往前推\n"
        "  ③ soft：软接入——观察、内心活动或日常动作，低强度安全选项"
    ),
    "turn": (
        "  ① hook：接住话头——回应最近对话里的人名、话题、情绪\n"
        "  ② push：主动推进——提出行动或新话题，把剧情往前推\n"
        "  ③ soft：软接入——观察、内心活动或日常动作，低强度安全选项"
    ),
    "draft": (
        "  ① hook：直球表达——用最自然的一句台词把玩家的意思说清楚\n"
        "  ② push：带着动作说——配上动作/场景描写，把同样的意思表达出来\n"
        "  ③ soft：含蓄一点说——用内心活动或委婉的说法表达同样的意思"
    ),
}


def _system_prompt(kind: str, style: str) -> str:
    style_line = f"风格要求：{_STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS['mixed'])}。\n"
    posture = _POSTURE_BLOCK.get(kind, _POSTURE_BLOCK["turn"])
    if kind == "draft":
        # draft 的多样性轴是"同一意思的三种表达方式"，且必须贴合玩家的原意
        posture_lines = (
            "- 三条都必须表达玩家写下的那个意思（不要跑题、不要替角色做决定）\n"
            "- 三种表达方式各写一条：\n"
            f"{posture}\n"
        )
    else:
        posture_lines = f"- 三种姿态各写一条：\n{posture}\n"
    return (
        "你是「玩家」的发言代笔器，服务于一个多角色角色扮演场景。"
        f"{_KIND_HEADER.get(kind, _KIND_HEADER['turn'])}\n"
        "生成规则：\n"
        "- 每条 1-3 句、不超过 120 字，用玩家第一人称口吻（贴合玩家设定）\n"
        + posture_lines
        + "- 只说玩家自己的言行；不替任何角色说话，不描写角色的反应\n"
        + "- 可用括号语法：（内心：……）表示内心活动，（旁白：……）表示旁白；其余为台词\n"
        + "- 多角色在场时，至少一条指定回应对象（mention 填角色名，只能从在场名单选；其余填 null）\n"
        + "- 三条内容不要同质化\n"
        + style_line
        + "只输出 JSON 数组，不要任何多余文本：\n"
        '[{"text": "……", "mention": "角色名或null", "kind": "hook"}]'
    )


class OptionChoice(BaseModel):
    """一条玩家候选消息（wire 契约；@ 目标在 mention_character_id，不含 @ 前缀）。

    kind：进入姿态 hook（接住）/push（推进）/soft（软接入）。
    """

    text: str
    mention_character_id: str | None = None
    kind: str = ""


class AssistCharacter(BaseModel):
    """在场角色（含解析 mention 用的名字集合）。"""

    id: str
    name: str
    note: str = ""  # 一句话设定（personality/description 截断）
    aliases: list[str] = []  # 卡名 + 别名（mention 精确匹配源）


class AssistMaterial(BaseModel):
    """一次候选生成的全部输入材料（由 SessionRunner 从会话状态提取）。"""

    kind: Literal["open", "scene", "turn", "draft"]
    scene_id: str = ""
    event_turn: int = 0
    style: str = "mixed"  # 仅 turn 使用（会话级选项风格）
    intent: str = ""  # 仅 draft 使用（R40 意图代笔：玩家写下的那一小句意思）
    persona: str = ""
    characters: list[AssistCharacter] = []
    opening_lines: list[str] = []  # open：各角色开场白（"名字：原文"）
    scene_title: str = ""
    scene_description: str = ""
    transition: str = ""  # scene：过渡描写原文
    recent_lines: list[str] = []  # 最近对话（"名: 内容"）
    player_samples: list[str] = []  # 玩家历史消息（风格样本）
    branch_id: str = ""
    branch_revision: int = 0
    anchor_message_id: str = ""
    anchor_fingerprint: str = ""
    anchor_actor: str = ""
    anchor_label: str = ""
    anchor_kind: str = ""
    anchor_text: str = ""
    player_identity_id: str | None = None


class AssistGenerator:
    """LLM 三态（与 PaddingGenerator 同构）：

    - `llm_call` 注入 → 测试替身（messages -> str）
    - `network=True` → 便宜模型通道（options_config，关思考）
    - 皆无 → 返回 []（跳过）
    """

    def __init__(self, llm_call: LlmCall | None = None, *, network: bool = False) -> None:
        self._llm_call = llm_call
        self._network = network
        self.usage = TokenUsage()

    def generate(self, material: AssistMaterial) -> list[OptionChoice]:
        """生成候选；任何失败（网络/解析/空产出）→ []（静默语义由调用方处理）。"""
        try:
            messages = self._build_messages(material)
            if self._llm_call is not None:
                text = self._llm_call(messages)
            elif self._network:
                text, usage = chat_text_with_usage(
                    messages, options_config(),
                    max_tokens=MAX_TOKENS, timeout=TIMEOUT_S,
                    no_thinking=True,  # 轻量通道关思考
                )
                self.usage = TokenUsage(**usage)
            else:
                return []
            return self._parse(text or "", material.characters)
        except Exception:  # noqa: BLE001 —— fail-open：候选绝不进入任何主流程
            return []

    # ---------------------------------------------------------------- prompt

    def _build_messages(self, m: AssistMaterial) -> list[dict[str, str]]:
        roster = (
            "\n".join(
                f"- {c.name}" + (f"：{c.note}" if c.note else "") for c in m.characters
            )
            or "-（无）"
        )
        scene_line = m.scene_title
        if m.scene_description:
            scene_line = f"{scene_line}——{m.scene_description}" if scene_line else m.scene_description
        parts = [
            f"玩家设定：{m.persona.strip() or '（未设置）'}",
            f"在场角色：\n{roster}",
            f"当前场景：{scene_line or '（未指定）'}",
        ]
        if m.intent:
            parts.append(f"玩家写下的意思（需要扩写）：{m.intent}")
        if m.anchor_text:
            parts.append(
                f"当前最新消息（{m.anchor_label or '未知发言者'} · {m.anchor_kind or '对话'}；请以这段原文为主要接话依据）：\n"
                f"{m.anchor_text}"
            )
        if m.opening_lines:
            parts.append("开场白：\n" + "\n".join(m.opening_lines))
        if m.transition:
            parts.append(f"过渡描写：{m.transition}")
        if m.recent_lines:
            parts.append("较早的近期对话（背景）：\n" + "\n".join(m.recent_lines))
        if m.player_samples:
            parts.append("玩家此前发言（风格参考）：\n" + "\n".join(f"- {s}" for s in m.player_samples))
        parts.append("请生成玩家的 3 条消息。")
        return [
            {"role": "system", "content": _system_prompt(m.kind, m.style)},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

    # ---------------------------------------------------------------- 解析

    def _parse(self, raw: str, characters: list[AssistCharacter]) -> list[OptionChoice]:
        data = extract_option_array(raw)

        # 名字/别名 -> 角色 id（只在场角色；离场者即使被点名也置 None）
        by_name: dict[str, str] = {}
        for c in characters:
            for n in [c.name, *c.aliases]:
                n = n.strip()
                if n:
                    by_name.setdefault(n, c.id)

        out: list[OptionChoice] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()
            if len(text) > MAX_TEXT_LEN or text in seen:
                continue  # 超长视为格式不符；重复去重
            mention = item.get("mention")
            mention_id: str | None = None
            if isinstance(mention, str):
                mention_id = by_name.get(mention.strip())
            kind = item.get("kind")
            seen.add(text)
            out.append(
                OptionChoice(
                    text=text,
                    mention_character_id=mention_id,
                    kind=kind if kind in _KINDS else "",
                )
            )
            if len(out) >= MAX_CANDIDATES:
                break
        return out


class FakeAssistGenerator:
    """fake 模式 / 冒烟：确定性 3 条（hook 条 @ 在场首位角色），零网络零成本。"""

    def __init__(self) -> None:
        self.usage = TokenUsage()

    def generate(self, material: AssistMaterial) -> list[OptionChoice]:
        target = material.characters[0] if material.characters else None
        name = target.name if target else ""
        hook = (
            f"（看向{name}）我先接一句——刚才说到哪儿了？" if name else "我先接一句——刚才说到哪儿了？"
        )
        return [
            OptionChoice(text=hook, mention_character_id=target.id if target else None, kind="hook"),
            OptionChoice(text="我想先四处看看，熟悉一下这里。", kind="push"),
            OptionChoice(text="（内心：先别急，看看大家的反应再说。）", kind="soft"),
        ]


# ---------------------------------------------------------------- 数组解析（原 options.py）


def extract_option_array(raw: str) -> list:
    """提取候选数组：extract_json 为主，数组优先平衡扫描兜底。

    extract_json 的 { 分支先于 [ 分支——裸 JSON 数组（无围栏）会被截成
    首个 {...} 字典返回。候选约定返回数组，故此处兜底一次。
    """
    data = extract_json(raw)
    if isinstance(data, list):
        return data
    arr = _balanced_array(raw)
    if arr is not None:
        return arr
    raise ValueError(f"候选生成回复不是 JSON 数组: {type(data).__name__}")


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


# ---------------------------------------------------------------- 材料预算小工具


def clip_line(text: str, limit: int = LINE_MAX) -> str:
    return text.strip()[:limit]


def opening_lines_with_budget(items: list[tuple[str, str]]) -> list[str]:
    """开场白（(名字, 原文) 按时间序）：从最后一条往前累计预算，保留最新的。

    单条截 FIRST_MES_MAX；总量预算 OPENING_BUDGET；最后一条无条件保留。
    """
    out: list[str] = []
    budget = OPENING_BUDGET
    for name, text in reversed(items):
        line = f"{name}：{clip_line(text, FIRST_MES_MAX)}"
        if out and len(line) > budget:
            break
        out.append(line)
        budget -= len(line)
    return list(reversed(out))
