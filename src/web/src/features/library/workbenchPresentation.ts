import type { Lorebook, ScenarioCreateInput, ScenarioPlayOptions } from '../../types';

export function groupLorebooks(books: Lorebook[], owners: Record<string, { world_id: string; world_title: string }>, scoped = false) {
  if (scoped) return [{ id: '', title: '', books }];
  const groups = new Map<string, { id: string; title: string; books: Lorebook[] }>();
  for (const book of books) {
    const owner = owners[book.id], id = owner?.world_id ?? '';
    if (!groups.has(id)) groups.set(id, { id, title: owner?.world_title || '未指定世界', books: [] });
    groups.get(id)!.books.push(book);
  }
  return [...groups.values()].sort((a, b) => !a.id ? 1 : !b.id ? -1 : a.title.localeCompare(b.title, 'zh-Hans-CN'));
}

export function relativeModified(date?: string, now = Date.now()) {
  if (!date || !Number.isFinite(Date.parse(date))) return '修改时间未记录';
  const minutes = Math.max(0, Math.floor((now - Date.parse(date)) / 60000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  if (minutes < 2880) return '昨天';
  if (minutes < 43200) return `${Math.floor(minutes / 1440)} 天前`;
  return `${Math.floor(minutes / 43200)} 个月前`;
}

export const SCENARIO_STEPS = ['基础信息', '角色', '世界与世界书', '开场与约定', '玩法与核对'];
export const POV_OPTIONS = [{ value: '', label: '沿用默认' }, { value: 'free', label: '自由' }, { value: 'second', label: '第二人称' }, { value: 'third', label: '第三人称' }];
export const DENSITY_OPTIONS = [{ value: '', label: '沿用默认' }, { value: 'dialogue', label: '对话为主' }, { value: 'balanced', label: '均衡' }, { value: 'atmosphere', label: '氛围描写' }];
export const DIRECTOR_OPTIONS = [{ value: '', label: '沿用默认' }, { value: 'auto', label: '自动' }, { value: 'confirm', label: '每次确认' }, { value: 'rules', label: '规则模式' }];
export function scenarioPlayLabels(play: ScenarioPlayOptions) {
  return [
    play.narrative_pov && `视角：${POV_OPTIONS.find(item => item.value === play.narrative_pov)?.label ?? '沿用默认'}`,
    play.narrative_density && `叙事：${DENSITY_OPTIONS.find(item => item.value === play.narrative_density)?.label ?? '沿用默认'}`,
    play.director_mode && `导演：${DIRECTOR_OPTIONS.find(item => item.value === play.director_mode)?.label ?? '沿用默认'}`,
  ].filter(Boolean) as string[];
}

export function scenarioCreateInput(input: ScenarioCreateInput): ScenarioCreateInput {
  return { ...input, title: input.title.trim(), description: input.description.trim(), author: input.author.trim(), license: input.license.trim(), source_url: input.source_url.trim(), tags: input.tags.map(tag => tag.trim()).filter(Boolean), character_ids: [...input.character_ids], lorebook_ids: [...input.lorebook_ids], world_id: input.world_id || null, instructions: input.instructions.trim(), opening: { ...input.opening, location: input.opening.location.trim() || '开场', description: input.opening.description.trim(), narration: input.opening.narration.trim(), member_keys: [...input.opening.member_keys] }, play: { ...input.play } };
}
