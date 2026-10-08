"""会话固定信息：从消息固定事实、手动添加、编辑和取消固定。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from mrp.server.container import AppContainer
from mrp.server.deps import get_container, runner_or_404
from mrp.shared.models import PinnedFact, Visibility
from mrp.shared.prompt import estimate_tokens

from mrp.server.command_ids import command_id, command_payload, execute_command

router = APIRouter()

MAX_PINNED_FACTS = 20
MAX_PINNED_FACTS_TOKENS = 700


class CreatePinnedFactReq(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    message_id: str | None = None
    visible_to: Visibility = "all"


class PatchPinnedFactReq(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


def _validate_visibility(visible_to: Visibility, character_ids: set[str]) -> None:
    if visible_to == "all":
        return
    if not visible_to or len(visible_to) != len(set(visible_to)):
        raise HTTPException(400, "固定信息至少要指定一个角色，且不能重复")
    if not set(visible_to).issubset(character_ids):
        raise HTTPException(400, "固定信息只能指定当前会话中的角色")


def _validate_budget(facts: list[PinnedFact]) -> None:
    content = (
        "[本局固定信息]\n"
        "以下由玩家明确标记为重要事实。后续回复应与其保持一致；若剧情明示事实已改变，则以新事实为准。\n"
        + "\n".join(f"- {fact.content.strip()}" for fact in facts)
    )
    if estimate_tokens(content) > MAX_PINNED_FACTS_TOKENS:
        raise HTTPException(400, "固定信息总量超过每轮注入上限（约 700 tokens），请精简内容或移除部分条目")


@router.post("/api/v1/sessions/{session_id}/pinned-facts")
async def create_pinned_fact(
    session_id: str,
    req: CreatePinnedFactReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        content = req.content.strip()
        if not content:
            raise HTTPException(400, "固定信息不能为空")

        source = None
        visible_to = req.visible_to
        source_actor = None
        if req.message_id:
            source = next((m for m in runner.state.messages if m.id == req.message_id), None)
            if source is None:
                raise HTTPException(404, "来源消息不存在")
            if source.status != "final" or source.kind in ("inner", "system_event"):
                raise HTTPException(400, "只能固定已完成的对话或场景消息")
            # 固定消息时沿用原消息的可见范围，避免把私密发言扩散给其他角色。
            visible_to = source.visible_to
            if isinstance(visible_to, list):
                visible_to = [cid for cid in visible_to if cid in set(runner.state.meta.character_ids)]
                if not visible_to:
                    raise HTTPException(400, "来源消息没有可注入的角色可见范围")
            source_actor = source.actor
            existing = next(
                (fact for fact in runner.state.pinned_facts if fact.source_message_id == source.id),
                None,
            )
            if existing is not None:
                return existing.model_dump(mode="json")
        else:
            _validate_visibility(visible_to, set(runner.state.meta.character_ids))

        if len(runner.state.pinned_facts) >= MAX_PINNED_FACTS:
            raise HTTPException(400, f"每局最多固定 {MAX_PINNED_FACTS} 条信息")
        fact = PinnedFact(
            content=content,
            source_message_id=source.id if source else None,
            source_actor=source_actor,
            visible_to=visible_to,
        )
        _validate_budget([*runner.state.pinned_facts, fact])
        runner.state.pinned_facts.append(fact)
        return fact.model_dump(mode="json")
    return await execute_command(container, runner, f"pin.create", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response)


@router.patch("/api/v1/sessions/{session_id}/pinned-facts/{fact_id}")
async def patch_pinned_fact(
    session_id: str,
    fact_id: str,
    req: PatchPinnedFactReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        fact = next((item for item in runner.state.pinned_facts if item.id == fact_id), None)
        if fact is None:
            raise HTTPException(404, "固定信息不存在")
        content = req.content.strip()
        if not content:
            raise HTTPException(400, "固定信息不能为空")
        updated = fact.model_copy(update={"content": content})
        _validate_budget([updated if item.id == fact_id else item for item in runner.state.pinned_facts])
        fact.content = content
        return fact.model_dump(mode="json")
    return await execute_command(container, runner, f"pin.patch:{fact_id}", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response)


@router.delete("/api/v1/sessions/{session_id}/pinned-facts/{fact_id}")
async def delete_pinned_fact(
    session_id: str,
    fact_id: str,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        before = len(runner.state.pinned_facts)
        runner.state.pinned_facts = [item for item in runner.state.pinned_facts if item.id != fact_id]
        if len(runner.state.pinned_facts) == before:
            raise HTTPException(404, "固定信息不存在")
        return {"ok": True}
    return await execute_command(container, runner, f"pin.delete:{fact_id}", {}, mutate,
        operation_id=command_id(operation_header), response=response)
