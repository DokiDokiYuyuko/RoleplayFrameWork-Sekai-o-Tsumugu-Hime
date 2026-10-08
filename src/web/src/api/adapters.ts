// 后端 → 前端 的**少量**适配。
//
// 契约层已经与后端 models.py 逐字段对齐（见 ../types），所以这里只剩两类工作：
//   1. 纯 UI 派生（气泡色系）——后端不存这个字段
//   2. 端点形状归一（会话列表 vs 会话状态返回的结构不同）
// 曾经存在的 uid↔id / anchor 改名 / generation_meta 重排 / 成本换算全部删掉了：
// 那些不是适配，是把契约漂移的代价转嫁给运行时。

import type { Character, CharacterColor, CharacterView, CostBucket, Session } from '../types';

const PALETTE: CharacterColor[] = ['emerald', 'violet', 'amber', 'sky', 'rose', 'slate'];

/** 由角色 id 稳定派生气泡色系（同一角色每次刷新同色） */
export function colorFor(id: string): CharacterColor {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function toView(c: Character): CharacterView {
  return { ...c, color: colorFor(c.id) };
}

export const EMPTY_BUCKET: CostBucket = { input_tokens: 0, output_tokens: 0, cached_tokens: 0 };

/** GET /api/v1/sessions —— 摘要行 */
export function mapSessionSummary(raw: Record<string, unknown>): import("../types").SessionSummary {
  return {
    id: String(raw.id),
    title: String(raw.title ?? ''),
    created_at: String(raw.created_at ?? raw.updated_at ?? ''),
    source_world_id: raw.source_world_id == null ? null : String(raw.source_world_id),
    story_id: raw.story_id == null ? String(raw.id) : String(raw.story_id),
    branch_name: raw.branch_name == null ? String(raw.title ?? '') : String(raw.branch_name),
    parent_branch_id: raw.parent_branch_id == null ? null : String(raw.parent_branch_id),
    branch_revision: Number(raw.branch_revision ?? 0),
    archived: Boolean(raw.archived ?? false),
    prompt_preset_id: raw.prompt_preset_id == null ? null : String(raw.prompt_preset_id),
    character_ids: (raw.character_ids as string[]) ?? [],
    character_names: (raw.character_names as string[]) ?? [],
    persona: String(raw.persona ?? ''),
    player_character_id: raw.player_character_id == null ? null : String(raw.player_character_id),
    reply_max_tokens: raw.reply_max_tokens == null ? null : Number(raw.reply_max_tokens),
    turn: Number(raw.turn ?? 0),
    options_enabled: raw.options_enabled == null ? undefined : Boolean(raw.options_enabled),
    options_style: raw.options_style == null ? undefined : String(raw.options_style),
    options_direct_send: raw.options_direct_send == null ? undefined : Boolean(raw.options_direct_send),
    streaming_enabled: raw.streaming_enabled == null ? undefined : Boolean(raw.streaming_enabled),
    hygiene_enabled: raw.hygiene_enabled == null ? undefined : Boolean(raw.hygiene_enabled),
    director_mode: raw.director_mode == null ? undefined : (raw.director_mode as Session['director_mode']),
    narrative_pov: raw.narrative_pov == null ? undefined : (raw.narrative_pov as Session['narrative_pov']),
      response_style_id: raw.response_style_id as string | null | undefined,
      response_style_overrides: (raw.response_style_overrides ?? {}) as Record<string, string | null>,
    narrative_density: raw.narrative_density == null ? undefined : (raw.narrative_density as Session['narrative_density']),
    short_input_padding: raw.short_input_padding == null ? undefined : Boolean(raw.short_input_padding),
  };
}

/** GET /api/v1/sessions/{id} —— 会话全量状态（取 meta + 末条消息的 turn） */
export function mapSessionState(state: Record<string, unknown>): Session {
  const meta = (state.meta ?? {}) as Record<string, unknown>;
  const messages = (state.messages ?? []) as Array<{ turn: number }>;
  return {
    response_style_id: meta.response_style_id as string | null | undefined,
    response_style_overrides: (meta.response_style_overrides ?? {}) as Record<string, string | null>,
    id: String(meta.id),
    title: String(meta.title ?? ''),
    created_at: String(meta.created_at ?? ''),
    source_world_id: meta.source_world_id == null ? null : String(meta.source_world_id),
    source_world_revision: meta.source_world_revision == null ? null : Number(meta.source_world_revision),
    world_core_brief: String(meta.world_core_brief ?? ''),
    story_id: String(meta.story_id ?? meta.id),
    branch_name: String(meta.branch_name ?? meta.title ?? ''),
    parent_branch_id: meta.parent_branch_id == null ? null : String(meta.parent_branch_id),
    branch_revision: Number(meta.branch_revision ?? 0),
    archived: Boolean(meta.archived ?? false),
    character_ids: (meta.character_ids as string[]) ?? [],
    character_names: ((state.characters ?? []) as Array<{ card?: { name?: string } }>).map(
      (character) => character.card?.name ?? '',
    ),
    persona: String(meta.player_persona ?? ''),
    player_identity_id: meta.player_identity_id == null ? null : String(meta.player_identity_id),
    player_identities: (state.player_identities ?? []) as import('../types').PlayerIdentity[],
    player_people: (state.player_people ?? {}) as Record<string, Character>,
    player_character_id: meta.player_character_id == null ? null : String(meta.player_character_id),
    reply_max_tokens: meta.reply_max_tokens == null ? null : Number(meta.reply_max_tokens),
    prompt_preset_id: meta.prompt_preset_id == null ? null : String(meta.prompt_preset_id),
    options_enabled: meta.options_enabled == null ? undefined : Boolean(meta.options_enabled),
    options_style: meta.options_style == null ? undefined : String(meta.options_style),
    options_direct_send: meta.options_direct_send == null ? undefined : Boolean(meta.options_direct_send),
    streaming_enabled: meta.streaming_enabled == null ? undefined : Boolean(meta.streaming_enabled),
    hygiene_enabled: meta.hygiene_enabled == null ? undefined : Boolean(meta.hygiene_enabled),
    director_mode: meta.director_mode == null ? undefined : (meta.director_mode as Session['director_mode']),
    narrative_pov: meta.narrative_pov == null ? undefined : (meta.narrative_pov as Session['narrative_pov']),
    narrative_density: meta.narrative_density == null ? undefined : (meta.narrative_density as Session['narrative_density']),
    short_input_padding: meta.short_input_padding == null ? undefined : Boolean(meta.short_input_padding),
    pinned_facts: (state.pinned_facts as Session['pinned_facts']) ?? [],
    turn: messages.length > 0 ? messages[messages.length - 1].turn : 0,
  };
}
