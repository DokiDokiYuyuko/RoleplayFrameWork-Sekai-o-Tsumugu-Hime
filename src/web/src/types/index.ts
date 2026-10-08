import { STORY_EVENT_TYPES } from '../api/generated/story-contracts';
// ===== 前端契约：逐一对应后端 src/mrp/shared/models.py =====
//
// 规则（吃过一次亏）：**字段名与后端一致**——uid 就是 uid，anchor 就是
// system/at_depth/near，retracted 就是 retracted。不做改名映射。
// 上一版这里是一套与后端并行演化的影子模型（mocks/mockApi），已经漂移出
// uid↔id、anchor 命名、generation_meta 形状、temperature/max_tokens 静默丢弃
// 等一系列缺陷。纯 UI 派生的东西（色系等）走 view 类型，不污染契约。

export type ActorId = string; // 'player' | character.id
export type SchemaVersion = 1 | 2 | 3;

/** 可见性两档（R3.1）：'all' 或 actor id 列表 */
export type Visibility = ActorId[] | 'all';

/** 后端 Message.kind */
export type MessageKind = 'roleplay' | 'scene' | 'ooc' | 'system_event' | 'inner';
/** 后端 Message.status */
export type MessageStatus = 'pending' | 'final' | 'retracted';

/** ST position 0..7 归并后的三档插入锚 */
export type InsertAnchor = 'system' | 'at_depth' | 'near';

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}

export type ReplyMode = 'auto' | 'parallel' | 'serial' | 'free';
export interface MessageSourceRef {
  message_id: string;
  variant_id: string | null;
  fingerprint: string;
}
export interface MessageOperationInput {
  operation_id: string;
  expected_branch_revision: number;
  expected_fingerprint: string;
  expected_player_identity_id?: string | null;
}
export interface MessageOperationResult {
  messages: Message[];
  branch_revision: number;
  affected_message_ids: string[];
  operation_id: string;
}
export interface GenerationEventIdentity {
  generation_id?: string | null;
  operation_id?: string | null;
  attempt_id?: string | number | null;
}
export interface ConversationStep {
  id: string;
  index: number;
  generation_id: string;
  speaker_id: string;
  message_id: string | null;
  status: 'committed' | 'failed' | 'cancelled';
  trigger_message_ids: string[];
  reply_to_message_ids: string[];
  usage: TokenUsage;
  error: string | null;
}
export interface ConversationRun {
  id: string;
  session_id?: string;
  operation_id: string;
  mode: 'free' | 'observe';
  participant_ids: string[];
  scene_id: string;
  player_identity_id: string | null;
  directive: string;
  max_replies: number;
  completed_replies: number;
  status: 'queued' | 'running' | 'paused' | 'awaiting_user' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
  stage: 'selecting' | 'generating' | 'committing' | 'boundary';
  current_speaker_id: string | null;
  current_message_id?: string | null;
  current_generation_id?: string | null;
  current_attempt_id?: string | null;
  stop_reason: string;
  last_error: string | null;
  epoch: number;
  pause_requested: boolean;
  stop_requested: boolean;
  last_committed_step_id: string | null;
  last_committed_message_id: string | null;
  cumulative_usage: TokenUsage;
  usage_incomplete?: boolean;
  /** Light story views omit execution history; the run detail endpoint supplies it. */
  steps?: ConversationStep[];
  created_at: string;
  updated_at: string;
}
export interface ConversationStartInput {
  operation_id: string;
  participant_ids: string[];
  max_replies: number;
  directive: string;
  expected_branch_revision: number;
  expected_player_identity_id: string | null;
}
export interface ConversationResumeInput {
  expected_branch_revision: number;
  expected_player_identity_id: string | null;
  additional_replies?: number;
}

export interface GenerationMeta extends GenerationEventIdentity {
  finish_reason?: string | null;
  completion_state?: "complete" | "truncated" | "filtered" | "unknown";
  message_id?: string | null;
  request_ids?: string[];
  provenance?: {
    sources: MessageSourceRef[];
    trigger_message_ids: string[];
    reply_to_message_ids: string[];
    mode: string;
    participants: string[];
    [key: string]: unknown;
  } | null;
  engine_session_id: string;
  model: string;
  usage: TokenUsage;
  /** 世界书+记忆条目溯源 */
  injected_entry_ids: string[];
  /** 分节 token 统计（键：lorebook/memory/history/instructions/near） */
  prompt_tokens_by_section: Record<string, number>;
  memory_recall?: { memory_id: string; category: string; source_message_ids: string[];
    turn_start: number; turn_end: number; important: boolean;
    disposition: 'candidate' | 'injected' | 'omitted'; reason: string }[];
}

// ---------- 消息 ----------

/** R34 输出卫生：一次校验违规（类别 + 证据句） */
export interface Violation {
  category: 'C1' | 'C2' | 'C3' | 'C4';
  evidence: string;
}

/** R34：一条（候选）回复的校验报告 */
export interface HygieneReport {
  passed: boolean;
  attempts: number;
  corrected: boolean;
  violations: Violation[];
  usage: TokenUsage;
}

/** R32.1：一次生成候选的完整快照（swipe 历史；不参与 prompt 组装） */
export interface MessageVariant extends GenerationEventIdentity {
  accepted_dependency_sources?: MessageSourceRef[];
  id: string;
  content: string;
  generation_meta: GenerationMeta | null;
  hygiene: HygieneReport | null;
  created_at: string;
}

export interface Message extends GenerationEventIdentity {
  dependency_stale?: boolean;
  dependency_stale_sources?: string[];
  accepted_dependency_sources?: MessageSourceRef[];
  player_identity_id?: string | null;
  person_id?: string | null;
  known_to?: string[] | null;
  control_event?: boolean;
  id: string;
  session_id: string;
  seq: number;
  turn: number;
  actor: ActorId;
  content: string;
  kind: MessageKind;
  visible_to: Visibility;
  status: MessageStatus;
  fingerprint: string;
  generation_meta: GenerationMeta | null;
  created_at: string;
  /** R32（additive；后端 pydantic 默认值保证旧存档兼容） */
  variants: MessageVariant[];
  active_variant: number | null;
  edited: boolean;
  hygiene: HygieneReport | null;
  /** R35 场景归属（additive；v1 存档迁移后回填） */
  scene_id: string | null;
  /** R45：显式 @ 路由随消息持久化（重跑最后一轮时复用） */
  mentions: string[];
  /** All player segments parsed from one submission share this id; absent on legacy saves. */
  input_group_id?: string | null;
  reply_mode?: ReplyMode | null;
  executed_reply_mode?: 'parallel' | 'serial' | 'free' | null;
  reply_order?: string[];
  reply_reason?: string;
  reply_basis_fingerprint?: string;
  reply_shared_scene?: string;
  /** 群体立即回应请求的幂等键；常规消息为空 */
  idempotency_key?: string | null;
  /** v3：该消息之后生效的剧情状态修订；旧历史可能为空。 */
  post_state_revision_id?: string | null;
}

export interface StateRevision {
  id: string;
  after_message_id: string | null;
  snapshot: Record<string, unknown>;
  content_hash: string;
  memory_watermark: number | null;
  source: 'legacy_head' | 'head_only' | 'runtime';
  created_at: string;
}

export interface StoryEvent {
  id: string;
  anchor_message_id: string;
  title: string;
  summary: string;
  kind: 'turning_point' | 'clue' | 'relationship' | 'scene' | 'note';
  created_by: 'player' | 'director';
  visible_to: Visibility;
  created_at: string;
}

export interface StoryBookmark {
  id: string;
  story_id: string;
  branch_id: string;
  message_id: string;
  title: string;
  tags: string[];
  source_anchor: string | null;
  created_at: string;
  valid?: boolean;
  branch_name?: string;
}

export interface StorySearchResult {
  story_id: string;
  branch_id: string;
  branch_name: string;
  message_id: string;
  actor: string;
  scene_id: string | null;
  excerpt: string;
  bookmarked: number;
  event: number;
}

/** 本局玩家固定的剧情事实；每轮注入当前角色可见的条目。 */
export interface PinnedFact {
  id: string;
  content: string;
  source_message_id: string | null;
  source_actor: ActorId | null;
  visible_to: Visibility;
  created_at: string;
}

// ---------- 角色 ----------

export interface CharacterCard {
  import_report?: { converted: string[]; partial: string[]; unsupported: string[] };
  spec: string;
  spec_version: string;
  name: string;
  description: string;
  appearance: string;
  /** Editable heading and content for a reusable core-traits field. */
  traits_label: string;
  traits: string;
  personality: string;
  scenario: string;
  first_mes: string;
  mes_example: string;
  alternate_greetings: string[];
  system_prompt: string | null;
  post_history_instructions: string | null;
  creator_notes: string;
  creator: string;
  character_version: string;
  tags: string[];
  extensions: Record<string, unknown>;
  avatar_path: string | null;
  /** ccv1 / ccv2 / ccv3 / charx */
  source_format: string;
}

// ---------- 外站角色卡搜索与灵感任务 ----------

export interface CardSourceCapability {
  source_id: string;
  label: string;
  summary: string;
  homepage: string;
  filters: string[];
  paging: 'offset' | 'page';
  full_card: boolean;
}

export interface DiscoveredCard {
  source_id: string;
  card_id: string;
  title: string;
  creator: string;
  summary: string;
  tags: string[];
  source_url: string;
  published_at: string | null;
  content_rating: 'sfw' | 'sensitive' | 'unknown';
  raw_metrics: Record<string, number | string | null>;
}

export interface CardSearchPage {
  source_id: string;
  status: 'ok' | 'error';
  results: DiscoveredCard[];
  next_cursor: string | null;
  error: string | null;
  skipped_count: number;
}

export interface CardSearchResponse {
  query: string;
  sources: CardSearchPage[];
}

export interface DiscoveredCardDetail {
  hit: DiscoveredCard;
  card: CharacterCard;
  fetched_at: string;
  content_sha256: string;
}

export interface CardInspirationReference {
  source_id: string;
  card_id: string;
  title: string;
  creator: string;
  source_url: string;
  tags: string[];
  content_rating: 'sfw' | 'sensitive' | 'unknown';
  fetched_at: string;
  content_sha256: string;
}

export interface CardInspirationDraft {
  id: string;
  revision: number;
  status: 'review' | 'committing' | 'committed';
  payload: CharacterCard;
  aliases: string[];
  field_history: CardInspirationFieldHistoryEntry[];
  quality_review?: CardInspirationQualityReview;
  committed_character_id: string | null;
}

export interface CardInspirationFieldHistoryEntry {
  field: 'name' | 'description' | 'appearance' | 'traits_label' | 'traits' | 'personality' | 'scenario' | 'first_mes' | 'mes_example';
  from_revision: number;
  to_revision: number;
  changed_at: string;
  source: 'user' | 'agent';
  previous_value: string | null;
  previous_value_preview: string;
  previous_value_truncated: boolean;
}

export interface CardInspirationQualityReview {
  passed: boolean;
  issues: string[];
  note: string;
}

export interface CardInspirationBrief {
  requirement: string;
  detail: string;
  borrow: string;
  avoid: string;
}

export interface CardInspirationBriefSuggestion {
  requirement?: string | null;
  detail?: string | null;
  borrow?: string | null;
  avoid?: string | null;
}

export interface CardInspirationMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  brief_suggestion?: CardInspirationBriefSuggestion | null;
  field_suggestions?: CardInspirationFieldSuggestion[];
}

export interface CardInspirationFieldSuggestion {
  field: 'description' | 'appearance' | 'traits' | 'personality' | 'scenario' | 'first_mes' | 'mes_example';
  value: string;
  rationale: string;
}

export interface CardInspirationJob {
  id: string;
  search_query: string;
  requirement: string;
  detail: string;
  borrow: string;
  avoid: string;
  status: 'ready' | 'queued' | 'generating' | 'agent_running' | 'review' | 'failed' | 'interrupted' | 'committed' | 'cancelled';
  stage: string;
  drafts: CardInspirationDraft[];
  errors: string[];
  attempt: number;
  brief_revision: number;
  agent_enabled: boolean;
  agent_messages: CardInspirationMessage[];
  agent_suggestion?: CardInspirationBriefSuggestion | null;
  agent_field_suggestions?: CardInspirationFieldSuggestion[];
  created_at: string;
  updated_at: string;
  references: CardInspirationReference[];
}

export interface CardInspirationJobSummary {
  id: string;
  requirement: string;
  status: CardInspirationJob['status'];
  created_at: string;
  updated_at: string;
}

/** 每角色独立模型配置（R1.3） */
export interface ModelConfig {
  inherit_model?: boolean | null;
  inherit_base_url?: boolean | null;
  effective_provider?: string;
  provider: string;
  model: string;
  base_url: string;
  /** 只存环境变量名，绝不存密钥 */
  api_key_env: string;
  /** max_tokens / reasoning_effort 等 */
  sampling: Record<string, unknown>;
}

export interface Character {
  id: string;
  schema_version?: number;
  revision?: number;
  source_asset_id?: string | null;
  source_asset_revision?: number | null;
  source_world_id?: string | null;
  authoring_source?: { text: string; captured_at: string; import_job_id: string | null } | null;
  created_at?: string;
  updated_at?: string;
  card: CharacterCard;
  aliases: string[];
  llm: ModelConfig;
  /** 0=Shy ~ 1=Chatty */
  talkativeness: number;
  muted: boolean;
  present: boolean;
  /** R38 主动性开关（库级默认） */
  interject_enabled: boolean;
  followup_enabled: boolean;
}

/** PATCH /api/v1/characters/{cid} 的请求体（对应后端 PatchCharacterReq） */
export interface CharacterPatch {
  expected_revision?: number;
  source_world_id?: string | null;
  authoring_source?: Character['authoring_source'];
  aliases?: string[];
  talkativeness?: number;
  model?: string;
  base_url?: string;
  api_key_env?: string;
  /** 局部卡片字段 */
  card?: Partial<CharacterCard>;
  /** 采样参数（并入 llm.sampling） */
  sampling?: Record<string, unknown>;
  /** R38 主动性开关（库级默认） */
  interject_enabled?: boolean;
  followup_enabled?: boolean;
}

// ---------- 世界书 ----------

/** ST 语义：AND_ANY / NOT_ALL / NOT_ANY / AND_ALL */
export type SelectiveLogic = 0 | 1 | 2 | 3;

export interface LorebookEntry {
  /** 书内唯一序号（后端字段名就是 uid，不再映射为 id） */
  uid: number;
  /** 支持 '/.../' 正则串 */
  keys: string[];
  secondary_keys: string[];
  content: string;
  comment: string;
  enabled: boolean;
  /** 蓝灯常驻 */
  constant: boolean;
  selective: boolean;
  selective_logic: SelectiveLogic;
  /** 越大越靠近上下文末尾 */
  order: number;
  anchor: InsertAnchor;
  depth: number;
  /** 0-100 */
  probability: number;
  extensions: Record<string, unknown>;
}

export interface Lorebook {
  entry_count?: number;
  import_report?: { converted: string[]; partial: string[]; unsupported: string[] };
  id: string;
  schema_version?: number;
  revision?: number;
  source_asset_id?: string | null;
  source_asset_revision?: number | null;
  created_at?: string;
  updated_at?: string;
  name: string;
  description: string;
  tags?: string[];
  entries: LorebookEntry[];
  scan_depth: number;
  token_budget: number;
  recursive_scanning: boolean;
  /** st / risu / embedded */
  source_format: string;
}

export interface LorebookAgentDraft {
  id: string;
  revision: number;
  status: 'review' | 'committing' | 'committed';
  edited: boolean;
  payload: Partial<LorebookEntry>;
  source_refs: { source_id: string; quote: string }[];
  positive_examples: string[];
  negative_examples: string[];
  rationale: string;
  risk_notes: string[];
  simulation?: {
    positive: { text: string; triggered: boolean; reason?: string }[];
    negative: { text: string; triggered: boolean; reason?: string }[];
    estimated_tokens: number;
    valid: boolean;
  };
  validation_errors?: string[];
  committed_book_id?: string;
}

export type LorebookAgentStatus = 'queued' | 'running' | 'regenerating' | 'review' | 'failed' | 'interrupted' | 'cancelled' | 'committed';

export interface LorebookAgentJob {
  id: string;
  world_id: string;
  world_title: string;
  world_revision: number;
  target_lorebook_id: string | null;
  target_revision: number | null;
  new_lorebook_name: string;
  goal: string;
  status: LorebookAgentStatus;
  stage: string;
  drafts: LorebookAgentDraft[];
  errors: string[];
  source_ids: string[];
  source_changed: boolean;
  progress: { tool_calls: number; last_tool: string | null; attempt: number };
  created_at: string;
  updated_at: string;
}

export interface LorebookAgentJobSummary {
  id: string;
  world_id: string;
  world_title: string;
  status: LorebookAgentStatus;
  created_at: string;
  updated_at: string;
}

export interface LorebookAgentSources {
  world_id: string;
  world_title: string;
  world_revision: number;
  source_changed: boolean;
  sources: { id: string; kind: string; title: string; revision: number; char_count: number; excerpt: string; content: string }[];
}

// ---------- 导演 ----------

export interface ScoredCandidate {
  character_id: string;
  score: number;
  reasons: string[];
}

export type DirectorTrigger =
  | 'mention'
  | 'talkativeness'
  | 'manual_force'
  | 'open_round'
  | 'explicit_mention'
  | 'llm_route'
  | 'llm_switch_scene'
  | 'llm_end_scene'
  | 'player_scene';

/** R35 场景切换决策载荷 */
export interface SceneAction {
  title: string;
  description: string;
  member_ids: string[];
  transition_hint: string;
}

export interface DirectorDecision {
  turn: number;
  trigger: DirectorTrigger;
  /** R35 v2（additive；旧决策无此字段按 pick_speaker 处理） */
  action?: 'pick_speaker' | 'switch_scene' | 'end_scene';
  candidates: ScoredCandidate[];
  /** 可多人排队——是列表，不是单个 id */
  chosen: ActorId[];
  rationale?: string;
  scene?: SceneAction | null;
  rng_seed: number;
  created_at: string;
}

/** R35 Scene 实体（GET /scenes / 会话状态内） */
export interface Scene {
  id: string;
  title: string;
  description: string;
  builtin_image_id?: string | null;
  turn_start: number;
  turn_end: number | null;
  member_ids: string[];
  group_ids: string[];
  created_at: string;
}

export interface GroupActor {
  id: string;
  label: string;
  aliases: string[];
  count: number | null;
  scene_id: string;
  joined_seq: number;
  status: 'active' | 'left';
  public_brief: string;
  current_state: string;
  director_note: string;
  participation: 'on_cue' | 'occasional';
  last_spoke_turn: number | null;
  source: Record<string, unknown>;
  source_snapshot: string;
  creation_key?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GroupSource {
  id: string;
  title: string;
  aliases: string[];
  summary: string;
  revision: number;
  world_revision: number;
  excerpt: string;
  estimated_tokens: number;
}

export interface GroupDraft {
  label: string;
  aliases: string[];
  count: number | null;
  public_brief: string;
  current_state: string;
  director_note: string;
  participation: 'on_cue' | 'occasional';
}

export interface GroupSourcesResult {
  world_id: string | null;
  world_title: string | null;
  world_revision: number | null;
  sources: GroupSource[];
}

export interface GroupDraftResult {
  draft: GroupDraft;
  source: Record<string, unknown>;
  source_snapshot: string;
  estimated_tokens: number;
  usage: TokenUsage;
}

// ---------- 会话与存档 ----------

/** 会话摘要（列表端点 / 会话状态归一化后的 UI 形态） */
export interface PlayerIdentity {
  id: string;
  person_id: string;
  source_character_id: string | null;
  name: string;
  persona: string;
  character: Character | null;
  avatar_ref: string | null;
  start_seq: number;
  previous_disposition?: 'npc' | 'leave' | null;
}

export interface SwitchPlayerInput {
  target_character_id: string;
  previous_disposition: 'npc' | 'leave';
  expected_branch_revision: number;
  expected_player_identity_id: string;
  idempotency_key: string;
  entry_brief: string;
}

/** Summary endpoints do not own detail-only fields such as pinned facts. */
export type SessionSummary = Omit<Session, "pinned_facts" | "player_identities" | "player_people" | "world_core_brief" | "source_world_revision">;

export interface Session {
  player_identity_id?: string | null;
  player_identities?: PlayerIdentity[];
  player_people?: Record<string, Character>;
  response_style_id?: string | null;
  response_style_overrides?: Record<string, string | null>;
  id: string;
  title: string;
  created_at: string;
  source_world_id?: string | null;
  source_world_revision?: number | null;
  world_core_brief?: string;
  story_id?: string;
  branch_name?: string;
  parent_branch_id?: string | null;
  branch_revision?: number;
  archived?: boolean;
  character_ids: string[];
  /** 会话角色快照名称（场景预设角色不一定属于全局素材库） */
  character_names?: string[];
  persona: string;
  player_character_id?: string | null;
  reply_max_tokens?: number | null;
  prompt_preset_id?: string | null;
  turn: number;
  /** R27 会话级选项配置（后端持久化；undefined = 后端旧数据，前端按默认处理） */
  options_enabled?: boolean;
  options_style?: string;
  /** M12-R41（D14）：点击候选直发；undefined/false = 填入输入框待确认 */
  options_direct_send?: boolean;
  /** R33.4 流式输出开关（undefined = 旧数据按默认 true 处理） */
  streaming_enabled?: boolean;
  /** R34.2 输出卫生校验开关 */
  hygiene_enabled?: boolean;
  /** R35.3 导演模式（undefined = auto） */
  director_mode?: 'auto' | 'confirm' | 'rules';
  /** R37 叙事控制（undefined = 默认 free/balanced/padding on） */
  narrative_pov?: 'free' | 'second' | 'third';
  narrative_density?: 'dialogue' | 'balanced' | 'atmosphere';
  short_input_padding?: boolean;
  /** 完整会话快照包含；列表摘要默认为空。 */
  pinned_facts: PinnedFact[];
}

export type ArchiveKind = 'background' | 'biology';
export interface ArchiveRecord {
  id: string;
  kind: ArchiveKind;
  subtype: string;
  title: string;
  aliases: string[];
  tags: string[];
  summary: string;
  body: string;
  visibility: 'public' | 'private';
  kind_data: Record<string, string>;
  schema_version: number;
  revision: number;
  created_at: string;
  updated_at: string | number;
  /** Server-owned copy link. The archive editor does not read or send these. */
  copied_from_world_id?: string | null;
  copied_from_archive_id?: string | null;
  copied_from_revision?: number | null;
}
export type ArchiveInput = Pick<ArchiveRecord, 'kind' | 'subtype' | 'title' | 'aliases' | 'tags' | 'summary' | 'body' | 'visibility' | 'kind_data'>;
export interface BiologyImportItem {
  id: string;
  mode: 'copy' | 'overwrite';
}
export interface WorldSummary {
  id: string;
  schema_version?: number;
  title: string;
  description: string;
  cover_id?: string | null;
  core_brief: string;
  revision: number;
  archived: boolean;
  manuscript_archive_id?: string | null;
  runtime_policy?: 'legacy_full' | 'raw' | 'compiled';
  background_count: number;
  biology_count: number;
  lorebook_count: number;
  created_at: string;
  updated_at: string;
}
export interface World extends Omit<WorldSummary, 'background_count' | 'biology_count' | 'lorebook_count'> {
  author_core_brief?: string;
  archive_records: ArchiveRecord[];
  lorebook_ids: string[];
}

export interface TrashedStory {
  generation_id?: string | null;
  story_id: string;
  title: string;
  created_at: string;
  branch_count: number;
  message_count: number;
  save_count: number;
  memory_count: number;
  sha256: string;
  external_avatar_characters?: string[];
}

export interface StorySummary {
  membership_revision?: string | null;
  story_id: string;
  title: string;
  cover_id?: string | null;
  branch_count: number;
  event_count: number;
  save_count?: number;
  latest_branch_id: string;
  updated_at: string | number;
}

export interface BranchSummary {
  id: string;
  name: string;
  parent_branch_id: string | null;
  fork_message_id: string | null;
  fork_save_id: string | null;
  branch_revision: number;
  archived: boolean;
  message_count: number;
  first_message_id: string | null;
  last_message_id: string | null;
  events: Pick<StoryEvent, 'id' | 'anchor_message_id' | 'title' | 'kind' | 'visible_to' | 'created_at'>[];
  created_at: string;
  updated_at: string;
}

export interface WorldlinePage {
  membership_revision?: string | null;
  story_id: string;
  total_branches: number;
  next_cursor: string | null;
  branches: BranchSummary[];
}

export interface ForkResult {
  warnings?: string[];
  branch_id: string;
  story_id: string;
  parent_branch_id: string;
  fork_message_id: string | null;
  branch_revision: number;
  repeated: boolean;
}

export interface BranchPoint {
  history_warning?: string | null;
  history_complete?: boolean;
  branch_revision: number;
  branch_id: string;
  message: Message;
  forkable: boolean;
  reason: string | null;
}

export interface ScenarioPlayOptions {
  narrative_pov?: 'free' | 'second' | 'third' | null;
  narrative_density?: 'dialogue' | 'balanced' | 'atmosphere' | null;
  director_mode?: 'auto' | 'confirm' | 'rules' | null;
  options_enabled?: boolean | null;
  options_style?: 'action' | 'dialogue' | 'mixed' | null;
  proactive_turn_limit?: number | null;
}

export interface ScenarioComponent {
  [key: string]: unknown;
  version: number;
  required: boolean;
  data: Record<string, unknown>;
}

export interface ScenarioCharacterCard {
  [key: string]: unknown;
  spec: string;
  spec_version: string;
  name: string;
  description: string;
  appearance: string;
  traits_label: string;
  traits: string;
  personality: string;
  scenario: string;
  first_mes: string;
  mes_example: string;
  alternate_greetings: string[];
  system_prompt: string | null;
  post_history_instructions: string | null;
  creator_notes: string;
  creator: string;
  character_version: string;
  tags: string[];
  extensions: Record<string, unknown>;
  source_format: string;
}

export interface ScenarioLorebookEntry {
  [key: string]: unknown;
  uid: number;
  keys: string[];
  secondary_keys: string[];
  content: string;
  comment: string;
  enabled: boolean;
  constant: boolean;
  selective: boolean;
  selective_logic: SelectiveLogic;
  order: number;
  anchor: InsertAnchor;
  depth: number;
  probability: number;
  extensions: Record<string, unknown>;
}

export interface ScenarioPackage {
  [key: string]: unknown;
  id: string;
  package_id: string;
  format: 'mrp.scenario';
  format_version: 1;
  schema_version: number;
  revision: number;
  title: string;
  description: string;
  author: string;
  license: string;
  source_url: string;
  tags: string[];
  player_persona: string;
  instructions: string;
  cast: {
    key: string;
    source_asset_id?: string | null;
    source_asset_revision?: number | null;
    card: ScenarioCharacterCard;
    aliases: string[];
    talkativeness: number;
    interject_enabled: boolean;
    followup_enabled: boolean;
    lorebook_keys: string[];
  }[];
  lorebooks: {
    key: string;
    source_asset_id?: string | null;
    source_asset_revision?: number | null;
    name: string;
    description: string;
    entries: ScenarioLorebookEntry[];
    scan_depth: number;
    token_budget: number;
    recursive_scanning: boolean;
    source_format: string;
  }[];
  opening: { location: string; description: string; narration: string; member_keys: string[] };
  play: ScenarioPlayOptions;
  components: Record<string, ScenarioComponent>;
  created_at: string;
  updated_at: string;
  imported_at: string | null;
}

export interface ScenarioSummary {
  id: string;
  package_id: string;
  revision: number;
  title: string;
  description: string;
  author: string;
  imported_at: string | null;
  tags: string[];
  character_names: string[];
  lorebook_count: number;
  created_at: string;
  updated_at?: string;
}

export interface ScenarioCreateInput {
  title: string;
  description: string;
  author: string;
  license: string;
  source_url: string;
  tags: string[];
  character_ids: string[];
  lorebook_ids: string[];
  world_id?: string | null;
  player_persona: string;
  instructions: string;
  opening: { location: string; description: string; narration: string; member_keys: string[] };
  play: ScenarioPlayOptions;
}

/** F10.3 迁移包导入报告（POST /api/v1/bundle/import） */
export interface BundleReport {
  ok: boolean;
  characters: { filename: string; name: string; id: string; warnings?: string[]; import_report?: { converted: string[]; partial: string[]; unsupported: string[] } }[];
  lorebooks: { filename: string; name: string; id: string; import_report?: { converted: string[]; partial: string[]; unsupported: string[] } }[];
  errors: { filename: string; error: string }[];
  skipped: { filename: string; reason: string }[];
}

/** Stable pack IDs let new appearance packs work without a backend enum update. */
export interface AppearancePreferences {
  avatar_frame_id: string;
  dialogue_avatar_size: 32 | 40 | 56;
  primary_button_skin: boolean;
  secondary_button_skin: boolean;
  card_ornaments: boolean;
  card_border: boolean;
  background_art: boolean;
  portrait_placeholder: 'art' | 'initial';
  reading_width: 'narrow' | 'standard' | 'wide';
  reading_font_size: number;
  bubble_style_id: string;
  theme_id: string;
  visual_style_id: string;
  typography_id: string;
  decoration_id: string;
  cursor_id: string;
  trail_id: string;
  click_effect_id: string;
  effect_intensity: number;
  density: 'comfortable' | 'compact';
}

/** R48 全局设置（GET/PATCH /api/v1/settings） */
export interface Settings {
  /** Optional only for compatibility with a server predating shared appearance. */
  appearance?: AppearancePreferences;
  response_styles: ResponseStyle[];
  response_styles_revision: number;
  engine: 'dsh' | 'openrouter' | 'group';
  thinking: 'on' | 'off';
  gateway: string;
  model: string;
  auxiliary_model: string;
  model_provider: string;
  auxiliary_provider: string;
  provider_allow_fallbacks: boolean;
  context_limit_override: number | null;
  active_prompt_preset_id: string | null;
  backup_retention_days: number;
  backup_keep_count: number;
  backup_max_bytes: number;
  generation: GenerationSettings;
  hygiene_enabled: boolean;
  memory_consolidation_enabled: boolean;
  gateway_profiles: Record<string, GatewayProfile>;
  /** Compatibility view of the selected profile's text. */
  break_armor_prompt: string;
  break_armor_prompts: BreakArmorPromptPreset[];
  active_break_armor_prompt_id: string | null;
  break_armor_mode: 'opening' | 'interval';
  break_armor_interval: number;
  tts: TTSSettings;
  info: {
    default_model: string;
    gateway: string;
    api_key_configured: boolean;
    fake_mode: boolean;
    configured_providers: string[];
  };
}

export interface GenerationSettings {
  temperature: number | null;
  top_p: number | null;
  frequency_penalty: number | null;
  presence_penalty: number | null;
  max_output_tokens: number | null;
}

export interface GatewayProfile {
  gateway: string;
  model: string;
  auxiliary_model: string;
  model_provider: string;
  auxiliary_provider: string;
  provider_allow_fallbacks: boolean;
}

export interface BreakArmorPromptPreset {
  id: string;
  name: string;
  content: string;
}

export interface PromptSegment {
  id: string;
  name: string;
  content: string;
  enabled: boolean;
  anchor: 'system' | 'near' | 'at_depth';
  depth: number;
  order: number;
}

export interface PromptTransform {
  id: string;
  name: string;
  pattern: string;
  replacement: string;
  enabled: boolean;
  phase: 'before_send';
}

export interface PromptPreset {
  format: 'mrp.prompt_preset';
  id: string;
  name: string;
  description: string;
  source: string;
  revision: number;
  segments: PromptSegment[];
  transforms: PromptTransform[];
  extensions: Record<string, unknown>;
}

export interface PromptImportPreview {
  draft: PromptPreset;
  converted: string[];
  partial: string[];
  unsupported: string[];
  original_sha256: string;
}

export interface TTSSettings {
  enabled: boolean;
  executable_path: string;
  model_path: string;
  tokenizer_path: string;
  host: string;
  port: number;
  default_voice_profile_id: string | null;
}

export interface TTSStatus {
  enabled: boolean;
  state: 'disabled' | 'starting' | 'loading' | 'ready' | 'stopping' | 'error';
  progress: number;
  stage: string;
  error: string | null;
  managed: boolean;
}

export interface VoiceProfile {
  id: string;
  name: string;
  filename: string;
  transcript: string;
  sample_sha256: string;
  content_type: string;
  created_at: string;
}

export interface SettingsTestResult {
  ok: true;
  model: string;
  latency_ms: number;
}

export interface ModelProviderOption {
  slug: string;
  name: string;
  endpoint_tags: string[];
  context_length?: number | null;
}

export interface ModelProvidersResult {
  model: string;
  providers: ModelProviderOption[];
}

export interface ModelCatalogResult {
  profile: string;
  models: { id: string; name: string; context_length?: number | null; max_completion_tokens?: number | null; prompt_price_per_million?: number | null; completion_price_per_million?: number | null; reasoning?: { mandatory?: boolean; supported_efforts?: string[]; default_effort?: string } | null }[];
}

export interface SimpleChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  variants: string[];
  active_variant: number | null;
  usage: SimpleChatUsage;
  variant_usages: SimpleChatUsage[];
}

export interface SimpleChatUsage {
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  cost_usd?: number;
  estimated_cost?: number;
  cost_currency?: string;
  cost_source?: string;
  finish_reason?: string;
  reasoning_tokens?: number;
  usage_incomplete?: boolean;
}

export interface SimpleChatGeneration {
  id: string;
  message_id: string;
  model: string;
  gateway: string;
  usage: SimpleChatUsage;
  created_at: string;
}

export interface SimpleChat {
  id: string;
  title: string;
  gateway: string;
  model: string;
  provider: string;
  provider_allow_fallbacks: boolean;
  system_prompt: string;
  generation: GenerationSettings;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  price_currency: string;
  messages: SimpleChatMessage[];
  generations: SimpleChatGeneration[];
  created_at: string;
  updated_at: string;
}

export interface SimpleChatSummary {
  id: string;
  title: string;
  gateway: string;
  model: string;
  updated_at: string;
  message_count: number;
}

export interface SettingsPatch {
  appearance?: Partial<AppearancePreferences>;
  response_styles?: ResponseStyle[];
  expected_response_styles_revision?: number;
  engine?: Settings['engine'];
  thinking?: Settings['thinking'];
  gateway?: string;
  model?: string;
  auxiliary_model?: string;
  model_provider?: string;
  auxiliary_provider?: string;
  provider_allow_fallbacks?: boolean;
  context_limit_override?: number | null;
  active_prompt_preset_id?: string | null;
  backup_retention_days?: number;
  backup_keep_count?: number;
  backup_max_bytes?: number;
  generation?: GenerationSettings;
  hygiene_enabled?: boolean;
  memory_consolidation_enabled?: boolean;
  /** Compatibility write field for older clients. */
  break_armor_prompt?: string;
  break_armor_prompts?: BreakArmorPromptPreset[];
  active_break_armor_prompt_id?: string | null;
  break_armor_mode?: Settings['break_armor_mode'];
  break_armor_interval?: number;
  /** 仅用于写入，服务端永不读回 */
  api_key?: string;
  clear_api_key?: boolean;
}

export interface ModelRequestSummary {
  player_identity_source?: { control_stage_id?: string; person_id?: string; name?: string };
  id: string;
  session_id: string;
  character_id: string;
  turn: number;
  engine: 'dsh' | 'openrouter';
  created_at: string;
}

export interface ModelRequestRecord extends ModelRequestSummary {
  body: Record<string, unknown>;
}

/** R36 角色记忆记录（GET/PATCH/DELETE /characters/{cid}/memories） */
export interface MemoryRecord {
  id: string;
  character_id: string;
  session_id: string;
  turn_start: number;
  turn_end: number;
  kind: 'summary' | 'fact' | 'manual' | 'episodic' | 'scene';
  content: string;
  source_message_ids: string[];
  effective_message_id: string | null;
  commit_seq: number;
  inherited_from_id: string | null;
  invalidated: boolean;
  created_at: string;
  participants: string[];
  keywords: string[];
  importance: number;
  scene_id: string;
  category?: 'experience' | 'relationship' | 'unfinished';
  participant_ids?: string[];
  source_fingerprints?: Record<string, string>;
  evidence?: Record<string, string>;
  important?: boolean;
  revision?: number;
  manually_revised?: boolean;
  source_changed?: boolean;
  revisions?: Record<string, unknown>[];
  matter_status?: 'open' | 'completed' | 'cancelled' | 'unknown';
  supersedes?: string[];
}

export type MemoryPatch = Pick<Partial<MemoryRecord>, 'content' | 'keywords' | 'importance' | 'category' | 'important' | 'matter_status'> & { expected_revision?: number; expected_branch_revision?: number };
export interface RememberRequest {
  character_ids: string[]; source_message_ids: string[]; source_fingerprints: Record<string, string>;
  participant_ids?: string[]; content: string; category: NonNullable<MemoryRecord['category']>;
  important: boolean; matter_status: NonNullable<MemoryRecord['matter_status']>;
}
export interface MemoryWindow { start: number; end: number; status: string; error: string }

export interface Save {
  id: string;
  session_id: string;
  name: string;
  created_at: string;
  turn: number;
  message_count: number;
}

// ---------- 成本 ----------

export interface CostBucket {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}

/** GET /api/v1/sessions/{sid}/cost（对应后端 cost_report()） */
export interface CostReport {
  usage_incomplete?: boolean;
  call_count?: number;
  scope?: string;
  by_model: Record<string, CostBucket>;
  by_character: Record<string, CostBucket>;
  total: CostBucket;
  /** R34.4：轻量用途独立账（judge 等；additive，旧后端可能不带） */
  by_purpose?: Record<string, CostBucket>;
  hygiene?: { checks: number; violations_found: number; auto_corrected: number };
}

// ---------- 注入检查器 ----------

/** 注入检查器的分节种类（与后端 session.py::_SECTION_MARKERS 对齐；
 *  system = [系统上下文]（R22 潜台词 / R34 校验反馈 / 场景卡等 system 锚注入），M9-0 起存在） */
export type PromptSectionKind = 'world_core' | 'player' | 'world' | 'memory' | 'system' | 'group' | 'scene_frame' | 'scene' | 'history' | 'instructions';

export interface PromptSection {
  kind: PromptSectionKind;
  title: string;
  tokens: number;
  content: string;
}

/** GET /api/v1/sessions/{sid}/inspections/{cid}/{turn} */
export interface InspectionTarget {
  characterId: string;
  turn: number;
  messageId?: string;
  generationId?: string | null;
}

export interface Inspection {
  character_id: string;
  turn: number;
  mode?: 'actual' | 'preview';
  branch_id?: string;
  plan_id?: string | null;
  budget_tokens?: number;
  context_limit?: number | null;
  capacity_source?: string;
  capacity_fetched_at?: string | null;
  model_id?: string;
  gateway?: string;
  model_provider?: string;
  output_reserve?: number;
  sources?: { entry_id: string; source: string; anchor: string; order: number; reason: string; tokens: number; included: boolean; excluded_reason: string | null }[];
  omitted_message_ids?: string[];
  not_triggered?: { entry_id: string; title: string; reason: string }[];
  pinned_facts?: { id: string; visible_to: Visibility; included: boolean; reason: string }[];
  prompt: string;
  tokens_by_section: Record<string, number>;
  sections: PromptSection[];
  total_tokens: number;
  injected_entry_ids: string[];
  visible_message_ids: string[];
}

// ===== SSE 事件 =====
// 后端实际发出的：message.pending / director.decision / message.final /
// message.retracted / cost.update / heartbeat（见 mrp/server/sse.py + session.py）。
// M8-R32 新增：message.updated / message.deleted / message.error。
// 注：message.delta（流式）在 M8-R33 落地时加入——v1 曾拍板"整段显示，
// 放弃 token 流式"，v3.0.md R33 推翻该决策（见 document/design/v3/m8-r33-streaming.md）。

/** R27：候选消息（M12-R41 起为完整玩家消息；options 手动生成，见 /assist/candidates） */
export interface OptionChoice {
  text: string;
  mention_character_id: string | null;
  /** 进入姿态：hook（接住）/ push（推进）/ soft（软接入） */
  kind?: string;
}

/** M12-R41 辅助候选批次（POST /assist/candidates | /assist/draft 响应）
 *  source: kickoff=开场/新场景；turn=对话接话；draft=R40 意图代笔 */
export interface AssistBatch {
  options: OptionChoice[];
  source?: 'kickoff' | 'turn' | 'draft';
  turn?: number;
  kickoff_kind?: 'open' | 'scene';
  scene_id?: string;
  branch_id?: string;
  branch_revision?: number;
  anchor_message_id?: string;
  anchor_fingerprint?: string;
  anchor_actor?: ActorId;
  anchor_label?: string;
  anchor_kind?: MessageKind;
  anchor_excerpt?: string;
  /** 未生成时的静默原因：disabled / empty / failed */
  reason?: string;
}

export interface AssistAnchor {
  message_id: string;
  fingerprint: string;
  actor: ActorId;
  label: string;
  kind: MessageKind;
  excerpt: string;
}

export interface InputGroupEditPart {
  message_id: string;
  expected_fingerprint: string;
  content: string;
}

export type SseEvent =
  | ({ type: 'message.pending'; message: Message } & GenerationEventIdentity)
  | { type: 'director.decision'; decision: DirectorDecision }
  | ({ type: 'message.final'; message: Message } & GenerationEventIdentity)
  | { type: 'message.retracted'; message_id: string; turn: number }
  | { type: 'cost.update'; cost_by_model: Record<string, CostBucket>; by_purpose?: Record<string, CostBucket> }
  | { type: 'message.updated'; message: Message }
  | { type: 'message.deleted'; message_id: string; turn: number }
  | ({ type: 'message.error'; message_id: string; turn: number; error: string; discard_pending?: boolean; message?: Message } & GenerationEventIdentity)
  | ({ type: 'message.delta'; message_id: string; turn: number; character_id: string; delta: string; offset: number; delivery?: 'live' | 'replay' } & GenerationEventIdentity)
  | { type: 'scene.switched'; old_scene: Scene | null; new_scene: Scene; transition_message: Message }
  | { type: 'director.pending'; decision: DirectorDecision }
  | { type: 'reply.plan'; turn: number; mode: 'parallel' | 'serial' | 'free'; order: string[]; reason: string; basis_fingerprint: string }
  | { type: 'session.roster.changed'; character_id: string }
  | { type: 'session.player.changed'; player_identity_id: string; branch_revision: number }
  | { type: 'turn.run.updated'; run: TurnRun; branch_revision: number }
  | { type: 'conversation.run.updated'; run: ConversationRun; branch_revision: number }
  | { type: 'heartbeat'; ts: number };

/** EventSource 的最小接口（原生 EventSource 满足） */
export interface EventSourceLike {
  readyState: number;
  addEventListener(type: string, listener: (ev: { data: string }) => void): void;
  removeEventListener(type: string, listener: (ev: { data: string }) => void): void;
  close(): void;
}

export const SSE_EVENT_TYPES = [...STORY_EVENT_TYPES, 'heartbeat'] as const;

// ===== UI 派生类型（不属后端契约，纯展示便利） =====

export type CharacterColor = 'emerald' | 'violet' | 'amber' | 'rose' | 'sky' | 'slate';

/** 契约 Character + 由 id 派生的气泡色系（后端不存该字段） */
export interface CharacterView extends Character {
  color: CharacterColor;
}

export interface ResponseStyle {
  id: string; schema_version: number; revision: number; name: string; description: string; content: string; enabled: boolean;
}

export interface TurnRun {
  id?: string;
  operation_id: string;
  session_id: string;
  status: 'running' | 'awaiting_director' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
  slots: Array<{ actor_id: string; message_id: string; generation_id?: string; seq?: number;
    status: 'pending' | 'committed' | 'failed' | 'cancelled'; error?: string | null }>;
  error?: string;
  last_error?: string | null;
  errors?: string[];
  updated_at?: string;
  epoch: number;
}
