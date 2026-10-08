export type LanSessionFailureCode = 'lan_session_expired' | 'lan_pairing_required';

export class LanSessionExpiredError extends Error {
  readonly code: LanSessionFailureCode;

  constructor(code: LanSessionFailureCode) {
    super(code === 'lan_session_expired' ? '局域网会话已过期；输入已保留，请重新配对后手动重试。' : '需要先配对这台设备。');
    this.name = 'LanSessionExpiredError';
    this.code = code;
  }
}

export function isLanSessionExpiredError(cause: unknown): cause is LanSessionExpiredError {
  return cause instanceof LanSessionExpiredError;
}

export function installLanSessionExpiryMonitor(): void {
  if (window.__mrpLanSessionExpiryMonitorInstalled) return;
  window.__mrpLanSessionExpiryMonitorInstalled = true;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const response = nativeFetch(input, init);
    void response.then((value) => {
      if (value.status !== 401) return;
      const url = input instanceof Request ? input.url : String(input);
      if (new URL(url, window.location.href).pathname === '/api/v1/lan/pair') return;
      void value.clone().json().then((payload: unknown) => {
        if (!payload || typeof payload !== 'object') return;
        const code = (payload as { code?: unknown }).code;
        if (code === 'lan_session_expired' || code === 'lan_pairing_required') {
          window.dispatchEvent(new CustomEvent('mrp:lan-session-expired', { detail: { code } }));
        }
      }).catch(() => undefined);
    }).catch(() => undefined);
    return response;
  };
}

let lastSseProbe = 0;
export function probeLanSessionAfterSseClose(): void {
  const now = Date.now();
  if (now - lastSseProbe < 10_000) return;
  lastSseProbe = now;
  void fetch('/api/v1/lan/status', { cache: 'no-store', credentials: 'same-origin' }).catch(() => undefined);
}

declare global {
  interface Window {
    __mrpLanSessionExpiryMonitorInstalled?: boolean;
  }
}
