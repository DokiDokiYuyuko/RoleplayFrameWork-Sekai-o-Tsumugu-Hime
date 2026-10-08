import type { StoryDeleteResult, StoryRestoreResult, StoryPurgeResult, StoryPresetResult, MemoryScheduledResult, MemoryJobView } from './generated/story-contracts';
import type { StoryView, StoryPageOptions, SessionSetup, SessionSetupPatch } from '../features/stories/storyView';
import type { WritingRequest, WritingBatch } from './writing';
// API 接缝层的**显式契约**，实现见 client.ts。
//
// 上一版这里的类型来自 `typeof mockApi`（从某个实现的形状反推接口）。
// mocks/ 已删除——接口应该是显式声明的，而不是某个实现的副产品。

import type { ReadOptions } from './request';
import type {
  AssistBatch,
  BundleReport,
  Character,
  CharacterCard,
  CharacterPatch,
  CharacterView,
  GroupActor,
  GroupDraft,
  GroupDraftResult,
  GroupSourcesResult,
  CostReport,
  Inspection,
  Lorebook,
  LorebookEntry,
  MemoryRecord,
  MemoryPatch, RememberRequest, MemoryWindow,
  Message,
  PinnedFact,
  Save,
  ScenarioCreateInput,
  ScenarioPackage,
  ScenarioSummary,
  Scene,
  Session,
  Settings,
  SettingsPatch,
  TTSStatus,
  VoiceProfile,
  ModelCatalogResult,
  ModelProvidersResult,
  ModelRequestRecord,
  ModelRequestSummary,
  SimpleChat,
  SimpleChatSummary,
  StorySummary,
  WorldlinePage,
  BranchPoint,
  ForkResult,
  StoryEvent,
  World,
  WorldSummary,
  ArchiveInput,
  BiologyImportItem,
  CardSourceCapability,
  CardSearchResponse,
  CardSearchPage,
  DiscoveredCardDetail,
  CardInspirationJob,
  CardInspirationJobSummary,
  CardInspirationDraft,
  CardInspirationBrief,
} from '../types';

/** 会话内角色动作（mute/unmute 仅改状态；present/absent 插场景消息；force 立即发言） */
export type CharacterAction =
  | 'mute' | 'unmute' | 'force' | 'present' | 'absent'
  | 'interject' | 'uninterject' | 'followup' | 'unfollowup'; // R38 会话级主动性开关

export interface NewSessionInput {
  title: string;
  character_ids: string[];
  persona: string;
  player_character_id?: string | null;
  reply_max_tokens?: number | null;
  lorebook_ids?: string[];
  world_id?: string | null;
  opening_scene?: string;
  greeting_choices?: Record<string, number>;
}

export interface LorebookPatch {
  expected_revision?: number;
  name?: string;
  description?: string;
  tags?: string[];
  entries?: LorebookEntry[];
}

export interface Api {
  listSimpleChats(options?: ReadOptions): Promise<SimpleChatSummary[]>;
  createSimpleChat(): Promise<SimpleChat>;
  getSimpleChat(id: string, options?: ReadOptions): Promise<SimpleChat>;
  patchSimpleChat(id: string, patch: Partial<Pick<SimpleChat, 'title' | 'gateway' | 'model' | 'provider' | 'provider_allow_fallbacks' | 'system_prompt' | 'generation' | 'input_price_per_million' | 'output_price_per_million' | 'price_currency'>>): Promise<SimpleChat>;
  deleteSimpleChat(id: string): Promise<void>;
  sendSimpleChatMessage(id: string, content: string, clientMessageId: string): Promise<SimpleChat>;
  stopSimpleChatGeneration(id: string): Promise<{ stopping: boolean }>;
  editSimpleChatMessage(id: string, messageId: string, content: string, truncateAfter: boolean): Promise<SimpleChat>;
  deleteSimpleChatMessage(id: string, messageId: string, truncateAfter: boolean): Promise<SimpleChat>;
  regenerateSimpleChatMessage(id: string, messageId: string): Promise<SimpleChat>;
  switchSimpleChatVariant(id: string, messageId: string, activeVariant: number): Promise<SimpleChat>;
  listWorlds(options?: ReadOptions): Promise<WorldSummary[]>;
  listWorldOwners(options?: ReadOptions): Promise<Record<string, { world_id: string; world_title: string }>>;
  getWorld(worldId: string, options?: ReadOptions): Promise<World>;
  listWorldCovers(options?: ReadOptions): Promise<{ id: string; title: string; theme: string; theme_label: string; image_url: string }[]>;
  createWorld(input: { title: string; cover_id?: string | null; description?: string; core_brief?: string; manuscript_body?: string; manuscript_visibility?: 'public' | 'private' }): Promise<World>;
  patchWorld(worldId: string, input: { expected_revision: number; cover_id?: string | null; title?: string; description?: string; core_brief?: string; archived?: boolean; runtime_policy?: 'legacy_full' | 'raw' | 'compiled' }): Promise<World>;
  createArchive(worldId: string, expectedRevision: number, record: ArchiveInput): Promise<World>;
  patchArchive(worldId: string, recordId: string, expectedRevision: number, record: ArchiveInput): Promise<World>;
  importBiology(worldId: string, expectedRevision: number, sourceWorldId: string, items: BiologyImportItem[]): Promise<World>;
  deleteArchive(worldId: string, recordId: string, expectedRevision: number): Promise<World>;
  linkWorldLorebook(worldId: string, bookId: string, expectedRevision: number): Promise<World>;
  unlinkWorldLorebook(worldId: string, bookId: string, expectedRevision: number): Promise<World>;
  importWorld(payload: unknown): Promise<World>;
  saveScenarioWorld(scenarioId: string): Promise<World>;
  listStories(options?: ReadOptions): Promise<StorySummary[]>;
  deleteStory(storyId: string, membershipRevision?: string | null): Promise<StoryDeleteResult>;
  listStoryTrash(options?: ReadOptions): Promise<import('../types').TrashedStory[]>;
  restoreStory(storyId: string, generationId?: string | null): Promise<StoryRestoreResult>;
  deleteBranch(branchId: string, membershipRevision?: string | null): Promise<StoryDeleteResult>;
  purgeStory(storyId: string, generationId?: string | null): Promise<StoryPurgeResult>;
  getStoryBackupStatus(options?: ReadOptions): Promise<{ errors: Record<string, string> }>;
  listPromptPresets(): Promise<import('../types').PromptPreset[]>;
  savePromptPreset(preset: import('../types').PromptPreset): Promise<import('../types').PromptPreset>;
  deletePromptPreset(id: string): Promise<void>;
  previewPromptPresetImport(file: File): Promise<import('../types').PromptImportPreview>;
  listStoryBookmarks(storyId: string): Promise<import('../types').StoryBookmark[]>;
  createBookmark(branchId: string, messageId: string, title?: string): Promise<import('../types').StoryBookmark>;
  deleteBookmark(branchId: string, bookmarkId: string): Promise<void>;
  patchBookmark(branchId: string, bookmarkId: string, patch: { title?: string; tags?: string[] }): Promise<import('../types').StoryBookmark>;
  searchStory(storyId: string, query: string, options?: { branch_id?: string; actor?: string; scene_id?: string; bookmarked?: boolean; event?: boolean; cursor?: number }): Promise<{ results: import('../types').StorySearchResult[]; next_cursor: number | null }>;
  setStoryPromptPreset(storyId: string, presetId: string | null, membershipRevision?: string | null): Promise<StoryPresetResult>;
  importStory(file: File): Promise<{ story_id: string; branches: { old_id: string; new_id: string }[]; saves: { old_id: string; new_id: string }[]; memory_records: number; missing_assets: string[]; credential_fields_redacted: number; note: string }>;
  getWorldline(storyId: string, cursor?: number, query?: string): Promise<WorldlinePage>;
  getBranchPoint(branchId: string, messageId: string): Promise<BranchPoint>;
  forkBranch(branchId: string, input: { message_id: string; title: string; expected_revision: number; idempotency_key: string }): Promise<ForkResult>;
  forkSave(saveId: string, title: string, idempotencyKey: string): Promise<ForkResult>;
  patchBranch(branchId: string, input: { name?: string; archived?: boolean; expected_revision: number }): Promise<{ id: string; name: string; archived: boolean; branch_revision: number }>;
  createStoryEvent(branchId: string, input: { anchor_message_id: string; title: string; summary?: string; kind?: StoryEvent['kind']; visible_to?: import('../types').Visibility }): Promise<StoryEvent>;
  updateStoryEvent(branchId: string, eventId: string, input: Partial<Pick<StoryEvent, 'title' | 'summary' | 'kind' | 'visible_to'>>): Promise<StoryEvent>;
  deleteStoryEvent(branchId: string, eventId: string): Promise<void>;
  // -- 会话 --
  listSessions(): Promise<import("../types").SessionSummary[]>;
  createSession(input: NewSessionInput): Promise<Session>;
  getSessionSnapshot(sessionId: string, options?: ReadOptions): Promise<{ session: Session; messages: Message[]; characters: Character[]; lorebooks: Lorebook[]; groups: GroupActor[]; scenes: Scene[]; active_scene_id: string | null; event_cursor: string | null; conversation_runs?: import('../types').ConversationRun[]; turn_runs?: import('../types').TurnRun[]; pending_director?: import('../types').DirectorDecision | null }>;
  getStoryView(sessionId: string, options?: StoryPageOptions): Promise<StoryView>;
  getSessionSetup(sessionId: string, options?: ReadOptions): Promise<SessionSetup>;
  patchSessionSetup(sessionId: string, patch: SessionSetupPatch): Promise<Partial<Session>>;
  listMessages(sessionId: string): Promise<Message[]>;
  switchPlayer(sessionId: string, input: import('../types').SwitchPlayerInput): ReturnType<Api['getSessionSnapshot']>;
  addStoryParticipant(sessionId: string, input: { character_id: string; join_current_scene?: boolean; entry_brief?: string }): Promise<{ added: boolean }>;
  listGroups(sessionId: string): Promise<GroupActor[]>;
  listGroupSources(sessionId: string): Promise<GroupSourcesResult>;
  draftGroup(sessionId: string, input: { archive_id: string; archive_revision: number }): Promise<GroupDraftResult>;
  createGroup(sessionId: string, input: GroupDraft & {
    expected_scene_id: string; expected_branch_revision: number; idempotency_key: string;
    source_archive_id?: string | null; source_archive_revision?: number | null; source_snapshot?: string;
  }): Promise<GroupActor>;
  patchGroup(sessionId: string, groupId: string, input: Partial<GroupDraft> & { expected_branch_revision: number }): Promise<GroupActor>;
  leaveGroup(sessionId: string, groupId: string, expectedBranchRevision: number): Promise<GroupActor>;
  replyGroup(sessionId: string, groupId: string, idempotencyKey: string): Promise<Message>;
  swipeGroup(sessionId: string, groupId: string, messageId: string): Promise<Message>;
  sendMessage(sessionId: string, content: string, mentions?: string[], channel?: string, clientMessageId?: string, replyMode?: import('../types').ReplyMode, expectedPlayerIdentity?: string | null, maxReplies?: number, conversationDirective?: string): Promise<{ messages: Message[]; errors: string[]; conversation_run?: import('../types').ConversationRun; turn_run?: import('../types').TurnRun }>;
  controlTurn(sessionId: string, operationId: string, action: 'resume' | 'stop', input: {
    expected_branch_revision?: number; expected_player_identity_id?: string | null;
  }): Promise<{ messages: Message[]; errors: string[]; turn_run: import('../types').TurnRun }>;
  startConversation(sessionId: string, input: import('../types').ConversationStartInput): Promise<import('../types').ConversationRun>;
  pauseConversation(sessionId: string, runId: string): Promise<import('../types').ConversationRun>;
  stopConversation(sessionId: string, runId: string): Promise<import('../types').ConversationRun>;
  resumeConversation(sessionId: string, runId: string, input: import('../types').ConversationResumeInput): Promise<import('../types').ConversationRun>;
  regenerateOne(sessionId: string, messageId: string, input: import('../types').MessageOperationInput): Promise<import('../types').MessageOperationResult>;
  regenerateDependents(sessionId: string, messageId: string, input: import('../types').MessageOperationInput): Promise<import('../types').MessageOperationResult>;
  acceptDependencies(sessionId: string, messageId: string, input: import('../types').MessageOperationInput): Promise<import('../types').MessageOperationResult>;
  swipeMessage(sessionId: string, messageId: string): Promise<Message>;
  /** R45 重跑最后一轮（编辑"我的消息"后基于新文本重新生成；旧回复被删除） */
  regenerateMessage(sessionId: string, messageId: string): Promise<{ messages: Message[]; errors: string[] }>;
  /** R32.2 编辑消息内容 */
  editMessage(sessionId: string, messageId: string, content: string, expectedBranchRevision?: number, expectedFingerprint?: string): Promise<Message>;
  editInputGroup(sessionId: string, messageId: string, input: {
    expected_branch_revision: number;
    parts: import('../types').InputGroupEditPart[];
  }): Promise<{ messages: Message[]; branch_revision: number }>;
  /** R32.1 候选切换 */
  switchVariant(sessionId: string, messageId: string, activeVariant: number, input?: import('../types').MessageOperationInput): Promise<Message>;
  /** R32.3 真删除 */
  deleteMessage(sessionId: string, messageId: string, expectedBranchRevision?: number): Promise<{ ok: boolean } | void>;
  /** R34 对当前内容重跑输出卫生校验 */
  recheckHygiene(sessionId: string, messageId: string): Promise<Message>;
  // -- 本局固定信息 --
  createPinnedFact(sessionId: string, input: { content: string; message_id?: string; visible_to?: import('../types').Visibility }): Promise<PinnedFact>;
  updatePinnedFact(sessionId: string, factId: string, content: string): Promise<PinnedFact>;
  deletePinnedFact(sessionId: string, factId: string): Promise<{ ok: boolean } | void>;

  // -- F10.3 迁移包 --
  /** 整包导入（zip：角色卡/头像/世界书；返回逐项报告） */
  importBundle(file: File): Promise<BundleReport>;

  // -- 场景预设 --
  listScenarios(options?: ReadOptions): Promise<ScenarioSummary[]>;
  getScenario(scenarioId: string): Promise<ScenarioPackage>;
  createScenario(input: ScenarioCreateInput): Promise<ScenarioPackage>;
  importScenario(file: File): Promise<ScenarioPackage>;
  deleteScenario(scenarioId: string): Promise<void>;
  startScenario(scenarioId: string, openingScene?: string): Promise<Session>;

  // -- R35 场景演化 --
  /** 玩家手动切场景 */
  sceneSwitch(
    sessionId: string,
    input: { title: string; description?: string; member_ids?: string[]; first_speaker_ids?: string[] },
  ): Promise<Message[]>;
  /** 确认档：执行/否决挂起的 LLM 导演决策 */
  confirmDirector(sessionId: string): Promise<Message[]>;
  rejectDirector(sessionId: string): Promise<Message[]>;

  // -- M12-R41 辅助候选（手动触发） --
  /** 生成一批候选（点击触发；冷启动点→开场/新场景，否则→回合接话；失败静默） */
  generateCandidates(sessionId: string): Promise<AssistBatch>;
  /** R40 意图代笔：把一小句意思扩写成 3 条完整候选（仅预填；空/超长意图 400） */
  draftAssist(sessionId: string, request: string | WritingRequest): Promise<WritingBatch>;
  /** R35 场景列表（含当前场景 id；导演与场景面板用） */
  patchSceneImage(sessionId: string, sceneId: string, input: { builtin_image_id: string | null; expected_branch_revision: number }): Promise<Scene>;
  listScenes(sessionId: string): Promise<{ active_scene_id: string | null; scenes: Scene[] }>;

  // -- R36 记忆查看器 --
  listMemories(characterId: string, sessionId?: string): Promise<MemoryRecord[]>;
  updateMemory(characterId: string, recordId: string, patch: MemoryPatch, sessionId?: string): Promise<MemoryRecord>;
  deleteMemory(characterId: string, recordId: string, sessionId?: string, expectedRevision?: number, expectedBranchRevision?: number): Promise<{ ok: boolean }>;
  consolidateAsync(sessionId: string, characterId?: string, start?: number, end?: number): Promise<MemoryScheduledResult>
  getMemoryJob(sessionId: string, operationId: string): Promise<MemoryJobView>;
  rememberEvent(sessionId: string, req: RememberRequest): Promise<MemoryRecord[]>;
  memoryWindows(sessionId: string, characterId: string): Promise<MemoryWindow[]>;
  memorySources(sessionId: string, characterId: string, recordId: string): Promise<Message[]>;

  // -- R37 叙事控制 --
  /** R37.3 续写最后一条角色消息（拼接语义，variants 可切回） */
  continueMessage(sessionId: string, messageId: string): Promise<Message>;
  /** R37 会话叙事设置（视角/密度/垫场）；返回后端权威配置（本地回填，免整表刷新） */
  patchNarrative(
    sessionId: string,
    patch: { response_style_id?: string | null; response_style_overrides?: Record<string, string | null>; narrative_pov?: string; narrative_density?: string; short_input_padding?: boolean; reply_max_tokens?: number | null },
  ): Promise<Partial<Session>>;

  // -- R48 全局设置 --
  /** 全量读回（前端以此为准渲染，防"改了不显示"） */
  getSettings(): Promise<Settings>;
  listModelProviders(model: string, options?: ReadOptions): Promise<ModelProvidersResult>;
  listGatewayModels(profile: string, options?: ReadOptions): Promise<ModelCatalogResult>;
  /** 修改全局推理设置；API Key 只写入、不读回。 */
  patchSettings(patch: SettingsPatch): Promise<Settings>;
  /** 使用当前已保存的网关/模型/密钥发起一次轻量验证。 */
  testSettingsConnection(): Promise<import('../types').SettingsTestResult>;

  // -- 本地语音合成 --
  getTTSStatus(): Promise<TTSStatus>;
  enableTTS(): Promise<TTSStatus>;
  disableTTS(): Promise<TTSStatus>;
  listVoiceProfiles(): Promise<{ voices: VoiceProfile[]; default_voice_profile_id: string | null }>;
  uploadVoiceProfile(name: string, transcript: string, file: File): Promise<VoiceProfile>;
  setDefaultVoiceProfile(voiceId: string): Promise<void>;
  deleteVoiceProfile(voiceId: string): Promise<void>;
  synthesizeSpeech(input: { text: string; voice_profile_id?: string; emotion?: string }): Promise<string>;

  // -- 角色 --
  listCharacters(): Promise<CharacterView[]>;
  /** 从卡 JSON 新建/导入角色（走 /characters/import 的文件上传入口） */
  createCharacter(card: Partial<CharacterCard> & { name: string }): Promise<CharacterView>;
  updateCharacter(characterId: string, patch: CharacterPatch): Promise<CharacterView>;
  /** R50：删除角色（库级；卡片 + 头像 + 该角色记忆；会话/存档内快照不受影响） */
  deleteCharacter(characterId: string): Promise<void>;
  characterAction(
    characterId: string,
    action: CharacterAction,
    sessionId?: string,
  ): Promise<CharacterView>;
  /** 后端返回契约 Character；调用方按需包一层色系（见 adapters.toView） */
  getCharacter(characterId: string): Promise<Character>;

  // -- 世界书 --
  listLorebooks(): Promise<Lorebook[]>;
  listLorebookAgentJobs(): Promise<import('../types').LorebookAgentJobSummary[]>;
  createLorebookAgentJob(input: { world_id: string; source_ids: string[]; include_core_brief: boolean; target_lorebook_id?: string | null; new_lorebook_name?: string; goal?: string }): Promise<import('../types').LorebookAgentJob>;
  getLorebookAgentJob(jobId: string): Promise<import('../types').LorebookAgentJob>;
  getLorebookAgentSources(jobId: string): Promise<import('../types').LorebookAgentSources>;
  cancelLorebookAgentJob(jobId: string): Promise<import('../types').LorebookAgentJob>;
  resumeLorebookAgentJob(jobId: string): Promise<import('../types').LorebookAgentJob>;
  deleteLorebookAgentJob(jobId: string): Promise<void>;
  editLorebookAgentDraft(jobId: string, draftId: string, input: { expected_revision: number; payload: Partial<LorebookEntry>; source_refs?: { source_id: string; quote: string }[]; positive_examples?: string[]; negative_examples?: string[]; rationale?: string; risk_notes?: string[] }): Promise<import('../types').LorebookAgentDraft>;
  simulateLorebookAgentDraft(jobId: string, draftId: string): Promise<NonNullable<import('../types').LorebookAgentDraft['simulation']>>;
  regenerateLorebookAgentDraft(jobId: string, draftId: string): Promise<import('../types').LorebookAgentJob>;
  commitLorebookAgentDraft(jobId: string, draftId: string, input: { expected_revision: number; accept_source_changes?: boolean }): Promise<{ book: Lorebook; draft: import('../types').LorebookAgentDraft }>;
  updateLorebook(bookId: string, patch: LorebookPatch): Promise<Lorebook>;
  createBlankLorebook(input: { name: string; description?: string; tags?: string[] }): Promise<Lorebook>;
  copyLorebook(bookId: string, input: { name: string; description?: string; tags?: string[] }): Promise<Lorebook>;
  deleteLorebook(bookId: string): Promise<void>;

  // -- 注入检查器 --
  getInspection(sessionId: string, characterId: string, turn: number): Promise<Inspection>;
  getGenerationInspection(sessionId: string, messageId: string, generationId: string): Promise<Inspection>;
  getContextPreview(sessionId: string, characterId: string): Promise<Inspection>;
  listModelRequests(sessionId: string): Promise<ModelRequestSummary[]>;
  getModelRequest(sessionId: string, requestId: string): Promise<ModelRequestRecord>;

  // -- 存档 --
  listSaves(sessionId: string): Promise<Save[]>;
  createSave(sessionId: string, name?: string): Promise<Save>;
  restoreSave(saveId: string): Promise<{ session: Session; messages: Message[]; characters: Character[]; lorebooks: Lorebook[]; memory_restore_status?: 'complete' | 'unavailable' | null }>;

  // -- 成本 --
  getCost(sessionId: string): Promise<CostReport>;

  // -- 会话选项配置（R27.4） --
  patchSessionOptions(
    sessionId: string,
    patch: { options_enabled?: boolean; options_style?: string; options_direct_send?: boolean },
  ): Promise<void>;

  // -- 工坊：角色（R23/R31） --
  generateCharacters(input: {
    requirement: string;
    detail?: string;
    reference_lorebook_ids?: string[];
    count?: number;
  }): Promise<{ card: CharacterCard; aliases: string[] }[]>;
  listCardSources(): Promise<CardSourceCapability[]>;
  searchCards(input: { query: string; source_ids: string[]; limit?: number }): Promise<CardSearchResponse>;
  nextCardPage(input: { source_id: string; query: string; cursor: string; limit?: number }): Promise<CardSearchPage>;
  getDiscoveredCard(sourceId: string, cardId: string): Promise<DiscoveredCardDetail>;
  listCardInspirationJobs(): Promise<CardInspirationJobSummary[]>;
  createCardInspirationJob(input: { search_query?: string; requirement: string; detail?: string; borrow?: string; avoid?: string; references: { source_id: string; card_id: string }[] }): Promise<CardInspirationJob>;
  getCardInspirationJob(jobId: string): Promise<CardInspirationJob>;
  generateCardInspirationDrafts(jobId: string, count?: number): Promise<CardInspirationJob>;
  updateCardInspirationBrief(jobId: string, input: CardInspirationBrief & { expected_revision: number }): Promise<CardInspirationJob>;
  sendCardInspirationAgentTurn(jobId: string, message: string): Promise<CardInspirationJob>;
  editCardInspirationDraft(jobId: string, draftId: string, input: { expected_revision: number; payload: CharacterCard; aliases: string[]; source?: 'user' | 'agent' }): Promise<CardInspirationDraft>;
  commitCardInspirationDraft(jobId: string, draftId: string, input: { expected_revision: number; payload: CharacterCard; aliases: string[] }): Promise<{ character: Character; draft: CardInspirationDraft }>;
  deleteCardInspirationJob(jobId: string): Promise<void>;
  previewTurn(card: CharacterCard, message: string): Promise<{ reply: string }>;
  aiEditField(card: CharacterCard, field: string, instruction: string): Promise<{ value: string }>;
  personaPreview(card: CharacterCard): Promise<{ persona: string; tokens: number }>;
  uploadAvatar(characterId: string, file: File, expectedRevision?: number): Promise<{ avatar_path: string | null; revision: number }>;
  uploadFullBody(characterId: string, file: File, expectedRevision?: number): Promise<{ avatar_path: string | null; revision: number }>;

  // -- 工坊：世界书（R29） --
  generateLorebook(input: {
    topic: string;
    concepts?: string[];
    reference_lorebook_id?: string;
    count?: number;
  }): Promise<Lorebook>;
  extendLorebook(bookId: string, direction: string, count?: number): Promise<LorebookEntry[]>;
  createLorebook(book: Lorebook): Promise<Lorebook>;
}

/** 导出下载链接（浏览器直接打开即下载） */
export const exportCharacterUrl = (characterId: string, format: 'json' | 'png') =>
  `/api/v1/characters/${characterId}/export?format=${format}`;

/** F10.3 迁移包导出（整库：角色/头像/世界书）——浏览器直接打开即下载 */
export const exportBundleUrl = '/api/v1/bundle/export';
export const exportStoryUrl = (storyId: string, includeMemory = true) => `/api/v1/stories/${encodeURIComponent(storyId)}/export?include_memory=${includeMemory}`;
export const exportLorebookUrl = (bookId: string) => `/api/v1/lorebooks/${bookId}/export`;
