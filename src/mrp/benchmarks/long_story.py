"""Run current FTS and evidence-filtered recall on a synthetic 240-turn story."""
from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from mrp.orchestrator.memory import MemoryStore
from mrp.orchestrator.memory_recall import recall_memories, finish_recall_audit
from mrp.shared.models import Character, CharacterCard, MemoryRecord, Message, Scene, SessionMeta, SessionState, TurnContext
from mrp.shared.prompt import compose_prompt


def fixture():
    sid = "synthetic-long-branch-a"
    scenes = [Scene(id=f"scene-{n}", title=title, member_ids=["guide", "player"],
                    turn_start=n * 60 + 1, turn_end=(n + 1) * 60 if n < 3 else None)
              for n, title in enumerate(("港口", "山道", "书馆", "庭院"))]
    contents = {7: "渡船人交给向导一枚小铜铃，用于标记旧日渡口。",
        12: "玩家与向导约定：再次相见时交换一封密封信。",
        20: "玩家答应和向导一起归还借来的星图。",
        30: "向导决定以后称呼玩家为老朋友。",
        40: "玩家与向导约定一起修理木车。",
        90: "仅玩家看见的合成密文：山后有一座隐秘仓库。",
        120: "玩家与向导已经一起把星图归还书馆。",
        145: "玩家与向导取消修理木车的计划，木车已经报废。",
        180: "本路线后期才发生的秘密：庭院钥匙交给了向导。"}
    messages = [Message(id=f"source-{turn}", session_id=sid, seq=turn, turn=turn,
        actor="player" if turn % 2 else "guide", scene_id=f"scene-{(turn - 1) // 60}",
        content=contents.get(turn, f"合成旅程第 {turn} 轮，两人在当前地点观察天气。"),
        visible_to=["player"] if turn == 90 else "all") for turn in range(1, 241)]
    state = SessionState(meta=SessionMeta(id=sid, title="合成长篇基线", character_ids=["guide"],
            memory_top_k=4), characters=[Character(id="guide", card=CharacterCard(name="向导"))],
            messages=messages, scenes=scenes, active_scene_id=scenes[-1].id)
    def record(mid, turn, **kw):
        source = messages[turn - 1]
        return MemoryRecord(id=mid, character_id="guide", session_id=sid,
            turn_start=turn, turn_end=turn, content=source.content,
            source_message_ids=[source.id], effective_message_id=source.id,
            source_fingerprints={source.id: source.fingerprint}, **kw)
    records = [record("bronze-bell", 7, participant_ids=["guide", "ferryman"]),
        record("important-letter", 12, important=True, category="unfinished", matter_status="open",
               participant_ids=["player", "guide"]),
        record("star-map-open", 20, category="unfinished", matter_status="open", participant_ids=["player", "guide"]),
        record("old-friend", 30, category="relationship", participant_ids=["player", "guide"]),
        record("cart-open", 40, category="unfinished", matter_status="open", participant_ids=["player", "guide"]),
        record("private-cipher", 90, important=True, participant_ids=["player", "guide"]),
        record("star-map-completed", 120, category="unfinished", matter_status="completed",
               supersedes=["star-map-open"], participant_ids=["player", "guide"]),
        record("cart-cancelled", 145, category="unfinished", matter_status="cancelled",
               supersedes=["cart-open"], participant_ids=["player", "guide"]),
        record("future-courtyard-key", 180, important=True, participant_ids=["player", "guide"])]
    # Query-only old experiences and distractions do not enter participant fallback.
    records.extend(record(f"distractor-{turn}", turn, participant_ids=["guide", "absent-npc"])
                   for turn in range(1, 241) if turn not in contents)
    records.append(MemoryRecord(id="sibling-secret", character_id="guide", session_id="synthetic-long-branch-b",
        turn_start=7, turn_end=7, content="小铜铃属于另一条路线的国王，绝不能带入本路线。",
        source_message_ids=["source-7"], effective_message_id="source-7", important=True))
    return state, records


def run_baseline(root: Path):
    state, records = fixture()
    store = MemoryStore(root / "synthetic.db", root / "synthetic-mirrors", embedding=None)
    try:
        store.add_many(records)
        visible = state.visible_messages_for("guide")
        cases = []
        labels = {}
        def recall(name, query, include=(), exclude=(), *, chosen_state=state, purpose="capability"):
            audit = []
            selected_visible = chosen_state.visible_messages_for("guide")
            start = time.perf_counter()
            injections = recall_memories(store, chosen_state, "guide", selected_visible, ["guide"], query, audit=audit)
            prompt = compose_prompt(TurnContext(session_id=chosen_state.meta.id, character_id="guide",
                turn=chosen_state.current_turn(), visible_messages=selected_visible, injections=injections,
                budget_tokens=32_000))
            finish_recall_audit(audit, prompt.included_entry_ids, prompt.omitted_reasons)
            returned = set(prompt.included_entry_ids)
            passed = set(include) <= returned and not set(exclude).intersection(returned)
            cases.append({"name": name, "kind": purpose, "query": query, "expected_ids": [labels.get(mid, mid) for mid in include],
                "forbidden_ids": [labels.get(mid, mid) for mid in exclude], "passed": passed,
                "returned_ids": sorted(labels.get(mid, mid) for mid in returned),
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "omitted": [{"id": labels.get(row["memory_id"], row["memory_id"]), "reason": row["reason"]}
                            for row in audit if row["disposition"] == "omitted"]})
            return prompt
        recall("旧经历：原关键词", "小铜铃", ["bronze-bell"], ["sibling-secret"])
        recall("旧经历：换说法，无相同三字片段", "那枚会响的金属物件", ["bronze-bell"],
               ["sibling-secret"], purpose="known_recall_limitation")
        important_prompt = recall("跨场景重要约定，无原关键词", "久别之后再次见面", ["important-letter"], ["sibling-secret"])
        assert "历史地点、去向与状态不代表现在" in important_prompt.text
        recall("已完成事项不再要求重新完成", "星图", ["star-map-completed"], ["star-map-open"])
        recall("已取消事项保留取消状态", "木车", ["cart-cancelled"], ["cart-open"])
        recall("重逢称呼无需关键词", "晚上好", ["old-friend"])
        recall("角色看不见的来源不会进入上下文", "隐秘仓库", exclude=["private-cipher"])
        recall("同关键词的另一条路线不能挤入", "小铜铃", ["bronze-bell"], ["sibling-secret"])
        # A historically earlier fork gets only anchored records in its prefix.
        child = state.model_copy(deep=True)
        child.meta.id = "synthetic-historical-child"
        child.messages = [message for message in child.messages if message.turn <= 100]
        for message in child.messages:
            message.session_id = child.meta.id
        copied = store.copy_at_fork(state.meta.id, child.meta.id, 10_000, child.messages)
        lineage = {record.inherited_from_id: record.id for record in copied}
        assert "future-courtyard-key" not in lineage and "star-map-completed" not in lineage
        labels.update({record.id: f"fork:{record.inherited_from_id}" for record in copied})
        recall("第100轮分叉仍使用当时未完成事项", "星图", [lineage["star-map-open"]],
               ["star-map-completed", "future-courtyard-key"], chosen_state=child)
        recall("第100轮分叉没有第180轮的新事实", "庭院钥匙", exclude=[lineage.get("future-courtyard-key", "future-courtyard-key")], chosen_state=child)
        raw = []
        for query in ("小铜铃", "那枚会响的金属物件"):
            raw.append({"query": query, "returned_ids": [r.id for r, _ in store.search("guide", query,
                k=4, current_turn=240, session_id=state.meta.id)]})
        return {"fixture": {"turns": 240, "scenes": 4, "messages": len(state.messages),
                "records": len(records), "visible_messages": len(visible), "embedding": None},
            "environment": {"python": platform.python_version(), "platform": platform.system()},
            "cases": cases, "raw_fts_queries": raw,
            "capability_passed": sum(row["passed"] for row in cases), "case_count": len(cases),
            "isolation_assertions_passed": all(row["passed"] for row in cases if row["kind"] != "known_recall_limitation"),
            "limitations": ["合成记忆记录，未测真实模型抽取质量。", "当前生产FTS不能保证无词面重合的语义召回；没有接入嵌入。",
                            "优先记忆和参与者规则可找回重要约定；普通无关人物旧经历仍依赖词面检索。"]}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mrp-synthetic-baseline-") as temp:
        result = run_baseline(Path(temp))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
