"""DshEngine：deepseek-harness Python SDK 适配（ADR-0001 的 dsh 实现）。

关键设计（计划 §4）：
- 每轮重组 + 每回合新 session_id（f"{sid}-{cid}-{turn}"）：dsh 无删改历史 API，
  swipe/离席/事后注入都要求编排核心掌控历史；dsh session JSONL 因此成为
  逐回合审计日志（非功能需求 2）
- 重跑同一回合（swipe/续写/重新生成）时 dsh 会话已存在 → 自动加唯一后缀换一个
  审计会话重试（session id 复用只影响审计归属，不影响正确性）
- SDK 是同步的 → asyncio.to_thread 驱动；进程跨回合复用（首启 ~17s 只付一次）
- profile 内容 hash 缓存：改卡/改模型配置才需要重启进程（R1.2 改卡下次回合生效）
- 回合事件（result.events）默认不物化（C13）：只提取 usage/finish_reason，
  需要原始事件时置 MRP_ENGINE_DEBUG_EVENTS=1
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
from deepseek_harness.errors import JsonRpcError

from mrp.engines.dsh.profile import (
    PROFILE_NAME,
    character_home,
    write_character_profile,
)
from mrp.engines.dsh.provider_proxy import ProviderRoutingProxy
from mrp.reasoning import is_openrouter
from mrp.settings import GenerationSettings
from mrp.engines.request_archive import RequestArchive
from mrp.shared.models import (
    Character,
    EngineHealth,
    EngineReply,
    TokenUsage,
    TurnContext,
)
from mrp.shared.prompt import compose_prompt, persona_from_card, player_name_for_context

# B12：上游 dsh runtime 的"审计会话已存在"错误文案（在 JsonRpcError.message 内）。
# SDK 侧异常类型是 structured（JsonRpcError.code），但该 code 未文档化、也未在真机
# 验证过具体取值，因此仍保留文案判定；**上游改文案需同步此常量**。
_SESSION_EXISTS_MARKER = "already exists"

# C13：置 1/true/yes/on 时保留原始回合事件（调试/审计用；默认关闭避免每回合深拷贝）
_DEBUG_EVENTS_ENV = "MRP_ENGINE_DEBUG_EVENTS"


def _debug_events_enabled() -> bool:
    return os.environ.get(_DEBUG_EVENTS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _is_session_exists_error(exc: BaseException) -> bool:
    """判定"审计会话已存在"错误（供重跑同回合加后缀重试）。

    JsonRpcError 优先看 message（structured 路径）；其余异常回落 str(exc)
    （测试替身/包装异常）。
    """
    message = exc.message if isinstance(exc, JsonRpcError) else str(exc)
    return _SESSION_EXISTS_MARKER in (message or "")


def _reasoning_effort(character: Character, override: str | None = None) -> str | None:
    """思考强度：设置页 override > 角色级 sampling > 全局 MRP_REASONING_EFFORT > 适配器默认(high)。

    实测（2026-09-24，真实引擎 A/B）：默认(high) 12.1s/次 → "low" 4.6s/次（回复质量正常）。
    """
    sampling = character.llm.sampling or {}
    return override or sampling.get("reasoning_effort") or os.environ.get("MRP_REASONING_EFFORT") or None


def _config_signature(character: Character, effort_override: str | None = None) -> str:
    """卡内容 + 模型配置的联合指纹——变更才需要重启进程。"""
    payload = (
        character.card.model_dump_json()
        + "|"
        + character.llm.model_dump_json()
        + "|"
        + (_reasoning_effort(character, effort_override) or "-")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _usage_from_events(events: list[dict[str, Any]]) -> TokenUsage:
    """从 assistant/message 事件提取 usage（M0 spike 验证的结构）。"""
    for e in reversed(events):
        if e.get("type") != "assistant/message":
            continue
        data = e.get("data") or {}
        msg = data.get("message") if isinstance(data.get("message"), dict) else data
        usage = (msg or {}).get("usage") or data.get("usage") or {}
        if isinstance(usage, dict):
            return TokenUsage(
                input_tokens=int(usage.get("input_tokens") or usage.get("inputTokens") or 0),
                output_tokens=int(usage.get("output_tokens") or usage.get("outputTokens") or 0),
                cached_tokens=int(usage.get("cached_tokens") or usage.get("cachedTokens") or 0),
            )
    return TokenUsage()


class DshEngine:
    """一个角色一个引擎实例（进程级单选模型路由——ADR-0001）。"""

    def __init__(self, engines_root: Path | None = None, reasoning_effort: str | None = None,
                 provider: str = "", provider_allow_fallbacks: bool = True,
                 request_archive: RequestArchive | None = None,
                 generation: GenerationSettings | None = None) -> None:
        self._root = engines_root
        self._effort_override = reasoning_effort  # R48：设置页强制档（None=按角色/env 回落）
        self._provider = provider
        self._provider_allow_fallbacks = provider_allow_fallbacks
        self._request_archive = request_archive
        self._generation = generation or GenerationSettings()
        self._proxy: ProviderRoutingProxy | None = None
        self._capture_context: tuple[str, str, int] | None = None
        self._capture_plan_id: str | None = None
        self._capture_player_identity: dict = {}
        self._capture_turn_ctx = None
        self._harness: DeepSeekHarness | None = None
        self._character: Character | None = None
        self._signature: str = ""
        self._started_at: float | None = None
        self._last_turn_at: float | None = None
        self._turns = 0
        self._errors = 0
        self._startup_seconds: float | None = None
        self._reply_max_tokens: int | None = None
        self._player_name = "玩家"

    # ---- 内部 ----

    def _build_config(self, character: Character) -> DeepSeekHarnessConfig:
        home = character_home(character.id, self._root)
        write_character_profile(home, PROFILE_NAME)
        api_key = os.environ.get(character.llm.api_key_env, "")
        sampling = character.llm.sampling or {}
        return DeepSeekHarnessConfig(
            provider=character.llm.provider,
            model=character.llm.model,
            profile=PROFILE_NAME,
            dsh_home=str(home),
            base_url=self._proxy.base_url if self._proxy is not None else character.llm.base_url,
            api_key=api_key,
            env={"DSH_SYSTEM_PROMPT": persona_from_card(character.card, self._player_name)},
            max_tokens=(sampling.get("max_tokens", self._generation.max_output_tokens)
                        if self._reply_max_tokens is None else self._reply_max_tokens) or None,
            reasoning_effort=_reasoning_effort(character, self._effort_override),
            # 首启含每-home node_modules 初始化（本地盘实测 17s+，服务器负载下更久），30s 默认会超时
            initialize_timeout_seconds=float(sampling.get("initialize_timeout_seconds", 180.0)),
            request_timeout_seconds=sampling.get("request_timeout_seconds", 300.0),
        )

    # ---- CharacterEngine 协议 ----

    async def start(self, character: Character) -> None:
        signature = _config_signature(character, self._effort_override) + f"|{self._reply_max_tokens}|{self._player_name}"
        if self._harness is not None and signature == self._signature:
            self._character = character  # 轻量更新（别名/静音等运行时字段）
            return
        await self.stop()  # 配置变更 → 重启进程
        provider = (character.llm.effective_provider
                    if character.llm.inherit_model is not None or character.llm.inherit_base_url is not None
                    else self._provider)
        if character.llm.base_url and (provider or self._request_archive is not None or is_openrouter(character.llm.base_url)):
            sampling = self._generation.request_parameters()
            sampling.update({
                key: character.llm.sampling[key]
                for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty")
                if key in character.llm.sampling and character.llm.sampling[key] is not None
            })
            self._proxy = ProviderRoutingProxy(
                character.llm.base_url, provider, self._provider_allow_fallbacks,
                on_request=self._capture_request,
                sampling=sampling,
            )
        # profile 生成/目录创建是同步文件 IO → 丢线程池，别卡事件循环
        harness = DeepSeekHarness(await asyncio.to_thread(self._build_config, character))
        t0 = time.perf_counter()
        await asyncio.to_thread(harness.start)  # 首启含 node_modules 初始化，本地盘实测 ~17s
        self._startup_seconds = time.perf_counter() - t0
        self._harness = harness
        self._character = character
        self._signature = signature
        self._started_at = time.time()

    async def stop(self) -> None:
        if self._harness is not None:
            harness, self._harness = self._harness, None
            await asyncio.to_thread(harness.close)
        if self._proxy is not None:
            proxy, self._proxy = self._proxy, None
            await asyncio.to_thread(proxy.close)
        self._capture_context = None
        self._capture_plan_id = None
        self._capture_player_identity = {}
        self._capture_turn_ctx = None
        self._signature = ""

    def _capture_request(self, body: dict[str, Any]) -> None:
        if self._request_archive is not None and self._capture_context is not None:
            ctx = self._capture_turn_ctx
            request_id = self._request_archive.write(*self._capture_context, "dsh", body,
                plan_id=self._capture_plan_id, player_identity_source=self._capture_player_identity,
                message_id=ctx.message_id if ctx else None, generation_id=ctx.generation_id if ctx else None,
                operation_id=ctx.operation_id if ctx else None, attempt_id=ctx.attempt_id if ctx else None)
            if ctx is not None and request_id:
                ctx.request_ids.append(request_id)

    async def is_alive(self) -> bool:
        return self._harness is not None

    async def generate(self, ctx: TurnContext, *, on_delta=None) -> EngineReply:
        """on_delta（R33）：当前忽略——dsh SDK 的 stdio 通知层无增量事件
        （spike FAIL，见 document/spike/v3/m8-dsh-streaming.md）；编排层走伪流式。
        未来 SDK 支持时在此桥接 on_notification → on_delta（回调在 reader
        线程触发，需 call_soon_threadsafe 投递，design/v3/m8-r33 §2.1）。
        """
        if self._harness is None or self._character is None:
            raise RuntimeError("DshEngine.generate before start()")
        player_name = player_name_for_context(ctx)
        if ctx.reply_max_tokens != self._reply_max_tokens or player_name != self._player_name:
            character = self._character
            self._reply_max_tokens = ctx.reply_max_tokens
            self._player_name = player_name
            await self.start(character)  # SDK 只在 initialize 时接受 maxTokens
        composed = ctx.planned_prompt or compose_prompt(ctx)
        if self._proxy is not None:
            self._proxy.capacity_limit = ctx.context_limit
            self._proxy.output_reserve = ctx.output_reserve or 0
        self._capture_context = (ctx.session_id, ctx.character_id, ctx.turn)
        self._capture_plan_id = ctx.plan_id
        self._capture_player_identity = dict(ctx.player_identity_source)
        self._capture_turn_ctx = ctx
        engine_session_id = f"{ctx.session_id}-{ctx.character_id}-{ctx.turn:04d}"
        try:
            result = await asyncio.to_thread(
                self._harness.run, composed.text, session_id=engine_session_id
            )
        except Exception as e:
            # 重跑同一回合（swipe/续写/重新生成）会复用 turn → dsh 会话已存在。
            # 换一个带唯一后缀的审计会话重试；首跑命名不变（保住 0014=第14轮 的可读性）。
            if not _is_session_exists_error(e):
                self._errors += 1
                raise
            engine_session_id = f"{engine_session_id}-{uuid.uuid4().hex[:6]}"
            try:
                result = await asyncio.to_thread(
                    self._harness.run, composed.text, session_id=engine_session_id
                )
            except Exception:
                self._errors += 1
                raise
        self._turns += 1
        self._last_turn_at = time.time()
        # C13：默认不物化事件列表（只读引用，usage 提取按需扫）；调试开关才深拷贝
        events = result.events or []
        return EngineReply(
            content=result.final_response or "",
            finish_reason=result.finish_reason,
            usage=_usage_from_events(events),
            engine_session_id=engine_session_id,
            raw_events=[dict(e) for e in events] if _debug_events_enabled() else [],
        )

    @property
    def pid(self) -> int | None:
        """dsh 子进程 pid（尽力而为：SDK 未公开访问器，走 client._proc 容错读取）。"""
        client = getattr(self._harness, "client", None)
        return getattr(getattr(client, "_proc", None), "pid", None)

    def health(self) -> EngineHealth:
        from mrp.shared.models import utcnow

        alive = self._harness is not None

        def _ts(epoch: float | None):
            from datetime import datetime, timezone

            return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch else None

        return EngineHealth(
            started_at=_ts(self._started_at) if alive else None,
            last_turn_at=_ts(self._last_turn_at),
            turns=self._turns,
            errors=self._errors,
            startup_seconds=self._startup_seconds,
        )
