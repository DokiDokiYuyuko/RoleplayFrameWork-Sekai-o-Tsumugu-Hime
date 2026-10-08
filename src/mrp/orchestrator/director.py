"""导演（Director）：R2.2 发言权裁决——谁说话、为什么（可复现可审计）。

四种 trigger（玩家发言后由会话循环先判 trigger 再调）：
- manual_force   外部点名（工具/玩家强控）
- mention        文中提及（名字/别名命中，多人按出现位置排队）
- talkativeness  无人被提及时按健谈度轮盘
- open_round     开场：全部在场非静音角色按卡序出 first_mes

纯逻辑：不碰引擎/网络/持久化；同 rng_seed + 同输入 → 同决策。
"""
from __future__ import annotations

import random
import re
from typing import Literal

from mrp.shared.models import (
    Character,
    DirectorDecision,
    Message,
    ScoredCandidate,
)

Trigger = Literal["manual_force", "mention", "talkativeness", "open_round"]

# 轮盘加成系数：score = talkativeness × (1 + _DISTANCE_BONUS × 距上次发言回合数)
_DISTANCE_BONUS = 0.3


def _has_cjk(text: str) -> bool:
    """是否含 CJK 表意字符（含扩展A/兼容区/假名）。"""
    for ch in text:
        if (
            "\u4e00" <= ch <= "\u9fff"
            or "\u3400" <= ch <= "\u4dbf"
            or "\uf900" <= ch <= "\ufaff"
            or "\u3040" <= ch <= "\u30ff"  # 平假名/片假名
        ):
            return True
    return False


def _word_pattern(name: str) -> re.Pattern[str]:
    """拉丁名字的全词边界模式：首尾邻位不得是 ASCII 字母数字。

    用 ASCII alnum 而非 \\w，保证 "爱Alice呀" 这类中西混排仍算提及
    （\\w 在 unicode 模式下会把 CJK 也算进词字符，导致 \\b 失效）。
    """
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])")


def extract_mentions(text: str, characters: list[Character]) -> list[tuple[str, str]]:
    """扫描 text，返回被提及的 (character_id, 命中词)，按文中出现位置排序。

    规则（R2.4）：
    - 含 CJK 的名字 → 子串匹配（中文无词边界）
    - 纯拉丁名字 → 全词边界（"Alice" 不匹配 "Alicia"/"Bobcat"）
    - 同一角色多个名字命中时取最早出现的一个词；只字面匹配，大小写敏感
    """
    hits: list[tuple[int, str, str]] = []  # (首次出现位置, character_id, 命中词)
    for ch in characters:
        best: tuple[int, str] | None = None  # (位置, 词)
        for name in ch.mention_names:
            if _has_cjk(name):
                idx = text.find(name)
            else:
                m = _word_pattern(name).search(text)
                idx = m.start() if m else -1
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, name)
        if best is not None:
            hits.append((best[0], ch.id, best[1]))
    hits.sort(key=lambda h: h[0])
    return [(cid, word) for _, cid, word in hits]


def _eligible(ch: Character) -> bool:
    """可发言：在场且未静音。"""
    return ch.present and not ch.muted


def _last_speak_turn(history: list[Message], actor: str) -> int | None:
    for m in reversed(history):
        if m.actor == actor and m.status == "final":
            return m.turn
    return None


def _consecutive_speaker(history: list[Message]) -> str | None:
    """"连续发言 2 轮排除"：最后两条 final 消息为同一 actor 时返回该 actor。"""
    finals = [m for m in history if m.status == "final"]
    if len(finals) >= 2 and finals[-1].actor == finals[-2].actor:
        return finals[-1].actor
    return None


class Director:
    """发言权裁决器（无状态，可复用）。"""

    def decide(
        self,
        trigger: Trigger,
        text: str,
        characters: list[Character],
        history: list[Message],
        rng_seed: int = 0,
        *,
        forced_character_id: str | None = None,
    ) -> DirectorDecision:
        """裁决本回合谁发言。

        trigger=manual_force 时必须给 forced_character_id（外部点名的角色 id）。
        history 为玩家发言后的消息流；决策 turn 取 history 末条消息的 turn
        （玩家消息已入列，同一触发产生的所有消息共享该 turn 号）。
        """
        turn = history[-1].turn if history else 0
        if trigger == "manual_force":
            candidates, chosen = self._manual_force(forced_character_id, characters)
        elif trigger == "mention":
            candidates, chosen = self._mention(text, characters)
        elif trigger == "talkativeness":
            candidates, chosen = self._talkativeness(characters, history, turn, rng_seed)
        elif trigger == "open_round":
            candidates, chosen = self._open_round(characters)
        else:  # pragma: no cover - Literal 已约束
            raise ValueError(f"unknown trigger: {trigger!r}")
        return DirectorDecision(
            turn=turn,
            trigger=trigger,
            candidates=candidates,
            chosen=chosen,
            rng_seed=rng_seed,
        )

    # ---- manual_force ----

    def _manual_force(
        self, forced_character_id: str | None, characters: list[Character]
    ) -> tuple[list[ScoredCandidate], list[str]]:
        if forced_character_id is None:
            raise ValueError("manual_force 需要 forced_character_id")
        candidates: list[ScoredCandidate] = []
        if any(c.id == forced_character_id for c in characters):
            candidates.append(
                ScoredCandidate(
                    character_id=forced_character_id, score=0.0, reasons=["manual"]
                )
            )
        return candidates, [forced_character_id]

    # ---- mention ----

    def _mention(
        self, text: str, characters: list[Character]
    ) -> tuple[list[ScoredCandidate], list[str]]:
        mentions = extract_mentions(text, characters)
        candidates: list[ScoredCandidate] = []
        chosen: list[str] = []
        for cid, word in mentions:
            ch = next(c for c in characters if c.id == cid)
            if not _eligible(ch):
                continue  # 静音/离席角色被提及也不入队
            chosen.append(cid)
            candidates.append(
                ScoredCandidate(character_id=cid, score=0.0, reasons=["mention", word])
            )
        return candidates, chosen

    # ---- talkativeness 轮盘 ----

    def _talkativeness(
        self, characters: list[Character], history: list[Message], turn: int, rng_seed: int
    ) -> tuple[list[ScoredCandidate], list[str]]:
        excluded_by_streak = _consecutive_speaker(history)
        candidates: list[ScoredCandidate] = []
        scores: dict[str, float] = {}
        for ch in characters:
            if not _eligible(ch):
                continue
            last = _last_speak_turn(history, ch.id)
            # 从未发言 → 距离按"距开场以来经过的回合数"计（越久没说话加成越高）
            turns_since = (turn - last) if last is not None else turn
            score = ch.talkativeness * (1.0 + _DISTANCE_BONUS * turns_since)
            scores[ch.id] = score
            candidates.append(
                ScoredCandidate(
                    character_id=ch.id,
                    score=score,
                    reasons=[
                        f"talkativeness={ch.talkativeness}",
                        f"turns_since_last_speak={turns_since}",
                    ],
                )
            )

        # 连续发言 2 轮的角色不获胜，但打分与排除原因保留在 candidates（审计：
        # 为什么最高分没被选）。muted/离席者本就不进轮盘。
        for c in candidates:
            if c.character_id == excluded_by_streak:
                c.reasons.append("excluded:consecutive_speak")

        pool = [c for c in candidates if c.character_id != excluded_by_streak]
        if not pool:
            return candidates, []

        max_score = max(c.score for c in pool)
        tied = [c for c in pool if c.score == max_score]
        if len(tied) == 1:
            winner = tied[0].character_id
        else:
            rng = random.Random(rng_seed)
            winner = rng.choice(tied).character_id
        return candidates, [winner]

    # ---- open_round ----

    def _open_round(
        self, characters: list[Character]
    ) -> tuple[list[ScoredCandidate], list[str]]:
        eligible = [c for c in characters if _eligible(c)]
        candidates = [
            ScoredCandidate(character_id=c.id, score=0.0, reasons=["open_round"])
            for c in eligible
        ]
        return candidates, [c.id for c in eligible]
