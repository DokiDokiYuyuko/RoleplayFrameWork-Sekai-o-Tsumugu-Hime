"""Read-only story projections: never construct engines or migrate a file on GET."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from mrp.contracts.story import (
    StoryView, SessionView, SessionSetup, MessagePage, SetupMeta, project_message,
    project_turn_run, project_conversation_run, project_character,
)
from mrp.server.deps import get_container
from mrp.shared.models import Character
from mrp.shared.run_windows import select_page_runs

router = APIRouter()


async def _visible(container,session_id):
    try:
        await asyncio.to_thread(container.sessions.require_visible_branch,session_id)
    except ValueError as exc:
        raise HTTPException(404,'会话不存在') from exc

async def _cache_current(container,session_id):
    runner=container.runners.get(session_id)
    if runner is None: return None
    if await asyncio.to_thread(container.sessions.story_db.owns,session_id):
        revision=await asyncio.to_thread(container.sessions.story_db.revision,session_id)
        if revision != runner.public_head.meta.branch_revision: return None
    return runner

async def _read(container, session_id):
    await _visible(container,session_id)
    runner=await _cache_current(container,session_id)
    if runner is not None: return runner.public_head,runner
    state=await container.sessions.load_state_readonly(session_id)
    if state is None: raise HTTPException(404,'会话不存在')
    return state,None


def _window(messages, *, limit, before_seq=None, around=None):
    ordered = sorted(messages, key=lambda message: message["seq"])
    if before_seq is not None and around is not None:
        raise HTTPException(422, "before_seq 与 around 不能同时使用")
    if around is not None:
        position = next((i for i, message in enumerate(ordered) if message["id"] == around), None)
        if position is None:
            raise HTTPException(404, "定位消息不存在")
        start = max(0, position - limit // 2)
        end = min(len(ordered), start + limit)
    else:
        end = next((i for i, message in enumerate(ordered) if message["seq"] >= before_seq), len(ordered)) if before_seq is not None else len(ordered)
        start = max(0, end - limit)
    # A submitted input group is indivisible even when it exceeds the page limit.
    if start < end:
        group = ordered[start].get("input_group_id")
        while group and start > 0 and ordered[start - 1].get("input_group_id") == group:
            start -= 1
        group = ordered[end - 1].get("input_group_id")
        while group and end < len(ordered) and ordered[end].get("input_group_id") == group:
            end += 1
    return ordered[start:end], ordered[start]["seq"] if start > 0 and start < end else None


def _messages(state, runner):
    # Project each selected object before transmission; no full state model_dump.
    values = {message.id: message for message in state.messages}
    if runner is not None:
        for mid, pending in runner.runtime.pending_messages.items():
            if pending.get("session_id") != state.meta.id:
                continue
            existing = values.get(mid)
            if existing is None or getattr(existing, "status", None) != "final":
                values[mid] = pending
    return values.values()


def _page(state, runner, limit, before_seq, around):
    # Only shallow indexing is required before selecting the window.
    objects = list(_messages(state, runner))
    by_id = {value["id"] if isinstance(value, dict) else value.id: value for value in objects}
    indexes = [value if isinstance(value, dict) else {"id": value.id, "seq": value.seq, "input_group_id": value.input_group_id} for value in objects]
    window, cursor = _window(indexes, limit=limit, before_seq=before_seq, around=around)
    return {"messages": [project_message(by_id[value["id"]]) for value in window],
            "next_before_seq": cursor, "turn": state.current_turn(), "branch_revision": state.meta.branch_revision,
            "latest_seq": max((item["seq"] for item in indexes), default=-1)}


async def _read_page(container, session_id, limit, before_seq, around):
    if before_seq is not None and around is not None:
        raise HTTPException(422, "before_seq 与 around 不能同时使用")
    await _visible(container,session_id)
    if await _cache_current(container,session_id) is None and await asyncio.to_thread(container.sessions.story_db.owns, session_id):
        try:
            window = await asyncio.to_thread(container.sessions.story_db.read_window, session_id,
                limit=limit, before_seq=before_seq, around=around)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        if window is None:
            raise HTTPException(404, "会话不存在")
        if await _cache_current(container,session_id) is None:
            state, turn, latest_seq, cursor = window
            page = {"messages": [project_message(message) for message in state.messages],
                    "turn": turn, "latest_seq": latest_seq if latest_seq is not None else -1,
                    "next_before_seq": cursor, "branch_revision": state.meta.branch_revision}
            return state, page, True
    state, runner = await _read(container, session_id)
    return state, _page(state, runner, limit, before_seq, around), False


@router.get("/api/v1/sessions/{session_id}/view", response_model=StoryView)
async def story_view(session_id: str, limit: int = Query(100, ge=1, le=100),
                     before_seq: int | None = Query(None, ge=0), around: str | None = None,
                     container=Depends(get_container)):
    state, page, windowed = await _read_page(container, session_id, limit, before_seq, around)
    characters = [project_character(character) for character in state.characters]
    people = {key: project_character(value) for key, value in state.player_people.items()}
    meta = state.meta.model_dump(mode="json", include=set(SessionView.model_fields) | {"player_persona"})
    session = {**meta, "persona": meta["player_persona"], "turn": page["turn"],
               "character_names": [character.card.name for character in state.characters],
               "pinned_facts": state.pinned_facts, "player_identities": state.player_identities, "player_people": people}
    return {**page, "session": session,
            "characters": characters, "groups": state.groups,
            "scenes": [scene for scene in state.scenes if scene.id == state.active_scene_id],
            "active_scene_id": state.active_scene_id, "player_identities": state.player_identities,
            "player_people": people, "turn_runs": [project_turn_run(run) for run in (state.turn_runs if windowed else select_page_runs(state.turn_runs,page["messages"]))],
            "conversation_runs": [project_conversation_run(run) for run in (state.conversation_runs if windowed else select_page_runs(state.conversation_runs,page["messages"]))], "pending_director": state.pending_director,
            "event_cursor": container.bus.current_cursor(session_id)}


@router.get("/api/v1/sessions/{session_id}/setup", response_model=SessionSetup)
async def story_setup(session_id: str, container=Depends(get_container)):
    await _visible(container,session_id)
    if await _cache_current(container,session_id) is None and await asyncio.to_thread(container.sessions.story_db.owns, session_id):
        result = await asyncio.to_thread(container.sessions.story_db.read_setup, session_id)
        if result is None:
            raise HTTPException(404, "会话不存在")
        if await _cache_current(container,session_id) is None:
            result["meta"] = SetupMeta.model_validate(result["meta"])
            result["characters"] = [project_character(Character.model_validate(value)) for value in result["characters"]]
            return result
    state, _ = await _read(container, session_id)
    return {"meta": state.meta.model_dump(mode="json", include=set(SetupMeta.model_fields)),
            "characters": [project_character(value) for value in state.characters],
            "lorebooks": [{"id": book.id, "name": book.name, "source_format": book.source_format,
                           "entry_count": len(book.entries), "entries": []} for book in state.lorebooks]}


@router.get("/api/v1/sessions/{session_id}/messages", response_model=MessagePage)
async def story_messages(session_id: str, limit: int = Query(100, ge=1, le=100),
                         before_seq: int | None = Query(None, ge=0), around: str | None = None,
                         container=Depends(get_container)):
    _, page, _ = await _read_page(container, session_id, limit, before_seq, around)
    return page
