export const TRIAL_ROUNDS = 6;
export const TRIAL_HISTORY_LIMIT = 20000;
const PERSONA_FIELDS = ['name', 'description', 'appearance', 'traits_label', 'traits', 'personality', 'scenario', 'system_prompt', 'post_history_instructions', 'mes_example'];
export function personaSignature(card) { return JSON.stringify(PERSONA_FIELDS.map(key => card[key] ?? '')); }
export function characterDraftKey(characterId, draftId) { return `mrp.character-editor.${characterId || `new:${draftId}`}`; }
export function applyAiCandidate(card, candidate) {
  if (personaSignature(card) !== candidate.signature || String(card[candidate.field] ?? '') !== candidate.original) throw new Error('人设已改变，这份候选已过期。请重新生成或手动复制。');
  const value = candidate.selection ? candidate.original.slice(0, candidate.selection.start) + candidate.value + candidate.original.slice(candidate.selection.end) : candidate.value;
  return { ...card, [candidate.field]: value };
}
export function trialHistoryError(history) {
  if (history.length > 10 || history.length % 2) return '最多六轮试聊，请重新试聊。';
  if (history.some((row, index) => row.role !== (index % 2 ? 'assistant' : 'user') || !row.content?.trim() || row.content.length > 5000)) return '消息身份或长度无效，单条最多 5000 字符。';
  if (history.reduce((total, row) => total + row.content.length, 0) > TRIAL_HISTORY_LIMIT) return '试聊历史超过 20000 字符，请重新试聊。';
  return '';
}
export function formatVoiceExample(player, reply) { return `<START>\n{{user}}: ${player.trim()}\n{{char}}: ${reply.trim()}`; }
export function appendVoiceExample(existing, example) {
  const blocks = existing.split(/\n?\s*<START>\s*\n/).map(block => block.trim());
  const body = example.replace(/^<START>\s*\n/, '').trim();
  return blocks.includes(body) ? existing : existing.trimEnd() + (existing.trim() ? '\n\n' : '') + example;
}
