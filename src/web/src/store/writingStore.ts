import { create } from 'zustand';
import { api } from '../api/client';
import type { WritingBatch } from '../api/writing';
import { useChatStore } from './chatStore';

export interface WritingTask {
  intent: string;
  draft: string;
  type: 'draft' | 'material';
  base: string;
  revision: number;
  resultRevision: number;
  batch: WritingBatch | null;
  context: string;
  composerAtStart: string;
  generating: boolean;
  error: string;
  requestId: number;
}
const prefix = 'mrp.writing.';
export const writingKey = (branch: string, identity?: string | null) => `${branch}:${identity ?? 'legacy'}`;
export function writingContext() {
  const s = useChatStore.getState();
  const session = s.sessions.find(x => x.id === s.currentSessionId);
  return JSON.stringify([s.currentSessionId, session?.branch_revision, session?.player_identity_id,
    s.messages.map(message => [message.id, message.fingerprint, message.status]), s.activeScene?.id, s.sessionGroups.map(group => group.id)]);
}
function save(key: string, task: WritingTask) {
  try { localStorage.setItem(prefix + key, JSON.stringify({ ...task, generating: false, requestId: 0 })); }
  catch { /* Storage may be unavailable or full; the open panel still works. */ }
}
function fresh(draft: string): WritingTask {
  return { intent: '', draft, type: 'draft', base: '', revision: 0, resultRevision: -1,
    batch: null, context: '', composerAtStart: draft, generating: false, error: '', requestId: 0 };
}
interface WritingState {
  tasks: Record<string, WritingTask>;
  openTask(key: string, draft: string): void;
  edit(key: string, patch: Partial<Pick<WritingTask, 'intent' | 'draft' | 'type' | 'base'>>): void;
  generate(key: string, branch: string, identity: string | null, composer: string): Promise<void>;
}
let sequence = 0;
export const useWritingStore = create<WritingState>()((set, get) => ({
  tasks: {},
  openTask(key, draft) {
    if (get().tasks[key]) return;
    let task = fresh(draft);
    try {
      const saved = JSON.parse(localStorage.getItem(prefix + key) ?? 'null');
      if (saved && typeof saved.intent === 'string' && typeof saved.draft === 'string'
        && ['draft', 'material'].includes(saved.type)) {
        task = { ...task, ...saved, generating: false, requestId: 0 };
        if (task.batch) task.batch = { ...task.batch, stale: true };
      }
    } catch { /* Broken local draft must not block the story. */ }
    set(s => ({ tasks: { ...s.tasks, [key]: task } }));
  },
  edit(key, patch) {
    const task = get().tasks[key];
    if (!task) return;
    const next = { ...task, ...patch, revision: task.revision + 1, error: '' };
    save(key, next);
    set(s => ({ tasks: { ...s.tasks, [key]: next } }));
  },
  async generate(key, branch, identity, composer) {
    const task = get().tasks[key];
    if (!task || task.generating || !task.intent.trim()) return;
    const session = useChatStore.getState().sessions.find(x => x.id === branch);
    const id = ++sequence;
    const context = writingContext();
    set(s => ({ tasks: { ...s.tasks, [key]: { ...task, generating: true, error: '', requestId: id } } }));
    try {
      const batch = await api.draftAssist(branch, {
        intent: task.intent, draft_text: task.draft, output_type: task.type,
        base_candidate: task.base || undefined,
        expected_branch_revision: session?.branch_revision,
        expected_player_identity_id: identity,
      });
      const latest = get().tasks[key];
      if (!latest || latest.requestId !== id) return;
      const stale = batch.stale || batch.branch_id !== branch || batch.player_identity_id !== identity
        || writingContext() !== context;
      const next = { ...latest, batch: { ...batch, stale }, context,
        composerAtStart: composer, resultRevision: task.revision, generating: false };
      save(key, next);
      set(s => ({ tasks: { ...s.tasks, [key]: next } }));
    } catch (e) {
      const latest = get().tasks[key];
      if (!latest || latest.requestId !== id) return;
      const next = { ...latest, generating: false, error: e instanceof Error ? e.message : '代笔失败，请重试。' };
      save(key, next);
      set(s => ({ tasks: { ...s.tasks, [key]: next } }));
    }
  },
}));
