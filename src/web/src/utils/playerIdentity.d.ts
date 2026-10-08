import type { Message, PlayerIdentity, Session } from '../types';
export function resolvePlayerIdentity(session: Session | undefined, message?: Message): PlayerIdentity | null;
export function isBeforePlayerBoundary(session: Session | undefined, message: Message): boolean;
export function playerDraftKey(branchId: string | null, identityId?: string | null): string;
export function playerAvatarUrl(branchId: string | null, identity: PlayerIdentity | null): string | undefined;
export function currentPlayerAvatarUrl(branchId: string | null, identity: PlayerIdentity | null): string | undefined;
export function canPersonSee(message: Message, personId: string): boolean;
