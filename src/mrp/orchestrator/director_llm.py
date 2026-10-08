"""R35b LLM 导演：pick_speaker / switch_scene / end_scene 三选一判定。

设计要点（design/v3/m9-r35 §3）：
- 规则前置：@/force/隐式提及走 v1 路径（0 LLM）；LLM 只占轮盘兜底槽位
  与 narration 旁白（旁白常含场景/时间信号）
- fail-open：任何异常返回 None → session.py 回退 v1 轮盘，回合永不中断
- 范式对齐 HygieneJudge（注入式 LlmCall + Fake 替身 + MRP_FAKE_DIRECTOR env）
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from mrp.llm import LlmCall, chat_text_with_usage, director_config, extract_json
from mrp.shared.models import TokenUsage

_DIRECTOR_MAX_TOKENS = 512
_DIRECTOR_TIMEOUT_S = 8.0

_SYSTEM_PROMPT = """你是多角色扮演的导演。根据当前场景、在场角色与最近对话，做三选一判定：

- pick_speaker：从在场者中选 1-2 人接话（按发言顺序排列）。默认选项。
- switch_scene：玩家话语中有**明确的地点转移或时间跳跃信号**才允许（如"走，去天台"、"第二天早上"）。
  普通对话续写绝不切换场景。需给出新场景的地点(title)、时间/氛围(description)、
  在场角色(members，只能从当前在场者中选子集)、过渡提示(transition_hint)。
- end_scene：当前话题已明确收束、剧情出现自然断点。关闭场景并开启同地点的后继场景。

硬约束：
- chosen/members 中的 id 只能来自"在场角色"列表
- 没有明确信号时一律 pick_speaker
- rationale 用一句话中文说明理由

只输出 JSON：
{"action": "pick_speaker", "chosen": ["<角色id>"], "rationale": "...",
 "scene": {"title": "", "description": "", "members": [], "transition_hint": ""},
 "interject": []}"""


class CharacterBrief(BaseModel):
    """导演视角的角色简报（R38 预留 interject_hint）。"""

    id: str
    name: str
    personality_hook: str = ""  # card.personality/description 摘要（≤80 字）
    talkativeness: float = 0.5
    last_speak_turn: int | None = None
    interject_hint: bool = False  # R38.1：主动插话开关开启


class DirectorJudgeInput(BaseModel):
    player_text: str
    channel: str  # dialogue / narration
    scene_title: str = ""
    scene_description: str = ""
    present: list[CharacterBrief] = []
    recent_messages: list[str] = []  # 最近 8 条渲染文本（"名字: 内容"）
    turn: int = 0


class DirectorJudgeLike(Protocol):
    """注入协议（与 HygieneJudgeHook 同模式）。R38 增 judge_followup。"""

    def decide(self, data: DirectorJudgeInput) -> dict | None: ...

    def judge_followup(self, data: FollowupInput) -> dict | None: ...


class FollowupInput(BaseModel):
    """R38.2 接话判定输入。"""

    last_speaker_name: str
    reply_text: str
    candidates: list[CharacterBrief]  # 已过规则闸门的开启者
    scene_title: str = ""


_FOLLOWUP_SYSTEM = """你是多角色扮演的导演。刚有一位角色发言完毕，判断列表中的其他角色
（都开启了"接话"意愿且具备资格）是否有人想对这段发言做出链式反应。
只输出 JSON：{"speak": true/false, "chosen": "<角色id 或 null>", "rationale": "一句话理由"}
没有自然的接话理由就 speak=false。chosen 只能来自候选列表。"""


class DirectorJudge:
    """便宜模型驱动的导演判定（fail-open：任何异常返回 None）。"""

    def __init__(self, llm_call: LlmCall | None = None) -> None:
        self._llm_call = llm_call
        self.usage = TokenUsage()

    def decide(self, data: DirectorJudgeInput) -> dict | None:
        try:
            return self._decide(data)
        except Exception:  # noqa: BLE001 —— fail-open：回退 v1 规则路由
            return None

    def judge_followup(self, data: FollowupInput) -> dict | None:
        """R38.2 接话判定（fail-open：异常返回 None=不接话）。"""
        try:
            cand_lines = [
                f"- {c.id}（{c.name}）" + (f"（{c.personality_hook}）" if c.personality_hook else "")
                for c in data.candidates
            ]
            user = "\n\n".join([
                f"当前场景：{data.scene_title or '未知'}",
                "候选角色（都愿意接话且具备资格）：\n" + "\n".join(cand_lines),
                f"{data.last_speaker_name} 刚刚说：\n{data.reply_text[:400]}",
            ])
            messages = [
                {"role": "system", "content": _FOLLOWUP_SYSTEM},
                {"role": "user", "content": user},
            ]
            if self._llm_call is not None:
                text = self._llm_call(messages)
            else:
                text, usage = chat_text_with_usage(
                    messages, director_config(),
                    max_tokens=200, timeout=_DIRECTOR_TIMEOUT_S,
                    no_thinking=True,  # R48：轻量通道关思考（16s → 1-2s）
                )
                self.usage = TokenUsage(**usage)
            parsed = extract_json(text)
            if not isinstance(parsed, dict):
                return None
            cand_ids = {c.id for c in data.candidates}
            chosen = parsed.get("chosen")
            if parsed.get("speak") and chosen in cand_ids:
                return {"speak": True, "chosen": chosen,
                        "rationale": str(parsed.get("rationale") or "")[:150]}
            return {"speak": False, "chosen": None, "rationale": ""}
        except Exception:  # noqa: BLE001
            return None

    def _decide(self, data: DirectorJudgeInput) -> dict | None:
        present_lines = []
        for c in data.present:
            hook = f"（{c.personality_hook}）" if c.personality_hook else ""
            last = f"，上次发言回合 {c.last_speak_turn}" if c.last_speak_turn is not None else "，尚未发言"
            hint = "，有主动插话意愿" if c.interject_hint else ""
            present_lines.append(f"- {c.id}（{c.name}）{hook}{last}{hint}")
        channel_note = (
            "【注意：这是旁白/画外音，常暗示场景或时间变化，但仍需明确信号才可 switch_scene】"
            if data.channel == "narration" else ""
        )
        user = "\n\n".join(
            [
                f"当前场景：{data.scene_title or '（未知）'}"
                + (f"——{data.scene_description}" if data.scene_description else ""),
                "在场角色：\n" + ("\n".join(present_lines) or "（无）"),
                "最近对话：\n" + ("\n".join(data.recent_messages[-8:]) or "（无）"),
                f"玩家本回合输入：{data.player_text}",
                channel_note,
            ]
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        if self._llm_call is not None:
            text = self._llm_call(messages)
        else:
            text, usage = chat_text_with_usage(
                messages, director_config(),
                max_tokens=_DIRECTOR_MAX_TOKENS, timeout=_DIRECTOR_TIMEOUT_S,
                no_thinking=True,  # R48：轻量通道关思考（16s → 1-2s）
            )
            self.usage = TokenUsage(**usage)

        parsed = extract_json(text)
        if not isinstance(parsed, dict):
            return None
        action = parsed.get("action")
        if action not in ("pick_speaker", "switch_scene", "end_scene"):
            return None
        # 硬约束校验：chosen 只能选在场者
        present_ids = {c.id for c in data.present}
        parsed["chosen"] = [c for c in parsed.get("chosen") or [] if c in present_ids]
        scene = parsed.get("scene")
        if isinstance(scene, dict):
            scene["members"] = [m for m in scene.get("members") or [] if m in present_ids]
        return parsed


class FakeDirectorJudge:
    """测试替身（MRP_FAKE_DIRECTOR）：switch=固定切场景 / pick=固定选角+恒接话。"""

    def __init__(self, mode: str = "switch") -> None:
        self.mode = mode
        self.calls: list[DirectorJudgeInput] = []
        self.followup_calls: list[FollowupInput] = []

    def judge_followup(self, data: FollowupInput) -> dict | None:
        self.followup_calls.append(data)
        if not data.candidates:
            return {"speak": False, "chosen": None, "rationale": ""}
        first = data.candidates[0]
        return {"speak": True, "chosen": first.id, "rationale": "（测试）想接话"}

    def decide(self, data: DirectorJudgeInput) -> dict | None:
        self.calls.append(data)
        if self.mode == "switch":
            present = [c.id for c in data.present]
            return {
                "action": "switch_scene",
                "chosen": [],
                "rationale": "（测试）检测到场景转移信号",
                "scene": {
                    "title": "天台",
                    "description": "夜晚，风很大",
                    "members": present[:1],  # 只有第一个在场角色跟上去
                    "transition_hint": "楼梯间的灯光与夜风",
                },
                "interject": [
                    c.id for c in data.present if c.interject_hint and c.id not in present[:1]
                ][:1],
            }
        # pick
        present = [c.id for c in data.present]
        return {
            "action": "pick_speaker",
            "chosen": present[:1],
            "rationale": "（测试）导演选角",
            "scene": None,
            "interject": [
                c.id for c in data.present if c.interject_hint and c.id not in present[:1]
            ][:1],
        }
