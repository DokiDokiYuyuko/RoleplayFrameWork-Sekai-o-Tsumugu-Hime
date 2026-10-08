import { ApiError } from './request';

export async function exportSelectedBundle(input: unknown): Promise<Blob> {
  const response = await fetch('/api/v1/bundle/export-selected', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(response.status, undefined, payload?.detail ?? '导出失败，请重试。');
  }
  return response.blob();
}
