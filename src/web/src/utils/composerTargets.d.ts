export interface ComposerTargetCharacter {
  id: string;
  present: boolean;
  muted: boolean;
}

export interface ComposerTargetGroup {
  id: string;
}

export function canReconcileComposerTargets(
  sessionId: string | null,
  loadedTargetsSessionId: string | null,
  snapshotLoadedSessionId: string | null,
): boolean;

export function filterValidComposerTargets(
  recipients: string[],
  characters: ComposerTargetCharacter[],
  groups: ComposerTargetGroup[],
): string[];
