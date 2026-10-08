import type { ReplyMode } from '../types';
export interface ConversationPreferences { recipients: string[]; replyMode: ReplyMode; maxReplies: number }
export function conversationPreferenceKey(branchId: string | null, identityId: string | null | undefined): string;
export function normalizeMaxReplies(value: unknown): number;
export function readConversationPreferences(storage: Pick<Storage, 'getItem'>, key: string): ConversationPreferences;
export function saveConversationPreferences(storage: Pick<Storage, 'setItem'>, key: string, value: ConversationPreferences): void;
