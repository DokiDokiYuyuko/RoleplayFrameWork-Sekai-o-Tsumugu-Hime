import { PublicArt } from '../design-system/PublicArt';
import { Button } from '../design-system/Button';
import { WritingAssistant } from './WritingAssistant';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { DropdownMenu, Popover } from 'radix-ui';
import { isConversationActive, useChatStore } from '../store/chatStore';
import { useCharacterStore } from '../store/characterStore';
import { useActiveCharacters } from '../store/useActiveCharacters';
import { useComposerStore } from '../store/composerStore';
import type { CharacterView, ReplyMode } from '../types';
import { COLOR_CLASSES, Switch, Avatar } from './common';
import { PlayerSwitchDialog } from './PlayerSwitchDialog';
import { PlayerRoleProfile } from './PlayerRoleProfile';
import { resolvePlayerIdentity, playerDraftKey, currentPlayerAvatarUrl } from '../utils/playerIdentity.js';
import { composerConversationRun } from '../utils/conversationDisplay.js';
import { ResponseStylePicker } from './ResponseStylePicker';
import { Brain, Clapperboard, MessageCircle, MoreHorizontal, Sparkles, Send, LoaderCircle } from '../design-system/Icon';
import { canReconcileComposerTargets, filterValidComposerTargets } from '../utils/composerTargets.js';
import { hasUnclosedChannelMarker, parseChannelParts } from '../utils/channels';
import { useComposerInput } from './useComposerInput';
import { useAutoSizeTextarea } from './useAutoSizeTextarea';
import { ComposerPanel } from './ComposerPanel';
import { conversationPreferenceKey, normalizeMaxReplies, readConversationPreferences, saveConversationPreferences } from '../utils/conversationPreferences.js';
import { ConversationRunPanel } from './ConversationRunPanel';
import '../conversation.css';

function readPreferences(key: string) {
  try { return readConversationPreferences(localStorage, key); }
  catch { return { recipients: [] as string[], replyMode: 'auto' as ReplyMode, maxReplies: 6 }; }
}

function readObservation(key: string): { observers: string[]; maxReplies: number } | null {
  try {
    const value = JSON.parse(localStorage.getItem(`${key}.observation`) ?? 'null');
    return value && Array.isArray(value.observers) ? {
      observers: value.observers.filter((id: unknown): id is string => typeof id === 'string'),
      maxReplies: normalizeMaxReplies(value.maxReplies),
    } : null;
  } catch { return null; }
}

/**
 * 底部输入区（B 收件人模式，R30 重构）：
 * - 对象面板：选择定向目标，发送后保留（持续定向对话）
 * - 文本保持纯净：@名字 仅作展示高亮，不参与路由（语义分离，消灭同步问题）
 * - 设置浮层独立于输入区布局，展开时不挤动聊天消息
 * - 内心 / 旁白在代笔后面直接插入，并在输入时显示解析预览
 */

/** 在光标处插入通道标记（内心/旁白），光标落在冒号后 */
function insertMarker(
  textarea: HTMLTextAreaElement,
  setText: (v: string) => void,
  marker: string,
) {
  const pos = textarea.selectionStart ?? textarea.value.length;
  const before = textarea.value.slice(0, pos);
  const after = textarea.value.slice(pos);
  const pad = before && !before.endsWith(' ') && !before.endsWith('（') && !before.endsWith('(') ? ' ' : '';
  const insert = `${pad}${marker}）`;
  const next = before + insert + after;
  setText(next);
  const caret = pos + pad.length + marker.length;
  requestAnimationFrame(() => {
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
  });
}
export function Composer() {
  const { currentSessionId, sendMessage, sending } = useChatStore();
  const action = useCharacterStore((s) => s.action);
  const characters = useActiveCharacters();
  const sessionGroups = useChatStore((s) => s.sessionGroups);
  const snapshotLoadedSessionId = useChatStore((s) => s.snapshotLoadedSessionId);
  const activeScene = useChatStore((s) => s.activeScene);
  const session = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId));
  const identity = resolvePlayerIdentity(session);
  const switching = useChatStore((s) => s.switchingPlayer);
  const pendingDirector = useChatStore((s) => s.pendingDirector);
  const conversationRuns = useChatStore((s) => s.conversationRuns);
  const conversationActionBusy = useChatStore((s) => s.conversationActionBusy);
  const localRuns = conversationRuns.filter((run) => !run.session_id || run.session_id === currentSessionId);
  const latestConversationRun = localRuns[localRuns.length - 1];
  const runActive = localRuns.some((run) => isConversationActive(run, currentSessionId));
  const conversationRun = composerConversationRun(conversationRuns, currentSessionId);
  const conversationBusy = Boolean(conversationActionBusy);
  const scopeKey = conversationPreferenceKey(currentSessionId, identity?.id);
  const scopeRef = useRef(scopeKey);
  scopeRef.current = scopeKey;
  const [switchOpen, setSwitchOpen] = useState(false);
  const [writingOpen, setWritingOpen] = useState(false);
  const [writingUndo, setWritingUndo] = useState<{ before: string; after: string } | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [modeError, setModeError] = useState('');
  const draftKey = playerDraftKey(currentSessionId, identity?.id);
  const readDraft = (key: string) => { try { return localStorage.getItem(key) ?? ''; } catch { return ''; } };
  const [draft, setDraft] = useState(() => ({ key: draftKey, text: readDraft(draftKey) }));
  const text = draft.key === draftKey ? draft.text : readDraft(draftKey);
  const priorDrafts = [...(session?.player_identities ?? [])].reverse()
    .filter((stage) => stage.person_id === identity?.person_id && stage.id !== identity?.id)
    .map((stage) => ({ stage, text: readDraft(playerDraftKey(currentSessionId, stage.id)) }))
    .filter((item) => item.text.trim());
  // Each callback keeps its original key, including a late failed-send response.
  const setText = (value: string | ((old: string) => string)) => setDraft((previous) => {
    const old = previous.key === draftKey ? previous.text : readDraft(draftKey);
    const next = typeof value === 'function' ? value(old) : value;
    try { localStorage.setItem(draftKey, next); } catch { /* Private browser storage can be unavailable. */ }
    return { key: draftKey, text: next };
  });
  useEffect(() => {
    setMentionDraft(null); setProfileOpen(false); setOpenPanel(null); setWritingOpen(false); setWritingUndo(null);
    useComposerStore.getState().resetAfterSend();
    setModeError('');
  }, [draftKey]);
  const [mentionDraft, setMentionDraft] = useState<{ query: string; start: number; end: number } | null>(null);
  const [recipients, setRecipients] = useState<string[]>(() => readPreferences(scopeKey).recipients);
  const [replyMode, setReplyMode] = useState<ReplyMode>(() => readPreferences(scopeKey).replyMode);
  const [maxRepliesInput, setMaxRepliesInput] = useState(() => String(readPreferences(scopeKey).maxReplies));
  const maxReplies = normalizeMaxReplies(maxRepliesInput);
  const [directive, setDirective] = useState('');
  const [observers, setObservers] = useState<string[]>(() => readObservation(scopeKey)?.observers ?? []);
  const [observationMaxInput, setObservationMaxInput] = useState(() => String(readObservation(scopeKey)?.maxReplies ?? 6));
  const observationMax = normalizeMaxReplies(observationMaxInput);
  const [observationDirective, setObservationDirective] = useState('');
  const [observationInitialized, setObservationInitialized] = useState(() => readObservation(scopeKey) !== null);
  const [interventionRunId, setInterventionRunId] = useState<string | null>(null);
  const [loadedPreferenceScope, setLoadedPreferenceScope] = useState(scopeKey);
  const [loadedTargetsSession, setLoadedTargetsSession] = useState<string | null>(currentSessionId);
  useEffect(() => {
    const preference = readPreferences(scopeKey);
    setRecipients(preference.recipients); setReplyMode(preference.replyMode); setMaxRepliesInput(String(preference.maxReplies));
    setDirective(''); setInterventionRunId(null); setLoadedPreferenceScope(scopeKey);
    const observation = readObservation(scopeKey);
    setObservers(observation?.observers ?? []); setObservationMaxInput(String(observation?.maxReplies ?? 6));
    setObservationDirective(''); setObservationInitialized(observation !== null);
    setMuteOverrides({}); setProactiveOverrides({}); setFollowupOverrides({});
    setLoadedTargetsSession(currentSessionId);
  }, [scopeKey, currentSessionId]);
  // M12-R41 预填通道：候选条 requestPrefill → 此处消费
  // 语义（2026-09-25 修订）：清空后覆盖（不叠加）；若覆盖掉的是玩家自己写的草稿 → 留一次性恢复
  const prefill = useComposerStore((s) => s.prefill);
  const draftBackup = useComposerStore((s) => s.draftBackup);
  const textRef = useRef('');
  textRef.current = text;
  useEffect(() => {
    if (!prefill) return;
    const st = useComposerStore.getState();
    if (st.prefill !== prefill) return; // A preceding identity change cleared this stale render.
    const incoming = prefill.text.trim();
    const prev = textRef.current.trim();
    // 现有内容既不是空、也不同于新候选、又不是"上一条候选（未改动）" → 是玩家自己的草稿
    if (prev && prev !== incoming && prev !== st.lastPrefill) {
      st.backupDraft(prev);
    }
    if (prev !== incoming) setText(incoming);
    st.notePrefilled(incoming);
    // 定向：候选带 @ → 替换为候选对象（无 @ 则不动，保持玩家的持续定向）
    if (prefill.mentionIds.length > 0) setRecipients(prefill.mentionIds);
    st.clearPrefill();
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [prefill]);
  const [openPanel, setOpenPanel] = useState<'recipients' | 'observation' | 'style' | 'members' | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useAutoSizeTextarea(inputRef, text, draftKey);
  /** R30 mute 是会话级状态，全局 characterStore 永远 muted=false —— 本地覆盖表修复显示 */
  const [muteOverrides, setMuteOverrides] = useState<Record<string, boolean>>({});
  /** R38 主动性开关的本地覆盖表（同 mute 模式） */
  const [proactiveOverrides, setProactiveOverrides] = useState<Record<string, boolean>>({});
  const [followupOverrides, setFollowupOverrides] = useState<Record<string, boolean>>({});
  const sessionChars = characters.filter((c) => session?.character_ids.includes(c.id));
  const currentGroups = sessionGroups.filter((group) => group.status === 'active'
    && (!activeScene || (group.scene_id === activeScene.id && activeScene.group_ids.includes(group.id))));
  useEffect(() => {
    if (loadedPreferenceScope !== scopeKey || !canReconcileComposerTargets(currentSessionId, loadedTargetsSession, snapshotLoadedSessionId)) return;
    const valid = filterValidComposerTargets(
      recipients,
      sessionChars.map((item) => ({
        id: item.id, present: item.present && item.id !== identity?.person_id,
        muted: muteOverrides[item.id] ?? item.muted,
      })),
      currentGroups,
    );
    if (valid.length !== recipients.length) {
      setRecipients(valid);
      setModeError('离场、静音或当前分支中无效的对象已从发言名单移除。');
    }
    try { saveConversationPreferences(localStorage, scopeKey, { recipients: valid, replyMode, maxReplies }); } catch { /* Browser storage may be unavailable. */ }
  }, [currentSessionId, loadedTargetsSession, loadedPreferenceScope, scopeKey, snapshotLoadedSessionId, recipients, sessionChars, currentGroups, muteOverrides, identity?.person_id, replyMode, maxReplies]);
  const effectiveRecipients = recipients.filter((id) => {
    const c = sessionChars.find((item) => item.id === id);
    return c ? c.id !== identity?.person_id && c.present && !(muteOverrides[id] ?? c.muted) : currentGroups.some((g) => g.id === id);
  });
  const mentionOptions = [
    ...sessionChars.filter((character) => character.id !== identity?.person_id && character.present && !(muteOverrides[character.id] ?? character.muted)).map((character) => ({ id: character.id, label: character.card.name, aliases: character.aliases, kind: '角色' })),
    ...currentGroups.map((group) => ({ id: group.id, label: group.label, aliases: group.aliases, kind: '群体' })),
  ];
  const effectiveObservers = observers.filter((id) => mentionOptions.some((option) => option.id === id));
  useEffect(() => {
    if (!observationInitialized || loadedPreferenceScope !== scopeKey || snapshotLoadedSessionId !== currentSessionId) return;
    try { localStorage.setItem(`${scopeKey}.observation`, JSON.stringify({ observers, maxReplies: observationMax })); } catch { /* Optional browser storage. */ }
  }, [observationInitialized, loadedPreferenceScope, scopeKey, snapshotLoadedSessionId, currentSessionId, observers, observationMax]);
  const recipientNames = recipients.map((id) =>
    sessionChars.find((character) => character.id === id)?.card.name
    ?? currentGroups.find((group) => group.id === id)?.label
    ?? id,
  );
  const mentionMatches = mentionDraft
    ? mentionOptions.filter((item) => [item.label, ...item.aliases].some((name) => name.toLocaleLowerCase().includes(mentionDraft.query.toLocaleLowerCase()))).slice(0, 6)
    : [];

  const updateComposerText = (event: FormEvent<HTMLTextAreaElement>) => {
    const next = event.currentTarget.value;
    const cursor = event.currentTarget.selectionStart ?? next.length;
    setText(next);
    const beforeCursor = next.slice(0, cursor);
    const start = beforeCursor.lastIndexOf('@');
    if (start >= 0 && !/[\s@]/.test(beforeCursor.slice(start + 1))) {
      setMentionDraft({ query: beforeCursor.slice(start + 1), start, end: cursor });
    } else setMentionDraft(null);
  };

  const insertMention = (item: { id: string; label: string }) => {
    if (!mentionDraft) return;
    const next = `${text.slice(0, mentionDraft.start)}@${item.label} ${text.slice(mentionDraft.end)}`;
    const caret = mentionDraft.start + item.label.length + 2;
    setText(next); setMentionDraft(null);
    setRecipients((previous) => previous.includes(item.id) ? previous : [...previous, item.id]);
    requestAnimationFrame(() => { inputRef.current?.focus(); inputRef.current?.setSelectionRange(caret, caret); });
  };

  // ---- R30.4：收件人的在场状态 ----
  const recipientChars = recipients
    .map((cid) => sessionChars.find((c) => c.id === cid))
    .filter((c): c is CharacterView => c != null);
  const absentRecipients = recipientChars.filter((c) => !c.present);
  const allRecipientsAbsent =
    recipients.length > 0 && recipientChars.length === recipients.length
    && absentRecipients.length === recipientChars.length;
  const someRecipientsAbsent = absentRecipients.length > 0 && !allRecipientsAbsent;

  const closeMenus = () => {
    setOpenPanel(null);
  };
  const togglePanel = (panel: Exclude<typeof openPanel, null>) => {
    if (panel === 'observation' && !observationInitialized) {
      setObservers([...effectiveRecipients]);
      setObservationInitialized(true);
    }
    setOpenPanel((current) => current === panel ? null : panel);
  };

  const submit = () => {
    const content = (inputRef.current?.value ?? text).trim();
    if (!content || !currentSessionId || sending || runActive || conversationBusy || switching || snapshotLoadedSessionId !== currentSessionId || allRecipientsAbsent) return;
    if (replyMode === 'free' && effectiveRecipients.length === 0) {
      setModeError('请先选择参与自由交谈的人物或群体。'); setOpenPanel('recipients'); return;
    }
    if (hasUnclosedChannelMarker(content)) {
      setModeError('内心或旁白标记尚未闭合，请补上右括号后发送。');
      inputRef.current?.focus();
      return;
    }
    setModeError('');
    setText('');
    useComposerStore.getState().clearDraftBackup(); // 发送后草稿备份一并作废
    closeMenus();
    // 收件人保留（持续定向）；为空时传 undefined 走隐式提及/轮盘
    const submittedScope = scopeKey;
    void sendMessage(content, effectiveRecipients.length > 0 ? effectiveRecipients : undefined, undefined, replyMode, maxReplies,
      replyMode === 'free' ? directive.trim() : undefined)
      .catch((error: unknown) => {
        setText((current) => current || content);
        if (scopeRef.current === submittedScope) setModeError(error instanceof Error ? error.message : '发送失败，输入已保留，请重试。');
      });
  };
  const inputEvents = useComposerInput(inputRef, submit);

  const toggleRecipient = (cid: string) => {
    setRecipients((prev) =>
      prev.includes(cid) ? prev.filter((id) => id !== cid) : [...prev, cid],
    );
  };
  const reorderRecipient = (id: string, delta: number) => setRecipients((current) => {
    const index = current.indexOf(id); const target = index + delta;
    if (index < 0 || target < 0 || target >= current.length) return current;
    const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next;
  });

  const requestGroupReply = async (groupId: string) => {
    if (!currentSessionId || sending || runActive || conversationBusy) return;
    const requestScope = scopeKey;
    closeMenus();
    try {
      await useChatStore.getState().requestGroupReply(groupId);
    } catch (error) {
      if (scopeRef.current === requestScope) setModeError(error instanceof Error ? error.message : '群体回应失败');
    }
  };

  const focusComposer = () => { closeMenus(); requestAnimationFrame(() => inputRef.current?.focus()); };
  const controlConversation = async (action: 'pause' | 'stop' | 'resume', additionalReplies?: number, runId = conversationRun?.id) => {
    if (!runId) return;
    const requestScope = scopeKey;
    try { await useChatStore.getState().controlConversation(runId, action, additionalReplies); }
    catch (error) { if (scopeRef.current === requestScope) setModeError(error instanceof Error ? error.message : '交谈操作失败'); }
  };
  const intervene = async () => {
    if (!conversationRun) return;
    if (!isConversationActive(conversationRun, currentSessionId)) { focusComposer(); return; }
    setInterventionRunId(conversationRun.id);
    await controlConversation('pause');
  };
  useEffect(() => {
    if (!interventionRunId) return;
    const run = conversationRuns.find((item) => item.id === interventionRunId);
    if (run && !isConversationActive(run, currentSessionId)) { setInterventionRunId(null); focusComposer(); }
  }, [conversationRuns, interventionRunId]);
  const startObservation = async () => {
    if (!effectiveObservers.length || sending || runActive || conversationBusy || switching || snapshotLoadedSessionId !== currentSessionId) return;
    const requestScope = scopeKey;
    setModeError('');
    try {
      await useChatStore.getState().startConversation(effectiveObservers, observationMax, observationDirective.trim());
      if (scopeRef.current === requestScope) closeMenus();
    } catch (error) { if (scopeRef.current === requestScope) setModeError(error instanceof Error ? error.message : '交谈启动失败'); }
  };

  // 实时解析预览（仅当有通道标记时显示）
  const previewParts = useMemo(() => parseChannelParts(text, 'roleplay'), [text]);
  const hasChannels = Boolean(text.trim()) && previewParts.some((p) => p.kind !== 'roleplay');

  return (
    <Popover.Root open={openPanel !== null} onOpenChange={(open) => { if (!open) closeMenus(); }}>
    <Popover.Anchor asChild><div className="story-composer v7-composer-inner border-t border-slate-200 bg-white px-4 py-3" data-composer-state={runActive ? 'conversation' : sending ? 'sending' : switching ? 'switching' : 'ready'}>
      <PublicArt path="v3/conversation/composer-teal-quill-corner.webp" className="story-composer-corner" aria-hidden="true" />
      <div className="story-composer-content">
      <div className="story-composer-context" data-composer-controls>
      <div className="player-control-bar">
        <div className="player-control-person"><Avatar name={identity?.name ?? '玩家'} color="slate" size="sm"
          src={currentPlayerAvatarUrl(currentSessionId, identity)} onClick={() => setProfileOpen(true)} title="查看当前控制人物" />
          <span className="story-composer-identity-label">当前控制</span><strong>{identity?.name ?? '玩家'}</strong></div>
        <Button variant="ghost" type="button" className="story-identity-switch mw-tool-button" aria-label="切换角色" aria-description={`当前控制 ${identity?.name ?? '玩家'}`} disabled={sending || runActive || conversationBusy || switching || Boolean(pendingDirector) || !identity || snapshotLoadedSessionId !== currentSessionId}
          title={runActive ? '请先暂停交谈，再切换控制人物' : sending || pendingDirector ? '请等待本轮生成和导演确认结束' : '选择另一个人物继续故事'} onClick={() => { closeMenus(); setSwitchOpen(true); }}>
          <span className="story-identity-switch-label">{switching ? '切换中…' : '切换角色'}</span><span className="story-identity-switch-current">{switching ? '切换中…' : identity?.name ?? '玩家'}</span></Button>
      </div>
      <label className="story-composer-reply-mode"><span>回复模式</span>
        <select value={replyMode} aria-label="回复模式" onChange={(event) => setReplyMode(event.target.value as ReplyMode)}>
          <option value="auto">自动安排</option><option value="parallel">并行回应</option><option value="serial">按序接话</option><option value="free">自由交谈</option>
        </select>
      </label>
      <Button variant="ghost" type="button" className="story-composer-targets mw-tool-button" onClick={() => togglePanel('recipients')} aria-expanded={openPanel === 'recipients'} title={recipientNames.length ? `定向给 ${recipientNames.join('、')}` : '由导演自动选择回应角色'}>
        对象 · {recipients.length > 0 ? `${recipients.length} 个` : '自动'}
      </Button>
      </div>
      <div className="story-composer-status">
      {switchOpen && <PlayerSwitchDialog onClose={() => setSwitchOpen(false)} />}
      {profileOpen && <PlayerRoleProfile identity={identity} branchId={currentSessionId} current onClose={() => setProfileOpen(false)} />}
      {priorDrafts.length > 0 && <details className="player-prior-drafts">
        <summary>这个人物此前的未发送草稿 · {priorDrafts.length}</summary>
        {priorDrafts.map((item, index) => <Button variant="ghost" type="button" key={item.stage.id} disabled={sending || switching}
          onClick={() => {
            if (text.trim() && text !== item.text) { setModeError('请先发送或清空当前草稿，再恢复以前的草稿。'); return; }
            setText(item.text); setModeError('已恢复此前的草稿，请确认内容后再发送。');
          }} title="由你确认恢复，不会自动挪到新身份">
          草稿 {index + 1} · {item.text.slice(0, 48)}{item.text.length > 48 ? '…' : ''}
        </Button>)}
      </details>}
      {modeError && <div className="story-composer-notice" role="alert"><p>{modeError}</p><Button variant="ghost" type="button" onClick={() => setModeError('')} aria-label="关闭输入提示">关闭</Button></div>}
      {openPanel === 'members' && (
        <ComposerPanel title="角色与点名" onClose={closeMenus}>
          <p className="mb-2 text-xs text-slate-500">调整本局角色的发言方式，或点名让角色立即发言。</p>
          {sessionChars.length === 0 && currentGroups.length === 0 && (
            <p className="py-2 text-sm text-slate-400">当前场景暂无角色或群体</p>
          )}
          <div className="v7-member-grid">
          {sessionChars.map((c) => (
            <div key={c.id} className="v7-member-row">
              <div className="flex items-center justify-between">
                <span className="text-sm">
                  {c.card.name}
                  {!c.present && (
                    <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">
                      不在场
                    </span>
                  )}
                </span>
                <Button variant="ghost" type="button" className="v7-member-force" disabled={sending || runActive || conversationBusy || c.id === identity?.person_id || !c.present} onClick={() => {
                  closeMenus();
                  void action(c.id, 'force', currentSessionId ?? undefined);
                }}>点名发言</Button>
              </div>
              <div className="mt-1 flex items-center justify-between pl-1">
                <span className="text-[11px] text-slate-500" title="静音后导演不会抽选这个角色">静音</span>
                <Switch on={muteOverrides[c.id] ?? c.muted} onToggle={() => {
                    const target = !(muteOverrides[c.id] ?? c.muted);
                    setMuteOverrides(prev => ({ ...prev, [c.id]: target }));
                    void action(c.id, target ? 'mute' : 'unmute', currentSessionId ?? undefined);
                  }} />
              </div>
              <div className="mt-1 flex items-center justify-between pl-1">
                <span className="text-[11px] text-slate-500">主动插话</span>
                <Switch
                  on={proactiveOverrides[c.id] ?? c.interject_enabled}
                  onToggle={() => {
                    const target = !(proactiveOverrides[c.id] ?? c.interject_enabled);
                    setProactiveOverrides(prev => ({ ...prev, [c.id]: target }));
                    void action(c.id, target ? 'interject' : 'uninterject', currentSessionId ?? undefined);
                  }}
                />
              </div>
              <div className="mt-0.5 flex items-center justify-between pl-1">
                <span className="text-[11px] text-slate-500">接话</span>
                <Switch
                  on={followupOverrides[c.id] ?? c.followup_enabled}
                  onToggle={() => {
                    const target = !(followupOverrides[c.id] ?? c.followup_enabled);
                    setFollowupOverrides(prev => ({ ...prev, [c.id]: target }));
                    void action(c.id, target ? 'followup' : 'unfollowup', currentSessionId ?? undefined);
                  }}
                />
              </div>
            </div>
          ))}
          {currentGroups.map((group) => (
            <div key={group.id} className="v7-member-row">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm"><span className="mr-2 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">群体</span>{group.label}{group.count == null ? '' : ` ×${group.count}`}</span>
                <Button variant="ghost" type="button" className="v7-member-force" disabled={sending || runActive || conversationBusy} onClick={() => void requestGroupReply(group.id)}>点名发言</Button>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">{group.participation === 'on_cue' ? '点名时回应' : '可偶尔主动回应'}</p>
            </div>
          ))}
          </div>
        </ComposerPanel>
      )}
      {openPanel === 'recipients' && (
        <ComposerPanel title="消息对象" onClose={closeMenus}>
          <p className="composer-panel-description">谁来回应你下一条消息？不选择时由导演自动决定。此处设置不会启动交谈。</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" type="button" aria-pressed={recipients.length === 0} onClick={() => setRecipients([])} className={`v7-recipient-option ${recipients.length === 0 ? 'is-active' : ''}`}>导演自动选择</Button>
          {sessionChars.map((c) => (
            <Button variant="ghost" key={c.id} type="button" aria-pressed={recipients.includes(c.id)} disabled={c.id === identity?.person_id || !c.present || Boolean(muteOverrides[c.id] ?? c.muted)} onClick={() => toggleRecipient(c.id)} className={`v7-recipient-option ${recipients.includes(c.id) ? 'is-active' : ''}`}>
              <span className={`inline-block h-2 w-2 rounded-full ${COLOR_CLASSES[c.color].dot}`} /> {c.card.name}{c.id === identity?.person_id ? ' · 你控制' : !c.present ? ' · 离席' : (muteOverrides[c.id] ?? c.muted) ? ' · 静音' : ''}
            </Button>
          ))}
          {currentGroups.map((group) => (
            <Button variant="ghost" key={group.id} type="button" aria-pressed={recipients.includes(group.id)} onClick={() => toggleRecipient(group.id)} className={`v7-recipient-option ${recipients.includes(group.id) ? 'is-active' : ''}`}>
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" /> {group.label} · 群体
            </Button>
          ))}
          </div>
          {effectiveRecipients.length >= 2 && replyMode !== 'free' && <div className="mt-3 border-t border-slate-100 pt-3">
            <p className="mb-2 text-xs font-medium text-slate-600">发言顺序（按点选顺序）</p>
            <ol className="mb-2 flex flex-wrap items-center gap-1 text-xs text-slate-600">
              {effectiveRecipients.map((id, index) => <li key={id} className="inline-flex items-center gap-1">
                {index > 0 && <span aria-hidden="true">→</span>}<span>{['①','②','③','④','⑤','⑥'][index] ?? `${index + 1}.`} {recipientNames[recipients.indexOf(id)] ?? id}</span>
                {effectiveRecipients.length > 2 && <span className="inline-flex"><Button variant="ghost" type="button" aria-label={`上移第 ${index + 1} 位`} disabled={index === 0} onClick={() => reorderRecipient(id, -1)} className="rounded px-1 text-indigo-600 disabled:text-slate-300">↑</Button><Button variant="ghost" type="button" aria-label={`下移第 ${index + 1} 位`} disabled={index === effectiveRecipients.length - 1} onClick={() => reorderRecipient(id, 1)} className="rounded px-1 text-indigo-600 disabled:text-slate-300">↓</Button></span>}
              </li>)}
            </ol>
            {effectiveRecipients.length === 2 && <Button variant="ghost" type="button" onClick={() => setRecipients((items) => [items[1], items[0]])} className="mb-2 rounded border border-slate-200 px-2 py-1 text-xs text-slate-600">交换顺序</Button>}
          </div>}
          <div className="conversation-settings">
            <label>回复方式
              <select value={replyMode} onChange={(event) => setReplyMode(event.target.value as ReplyMode)} aria-label="多人回复方式">
                <option value="auto">自动</option><option value="parallel">并行</option><option value="serial">串行</option><option value="free">自由交谈</option>
              </select>
            </label>
            {replyMode === 'free' && <><label>本次最多
              <input type="number" min={1} max={30} value={maxRepliesInput} aria-label="自由交谈条数" onChange={(event) => setMaxRepliesInput(event.target.value)} onBlur={() => setMaxRepliesInput(String(maxReplies))} /> 条
            </label>
            <p>发送后，所选人物与群体会根据你的新消息自然接话，可以再次发言，也可以保持沉默。</p>
            <label className="conversation-directive">导演要求（可选）
              <textarea rows={2} aria-label="导演要求" value={directive} onChange={(event) => setDirective(event.target.value)} placeholder="例如：围绕刚才的话题交换各自知道的事情" />
            </label>
            </>}
            <p>{replyMode === 'auto' ? '根据本轮内容自动安排并行或串行回复。' : replyMode === 'parallel' ? '每位各自回应你的消息，同时生成。' : replyMode === 'serial' ? '按点选顺序接话，后面的人能看到前面已经说出的内容。' : '至少选择一位参与者，再发送消息开始。'}</p>
            <Button variant="ghost" type="button" className="composer-panel-done" onClick={closeMenus}>完成设置</Button>
          </div>
        </ComposerPanel>
      )}
      {openPanel === 'observation' && <ComposerPanel title="旁观交谈" onClose={closeMenus}>
        <p className="composer-panel-description">你暂时不发言，让选定的人物自行交流一小段。不会发送输入框里的草稿，也不会更改消息对象。</p>
        <div className="composer-observation-heading"><strong>参与者 · {effectiveObservers.length}</strong>
          <Button variant="ghost" type="button" onClick={() => setObservers(mentionOptions.map((option) => option.id))}>选择全部在场人物</Button>
        </div>
        <div className="flex flex-wrap gap-2">
          {mentionOptions.map((option) => <Button variant="ghost" type="button" key={option.id} aria-pressed={effectiveObservers.includes(option.id)}
            className={`v7-recipient-option ${effectiveObservers.includes(option.id) ? 'is-active' : ''}`}
            onClick={() => setObservers((previous) => previous.includes(option.id) ? previous.filter((id) => id !== option.id) : [...previous, option.id])}>
            {option.label}{option.kind === '群体' ? ' · 群体' : ''}
          </Button>)}
        </div>
        {mentionOptions.length === 0 && <p className="composer-panel-description">当前没有可参与的在场人物。</p>}
        {observers.length !== effectiveObservers.length && <p className="composer-panel-description">已离场或静音的参与者本次不会发言。</p>}
        <div className="conversation-settings">
          <label>本次最多 <input type="number" min={1} max={30} value={observationMaxInput} aria-label="旁观交谈条数"
            onChange={(event) => setObservationMaxInput(event.target.value)} onBlur={() => setObservationMaxInput(String(observationMax))} /> 条</label>
          <label className="conversation-directive">交谈方向（可选）
            <textarea rows={3} aria-label="旁观交谈方向" value={observationDirective} onChange={(event) => setObservationDirective(event.target.value)} placeholder="例如：交流刚才发生的事，商量接下来的安排" />
          </label>
          <p>角色会自然选择接话对象，可以反复发言或提前结束。开始后可随时暂停、停止或干预。</p>
          <div className="conversation-start-actions">
            <Button variant="ghost" type="button" className="is-primary" aria-label="让他们聊一会儿" disabled={!effectiveObservers.length || sending || runActive || conversationBusy || switching || snapshotLoadedSessionId !== currentSessionId}
              onClick={() => void startObservation()}>{conversationActionBusy === 'start' ? '正在开始…' : '让他们聊一会儿'}</Button>
            <span>{effectiveObservers.length ? `${effectiveObservers.length} 位参与 · 最多 ${observationMax} 条` : '先选择参与者'}</span>
          </div>
        </div>
        {localRuns.length > 0 && <details className="conversation-history">
          <summary>此前交谈记录 · {localRuns.length} 段</summary>
          {[...localRuns].reverse().map((run) => <ConversationRunPanel key={run.id} run={run} history
            speakerName={null} busy={false} contextMatches={false} maxReplies={run.max_replies}
            onControl={() => {}} onIntervene={() => {}} />)}
          {latestConversationRun?.status === 'completed' && <Button variant="ghost" type="button" className="" aria-label="再聊一段"
            disabled={sending || runActive || conversationBusy || switching
              || latestConversationRun.scene_id !== activeScene?.id || latestConversationRun.player_identity_id !== (identity?.id ?? null)}
            onClick={() => { closeMenus(); void controlConversation('resume', latestConversationRun.mode === 'observe' ? observationMax : maxReplies, latestConversationRun.id); }}>再聊一段</Button>}
        </details>}
      </ComposerPanel>}
      {conversationRun && <ConversationRunPanel run={conversationRun} busy={conversationBusy || switching || (sending && !runActive)} maxReplies={conversationRun.mode === 'observe' ? observationMax : maxReplies}
        contextMatches={(!conversationRun.session_id || conversationRun.session_id === currentSessionId)
          && conversationRun.scene_id === activeScene?.id && conversationRun.player_identity_id === (identity?.id ?? null)}
        readOnly={Boolean(conversationRun.session_id && conversationRun.session_id !== currentSessionId)}
        speakerName={sessionChars.find((character) => character.id === conversationRun.current_speaker_id)?.card.name ?? currentGroups.find((group) => group.id === conversationRun.current_speaker_id)?.label ?? null}
        onControl={(kind, additional) => void controlConversation(kind, additional)} onIntervene={() => void intervene()} />}

      {/* R30.4 收件人告警 + 收件人栏 */}
      {recipientChars.length > 0 && (
        <div className="mb-2 flex flex-col gap-1.5">
          {allRecipientsAbsent && (
            <div className="rounded-lg bg-rose-50 px-3 py-1.5 text-xs text-rose-600">
              收件人均不在场，他们将看不到这条消息
            </div>
          )}
          {someRecipientsAbsent && (
            <div className="rounded-lg bg-amber-50 px-3 py-1.5 text-xs text-amber-600">
              收件人中 {absentRecipients.map((c) => c.card.name).join('、')}
              不在场，将看不到这条消息
            </div>
          )}
        </div>
      )}
      </div>
      <div className="v7-input-area flex min-w-0 flex-col gap-2">
        {mentionDraft && mentionMatches.length > 0 && <div className="v7-mention-suggestions" role="listbox" aria-label="点名补全">
          {mentionMatches.map((item) => <Button variant="ghost" key={item.id} type="button" role="option" onMouseDown={(event) => event.preventDefault()} onClick={() => insertMention(item)}>
            <span>{item.label}</span><small>{item.kind}</small>
          </Button>)}
        </div>}
        <form
          className="v7-input-box story-composer-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
        <div className="story-composer-manuscript">
        <textarea
          ref={inputRef}
          value={text}
          onChange={updateComposerText}
          onInput={updateComposerText}
          {...inputEvents}
          onKeyDown={(e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === 'Escape' && openPanel) {
              closeMenus();
              return;
            }
            inputEvents.onKeyDown(e);
          }}
          rows={2}
          aria-label="故事回应正文"
          enterKeyHint="send"
          placeholder={runActive ? '交谈进行中，可以先写草稿；暂停后即可发送' : '写下你的回应…'}
          className="composer-autosize mw-manuscript-input min-w-0 resize-none text-sm focus:outline-none"
        />
        </div>
        <div className="story-composer-foundation">
        <div className="v7-input-tools v7-composer-toolbar flex flex-wrap items-center gap-2" data-composer-controls>
          <ResponseStylePicker open={openPanel === 'style'} onToggle={() => togglePanel('style')} onClose={closeMenus} />
          <div className="story-composer-channel-tools" role="group" aria-label="文字通道">
          <Button variant="ghost" type="button" className="mw-tool-button" onClick={() => { if (inputRef.current) insertMarker(inputRef.current, setText, '（内心：'); }} title="记录潜台词和情绪">
            <Brain size={14} /> 内心
          </Button>
          <Button variant="ghost" type="button" className="mw-tool-button" onClick={() => { if (inputRef.current) insertMarker(inputRef.current, setText, '（旁白：'); }} title="描写场景事件">
            <Clapperboard size={14} /> 旁白
          </Button>
          </div>
          <Button variant="ghost" type="button" className="mw-tool-button story-composer-assist" onClick={() => setWritingOpen(true)} disabled={sending || switching || !currentSessionId || snapshotLoadedSessionId !== currentSessionId} data-testid="draft-assist" title="填写写作要求，生成草稿或参考素材，不会直接发送">
            <Sparkles size={14} /> 代笔
          </Button>
          <DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" type="button" className="mw-tool-button story-composer-more" aria-label="更多输入工具"><MoreHorizontal size={14} /><span>更多</span></Button></DropdownMenu.Trigger>
            <DropdownMenu.Portal><DropdownMenu.Content className="message-action-menu" align="start" side="top" sideOffset={8}>
              <DropdownMenu.Item asChild><Button variant="ghost" type="button" className="story-style-menu-entry" onClick={() => togglePanel('style')}>回应风格</Button></DropdownMenu.Item>
              <DropdownMenu.Item asChild><Button variant="ghost" type="button" aria-label="设置旁观交谈" onClick={() => togglePanel('observation')}>旁观交谈</Button></DropdownMenu.Item>
              <DropdownMenu.Item asChild><Button variant="ghost" type="button" onClick={() => togglePanel('members')}>角色与点名</Button></DropdownMenu.Item>
            </DropdownMenu.Content></DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
        <div className="story-composer-primary">
          <Button variant="primary" skin="primary" type="submit" disabled={sending || runActive || conversationBusy || switching || !text.trim() || !currentSessionId || snapshotLoadedSessionId !== currentSessionId || allRecipientsAbsent}
            title={runActive ? '请先暂停交谈，再发送回应' : allRecipientsAbsent ? '收件人均不在场' : undefined}
            className="story-composer-send" aria-busy={sending}>
            {sending ? <LoaderCircle size={18} className="story-send-spinner" aria-hidden="true" /> : <Send size={18} aria-hidden="true" />}<span>{runActive ? '交谈中' : sending ? '发送中…' : '发送'}</span>
          </Button>
        </div>
        </div>
        </form>
      </div>

      {writingOpen && currentSessionId && <WritingAssistant key={draftKey}
        branch={currentSessionId} identity={identity?.id ?? null} composer={text}
        onClose={() => setWritingOpen(false)} onApply={(next) => {
          useComposerStore.getState().backupDraft(text);
          setWritingUndo({ before: text, after: next });
          setText(next);
        }} />}

      {writingUndo && text === writingUndo.after && <Button variant="ghost" type="button" className="v7-panel-button-text"
        onClick={() => { setText(writingUndo.before); setWritingUndo(null); useComposerStore.getState().clearDraftBackup(); }}>
        撤销代笔填入
      </Button>}

      {/* 覆盖提示：候选覆盖掉了玩家自己写的草稿 → 一次性恢复入口 */}
      {draftBackup && draftBackup !== text.trim() && (
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-400">
          <span>输入框已被候选覆盖</span>
          <Button variant="ghost"
            type="button"
            data-testid="composer-restore-draft"
            onClick={() => {
              const backup = useComposerStore.getState().restoreDraft();
              if (backup != null) setText(backup);
              inputRef.current?.focus();
            }}
            className="rounded border border-slate-200 px-1.5 py-0.5 text-slate-500 hover:border-indigo-300 hover:text-indigo-500"
            title="恢复被覆盖前的草稿"
          >
            ↩ 恢复草稿
          </Button>
        </div>
      )}

      {/* R22 实时解析预览：有通道标记时显示三色 chips */}
      {hasChannels && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-slate-400">解析预览：</span>
          {previewParts.map((p, i) => (
            <span
              key={i}
              className={`inline-flex max-w-[14rem] items-center gap-1 truncate rounded-full px-2 py-0.5 text-[11px] ${
                p.kind === 'inner'
                  ? 'border border-violet-200 bg-violet-50 italic text-violet-600'
                  : p.kind === 'scene'
                    ? 'border border-amber-200 bg-amber-50 italic text-amber-700'
                    : 'bg-slate-100 text-slate-600'
              }`}
              title={
                p.kind === 'inner'
                  ? '内心气泡只对你显示，原文会作为个体角色的表演参考'
                  : p.kind === 'scene'
                    ? '旁白：场景事件，角色会反应'
                    : '台词：触发角色回应'
              }
            >
              {p.kind === 'inner' ? <Brain size={12} /> : p.kind === 'scene' ? <Clapperboard size={12} /> : <MessageCircle size={12} />} {p.text}
            </span>
          ))}
        </div>
      )}
      </div>
    </div></Popover.Anchor>
    </Popover.Root>
  );
}

