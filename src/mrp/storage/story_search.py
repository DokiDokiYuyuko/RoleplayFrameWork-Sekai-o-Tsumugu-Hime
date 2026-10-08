"""Rebuildable local story search index. Session JSON remains authoritative."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from mrp.shared.models import SessionState


class StorySearchIndex:
    def __init__(self, data_root: Path) -> None:
        self.path = data_root / "search" / "stories.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._scheduled: dict[str, asyncio.Task] = {}
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def _init(self) -> None:
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS messages (story_id TEXT, branch_id TEXT, message_id TEXT, actor TEXT, scene_id TEXT, content TEXT, bookmarked INTEGER, event INTEGER, PRIMARY KEY(branch_id, message_id))")
            db.execute("CREATE INDEX IF NOT EXISTS idx_story_messages ON messages(story_id, branch_id)")
            db.execute("CREATE TABLE IF NOT EXISTS indexed_branches (branch_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(content, content='messages', content_rowid='rowid', tokenize='trigram')")
                db.executescript("""
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN INSERT INTO message_fts(rowid,content) VALUES(new.rowid,new.content); END;
                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN INSERT INTO message_fts(message_fts,rowid,content) VALUES('delete',old.rowid,old.content); END;
                CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN INSERT INTO message_fts(message_fts,rowid,content) VALUES('delete',old.rowid,old.content); INSERT INTO message_fts(rowid,content) VALUES(new.rowid,new.content); END;
                """)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False

    def index_state(self, state: SessionState) -> None:
        branch = state.meta.id
        bookmarks = {item.message_id for item in state.bookmarks}
        events = {item.anchor_message_id for item in state.story_events}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM indexed_branches WHERE branch_id=?", (branch,)).fetchone()
            if revision is not None and revision[0] > state.meta.branch_revision:
                return
            existing = {row["message_id"]: (row["story_id"], row["actor"], row["scene_id"], row["content"], row["bookmarked"], row["event"])
                        for row in db.execute("SELECT story_id,message_id,actor,scene_id,content,bookmarked,event FROM messages WHERE branch_id=?", (branch,))}
            current = [
                (state.meta.story_id, branch, message.id, str(message.actor), message.scene_id,
                 message.content, int(message.id in bookmarks), int(message.id in events))
                for message in state.messages if message.can_see("player") and message.status == "final"
            ]
            current_ids = {row[2] for row in current}
            changed = [row for row in current if existing.get(row[2]) != (row[0], row[3], row[4], row[5], row[6], row[7])]
            db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", [row for row in changed if row[2] not in existing])
            db.executemany("UPDATE messages SET story_id=?,actor=?,scene_id=?,content=?,bookmarked=?,event=? WHERE branch_id=? AND message_id=?",
                           [(row[0], row[3], row[4], row[5], row[6], row[7], row[1], row[2])
                            for row in changed if row[2] in existing])
            db.executemany("DELETE FROM messages WHERE branch_id=? AND message_id=?",
                           [(branch, old_id) for old_id in existing if old_id not in current_ids])
            db.execute("INSERT INTO indexed_branches VALUES (?,?) ON CONFLICT(branch_id) DO UPDATE SET revision=excluded.revision", (branch, state.meta.branch_revision))

    def schedule(self, state: SessionState) -> None:
        branch = state.meta.id
        # Capture committed input now. Cancellation cannot stop a running worker;
        # index_state's revision fence prevents that worker overwriting newer data.
        snapshot = state.model_copy(deep=True)
        old = self._scheduled.pop(branch, None)
        if old is not None:
            old.cancel()
        async def delayed() -> None:
            try:
                await asyncio.sleep(2)
                await asyncio.to_thread(self.index_state, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging
                logging.getLogger("mrp.search").exception("Story search indexing failed for %s", branch)
            finally:
                if self._scheduled.get(branch) is asyncio.current_task():
                    self._scheduled.pop(branch, None)
        self._scheduled[branch] = asyncio.create_task(delayed())

    def has_branch(self, branch_id: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM messages WHERE branch_id=? LIMIT 1", (branch_id,)).fetchone() is not None

    def delete_branch(self, branch_id: str) -> None:
        pending = self._scheduled.pop(branch_id, None)
        if pending is not None:
            pending.cancel()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM messages WHERE branch_id=?", (branch_id,))
            # Tombstone fences already running workers as well as scheduled ones.
            db.execute("INSERT INTO indexed_branches VALUES (?,?) ON CONFLICT(branch_id) DO UPDATE SET revision=excluded.revision", (branch_id, 9223372036854775807))

    def indexed_revision(self, branch_id: str) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT revision FROM indexed_branches WHERE branch_id=?", (branch_id,)).fetchone()
            return int(row[0]) if row else None

    def search(self, story_id: str, query: str, *, branch_id: str | None = None,
               actor: str | None = None, scene_id: str | None = None,
               bookmarked: bool = False, event: bool = False,
               cursor: int = 0, limit: int = 30) -> dict[str, Any]:
        conditions = ["story_id=?"]
        args: list[Any] = [story_id]
        if branch_id:
            conditions.append("branch_id=?"); args.append(branch_id)
        if actor:
            conditions.append("actor=?"); args.append(actor)
        if scene_id:
            conditions.append("scene_id=?"); args.append(scene_id)
        if bookmarked:
            conditions.append("bookmarked=1")
        if event:
            conditions.append("event=1")
        if query.strip():
            conditions.append("content LIKE ?")
            args.append(f"%{query.strip()}%")
        if self.fts and len(query.strip()) >= 3:
            conditions.append("rowid IN (SELECT rowid FROM message_fts WHERE message_fts MATCH ?)")
            args.append('"' + query.strip().replace('"', '""') + '"')
        sql = "SELECT story_id,branch_id,message_id,actor,scene_id,content,bookmarked,event FROM messages WHERE " + " AND ".join(conditions) + " ORDER BY rowid DESC LIMIT ? OFFSET ?"
        with self._connect() as db:
            try:
                rows = [dict(row) for row in db.execute(sql, (*args, limit + 1, cursor))]
            except sqlite3.OperationalError:
                # Some system SQLite builds lack trigram query support; LIKE still
                # works with the story/branch index and server-side pagination.
                if not self.fts or len(query.strip()) < 3:
                    raise
                fallback = sql.replace(" AND rowid IN (SELECT rowid FROM message_fts WHERE message_fts MATCH ?)", "")
                rows = [dict(row) for row in db.execute(fallback, (*args[:-1], limit + 1, cursor))]
        has_more = len(rows) > limit
        rows = rows[:limit]
        for row in rows:
            content = row.pop("content")
            index = max(0, content.casefold().find(query.strip().casefold())) if query.strip() else 0
            row["excerpt"] = content[max(0, index - 60):index + 160]
        return {"results": rows, "next_cursor": cursor + limit if has_more else None}
