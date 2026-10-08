"""世界书触发引擎（R4.1）：纯逻辑 + 可复现（rng_seed）。

职责边界：只做"哪些条目该注入"的判定与预算裁剪，输出 list[Injection]；
锚点排序与最终提示组装是 shared/prompt.py PromptComposer 的职责。

ST（SillyTavern）语义对齐注记：
- key 匹配：普通串 → 大小写不敏感子串；'/.../'（Risu 风格）→ re.search，
  大小写由正则自身控制；多个 key 命中任一即触发（reason 记首个命中者）。
- selective_logic 二级门（selective=True 且 secondary_keys 非空时生效）：
  0=AND_ANY：任一次级 key 命中才过；
  1=NOT_ALL：仅"全部次级 key 命中"时排除（部分命中/全不命中都过）；
  2=NOT_ANY：任一次级 key 命中即排除；
  3=AND_ALL：全部次级 key 命中才过。
- probability：整个 scan 共享一个 random.Random(rng_seed)，按条目书序掷骰
  （randint(1,100) <= probability），掷骰失败的条目本次 scan 不再激活
  （含递归轮），保证"一次扫描一次概率"。
- 一级递归：首轮命中（含 constant）条目的 content 并入扫描文本再扫一遍；
  extensions.excludeRecursion=True 的条目不被递归激活（一级正常触发不受影响）；
  extensions.preventRecursion=True 的条目其内容不参与递归扫描文本。
- 预算：按 order 降序贪心填充（order 大=靠近上下文末尾=影响大，优先保），
  放不下的丢弃但继续尝试更小的；tokens 以 len(content)//2 近似（中文够用）。
"""
from __future__ import annotations

import random
import re

from mrp.shared.models import Injection, Lorebook, LorebookEntry, Message


def _is_regex_key(key: str) -> bool:
    """'/.../' 形式（长度 >= 3）视为正则 key（Risu 风格）。"""
    return len(key) >= 3 and key.startswith("/") and key.endswith("/")


def _match_key(text: str, key: str) -> bool:
    if _is_regex_key(key):
        try:
            return re.search(key[1:-1], text) is not None  # 大小写由正则自身控制
        except re.error:  # 非法正则：视为不命中，不让一本书拖垮整轮扫描
            return False
    return key.lower() in text.lower()


def _match_any(text: str, keys: list[str]) -> str | None:
    """返回命中的 key（用于 reason 拼接）；未命中返回 None。空 key 不参与匹配。"""
    for key in keys:
        if key and _match_key(text, key):
            return key
    return None


def _secondary_gate(entry: LorebookEntry, text: str) -> bool:
    """selective 二级门。selective=False 或无有效次级 key 时不设门。"""
    if not entry.selective:
        return True
    keys = [k for k in entry.secondary_keys if k]
    if not keys:
        return True
    hits = [_match_key(text, k) for k in keys]
    if entry.selective_logic == 0:  # AND_ANY
        return any(hits)
    if entry.selective_logic == 1:  # NOT_ALL：仅全部命中时排除
        return not all(hits)
    if entry.selective_logic == 2:  # NOT_ANY：任一命中即排除
        return not any(hits)
    return all(hits)  # 3 = AND_ALL


def _flag(entry: LorebookEntry, name: str) -> bool:
    return bool(entry.extensions.get(name, False))


def _build_scan_text(book: Lorebook, window: list[Message]) -> str:
    """扫描文本：最近 scan_depth 回合的消息 + 玩家最新一条（始终包含，防丢当前输入）。"""
    ordered = sorted(window, key=lambda m: m.seq)
    turns: dict[int, list[Message]] = {}
    for m in ordered:
        turns.setdefault(m.turn, []).append(m)
    all_turns = sorted(turns)
    depth = book.scan_depth
    selected_turns = all_turns[-depth:] if depth > 0 else []
    selected = [m for t in selected_turns for m in turns[t]]
    selected_ids = {m.id for m in selected}
    last_player = None
    for m in ordered:
        if m.actor == "player":
            last_player = m
    if last_player is not None and last_player.id not in selected_ids:
        selected.append(last_player)
    return "\n".join(m.content for m in selected)


def _gate_reason(entry: LorebookEntry, text: str) -> str | None:
    """constant/key/二级门判定：通过返回 reason（不含概率部分），未通过返回 None。"""
    if entry.constant:
        return "constant"  # 无条件注入，不看 key、不看二级门
    if not entry.keys:
        return None  # 非 constant 且无 key：永不触发
    hit = _match_any(text, entry.keys)
    if hit is None:
        return None
    reason = f"regex:{hit[1:-1]}" if _is_regex_key(hit) else f"key:{hit}"
    if not _secondary_gate(entry, text):
        return None
    return reason


class LorebookEngine:
    """世界书触发引擎：scan() 纯函数式，无状态、无 IO。"""

    @staticmethod
    def scan(
        book: Lorebook,
        window: list[Message],
        rng_seed: int = 0,
        budget: int | None = None,
    ) -> list[Injection]:
        """对一本书跑一次触发扫描。

        window：已过可见性裁决的消息（调用方负责）；rng_seed：概率掷骰种子，
        同 seed 结果确定（进决策日志可复现）；budget：token 预算，None 时取
        book.token_budget。
        """
        return LorebookEngine.scan_with_diagnostics(book, window, rng_seed, budget)[0]

    @staticmethod
    def scan_with_diagnostics(
        book: Lorebook,
        window: list[Message],
        rng_seed: int = 0,
        budget: int | None = None,
    ) -> tuple[list[Injection], dict[str, str]]:
        """Return selected injections and an exact reason for each excluded entry."""
        rng = random.Random(rng_seed)
        if budget is None:
            budget = book.token_budget
        scan_text = _build_scan_text(book, window)

        # uid -> (entry, reason)；插入序 = 书序，去重防环
        triggered: dict[int, tuple[LorebookEntry, str]] = {}
        dice_failed: set[int] = set()
        excluded: dict[str, str] = {}

        def run_round(text: str, recursive: bool) -> None:
            for entry in book.entries:
                key = f"{book.id}:{entry.uid}"
                if entry.extensions.get('mrp.runtime_scope') == 'author':
                    excluded[key] = '作者资料，未选为故事用途'
                    continue
                if not entry.enabled or entry.uid in triggered or entry.uid in dice_failed:
                    if not entry.enabled:
                        excluded[key] = "已关闭"
                    continue
                if recursive and _flag(entry, "excludeRecursion"):
                    continue
                reason = _gate_reason(entry, text)
                if reason is None:
                    if not entry.constant:
                        if not entry.keys:
                            excluded[key] = "没有触发关键词"
                        elif _match_any(text, entry.keys) is None:
                            excluded[key] = "关键词未命中"
                        else:
                            excluded[key] = "二级关键词条件未通过"
                    continue
                if entry.probability < 100:
                    roll = rng.randint(1, 100)
                    if roll > entry.probability:
                        dice_failed.add(entry.uid)
                        excluded[key] = f"概率未通过（掷出 {roll}，阈值 {entry.probability}）"
                        continue
                    reason = f"{reason},probability:{roll}"
                if recursive:
                    reason = f"recursive:{reason}"
                triggered[entry.uid] = (entry, reason)

        run_round(scan_text, recursive=False)
        if book.recursive_scanning and triggered:
            # 一级递归：preventRecursion 条目的内容不并入
            extra = "\n".join(
                e.content for e, _ in triggered.values() if not _flag(e, "preventRecursion")
            )
            run_round(f"{scan_text}\n{extra}" if extra else scan_text, recursive=True)

        # 预算：order 降序贪心（稳定排序，同 order 保书序）
        selected = sorted(triggered.values(), key=lambda pair: -pair[0].order)
        out: list[Injection] = []
        used = 0
        for entry, reason in selected:
            tokens = len(entry.content) // 2
            if used + tokens > budget:
                excluded[f"{book.id}:{entry.uid}"] = f"超出本书预算（需要 {tokens}，剩余 {budget - used} tokens）"
                continue  # 放不下丢弃，继续尝试更小的
            used += tokens
            out.append(
                Injection(
                    source="lorebook",
                    entry_id=f"{book.id}:{entry.uid}",
                    content=entry.content,
                    anchor=entry.anchor,
                    depth=entry.depth,
                    order=entry.order,
                    tokens=tokens,
                    reason=reason,
                )
            )
        selected_ids = {item.entry_id for item in out}
        for entry in book.entries:
            key = f"{book.id}:{entry.uid}"
            if key not in selected_ids and key not in excluded:
                excluded[key] = "未在当前可见对话中触发"
        return out, excluded
