import { commandReceipt } from '../utils/commandReceipt';
import { loadSessionSummaries } from '../features/stories/storyQueries';
import { createStoryConnection } from '../features/stories/storyConnection';
import { prepareStoryCommand } from '../api/storyCommands';
import { ApiError } from '../api/request';
import { installSessionMetadata, mergeSessionFields, type SessionFieldRevisions } from '../features/stories/storySessionMetadata';
import { reduceStoryEvent, reduceGenerationAttempt, acceptsTurnIdentity, commitStoryMessage } from '../features/stories/storyEventReducer';
import { useStoryUiStore } from '../features/stories/storyUiStore';
export type { AssistTab } from '../features/stories/storyUiStore';
import { projectMessage, projectMessages, projectTurnRun, contiguousPrefix } from '../features/stories/storyView';
import { createClientId } from '../utils/clientId.js';
import { create } from 'zustand';
import { storyApi as api } from '../features/stories/storyApi';
import { toView } from '../api/adapters';
import { connectSSE, type ConnectionStatus } from '../api/sse';
import { readStoryLocation, writeStoryLocation } from '../api/navigation';
import { parseChannelParts } from '../utils/channels';
import { isAssistBatchCurrent } from '../utils/assistFreshness';
import { pendingAfterSnapshot } from '../utils/optimisticMessages';
import {
  SSE_EVENT_TYPES,
  type AssistBatch,
  type AssistAnchor,
  type CostBucket,
  type Character,
  type CharacterView,
  type Lorebook,
  type DirectorDecision,
  type GroupActor,
  type Message,
  type OptionChoice,
  type InputGroupEditPart,
  type Scene,
  type Session,
  type SseEvent,
  type Visibility,
  type ConversationRun,
  type TurnRun,
  type GenerationEventIdentity,
  type MessageOperationInput,
  type MessageOperationResult,
  type ReplyMode,
  type InspectionTarget,
} from '../types';

function httpCommandEvent(event: Extract<SseEvent, { type: 'message.updated' | 'message.deleted' }>, result: unknown, fallbackRevision?: number): SseEvent {
  const receipt = commandReceipt(result);
  const messageId = 'message' in event && event.message ? event.message.id : 'message_id' in event ? event.message_id : '';
  const command = { ...event, branch_revision: receipt?.branch_revision ?? fallbackRevision,
    outbox_event_id: receipt ? `command:${receipt.command_id}:${messageId}` : undefined, delivery_source: 'http' };
  return command;
}

/** 模块级持有 SSE 连接（不进 react state） */
const storyConnection = createStoryConnection(connectSSE, SSE_EVENT_TYPES);
let sessionRefreshTimer: ReturnType<typeof setTimeout> | null = null;
let branchLoadEpoch = 0;
export const storyBranchEpoch = () => branchLoadEpoch;
let historyReadEpoch = 0;
const factReceiptRevisions = new Map<string, number>();
let eventResync: { branchId: string; epoch: number; promise?: Promise<void> } | null = null;
let acceptedOutboxEvents: readonly string[] = [];
let messageRequestEpoch = 0;
let freeSendRequestEpoch: number | null = null;
let ordinarySendOperationId: string | null = null;
let branchRead: AbortController | null = null;
const swipingIds = new Set<string>();
export const isSwipingMessage = (messageId: string): boolean => swipingIds.has(messageId);

interface GenerationSlot extends GenerationEventIdentity {
  completed: boolean;
  retired: Set<string>;
}
const generations = new Map<string, GenerationSlot>();
const streamKey = (identity: GenerationEventIdentity) => `${identity.generation_id ?? ''}:${identity.attempt_id ?? ''}`;
function seedGenerations(messages: Message[], preserveActive = false): void {
  const prior = new Map(generations);
  const active = preserveActive ? [...prior.entries()].filter(([id, slot]) => {
    const fresh = messages.find((message) => message.id === id);
    return !slot.completed && Boolean(useChatStore.getState().regenerationPrevious[id])
      && !(fresh?.status === 'pending' && fresh.generation_id && streamKey(fresh) !== streamKey(slot));
  }) : [];
  generations.clear();
  for (const message of messages) if (message.generation_id) {
    const previous = preserveActive ? prior.get(message.id) : undefined;
    const retired = previous?.retired ?? new Set<string>();
    if (previous?.generation_id && streamKey(previous) !== streamKey(message)) retired.add(streamKey(previous));
    generations.set(message.id, {
      generation_id: message.generation_id, attempt_id: message.attempt_id, operation_id: message.operation_id,
      completed: message.status !== 'pending', retired,
    });
  }
  for (const [id, slot] of active) generations.set(id, slot);
}
/** A new pending event is the only boundary allowed to activate a new attempt. */
export function acceptGenerationEvent(messageId: string, identity: GenerationEventIdentity, kind: 'pending' | 'delta' | 'final' | 'error'): boolean {
  const result = reduceGenerationAttempt(generations.get(messageId), identity, kind);
  if (result.accepted && result.slot) generations.set(messageId, { ...result.slot, retired: new Set(result.slot.retired) });
  return result.accepted;
}
export const isConversationActive = (run: ConversationRun | null | undefined, sessionId?: string | null): boolean => Boolean(run
  && (!run.session_id || sessionId === undefined || run.session_id === sessionId)
  && (run.status === 'queued' || run.status === 'running'));
export function mergeConversationRun(runs: ConversationRun[], incoming: ConversationRun): ConversationRun[] {
  const previous = runs.find((run) => run.id === incoming.id);
  if (previous && (incoming.epoch < previous.epoch || (incoming.epoch === previous.epoch && (
    incoming.completed_replies < previous.completed_replies || incoming.updated_at < previous.updated_at
  )))) return runs;
  return previous ? runs.map((run) => run.id === incoming.id ? incoming : run) : [...runs, incoming];
}

/** Epochs and durable timestamps prevent a late request/replay from reopening a stopped turn. */
export function mergeTurnRun(runs: TurnRun[], incoming: TurnRun): TurnRun[] {
  incoming = projectTurnRun(incoming);
  const previous = runs.find((run) => run.operation_id === incoming.operation_id && run.session_id === incoming.session_id);
  if (previous) {
    if (incoming.epoch < previous.epoch) return runs;
    if (incoming.epoch === previous.epoch) {
      if (incoming.updated_at && previous.updated_at && incoming.updated_at < previous.updated_at) return runs;
      if (incoming.slots.filter((slot) => slot.status === 'committed').length
          < previous.slots.filter((slot) => slot.status === 'committed').length) return runs;
      if (previous.status !== 'running' && incoming.status === 'running'
          && (!incoming.updated_at || !previous.updated_at || incoming.updated_at <= previous.updated_at)) return runs;
    }
  }
  return previous ? runs.map((run) => run === previous ? incoming : run) : [...runs, incoming];
}

const ordinaryOwner = (runs: TurnRun[], identity: GenerationEventIdentity): TurnRun | undefined =>
  identity.operation_id ? runs.find((run) => run.operation_id === identity.operation_id) : undefined;

function acceptTurnEvent(messageId: string, identity: GenerationEventIdentity,
                         kind: 'pending' | 'delta' | 'final' | 'error'): boolean {
  const state = useChatStore.getState();
  return acceptsTurnIdentity(state.currentSessionId, state.turnRuns, messageId, identity, kind);
}

function reconcileOrdinaryPending(messages: Message[], runs: TurnRun[], sessionId: string): Message[] {
  return messages.filter((message) => {
    if (message.status !== 'pending') return true;
    const owner = ordinaryOwner(runs, message);
    if (!owner) return true;
    if (owner.session_id !== sessionId) return false;
    const slot = owner.slots.find((item) => item.message_id === message.id);
    return Boolean(slot && (!slot.generation_id || !message.generation_id || slot.generation_id === message.generation_id)
      && (slot.status === 'committed' || (slot.status === 'pending' && owner.status === 'running')));
  });
}

/** Snapshot runtime projections must belong to this branch; committed history remains readable. */
export function snapshotMessagesForBranch(messages: Message[], runs: ConversationRun[], sessionId: string,
                                          turnRuns: TurnRun[] = []): Message[] {
  const kept = new Map<string, Message>();
  for (const message of messages) {
    if (message.status === 'pending') {
      if (message.session_id && message.session_id !== sessionId) continue;
      const owner = runs.find((run) => Boolean(message.operation_id && run.operation_id === message.operation_id)
        || Boolean(message.generation_id && run.current_generation_id === message.generation_id)
        || (!message.operation_id && !message.generation_id && run.current_message_id === message.id));
      if (owner?.session_id && owner.session_id !== sessionId) continue;
      if (owner && !isConversationActive(owner, sessionId)) continue;
      if (owner?.current_generation_id && message.generation_id && owner.current_generation_id !== message.generation_id) continue;
      if (owner?.current_attempt_id && message.attempt_id && owner.current_attempt_id !== message.attempt_id) continue;
    }
    const previous = kept.get(message.id);
    if (previous?.status === 'final' && message.status === 'pending') continue;
    kept.set(message.id, message);
  }
  return reconcileOrdinaryPending(Array.from(kept.values()), turnRuns, sessionId);
}

function refreshSessionSummariesSoon(): void {
  if (sessionRefreshTimer != null) clearTimeout(sessionRefreshTimer);
  sessionRefreshTimer = setTimeout(() => {
    sessionRefreshTimer = null;
    void useChatStore.getState().loadSessions().catch(() => { /* Next navigation offers retry. */ });
  }, 350);
}

export type SseStatus = 'idle' | 'open' | 'reconnecting' | 'closed';

interface ChatState {
  sessions: Session[];
  sessionFieldRevisions: SessionFieldRevisions;
  sessionCharacters: CharacterView[];
  sessionLorebooks: Lorebook[];
  sessionGroups: GroupActor[];
  currentSessionId: string | null;
  /** Session roster and scenes are safe to use for pruning persisted composer targets only after this matches currentSessionId. */
  snapshotLoadedSessionId: string | null;
  snapshotStatus: 'idle' | 'loading' | 'ready' | 'error';
  snapshotError: string | null;
  messages: Message[];
  nextBeforeSeq: number | null;
  latestSeq: number;
  appliedSnapshotRevision: number;
  locatedMessageId: string | null;
  historyLoading: boolean;
  historyError: string | null;
  loadEarlier: () => Promise<void>;
  locateMessage: (messageId: string | null) => Promise<void>;
  lastDecision: DirectorDecision | null;
  /** R35.3 确认档：挂起的 LLM 导演决策（director.pending 事件落地） */
  pendingDirector: DirectorDecision | null;
  /** 后端 cost.update 事件的最新快照（按模型） */
  costByModel: Record<string, CostBucket> | null;
  /** M12-R41：当前一批辅助候选（仅点击生成；发送/切场景/关闭后清空） */
  options: OptionChoice[];
  optionsTurn: number | null;
  /** 候选批次元信息（来源/上下文；展示用） */
  optionsSource: 'turn' | 'kickoff' | 'draft' | null;
  optionsKind: 'open' | 'scene' | null;
  optionsSceneId: string | null;
  optionsAnchor: AssistAnchor | null;
  optionsNotice: string | null;
  /** 生成中（按钮 loading；防重复点击） */
  generatingCandidates: boolean;
  /** 「查看本轮上下文」目标（导演区 → 检查器联动，消费后不清也行） */
  inspectTarget: InspectionTarget | null;
  /** 当前场景（导演与场景面板用；打开会话时拉取 + scene.switched 更新） */
  activeScene: Scene | null;
  sseStatus: SseStatus;
  sending: boolean;
  switchingPlayer: boolean;
  switchPlayer: (input: import('../types').SwitchPlayerInput) => Promise<void>;
  generationError: string | null;
  dismissGenerationError: () => void;
  regenerationPrevious: Record<string, Message>;
  turnRuns: import('../types').TurnRun[];
  conversationRuns: ConversationRun[];
  conversationActionBusy: string | null;

  loadSessions: () => Promise<void>;
  selectSession: (id: string, updateHistory?: boolean) => Promise<void>;
  refreshSession: (id: string) => Promise<void>;
  createSession: (title: string, characterIds: string[], persona: string, playerCharacterId?: string | null) => Promise<void>;
  sendMessage: (content: string, mentions?: string[], channel?: string, replyMode?: ReplyMode, maxReplies?: number, conversationDirective?: string) => Promise<void>;
  regenerateOne: (messageId: string) => Promise<void>;
  regenerateDependents: (messageId: string) => Promise<void>;
  acceptDependencies: (messageId: string) => Promise<void>;
  startConversation: (participantIds: string[], maxReplies: number, directive?: string) => Promise<void>;
  controlConversation: (runId: string, action: 'pause' | 'stop' | 'resume', additionalReplies?: number) => Promise<void>;
  controlTurn: (operationId: string, action: 'resume' | 'stop') => Promise<void>;
  requestGroupReply: (groupId: string) => Promise<void>;
  swipe: (messageId: string) => Promise<void>;
  swipeGroup: (groupId: string, messageId: string) => Promise<void>;
  /** M12-R41 生成候选（冷启动点 → 开场/新场景；否则 → 对话接话）；每次点击都是全新一批 */
  generateCandidates: () => Promise<void>;
  /** ✕ 关闭本批（清空显示；下次点击生成才有新的） */
  dismissOptions: () => void;
  /** 开合右侧辅助面板 */
  requestInspect: (characterId: string, turn: number, messageId?: string, generationId?: string | null) => void;
  /** R45 重跑最后一轮：编辑我的消息后基于新文本重新生成（旧回复由 SSE message.deleted 清除） */
  regenerate: (messageId: string) => Promise<void>;
  /** R32.2 编辑消息（下回合起引用编辑后内容） */
  editMessage: (messageId: string, content: string, expectedBranchRevision?: number, expectedFingerprint?: string) => Promise<void>;
  editInputGroup: (messageId: string, parts: InputGroupEditPart[], expectedBranchRevision: number) => Promise<void>;
  /** R32.1 候选切换 */
  switchVariant: (messageId: string, activeVariant: number) => Promise<void>;
  /** R32.3 真删除 */
  deleteMessage: (messageId: string) => Promise<void>;
  createPinnedFact: (input: { content: string; message_id?: string; visible_to?: Visibility }) => Promise<void>;
  updatePinnedFact: (factId: string, content: string) => Promise<void>;
  deletePinnedFact: (factId: string) => Promise<void>;
  /** R34 对当前内容重跑校验（角标「重新校验」入口） */
  recheckHygiene: (messageId: string) => Promise<void>;
  /** R35.3 确认档：执行挂起的 LLM 导演决策 */
  confirmDirector: () => Promise<void>;
  /** R35.3 确认档：否决 → v1 规则执行 */
  rejectDirector: () => Promise<void>;
  /** R37.3 续写最后一条角色消息 */
  continueMessage: (messageId: string) => Promise<void>;
  /** 存档恢复后直接落地会话与消息 */
  applyRestore: (session: Session, messages: Message[], characters?: Character[], lorebooks?: Lorebook[], groups?: GroupActor[]) => void;
  handleSseEvent: (evt: SseEvent) => void;
  disconnect: () => void;
}

function connect(sessionId: string, after: string | null, onEvent: (evt: SseEvent) => void, onConnectionChange: (status: ConnectionStatus) => void): void {
  storyConnection.connect(sessionId, after, onEvent, onConnectionChange, () => { void resyncSession(sessionId); });
}

async function resyncSession(sessionId: string, reportError = false, reloadWindow = false): Promise<void> {
  if (useChatStore.getState().currentSessionId !== sessionId) return;
  const loadEpoch = branchLoadEpoch;
  const revisionAtRequest = useChatStore.getState().sessions.find((x) => x.id === sessionId)?.branch_revision ?? 0;
  storyConnection.close();
  useChatStore.setState({ sseStatus: 'reconnecting' });
  try {
    const location = useChatStore.getState().locatedMessageId;
    let snapshot = await api.getStoryView(sessionId, reloadWindow && location ? { around: location } : {});
    const loaded = useChatStore.getState().messages;
    const joined = reloadWindow ? snapshot.messages : contiguousPrefix(loaded, snapshot.messages);
    const preservedPrefix = joined !== snapshot.messages;
    if (location && joined === snapshot.messages && loaded.length && snapshot.messages.length
      && loaded[loaded.length - 1].seq < snapshot.messages[0].seq - 1) {
      snapshot = await api.getStoryView(sessionId, { around: location });
    } else {
      snapshot = { ...snapshot, messages: joined, next_before_seq: joined !== snapshot.messages
        ? useChatStore.getState().nextBeforeSeq : snapshot.next_before_seq };
    }
    if (useChatStore.getState().currentSessionId !== sessionId || loadEpoch !== branchLoadEpoch) return;
    const currentRevision = useChatStore.getState().sessions.find((x) => x.id === sessionId)?.branch_revision ?? 0;
    if ((snapshot.session.branch_revision ?? 0) < Math.max(currentRevision, revisionAtRequest)) {
      connectStory(sessionId, null);
      return;
    }
    clearTyping();
    const currentState = useChatStore.getState();
    const turnRuns = (snapshot.turn_runs ?? []).reduce(mergeTurnRun, currentState.turnRuns);
    const localRuns = turnRuns.filter((run) => run.session_id === sessionId);
    const latestRun = localRuns[localRuns.length - 1];
    const previousRun = currentState.turnRuns.find((run) => run.operation_id === latestRun?.operation_id);
    const recoveredError = latestRun?.status === 'completed' && previousRun?.status !== 'completed';
    const freshMessages = snapshotMessagesForBranch(snapshot.messages, snapshot.conversation_runs ?? [], sessionId, turnRuns);
    const messages = snapshotMessagesForBranch([...freshMessages.map((fresh) => {
      const current = currentState.messages.find((message) => message.id === fresh.id);
      const sameStream = !fresh.generation_id || !current?.generation_id || streamKey(current) === streamKey(fresh);
      if (currentState.regenerationPrevious[fresh.id] && current?.status === 'pending' && fresh.status !== 'pending') return current;
      if (current?.status === 'final' && fresh.status === 'pending' && sameStream) return current;
      if (current?.status === 'pending' && fresh.status === 'pending' && sameStream && current.content.length > fresh.content.length) {
        return { ...fresh, content: current.content };
      }
      return fresh;
    }), ...pendingAfterSnapshot(currentState.messages, freshMessages)],
      (snapshot.conversation_runs ?? []).reduce(mergeConversationRun, currentState.conversationRuns), sessionId, turnRuns);
    seedGenerations(messages, true);
    useChatStore.setState((state) => ({
      // HTTP 请求仍在进行时保留本地气泡；其最终响应仍会落地权威消息。
      messages,
      appliedSnapshotRevision: preservedPrefix ? currentState.appliedSnapshotRevision : snapshot.session.branch_revision ?? 0,
      nextBeforeSeq: snapshot.next_before_seq ?? null,
      latestSeq: snapshot.latest_seq ?? Math.max(-1, ...snapshot.messages.map(message => message.seq)),
      sessionCharacters: snapshot.characters.map(toView),
      sessionLorebooks: snapshot.lorebooks,
      sessionGroups: snapshot.groups,
      conversationRuns: (snapshot.conversation_runs ?? []).reduce(mergeConversationRun, state.conversationRuns),
      turnRuns,
      generationError: recoveredError ? null : state.generationError,
      sending: turnRuns.some((run) => run.session_id === sessionId && run.status === 'running') ? true : state.sending,
      pendingDirector: snapshot.pending_director ?? null,
      activeScene: snapshot.scenes.find((scene) => scene.id === snapshot.active_scene_id) ?? null,
      ...installSessionMetadata(state, sessionId, snapshot.session, snapshot.session.branch_revision, true),
    }));
    connectStory(sessionId, snapshot.event_cursor);
  } catch (cause) {
    if (useChatStore.getState().currentSessionId === sessionId && loadEpoch === branchLoadEpoch) useChatStore.setState({ sseStatus: 'closed' });
    if (reportError) throw cause;
  }
}

function connectStory(sessionId: string, after: string | null): void {
  connect(sessionId, after, (evt) => {
    if (useChatStore.getState().currentSessionId !== sessionId) return;
    useChatStore.getState().handleSseEvent(evt);
  }, (status) => {
    if (useChatStore.getState().currentSessionId === sessionId) useChatStore.setState({ sseStatus: status });
  });
}

/** 把一条消息并入列表：同 id 就地替换，否则追加 */
function upsert(messages: Message[], incoming: Message): Message[] {
  const idx = messages.findIndex((m) => m.id === incoming.id);
  if (idx < 0) return [...messages, incoming];
  const next = [...messages];
  next[idx] = incoming;
  return next;
}

function messageOperationInput(sessionId: string, message: Message): MessageOperationInput {
  const session = useChatStore.getState().sessions.find((item) => item.id === sessionId);
  return { operation_id: createClientId(), expected_branch_revision: session?.branch_revision ?? 0,
    expected_fingerprint: message.fingerprint, expected_player_identity_id: session?.player_identity_id ?? null };
}
function applyMessageOperation(result: MessageOperationResult): void {
  for (const message of result.messages) if (message.generation_id) {
    const previous = generations.get(message.id);
    const retired = previous?.retired ?? new Set<string>();
    if (previous?.generation_id && streamKey(previous) !== streamKey(message)) retired.add(streamKey(previous));
    generations.set(message.id, { generation_id: message.generation_id, operation_id: message.operation_id,
      attempt_id: message.attempt_id, completed: message.status !== 'pending', retired });
  }
  useChatStore.setState((state) => ({
    messages: result.messages.reduce(upsert, state.messages),
    sessions: state.sessions.map((session) => session.id === state.currentSessionId
      ? { ...session, branch_revision: Math.max(session.branch_revision ?? 0, result.branch_revision) } : session),
    ...clearOptions,
  }));
}
async function performMessageOperation(messageId: string, action: 'one' | 'dependents' | 'accept'): Promise<void> {
  const state = useChatStore.getState();
  const sid = state.currentSessionId;
  const before = state.messages.find((message) => message.id === messageId);
  if (!sid || !before || before.status !== 'final' || state.sending || state.conversationActionBusy || state.conversationRuns.some((run) => isConversationActive(run, sid))) return;
  const loadEpoch = branchLoadEpoch;
  ++messageRequestEpoch;
  const operationPath = `/api/v1/sessions/${encodeURIComponent(sid)}/messages/${encodeURIComponent(messageId)}/${action === 'one' ? 'regenerate-one' : action === 'dependents' ? 'regenerate-dependents' : 'accept-dependencies'}`;
  const input = prepareStoryCommand(operationPath, { ...messageOperationInput(sid, before) }).payload as unknown as MessageOperationInput;
  invalidateCandidateEpoch();
  if (action !== 'accept') {
    clearTyping(messageId);
    swipingIds.add(messageId);
    const retired = generations.get(messageId)?.retired ?? new Set<string>();
    if (before.generation_id) retired.add(streamKey(before));
    generations.set(messageId, { operation_id: input.operation_id, completed: false, retired });
  }
  useChatStore.setState((current) => ({ sending: true, generationError: null, ...clearOptions,
    regenerationPrevious: action === 'accept' ? current.regenerationPrevious : { ...current.regenerationPrevious, [messageId]: before },
    messages: action === 'one' ? current.messages.map((message) => message.id === messageId
      ? { ...message, status: 'pending', content: '', operation_id: input.operation_id } : message) : current.messages,
  }));
  try {
    if (!storyConnection.isConnected()) await resyncSession(sid);
    if (useChatStore.getState().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
    const result = action === 'one' ? await api.regenerateOne(sid, messageId, input)
      : action === 'dependents' ? await api.regenerateDependents(sid, messageId, input)
      : await api.acceptDependencies(sid, messageId, input);
    if (useChatStore.getState().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
    const receipt = commandReceipt(result);
    const revision = useChatStore.getState().sessions.find(session => session.id === sid)?.branch_revision ?? 0;
    if ((receipt?.branch_revision ?? result.branch_revision) < revision) {
      useChatStore.setState(current => ({ messages: current.messages.map(message => message.id === messageId
        && message.status === 'pending' && message.operation_id === input.operation_id ? before : message) }));
      return;
    }
    result.messages.forEach((message) => clearTyping(message.id));
    applyMessageOperation(result);
  } catch (cause) {
    if (useChatStore.getState().currentSessionId === sid && loadEpoch === branchLoadEpoch) {
      clearTyping(messageId);
      useChatStore.setState((current) => ({ messages: upsert(current.messages, before),
        generationError: cause instanceof Error ? cause.message : '生成失败，原候选已保留' }));
      await resyncSession(sid);
    }
    throw cause;
  } finally {
    swipingIds.delete(messageId);
    if (useChatStore.getState().currentSessionId === sid && loadEpoch === branchLoadEpoch) useChatStore.setState((current) => {
      const previous = { ...current.regenerationPrevious }; delete previous[messageId];
      return { sending: false, regenerationPrevious: previous };
    });
  }
}

/** A server message replaces the temporary bubble for the same speaker and turn. */
function replaceLocalPending(messages: Message[], incoming: Message): Message[] {
  return commitStoryMessage(messages, incoming);
}

// ===== M12-R41 辅助候选（手动触发）：批次落地 =====

/** 落地一批候选（POST /assist/candidates 响应）。 */
function applyAssistBatch(batch: AssistBatch): void {
  useChatStore.setState({
    options: batch.options ?? [],
    optionsTurn: batch.turn ?? null,
    optionsSource: batch.source ?? null,
    optionsKind: batch.kickoff_kind ?? null,
    optionsSceneId: batch.scene_id ?? null,
    optionsAnchor: batch.anchor_message_id ? {
      message_id: batch.anchor_message_id,
      fingerprint: batch.anchor_fingerprint ?? '',
      actor: batch.anchor_actor ?? 'player',
      label: batch.anchor_label ?? '未知发言者',
      kind: batch.anchor_kind ?? 'roleplay',
      excerpt: batch.anchor_excerpt ?? '',
    } : null,
    optionsNotice: null,
  });
}

// ===== 打字缓冲：仅用于 DSH 等完整回复后的 replay 片段 =====
// live 片段由上游实时产生，直接写入气泡，不再人为延长可见回复时间。

interface TypingState {
  pending: string;
  final: Message | null; // final 已到但仍在释放 → 排空后提交（final 是权威整包）
}
const typing = new Map<string, TypingState>();
let rafId: number | null = null;

function clearTyping(messageId?: string) {
  if (messageId != null) typing.delete(messageId);
  else typing.clear();
  if (typing.size === 0 && rafId != null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
}

function pumpTyping() {
  rafId = null;
  for (const [id, st] of typing) {
    if (st.pending.length > 0) {
      const take = Math.max(2, Math.ceil(st.pending.length / 45));
      const piece = st.pending.slice(0, take);
      st.pending = st.pending.slice(take);
      useChatStore.setState((s) => ({
        messages: s.messages.map((m) => (m.id === id ? { ...m, content: m.content + piece } : m)),
      }));
    } else if (st.final != null) {
      const fin = st.final;
      typing.delete(id);
      useChatStore.setState((s) => ({ messages: commitStoryMessage(s.messages, fin) }));
    }
  }
  if (typing.size > 0) rafId = requestAnimationFrame(pumpTyping);
}

/** delta 落缓冲（offset 规则见 design/v3/m8-r33 §3.3；
 *  R48 修正：截断按"content + 待打字缓冲"的逻辑位置判定——重试片可能在
 *  上一段还在打字（content 尚未增长）时到达，只看 content 会漏判导致两段叠加） */
function bufferDelta(evt: Extract<SseEvent, { type: 'message.delta' }>) {
  const msg = useChatStore.getState().messages.find((m) => m.id === evt.message_id);
  if (msg == null) return; // pending 未到/已删——忽略，等 final 权威替换
  if (msg.status === 'final') return; // HTTP 完成或重放 final 后，旧片段不得改写
  if (evt.offset < msg.content.length
    && msg.content.slice(evt.offset, evt.offset + evt.delta.length) === evt.delta
    && evt.offset + evt.delta.length <= msg.content.length) return; // 重连补发时去重
  if (evt.delivery === 'live') {
    // 后端已在 60ms 窗口合流；这里跟随实际到达速度显示。
    // offset 小于当前长度表示重试从中间或开头改写，最终消息仍是权威结果。
    clearTyping(evt.message_id);
    if (evt.offset > msg.content.length) return; // 中间片丢失，等待 final 补齐
    useChatStore.setState((s) => ({
      messages: s.messages.map((m) => m.id === evt.message_id
        ? { ...m, content: m.content.slice(0, evt.offset) + evt.delta }
        : m),
    }));
    return;
  }
  const st = typing.get(evt.message_id) ?? { pending: '', final: null };
  typing.set(evt.message_id, st);
  const logical = msg.content.length + st.pending.length;
  if (evt.offset > logical) return; // 丢了中间片，等 final
  if (evt.offset < logical) {
    // 内容重启（R34 重试 / 引擎重建重试）：按逻辑位置截断（含未打字的缓冲）
    if (evt.offset < msg.content.length) {
      useChatStore.setState((s) => ({
        messages: s.messages.map((m) =>
          m.id === evt.message_id ? { ...m, content: m.content.slice(0, evt.offset) } : m,
        ),
      }));
      st.pending = '';
    } else {
      st.pending = st.pending.slice(0, evt.offset - msg.content.length);
    }
  }
  st.pending += evt.delta;
  if (rafId == null) rafId = requestAnimationFrame(pumpTyping);
}

/** 清空候选（发送/重跑/切会话；R27.2 + M12-R41 元信息一起复位）。 */
const clearOptions = {
  options: [] as OptionChoice[],
  optionsTurn: null as number | null,
  optionsSource: null as 'turn' | 'kickoff' | 'draft' | null,
  optionsKind: null as 'open' | 'scene' | null,
  optionsSceneId: null as string | null,
  optionsAnchor: null as AssistAnchor | null,
  optionsNotice: null as string | null,
  optionsMoreRemaining: 0,
};
let candidateEpoch = 0;
function invalidateCandidateEpoch(): void { candidateEpoch += 1; }

export const useChatStore = create<ChatState>()((rawSet, get, store) => {
  // Enforce the reader boundary for commands, streams and external feature writes.
  const clean = (patch: Partial<ChatState>): Partial<ChatState> => ({ ...patch,
    ...(patch.messages ? { messages: projectMessages(patch.messages) } : {}),
    ...(patch.turnRuns ? { turnRuns: patch.turnRuns.map(projectTurnRun) } : {}),
    ...(patch.regenerationPrevious ? { regenerationPrevious: Object.fromEntries(Object.entries(patch.regenerationPrevious).map(([id, message]) => [id, projectMessage(message)])) } : {}),
  });
  const set: typeof rawSet = (patch, replace) => rawSet(typeof patch === 'function'
    ? state => clean(patch(state)) : clean(patch), replace);
  store.setState = set;
  return ({
  sessions: [],
  sessionFieldRevisions: {},
  sessionCharacters: [],
  sessionLorebooks: [],
  sessionGroups: [],
  currentSessionId: null,
  snapshotLoadedSessionId: null,
  snapshotStatus: 'idle',
  snapshotError: null,
  messages: [],
  nextBeforeSeq: null, latestSeq: -1, appliedSnapshotRevision: 0, locatedMessageId: null, historyLoading: false, historyError: null,
  lastDecision: null,
  pendingDirector: null,
  costByModel: null,
  options: [],
  optionsTurn: null,
  optionsSource: null,
  optionsKind: null,
  optionsSceneId: null,
  optionsAnchor: null,
  optionsNotice: null,
  generatingCandidates: false,
  inspectTarget: null,
  activeScene: null,
  sseStatus: 'idle',
  sending: false,
  switchingPlayer: false,
  generationError: null,
  dismissGenerationError: () => set({ generationError: null }),
  regenerationPrevious: {},
  turnRuns: [],
  conversationRuns: [],
  conversationActionBusy: null,

  loadSessions: async () => {
    const fresh = await loadSessionSummaries();
    set((s) => {
      let fieldRevisions = s.sessionFieldRevisions;
      const sessions = fresh.map((x) => {
      const old = s.sessions.find((item) => item.id === x.id);
      if (old && (x.branch_revision ?? 0) < (old.branch_revision ?? 0)) return old;
      const merged = old && x.id === s.currentSessionId ? mergeSessionFields(old, x, x.branch_revision, fieldRevisions) : null;
      if (merged) fieldRevisions = merged.revisions;
      return { ...(merged?.session ?? { ...old, ...x }), pinned_facts: old?.pinned_facts ?? [],
        // A newer list summary has not installed that commit's message details.
        branch_revision: old && x.id === s.currentSessionId ? old.branch_revision : x.branch_revision };
      });
      return { sessions, sessionFieldRevisions: fieldRevisions };
    });
  },

  switchPlayer: async (input) => {
    const sid = get().currentSessionId;
    if (!sid || get().sending || get().switchingPlayer || get().pendingDirector || get().conversationActionBusy || get().conversationRuns.some((run) => isConversationActive(run, sid))) throw new Error('请先暂停交谈，再切换控制角色');
    set({ switchingPlayer: true });
    const loadEpoch = branchLoadEpoch;
    ++messageRequestEpoch;
    invalidateCandidateEpoch();
    try {
      const snapshot = await api.switchPlayer(sid, input);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const revision = get().sessions.find((x) => x.id === sid)?.branch_revision ?? 0;
      if ((snapshot.session.branch_revision ?? 0) < revision) return;
      await resyncSession(sid);
    } catch (error) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) await resyncSession(sid);
      throw error;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set({ switchingPlayer: false });
    }
  },

  loadEarlier: async () => {
    const sid = get().currentSessionId, before = get().nextBeforeSeq;
    if (!sid || before === null || get().historyLoading) return;
    const historyEpoch = ++historyReadEpoch;
    const epoch = branchLoadEpoch;
    set({ historyLoading: true, historyError: null });
    try {
      const page = await api.getStoryView(sid, { before_seq: before });
      if (get().currentSessionId !== sid || branchLoadEpoch !== epoch || historyEpoch !== historyReadEpoch || get().nextBeforeSeq !== before) return;
      set(state => ({ messages: [...page.messages, ...state.messages.filter(message => !page.messages.some(older => older.id === message.id))], nextBeforeSeq: page.next_before_seq ?? null }));
    } catch (cause) {
      if (get().currentSessionId === sid && branchLoadEpoch === epoch && historyReadEpoch === historyEpoch) set({ historyError: cause instanceof Error ? cause.message : '历史读取失败' });
    } finally {
      if (get().currentSessionId === sid && branchLoadEpoch === epoch && historyReadEpoch === historyEpoch) set({ historyLoading: false });
    }
  },
  locateMessage: async (messageId) => {
    const historyEpoch = ++historyReadEpoch;
    const sid = get().currentSessionId;
    if (!sid) return;
    const epoch = branchLoadEpoch;
    set({ locatedMessageId: messageId, historyLoading: true, historyError: null });
    try {
      const page = await api.getStoryView(sid, messageId ? { around: messageId } : {});
      if (get().currentSessionId !== sid || branchLoadEpoch !== epoch || get().locatedMessageId !== messageId || historyReadEpoch !== historyEpoch) return;
      seedGenerations(page.messages, true);
      set(state => ({ messages: page.messages, appliedSnapshotRevision: page.session.branch_revision ?? 0, nextBeforeSeq: page.next_before_seq ?? null, latestSeq: page.latest_seq ?? get().latestSeq, ...installSessionMetadata(state, sid, page.session, page.session.branch_revision, true) }));
    } catch (cause) {
      if (get().currentSessionId === sid && branchLoadEpoch === epoch && historyReadEpoch === historyEpoch) set({ historyError: cause instanceof Error ? cause.message : '定位失败' });
    } finally {
      if (get().currentSessionId === sid && branchLoadEpoch === epoch && historyReadEpoch === historyEpoch) set({ historyLoading: false });
    }
  },

  refreshSession: (id) => resyncSession(id, true),

  selectSession: async (id, updateHistory = true) => {
    const loadEpoch = ++branchLoadEpoch;
    branchRead?.abort();
    const controller = new AbortController();
    branchRead = controller;
    storyConnection.close();
    invalidateCandidateEpoch();
    clearTyping(); // R33：会话切换清空打字缓冲
    swipingIds.clear();
    generations.clear();
    ++messageRequestEpoch;
    freeSendRequestEpoch = null;
    set({ currentSessionId: id, sessionFieldRevisions: get().currentSessionId === id ? get().sessionFieldRevisions : {}, snapshotLoadedSessionId: null, snapshotStatus: 'loading', snapshotError: null, messages: [], nextBeforeSeq: null, latestSeq: -1, appliedSnapshotRevision: 0, locatedMessageId: null, historyLoading: false, historyError: null, sessionCharacters: [], sessionLorebooks: [], sessionGroups: [], lastDecision: null, pendingDirector: null, activeScene: null, inspectTarget: null, sseStatus: 'idle', generationError: null, sending: false, switchingPlayer: false, turnRuns: [], conversationRuns: [], conversationActionBusy: null, regenerationPrevious: {}, ...clearOptions });
    let snapshot;
    try {
      snapshot = await api.getStoryView(id, { signal: controller.signal });
    } catch (cause) {
      if (get().currentSessionId === id && loadEpoch === branchLoadEpoch && !controller.signal.aborted) {
        set({ snapshotStatus: 'error', snapshotError: cause instanceof Error ? cause.message : '路线读取失败' });
      }
      throw cause;
    } finally {
      if (branchRead === controller) branchRead = null;
    }
    // 防止异步竞态：会话已切换则丢弃
    if (get().currentSessionId !== id || loadEpoch !== branchLoadEpoch) return;
    const messages = snapshotMessagesForBranch(snapshot.messages, snapshot.conversation_runs ?? [], id, snapshot.turn_runs ?? []);
    seedGenerations(messages);
    set((s) => ({
      messages,
      appliedSnapshotRevision: snapshot.session.branch_revision ?? 0,
      nextBeforeSeq: snapshot.next_before_seq ?? null,
      latestSeq: snapshot.latest_seq ?? Math.max(-1, ...snapshot.messages.map(message => message.seq)),
      sessionCharacters: snapshot.characters.map(toView),
      sessionLorebooks: snapshot.lorebooks,
      sessionGroups: snapshot.groups,
      conversationRuns: snapshot.conversation_runs ?? [],
      turnRuns: snapshot.turn_runs ?? [],
      sending: Boolean(snapshot.turn_runs?.some((run) => run.session_id === id && run.status === 'running')),
      pendingDirector: snapshot.pending_director ?? null,
      activeScene: snapshot.scenes.find((scene) => scene.id === snapshot.active_scene_id) ?? null,
      snapshotLoadedSessionId: id,
      snapshotStatus: 'ready',
      snapshotError: null,
      ...installSessionMetadata(s, id, snapshot.session, snapshot.session.branch_revision, true),
    }));
    if (updateHistory) {
      const storyId = snapshot.session.story_id ?? id;
      if (readStoryLocation().branchId !== id) writeStoryLocation(storyId, id);
    }
    connectStory(id, snapshot.event_cursor);
  },

  createSession: async (title, characterIds, persona, playerCharacterId) => {
    const session = await api.createSession({ title, character_ids: characterIds, persona, player_character_id: playerCharacterId });
    await Promise.allSettled([get().loadSessions(), get().selectSession(session.id)]);
  },

  regenerateOne: (messageId) => performMessageOperation(messageId, 'one'),
  regenerateDependents: (messageId) => performMessageOperation(messageId, 'dependents'),
  acceptDependencies: (messageId) => performMessageOperation(messageId, 'accept'),

  startConversation: async (participantIds, maxReplies, directive = '') => {
    const sid = get().currentSessionId;
    if (!sid || get().sending || get().conversationActionBusy || get().conversationRuns.some((run) => isConversationActive(run, sid))) return;
    if (!participantIds.length) throw new Error('请先选择要参与交谈的人物或群体');
    const loadEpoch = branchLoadEpoch;
    set({ conversationActionBusy: 'start', generationError: null });
    try {
      if (!storyConnection.isConnected()) await resyncSession(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const session = get().sessions.find((item) => item.id === sid);
      const run = await api.startConversation(sid, { operation_id: createClientId(), participant_ids: participantIds,
        max_replies: maxReplies, directive, expected_branch_revision: session?.branch_revision ?? 0,
        expected_player_identity_id: session?.player_identity_id ?? null });
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set((state) => ({
        conversationRuns: mergeConversationRun(state.conversationRuns, run), ...clearOptions,
      }));
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) {
        set({ generationError: cause instanceof Error ? cause.message : '交谈启动失败' });
        await resyncSession(sid);
      }
      throw cause;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set({ conversationActionBusy: null });
    }
  },

  controlConversation: async (runId, action, additionalReplies) => {
    const sid = get().currentSessionId;
    if (!sid || get().conversationActionBusy || get().switchingPlayer || (action === 'resume' && get().sending)) return;
    const selected = get().conversationRuns.find((run) => run.id === runId);
    if (selected?.session_id && selected.session_id !== sid) throw new Error('这段交谈属于另一条路线，只能查看原记录。');
    const loadEpoch = branchLoadEpoch;
    set({ conversationActionBusy: runId, generationError: null });
    try {
      if (action === 'resume') await resyncSession(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const refreshed = get().conversationRuns.find((run) => run.id === runId);
      if (refreshed?.session_id && refreshed.session_id !== sid) throw new Error('这段交谈属于另一条路线，只能查看原记录。');
      const session = get().sessions.find((item) => item.id === sid);
      const run = action === 'pause' ? await api.pauseConversation(sid, runId)
        : action === 'stop' ? await api.stopConversation(sid, runId)
        : await api.resumeConversation(sid, runId, { expected_branch_revision: session?.branch_revision ?? 0,
          expected_player_identity_id: session?.player_identity_id ?? null, additional_replies: additionalReplies });
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set((state) => {
        const runs = mergeConversationRun(state.conversationRuns, run);
        return { conversationRuns: runs,
          sending: !runs.some((item) => isConversationActive(item, sid)) && freeSendRequestEpoch === messageRequestEpoch ? false : state.sending };
      });
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set({ generationError: cause instanceof Error ? cause.message : '交谈操作失败' });
      throw cause;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set({ conversationActionBusy: null });
    }
  },

  requestGroupReply: async (groupId) => {
    const sid = get().currentSessionId;
    if (!sid || get().sending || get().switchingPlayer || get().conversationActionBusy || get().conversationRuns.some((run) => isConversationActive(run, sid))) return;
    const loadEpoch = branchLoadEpoch;
    const requestEpoch = ++messageRequestEpoch;
    set({ sending: true, generationError: null });
    try {
      if (!storyConnection.isConnected()) await resyncSession(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const message = await api.replyGroup(sid, groupId, createClientId());
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) {
        set((state) => ({ messages: upsert(state.messages, message) }));
      }
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) {
        set({ generationError: cause instanceof Error ? cause.message : '群体回应失败' });
      }
      throw cause;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) set({ sending: false });
    }
  },

  controlTurn: async (operationId, action) => {
    const sid = get().currentSessionId;
    if (!sid || get().conversationActionBusy || get().switchingPlayer) return;
    const selected = get().turnRuns.find((run) => run.operation_id === operationId);
    if (!selected || selected.session_id !== sid) throw new Error('这一轮属于另一条路线，请在原路线恢复。');
    const loadEpoch = branchLoadEpoch;
    const requestEpoch = ++messageRequestEpoch;
    set({ conversationActionBusy: operationId, generationError: null });
    try {
      if (action === 'resume' || !storyConnection.isConnected()) await resyncSession(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const freshRun = get().turnRuns.find((run) => run.operation_id === operationId);
      if (action === 'resume' && (freshRun?.status === 'running' || freshRun?.status === 'completed')) return;
      const session = get().sessions.find((item) => item.id === sid);
      if (action === 'resume') set({ sending: true });
      const result = await api.controlTurn(sid, operationId, action, {
        expected_branch_revision: session?.branch_revision ?? 0,
        expected_player_identity_id: session?.player_identity_id ?? null,
      });
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch || requestEpoch !== messageRequestEpoch) return;
      const prior = get().turnRuns;
      const runs = mergeTurnRun(prior, result.turn_run);
      if (runs !== prior) {
        result.messages.forEach((message) => clearTyping(message.id));
        set((state) => ({ turnRuns: runs,
          messages: reconcileOrdinaryPending(result.messages.reduce(upsert, state.messages), runs, sid),
          generationError: result.errors.length ? result.errors.join('；') : null }));
      }
      await resyncSession(sid);
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) {
        set({ generationError: cause instanceof Error ? cause.message : '本轮操作失败，请重试' });
        await resyncSession(sid);
      }
      throw cause;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) {
        set((state) => ({ conversationActionBusy: null,
          sending: state.turnRuns.some((run) => run.session_id === sid && run.status === 'running')
            || state.conversationRuns.some((run) => isConversationActive(run, sid)) }));
      }
    }
  },

  sendMessage: async (content, mentions, channel, replyMode, maxReplies, conversationDirective) => {
    const sid = get().currentSessionId;
    if (!sid || get().sending || get().switchingPlayer || get().conversationActionBusy || get().conversationRuns.some((run) => isConversationActive(run, sid))) return;
    const loadEpoch = branchLoadEpoch;
    const requestEpoch = ++messageRequestEpoch;
    if (replyMode === 'free') freeSendRequestEpoch = requestEpoch;
    const identityId = get().sessions.find((x) => x.id === sid)?.player_identity_id;
    const identity = get().sessions.find((x) => x.id === sid)?.player_identities?.find((x) => x.id === identityId);
    invalidateCandidateEpoch();
    const clientMessageId = `msg-${createClientId().replace(/-/g, '')}`;
    if (replyMode !== 'free') ordinarySendOperationId = clientMessageId;
    if (get().locatedMessageId) await get().locateMessage(null);
    const prior = get().messages;
    const parts = channel === 'inner'
      ? [{ kind: 'inner' as const, text: content }]
      : parseChannelParts(content, channel === 'narration' ? 'scene' : 'roleplay');
    const hasPublic = parts.some((part) => part.kind !== 'inner');
    const position = prior.reduce(
      (current, message) => ({ seq: Math.max(current.seq, message.seq), turn: Math.max(current.turn, message.turn) }),
      { seq: get().latestSeq, turn: get().sessions.find(session => session.id === sid)?.turn ?? 0 },
    );
    const optimistic: Message[] = parts.map((part, index) => ({
      id: index === 0 ? clientMessageId : `local-channel-${clientMessageId}-${index}`,
      input_group_id: `input-${clientMessageId}`,
      session_id: sid,
      seq: position.seq + index + 1,
      turn: position.turn + 1,
      actor: 'player',
      player_identity_id: identityId,
      person_id: identity?.person_id,
      content: part.text,
      kind: part.kind,
      visible_to: part.kind === 'inner' ? ['player'] : 'all',
      status: 'pending',
      fingerprint: '',
      generation_meta: null,
      created_at: new Date().toISOString(),
      variants: [], active_variant: null, edited: false, hygiene: null,
      scene_id: get().activeScene?.id ?? null,
      mentions: part.kind === 'inner' ? [] : mentions ?? [],
      reply_mode: part.kind === 'inner' ? null : replyMode ?? 'auto',
    }));
    set((s) => ({
      messages: [...s.messages, ...optimistic],
      sending: hasPublic ? true : s.sending,
      generationError: null,
      ...clearOptions,
    }));
    try {
      // Restore a closed subscription before generation; the snapshot cursor
      // covers events between reading the snapshot and opening EventSource.
      if (!storyConnection.isConnected()) await resyncSession(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch || requestEpoch !== messageRequestEpoch) return;
      const returned = await api.sendMessage(sid, content, mentions, channel, clientMessageId, replyMode ?? 'auto', identityId, maxReplies,
        replyMode === 'free' ? conversationDirective : undefined);
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) {
        const existingRuns = get().turnRuns;
        const incomingRuns = returned.turn_run ? mergeTurnRun(existingRuns, returned.turn_run) : existingRuns;
        if (returned.turn_run && incomingRuns === existingRuns) return;
        returned.messages.forEach((message) => clearTyping(message.id));
        const confirmed = new Set(returned.messages.map((message) => message.id));
        const confirmedTurn = returned.messages.find((message) => message.id === clientMessageId)?.turn ?? optimistic[0].turn;
        set((s) => ({
          messages: returned.messages.reduce(
            (acc, message) => upsert(acc, message),
            s.messages.filter((message) =>
              !(message.turn === confirmedTurn && message.status === 'pending' && !confirmed.has(message.id)),
            ),
          ),
          generationError: returned.errors.length ? returned.errors.join('；') : null,
          conversationRuns: returned.conversation_run ? mergeConversationRun(s.conversationRuns, returned.conversation_run) : s.conversationRuns,
          turnRuns: returned.turn_run ? mergeTurnRun(s.turnRuns, returned.turn_run) : s.turnRuns,
        }));
      }
    } catch (error) {
      // 请求出错也可能已经在服务端落账，先读权威快照再决定是否撤销即时气泡。
      const snapshot = await api.getStoryView(sid).catch(() => null);
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) {
        if (snapshot) {
          const turnRuns = (snapshot.turn_runs ?? []).reduce(mergeTurnRun, get().turnRuns);
          const messages = snapshotMessagesForBranch(snapshot.messages, snapshot.conversation_runs ?? [], sid, turnRuns);
          seedGenerations(messages);
          set((state) => ({ messages, appliedSnapshotRevision: snapshot.session.branch_revision ?? 0, nextBeforeSeq: snapshot.next_before_seq ?? null, latestSeq: snapshot.latest_seq ?? state.latestSeq,
            conversationRuns: (snapshot.conversation_runs ?? []).reduce(mergeConversationRun, state.conversationRuns),
            turnRuns,
            pendingDirector: snapshot.pending_director ?? null,
            ...installSessionMetadata(state, sid, snapshot.session, snapshot.session.branch_revision, true) }));
        } else {
          const optimisticIds = new Set(optimistic.map((message) => message.id));
          set((s) => ({ messages: s.messages.filter((message) => !optimisticIds.has(message.id)) }));
        }
        set({ generationError: error instanceof Error ? error.message : '发送失败，请重试' });
      }
      throw error;
    } finally {
      if (hasPublic && get().currentSessionId === sid && loadEpoch === branchLoadEpoch && requestEpoch === messageRequestEpoch) {
        set((state) => ({ sending: state.turnRuns.some((run) => run.session_id === sid && run.status === 'running') }));
      }
      if (freeSendRequestEpoch === requestEpoch) freeSendRequestEpoch = null;
      if (ordinarySendOperationId === clientMessageId) ordinarySendOperationId = null;
    }
  },

  generateCandidates: async () => {
    const sid = get().currentSessionId;
    if (!sid || get().generatingCandidates) return;
    const epoch = candidateEpoch;
    set({ generatingCandidates: true });
    try {
      const batch = await api.generateCandidates(sid);
      if (get().currentSessionId !== sid || epoch !== candidateEpoch) return;
      const branchRevision = get().sessions.find((session) => session.id === sid)?.branch_revision;
      if (batch.reason === 'stale' || (batch.options?.length && !isAssistBatchCurrent(batch, sid, get().messages, branchRevision))) {
        invalidateCandidateEpoch();
        set({ ...clearOptions, optionsNotice: '生成期间对话发生变化，这批候选已过期，请重新生成。' });
      } else if (batch.options?.length) applyAssistBatch(batch);
      else set({ ...clearOptions, optionsNotice: batch.reason === 'control_boundary'
        ? '已切换控制角色，请先以当前身份发送一条新消息，再生成接话候选。' : null });
    } catch {
      // 静默（失败不弹错；面板保持原状/空态）
    } finally {
      set({ generatingCandidates: false });
    }
  },

  dismissOptions: () => { invalidateCandidateEpoch(); set({ ...clearOptions }); },

  requestInspect: (characterId, turn, messageId, generationId) => {
    useStoryUiStore.getState().openPanel('inspect');
    set({ inspectTarget: { characterId, turn, messageId, generationId } });
  },

  swipe: async (messageId) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const loadEpoch = branchLoadEpoch;
    const baseRevision = get().sessions.find(session => session.id === sid)?.branch_revision ?? 0;
    // R32.4 失败保护：发起前快照，失败回写（后端也已回滚，双保险对齐 UI）
    const before = get().messages.find((m) => m.id === messageId) ?? null;
    if (!before || before.status !== 'final') return;
    swipingIds.add(messageId);
    clearTyping(messageId);
    set((s) => ({ messages: s.messages.map((message) => message.id === messageId
      ? { ...message, content: '', status: 'pending' as const } : message) }));
    try {
      const fresh = await api.swipeMessage(sid, messageId);
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message: fresh }, fresh));
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch
        && (get().sessions.find(session => session.id === sid)?.branch_revision ?? 0) <= baseRevision) {
        set((s) => ({ messages: upsert(s.messages, before) }));
      }
      if (cause instanceof ApiError && cause.code === 'operation_incomplete') throw new Error('前次操作未完整提交，请检查当前结果。重复点击不会重新生成；需要继续时请使用明确的恢复操作。');
      throw new Error('重roll 失败，原候选已保留');
    } finally {
      swipingIds.delete(messageId);
    }
  },

  swipeGroup: async (groupId, messageId) => {
    const sid = get().currentSessionId;
    const before = get().messages.find((message) => message.id === messageId);
    if (!sid || !before || before.status !== 'final') return;
    const loadEpoch = branchLoadEpoch;
    const baseRevision = get().sessions.find(session => session.id === sid)?.branch_revision ?? 0;
    swipingIds.add(messageId);
    clearTyping(messageId);
    set((s) => ({ messages: s.messages.map((message) => message.id === messageId
      ? { ...message, content: '', status: 'pending' as const } : message) }));
    try {
      const fresh = await api.swipeGroup(sid, groupId, messageId);
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message: fresh }, fresh));
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch
        && (get().sessions.find(session => session.id === sid)?.branch_revision ?? 0) <= baseRevision) set((s) => ({ messages: upsert(s.messages, before) }));
      if (cause instanceof ApiError && cause.code === 'operation_incomplete') throw new Error('前次操作未完整提交，请检查当前结果。重复点击不会重新生成；需要继续时请使用明确的恢复操作。');
      throw new Error('群体重新生成失败，原候选已保留');
    } finally {
      swipingIds.delete(messageId);
    }
  },

  regenerate: async (messageId) => {
    const sid = get().currentSessionId;
    if (!sid || get().sending) return;
    invalidateCandidateEpoch();
    const loadEpoch = branchLoadEpoch;
    const before = get().messages;
    const index = before.findIndex((message) => message.id === messageId);
    if (index < 0) return;
    const player = before[index];
    const tail = before.slice(index + 1);
    const activeActors = new Set<string>([
      ...get().sessionCharacters.filter((character) => character.present).map((character) => character.id),
      ...get().sessionGroups.filter((group) => group.status === 'active'
        && (!get().activeScene || group.scene_id === get().activeScene?.id)).map((group) => group.id),
    ]);
    const speakers = [...new Set([
      ...tail.map((message) => message.actor),
      ...player.mentions,
    ].filter((actor) => activeActors.has(actor)))];
    const pending = speakers.map((actor, offset): Message => ({
      id: `local-regenerate-${createClientId()}`,
      session_id: sid,
      seq: player.seq + offset + 1,
      turn: player.turn,
      actor,
      content: '',
      kind: 'roleplay',
      visible_to: 'all',
      status: 'pending',
      fingerprint: '',
      generation_meta: null,
      created_at: new Date().toISOString(),
      variants: [], active_variant: null, edited: false, hygiene: null,
      scene_id: player.scene_id,
      mentions: [],
    }));
    tail.forEach((message) => clearTyping(message.id));
    set({
      messages: [...before.slice(0, index + 1), ...pending],
      sending: true, generationError: null, ...clearOptions,
    });
    try {
      const fresh = await api.regenerateMessage(sid, messageId);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      fresh.messages.forEach(message => get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message }, fresh)));
      set({ generationError: fresh.errors.length ? fresh.errors.join('；') : null });
      await resyncSession(sid, false, true);
    } catch (error) {
      const snapshot = await api.getStoryView(sid).catch(() => null);
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) {
        clearTyping();
        set({ messages: snapshot?.messages ?? before, nextBeforeSeq: snapshot?.next_before_seq ?? get().nextBeforeSeq, latestSeq: snapshot?.latest_seq ?? get().latestSeq,
          generationError: error instanceof Error ? error.message : '重新生成失败' });
      }
      throw error;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set({ sending: false });
    }
  },

  editMessage: async (messageId, content, expectedBranchRevision, expectedFingerprint) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const epoch = branchLoadEpoch;
    invalidateCandidateEpoch();
    const current = get().messages.find((message) => message.id === messageId);
    const revision = expectedBranchRevision ?? get().sessions.find((session) => session.id === sid)?.branch_revision;
    const fingerprint = expectedFingerprint ?? current?.fingerprint;
    const fresh = await api.editMessage(sid, messageId, content, revision, fingerprint);
    if (get().currentSessionId !== sid || branchLoadEpoch !== epoch) return;
    get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message: fresh }, fresh));
    void get().loadSessions().catch(() => undefined);
  },

  editInputGroup: async (messageId, parts, expectedBranchRevision) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const epoch = branchLoadEpoch;
    invalidateCandidateEpoch();
    const result = await api.editInputGroup(sid, messageId, {
      expected_branch_revision: expectedBranchRevision,
      parts,
    });
    if (get().currentSessionId !== sid || branchLoadEpoch !== epoch) return;
    for (const message of result.messages) get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message }, result, result.branch_revision));

  },

  switchVariant: async (messageId, activeVariant) => {
    const sid = get().currentSessionId;
    const before = get().messages.find((message) => message.id === messageId);
    if (!sid || !before || before.status !== 'final' || get().sending || get().conversationActionBusy || get().conversationRuns.some((run) => isConversationActive(run, sid))) return;
    const loadEpoch = branchLoadEpoch;
    ++messageRequestEpoch;
    invalidateCandidateEpoch();
    set({ sending: true });
    try {
      const fresh = await api.switchVariant(sid, messageId, activeVariant, messageOperationInput(sid, before));
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message: fresh }, fresh));
      await resyncSession(sid);
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) set({ sending: false });
    }
  },

  deleteMessage: async (messageId) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const epoch = branchLoadEpoch;
    invalidateCandidateEpoch();
    const receipt = await api.deleteMessage(sid, messageId, get().sessions.find(session => session.id === sid)?.branch_revision);
    if (get().currentSessionId !== sid || branchLoadEpoch !== epoch) return;
    get().handleSseEvent(httpCommandEvent({ type: 'message.deleted', message_id: messageId, turn: 0 }, receipt));
  },

  createPinnedFact: async (input) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const loadEpoch = branchLoadEpoch;
    const fact = await api.createPinnedFact(sid, input);
    if (get().currentSessionId !== sid || branchLoadEpoch !== loadEpoch) return;
    const receipt = commandReceipt(fact);
    const factKey = `${loadEpoch}:${sid}:${fact.id}`;
    if (receipt && receipt.branch_revision < Math.max(get().sessions.find(session => session.id === sid)?.branch_revision ?? 0, factReceiptRevisions.get(factKey) ?? 0)) return;
    if (receipt) factReceiptRevisions.set(factKey, receipt.branch_revision);
    set((s) => ({
      sessions: s.sessions.map((session) => session.id === sid
        ? { ...session, pinned_facts: [...(session.pinned_facts ?? []).filter((item) => item.id !== fact.id), fact] }
        : session),
    }));
  },

  updatePinnedFact: async (factId, content) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const loadEpoch = branchLoadEpoch;
    const fact = await api.updatePinnedFact(sid, factId, content);
    if (get().currentSessionId !== sid || branchLoadEpoch !== loadEpoch) return;
    const receipt = commandReceipt(fact);
    const factKey = `${loadEpoch}:${sid}:${fact.id}`;
    if (receipt && receipt.branch_revision < Math.max(get().sessions.find(session => session.id === sid)?.branch_revision ?? 0, factReceiptRevisions.get(factKey) ?? 0)) return;
    if (receipt) factReceiptRevisions.set(factKey, receipt.branch_revision);
    set((s) => ({
      sessions: s.sessions.map((session) => session.id === sid
        ? { ...session, pinned_facts: (session.pinned_facts ?? []).map((item) => item.id === fact.id ? fact : item) }
        : session),
    }));
  },

  deletePinnedFact: async (factId) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const loadEpoch = branchLoadEpoch;
    const fact = await api.deletePinnedFact(sid, factId);
    if (get().currentSessionId !== sid || branchLoadEpoch !== loadEpoch) return;
    const receipt = commandReceipt(fact);
    const factKey = `${loadEpoch}:${sid}:${factId}`;
    if (receipt && receipt.branch_revision < Math.max(get().sessions.find(session => session.id === sid)?.branch_revision ?? 0, factReceiptRevisions.get(factKey) ?? 0)) return;
    if (receipt) factReceiptRevisions.set(factKey, receipt.branch_revision);
    set((s) => ({
      sessions: s.sessions.map((session) => session.id === sid
        ? { ...session, pinned_facts: (session.pinned_facts ?? []).filter((item) => item.id !== factId) }
        : session),
    }));
  },

  recheckHygiene: async (messageId) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const loadEpoch = branchLoadEpoch;
    const fresh = await api.recheckHygiene(sid, messageId);
    if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
    get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message: fresh }, fresh));
  },

  confirmDirector: async () => {
    const sid = get().currentSessionId;
    if (!sid || get().sending) return;
    const loadEpoch = branchLoadEpoch;
    set({ sending: true, generationError: null });
    try {
      const created = await api.confirmDirector(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const receipt = commandReceipt(created);
      if (receipt && receipt.branch_revision < (get().sessions.find(session => session.id === sid)?.branch_revision ?? 0)) return;
      created.forEach(message => get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message }, created)));
      set({ pendingDirector: null });
      await resyncSession(sid);
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) await resyncSession(sid);
      throw cause;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) {
        set((state) => ({ sending: state.turnRuns.some((run) => run.session_id === sid && run.status === 'running') }));
      }
    }
  },

  rejectDirector: async () => {
    const sid = get().currentSessionId;
    if (!sid || get().sending) return;
    const loadEpoch = branchLoadEpoch;
    set({ sending: true, generationError: null });
    try {
      const created = await api.rejectDirector(sid);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      const receipt = commandReceipt(created);
      if (receipt && receipt.branch_revision < (get().sessions.find(session => session.id === sid)?.branch_revision ?? 0)) return;
      created.forEach(message => get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message }, created)));
      set({ pendingDirector: null });
      await resyncSession(sid);
    } catch (cause) {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) await resyncSession(sid);
      throw cause;
    } finally {
      if (get().currentSessionId === sid && loadEpoch === branchLoadEpoch) {
        set((state) => ({ sending: state.turnRuns.some((run) => run.session_id === sid && run.status === 'running') }));
      }
    }
  },

  continueMessage: async (messageId) => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const loadEpoch = branchLoadEpoch;
    const baseRevision = get().sessions.find(session => session.id === sid)?.branch_revision ?? 0;
    const before = get().messages.find((m) => m.id === messageId) ?? null;
    try {
      const fresh = await api.continueMessage(sid, messageId);
      if (get().currentSessionId !== sid || loadEpoch !== branchLoadEpoch) return;
      get().handleSseEvent(httpCommandEvent({ type: 'message.updated', message: fresh }, fresh));
    } catch (cause) {
      if (before && get().currentSessionId === sid && loadEpoch === branchLoadEpoch && (get().sessions.find(session => session.id === sid)?.branch_revision ?? 0) <= baseRevision) set((s) => ({ messages: upsert(s.messages, before) }));
      if (cause instanceof ApiError && cause.code === 'operation_incomplete') throw new Error('前次操作未完整提交，请检查当前结果。重复点击不会重新生成；需要继续时请使用明确的恢复操作。');
      throw new Error('续写失败，原内容已保留');
    }
  },

  applyRestore: (session, _messages, _characters = [], lorebooks = [], groups = []) => {
    const receipt = commandReceipt(session);
    const installed = get().sessions.find(item => item.id === session.id)?.branch_revision ?? 0;
    if (receipt && receipt.branch_revision < installed) return;
    clearTyping(); // R33
    swipingIds.clear();
    storyConnection.close();
    seedGenerations([]);
    set((s) => ({
      ...installSessionMetadata({ ...s, currentSessionId: session.id, sessionFieldRevisions: s.currentSessionId === session.id ? s.sessionFieldRevisions : {} }, session.id, session, session.branch_revision, true),
      currentSessionId: session.id,
      snapshotLoadedSessionId: null,
      messages: [],
      nextBeforeSeq: null, latestSeq: -1, appliedSnapshotRevision: 0, locatedMessageId: null,
      sessionCharacters: [],
      sessionLorebooks: lorebooks,
      sessionGroups: groups,
      lastDecision: null,
      conversationRuns: [],
      turnRuns: [],
      pendingDirector: null,
      regenerationPrevious: {},
      conversationActionBusy: null,
      sending: false,
      sseStatus: 'idle',
    }));
    // Restore uses the same cancellable snapshot lifecycle as ordinary navigation.
    void get().selectSession(session.id, false).catch(() => { /* Visible retry state is set by selectSession. */ });
  },

  handleSseEvent: (evt) => {
    const current = get(), session = current.sessions.find(session => session.id === current.currentSessionId);
    const previousAttempts = new Map(generations);
    const reduction = reduceStoryEvent({ branchId: current.currentSessionId, revision: session?.branch_revision ?? 0,
      snapshotRevision: current.appliedSnapshotRevision, seenOutboxIds: acceptedOutboxEvents, messages: current.messages, latestSeq: current.latestSeq,
      turn: session?.turn ?? 0, locatedMessageId: current.locatedMessageId, turnRuns: current.turnRuns,
      attempts: generations, regenerationPrevious: current.regenerationPrevious }, evt);
    acceptedOutboxEvents = reduction.state.seenOutboxIds;
    if (reduction.effects.includes('resync') && current.currentSessionId
      && (!eventResync || eventResync.branchId !== current.currentSessionId || eventResync.epoch !== branchLoadEpoch)) {
      const branchId = current.currentSessionId, epoch = branchLoadEpoch;
      const recovery = { branchId, epoch, promise: undefined as Promise<void> | undefined };
      eventResync = recovery;
      recovery.promise = Promise.resolve().then(() => {
        if (get().currentSessionId === branchId && branchLoadEpoch === epoch) return resyncSession(branchId, true, true);
      }).catch(cause => {
        if (get().currentSessionId === branchId && branchLoadEpoch === epoch) set({ generationError: cause instanceof Error ? cause.message : '对话同步失败，请重新读取' });
      }).finally(() => { if (eventResync === recovery) eventResync = null; });
    }
    if (!reduction.accepted) return;
    if (reduction.state.attempts !== generations) {
      generations.clear();
      for (const [id, slot] of reduction.state.attempts) generations.set(id, { ...slot, retired: new Set(slot.retired) });
    }
    evt = reduction.event;
    set(state => ({ latestSeq: reduction.state.latestSeq, sessions: state.sessions.map(session => session.id === current.currentSessionId
      ? { ...session, branch_revision: reduction.state.revision, turn: reduction.state.turn } : session) }));
    if (!reduction.deliver) return;
    const sid = get().currentSessionId;
    switch (evt.type) {
      case 'message.pending': {
        if (evt.message.session_id !== sid) return;
        const identity = { ...evt.message, ...evt };
        if (!acceptTurnEvent(evt.message.id, identity, 'pending')) return;
        const previous = previousAttempts.get(evt.message.id);
        const sameAttempt = previous?.generation_id && streamKey(previous) === streamKey(identity);
        if (!sameAttempt) clearTyping(evt.message.id);
        invalidateCandidateEpoch();
        set({ ...clearOptions });
        set((s) => {
          const current = s.messages.find((message) => message.id === evt.message.id);
          if (current?.status === 'final' && !identity.generation_id && !swipingIds.has(evt.message.id)) return { messages: s.messages };
          const incoming = { ...evt.message, generation_id: identity.generation_id, attempt_id: identity.attempt_id, operation_id: identity.operation_id };
          return { regenerationPrevious: current?.status === 'final' && identity.generation_id
            ? { ...s.regenerationPrevious, [current.id]: current } : s.regenerationPrevious,
          messages: replaceLocalPending(s.messages,
            sameAttempt && current?.status === 'pending' && current.content.length > incoming.content.length
              ? { ...incoming, content: current.content } : incoming) };
        });
        break;
      }
      case 'director.decision':
        set({ lastDecision: evt.decision });
        break;
      case 'message.final':
        if (evt.message.session_id !== sid) return;
        if (!acceptTurnEvent(evt.message.id, { ...evt.message, ...evt }, 'final')) return;
        invalidateCandidateEpoch();
        set((state) => { const previous = { ...state.regenerationPrevious }; delete previous[evt.message.id];
          return { ...clearOptions, regenerationPrevious: previous }; });
        if (typing.has(evt.message.id)) {
          // R33：仍在逐字释放——final 进持有槽，排空后提交（不半路替换）
          typing.get(evt.message.id)!.final = evt.message;
          if (rafId == null) rafId = requestAnimationFrame(pumpTyping);
        } else {
          set({
            messages: reduction.state.messages,
          });
        }
        refreshSessionSummariesSoon();
        break;
      case 'message.delta':
        if (!get().messages.some((message) => message.id === evt.message_id)) return;
        if (!acceptTurnEvent(evt.message_id, evt, 'delta')) return;
        bufferDelta(evt);
        break;
      case 'turn.run.updated': {
        if (evt.run.session_id !== sid) return;
        const current = get();
        const runs = mergeTurnRun(current.turnRuns, evt.run);
        const messages = reconcileOrdinaryPending(current.messages, runs, sid);
        for (const old of current.messages) if (!messages.some((message) => message.id === old.id)) clearTyping(old.id);
        set((state) => ({ turnRuns: runs, messages,
          generationError: runs !== current.turnRuns && (evt.run.status === 'completed'
            || (evt.run.status === 'running' && evt.run.epoch > (current.turnRuns.find((run) => run.operation_id === evt.run.operation_id)?.epoch ?? -1)))
            ? null : state.generationError,
          pendingDirector: runs !== current.turnRuns && evt.run.status === 'running' ? null : state.pendingDirector,
          sending: runs.some((run) => run.session_id === sid && run.status === 'running') ? true
            : (!ordinarySendOperationId || ordinarySendOperationId === evt.run.operation_id)
              && !state.conversationRuns.some((run) => isConversationActive(run, sid)) ? false : state.sending,
          sessions: state.sessions.map((session) => session.id === sid ? { ...session,
            branch_revision: Math.max(session.branch_revision ?? 0, evt.branch_revision) } : session) }));
        if (runs !== current.turnRuns && evt.run.status === 'completed'
            && messages.some((message) => message.status === 'pending' && message.operation_id === evt.run.operation_id)) {
          void resyncSession(sid);
        }
        break;
      }
      case 'conversation.run.updated':
        if (evt.run.session_id && evt.run.session_id !== sid) return;
        set((state) => {
          const runs = mergeConversationRun(state.conversationRuns, evt.run);
          return {
          conversationRuns: runs,
          sessions: state.sessions.map((session) => session.id === sid
            ? { ...session, branch_revision: Math.max(session.branch_revision ?? 0, evt.branch_revision) } : session),
          sending: evt.run.mode === 'free' && !runs.some((item) => isConversationActive(item, sid)) && freeSendRequestEpoch === messageRequestEpoch ? false : state.sending,
        }; });
        break;
      case 'scene.switched':
        // R35：过渡消息经 message.final 到达——此事件仅作信号
        // M12-R41：旧候选立即失效（点击生成才有新的）；同步当前场景（面板展示）
        invalidateCandidateEpoch();
        set({ ...clearOptions, activeScene: evt.new_scene });
        break;
      case 'director.pending':
        set({ pendingDirector: evt.decision });
        break;
      case 'reply.plan':
        set({ messages: reduction.state.messages });
        break;
      case 'session.player.changed':
        invalidateCandidateEpoch();
        if (sid) void resyncSession(sid);
        break;
      case 'session.roster.changed':
        invalidateCandidateEpoch();
        set({ ...clearOptions });
        if (sid) void api.getStoryView(sid).then((snapshot) => {
          if (get().currentSessionId !== sid) return;
          if ((snapshot.session.branch_revision ?? 0) < (get().sessions.find((x) => x.id === sid)?.branch_revision ?? 0)) return;
          set((state) => ({
            sessionCharacters: snapshot.characters.map(toView),
            sessionLorebooks: snapshot.lorebooks,
            sessionGroups: snapshot.groups,
            activeScene: snapshot.scenes.find((scene) => scene.id === snapshot.active_scene_id) ?? null,
            ...installSessionMetadata(state, sid, snapshot.session, snapshot.session.branch_revision, true),
          }));
        }).catch(() => undefined);
        break;
      case 'message.retracted':
        invalidateCandidateEpoch();
        set({ ...clearOptions, messages: reduction.state.messages });
        break;
      case 'message.updated':
        clearTyping(evt.message.id);
        invalidateCandidateEpoch();
        set({ ...clearOptions, messages: reduction.state.messages });
        break;
      case 'message.deleted':
        clearTyping(evt.message_id);
        invalidateCandidateEpoch();
        set({ ...clearOptions, messages: reduction.state.messages });
        break;
      case 'message.error':
        // R32.4/R45：生成失败——后端已回滚。swipe/续写恢复原内容为 final；
        // 全新的空 pending（普通回合/重跑失败）直接移除，不留幽灵气泡。
        if (!acceptTurnEvent(evt.message_id, evt, 'error')) return;
        clearTyping(evt.message_id);
        invalidateCandidateEpoch();
        set((s) => ({
          ...clearOptions,
          generationError: evt.error,
          messages: evt.message || s.regenerationPrevious[evt.message_id]
            ? upsert(s.messages, evt.message ?? s.regenerationPrevious[evt.message_id])
            : s.messages.filter((m) => !(m.id === evt.message_id
                && (evt.discard_pending || ordinaryOwner(s.turnRuns, evt) || m.content === '')))
              .map((m) => (m.id === evt.message_id ? { ...m, status: 'final' as const } : m)),
        }));
        set((state) => { const previous = { ...state.regenerationPrevious }; delete previous[evt.message_id]; return { regenerationPrevious: previous }; });
        break;
      case 'cost.update':
        set({ costByModel: evt.cost_by_model });
        break;
      case 'heartbeat':
        set({ sseStatus: 'open' });
        break;
    }
  },

  disconnect: () => {
    ++branchLoadEpoch;
    factReceiptRevisions.clear();
    branchRead?.abort();
    branchRead = null;
    clearTyping(); // R33
    if (sessionRefreshTimer != null) clearTimeout(sessionRefreshTimer);
    sessionRefreshTimer = null;
    storyConnection.close();
    set((s) => ({ sseStatus: 'closed', snapshotStatus: s.snapshotStatus === 'loading' ? 'idle' : s.snapshotStatus }));
  },
});
});
