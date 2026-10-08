import type { CharacterCard } from '../types';
export const TRIAL_ROUNDS: number;
export const TRIAL_HISTORY_LIMIT: number;
export interface TrialMessage { role: 'user' | 'assistant'; content: string }
export interface AiCandidate { field: string; original: string; value: string; signature: string; selection?: { start: number; end: number } }
export function personaSignature(card: CharacterCard): string;
export function characterDraftKey(characterId: string | undefined, draftId: string): string;
export function applyAiCandidate(card: CharacterCard, candidate: AiCandidate): CharacterCard;
export function trialHistoryError(history: TrialMessage[]): string;
export function formatVoiceExample(player: string, reply: string): string;
export function appendVoiceExample(existing: string, example: string): string;
