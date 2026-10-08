import { createClientId } from '../utils/clientId.js';
import { fileSha256 } from '../utils/sha256';
import { ApiError, jsonRequest } from './request';
import { attachCommandReceipt, type CommandReceipt } from '../utils/commandReceipt';

type Payload = Record<string, unknown>;
const preconditions = new Set(['expected_branch_revision', 'expected_revision', 'expected_fingerprint', 'operation_id', 'idempotency_key']);
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value).filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => a.localeCompare(b)).map(([key, v]) => `${JSON.stringify(key)}:${stable(v)}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}
/** Preconditions are frozen with the first attempt; changing user content begins a new intent. */
function intentOf(path: string, payload: Payload): string {
  const intent = Object.fromEntries(Object.entries(payload).filter(([key]) => !preconditions.has(key)));
  if (intent.__command_query && typeof intent.__command_query === 'object') {
    intent.__command_query = Object.fromEntries(Object.entries(intent.__command_query).filter(([key]) => !['expected_branch_revision', 'expected_revision', 'membership_revision', 'generation_id'].includes(key)));
  }
  if (path.endsWith('/regenerate-corrected')) delete intent.options_digest;
  if (path.endsWith('/input-group') && Array.isArray(intent.parts)) {
    intent.parts = intent.parts.map(part => part && typeof part === 'object' && !Array.isArray(part)
      ? Object.fromEntries(Object.entries(part).filter(([key]) => key !== 'expected_fingerprint')) : part);
  }
  return stable(intent);
}
interface Attempt { intent: string; reviewedPayload: string; requiresReview?: boolean; incomplete?: boolean; id: string; url: string; body?: BodyInit; promise?: Promise<unknown>; committed?: boolean; response?: unknown }
export interface IncompleteCommand { path: string; method: 'POST' | 'PATCH' | 'DELETE'; id: string }
export function createStoryCommands(request: typeof jsonRequest = jsonRequest, id: () => string = createClientId) {
  const pending = new Map<string, Attempt>();
  const reviewedIds = new Map<string, string>();
  const listeners = new Set<() => void>();
  let incomplete: readonly IncompleteCommand[] = [];
  function publish() {
    incomplete = [...pending.entries()].filter(([, attempt]) => attempt.incomplete).map(([scope, attempt]) => {
      const split = scope.indexOf(':'); return { method: scope.slice(0, split) as IncompleteCommand['method'], path: scope.slice(split + 1), id: attempt.id };
    });
    for (const listener of listeners) listener();
  }
  function prepare(path: string, method: 'POST' | 'PATCH' | 'DELETE', payload: Payload, upload?: File) {
    const scope = `${method}:${path}`, intent = intentOf(path, payload);
    const reviewedPayload = stable(Object.fromEntries(Object.entries(payload).filter(([key]) => key !== 'operation_id' && key !== 'idempotency_key')));
    let attempt = pending.get(scope);
    if (attempt?.incomplete && attempt.intent === intent && attempt.reviewedPayload !== reviewedPayload) {
      throw new ApiError(409, 'operation_incomplete', '前次操作尚未完成，请检查结果后再开始新的操作。');
    }
    if (!attempt || attempt.intent !== intent || (attempt.requiresReview && !attempt.incomplete && attempt.reviewedPayload !== reviewedPayload)) {
      const reviewedId = reviewedIds.get(scope);
      const operationId = reviewedId ?? (typeof payload.operation_id === 'string' ? payload.operation_id
        : typeof payload.idempotency_key === 'string' ? payload.idempotency_key : id());
      if (reviewedId) {
        payload = { ...payload, ...('operation_id' in payload ? { operation_id: operationId } : {}),
          ...('idempotency_key' in payload ? { idempotency_key: operationId } : {}) };
        reviewedIds.delete(scope);
      }
      const query = new URLSearchParams(path.includes('?') ? path.slice(path.indexOf('?') + 1) : '');
      if ((method === 'DELETE' || path.endsWith('/leave')) && payload.expected_branch_revision != null) query.set('expected_branch_revision', String(payload.expected_branch_revision));
      if (payload.__command_query && typeof payload.__command_query === 'object') for (const [key, value] of Object.entries(payload.__command_query)) if (value != null) query.set(key, String(value));
      const url = `${path.split('?')[0]}${query.size ? '?' + query : ''}`;
      const bodyPayload = Object.fromEntries(Object.entries(payload).filter(([key]) => key !== '__command_query'));
      const form = upload ? new FormData() : null;
      if (upload) {
        form!.append('file', upload);
        for (const [key, value] of Object.entries(payload)) if (key !== 'upload_sha256' && value != null) form!.append(key, String(value));
      }
      attempt = { intent, reviewedPayload, id: operationId, url, body: form ?? (method === 'DELETE' ? undefined : JSON.stringify(bodyPayload)) };
      pending.set(scope, attempt);
      publish();
    }
    return attempt;
  }
  async function command<T>(path: string, method: 'POST' | 'PATCH' | 'DELETE', payload: Payload, upload?: File, complete?: (value: T) => Promise<T>): Promise<T> {
    const scope = `${method}:${path}`;
    const attempt = prepare(path, method, payload, upload);
    if (attempt.promise) return attempt.promise as Promise<T>;
    const current = attempt;
    let receipt: CommandReceipt | undefined;
    const response = current.committed ? Promise.resolve(current.response as T) : request<T>(current.url, { method, headers: { 'X-Operation-ID': current.id }, body: current.body }, response => {
      const revision = Number(response.headers.get('X-Branch-Revision'));
      const commandId = response.headers.get('X-Command-ID');
      if (commandId || response.headers.has('X-Branch-Revision')) {
        if (commandId !== current.id || !response.headers.get('X-Branch-Revision')?.trim()
          || !Number.isInteger(revision) || revision < 0) throw new Error('命令回执不完整，请重新读取后重试。');
        receipt = { command_id: commandId, branch_revision: revision };
      }
    }).then(value => { current.committed = true; current.response = attachCommandReceipt(value, receipt); return current.response as T; });
    current.promise = response.then(value => complete ? complete(value) : value);
    try {
      const value = await current.promise;
      if (pending.get(scope) === current) pending.delete(scope);
      publish();
      return value as T;
    } catch (cause) {
      // Repeating the same failed command retains its ID. Reviewed new CAS fields begin a new intent.
      if (cause instanceof ApiError && cause.status < 500) {
        current.requiresReview = true;
        current.incomplete = cause.code === 'operation_incomplete';
        publish();
      }
      throw cause;
    } finally { current.promise = undefined; }
  }
  return Object.assign(command, {
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    incomplete: () => incomplete,
    reviewNewAttempt(path: string, method: IncompleteCommand['method'], expectedId: string) {
      const scope = `${method}:${path}`, previous = pending.get(scope);
      if (!previous?.incomplete || previous.promise || previous.id !== expectedId) return false;
      pending.delete(scope); reviewedIds.set(scope, id()); publish(); return true;
    },
    prepare(path: string, payload: Payload) {
    const attempt = prepare(path, 'POST', payload);
    return { id: attempt.id, payload: typeof attempt.body === 'string' ? JSON.parse(attempt.body) as Payload : payload };
  } });
}
export const storyCommand = createStoryCommands();
export const prepareStoryCommand = storyCommand.prepare;
/** Fingerprints stay in memory; file bytes and names are never logged or persisted. */
export async function uploadStoryCommand<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const fingerprint = await fileSha256(file);
  return storyCommand<T>(path, 'POST', { ...fields, upload_sha256: fingerprint }, file);
}

/** Query CAS is frozen with the same attempt as JSON. It never leaks into request bodies. */
export function queryStoryCommand<T>(path: string, method: 'POST' | 'PATCH' | 'DELETE', payload: Payload = {}, query: Payload = {}): Promise<T> {
  return storyCommand<T>(path, method, { ...payload, __command_query: query });
}
