"""OpenRouterEngine：原生 OpenRouter 直连引擎（CharacterEngine 第二实现，R48）。

与 DshEngine 的差异：
- 无子进程、无审计会话目录：一次 generate = 一次 chat/completions 请求；
- 支持**真流式**：on_delta 逐片回调（在工作线程触发，调用方需线程安全）；
- 思考开关：on → `reasoning.effort="low"`；off → `reasoning.enabled=false`。

prompt 口径与 DSH 完全一致（同一 compose_prompt + 同一 persona），
两套引擎可在同一会话中途切换（会话历史在编排侧，引擎无状态）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from mrp.engines.base import DeltaCallback
from mrp.engines.request_archive import RequestArchive
from mrp.reasoning import normalize_reasoning
from mrp.settings import GenerationSettings
from mrp.shared.models import (
    Character,
    EngineHealth,
    EngineReply,
    TokenUsage,
    TurnContext,
)
from mrp.shared.prompt import compose_prompt, persona_from_card, estimate_tokens, player_name_for_context

logger = logging.getLogger("mrp.engine.openrouter")

class _StreamOptionsUnsupported(RuntimeError):
    """网关不认 `stream_options`（含 include_usage）→ 由 generate 降级重试一次。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _usage_from_body(body: dict[str, Any] | None) -> TokenUsage:
    """OpenAI 兼容 usage → TokenUsage（缺失一律置 0）。"""
    usage = body or {}
    details = usage.get("prompt_tokens_details") or {}
    return TokenUsage(
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
    )


class OpenRouterEngine:
    """原生直连引擎（每角色一实例；无状态 HTTP，不占进程）。"""

    def __init__(
        self,
        *,
        thinking: bool = True,
        provider: str = "",
        provider_allow_fallbacks: bool = True,
        client_factory: Callable[[], httpx.Client] | None = None,
        request_archive: RequestArchive | None = None,
        generation: GenerationSettings | None = None,
    ) -> None:
        self._thinking = thinking
        self._provider = provider.strip()
        self._provider_allow_fallbacks = provider_allow_fallbacks
        self._request_archive = request_archive
        self._generation = generation or GenerationSettings()
        self._client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
            )
        )
        self._client: httpx.Client | None = None
        self._character: Character | None = None
        self._started_at: float | None = None
        self._last_turn_at: float | None = None
        self._turns = 0
        self._errors = 0
        # C14：网关是否支持流式 usage（stream_options.include_usage）；
        # 首次被拒后置 False，本实例后续流式请求不再带该参数
        self._stream_usage_supported = True

    # ---- CharacterEngine 协议 ----

    async def start(self, character: Character) -> None:
        self._character = character
        if self._client is None:
            self._client = self._client_factory()
        if self._started_at is None:
            self._started_at = time.time()

    async def stop(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await asyncio.to_thread(client.close)
        self._started_at = None

    async def is_alive(self) -> bool:
        return self._client is not None

    async def generate(
        self, ctx: TurnContext, *, on_delta: DeltaCallback | None = None
    ) -> EngineReply:
        if self._client is None or self._character is None:
            raise RuntimeError("OpenRouterEngine.generate before start()")
        character = self._character
        composed = ctx.planned_prompt or compose_prompt(ctx)
        body: dict[str, Any] = {
            "model": character.llm.model,
            "messages": [
                {"role": "system", "content": persona_from_card(character.card, player_name_for_context(ctx))},
                {"role": "user", "content": composed.text},
            ],
            "reasoning": {"effort": "low"} if self._thinking else {"enabled": False},
        }
        sampling = character.llm.sampling or {}
        for key, value in self._generation.request_parameters().items():
            body[key] = sampling.get(key, value)
        for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
            if key in sampling and sampling[key] is not None:
                body[key] = sampling[key]
        max_tokens = (sampling.get("max_tokens", self._generation.max_output_tokens)
                      if ctx.reply_max_tokens is None else ctx.reply_max_tokens)
        if max_tokens is not None and int(max_tokens) > 0:
            body["max_tokens"] = int(max_tokens)
        mandatory_reasoning = normalize_reasoning(body, character.llm.base_url)
        provider = ctx.model_provider if ctx.gateway else self._provider
        if provider:
            body["provider"] = {
                "order": [provider],
                "allow_fallbacks": self._provider_allow_fallbacks,
            }
        if ctx.context_limit is not None:
            estimated = sum(estimate_tokens(str(item.get("content") or "")) + 8 for item in body["messages"])
            if estimated + (ctx.output_reserve or 0) > ctx.context_limit:
                raise ValueError("最终请求超过当前模型上下文容量，请缩短固定设定或切换上游")
        url = f"{character.llm.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {os.environ.get(character.llm.api_key_env, '')}"}
        engine_session_id = f"{ctx.session_id}-{ctx.character_id}-{ctx.turn:04d}-or"
        usage = TokenUsage()
        usage_calls: list[TokenUsage] = []
        try:
            if on_delta is not None:
                body["stream"] = True
                if self._stream_usage_supported:
                    # C14：要求网关在最后一个 chunk 带 usage；否则流式回合 usage 恒 0（成本账失真）
                    body["stream_options"] = {"include_usage": True}
                try:
                    self._capture_request(ctx, body)
                    content, finish, usage = await asyncio.to_thread(
                        self._stream_generate, url, headers, body, on_delta
                    )
                except _StreamOptionsUnsupported as e:
                    # 网关不支持 → 去掉参数重试一次（本回合 usage 缺失，仅告警一次）
                    self._stream_usage_supported = False
                    logger.warning(
                        "网关不支持 stream_options.include_usage，已降级（流式回合 usage 将缺失）: %s",
                        e,
                    )
                    body.pop("stream_options", None)
                    self._capture_request(ctx, body)
                    content, finish, usage = await asyncio.to_thread(
                        self._stream_generate, url, headers, body, on_delta
                    )
            else:
                self._capture_request(ctx, body)
                content, finish, usage = await asyncio.to_thread(
                    self._blocking_generate, url, headers, body
                )
            usage_calls.append(usage)
            if not content.strip() and self._thinking and not mandatory_reasoning:
                # Some reasoning models exhaust their visible-output budget on
                # reasoning and return an empty assistant body. Retry once
                # without reasoning, retaining both requests' usage.
                retry_body = {key: value for key, value in body.items()
                              if key not in {"stream", "stream_options"}}
                retry_body["reasoning"] = {"enabled": False}
                self._capture_request(ctx, retry_body)
                retry_content, retry_finish, retry_usage = await asyncio.to_thread(
                    self._blocking_generate, url, headers, retry_body
                )
                usage_calls.append(retry_usage)
                if retry_content and on_delta is not None:
                    try:
                        on_delta(retry_content)
                    except Exception:
                        logger.warning("on_delta 回调异常（已忽略）", exc_info=True)
                content, finish = retry_content, retry_finish
                usage = TokenUsage(
                    input_tokens=usage.input_tokens + retry_usage.input_tokens,
                    output_tokens=usage.output_tokens + retry_usage.output_tokens,
                    cached_tokens=usage.cached_tokens + retry_usage.cached_tokens,
                )
            if not content.strip():
                if mandatory_reasoning and finish in {"length", "max_tokens", "max-tokens"}:
                    error = RuntimeError("该模型必须开启推理；本次输出上限已被推理耗尽，未得到正文。请提高输出上限后再补完回应。")
                    error.retryable = False
                    raise error
                raise RuntimeError(f"模型返回空回复（finish_reason={finish or '未知'}）")
        except Exception as exc:
            # A successful empty reasoning response is still a paid call. If
            # the visible-output retry fails, expose all known usage to the
            # manager/orchestrator instead of losing it with the exception.
            prior = getattr(exc, "usage", None)
            if prior is not None:
                prior = prior if isinstance(prior, TokenUsage) else TokenUsage(**prior)
                usage_calls.append(prior)
            if usage_calls:
                exc.usage_calls = usage_calls
                exc.usage = TokenUsage(**{
                    key: sum(getattr(value, key) for value in usage_calls)
                    for key in ("input_tokens", "output_tokens", "cached_tokens")
                })
            self._errors += 1
            raise
        self._turns += 1
        self._last_turn_at = time.time()
        return EngineReply(
            content=content,
            finish_reason=finish,
            usage=usage,
            usage_calls=usage_calls,
            engine_session_id=engine_session_id,
        )

    def _capture_request(self, ctx: TurnContext, body: dict[str, Any]) -> None:
        if self._request_archive is None:
            return
        try:
            request_id = self._request_archive.write(ctx.session_id, ctx.character_id, ctx.turn, "openrouter", body,
                plan_id=ctx.plan_id, player_identity_source=ctx.player_identity_source,
                message_id=ctx.message_id, generation_id=ctx.generation_id,
                operation_id=ctx.operation_id, attempt_id=ctx.attempt_id)
            if request_id:
                ctx.request_ids.append(request_id)
        except Exception:
            logger.warning("无法保存模型请求快照", exc_info=True)

    def health(self) -> EngineHealth:
        return EngineHealth(
            started_at=datetime.fromtimestamp(self._started_at, tz=timezone.utc)
            if self._started_at
            else None,
            last_turn_at=datetime.fromtimestamp(self._last_turn_at, tz=timezone.utc)
            if self._last_turn_at
            else None,
            turns=self._turns,
            errors=self._errors,
            startup_seconds=0.0,  # 无进程，无冷启动
        )

    # ---- HTTP（同步，经 to_thread 调用） ----

    def _blocking_generate(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[str, str | None, TokenUsage]:
        assert self._client is not None
        resp = self._client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            choice = (data.get("choices") or [])[0]
            content = (choice.get("message") or {}).get("content") or ""
            finish = choice.get("finish_reason")
        except (IndexError, AttributeError) as e:  # 响应畸形
            error = RuntimeError(f"OpenRouter 响应畸形: {str(data)[:300]}")
            error.usage = _usage_from_body(data.get("usage"))
            raise error from e
        return content, finish, _usage_from_body(data.get("usage"))

    def _stream_generate(
        self, url: str, headers: dict[str, str], body: dict[str, Any], on_delta: DeltaCallback
    ) -> tuple[str, str | None, TokenUsage]:
        """SSE 逐片解析：delta.content → on_delta（工作线程）；usage 取最终 chunk。"""
        assert self._client is not None
        parts: list[str] = []
        finish: str | None = None
        usage_body: dict[str, Any] = {}
        callback_failed = False
        with self._client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                resp.read()
                text = resp.text[:300]
                if body.get("stream_options") and "stream_options" in text:
                    # 网关把未知参数当错误 → 交由 generate 降级重试
                    raise _StreamOptionsUnsupported(f"OpenRouter {resp.status_code}: {text}")
                raise RuntimeError(f"OpenRouter {resp.status_code}: {text}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue  # 心跳/非 JSON 行
                if chunk.get("usage"):
                    usage_body = chunk["usage"]  # include_usage 的最终 chunk（choices 为空）
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    parts.append(piece)
                    try:
                        on_delta(piece)
                    except Exception:  # noqa: BLE001 —— 回调异常不应中断生成
                        if not callback_failed:
                            callback_failed = True
                            logger.warning("on_delta 回调异常（已忽略，后续增量继续投递）", exc_info=True)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        return "".join(parts), finish, _usage_from_body(usage_body)
