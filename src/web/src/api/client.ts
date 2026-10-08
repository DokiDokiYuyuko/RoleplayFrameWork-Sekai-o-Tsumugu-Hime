import { readSettings, writeSettings } from '../features/resources/settingsResource';
import type { StoryDeleteResult, StoryRestoreResult, StoryPurgeResult, StoryPresetResult, MemoryScheduledResult, MemoryJobView, MemoryRecordView, MemoryRecordsResult, LegacyRestoreResult } from './generated/story-contracts';
import { attachCommandReceipt, commandReceipt } from '../utils/commandReceipt';
import { projectMessages, projectTurnRun, type StoryView, type SessionSetup } from '../features/stories/storyView';
import { storyCommand, uploadStoryCommand, queryStoryCommand } from './storyCommands';
import type { WritingRequest, WritingBatch } from './writing';
// API 接缝层：真实后端实现（fetch），契约见 ./Api.ts。
// 后端响应已与 ../types 的契约逐字段对齐，故这里基本是直通；只有会话端点
// （列表 vs 全量状态两种形状）与角色色系需要经过 adapters。

import type {
  AssistBatch,
  BundleReport,
  Character,
  CharacterCard,
  CharacterPatch,
  CharacterView,
  GroupActor,
  GroupDraftResult,
  GroupSourcesResult,
  CostReport,
  Inspection,
  Lorebook,
  MemoryRecord,
  MemoryPatch, MemoryWindow,
  Message,
  ModelCatalogResult,
  ModelProvidersResult,
  ModelRequestRecord,
  ModelRequestSummary,
  SimpleChat,
  SimpleChatSummary,
  PinnedFact,
  Save,
  ScenarioCreateInput,
  ScenarioPackage,
  ScenarioSummary,
  Scene,
  Session,
  Settings,
  SettingsTestResult,
  TTSStatus,
  VoiceProfile,
  StorySummary,
  WorldlinePage,
  BranchPoint,
  ForkResult,
  StoryEvent,
  World,
  WorldSummary,
  CardSourceCapability,
  CardSearchResponse,
  CardSearchPage,
  DiscoveredCardDetail,
  CardInspirationJob,
  CardInspirationJobSummary,
  CardInspirationDraft,
} from '../types';
import type { Api, CharacterAction, LorebookPatch, NewSessionInput } from './Api';
import { mapSessionState, mapSessionSummary, toView } from './adapters';
import { jsonRequest as jfetch } from './request';


export const api: Api = {
  listSimpleChats: async (options) => { const result = await jfetch<{ chats: SimpleChatSummary[] }>('/api/v1/simple-chats', options); if (!Array.isArray(result.chats)) throw new Error('聊天列表不完整，请重新读取。'); return result.chats; },
  createSimpleChat: () => jfetch<SimpleChat>('/api/v1/simple-chats', { method: 'POST' }),
  getSimpleChat: async (id, options) => {
    const chat = await jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}`, options);
    if (chat.id !== id || !Array.isArray(chat.messages)) throw new Error('聊天资料不完整，请重新读取。');
    return chat;
  },
  patchSimpleChat: (id, patch) => jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteSimpleChat: async (id) => { await jfetch(`/api/v1/simple-chats/${encodeURIComponent(id)}`, { method: 'DELETE' }); },
  sendSimpleChatMessage: (id, content, clientMessageId) => jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}/messages`, { method: 'POST', body: JSON.stringify({ content, client_message_id: clientMessageId }) }),
  stopSimpleChatGeneration: (id) => jfetch<{ stopping: boolean }>(`/api/v1/simple-chats/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  editSimpleChatMessage: (id, messageId, content, truncateAfter) => jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}`, { method: 'PATCH', body: JSON.stringify({ content, truncate_after: truncateAfter }) }),
  deleteSimpleChatMessage: (id, messageId, truncateAfter) => jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}`, { method: 'DELETE', body: JSON.stringify({ truncate_after: truncateAfter }) }),
  regenerateSimpleChatMessage: (id, messageId) => jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/regenerate`, { method: 'POST' }),
  switchSimpleChatVariant: (id, messageId, activeVariant) => jfetch<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/variant`, { method: 'PATCH', body: JSON.stringify({ active_variant: activeVariant }) }),
  listWorlds: (options) => jfetch<WorldSummary[]>('/api/v1/worlds', { signal: options?.signal }),
  listWorldCovers: (options) => jfetch('/api/v1/world-covers', { signal: options?.signal }),
  listWorldOwners: (options) => jfetch<Record<string, { world_id: string; world_title: string }>>('/api/v1/worlds/lorebook-owners', { signal: options?.signal }),
  getWorld: (id, options) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}`, { signal: options?.signal }),
  createWorld: (input) => jfetch<World>('/api/v1/worlds', { method: 'POST', body: JSON.stringify(input) }),
  patchWorld: (id, input) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(input) }),
  createArchive: (id, expected, record) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}/archive`, { method: 'POST', body: JSON.stringify({ expected_revision: expected, record }) }),
  patchArchive: (id, recordId, expected, record) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}/archive/${encodeURIComponent(recordId)}`, { method: 'PATCH', body: JSON.stringify({ expected_revision: expected, record }) }),
  importBiology: (id, expected, sourceWorldId, items) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}/archive/import-biology`, { method: 'POST', body: JSON.stringify({ expected_revision: expected, source_world_id: sourceWorldId, items }) }),
  deleteArchive: (id, recordId, expected) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}/archive/${encodeURIComponent(recordId)}`, { method: 'DELETE', body: JSON.stringify({ expected_revision: expected }) }),
  linkWorldLorebook: (id, bookId, expected) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}/lorebooks`, { method: 'POST', body: JSON.stringify({ book_id: bookId, expected_revision: expected }) }),
  unlinkWorldLorebook: (id, bookId, expected) => jfetch<World>(`/api/v1/worlds/${encodeURIComponent(id)}/lorebooks/${encodeURIComponent(bookId)}`, { method: 'DELETE', body: JSON.stringify({ expected_revision: expected }) }),
  importWorld: (payload) => jfetch<World>('/api/v1/worlds/import', { method: 'POST', body: JSON.stringify(payload) }),
  saveScenarioWorld: (id) => jfetch<World>(`/api/v1/scenarios/${encodeURIComponent(id)}/save-world`, { method: 'POST' }),
  listStories: (options) => jfetch<StorySummary[]>('/api/v1/stories', { signal: options?.signal }),
  deleteStory: (id, membershipRevision) => queryStoryCommand<StoryDeleteResult>(`/api/v1/stories/${encodeURIComponent(id)}`, 'DELETE', {}, { membership_revision: membershipRevision }),
  listStoryTrash: (options) => jfetch<import('../types').TrashedStory[]>('/api/v1/stories/trash', { signal: options?.signal }),
  restoreStory: (id, generationId) => queryStoryCommand<StoryRestoreResult>(`/api/v1/stories/trash/${encodeURIComponent(id)}/restore`, 'POST', {}, { generation_id: generationId }),
  deleteBranch: (id, membershipRevision) => queryStoryCommand<StoryDeleteResult>(`/api/v1/sessions/${encodeURIComponent(id)}`, 'DELETE', {}, { membership_revision: membershipRevision }),
  purgeStory: (id, generationId) => queryStoryCommand<StoryPurgeResult>(`/api/v1/stories/trash/${encodeURIComponent(id)}`, 'DELETE', {}, { generation_id: generationId }),
  getStoryBackupStatus: (options) => jfetch<{ errors: Record<string, string> }>('/api/v1/stories/backup-status', { signal: options?.signal }),
  listPromptPresets: () => jfetch<import('../types').PromptPreset[]>('/api/v1/prompt-presets'),
  savePromptPreset: (preset) => jfetch<import('../types').PromptPreset>('/api/v1/prompt-presets', { method: 'POST', body: JSON.stringify(preset) }),
  deletePromptPreset: async (id) => { await jfetch(`/api/v1/prompt-presets/${encodeURIComponent(id)}`, { method: 'DELETE' }); },
  previewPromptPresetImport: async (file) => {
    const form = new FormData(); form.append('file', file);
    const response = await fetch('/api/v1/prompt-presets/import-preview', { method: 'POST', body: form });
    if (!response.ok) throw new Error(`${response.status}: ${(await response.text()).slice(0, 200)}`);
    return response.json();
  },
  listStoryBookmarks: (storyId) => jfetch<import('../types').StoryBookmark[]>(`/api/v1/stories/${encodeURIComponent(storyId)}/bookmarks`),
  createBookmark: (branchId, messageId, title = '') => storyCommand<import('../types').StoryBookmark>(`/api/v1/branches/${encodeURIComponent(branchId)}/bookmarks`, 'POST', { message_id: messageId, title }),
  deleteBookmark: async (branchId, bookmarkId) => { await storyCommand(`/api/v1/branches/${encodeURIComponent(branchId)}/bookmarks/${encodeURIComponent(bookmarkId)}`, 'DELETE', {}); },
  patchBookmark: (branchId, bookmarkId, patch) => storyCommand<import('../types').StoryBookmark>(`/api/v1/branches/${encodeURIComponent(branchId)}/bookmarks/${encodeURIComponent(bookmarkId)}`, 'PATCH', patch),
  searchStory: (storyId, query, options = {}) => {
    const params = new URLSearchParams({ q: query, cursor: String(options.cursor ?? 0) });
    if (options.branch_id) params.set('branch_id', options.branch_id);
    if (options.actor) params.set('actor', options.actor);
    if (options.scene_id) params.set('scene_id', options.scene_id);
    if (options.bookmarked) params.set('bookmarked', 'true');
    if (options.event) params.set('event', 'true');
    return jfetch<{ results: import('../types').StorySearchResult[]; next_cursor: number | null }>(`/api/v1/stories/${encodeURIComponent(storyId)}/search?${params}`);
  },
  setStoryPromptPreset: (storyId, presetId, membershipRevision) => queryStoryCommand<StoryPresetResult>(`/api/v1/stories/${encodeURIComponent(storyId)}/prompt-preset`, 'PATCH', { preset_id: presetId }, { membership_revision: membershipRevision }),
  importStory: (file) => uploadStoryCommand('/api/v1/stories/import', file),
  getWorldline: (storyId, cursor = 0, query = '') => jfetch<WorldlinePage>(`/api/v1/stories/${encodeURIComponent(storyId)}/worldline?cursor=${cursor}&limit=100&query=${encodeURIComponent(query)}`),
  getBranchPoint: (branchId, messageId) => jfetch<BranchPoint>(`/api/v1/branches/${encodeURIComponent(branchId)}/messages/${encodeURIComponent(messageId)}/point`),
  forkBranch: (branchId, input) => storyCommand<ForkResult>(`/api/v1/branches/${encodeURIComponent(branchId)}/fork`, 'POST', { ...input }),
  forkSave: (saveId, title, idempotencyKey) => storyCommand<ForkResult>(`/api/v1/saves/${encodeURIComponent(saveId)}/fork`, 'POST', { title, idempotency_key: idempotencyKey }),
  patchBranch: (branchId, input) => storyCommand(`/api/v1/branches/${encodeURIComponent(branchId)}`, 'PATCH', { ...input }),
  createStoryEvent: (branchId, input) => storyCommand<StoryEvent>(`/api/v1/branches/${encodeURIComponent(branchId)}/events`, 'POST', { ...input }),
  updateStoryEvent: (branchId, eventId, input) => storyCommand<StoryEvent>(`/api/v1/branches/${encodeURIComponent(branchId)}/events/${encodeURIComponent(eventId)}`, 'PATCH', { ...input }),
  deleteStoryEvent: async (branchId, eventId) => { await storyCommand(`/api/v1/branches/${encodeURIComponent(branchId)}/events/${encodeURIComponent(eventId)}`, 'DELETE', {}); },
  // -- 会话 --
  listSessions: async (): Promise<import("../types").SessionSummary[]> => {
    const rows = await jfetch<Record<string, unknown>[]>('/api/v1/sessions');
    return rows.map(mapSessionSummary);
  },

  createSession: async (input: NewSessionInput): Promise<Session> => {
    const state = await storyCommand<Record<string, unknown>>('/api/v1/sessions', 'POST', {
        title: input.title,
        character_ids: input.character_ids,
        player_persona: input.persona,
        player_character_id: input.player_character_id ?? null,
        reply_max_tokens: input.reply_max_tokens ?? null,
        lorebook_ids: input.lorebook_ids ?? [],
        world_id: input.world_id ?? null,
        opening_scene: input.opening_scene ?? '',
        greeting_choices: input.greeting_choices ?? {},
      });
    return attachCommandReceipt(mapSessionState(state), commandReceipt(state));
  },

  getStoryView: async (sessionId, options = {}) => {
    const query = new URLSearchParams();
    if (options.before_seq != null) query.set('before_seq', String(options.before_seq));
    if (options.around) query.set('around', options.around);
    if (options.limit != null) query.set('limit', String(options.limit));
    const view = await jfetch<StoryView>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/view${query.size ? '?' + query : ''}`, { signal: options.signal });
    if (view.session?.id !== sessionId || !Array.isArray(view.messages) || !Array.isArray(view.characters)) throw new Error('路线资料不完整，请重新读取。');
    return { ...view, messages: projectMessages(view.messages), turn_runs: view.turn_runs?.map(projectTurnRun) };
  },
  getSessionSetup: async (sessionId, options) => {
    const setup = await jfetch<SessionSetup>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/setup`, options);
    if (setup.meta?.id !== sessionId || !Array.isArray(setup.meta.lorebook_ids) || !Number.isInteger(setup.meta.branch_revision)) throw new Error('会话设置不完整，请重新读取。');
    return setup;
  },
  patchSessionSetup: (sessionId, patch) => storyCommand(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, 'PATCH', { ...patch }),

  getSessionSnapshot: async (sessionId: string, options) => {
    const state = await jfetch<Record<string, unknown>>(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, options);
    if (!state.meta || !Array.isArray(state.messages) || !Array.isArray(state.characters)) throw new Error('路线资料不完整，请重新读取。');
    if ((state.meta as { id?: string }).id !== sessionId) throw new Error('路线与请求不匹配，请重新读取。');
    return {
      session: mapSessionState(state),
      messages: (state.messages ?? []) as Message[],
      characters: (state.characters ?? []) as Character[],
      lorebooks: (state.lorebooks ?? []) as Lorebook[],
      groups: (state.groups ?? []) as GroupActor[],
      scenes: (state.scenes ?? []) as Scene[],
      active_scene_id: (state.active_scene_id ?? null) as string | null,
      event_cursor: (state.event_cursor ?? null) as string | null,
      conversation_runs: (state.conversation_runs ?? []) as import('../types').ConversationRun[],
      turn_runs: (state.turn_runs ?? []) as import('../types').TurnRun[],
      pending_director: (state.pending_director ?? null) as import('../types').DirectorDecision | null,
    };
  },

  listMessages: async (sessionId: string): Promise<Message[]> => {
    const state = await jfetch<{ messages?: Message[] }>(`/api/v1/sessions/${sessionId}`);
    return state.messages ?? [];
  },

  switchPlayer: async (sessionId, input) => {
    const state = await jfetch<Record<string, unknown>>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/player/switch`, {
      method: 'POST', body: JSON.stringify(input),
    });
    return { session: mapSessionState(state), messages: (state.messages ?? []) as Message[],
      characters: (state.characters ?? []) as Character[], lorebooks: (state.lorebooks ?? []) as Lorebook[],
      groups: (state.groups ?? []) as GroupActor[], scenes: (state.scenes ?? []) as Scene[],
      active_scene_id: (state.active_scene_id ?? null) as string | null,
      event_cursor: (state.event_cursor ?? null) as string | null,
      conversation_runs: (state.conversation_runs ?? []) as import('../types').ConversationRun[] };
  },

  addStoryParticipant: async (sessionId, input) => {
    const response = await storyCommand<{ added: boolean }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/participants`, 'POST', { ...input });
    return response;
  },

  listGroups: (sessionId) => jfetch<GroupActor[]>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups`),
  listGroupSources: (sessionId) => jfetch<GroupSourcesResult>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups/sources`),
  draftGroup: (sessionId, input) => jfetch<GroupDraftResult>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups/draft`, {
    method: 'POST', body: JSON.stringify(input),
  }),
  createGroup: (sessionId, input) => storyCommand<GroupActor>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups`, 'POST', { ...input }),
  patchGroup: (sessionId, groupId, input) => storyCommand<GroupActor>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups/${encodeURIComponent(groupId)}`, 'PATCH', { ...input }),
  leaveGroup: (sessionId, groupId, expectedBranchRevision) => storyCommand<GroupActor>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups/${encodeURIComponent(groupId)}/leave`, 'POST', { expected_branch_revision: expectedBranchRevision }),
  replyGroup: (sessionId, groupId, idempotencyKey) => storyCommand<Message>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups/${encodeURIComponent(groupId)}/reply`, 'POST', { idempotency_key: idempotencyKey }),
  swipeGroup: (sessionId, groupId, messageId) => storyCommand<Message>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/groups/${encodeURIComponent(groupId)}/messages/${encodeURIComponent(messageId)}/swipe`, 'POST', {}),

  sendMessage: async (sessionId: string, content: string, mentions?: string[], channel?: string, clientMessageId?: string, replyMode?: import('../types').ReplyMode, expectedPlayerIdentity?: string | null, maxReplies?: number, conversationDirective?: string) => {
    const d = await jfetch<{ messages: Message[]; errors?: string[]; conversation_run?: import('../types').ConversationRun;
      turn_run?: import('../types').TurnRun }>(`/api/v1/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content, mentions: mentions ?? [], channel: channel ?? 'dialogue', client_message_id: clientMessageId, reply_mode: replyMode ?? 'auto', expected_player_identity_id: expectedPlayerIdentity ?? undefined, max_replies: replyMode === 'free' ? maxReplies ?? 6 : undefined, conversation_directive: replyMode === 'free' ? conversationDirective?.trim() ?? '' : undefined }),
    });
    // SSE 负责即时展示；HTTP 整包负责断线时补齐所有本轮回复。
    return { messages: d.messages, errors: d.errors ?? [], conversation_run: d.conversation_run, turn_run: d.turn_run };
  },

  controlTurn: (sid, operationId, action, input) => jfetch(
    `/api/v1/sessions/${encodeURIComponent(sid)}/turn-runs/${encodeURIComponent(operationId)}/${action}`,
    { method: 'POST', body: JSON.stringify(input) }),

  startConversation: (sid, input) => jfetch(`/api/v1/sessions/${encodeURIComponent(sid)}/conversation-runs`, { method: 'POST', body: JSON.stringify(input) }),
  pauseConversation: (sid, runId) => jfetch(`/api/v1/sessions/${encodeURIComponent(sid)}/conversation-runs/${encodeURIComponent(runId)}/pause`, { method: 'POST' }),
  stopConversation: (sid, runId) => jfetch(`/api/v1/sessions/${encodeURIComponent(sid)}/conversation-runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' }),
  resumeConversation: (sid, runId, input) => jfetch(`/api/v1/sessions/${encodeURIComponent(sid)}/conversation-runs/${encodeURIComponent(runId)}/resume`, { method: 'POST', body: JSON.stringify(input) }),

  swipeMessage: async (sessionId: string, messageId: string): Promise<Message> =>
    storyCommand<Message>(`/api/v1/sessions/${sessionId}/messages/${messageId}/swipe`, 'POST', {}),

  regenerateOne: (sessionId, messageId, input) => storyCommand(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/regenerate-one`, 'POST', { ...input }),
  regenerateDependents: (sessionId, messageId, input) => storyCommand(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/regenerate-dependents`, 'POST', { ...input }),
  acceptDependencies: (sessionId, messageId, input) => storyCommand(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/accept-dependencies`, 'POST', { ...input }),

  /** R45 重跑最后一轮：旧回复由后端删除（SSE message.deleted），返回新生成消息 */
  regenerateMessage: async (sessionId: string, messageId: string): Promise<{ messages: Message[]; errors: string[] }> => {
    const d = await storyCommand<{ messages: Message[]; errors?: string[] }>(`/api/v1/sessions/${sessionId}/messages/${messageId}/regenerate`, 'POST', {});
    return attachCommandReceipt({ messages: d.messages, errors: d.errors ?? [] }, commandReceipt(d));
  },

  editMessage: async (sessionId: string, messageId: string, content: string, expectedBranchRevision?: number, expectedFingerprint?: string): Promise<Message> =>
    storyCommand<Message>(`/api/v1/sessions/${sessionId}/messages/${messageId}`, 'PATCH',
      { content, expected_branch_revision: expectedBranchRevision, expected_fingerprint: expectedFingerprint }),

  editInputGroup: async (sessionId, messageId, input) =>
    storyCommand(`/api/v1/sessions/${sessionId}/messages/${messageId}/input-group`, 'PATCH', { ...input }),

  switchVariant: async (sessionId: string, messageId: string, activeVariant: number, input): Promise<Message> =>
    storyCommand<Message>(`/api/v1/sessions/${sessionId}/messages/${messageId}`, 'PATCH', { active_variant: activeVariant, ...input }),

  deleteMessage: async (sessionId: string, messageId: string, expectedBranchRevision?: number) => {
    return storyCommand<{ ok: boolean }>(`/api/v1/sessions/${sessionId}/messages/${messageId}`, 'DELETE', { expected_branch_revision: expectedBranchRevision });
  },

  recheckHygiene: async (sessionId: string, messageId: string): Promise<Message> =>
    storyCommand<Message>(`/api/v1/sessions/${sessionId}/messages/${messageId}/hygiene/recheck`, 'POST', {}),

  createPinnedFact: async (sessionId: string, input: { content: string; message_id?: string; visible_to?: import('../types').Visibility }): Promise<PinnedFact> =>
    storyCommand<PinnedFact>(`/api/v1/sessions/${sessionId}/pinned-facts`, 'POST', input),

  updatePinnedFact: async (sessionId: string, factId: string, content: string): Promise<PinnedFact> =>
    storyCommand<PinnedFact>(`/api/v1/sessions/${sessionId}/pinned-facts/${factId}`, 'PATCH', { content }),

  deletePinnedFact: async (sessionId: string, factId: string) => {
    return storyCommand<{ ok: boolean }>(`/api/v1/sessions/${sessionId}/pinned-facts/${factId}`, 'DELETE', {});
  },

  sceneSwitch: async (sessionId: string, input: {
    title: string; description?: string; member_ids?: string[]; first_speaker_ids?: string[];
  }): Promise<Message[]> =>
    storyCommand<{ messages: Message[] }>(`/api/v1/sessions/${sessionId}/scene/switch`, 'POST', { ...input }).then((r) =>
      attachCommandReceipt(r.messages.map(message => attachCommandReceipt(message, commandReceipt(r))), commandReceipt(r))),

  confirmDirector: async (sessionId: string): Promise<Message[]> =>
    storyCommand<{ messages: Message[] }>(`/api/v1/sessions/${sessionId}/director/pending/confirm`, 'POST', {}).then((r) => attachCommandReceipt(r.messages.map(message => attachCommandReceipt(message, commandReceipt(r))), commandReceipt(r))),

  rejectDirector: async (sessionId: string): Promise<Message[]> =>
    storyCommand<{ messages: Message[] }>(`/api/v1/sessions/${sessionId}/director/pending/reject`, 'POST', {}).then((r) => attachCommandReceipt(r.messages.map(message => attachCommandReceipt(message, commandReceipt(r))), commandReceipt(r))),

  // -- M12-R41 辅助候选（手动触发） --
  generateCandidates: async (sessionId: string): Promise<AssistBatch> =>
    jfetch<AssistBatch>(`/api/v1/sessions/${sessionId}/assist/candidates`, { method: 'POST' }),

  draftAssist: async (sessionId: string, request: string | WritingRequest): Promise<WritingBatch> =>
    jfetch<WritingBatch>(`/api/v1/sessions/${sessionId}/assist/draft`, {
      method: 'POST',
      body: JSON.stringify(typeof request === 'string' ? { intent: request } : request),
    }),

  patchSceneImage: (sessionId, sceneId, input) => storyCommand<Scene>(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/scenes/${encodeURIComponent(sceneId)}/image`, 'PATCH', { ...input }),

  listScenes: async (sessionId: string): Promise<{ active_scene_id: string | null; scenes: Scene[] }> =>
    jfetch<{ active_scene_id: string | null; scenes: Scene[] }>(
      `/api/v1/sessions/${sessionId}/scenes`,
    ),

  listMemories: async (characterId: string, sessionId?: string): Promise<MemoryRecord[]> =>
    jfetch<{ records: MemoryRecord[] }>(sessionId
      ? `/api/v1/sessions/${sessionId}/memory/records?character_id=${encodeURIComponent(characterId)}`
      : `/api/v1/characters/${characterId}/memories`).then((r) => r.records),

  updateMemory: async (characterId: string, recordId: string, patch: MemoryPatch, sessionId?: string): Promise<MemoryRecord> =>
    queryStoryCommand<MemoryRecord & MemoryRecordView>(`/api/v1/characters/${characterId}/memories/${recordId}`, 'PATCH', patch, { session_id: sessionId }),

  deleteMemory: async (characterId, recordId, sessionId, expectedRevision, expectedBranchRevision): Promise<{ ok: boolean }> => {
    return queryStoryCommand(`/api/v1/characters/${characterId}/memories/${recordId}`, 'DELETE', {}, { session_id: sessionId, expected_revision: expectedRevision, expected_branch_revision: expectedBranchRevision });
  },
  consolidateAsync: (sessionId, characterId, start, end): Promise<MemoryScheduledResult> =>
    queryStoryCommand(`/api/v1/sessions/${sessionId}/memory/consolidate`, 'POST', {}, { async_mode: true, character_id: characterId, turn_start: start, turn_end: end }),
  getMemoryJob: (sessionId, operationId): Promise<MemoryJobView> => jfetch(`/api/v1/sessions/${sessionId}/memory/jobs/${encodeURIComponent(operationId)}`),
  rememberEvent: async (sessionId, req): Promise<MemoryRecord[]> => {
    const result = await storyCommand<MemoryRecordsResult>(`/api/v1/sessions/${sessionId}/memory/records`, 'POST', req as unknown as Record<string, unknown>);
    if (!Array.isArray(result.records)) throw new Error("记忆响应不完整，请重新读取。");
    return attachCommandReceipt(result.records as MemoryRecord[], commandReceipt(result));
  },
  memoryWindows: async (sessionId: string, characterId: string): Promise<MemoryWindow[]> =>
    jfetch<{windows: MemoryWindow[]}>(`/api/v1/sessions/${sessionId}/memory/windows?character_id=${encodeURIComponent(characterId)}`).then((r) => r.windows),
  memorySources: async (sessionId: string, characterId: string, recordId: string): Promise<Message[]> =>
    jfetch<{messages: Message[]}>(`/api/v1/sessions/${sessionId}/memory/records/${recordId}/sources?character_id=${encodeURIComponent(characterId)}`).then((r) => r.messages),

  continueMessage: async (sessionId: string, messageId: string): Promise<Message> =>
    storyCommand<Message>(`/api/v1/sessions/${sessionId}/messages/${messageId}/continue`, 'POST', {}),

  /** R37 叙事设置；把 PATCH 响应归一为 Session 分片（调用方本地回填，免 loadSessions） */
  patchNarrative: async (sessionId: string, patch: {
    response_style_id?: string | null; response_style_overrides?: Record<string, string | null>;
    narrative_pov?: string; narrative_density?: string; short_input_padding?: boolean; reply_max_tokens?: number | null;
  }): Promise<Partial<Session>> => {
    const d = await storyCommand<Record<string, unknown>>(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, 'PATCH', { ...patch });
    return attachCommandReceipt({
      options_enabled: d.options_enabled as boolean | undefined,
      options_style: d.options_style as string | undefined,
      options_direct_send: d.options_direct_send as boolean | undefined,
      streaming_enabled: d.streaming_enabled as boolean | undefined,
      hygiene_enabled: d.hygiene_enabled as boolean | undefined,
      director_mode: d.director_mode as Session['director_mode'],
      narrative_pov: d.narrative_pov as Session['narrative_pov'],
        response_style_id: d.response_style_id as string | null,
        response_style_overrides: d.response_style_overrides as Record<string, string | null>,
      narrative_density: d.narrative_density as Session['narrative_density'],
      short_input_padding: d.short_input_padding as boolean | undefined,
      reply_max_tokens: d.reply_max_tokens == null ? null : Number(d.reply_max_tokens),
    }, commandReceipt(d));
  },

  // -- R48 全局设置（引擎/思考） --
  getSettings: (): Promise<Settings> => {
    return readSettings(() => jfetch<Settings>('/api/v1/settings'));
  },
  listModelProviders: async (model, options): Promise<ModelProvidersResult> =>
    jfetch<ModelProvidersResult>(`/api/v1/settings/providers?model=${encodeURIComponent(model)}`, options),
  listGatewayModels: async (profile, options): Promise<ModelCatalogResult> =>
    jfetch<ModelCatalogResult>(`/api/v1/settings/models?profile=${encodeURIComponent(profile)}`, options),
  patchSettings: async (patch): Promise<Settings> =>
    writeSettings(() => jfetch<Settings>(`/api/v1/settings`, { method: 'PATCH', body: JSON.stringify(patch) })),
  testSettingsConnection: async (): Promise<SettingsTestResult> =>
    jfetch<SettingsTestResult>('/api/v1/settings/test-connection', { method: 'POST' }),

  // -- 本地语音合成 --
  getTTSStatus: () => jfetch<TTSStatus>('/api/v1/tts/status'),
  enableTTS: () => jfetch<TTSStatus>('/api/v1/tts/enable', { method: 'POST' }),
  disableTTS: () => jfetch<TTSStatus>('/api/v1/tts/disable', { method: 'POST' }),
  listVoiceProfiles: () => jfetch('/api/v1/tts/voices'),
  uploadVoiceProfile: async (name, transcript, file) => {
    const form = new FormData();
    form.append('name', name);
    form.append('transcript', transcript);
    form.append('file', file);
    const response = await fetch('/api/v1/tts/voices', { method: 'POST', body: form });
    if (!response.ok) throw new Error(`${response.status}: ${(await response.text()).slice(0, 300)}`);
    return response.json() as Promise<VoiceProfile>;
  },
  setDefaultVoiceProfile: async (voiceId) => {
    await jfetch(`/api/v1/tts/voices/${encodeURIComponent(voiceId)}/default`, { method: 'POST' });
  },
  deleteVoiceProfile: async (voiceId) => {
    await jfetch(`/api/v1/tts/voices/${encodeURIComponent(voiceId)}`, { method: 'DELETE' });
  },
  synthesizeSpeech: async (input) => {
    const response = await fetch('/api/v1/tts/synthesize', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (!response.ok) throw new Error(`${response.status}: ${(await response.text()).slice(0, 300)}`);
    return URL.createObjectURL(await response.blob());
  },

  // -- 角色 --
  listCharacters: async (): Promise<CharacterView[]> => {
    const rows = await jfetch<Character[]>('/api/v1/characters');
    return rows.map(toView);
  },

  getCharacter: async (characterId: string): Promise<Character> =>
    jfetch<Character>(`/api/v1/characters/${characterId}`),

  createCharacter: async (card: Partial<CharacterCard> & { name: string }): Promise<CharacterView> => {
    const fd = new FormData();
    fd.append('file', new Blob([JSON.stringify(card)], { type: 'application/json' }), 'card.json');
    const res = await fetch('/api/v1/characters/import', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return toView((await res.json()) as Character);
  },

  updateCharacter: async (characterId: string, patch: CharacterPatch): Promise<CharacterView> => {
    const updated = await jfetch<Character>(`/api/v1/characters/${characterId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
    return toView(updated);
  },

  importBundle: async (file: File): Promise<BundleReport> => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/v1/bundle/import', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json() as Promise<BundleReport>;
  },

  // -- 场景预设 --
  listScenarios: async (options): Promise<ScenarioSummary[]> => jfetch<ScenarioSummary[]>('/api/v1/scenarios', { signal: options?.signal }),
  getScenario: async (scenarioId: string): Promise<ScenarioPackage> =>
    jfetch<ScenarioPackage>(`/api/v1/scenarios/${scenarioId}`),
  createScenario: async (input: ScenarioCreateInput): Promise<ScenarioPackage> =>
    jfetch<ScenarioPackage>('/api/v1/scenarios', { method: 'POST', body: JSON.stringify(input) }),
  importScenario: async (file: File): Promise<ScenarioPackage> => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/v1/scenarios/import', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json() as Promise<ScenarioPackage>;
  },
  deleteScenario: async (scenarioId: string): Promise<void> => {
    await jfetch(`/api/v1/scenarios/${scenarioId}`, { method: 'DELETE' });
  },
  startScenario: async (scenarioId: string, openingScene = ''): Promise<Session> => {
    const state = await storyCommand<Record<string, unknown>>(`/api/v1/scenarios/${scenarioId}/start`, 'POST', { opening_scene: openingScene });
    return attachCommandReceipt(mapSessionState(state), commandReceipt(state));
  },

  deleteCharacter: async (characterId: string): Promise<void> => {
    await jfetch<{ ok: boolean }>(`/api/v1/characters/${characterId}`, { method: 'DELETE' });
  },

  characterAction: async (
    characterId: string,
    action: CharacterAction,
    sessionId?: string,
  ): Promise<CharacterView> => {
    if (!sessionId) throw new Error('该操作需要会话上下文');
    const path = action === 'force' ? `/api/v1/sessions/${sessionId}/continue` : `/api/v1/sessions/${sessionId}/characters/${characterId}`;
    const payload = action === 'force' ? { action, character_id: characterId } : { action };
    const result = await storyCommand<Record<string, unknown>>(path, 'POST', payload, undefined, async value => {
      const snapshot = await api.getStoryView(sessionId);
      const character = snapshot.characters.find((c) => c.id === characterId);
      const updated = character ? toView(character) : toView(await jfetch<Character>(`/api/v1/characters/${characterId}`));
      return { ...value, character: updated };
    });
    return result.character as CharacterView;
  },

  // -- 世界书 --
  listLorebooks: async (): Promise<Lorebook[]> => jfetch<Lorebook[]>('/api/v1/lorebooks'),
  listLorebookAgentJobs: () => jfetch<import('../types').LorebookAgentJobSummary[]>('/api/v1/lorebook-agent-jobs'),

  createLorebookAgentJob: (input) => jfetch<import('../types').LorebookAgentJob>('/api/v1/lorebook-agent-jobs', {
    method: 'POST', body: JSON.stringify(input),
  }),
  getLorebookAgentJob: (jobId) => jfetch<import('../types').LorebookAgentJob>(`/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}`),
  getLorebookAgentSources: (jobId) => jfetch<import('../types').LorebookAgentSources>(`/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/sources`),
  cancelLorebookAgentJob: (jobId) => jfetch<import('../types').LorebookAgentJob>(`/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
  resumeLorebookAgentJob: (jobId) => jfetch<import('../types').LorebookAgentJob>(`/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' }),
  deleteLorebookAgentJob: async (jobId) => { await jfetch(`/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' }); },
  editLorebookAgentDraft: (jobId, draftId, input) => jfetch<import('../types').LorebookAgentDraft>(
    `/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}`,
    { method: 'PATCH', body: JSON.stringify(input) },
  ),
  simulateLorebookAgentDraft: (jobId, draftId) => jfetch<NonNullable<import('../types').LorebookAgentDraft['simulation']>>(
    `/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}/simulate`, { method: 'POST' },
  ),
  regenerateLorebookAgentDraft: (jobId, draftId) => jfetch<import('../types').LorebookAgentJob>(
    `/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}/regenerate`, { method: 'POST' },
  ),
  commitLorebookAgentDraft: (jobId, draftId, input) => jfetch<{ book: Lorebook; draft: import('../types').LorebookAgentDraft }>(
    `/api/v1/lorebook-agent-jobs/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}/commit`,
    { method: 'POST', body: JSON.stringify(input) },
  ),

  updateLorebook: async (bookId: string, patch: LorebookPatch): Promise<Lorebook> =>
    jfetch<Lorebook>(`/api/v1/lorebooks/${bookId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  createBlankLorebook: async (input): Promise<Lorebook> =>
    jfetch<Lorebook>('/api/v1/lorebooks/blank', {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  copyLorebook: async (bookId, input): Promise<Lorebook> =>
    jfetch<Lorebook>(`/api/v1/lorebooks/${bookId}/copy`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  deleteLorebook: async (bookId: string): Promise<void> => {
    await jfetch<{ ok: boolean }>(`/api/v1/lorebooks/${bookId}`, { method: 'DELETE' });
  },

  // -- 注入检查器 --
  getInspection: async (sessionId: string, characterId: string, turn: number): Promise<Inspection> =>
    jfetch<Inspection>(`/api/v1/sessions/${sessionId}/inspections/${characterId}/${turn}`),
  getGenerationInspection: (sessionId, messageId, generationId) =>
    jfetch<Inspection>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/generation-inspections/${encodeURIComponent(messageId)}/${encodeURIComponent(generationId)}`),
  getContextPreview: async (sessionId: string, characterId: string): Promise<Inspection> =>
    jfetch<Inspection>(`/api/v1/sessions/${sessionId}/context-preview/${characterId}`),
  listModelRequests: async (sessionId: string): Promise<ModelRequestSummary[]> =>
    jfetch<{ requests: ModelRequestSummary[] }>(`/api/v1/sessions/${sessionId}/model-requests`).then((r) => r.requests),
  getModelRequest: async (sessionId: string, requestId: string): Promise<ModelRequestRecord> =>
    jfetch<ModelRequestRecord>(`/api/v1/sessions/${sessionId}/model-requests/${requestId}`),

  // -- 存档 --
  listSaves: async (sessionId: string): Promise<Save[]> =>
    jfetch<Save[]>(`/api/v1/saves?session_id=${encodeURIComponent(sessionId)}`),

  createSave: async (sessionId: string, name = ''): Promise<Save> =>
    storyCommand<Save>(`/api/v1/sessions/${sessionId}/save?name=${encodeURIComponent(name)}`, 'POST', {}),

  restoreSave: async (saveId: string): Promise<{ session: Session; messages: Message[]; characters: Character[]; lorebooks: Lorebook[]; memory_restore_status?: 'complete' | 'unavailable' | null }> => {
    const state = await storyCommand<LegacyRestoreResult>(`/api/v1/saves/${saveId}/restore`, 'POST', {});
    return attachCommandReceipt({
      session: attachCommandReceipt(mapSessionState(state), commandReceipt(state)),
      messages: (state.messages ?? []) as Message[],
      characters: (state.characters ?? []) as Character[],
      lorebooks: (state.lorebooks ?? []) as Lorebook[],
      memory_restore_status: state.memory_restore_status,
    }, commandReceipt(state));
  },

  // -- 成本 --
  getCost: async (sessionId: string): Promise<CostReport> =>
    jfetch<CostReport>(`/api/v1/sessions/${sessionId}/cost`),

  // -- 会话选项配置（R27.4） --
  patchSessionOptions: async (sessionId, patch) => {
    await storyCommand(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, 'PATCH', { ...patch });
  },

  // -- 工坊：角色（R23/R31） --
  listCardSources: () => jfetch<CardSourceCapability[]>('/api/v1/card-sources'),
  searchCards: (input) => jfetch<CardSearchResponse>('/api/v1/card-searches', {
    method: 'POST', body: JSON.stringify(input),
  }),
  nextCardPage: (input) => jfetch<CardSearchPage>('/api/v1/card-searches/next', {
    method: 'POST', body: JSON.stringify(input),
  }),
  getDiscoveredCard: (sourceId, cardId) => jfetch<DiscoveredCardDetail>(
    `/api/v1/card-sources/${encodeURIComponent(sourceId)}/cards/${encodeURIComponent(cardId)}`,
  ),
  listCardInspirationJobs: () => jfetch<CardInspirationJobSummary[]>('/api/v1/card-inspiration-jobs'),
  createCardInspirationJob: (input) => jfetch<CardInspirationJob>('/api/v1/card-inspiration-jobs', {
    method: 'POST', body: JSON.stringify(input),
  }),
  getCardInspirationJob: (jobId) => jfetch<CardInspirationJob>(`/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}`),
  generateCardInspirationDrafts: (jobId, count = 1) => jfetch<CardInspirationJob>(
    `/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}/generate`,
    { method: 'POST', body: JSON.stringify({ count }) },
  ),
  updateCardInspirationBrief: (jobId, input) => jfetch<CardInspirationJob>(
    `/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}/brief`,
    { method: 'PATCH', body: JSON.stringify(input) },
  ),
  sendCardInspirationAgentTurn: (jobId, message) => jfetch<CardInspirationJob>(
    `/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}/agent/turn`,
    { method: 'POST', body: JSON.stringify({ message }) },
  ),
  editCardInspirationDraft: (jobId, draftId, input) => jfetch<CardInspirationDraft>(
    `/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}`,
    { method: 'PATCH', body: JSON.stringify(input) },
  ),
  commitCardInspirationDraft: (jobId, draftId, input) => jfetch(
    `/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}/commit`,
    { method: 'POST', body: JSON.stringify(input) },
  ),
  deleteCardInspirationJob: async (jobId) => {
    await jfetch(`/api/v1/card-inspiration-jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
  },
  generateCharacters: async (input) =>
    jfetch(`/api/v1/characters/generate`, {
      method: 'POST',
      body: JSON.stringify({
        requirement: input.requirement,
        detail: input.detail ?? '',
        reference_lorebook_ids: input.reference_lorebook_ids ?? [],
        count: input.count ?? 3,
      }),
    }),

  previewTurn: async (card, message) =>
    jfetch(`/api/v1/characters/preview-turn`, {
      method: 'POST',
      body: JSON.stringify({ card, message }),
    }),

  aiEditField: async (card, field, instruction) =>
    jfetch(`/api/v1/characters/ai-edit`, {
      method: 'POST',
      body: JSON.stringify({ card, field, instruction }),
    }),

  personaPreview: async (card) =>
    jfetch(`/api/v1/characters/persona-preview`, {
      method: 'POST',
      body: JSON.stringify({ card }),
    }),

  uploadAvatar: async (characterId, file, expectedRevision) => {
    const fd = new FormData();
    fd.append('file', file);
    if (expectedRevision != null) fd.append('expected_revision', String(expectedRevision));
    const res = await fetch(`/api/v1/characters/${encodeURIComponent(characterId)}/avatar`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json();
  },

  uploadFullBody: async (characterId, file, expectedRevision) => {
    const fd = new FormData(); fd.append('file', file);
    if (expectedRevision != null) fd.append('expected_revision', String(expectedRevision));
    const res = await fetch(`/api/v1/characters/${encodeURIComponent(characterId)}/full-body`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json();
  },

  // -- 工坊：世界书（R29） --
  generateLorebook: async (input) =>
    jfetch(`/api/v1/lorebooks/generate`, {
      method: 'POST',
      body: JSON.stringify({
        topic: input.topic,
        concepts: input.concepts ?? [],
        reference_lorebook_id: input.reference_lorebook_id ?? null,
        count: input.count ?? 8,
      }),
    }),

  extendLorebook: async (bookId, direction, count) =>
    jfetch(`/api/v1/lorebooks/generate/extend`, {
      method: 'POST',
      body: JSON.stringify({ book_id: bookId, direction, count: count ?? 4 }),
    }),

  createLorebook: async (book) =>
    jfetch(`/api/v1/lorebooks`, { method: 'POST', body: JSON.stringify({ book }) }),
};
