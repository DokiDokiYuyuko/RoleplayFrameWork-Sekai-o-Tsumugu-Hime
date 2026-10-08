/** Shared gateway presets used by settings and independent chat. */
export const GATEWAY_PRESETS = [
  { id: 'openrouter', label: 'OpenRouter', gateway: 'https://openrouter.ai/api/v1', model: 'deepseek/deepseek-v4-flash' },
  { id: 'getgoapi', label: 'GetGoAPI', gateway: 'https://api.getgoapi.com/v1', model: 'gemini-3.5-flash' },
] as const;

export function profileIdForGateway(gateway: string): string {
  try {
    const host = new URL(gateway.trim()).hostname.toLowerCase();
    if (host === 'openrouter.ai' || host.endsWith('.openrouter.ai')) return 'openrouter';
    if (host === 'getgoapi.com' || host.endsWith('.getgoapi.com')) return 'getgoapi';
    return host ? `custom:${host}` : '';
  } catch {
    return '';
  }
}
