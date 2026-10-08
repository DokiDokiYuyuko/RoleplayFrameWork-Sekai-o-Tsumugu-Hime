/** Display-only normalization. The persisted title is never rewritten. */
export function chatTitle(title) {
  return String(title ?? '').replace(/[*_`#~>]/g, '').replace(/\s+/g, ' ').trim() || '未命名聊天';
}
export function modelShortName(model) {
  return String(model ?? '').split('/').at(-1) || '未选择模型';
}
export function modelFamily(model) {
  const name = String(model ?? '').toLowerCase();
  if (/claude|anthropic/.test(name)) return 'claude';
  if (/gemini|google/.test(name)) return 'gemini';
  if (/deepseek/.test(name)) return 'deepseek';
  if (/gpt|openai|^o[134]/.test(name)) return 'gpt';
  return 'other';
}
export function chatDateGroup(stamp, now = new Date()) {
  const date = new Date(stamp);
  if (!Number.isFinite(date.getTime())) return '更早';
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (date >= today) return '今天';
  const week = new Date(today);
  week.setDate(week.getDate() - 6);
  return date >= week ? '近 7 天' : '更早';
}
export function chatRelativeTime(stamp, now = new Date()) {
  const date = new Date(stamp);
  if (!Number.isFinite(date.getTime())) return '时间未知';
  const minutes = Math.max(0, Math.floor((now - date) / 60000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  const days = Math.floor(minutes / 1440);
  if (days < 7) return `${days} 天前`;
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}
