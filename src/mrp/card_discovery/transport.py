from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx


class SourceRequestError(RuntimeError):
    def __init__(self, message: str, *, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


async def fetch_bytes(
    url: str,
    *,
    allowed_host: str,
    max_bytes: int,
    timeout_seconds: float = 12.0,
    accept: str = "application/json",
) -> tuple[bytes, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != allowed_host or parsed.username or parsed.password:
        raise SourceRequestError("连接器生成了非许可目标地址")
    timeout = httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"Accept": accept, "User-Agent": "SekaiTavernCardResearch/1.0"}) as response:
                if response.is_redirect:
                    raise SourceRequestError("站点返回重定向；为防止越权访问已拒绝跟随")
                if response.status_code == 429:
                    raise SourceRequestError("站点请求频率受限", retry_after=response.headers.get("Retry-After"))
                if response.status_code >= 400:
                    raise SourceRequestError(f"站点返回 HTTP {response.status_code}")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise SourceRequestError("站点响应超过大小上限")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceRequestError("站点响应超过大小上限")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("Content-Type", "")
    except SourceRequestError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise SourceRequestError(f"连接站点失败：{type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        raise SourceRequestError(f"站点响应无效：{type(exc).__name__}") from exc


async def fetch_json(url: str, *, allowed_host: str, max_bytes: int = 4 * 1024 * 1024) -> object:
    raw, content_type = await fetch_bytes(url, allowed_host=allowed_host, max_bytes=max_bytes)
    if "json" not in content_type.lower():
        raise SourceRequestError("站点没有返回 JSON")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRequestError("站点返回了无法解析的 JSON") from exc
