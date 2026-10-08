import { Button } from '../design-system/Button';
import { BubbleFrame } from '../appearance/BubbleFrame';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ArrowLeft, ArrowRight, BookOpen, Bookmark, Brain, Clapperboard, Clipboard, Eye, GitBranch, Pencil, Pin, RefreshCw, Trash2 } from '../design-system/Icon';
import { storiesClient as api } from '../features/stories/storiesClient';
import { isConversationActive, isSwipingMessage, useChatStore } from '../store/chatStore';
import '../conversation.css';
import { useSettingsStore } from '../store/settingsStore';
import { useActiveCharacters } from '../store/useActiveCharacters';
import { toView } from '../api/adapters';
import { resolvePlayerIdentity, isBeforePlayerBoundary, playerAvatarUrl } from '../utils/playerIdentity.js';
import { PlayerRoleProfile } from './PlayerRoleProfile';
import type { Message } from '../types';
import type { InputGroupEditPart } from '../types';
import { Avatar, COLOR_CLASSES, Modal } from './common';
import { CharacterInfoModal } from './CharacterInfoModal';
import { DeleteMessageModal, EditMessageModal, EditPlayerInputModal } from './EditMessageModal';
import { HygieneModal } from './HygieneModal';
import { RememberMemoryModal } from './RememberMemoryModal';
import { generationIdForMessage } from '../utils/inspectionTarget.js';
import { MessageActionBar, type MessageAction, type MessageSpeech } from './MessageActionBar';

export function MessageBubble({ message, logicalInputMessages, onFork, onEvent, onEdit, onEditInputGroup, bookmarked, onBookmark }: {
  message: Message;
  logicalInputMessages?: Message[];
  onFork?: (message: Message) => Promise<void>;
  onEvent?: (message: Message) => Promise<void>;
  onEdit?: (message: Message, content: string, branchRevision: number, fingerprint: string) => Promise<void>;
  onEditInputGroup?: (messageId: string, parts: InputGroupEditPart[], branchRevision: number) => Promise<void>;
  bookmarked?: boolean;
  onBookmark?: (message: Message) => Promise<void>;
}) {
  const characters = useActiveCharacters();
  const groups = useChatStore((s) => s.sessionGroups);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const session = useChatStore((s) => s.sessions.find((item) => item.id === s.currentSessionId));
  const playerIdentity = resolvePlayerIdentity(session, message);
  const playerCharacter = playerIdentity?.character ? toView(playerIdentity.character) : null;
  const beforeBoundary = isBeforePlayerBoundary(session, message);
  const charIds = session?.character_ids;
  const branchRevision = session?.branch_revision ?? 0;
  const isLast = useChatStore((s) => message.seq === s.latestSeq);
  const allMessages = useChatStore((s) => s.messages);
  const sending = useChatStore((s) => s.sending);
  const runActive = useChatStore((s) => s.conversationRuns.some((run) => isConversationActive(run, s.currentSessionId)));
  const operationBusy = useChatStore((s) => Boolean(s.conversationActionBusy));
  const previousCandidate = useChatStore((s) => s.regenerationPrevious[message.id]);
  const mutationBusy = sending || runActive || operationBusy;
  const activeScene = useChatStore((s) => s.activeScene);
  const inputParts = logicalInputMessages?.length ? logicalInputMessages : [message];
  const latestSeq = useChatStore((s) => s.latestSeq);
  const inputHasFuture = inputParts[inputParts.length - 1].seq < latestSeq;
  const pinnedFact = session?.pinned_facts?.find((fact) => fact.source_message_id === message.id);
  const isPlayer = message.actor === 'player';
  const formerCharacter = session?.player_people?.[message.actor];
  const character = characters.find((c) => c.id === message.actor) ?? (formerCharacter ? toView(formerCharacter) : undefined);
  const group = groups.find((item) => item.id === message.actor);
  const name = isPlayer ? playerIdentity?.name ?? '玩家' : character?.card.name ?? group?.label ?? message.actor;
  const color = character?.color ?? (group ? 'emerald' : 'slate');
  const ttsEnabled = useSettingsStore((s) => s.settings?.tts.enabled ?? false);

  // Message operation errors persist until dismissal or another operation.
  const [modal, setModal] = useState<'edit' | 'delete' | 'hygiene' | 'pin' | 'remember' | null>(null);
  const [editExpectedRevision, setEditExpectedRevision] = useState<number | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ttsBusy, setTtsBusy] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [emotion, setEmotion] = useState('');
  const audioRef = useRef<HTMLAudioElement>(null);
  useEffect(() => () => { if (audioUrl) URL.revokeObjectURL(audioUrl); }, [audioUrl]);

  // 只有真实角色的消息可重roll（排除玩家/导演消息）
  const latestTurn = session?.turn ?? 0;
  const myIdx = allMessages.findIndex((m) => m.id === message.id);
  const after = myIdx >= 0 ? allMessages.slice(myIdx + 1) : [];
  const laterInput = after.some((item) => Boolean(item.control_event) || (item.actor === 'player' && (item.kind === 'roleplay' || item.kind === 'scene')));
  const controlledPersonId = resolvePlayerIdentity(session)?.person_id;
  const currentInteraction = message.turn === latestTurn && (!activeScene || message.scene_id === activeScene.id);
  const eligibleReply = message.kind === 'roleplay' && message.status === 'final' && currentInteraction && !beforeBoundary && !laterInput && !isPlayer && message.actor !== 'director';
  const canSwipe = character != null && character.present && !character.muted && character.id !== controlledPersonId && eligibleReply;
  const canSwipeGroup = group != null && group.status === 'active' && (!activeScene || group.scene_id === activeScene.id) && eligibleReply;
  const canEdit = message.status === 'final' && !message.control_event;
  const canPin = message.status === 'final' && message.kind !== 'inner' && message.kind !== 'system_event';

  // R45：玩家消息"重新生成"仅限最后一轮（其后只有本回合的非玩家消息）
  const lastPlayer = message.turn === latestTurn && !after.some((m) => m.actor === 'player');
  const showRegen = isPlayer && message.status === 'final' && message.kind !== 'inner';
  const canRegen = showRegen && lastPlayer && after.every((m) => m.turn === message.turn) && !beforeBoundary;
  const regenHint = beforeBoundary ? '已切换控制角色，切换前的回复不能重新生成' : '重新生成：仅最后一轮可用';

  const runAction = async (fn: () => Promise<void>) => {
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '操作失败');
    }
  };
  const speak = async () => {
    if (!ttsEnabled || ttsBusy || message.status !== 'final' || !message.content.trim()) return;
    if (audioUrl && audioRef.current) {
      if (playing) { audioRef.current.pause(); setPlaying(false); }
      else { await audioRef.current.play(); setPlaying(true); }
      return;
    }
    setTtsBusy(true);
    try {
      const url = await api.synthesizeSpeech({ text: message.content, emotion });
      setAudioUrl(url);
      window.setTimeout(() => { if (audioRef.current) void audioRef.current.play().then(() => setPlaying(true)).catch((cause) => {
        setPlaying(false); setErr(cause instanceof Error ? cause.message : '播放失败，请重试朗读。');
      }); }, 0);
    } catch (cause) { setErr(cause instanceof Error ? cause.message : '语音生成失败'); }
    finally { setTtsBusy(false); }
  };
  const copyMessage = () => navigator.clipboard.writeText(message.content);
  const changeEmotion = (value: string) => {
    if (ttsBusy || value === emotion) return;
    audioRef.current?.pause(); setPlaying(false); setAudioUrl(null); setEmotion(value);
  };

  // R30：@名字 高亮——当前会话任一角色的名字/别名（长名优先，避免前缀截断）
  const mentionNames = useMemo(() => {
    const inSession = charIds ? characters.filter((c) => charIds.includes(c.id)) : [];
    return [...new Set([
      ...inSession.flatMap((c) => [c.card.name, ...c.aliases]),
      ...groups.filter((g) => g.status === 'active').flatMap((g) => [g.label, ...g.aliases]),
    ])]
      .filter((n) => n.length > 0)
      .sort((a, b) => b.length - a.length);
  }, [characters, charIds, groups]);
  const mentionRe = useMemo(
    () =>
      mentionNames.length === 0
        ? null
        : new RegExp(`@(?:${mentionNames.map(escapeRegExp).join('|')})(?![\\w@])`, 'g'),
    [mentionNames],
  );

  const visibleNames =
    message.visible_to === 'all'
      ? null
      : message.visible_to
          .map((id) => (id === 'player' ? '你' : characters.find((c) => c.id === id)?.card.name ?? groups.find((g) => g.id === id)?.label ?? id))
          .join('、');

  const meta = message.generation_meta;
  const promptTokens = meta
    ? Object.values(meta.prompt_tokens_by_section).reduce((s, n) => s + n, 0)
    : 0;
  // 开场白判定：后端不单列 kind，开场消息就是卡内 first_mes 的逐字拷贝
  // （编辑/swipe 开场白后徽章消失——已接受的行为，见 design/v3/m8-r32 §6.6）
  const isOpening = !isPlayer && character != null
    && message.status === 'final'
    && message.generation_meta == null
    && character.card.first_mes.trim().length > 0
    && message.content === character.card.first_mes;

  // Keep less-used message actions together, with labels that can be read without hovering.
  const actions: MessageAction[] = [
    ...(message.status === 'final' && !message.control_event ? [{ id: 'copy', label: '复制消息', icon: <Clipboard size={14} />, onClick: copyMessage }] : []),
    ...(!isPlayer && message.actor !== 'director' && message.kind === 'roleplay' && message.status === 'final' ? [{
      id: 'context', label: '查看这条上下文', title: '查看这条回应实际使用的上下文与来源', icon: <Brain size={14} />,
      onClick: () => useChatStore.getState().requestInspect(message.actor, message.turn, message.id,
        generationIdForMessage(message)),
    }] : []),
    ...(message.status === 'final' ? [{ id: 'remember', label: '记住这件事', title: '保存到知情角色的重要记忆', icon: <Brain size={14} />, onClick: () => setModal('remember') }] : []),
    ...(message.status === 'final' && onBookmark ? [{ id: 'bookmark', label: bookmarked ? '取消书签' : '书签',
      title: bookmarked ? '从剧情导航移除书签' : '加入剧情导航（不会注入模型）',
      icon: <Bookmark size={14} fill={bookmarked ? 'currentColor' : 'none'} />,
      active: Boolean(bookmarked), onClick: () => runAction(() => onBookmark(message)) }] : []),
    ...(canEdit && onFork ? [{ id: 'fork', label: '新世界线', title: '从这条消息开新世界线', icon: <GitBranch size={14} />, onClick: () => runAction(() => onFork(message)) }] : []),
    ...(canPin && onEvent ? [{ id: 'event', label: '剧情事件', title: '标记剧情事件', icon: <BookOpen size={14} />, onClick: () => runAction(() => onEvent(message)) }] : []),
    ...(canSwipe
      ? [
          {
            id: 'regenerate', label: '重新生成',
            title: '只重新生成这条回应，保留其他气泡和候选历史',
            icon: <RefreshCw size={14} />,
            disabled: mutationBusy,
            onClick: () => runAction(() => useChatStore.getState().regenerateOne(message.id)),
          },
          ...(isLast ? [{
            id: 'continue', label: '续写',
            title: '续写（从结尾自然接续）',
            icon: <ArrowRight size={14} />,
            onClick: () => runAction(() => useChatStore.getState().continueMessage(message.id)),
            disabled: mutationBusy,
          }] : []),
        ]
      : []),
    ...(canSwipeGroup && currentSessionId
      ? [{
          id: 'regenerate', label: '重新生成', title: '只重新生成这条群体回应，保留其他气泡和候选历史', icon: <RefreshCw size={14} />,
          disabled: mutationBusy,
          onClick: () => runAction(() => useChatStore.getState().regenerateOne(message.id)),
        }]
      : []),
    ...((character || group) && beforeBoundary && message.status === 'final' ? [{
      id: 'regenerate', label: '重新生成', title: '已切换控制角色，切换前的回复不能生成新候选或续写；仍可编辑和选择已有候选',
      icon: <RefreshCw size={14} />, disabled: true, quick: false, onClick: () => {},
    }] : []),
    ...(showRegen
      ? [
          {
            id: 'regenerate', label: '重新生成',
            title: canRegen ? '重新生成（重跑这一轮：角色基于这条消息重新回应）' : regenHint,
            quick: canRegen,
            icon: <RefreshCw size={14} />,
            disabled: !canRegen || mutationBusy,
            onClick: () => runAction(() => useChatStore.getState().regenerate(message.id)),
          },
        ]
      : []),
    ...(canPin
      ? [{
          id: 'pin', label: pinnedFact ? '取消固定' : '固定信息',
          title: pinnedFact ? '取消固定（不再注入后续上下文）' : '固定为本局重要信息（后续回合持续注入）',
          icon: <Pin size={14} />,
          active: Boolean(pinnedFact),
          onClick: () => pinnedFact
            ? runAction(() => useChatStore.getState().deletePinnedFact(pinnedFact.id))
            : setModal('pin'),
        }]
      : []),
    ...(canEdit
      ? [
          {
            id: 'edit', label: '编辑',
            title: '编辑消息',
            icon: <Pencil size={14} />,
            disabled: mutationBusy,
            onClick: () => { setEditExpectedRevision(branchRevision); setModal('edit'); },
          },
          {
            id: 'delete', label: '删除',
            title: '删除消息（角色将不再看到它）',
            icon: <Trash2 size={14} />,
            danger: true,
            disabled: mutationBusy,
            onClick: () => setModal('delete'),
          },
        ]
      : []),
  ];

  const modals =
    modal === 'remember' ? (
      <RememberMemoryModal message={message} onClose={() => setModal(null)} />
    ) : modal === 'pin' ? (
      <PinFactModal
        initial={message.content}
        onSave={(content) => useChatStore.getState().createPinnedFact({ content, message_id: message.id })}
        onClose={() => setModal(null)}
      />
    ) : modal === 'edit' && isPlayer ? (
      <EditPlayerInputModal
        messages={inputParts}
        hasFuture={inputHasFuture}
        expectedBranchRevision={editExpectedRevision ?? branchRevision}
        onSave={(parts, expectedRevision) => onEditInputGroup
          ? onEditInputGroup(message.id, parts, expectedRevision)
          : useChatStore.getState().editInputGroup(message.id, parts, expectedRevision)}
        onClose={() => setModal(null)}
      />
    ) : modal === 'edit' ? (
      <EditMessageModal
        initial={message.content}
        hasFuture={!isLast}
        hasVariants={message.variants.length > 0}
        edited={message.edited}
        onSave={(content) => onEdit
          ? onEdit(message, content, editExpectedRevision ?? branchRevision, message.fingerprint)
          : useChatStore.getState().editMessage(message.id, content, editExpectedRevision ?? branchRevision, message.fingerprint)}
        onClose={() => setModal(null)}
      />
    ) : modal === 'delete' ? (
      <DeleteMessageModal
        onDelete={() => useChatStore.getState().deleteMessage(message.id)}
        onClose={() => setModal(null)}
      />
    ) : modal === 'hygiene' && message.hygiene != null ? (
      <HygieneModal
        report={message.hygiene}
        onRecheck={() =>
          runAction(() => useChatStore.getState().recheckHygiene(message.id))}
        onClose={() => setModal(null)}
      />
    ) : null;

  const infoCharacter = isPlayer ? playerCharacter : character;
  const npcIdentity = [...(session?.player_identities ?? [])].reverse().find((item) => item.person_id === message.actor);
  const infoModal =
    infoOpen && isPlayer ? <PlayerRoleProfile identity={playerIdentity} branchId={currentSessionId} onClose={() => setInfoOpen(false)} /> : infoOpen && infoCharacter != null ? (
      <CharacterInfoModal character={infoCharacter} readOnly snapshotAvatar={Boolean(npcIdentity)}
        avatarSrc={playerAvatarUrl(currentSessionId, npcIdentity ?? null)} onClose={() => setInfoOpen(false)} />
    ) : null;

  const speech: MessageSpeech = { enabled: ttsEnabled, busy: ttsBusy, playing, emotion, onEmotionChange: changeEmotion,
    onSpeak: speak, onEnded: () => setPlaying(false), audioUrl, audioRef };

  if (message.control_event) return <div data-message-id={message.id} className="my-3 text-center text-xs text-slate-500" role="status">{message.content}</div>;

  const actionBar = (navigation?: ReactNode) => <MessageActionBar compactIcons actions={actions} externalError={err} onDismissError={() => setErr(null)} speech={message.status === 'final' ? speech : undefined} navigation={navigation} />;

  if (message.kind === 'system_event' && message.actor === 'director' && message.content.startsWith('[群体')) {
    const lines = message.content.split('\n');
    return <div data-message-id={message.id} className="v7-group-event-wrap">
      <details className="v7-group-event">
        <summary><span>场景记录</span>{lines[1] ?? '群体状态发生变化'}</summary>
        <pre>{lines.slice(2).join('\n')}</pre>
      </details>{actionBar()}{modals}
    </div>;
  }

  const inner = message.kind === 'inner';
  const sceneChannel = message.kind === 'scene' && (isPlayer || message.actor === 'director');
  if (inner || sceneChannel) {
    const channel = inner ? 'inner' : 'scene';
    const tint = inner ? 'text-violet-400' : isPlayer ? 'text-amber-500' : 'text-slate-400';
    const title = inner ? '仅你可见——角色不会直接看到这段文字' : isPlayer ? '场景事件——角色会做出反应' : '导演生成的场景描写';
    return <div data-message-id={message.id} data-message-status={message.status} className={`story-message-channel story-message-${channel} group relative flex flex-col ${inner ? 'items-end' : 'items-center'}`}>
      <div className="story-channel-body">
        <BubbleFrame speaker={channel} className={inner ? 'max-w-[83%] text-sm' : 'max-w-[85%] text-sm italic'}>
          <span className={`story-channel-label mr-1.5 text-[10px] not-italic ${tint}`} title={title}>
            {inner ? <Brain size={13} aria-hidden="true" /> : <Clapperboard size={13} aria-hidden="true" />}{inner ? '内心 · 仅自己可见' : isPlayer ? '旁白' : '场景'}
          </span>{message.content}
        </BubbleFrame>{actionBar()}
      </div>{modals}
    </div>;
  }

  const swipeInProgress = message.status === 'pending' && isSwipingMessage(message.id);
  // Presentation slot only: the bubble retains all candidate mutation guards and callbacks.
  const candidateNavigation = message.variants.length > 1 && message.active_variant != null ? (
<span className="story-candidate-switch flex items-center gap-1">
                  <Button variant="ghost"
                    type="button"
                    disabled={mutationBusy || message.status !== 'final' || message.active_variant <= 0}
                    onClick={() =>
                      runAction(() =>
                        useChatStore.getState().switchVariant(message.id, message.active_variant! - 1),
                      )
                    }
                    className="story-candidate-button mw-tool-button rounded px-1 py-0.5 hover:bg-slate-100 hover:text-indigo-500 disabled:opacity-30"
                    title="上一个候选"
                    aria-label="上一个候选"
                  >
                    <ArrowLeft size={14} />
                  </Button>
                  <span title={`${message.variants.length} 个重roll 候选`}>
                    {message.active_variant + 1}/{message.variants.length}
                  </span>
                  <Button variant="ghost"
                    type="button"
                    disabled={mutationBusy || message.status !== 'final' || message.active_variant >= message.variants.length - 1}
                    onClick={() =>
                      runAction(() =>
                        useChatStore.getState().switchVariant(message.id, message.active_variant! + 1),
                      )
                    }
                    className="story-candidate-button mw-tool-button rounded px-1 py-0.5 hover:bg-slate-100 hover:text-indigo-500 disabled:opacity-30"
                    title="下一个候选"
                    aria-label="下一个候选"
                  >
                    <ArrowRight size={14} />
                  </Button>
                </span>
  ) : null;


  return (
    <div data-message-id={message.id} data-message-status={message.status} className={`v7-message group relative flex gap-3 ${isPlayer ? 'v7-message-player flex-row-reverse' : 'v7-message-character'}`}>
      <div className="story-message-avatar">
      <Avatar
        name={name}
        color={isPlayer ? (playerCharacter?.color ?? 'slate') : color}
        src={isPlayer ? playerAvatarUrl(currentSessionId, playerIdentity) : playerAvatarUrl(currentSessionId,
          session?.player_identities?.find((x) => x.person_id === message.actor && x.avatar_ref) ?? null)}
        characterId={isPlayer ? undefined : character?.id}
        imageVersion={character?.updated_at ?? character?.revision ?? playerCharacter?.updated_at ?? playerCharacter?.revision}
        onClick={isPlayer || infoCharacter != null ? () => setInfoOpen(true) : undefined}
        title={isPlayer && playerCharacter ? `查看 ${playerCharacter.card.name} 的角色信息` : undefined}
      />
      </div>
      <div className={`v7-message-body ${isPlayer ? 'items-end text-right' : ''} flex flex-col gap-1`}>
        <div className={`story-message-identity flex items-center gap-2 text-xs text-slate-500 ${isPlayer ? 'flex-row-reverse' : ''}`}>
          <span className={`story-speaker-name font-medium ${isPlayer ? 'text-slate-600' : COLOR_CLASSES[color].name}`}>
            {name}
          </span>
          {group && <span className={`rounded px-1.5 py-0.5 text-[10px] ${COLOR_CLASSES[color].badge}`}>群体{group.count == null ? '' : ` ×${group.count}`}</span>}
          {isOpening && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-400">
              开场白
            </span>
          )}
          {message.edited && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-400" title="玩家编辑过">
              已编辑
            </span>
          )}
        {message.dependency_stale && <span className="conversation-stale-badge" title="这条回应读到的前序内容已经改变">基于旧版本</span>}
          {message.status === 'retracted' && (
            <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] text-rose-500">
              已撤销
            </span>
          )}
          {isPlayer && message.executed_reply_mode && message.reply_order?.length ? (
            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-700" title={message.reply_reason || undefined}>
              {message.executed_reply_mode === 'free' ? '自由交谈' : message.executed_reply_mode === 'parallel' ? '并行回应' : '串行接话'} · {message.reply_order.map((id) => characters.find((item) => item.id === id)?.card.name ?? groups.find((item) => item.id === id)?.label ?? id).join(' → ')}
            </span>
          ) : null}
          {isPlayer && message.status === 'pending' && message.reply_mode === 'auto' && (message.mentions?.length ?? 0) >= 2 ? (
            <span role="status" aria-live="polite" className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">安排发言中…</span>
          ) : null}
          {message.hygiene != null && !message.hygiene.passed && (
            <Button variant="ghost"
              type="button"
              onClick={() => setModal('hygiene')}
              className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600 hover:bg-amber-100"
              title={hygieneTooltip(message)}
            >
              ⚠ 校验未过
            </Button>
          )}
          {visibleNames !== null && (
            <span
              className="flex items-center gap-0.5 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600"
              title={`仅 ${visibleNames} 可见`}
            >
              <Eye size={12} /> {visibleNames}
            </span>
          )}
        </div>

        <BubbleFrame
          speaker={isPlayer ? 'player' : 'npc'}
          className={`v7-message-text ${message.status === 'retracted' ? 'opacity-50 line-through decoration-slate-400' : ''} ${
            swipeInProgress ? 'opacity-60' : ''
          }`}
        >
          {message.status === 'pending' && message.content === '' ? (
            <span role="status" aria-live="polite" className="flex items-center gap-2 py-1">
              <span className="flex gap-1" aria-hidden="true">
                <Dot delay="0ms" />
                <Dot delay="150ms" />
                <Dot delay="300ms" />
              </span>
              <span className="text-xs opacity-70">正在生成回复…</span>
            </span>
          ) : (
            <>
              {renderContent(message.content, mentionRe, isPlayer)}
              {message.status === 'pending' && (
                <span className="story-stream-caret ml-0.5 animate-pulse">▍</span>
              )}
            </>
          )}
        </BubbleFrame>

        {previousCandidate && message.status === 'pending' && <details className="conversation-original-candidate">
          <summary>原候选已保留</summary><div>{renderContent(previousCandidate.content, mentionRe, false)}</div>
        </details>}
        {meta?.completion_state === 'truncated' && <div role="status" className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">回应达到长度上限，内容尚未完整。{canSwipe && isLast && <Button variant="ghost" type="button" className="ml-2 underline" disabled={mutationBusy} onClick={() => runAction(() => useChatStore.getState().continueMessage(message.id))}>从结尾续写</Button>}</div>}
        {meta?.completion_state === 'filtered' && <div role="status" className="mb-2 text-xs text-amber-700">接口停止了这条回应，内容可能不完整。</div>}
        {message.dependency_stale && <div className="conversation-dependency-notice" role="status">
          <span>前序内容已变更。这条回应仍基于旧版本。</span>
          <div><Button variant="ghost" type="button" aria-label="更新受影响回应" disabled={mutationBusy || message.status !== 'final'}
            onClick={() => runAction(() => useChatStore.getState().regenerateDependents(message.id))}>更新受影响回应</Button>
          <Button variant="ghost" type="button" aria-label="继续保留" disabled={mutationBusy || message.status !== 'final'}
            onClick={() => runAction(() => useChatStore.getState().acceptDependencies(message.id))}>继续保留</Button></div>
        </div>}

        <div
          className={`story-message-footer flex flex-wrap items-center gap-2 text-[10px] text-slate-400 ${
            isPlayer ? 'flex-row-reverse' : ''
          }`}
        >
          {message.status === 'final' && actionBar(!swipeInProgress ? candidateNavigation : undefined)}
          {swipeInProgress ? (
            <span className="story-message-pending" role="status">正在重新生成…</span>
          ) : (
            <>
              {meta && (
                <span className="max-w-full break-all" title={`fingerprint: ${message.fingerprint}`}>
                  {meta.model} · ↑{promptTokens} ↓{meta.usage.output_tokens}
                </span>
              )}

            </>
          )}
          {message.status !== 'final' && err && <span role="alert" className="text-rose-500">{err}</span>}
        </div>
      </div>
      {modals}
      {infoModal}
    </div>
  );
}

function PinFactModal({ initial, onSave, onClose }: { initial: string; onSave: (content: string) => Promise<void>; onClose: () => void }) {
  const [content, setContent] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    if (!content.trim() || saving) return;
    setSaving(true);
    setError('');
    try {
      await onSave(content.trim());
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '固定失败');
    } finally {
      setSaving(false);
    }
  };
  return <Modal title="固定为本局重要信息" onClose={onClose}>
    <p className="mb-3 text-xs leading-relaxed text-slate-500">这段内容会在后续每轮优先提供给原消息可见的角色。建议把长回复精简成一句事实；固定信息总量上限约 700 tokens。</p>
    <textarea autoFocus value={content} onChange={(event) => setContent(event.target.value)} maxLength={10000} rows={6} className="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm leading-relaxed focus:border-indigo-400 focus:outline-none" />
    {error && <p role="alert" className="mt-2 text-xs text-rose-600">{error}</p>}
    <div className="mt-4 flex justify-end gap-2">
      <Button variant="ghost" type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">取消</Button>
      <Button variant="ghost" type="button" onClick={() => void save()} disabled={!content.trim() || saving} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">{saving ? '固定中…' : '固定信息'}</Button>
    </div>
  </Modal>;
}


/** R34 ⚠️ 角标 tooltip（详情 Modal 在 R34 落地） */
function hygieneTooltip(message: Message): string {
  const h = message.hygiene;
  if (!h) return '';
  const names: Record<string, string> = {
    C1: '代替玩家言行',
    C2: '代替其他角色发言',
    C3: '心灵感应（回应未表露的内心）',
    C4: '出戏（OOC/元话语）',
  };
  const lines = h.violations.map((v) => `${names[v.category] ?? v.category}：${v.evidence}`);
  return `输出卫生校验未通过${h.attempts > 1 ? '（重试后仍违规）' : ''}\n${lines.join('\n')}`;
}

/** 把内容里的 "@角色名/别名" 词段渲染为高亮 chip，其余文本原样（React 转义，无 XSS 面）。
 *  玩家气泡为 indigo-500 底白字——高亮换白色半透明变体保证对比度。 */
function renderContent(content: string, mentionRe: RegExp | null, isPlayer = false): ReactNode {
  if (mentionRe == null || !content.includes('@')) return content;
  const chipCls = isPlayer
    ? 'rounded bg-white/25 px-1 font-medium text-white'
    : 'rounded bg-indigo-50 px-1 font-medium text-indigo-600';
  const parts: ReactNode[] = [];
  let last = 0;
  mentionRe.lastIndex = 0;
  let m = mentionRe.exec(content);
  while (m != null) {
    if (m.index > last) parts.push(content.slice(last, m.index));
    parts.push(
      <span key={parts.length} className={chipCls}>
        {m[0]}
      </span>,
    );
    last = m.index + m[0].length;
    m = mentionRe.exec(content);
  }
  if (last < content.length) parts.push(content.slice(last));
  return parts;
}

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400"
      style={{ animationDelay: delay }}
    />
  );
}


