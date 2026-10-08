/** LAN JSON commands and the certificate response share this transport boundary. */
export function lanRequest(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(path, { cache: 'no-store', credentials: 'same-origin', ...init });
}
export async function downloadLanCertificate(): Promise<Blob> {
  const response = await lanRequest('/api/v1/lan/root-ca.crt');
  if (!response.ok) throw new Error(`下载根证书失败（${response.status}）。请在电脑本机的 127.0.0.1 页面操作。`);
  return response.blob();
}
