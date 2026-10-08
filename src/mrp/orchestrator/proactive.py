"""R38 角色主动性:开关制插话/接话的规则引擎(纯逻辑零 LLM)。

规则答"能不能"(资格/链深/上限),LLM 只答"想不想"(director_judge)。
无候选 → 零 LLM 调用(闸门短路)。
"""
from __future__ import annotations

from mrp.shared.models import Character, Message

# R38.2 硬约束:接话链深上限(需求原文,不配置化)
_MAX_FOLLOWUP_DEPTH = 2


class ProactiveEngine:
    """纯规则资格判定——所有闸门可单测穷举。"""

    def interject_candidates(self, characters: list[Character]) -> list[Character]:
        """主动插话资格:开关开 + 在场 + 未静音。"""
        return [
            c for c in characters
            if c.interject_enabled and c.present and not c.muted
        ]

    def followup_candidates(
        self,
        last_speaker: str,
        spoken_this_turn: set[str],
        characters: list[Character],
        history: list[Message],
        chain_depth: int,
        proactive_count: int,
        limit: int,
    ) -> list[Character]:
        """接话资格(全部闸门,返回空=无需 LLM 判定):
        开关开 + 在场 + 未静音 + 不在本回合已发言集合 + 非最后发言者本人
        + 连续发言排除(沿用 director 语义:该角色最近 2 条连续发言即排除)
        + 链深 < 2 + 全局主动发言数 < 上限。
        """
        if chain_depth >= _MAX_FOLLOWUP_DEPTH or proactive_count >= limit:
            return []
        # 连续发言排除:检查该角色是否已连续发言(最近 final 消息都是他)
        def _consecutive(cid: str) -> bool:
            count = 0
            for m in reversed(history):
                if m.status != "final":
                    continue
                if m.actor == cid:
                    count += 1
                    if count >= 2:
                        return True
                else:
                    break
            return False

        return [
            c for c in characters
            if c.followup_enabled
            and c.present
            and not c.muted
            and c.id != last_speaker
            and c.id not in spoken_this_turn
            and not _consecutive(c.id)
        ]
