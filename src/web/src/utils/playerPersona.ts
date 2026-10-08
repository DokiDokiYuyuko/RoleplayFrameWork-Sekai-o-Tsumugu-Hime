import type { CharacterCard } from '../types';

/** 从角色卡取玩家资料，不把 NPC 的系统提示或示例对话带进玩家身份。 */
export function playerPersonaFromCard(card: CharacterCard): string {
  return [
    `姓名：${card.name.trim() || '玩家'}`,
    card.description.trim() && `身份与背景：${card.description.trim()}`,
    card.traits?.trim() && `${card.traits_label?.trim() || '能力与实力'}：${card.traits.trim()}`,
    card.personality.trim() && `性格：${card.personality.trim()}`,
    card.scenario.trim() && `场景关系：${card.scenario.trim()}`,
  ].filter(Boolean).join('\n');
}
