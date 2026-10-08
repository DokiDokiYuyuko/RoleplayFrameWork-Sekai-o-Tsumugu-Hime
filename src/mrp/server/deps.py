"""路由共享依赖与 helper（容器注入 / 404 包装 / LLM 调用）。

约束：routers 内**不得**引用模块级全局容器——一律通过 `Depends(get_container)`
从 `request.app.state.container` 取，保证两个 `create_app()` 实例互不影响。
"""
from __future__ import annotations

from typing import Any, Callable
from copy import deepcopy

from fastapi import HTTPException, Request

from mrp.server.container import DEFAULT_OPENROUTER_URL, AppContainer
from mrp.shared.models import Message


def get_container(request: Request) -> AppContainer:
    """FastAPI 依赖：当前 app 实例的容器。"""
    return request.app.state.container


async def runner_or_404(container: AppContainer, session_id: str):
    r = await container.load_session(session_id)
    if r is None:
        raise HTTPException(404, f"会话不存在: {session_id}")
    return r


def state_dict(r) -> dict[str, Any]:
    # Ordinary input and scene preparation may modify the working model while
    # its checkpoint is being saved. Reads retain the last durable story head;
    # only a runtime generation prefix may be projected as pending below.
    from mrp.application.branch_commit import committed_head
    snapshot = committed_head(r).model_dump(mode="json")
    by_id = {message["id"]: message for message in snapshot["messages"]}
    for pending in r.runtime.pending_messages.values():
        if pending.get("session_id") != r.state.meta.id:
            continue
        current = by_id.get(pending["id"])
        if current is not None and current["status"] == "final":
            continue
        if current is not None:
            current.update(deepcopy(pending))
        else:
            snapshot["messages"].append(deepcopy(pending))
    return snapshot


def message_or_404(r, message_id: str) -> Message:
    msg = next((m for m in r.state.messages if m.id == message_id), None)
    if msg is None:
        raise HTTPException(404, f"消息不存在: {message_id}")
    return msg


def workshop_module():
    """懒加载工坊模块（生成器函数集合）。"""
    from mrp.orchestrator import workshop

    return workshop


def llm_call(container: AppContainer) -> Callable[[list[dict]], str]:
    """工坊生成用的聊天函数（模型/网关来自容器配置，与设置页 info 同源）。"""
    from mrp.llm import LlmConfig, chat_text

    cfg = LlmConfig(
        model=container.config.model,
        base_url=container.config.base_url or DEFAULT_OPENROUTER_URL,
        api_key_env=container.config.api_key_env,
        provider=container.settings.auxiliary_provider,
        provider_allow_fallbacks=container.settings.provider_allow_fallbacks,
    )
    return lambda messages: chat_text(messages, cfg)
