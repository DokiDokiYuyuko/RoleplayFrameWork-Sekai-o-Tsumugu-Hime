"""Plain chat API, separate from story sessions and roleplay orchestration."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.server.sse import sse_stream
from mrp.settings import provider_profile_id
from mrp.settings import GenerationSettings
from mrp.simple_chat import PlainChatConflictError, valid_gateway


router = APIRouter(prefix="/api/v1/simple-chats", tags=["simple-chats"])


class PatchChatReq(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    gateway: str | None = Field(None, min_length=1, max_length=500)
    model: str | None = Field(None, min_length=1, max_length=256)
    provider: str | None = Field(None, max_length=128)
    provider_allow_fallbacks: bool | None = None
    system_prompt: str | None = Field(None, max_length=20000)
    generation: GenerationSettings | None = None
    input_price_per_million: float | None = Field(None, ge=0)
    output_price_per_million: float | None = Field(None, ge=0)
    price_currency: str | None = Field(None, min_length=3, max_length=3)


class SendReq(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    client_message_id: str | None = None


class EditReq(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    truncate_after: bool = False
    expected_updated_at: datetime | None = None


class DeleteMessageReq(BaseModel):
    truncate_after: bool = False
    expected_updated_at: datetime | None = None


class SwitchVariantReq(BaseModel):
    active_variant: int = Field(ge=0)


@router.get("")
async def list_chats(container: AppContainer = Depends(get_container)):
    return {"chats": await container.simple_chats.list()}


@router.post("")
async def create_chat(container: AppContainer = Depends(get_container)):
    return await container.simple_chats.create()


@router.get("/{chat_id}")
async def get_chat(chat_id: str, container: AppContainer = Depends(get_container)):
    chat = await container.simple_chats.get(chat_id)
    if chat is None:
        raise HTTPException(404, "聊天不存在")
    return chat


@router.patch("/{chat_id}")
async def patch_chat(chat_id: str, req: PatchChatReq,
                     container: AppContainer = Depends(get_container)):
    changes = req.model_dump(exclude_unset=True)
    if "gateway" in changes:
        changes["gateway"] = changes["gateway"].strip().rstrip("/")
        if not valid_gateway(changes["gateway"]):
            raise HTTPException(400, "网关地址须为有效的 http/https URL")
    for field in ("title", "model", "provider"):
        if field in changes:
            changes[field] = changes[field].strip()
    if changes.get("title") == "" or changes.get("model") == "":
        raise HTTPException(400, "标题和模型不能为空")
    if "price_currency" in changes:
        if changes["price_currency"] is None:
            raise HTTPException(400, "价格币种不能为空")
        changes["price_currency"] = changes["price_currency"].upper()
    current = await container.simple_chats.get(chat_id)
    if current is None:
        raise HTTPException(404, "聊天不存在")
    target_gateway = changes.get("gateway", current.gateway)
    if provider_profile_id(target_gateway) != "openrouter":
        changes["provider"] = ""
    chat = await container.simple_chats.patch(chat_id, changes)
    return chat


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, container: AppContainer = Depends(get_container)):
    try:
        deleted = await container.simple_chats.delete(chat_id)
    except ValueError:
        deleted = False
    if not deleted:
        raise HTTPException(404, "聊天不存在")
    return {"ok": True}


@router.get("/{chat_id}/events")
async def chat_events(chat_id: str, container: AppContainer = Depends(get_container)):
    if await container.simple_chats.get(chat_id) is None:
        raise HTTPException(404, "聊天不存在")
    return StreamingResponse(
        sse_stream(container.bus, chat_id), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/stop")
async def stop_generation(chat_id: str, container: AppContainer = Depends(get_container)):
    try:
        stopping = container.simple_chats.stop_generation(chat_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"stopping": stopping}


@router.post("/{chat_id}/messages")
async def send(chat_id: str, req: SendReq, container: AppContainer = Depends(get_container)):
    content = req.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    try:
        chat = await container.simple_chats.send(chat_id, content, req.client_message_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"模型调用失败：{str(exc)[:250]}") from exc
    if chat is None:
        raise HTTPException(404, "聊天不存在")
    return chat


@router.patch("/{chat_id}/messages/{message_id}")
async def edit_message(chat_id: str, message_id: str, req: EditReq,
                       container: AppContainer = Depends(get_container)):
    content = req.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    try:
        chat = await container.simple_chats.edit_message(
            chat_id, message_id, content, req.truncate_after, req.expected_updated_at
        )
    except PlainChatConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if chat is None:
        raise HTTPException(404, "聊天不存在")
    return chat


@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(chat_id: str, message_id: str, req: DeleteMessageReq,
                         container: AppContainer = Depends(get_container)):
    try:
        chat = await container.simple_chats.delete_message(
            chat_id, message_id, req.truncate_after, req.expected_updated_at
        )
    except PlainChatConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if chat is None:
        raise HTTPException(404, "聊天不存在")
    return chat


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate(chat_id: str, message_id: str,
                     container: AppContainer = Depends(get_container)):
    try:
        chat = await container.simple_chats.regenerate(chat_id, message_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"模型调用失败：{str(exc)[:250]}") from exc
    if chat is None:
        raise HTTPException(404, "聊天不存在")
    return chat


@router.patch("/{chat_id}/messages/{message_id}/variant")
async def switch_variant(chat_id: str, message_id: str, req: SwitchVariantReq,
                         container: AppContainer = Depends(get_container)):
    try:
        chat = await container.simple_chats.switch_variant(
            chat_id, message_id, req.active_variant
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if chat is None:
        raise HTTPException(404, "聊天不存在")
    return chat
