"""Plain multi-turn chat, sharing gateway credentials and HTTP transport with stories."""
from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from mrp.engines.request_archive import RequestArchive
from mrp.llm import LlmConfig, StreamControl, chat_stream_with_usage
from mrp.settings import AppSettings, GenerationSettings, provider_profile_id
from mrp.storage.atomic import read_json, write_json_atomic
from mrp.server.sse import EventBus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlainChatConflictError(ValueError):
    """The confirmed edit/delete scope changed before its write lock was acquired."""


class PlainMessage(BaseModel):
    id: str = Field(default_factory=lambda: f"msg-{uuid.uuid4().hex}")
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=_now)
    variants: list[str] = Field(default_factory=list)
    active_variant: int | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    variant_usages: list[dict[str, Any]] = Field(default_factory=list)


class PlainGeneration(BaseModel):
    id: str = Field(default_factory=lambda: f"gen-{uuid.uuid4().hex}")
    message_id: str
    model: str
    gateway: str
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class PlainChat(BaseModel):
    id: str = Field(default_factory=lambda: f"chat-{uuid.uuid4().hex}")
    title: str = "新聊天"
    gateway: str
    model: str
    provider: str = ""
    provider_allow_fallbacks: bool = True
    system_prompt: str = ""
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    price_currency: str = "USD"
    messages: list[PlainMessage] = Field(default_factory=list)
    generations: list[PlainGeneration] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PlainChatService:
    def __init__(self, data_root: Path, settings: Callable[[], AppSettings],
                 bus: EventBus, archive: RequestArchive) -> None:
        self.root = Path(data_root) / "simple_chats"
        self.settings = settings
        self.bus = bus
        self.archive = archive
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_generations: dict[str, StreamControl] = {}

    def _path(self, chat_id: str) -> Path:
        if not re.fullmatch(r"chat-[0-9a-f]{32}", chat_id):
            raise ValueError("无效的聊天 ID")
        return self.root / f"{chat_id}.json"

    def _lock(self, chat_id: str) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def list(self) -> list[dict[str, Any]]:
        def read_all() -> list[dict[str, Any]]:
            if not self.root.exists():
                return []
            rows: list[dict[str, Any]] = []
            for path in self.root.glob("chat-*.json"):
                raw = read_json(path)
                if not isinstance(raw, dict):
                    continue
                try:
                    chat = PlainChat.model_validate(raw)
                except ValueError:
                    continue
                rows.append({
                    "id": chat.id, "title": chat.title, "model": chat.model,
                    "gateway": chat.gateway, "updated_at": chat.updated_at.isoformat(),
                    "message_count": len(chat.messages),
                })
            return sorted(rows, key=lambda row: row["updated_at"], reverse=True)
        return await asyncio.to_thread(read_all)

    async def get(self, chat_id: str) -> PlainChat | None:
        try:
            path = self._path(chat_id)
        except ValueError:
            return None
        raw = await asyncio.to_thread(read_json, path)
        if not isinstance(raw, dict):
            return None
        try:
            chat = PlainChat.model_validate(raw)
            if "generation" not in raw:
                chat.generation = self.settings().generation.model_copy(deep=True)
            if "generations" not in raw:
                chat.generations = [
                    PlainGeneration(message_id=message.id, model=chat.model, gateway=chat.gateway,
                                    usage=message.usage, created_at=message.created_at)
                    for message in chat.messages if message.role == "assistant" and message.usage
                ]
            return chat
        except ValueError:
            return None

    async def _save(self, chat: PlainChat) -> None:
        chat.updated_at = _now()
        await asyncio.to_thread(write_json_atomic, self._path(chat.id), chat, indent=None)

    async def create(self) -> PlainChat:
        settings = self.settings()
        chat = PlainChat(
            gateway=settings.gateway, model=settings.model,
            provider=settings.model_provider,
            provider_allow_fallbacks=settings.provider_allow_fallbacks,
            generation=settings.generation.model_copy(deep=True),
        )
        await self._save(chat)
        return chat

    async def delete(self, chat_id: str) -> bool:
        async with self._lock(chat_id):
            path = self._path(chat_id)
            if not path.exists():
                return False
            await asyncio.to_thread(path.unlink)
            await asyncio.to_thread(self.archive.delete_session, chat_id)
            return True

    async def patch(self, chat_id: str, changes: dict[str, Any]) -> PlainChat | None:
        async with self._lock(chat_id):
            chat = await self.get(chat_id)
            if chat is None:
                return None
            for field, value in changes.items():
                if field == "generation":
                    value = GenerationSettings.model_validate(value)
                setattr(chat, field, value)
            await self._save(chat)
            return chat

    async def edit_message(self, chat_id: str, message_id: str, content: str,
                           truncate_after: bool, expected_updated_at: datetime | None = None) -> PlainChat | None:
        async with self._lock(chat_id):
            chat = await self.get(chat_id)
            if chat is None:
                return None
            if expected_updated_at is not None and chat.updated_at != expected_updated_at:
                raise PlainChatConflictError("聊天已经变化，请重新核对影响范围后再保存；本次没有修改消息。")
            index = next((i for i, message in enumerate(chat.messages) if message.id == message_id), -1)
            if index < 0:
                raise ValueError("消息不存在")
            if index < len(chat.messages) - 1 and not truncate_after:
                raise ValueError("编辑历史消息需要确认移除后续对话")
            message = chat.messages[index]
            message.content = content
            if message.role == "assistant":
                message.variants = [content]
                message.active_variant = 0
                message.usage = {}
                message.variant_usages = [{}]
            chat.messages = chat.messages[:index + 1]
            await self._save(chat)
            return chat

    async def delete_message(self, chat_id: str, message_id: str,
                             truncate_after: bool, expected_updated_at: datetime | None = None) -> PlainChat | None:
        async with self._lock(chat_id):
            chat = await self.get(chat_id)
            if chat is None:
                return None
            if expected_updated_at is not None and chat.updated_at != expected_updated_at:
                raise PlainChatConflictError("聊天已经变化，请重新核对删除范围；本次没有删除消息。")
            index = next((i for i, message in enumerate(chat.messages) if message.id == message_id), -1)
            if index < 0:
                raise ValueError("消息不存在")
            if index < len(chat.messages) - 1 and not truncate_after:
                raise ValueError("删除历史消息需要确认移除后续对话")
            chat.messages = chat.messages[:index]
            await self._save(chat)
            return chat

    async def switch_variant(self, chat_id: str, message_id: str,
                             active_variant: int) -> PlainChat | None:
        async with self._lock(chat_id):
            chat = await self.get(chat_id)
            if chat is None:
                return None
            message = next((item for item in chat.messages if item.id == message_id), None)
            if message is None or message.role != "assistant":
                raise ValueError("无法切换此消息的候选")
            if not 0 <= active_variant < len(message.variants):
                raise ValueError("候选序号无效")
            message.active_variant = active_variant
            message.content = message.variants[active_variant]
            if active_variant < len(message.variant_usages):
                message.usage = message.variant_usages[active_variant]
            await self._save(chat)
            return chat

    def _key_for(self, gateway: str) -> str:
        settings = self.settings()
        profile_id = provider_profile_id(gateway)
        key = settings.provider_api_keys.get(profile_id, "")
        if not key and provider_profile_id(settings.gateway) == profile_id:
            key = settings.api_key
        if not key:
            raise ValueError("此渠道尚未保存 API Key，请先在连接与模型设置中配置")
        return key

    async def _generate(
        self,
        chat: PlainChat,
        *,
        stream_control: StreamControl,
        replace_last: PlainMessage | None = None,
    ) -> PlainChat:
        key = self._key_for(chat.gateway)
        history = chat.messages[:-1] if replace_last is not None else chat.messages
        messages: list[dict[str, str]] = []
        if chat.system_prompt.strip():
            messages.append({"role": "system", "content": chat.system_prompt})
        messages.extend({"role": item.role, "content": item.content} for item in history)
        if not messages:
            raise ValueError("请先输入消息")
        settings = self.settings()
        config = LlmConfig(
            model=chat.model, base_url=chat.gateway, api_key_env="OPENROUTER_API_KEY",
            api_key=key, provider=chat.provider,
            provider_allow_fallbacks=chat.provider_allow_fallbacks,
            include_usage_cost=provider_profile_id(chat.gateway) == "openrouter",
            sampling=chat.generation.request_parameters(),
        )
        turn = sum(item.role == "user" for item in history)
        pending_id = replace_last.id if replace_last else f"msg-{uuid.uuid4().hex}"
        await self.bus.publish(chat.id, "message.pending", {"id": pending_id, "role": "assistant"})
        loop = asyncio.get_running_loop()
        sends: list[Future[Any]] = []

        def on_delta(piece: str) -> None:
            sends.append(asyncio.run_coroutine_threadsafe(
                self.bus.publish(chat.id, "message.delta", {"id": pending_id, "delta": piece}, lossy=True),
                loop,
            ))

        def on_request(body: dict[str, Any]) -> None:
            self.archive.write(chat.id, "assistant", turn, "simple_chat", body)

        try:
            text, usage = await asyncio.to_thread(
                chat_stream_with_usage, messages, config, on_delta,
                max_tokens=chat.generation.max_output_tokens or 0, timeout=300, no_thinking=(
                    settings.thinking == "off" and provider_profile_id(chat.gateway) == "openrouter"
                ), on_request=on_request, stream_control=stream_control,
            )
            if sends:
                await asyncio.gather(*(asyncio.wrap_future(sent) for sent in sends), return_exceptions=True)
            if not text.strip() and stream_control.stopped:
                await self.bus.publish(chat.id, "message.cancelled", {"id": pending_id})
                return chat
            if not text.strip():
                raise RuntimeError("模型返回空回复")
        except Exception as exc:
            await self.bus.publish(chat.id, "message.error", {"id": pending_id, "error": str(exc)[:300]})
            raise
        if (
            "cost_usd" not in usage
            and not usage.get("usage_incomplete")
            and chat.input_price_per_million is not None
            and chat.output_price_per_million is not None
        ):
            usage["estimated_cost"] = round(
                (usage.get("input_tokens", 0) * chat.input_price_per_million
                 + usage.get("output_tokens", 0) * chat.output_price_per_million) / 1_000_000,
                10,
            )
            usage["cost_currency"] = chat.price_currency
            usage["cost_source"] = "estimate"
        if replace_last is not None:
            if not replace_last.variants:
                replace_last.variants = [replace_last.content]
            if not replace_last.variant_usages:
                replace_last.variant_usages = [replace_last.usage] + [{} for _ in replace_last.variants[1:]]
            replace_last.variants.append(text)
            replace_last.variant_usages.append(usage)
            replace_last.active_variant = len(replace_last.variants) - 1
            replace_last.content = text
            replace_last.usage = usage
            reply = replace_last
        else:
            reply = PlainMessage(role="assistant", id=pending_id, content=text,
                                 variants=[text], active_variant=0, usage=usage,
                                 variant_usages=[usage])
            chat.messages.append(reply)
        chat.generations.append(PlainGeneration(
            message_id=reply.id, model=chat.model, gateway=chat.gateway, usage=usage,
        ))
        await self._save(chat)
        await self.bus.publish(chat.id, "message.final", {"message": reply.model_dump(mode="json")})
        return chat

    async def send(self, chat_id: str, content: str, client_message_id: str | None = None) -> PlainChat | None:
        async with self._lock(chat_id):
            stream_control = StreamControl()
            self._active_generations[chat_id] = stream_control
            try:
                chat = await self.get(chat_id)
                if chat is None:
                    return None
                if client_message_id is not None and not re.fullmatch(r"msg-[0-9a-f]{32}", client_message_id):
                    raise ValueError("消息 ID 无效")
                if client_message_id and any(item.id == client_message_id for item in chat.messages):
                    return chat
                chat.messages.append(PlainMessage(role="user", id=client_message_id or f"msg-{uuid.uuid4().hex}", content=content))
                if chat.title == "新聊天" and len(chat.messages) == 1:
                    chat.title = content.replace("\n", " ").strip()[:32] or "新聊天"
                await self._save(chat)
                await self.bus.publish(chat.id, "message.final", {
                    "message": chat.messages[-1].model_dump(mode="json")
                })
                return await self._generate(chat, stream_control=stream_control)
            finally:
                if self._active_generations.get(chat_id) is stream_control:
                    self._active_generations.pop(chat_id, None)

    async def regenerate(self, chat_id: str, message_id: str) -> PlainChat | None:
        async with self._lock(chat_id):
            stream_control = StreamControl()
            self._active_generations[chat_id] = stream_control
            try:
                chat = await self.get(chat_id)
                if chat is None:
                    return None
                if not chat.messages or chat.messages[-1].id != message_id or chat.messages[-1].role != "assistant":
                    raise ValueError("只能重新生成最后一条助手回复")
                return await self._generate(chat, stream_control=stream_control, replace_last=chat.messages[-1])
            finally:
                if self._active_generations.get(chat_id) is stream_control:
                    self._active_generations.pop(chat_id, None)

    def stop_generation(self, chat_id: str) -> bool:
        """Request cancellation without waiting for the chat's generation lock."""
        self._path(chat_id)
        control = self._active_generations.get(chat_id)
        if control is None:
            return False
        control.request_stop()
        return True


def valid_gateway(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
