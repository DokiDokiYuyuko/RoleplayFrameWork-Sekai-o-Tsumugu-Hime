import { lanRequest, downloadLanCertificate } from '../../api/lan';
export type LanInterface = { ip: string; url: string; name: string; interface_index: number; subnet: string };
export type LanDeviceSession = {
  session_id: string;
  device_name: string;
  ip: string;
  created_at: number;
  last_activity_at: number;
  expires_in_seconds: number;
  idle_expires_in_seconds: number;
  remembered: boolean;
};
export type LanStatus = {
  enabled: boolean;
  port: number;
  interfaces: LanInterface[];
  bind_address: string;
  firewall_rule_verified: boolean | null;
  firewall_state: "configured_rule_verified" | "unverified";
  transport: "http" | "https";
  https_available: boolean;
  root_ca_fingerprint_sha256: string | null;
  certificate_expires_at: string | null;
  public_reachability: "unverified";
  pairing_required: boolean;
  session_policies: {
    normal: { idle_days: number; absolute_days: number };
    remembered: { idle_days: number; absolute_days: number };
  };
  session_store_persistent: boolean;
  session_store_available: boolean;
  session_state_cleanup_confirmed: boolean;
  can_manage_devices: boolean;
};


export const lanApi = {
  status: () => lanRequest('/api/v1/lan/status'),
  devices: () => lanRequest('/api/v1/lan/sessions'),
  revoke: (id: string) => lanRequest(`/api/v1/lan/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  revokeAll: () => lanRequest('/api/v1/lan/sessions/revoke-all', { method: 'POST' }),
  rotate: () => lanRequest('/api/v1/lan/rotate-code', { method: 'POST' }),
  close: () => lanRequest('/api/v1/lan/close', { method: 'POST' }),
  certificate: downloadLanCertificate,
  pair: (accessCode: string, remember: boolean) => lanRequest('/api/v1/lan/pair', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ access_code: accessCode, remember }) }),
};
export const lanQueries = { overview: () => ({ queryKey: ['lan-overview'] as const, queryFn: async () => {
  const response = await lanApi.status(); if (!response.ok) throw new Error(`读取失败（${response.status}）`);
  const status = await response.json() as LanStatus;
  let sessions: LanDeviceSession[] = [];
  if (status.enabled && status.can_manage_devices) {
    const devices = await lanApi.devices(); if (!devices.ok) throw new Error(`读取配对设备失败（${devices.status}）`);
    sessions = (await devices.json()).sessions;
  }
  return { status, sessions };
} }) };
