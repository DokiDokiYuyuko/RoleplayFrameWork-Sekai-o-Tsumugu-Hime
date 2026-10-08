import type { Session } from '../../types';

export type SessionFieldRevisions = Readonly<Record<string, number>>;
interface MetadataState {
  currentSessionId: string | null;
  sessions: Session[];
  sessionFieldRevisions?: SessionFieldRevisions;
}
/** Metadata fields and installed message commits have independent cursors. */
export function mergeSessionFields(previous: Session, incoming: Partial<Session>, revision: number | undefined,
  revisions: SessionFieldRevisions = {}) {
  const fields: Record<string, unknown> = {}, nextRevisions = { ...revisions };
  for (const [key, value] of Object.entries(incoming)) {
    if (key === 'branch_revision' || value === undefined) continue;
    if (revision !== undefined && revision < (revisions[key] ?? 0)) continue;
    fields[key] = value;
    if (revision !== undefined) nextRevisions[key] = revision;
  }
  return { session: { ...previous, ...fields } as Session, revisions: nextRevisions };
}

/** Call only after the branch/epoch ownership check. A complete view may also install its message revision. */
export function installSessionMetadata(state: MetadataState, branchId: string, incoming: Partial<Session>,
  revision: number | undefined, installMessageRevision = false) {
  const current = state.sessions.find(session => session.id === branchId);
  if (!current && !installMessageRevision) return { sessions: state.sessions, sessionFieldRevisions: state.sessionFieldRevisions ?? {} };
  const previous = current ?? incoming as Session;
  const owned = state.currentSessionId === branchId;
  const merged = mergeSessionFields(previous, incoming, revision, owned ? state.sessionFieldRevisions : {});
  const session = { ...merged.session, branch_revision: installMessageRevision
    ? (incoming.branch_revision ?? previous.branch_revision) : previous.branch_revision };
  return { sessions: current ? state.sessions.map(item => item.id === branchId ? session : item) : [...state.sessions, session],
    sessionFieldRevisions: owned ? merged.revisions : state.sessionFieldRevisions ?? {} };
}
