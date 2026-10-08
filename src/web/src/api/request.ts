import { LanSessionExpiredError } from './lanSession';
import { beginLoad } from '../utils/loadDiagnostics';

export class ApiError extends Error {
  constructor(public status: number, public code: string | undefined, message: string, public details?: unknown) {
    super(`${status}: ${message.slice(0, 200)}`); this.name = 'ApiError';
  }
}
export interface ReadOptions { signal?: AbortSignal }
export class RequestTimeoutError extends Error {
  constructor(seconds: number) { super(`读取超过 ${seconds} 秒，请重试。`); this.name = 'RequestTimeoutError'; }
}
export function readTimeout(url: string, method = 'GET'): number | null {
  if (!['GET', 'HEAD'].includes(method.toUpperCase())) return null;
  return /^\/api\/v1\/settings\/(models|providers)(?:\?|$)/.test(url) ? 25_000 : 15_000;
}
export function isCancelled(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

/** The timer covers both headers and JSON body. Generation/writes keep their existing lifetime. */
export async function jsonRequest<T>(url: string, init?: RequestInit, onResponse?: (response: Response) => void): Promise<T> {
  const duration = readTimeout(url, init?.method);
  const controller = new AbortController();
  const finish = beginLoad('request', url);
  let expired = false;
  const relay = () => controller.abort(init?.signal?.reason);
  if (init?.signal?.aborted) relay();
  else init?.signal?.addEventListener('abort', relay, { once: true });
  const timer = duration === null ? null : setTimeout(() => { expired = true; controller.abort(); }, duration);
  try {
    const res = await fetch(url, {
      ...init, headers: { ...(typeof init?.body === 'string' ? { 'content-type': 'application/json' } : {}),
        ...Object.fromEntries(new Headers(init?.headers).entries()) }, signal: controller.signal,
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null) as { code?: string; detail?: string | { message?: string; code?: string } } | null;
      const detail = payload?.detail;
      const code = payload?.code ?? (typeof detail === 'object' ? detail?.code : undefined);
      if (code === 'lan_session_expired' || code === 'lan_pairing_required') throw new LanSessionExpiredError(code);
      const message = typeof detail === 'string' ? detail : detail?.message ?? res.statusText;
      throw new ApiError(res.status, code, message, detail);
    }
    const value = await res.json() as T;
    onResponse?.(res);
    if (controller.signal.aborted) throw expired ? new RequestTimeoutError(duration! / 1000) : new DOMException('已取消读取', 'AbortError');
    finish();
    return value;
  } catch (cause) {
    finish(expired ? 'timeout' : isCancelled(cause) ? 'cancelled' : 'error');
    if (expired) throw new RequestTimeoutError(duration! / 1000);
    throw cause;
  } finally {
    if (timer !== null) clearTimeout(timer);
    init?.signal?.removeEventListener('abort', relay);
  }
}
