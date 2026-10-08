"""R34 输出卫生：角色回复的行为契约校验器。

范式与 OptionsGenerator 一致（注入式 LlmCall + fail-open 降级）：
- 四类违规：C1 抢戏玩家 / C2 抢戏他角 / C3 心灵感应 / C4 出戏
- 任何异常（网络/解析/超时）→ passed=True —— 校验器绝不能挡主流程
- 成本独立账（judge 用量不进 cost_by_model 主账，R34.4）
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from mrp.llm import LlmCall, chat_text_with_usage, judge_config
from mrp.shared.models import HygieneReport, TokenUsage, Violation

_JUDGE_MAX_TOKENS = 512
_JUDGE_TIMEOUT_S = 8.0

_SYSTEM_PROMPT = """你是角色扮演输出审查器。判定以下角色回复是否违反行为契约，只判四类违规：

- C1 抢戏玩家：代替"{player}"（玩家本人）做出新的言行、决定或心理描写。
  豁免：转述玩家已说过的话（见"玩家近期公开发言"）；无意识的短促生理反应不算。
- C2 抢戏他角：回复中出现了在场其他角色的新发言或新行动描写（台词或动作）。
  包括替他人回答明确只问他人的问题、错把他人身份当作自己、编造他人的亲属或既往关系。
  豁免：自然接续对方已经公开说过的话；多人共同问题可共同回答，不能仅因提到别人名字就判违规。
- C3 心灵感应：回复直接回应或点破了玩家**未表露**的内心想法（见"玩家未表露的内心"）。
  只通过可观察的外在表现（微表情、肢体语言、语气变化）间接反映情绪不算违规。
- C4 出戏：OOC 元话语、"作为AI"、编剧说明、系统腔等破坏角色扮演的内容。
  若提供了本轮直接任务，完全漏答任务而转向无关内容也记为 C4；用原回复中的无关内容作证据。

规则：
- evidence 必须逐字引用回复原文中的句子，不许改写或编造
- 没有违规就输出 passed=true，violations 为空数组
- 只输出 JSON，不要任何其他文字

输出格式：
{{"violations": [{{"category": "C1", "evidence": "逐字引用的句子"}}], "passed": false}}"""

_GROUP_SYSTEM_PROMPT = """你是群体角色输出审查器。只检查这条群体回应是否越权：

- C1 抢戏玩家：替玩家做出尚未发生的言行、决定或心理描写。不得将群体自身动作误判成玩家动作。
- C2 抢戏正式角色：替正式角色写新台词或新行动。群体内部未命名成员的动作和短台词是允许的。
  包括替正式角色回答明确问他们的问题或凭空改变他们的身份关系；共同问题与对已公开回复的接话不算。
- C4 出戏：OOC 元话语、"作为AI"、编剧说明、系统腔等破坏角色扮演的内容。

不要检查群体内部不同成员之间的台词；群体也不可能读取玩家未表露的内心。
证据必须逐字引用回复原文。没有违规输出 passed=true、violations=[]；只输出 JSON。
格式：{{"violations": [{{"category": "C1", "evidence": "原文句子"}}], "passed": false}}"""


class JudgeInput(BaseModel):
    """一次校验的输入（C1/C2/C3 判据的证据源）。"""

    reply: str
    character_name: str
    player_name: str
    present_characters: list[str] = []
    player_inner_texts: list[str] = []
    player_public_texts: list[str] = []
    group_response: bool = False
    addressed_inputs: list[str] = []
    other_addressed_inputs: list[str] = []
    identity_source: str = ""


class HygieneJudgeHook(Protocol):
    """注入协议（与 OptionsHook 同模式；fake/真实现互换）。"""

    def judge(self, data: JudgeInput) -> HygieneReport: ...


class HygieneJudge:
    """便宜模型驱动的校验器（fail-open）。"""

    def __init__(self, llm_call: LlmCall | None = None) -> None:
        self._llm_call = llm_call
        self._usage = TokenUsage()

    @property
    def last_usage(self) -> TokenUsage:
        return self._usage

    def judge(self, data: JudgeInput) -> HygieneReport:
        try:
            return self._judge(data)
        except Exception:  # noqa: BLE001 —— fail-open：校验器绝不挡主流程
            return HygieneReport(passed=True, usage=self._usage)

    def _judge(self, data: JudgeInput) -> HygieneReport:
        system = _GROUP_SYSTEM_PROMPT if data.group_response else _SYSTEM_PROMPT.format(player=data.player_name)
        parts = [
            f"角色名：{data.character_name}",
            f"玩家名：{data.player_name}",
        ]
        if data.present_characters:
            parts.append("在场其他角色：" + "、".join(data.present_characters))
        if data.addressed_inputs:
            parts.append("本轮直接交给当前发言者的事项：\n" + '\n'.join(data.addressed_inputs))
        if data.other_addressed_inputs:
            parts.append("本轮明确交给其他人的事项（不可代答）：\n" + '\n'.join(data.other_addressed_inputs))
        if data.identity_source:
            parts.append("当前角色身份资料（不是新事件）：\n" + data.identity_source)
        if data.player_public_texts:
            lines = "\n".join(f"- {t}" for t in data.player_public_texts)
            parts.append(f"玩家近期公开发言（转述这些不算违规）：\n{lines}")
        if data.player_inner_texts:
            lines = "\n".join(f"- {t}" for t in data.player_inner_texts)
            parts.append(f"玩家未表露的内心（回复直接回应/点破这些即 C3）：\n{lines}")
        else:
            parts.append("玩家未表露的内心：（本回合无——C3 不可能触发）")
        parts.append(f"待审查的回复：\n{data.reply}")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

        from mrp.llm import extract_json

        if self._llm_call is not None:
            # 注入式（测试替身）
            text = self._llm_call(messages)
        else:
            text, usage = chat_text_with_usage(
                messages, judge_config(),
                max_tokens=_JUDGE_MAX_TOKENS, timeout=_JUDGE_TIMEOUT_S,
                no_thinking=True,  # R48：轻量通道关思考（16s → 1-2s）
            )
            self._usage = TokenUsage(**usage)

        parsed = extract_json(text)
        if not isinstance(parsed, dict):
            raise ValueError("judge 输出非对象")
        violations: list[Violation] = []
        for v in parsed.get("violations") or []:
            if not isinstance(v, dict):
                continue
            cat = str(v.get("category", ""))
            if cat not in ("C1", "C2", "C3", "C4"):
                cat = "C4"
            violations.append(Violation(category=cat, evidence=str(v.get("evidence", ""))[:500]))
        passed = parsed.get("passed")
        if not isinstance(passed, bool):
            passed = not violations  # 模型忘带 passed 字段——按违规列表判定
        return HygieneReport(passed=passed, violations=violations, usage=self._usage)


class FakeJudge:
    """测试替身（MRP_FAKE_JUDGE）：mode=flag 恒违规 / strict 首查违规重查通过。"""

    def __init__(self, mode: str = "flag") -> None:
        self.mode = mode
        self.calls = 0

    def judge(self, data: JudgeInput) -> HygieneReport:
        self.calls += 1
        if self.mode == "strict" and self.calls > 1:
            return HygieneReport(passed=True, attempts=2, corrected=True,
                                 usage=TokenUsage(input_tokens=8, output_tokens=4))
        return HygieneReport(
            passed=False,
            violations=[Violation(category="C1", evidence="你还没来得及回答，她便凑近了。")],
            usage=TokenUsage(input_tokens=8, output_tokens=4),
        )
