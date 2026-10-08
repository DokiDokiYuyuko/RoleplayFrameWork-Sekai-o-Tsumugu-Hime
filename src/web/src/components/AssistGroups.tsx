import { storyRuntime } from '../features/stories/storyRuntime';
import { createClientId } from '../utils/clientId.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, LogOut, MessageCircle, Pencil, Plus, UsersRound, WandSparkles } from 'lucide-react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from '../store/chatStore';
import { useComposerStore } from '../store/composerStore';
import type { GroupActor, GroupDraft, GroupSource, GroupSourcesResult } from '../types';

type View = 'list' | 'create' | 'edit';
const blankDraft = (): GroupDraft => ({
  label: '', aliases: [], count: null, public_brief: '', current_state: '', director_note: '', participation: 'on_cue',
});

export function AssistGroups() {
  const sessionId = useChatStore((s) => s.currentSessionId);
  const sending = useChatStore((s) => s.sending);
  const scene = useChatStore((s) => s.activeScene);
  const groups = useChatStore((s) => s.sessionGroups);
  const session = useChatStore((s) => s.sessions.find((item) => item.id === s.currentSessionId));
  const [view, setView] = useState<View>('list');
  const [editing, setEditing] = useState<GroupActor | null>(null);
  const [draft, setDraft] = useState<GroupDraft>(blankDraft);
  const [sourceResult, setSourceResult] = useState<GroupSourcesResult | null>(null);
  const [sourceId, setSourceId] = useState('');
  const [sourceSnapshot, setSourceSnapshot] = useState('');
  const [sourceMode, setSourceMode] = useState<'manual' | 'biology'>('manual');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lastCreateRequest = useRef<{ fingerprint: string; key: string; input: Record<string, unknown> } | null>(null);
  const replyKeys = useRef(new Map<string, string>());

  const currentGroups = useMemo(() => groups.filter((group) =>
    group.status === 'active' && group.scene_id === scene?.id && scene?.group_ids.includes(group.id),
  ), [groups, scene]);
  const pastGroups = useMemo(() => groups.filter((group) => !currentGroups.some((active) => active.id === group.id)), [groups, currentGroups]);
  const selectedSource = sourceResult?.sources.find((source) => source.id === sourceId) ?? null;

  const refresh = async () => {
    if (!sessionId) return;
    const ownsBranch = storyRuntime.capture(sessionId);
    const [groups, sceneResult] = await Promise.all([api.listGroups(sessionId), api.listScenes(sessionId)]);
    if (!ownsBranch()) return;
    storyRuntime.applyRoster(sessionId, groups, sceneResult);
  };

  const latestRevision = async () => {
    if (!sessionId) return session?.branch_revision ?? 0;
    const setup = await api.getSessionSetup(sessionId);
    if (useChatStore.getState().currentSessionId !== sessionId) return 0;
    return setup.meta.branch_revision;
  };

  useEffect(() => {
    setView('list'); setEditing(null); setError('');
    replyKeys.current.clear();
    if (!sessionId) return;
    const ownsBranch = storyRuntime.capture(sessionId);
    void Promise.all([api.listGroups(sessionId), api.listGroupSources(sessionId), api.listScenes(sessionId)])
      .then(([items, sources, sceneResult]) => {
        if (!ownsBranch()) return;
        storyRuntime.applyRoster(sessionId, items, sceneResult);
        setSourceResult(sources);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : '群体资料加载失败'));
  }, [sessionId]);

  const beginCreate = () => {
    if (!sessionId || busy) return;
    const requested = sessionId;
    setDraft(blankDraft()); setEditing(null); setSourceId(''); setSourceSnapshot('');
    lastCreateRequest.current = null;
    setBusy(true); setError('');
    void api.listGroupSources(requested)
      .then((sources) => {
        if (useChatStore.getState().currentSessionId !== requested) return;
        setSourceResult(sources);
        setSourceMode(sources.sources.length ? 'biology' : 'manual');
        setView('create');
      })
      .catch(() => {
        if (useChatStore.getState().currentSessionId !== requested) return;
        setSourceMode('manual');
        setError('生物设定名单刷新失败，可先手动填写');
        setView('create');
      })
      .finally(() => setBusy(false));
  };
  const beginEdit = (group: GroupActor) => {
    setDraft({
      label: group.label, aliases: [...group.aliases], count: group.count,
      public_brief: group.public_brief, current_state: group.current_state,
      director_note: group.director_note, participation: group.participation,
    });
    setEditing(group); setSourceMode('manual'); setError(''); setView('edit');
  };

  const setField = <K extends keyof GroupDraft>(key: K, value: GroupDraft[K]) =>
    setDraft((valueNow) => ({ ...valueNow, [key]: value }));

  const generateDraft = async () => {
    if (!sessionId || !selectedSource || busy) return;
    setBusy(true); setError('');
    try {
      const result = await api.draftGroup(sessionId, { archive_id: selectedSource.id, archive_revision: selectedSource.revision });
      setDraft(result.draft); setSourceSnapshot(result.source_snapshot);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '生成草稿失败'); }
    finally { setBusy(false); }
  };

  const save = async () => {
    if (!sessionId || busy || sending || !draft.label.trim()) return;
    setBusy(true); setError('');
    try {
      if (view === 'edit' && editing) {
        await api.patchGroup(sessionId, editing.id, { ...draft, expected_branch_revision: await latestRevision() });
      } else {
        if (!scene) throw new Error('当前没有可加入的场景');
        const baseInput = {
          ...draft, label: draft.label.trim(), expected_scene_id: scene.id,
          source_archive_id: sourceMode === 'biology' ? sourceId || null : null,
          source_archive_revision: sourceMode === 'biology' ? selectedSource?.revision ?? null : null,
          source_snapshot: sourceMode === 'biology' ? sourceSnapshot : draft.public_brief,
        };
        const fingerprint = JSON.stringify(baseInput);
        if (lastCreateRequest.current?.fingerprint !== fingerprint) {
          const [expectedBranchRevision, currentScenes] = await Promise.all([latestRevision(), api.listScenes(sessionId)]);
          if (currentScenes.active_scene_id !== scene.id) throw new Error('场景已切换，请返回后重新打开群体表单');
          lastCreateRequest.current = {
            fingerprint,
            key: createClientId(),
            input: { ...baseInput, expected_branch_revision: expectedBranchRevision },
          };
        }
        await api.createGroup(sessionId, {
          ...(lastCreateRequest.current.input as typeof baseInput & { expected_branch_revision: number }),
          idempotency_key: lastCreateRequest.current.key,
        });
      }
      await refresh(); setView('list'); setEditing(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存群体失败');
      void refresh().catch(() => undefined);
    } finally { setBusy(false); }
  };

  const leave = async (group: GroupActor) => {
    if (!sessionId || busy || sending) return;
    setBusy(true); setError('');
    try { await api.leaveGroup(sessionId, group.id, await latestRevision()); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '群体离场失败'); void refresh().catch(() => undefined); }
    finally { setBusy(false); }
  };

  const reply = async (group: GroupActor) => {
    if (!sessionId || busy || sending) return;
    setBusy(true); setError('');
    try {
      const requestKey = replyKeys.current.get(group.id) ?? createClientId();
      replyKeys.current.set(group.id, requestKey);
      const ownsBranch = storyRuntime.capture(sessionId);
      const message = await api.replyGroup(sessionId, group.id, requestKey);
      if (!ownsBranch()) return;
      storyRuntime.applyMessage(sessionId, message);
      await refresh();
      replyKeys.current.delete(group.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '群体回应失败'); }
    finally { setBusy(false); }
  };

  const mention = (group: GroupActor) => {
    useComposerStore.getState().requestPrefill(`@${group.label} `, [group.id]);
  };

  return <section className="v7-panel-section v7-group-section" data-testid="assist-groups">
    {view === 'list' ? <>
      <div className="v7-panel-section-head">
        <div><h3>群体角色</h3><p>让一群临时参与者进入当前场景，不必逐个建卡</p></div>
        <button type="button" className="v7-group-add" onClick={beginCreate} disabled={!scene || busy || sending}>
          <Plus size={15} /> 添加
        </button>
      </div>
      {error && <p className="v7-group-error" role="alert">{error}</p>}
      {currentGroups.length ? <div className="v7-group-list">
        {currentGroups.map((group) => <GroupCard key={group.id} group={group} busy={busy || sending}
          onMention={() => mention(group)} onReply={() => void reply(group)}
          onEdit={() => beginEdit(group)} onLeave={() => void leave(group)} />)}
      </div> : <div className="v7-group-empty">
        <UsersRound size={19} />
        <span>{scene ? '当前场景还没有群体参与者' : '等待当前场景载入'}</span>
      </div>}
      {pastGroups.length > 0 && <details className="v7-group-history">
        <summary>本故事曾出现 · {pastGroups.length}</summary>
        <div>{pastGroups.map((group) => <p key={group.id}><strong>{group.label}</strong>{group.count == null ? '' : ` ×${group.count}`}<span>已离场</span></p>)}</div>
      </details>}
    </> : <>
      <div className="v7-panel-section-head v7-group-editor-head">
        <button type="button" className="v7-group-back" onClick={() => { setView('list'); setError(''); }}><ArrowLeft size={15} /> 返回群体列表</button>
      </div>
      <div className="v7-group-editor">
        <div><h3>{view === 'edit' ? '编辑群体' : '添加群体'}</h3><p>设定一份经过你确认的故事内快照</p></div>
        {view === 'create' && <>
          <div className="v7-group-source-tabs">
            <button type="button" className={sourceMode === 'biology' ? 'is-active' : ''} onClick={() => setSourceMode('biology')} disabled={!sourceResult?.sources.length}>从生物设定生成</button>
            <button type="button" className={sourceMode === 'manual' ? 'is-active' : ''} onClick={() => setSourceMode('manual')}>手动填写</button>
          </div>
          {sourceMode === 'biology' && <div className="v7-group-source-box">
            {sourceResult?.sources.length ? <>
              <label>选择当前世界的公开生物设定
                <select value={sourceId} onChange={(event) => { setSourceId(event.target.value); setSourceSnapshot(''); }}>
                  <option value="">选择一项…</option>
                  {sourceResult.sources.map((source: GroupSource) => <option key={source.id} value={source.id}>{source.title} · 修订 {source.revision}</option>)}
                </select>
              </label>
              {selectedSource && <details><summary>预览与精简来源 · 约 {selectedSource.estimated_tokens} tokens</summary><textarea rows={6} value={sourceSnapshot || selectedSource.excerpt} onChange={(event) => setSourceSnapshot(event.target.value)} aria-label="群体来源设定摘录" /></details>}
              <button type="button" className="v7-group-draft-button" disabled={!selectedSource || busy} onClick={() => void generateDraft()}><WandSparkles size={14} />{busy ? '整理中…' : '生成可编辑草稿'}</button>
            </> : <p className="v7-group-muted">当前故事没有可用的公开生物设定，可以切换到手动填写。</p>}
          </div>}
        </>}
        <label>群体名称<input value={draft.label} maxLength={120} onChange={(event) => setField('label', event.target.value)} placeholder="例如：洞穴巡逻的哥布林" /></label>
        <div className="v7-group-inline-fields">
          <label>人数<input type="number" min="0" max="1000000" value={draft.count ?? ''} onChange={(event) => setField('count', event.target.value === '' ? null : Math.max(0, Number(event.target.value)))} placeholder="不确定" /></label>
          <label>参与方式<select value={draft.participation} onChange={(event) => setField('participation', event.target.value as GroupDraft['participation'])}><option value="on_cue">互动时回应</option><option value="occasional">偶尔参与</option></select></label>
        </div>
        <label>别名<input value={draft.aliases.join('、')} onChange={(event) => setField('aliases', event.target.value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean))} placeholder="哥布林、巡逻队" /></label>
        <label>共同特征<textarea rows={3} value={draft.public_brief} onChange={(event) => setField('public_brief', event.target.value)} placeholder="在场角色可以观察到的外形、习性或装备" /></label>
        <label>此刻状态<textarea rows={2} value={draft.current_state} onChange={(event) => setField('current_state', event.target.value)} placeholder="例如：守着洞口，对火光很警惕" /></label>
        <label>幕后意图<textarea rows={2} value={draft.director_note} onChange={(event) => setField('director_note', event.target.value)} placeholder="只供群体回应参考，不会注入其他角色" /></label>
        {error && <p className="v7-group-error" role="alert">{error}</p>}
        <div className="v7-group-form-actions"><button type="button" onClick={() => setView('list')}>取消</button><button type="button" className="is-primary" disabled={busy || sending || !draft.label.trim()} onClick={() => void save()}>{busy ? '保存中…' : view === 'edit' ? '保存修改' : '加入当前场景'}</button></div>
      </div>
    </>}
  </section>;
}

function GroupCard({ group, busy, onMention, onReply, onEdit, onLeave }: {
  group: GroupActor; busy: boolean; onMention: () => void; onReply: () => void; onEdit: () => void; onLeave: () => void;
}) {
  return <article className="v7-group-card">
    <div className="v7-group-card-title"><span className="v7-group-icon"><UsersRound size={15} /></span><div><strong>{group.label}</strong><p>{group.count == null ? '人数未定' : `${group.count} 名成员`}<span>群体</span></p></div></div>
    {(group.current_state || group.public_brief) && <p className="v7-group-card-state">{group.current_state || group.public_brief}</p>}
    <div className="v7-group-card-actions">
      <button type="button" onClick={onMention} disabled={busy} title="在输入框中 @ 这个群体">@ 点名</button>
      <button type="button" onClick={onReply} disabled={busy || group.count === 0} title="让群体立即回应"><MessageCircle size={13} /> 现在回应</button>
      <button type="button" onClick={onEdit} disabled={busy} title="编辑群体状态"><Pencil size={13} /></button>
      <button type="button" onClick={onLeave} disabled={busy} title="让群体离开当前场景"><LogOut size={13} /></button>
    </div>
  </article>;
}
