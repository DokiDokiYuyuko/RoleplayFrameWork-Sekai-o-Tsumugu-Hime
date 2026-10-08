"""LLM 网关：OpenAI 兼容 chat/completions 直调（httpx，同步）。

供编排侧的轻量 LLM 需求使用（选项生成/工坊生成/记忆摘要/试聊预览）——
这些不需要 dsh 引擎的完整管线，直接调网关。密钥只从环境变量读。
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Callable

import httpx
from mrp.reasoning import is_openrouter, normalize_reasoning
from pydantic import BaseModel, Field


class LlmConfig(BaseModel):
    model: str
    base_url: str
    api_key_env: str
    api_key: str = Field(default="", repr=False)
    max_tokens: int = 2048
    provider: str = ""
    provider_allow_fallbacks: bool = True
    include_usage_cost: bool = False
    sampling: dict[str, float] = Field(default_factory=dict)


class StreamControl:
    """Thread-safe cancellation for a synchronous streaming HTTP request."""

    def __init__(self) -> None:
        self._stop_requested = threading.Event()
        self._lock = threading.Lock()
        self._response: httpx.Response | None = None

    @property
    def stopped(self) -> bool:
        return self._stop_requested.is_set()

    def attach_response(self, response: httpx.Response) -> None:
        with self._lock:
            self._response = response
            should_close = self._stop_requested.is_set()
        if should_close:
            response.close()

    def detach_response(self, response: httpx.Response) -> None:
        with self._lock:
            if self._response is response:
                self._response = None

    def request_stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def default_config(
    model: str | None = None,
    *,
    provider: str | None = None,
    provider_allow_fallbacks: bool | None = None,
) -> LlmConfig:
    """从环境读默认网关配置（.env 已由 run.sh 加载）。"""
    return LlmConfig(
        model=model or os.environ.get("MRP_MODEL", "deepseek/deepseek-v4-flash"),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key_env="OPENROUTER_API_KEY",
        provider=provider if provider is not None else os.environ.get("MRP_MODEL_PROVIDER", ""),
        provider_allow_fallbacks=(
            provider_allow_fallbacks
            if provider_allow_fallbacks is not None
            else os.environ.get("MRP_PROVIDER_ALLOW_FALLBACKS", "true").lower() != "false"
        ),
    )


def options_config() -> LlmConfig:
    """辅助任务配置：优先使用设置页配置的辅助模型。"""
    return default_config(
        os.environ.get("MRP_AUX_MODEL") or os.environ.get("MRP_OPTIONS_MODEL"),
        provider=os.environ.get("MRP_AUXILIARY_PROVIDER", ""),
    )


def judge_config() -> LlmConfig:
    """校验、记忆和垫场共用辅助模型。"""
    return default_config(
        os.environ.get("MRP_AUX_MODEL")
        or os.environ.get("MRP_JUDGE_MODEL")
        or os.environ.get("MRP_OPTIONS_MODEL"),
        provider=os.environ.get("MRP_AUXILIARY_PROVIDER", ""),
    )


def director_config() -> LlmConfig:
    """导演调用使用辅助模型。"""
    return default_config(
        os.environ.get("MRP_AUX_MODEL")
        or os.environ.get("MRP_DIRECTOR_MODEL")
        or os.environ.get("MRP_JUDGE_MODEL")
        or os.environ.get("MRP_OPTIONS_MODEL"),
        provider=os.environ.get("MRP_AUXILIARY_PROVIDER", ""),
    )


def chat_text(
    messages: list[dict[str, str]],
    config: LlmConfig,
    *,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    no_thinking: bool = False,
) -> str:
    """一次 chat completion，返回文本。失败抛 RuntimeError（含状态码与响应体摘要）。

    no_thinking（R48 实测，2026-09-24）：轻量通道（导演/校验/选项/垫场）关闭思考，
    实测导演单次 16s → 1-2s（OpenRouter `reasoning.enabled=false`，4/4 稳定生效）。
    """
    return chat_text_with_usage(
        messages, config, max_tokens=max_tokens, timeout=timeout, no_thinking=no_thinking
    )[0]


def chat_text_with_usage(
    messages: list[dict[str, str]],
    config: LlmConfig,
    *,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    no_thinking: bool = False,
    on_request: Callable[[dict[str, Any]], None] | None = None,
    require_complete: bool = False,
) -> tuple[str, dict[str, int]]:
    """chat completion，返回 (文本, usage)——R34 judge 成本分列用。"""
    api_key = config.api_key or os.environ.get(config.api_key_env, "")
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
    }
    output_limit = max_tokens if max_tokens is not None else config.max_tokens
    if output_limit > 0:
        body["max_tokens"] = output_limit
    body.update(config.sampling)
    if config.provider:
        body["provider"] = {
            "order": [config.provider],
            "allow_fallbacks": config.provider_allow_fallbacks,
        }
    # `reasoning.enabled` is an OpenRouter extension. Other OpenAI-compatible
    # gateways may reject it (GetGoAPI Gemini models require thinking enabled).
    if no_thinking and is_openrouter(config.base_url):
        body["reasoning"] = {"enabled": False}
    normalize_reasoning(body, config.base_url)
    if on_request is not None:
        try:
            on_request(body)
        except Exception:
            pass
    try:
        resp = httpx.post(
            f"{config.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM 网关请求失败: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"LLM 网关 {resp.status_code}: {resp.text[:300]}")
    try:
        result = resp.json()
        choice = result["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"LLM 网关响应畸形: {resp.text[:300]}") from exc
    if require_complete and choice.get("finish_reason") in {"length", "max_tokens", "max-tokens"}:
        error = RuntimeError("模型输出达到上游 token 上限；不完整结果已丢弃，请缩小整理范围或调整上游输出预算")
        raw = result.get("usage") or {}
        error.usage = {"input_tokens": int(raw.get("prompt_tokens") or 0),
                       "output_tokens": int(raw.get("completion_tokens") or 0), "cached_tokens": 0}
        raise error
    raw_usage = result.get("usage") or {}
    usage = {
        "input_tokens": int(raw_usage.get("prompt_tokens") or 0),
        "output_tokens": int(raw_usage.get("completion_tokens") or 0),
        "cached_tokens": int((raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        "finish_reason": choice.get("finish_reason"),
    }
    return text, usage


def chat_stream_with_usage(
    messages: list[dict[str, str]],
    config: LlmConfig,
    on_delta: Callable[[str], None],
    *,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    no_thinking: bool = False,
    on_request: Callable[[dict[str, Any]], None] | None = None,
    stream_control: StreamControl | None = None,
    require_complete: bool = False,
) -> tuple[str, dict[str, Any]]:
    """流式 chat completion；逐片回调，最终返回完整文本与网关用量。"""
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if config.include_usage_cost:
        body["usage"] = {"include": True}
    output_limit = max_tokens if max_tokens is not None else config.max_tokens
    if output_limit > 0:
        body["max_tokens"] = output_limit
    body.update(config.sampling)
    if config.provider:
        body["provider"] = {
            "order": [config.provider],
            "allow_fallbacks": config.provider_allow_fallbacks,
        }
    if no_thinking and is_openrouter(config.base_url):
        body["reasoning"] = {"enabled": False}
    normalize_reasoning(body, config.base_url)
    headers = {"Authorization": f"Bearer {config.api_key or os.environ.get(config.api_key_env, '')}"}
    url = f"{config.base_url.rstrip('/')}/chat/completions"

    def read_stream(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        usage_body: dict[str, Any] = {}
        finish_reason = ""
        saw_done = False
        cancelled = False
        if stream_control is not None and stream_control.stopped:
            return "", {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "finish_reason": "cancelled"}
        if on_request is not None:
            try:
                on_request(payload)
            except Exception:
                pass
        request_timeout = (
            httpx.Timeout(timeout, connect=min(timeout, 10.0))
            if stream_control is not None else timeout
        )
        with httpx.stream("POST", url, headers=headers, json=payload, timeout=request_timeout) as resp:
            if stream_control is not None:
                stream_control.attach_response(resp)
            if resp.status_code != 200:
                try:
                    resp.read()
                except Exception:
                    if stream_control is None or not stream_control.stopped:
                        raise
                finally:
                    if stream_control is not None:
                        stream_control.detach_response(resp)
                if stream_control is not None and stream_control.stopped:
                    return "", {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "finish_reason": "cancelled", "usage_incomplete": True}
                raise RuntimeError(f"LLM 网关 {resp.status_code}: {resp.text[:300]}")
            try:
                for line in resp.iter_lines():
                    if stream_control is not None and stream_control.stopped:
                        cancelled = True
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage_body = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    reason = choices[0].get("finish_reason")
                    if isinstance(reason, str) and reason:
                        finish_reason = reason
                    piece = (choices[0].get("delta") or {}).get("content")
                    if isinstance(piece, str) and piece:
                        parts.append(piece)
                        on_delta(piece)
            except Exception:
                if stream_control is None or not stream_control.stopped:
                    raise
                cancelled = True
            finally:
                if stream_control is not None:
                    stream_control.detach_response(resp)
        normalized_usage: dict[str, Any] = {
            "input_tokens": int(usage_body.get("prompt_tokens") or 0),
            "output_tokens": int(usage_body.get("completion_tokens") or 0),
            "cached_tokens": int((usage_body.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            "finish_reason": (
                "cancelled" if cancelled or (stream_control is not None and stream_control.stopped and not saw_done)
                else "connection_closed" if require_complete and not saw_done
                else finish_reason or ("unknown" if saw_done else "connection_closed")
            ),
        }
        if cancelled and not any(key in usage_body for key in ("prompt_tokens", "completion_tokens")):
            normalized_usage["usage_incomplete"] = True
        completion_details = usage_body.get("completion_tokens_details") or {}
        if isinstance(completion_details, dict) and completion_details.get("reasoning_tokens") is not None:
            normalized_usage["reasoning_tokens"] = int(completion_details["reasoning_tokens"])
        if config.include_usage_cost and isinstance(usage_body.get("cost"), (int, float)):
            normalized_usage["cost_usd"] = float(usage_body["cost"])
            normalized_usage["cost_source"] = "gateway"
        return "".join(parts), normalized_usage

    try:
        return read_stream(body)
    except RuntimeError as exc:
        if "stream_options" not in str(exc):
            raise
        body.pop("stream_options")
        return read_stream(body)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM 网关请求失败: {exc}") from exc


def chat_json(
    messages: list[dict[str, str]],
    config: LlmConfig,
    *,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> Any:
    """chat completion 并解析 JSON（```json 围栏或裸 JSON 均支持）。失败抛 ValueError。"""
    text = chat_text(messages, config, max_tokens=max_tokens, timeout=timeout)
    return extract_json(text)


def extract_json(text: str) -> Any:
    # 1) ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidates = [m.group(1).strip()] if m else []
    # 2) 文本中最早出现的 {...} 或 [...]（此前 { 分支先于 [ 分支，会把裸数组
    #    [{"a":1},...] 截断成首个字典——R27 实测踩坑，改为取最早括号类型）
    starts = [(text.find(op), op) for op in "{[" if text.find(op) >= 0]
    if starts:
        start, opener = min(starts)
        closer = "}" if opener == "{" else "]"
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    # 3) 全文直解
    candidates.append(text.strip())
    for cand in candidates:
        try:
            return json.loads(cand)
        except (ValueError, TypeError):
            continue
    raise ValueError(f"回复中未找到可解析 JSON: {text[:200]}")


# 注入式调用签名（测试替身用）：messages -> 文本
LlmCall = Callable[[list[dict[str, str]]], str]
