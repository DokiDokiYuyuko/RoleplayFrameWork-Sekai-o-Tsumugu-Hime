export const conversationPreferenceKey = (branchId, identityId) => `mrp.conversation.${branchId ?? 'none'}.${identityId ?? 'player'}`;
export function normalizeMaxReplies(value) {
  if (value == null || (typeof value === 'string' && !value.trim())) return 6;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(1, Math.min(30, Math.round(number))) : 6;
}
export function readConversationPreferences(storage, key) {
  try {
    const value = JSON.parse(storage.getItem(key) || '{}');
    return {
      recipients: Array.isArray(value.recipients) ? [...new Set(value.recipients.filter((id) => typeof id === 'string'))] : [],
      replyMode: ['auto', 'parallel', 'serial', 'free'].includes(value.replyMode) ? value.replyMode : 'auto',
      maxReplies: value.maxReplies == null ? 6 : normalizeMaxReplies(value.maxReplies),
    };
  } catch { return { recipients: [], replyMode: 'auto', maxReplies: 6 }; }
}
export function saveConversationPreferences(storage, key, value) {
  try { storage.setItem(key, JSON.stringify(value)); } catch { /* Private storage may be unavailable. */ }
}
