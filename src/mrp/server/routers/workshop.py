"""工坊与编辑器路由（R23/R29/R31/R40，M5）：生成 / 试聊 / 改名 / persona 预览。"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mrp.server.container import AppContainer
from mrp.server.deps import get_container, llm_call, workshop_module
from mrp.shared.models import CharacterCard

router = APIRouter()


class GenerateCharactersReq(BaseModel):
    requirement: str = Field(max_length=2000)
    detail: str = Field("", max_length=5000)
    reference_lorebook_ids: list[str] = Field(default_factory=list, max_length=10)
    count: int = 3


class PreviewMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=5000)


class PreviewTurnReq(BaseModel):
    card: CharacterCard
    message: str = Field(min_length=1, max_length=5000)
    history: list[PreviewMessage] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_history(self):
        if len(self.history) % 2 or any(row.role != ("user" if i % 2 == 0 else "assistant")
                                       for i, row in enumerate(self.history)):
            raise ValueError("试聊历史必须为完整的玩家与角色交换")
        if sum(len(row.content) for row in self.history) > 20000:
            raise ValueError("试聊历史超过 20000 字符，请重新试聊")
        if not self.message.strip() or any(not row.content.strip() for row in self.history):
            raise ValueError("试聊消息不能为空")
        return self


class AiEditReq(BaseModel):
    card: CharacterCard
    field: str  # description/personality/scenario/first_mes/mes_example
    instruction: str = Field(min_length=1, max_length=2000)
    scope: Literal["field", "selection"] = "field"
    selection_text: str | None = Field(None, max_length=30000)


class PersonaPreviewReq(BaseModel):
    card: CharacterCard


class GenerateLorebookReq(BaseModel):
    topic: str = Field(max_length=500)
    concepts: list[str] = Field(default_factory=list, max_length=20)
    reference_lorebook_id: str | None = None
    count: int = 8


class ExtendLorebookReq(BaseModel):
    book_id: str
    direction: str = Field(max_length=1000)
    count: int = 4


@router.post("/api/v1/characters/generate")
async def generate_characters(
    req: GenerateCharactersReq, container: AppContainer = Depends(get_container)
):
    """R23.1：需求 → 2-3 张候选卡（不入库，编辑确认后才保存）。"""
    refs = [
        container.lorebooks[bid]
        for bid in req.reference_lorebook_ids
        if bid in container.lorebooks
    ]
    count = max(1, min(req.count, 5))
    try:
        results = await asyncio.to_thread(
            workshop_module().generate_character_cards,
            req.requirement,
            req.detail,
            refs,
            count,
            llm_call(container),
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, f"生成失败: {e}") from e
    return [
        {"card": card.model_dump(mode="json"), "aliases": aliases} for card, aliases in results
    ]


@router.post("/api/v1/characters/preview-turn")
async def preview_turn(req: PreviewTurnReq, container: AppContainer = Depends(get_container)):
    """R31.8：沙盒试聊——未保存的卡即时生成一句回复（不建会话不写记忆）。"""
    try:
        reply = await asyncio.to_thread(
            workshop_module().preview_turn, req.card, req.message, llm_call(container),
            [row.model_dump() for row in req.history],
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, f"试聊失败: {e}") from e
    if not isinstance(reply, str) or not reply.strip():
        raise HTTPException(502, "模型返回空回复，请重试")
    if len(reply) > 5000:
        raise HTTPException(502, "试聊回复超过单条 5000 字符限制，请缩短输入后重试")
    return {"reply": reply}


@router.post("/api/v1/characters/ai-edit")
async def ai_edit(req: AiEditReq, container: AppContainer = Depends(get_container)):
    """R31.7：字段级 AI 改写（返回候选值，不自动保存）。"""
    try:
        value = await asyncio.to_thread(
            workshop_module().ai_edit_field,
            req.card,
            req.field,
            req.instruction,
            llm_call(container),
            scope=req.scope,
            selection_text=req.selection_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, f"改写失败: {e}") from e
    return {"value": value}


@router.post("/api/v1/characters/persona-preview")
async def persona_preview(req: PersonaPreviewReq, container: AppContainer = Depends(get_container)):
    """R31.5：persona 实时预览（与引擎同函数保证一致）。"""
    return workshop_module().persona_preview(req.card)


@router.post("/api/v1/lorebooks/generate")
async def generate_lorebook(
    req: GenerateLorebookReq, container: AppContainer = Depends(get_container)
):
    """R29.1：主题 → 候选世界书（不入库）。"""
    ref = container.lorebooks.get(req.reference_lorebook_id or "") or None
    count = max(1, min(req.count, 20))
    try:
        book = await asyncio.to_thread(
            workshop_module().generate_lorebook,
            req.topic,
            req.concepts,
            ref,
            count,
            llm_call(container),
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, f"生成失败: {e}") from e
    return book.model_dump(mode="json")


@router.post("/api/v1/lorebooks/generate/extend")
async def extend_lorebook(
    req: ExtendLorebookReq, container: AppContainer = Depends(get_container)
):
    """R29.3：现有书扩展生成（只产新条目，uid 续号）。"""
    book = container.lorebooks.get(req.book_id)
    if book is None:
        raise HTTPException(404, f"世界书不存在: {req.book_id}")
    try:
        entries = await asyncio.to_thread(
            workshop_module().extend_lorebook,
            book,
            req.direction,
            max(1, min(req.count, 10)),
            llm_call(container),
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, f"生成失败: {e}") from e
    return [e.model_dump(mode="json") for e in entries]
