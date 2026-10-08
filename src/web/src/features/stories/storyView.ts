import type { StoryView as WireStoryView, SessionView as WireSession, SetupMeta as WireSetupMeta } from '../../api/generated/story-contracts';
import type { Character, ConversationRun, DirectorDecision, GroupActor, Lorebook, Message, Scene, Session, TurnRun } from '../../types';

/** Reader data is separate from archive/write models. All ingress uses this projection. */
export interface StoryView extends Pick<WireStoryView, "event_cursor" | "next_before_seq" | "latest_seq"> {
  session: Session & Pick<WireSession, "id" | "branch_revision" | "turn">;
  messages: Message[];
  characters: Character[];
  lorebooks: Lorebook[];
  groups: GroupActor[];
  scenes: Scene[];
  active_scene_id: string | null;
  event_cursor: string | null;
  conversation_runs?: ConversationRun[];
  turn_runs?: TurnRun[];
  pending_director?: DirectorDecision | null;
  latest_seq?: number;
  next_before_seq?: number | null;
}
export interface StoryPageOptions { signal?: AbortSignal; before_seq?: number; around?: string; limit?: number }
export interface SessionSetup {
  meta: Pick<WireSetupMeta, "id" | "title" | "branch_revision" | "lorebook_ids" | "character_ids" | "director_mode"> & { id: string; branch_revision: number; title: string; character_ids: string[]; lorebook_ids: string[];
    player_persona?: string; player_character_id?: string | null; streaming_enabled?: boolean;
    hygiene_enabled?: boolean; director_mode?: 'auto' | 'confirm' | 'rules' };
  characters: Character[];
  lorebooks: Lorebook[];
}
export interface SessionSetupPatch {
  expected_branch_revision: number; title: string; lorebook_ids: string[];
  streaming_enabled: boolean; hygiene_enabled: boolean; director_mode: 'auto' | 'confirm' | 'rules';
}

const privateKeys = new Set(['baseline_state', 'baseline_material_ref', 'material_blobs', 'parallel_context',
  'world_archive_records', 'state_revisions', 'generation_operations', 'snapshot_ref']);
/** Structural sharing keeps unchanged messages stable during streamed updates. */
function withoutMaterials<T>(value: T): T {
  if (!value || typeof value !== 'object') return value;
  if (Array.isArray(value)) {
    const next = value.map(withoutMaterials);
    return next.some((item, index) => item !== value[index]) ? next as T : value;
  }
  let changed = false;
  const next: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (privateKeys.has(key)) { changed = true; continue; }
    next[key] = withoutMaterials(item);
    if (next[key] !== item) changed = true;
  }
  return changed ? next as T : value;
}
export const projectMessage = (message: Message): Message => withoutMaterials(message);
export const projectTurnRun = (run: TurnRun): TurnRun => withoutMaterials(run);
export const projectMessages = (messages: Message[]): Message[] => {
  const next = messages.map(projectMessage);
  return next.some((message, index) => message !== messages[index]) ? next : messages;
};
export function contiguousPrefix(older: Message[], latest: Message[]): Message[] {
  if (!older.length || !latest.length) return latest;
  const prefix = older.filter(message => message.seq < latest[0].seq);
  return prefix.length && prefix[prefix.length - 1].seq + 1 === latest[0].seq ? [...prefix, ...latest] : latest;
}
