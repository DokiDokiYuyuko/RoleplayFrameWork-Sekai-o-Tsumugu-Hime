// R23 角色工坊：一句话需求 → AI 生成候选角色卡 → 选中进入 CharacterEditor 编辑 → 保存入库。
// 保存流程：store.importCharacter（createCharacter + 列表追加）→ 别名/运行时配置走一次 PATCH。

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { creationClient as api } from '../features/creation/creationClient';
import CharacterEditor from '../components/CharacterEditor';
import type { CharacterEditorPayload } from '../components/CharacterEditor';
import { Avatar, Button, Card, EmptyState, Field, Segmented, Tag, Textarea, WritingPanel } from '../design-system';
import { WorkbenchColumn, WorkbenchDesk } from '../design-system/Workbench';
import { Users } from '../design-system/Icon';
import { Checkbox } from '../design-system';
import { WorkshopNotice, WorkshopPaneSwitch, WorkshopWait } from './WorkshopUi';
import { useLorebookStore } from '../store/lorebookStore';
import { useCharacterStore } from '../store/characterStore';
import type { CharacterCard, CharacterPatch } from '../types';
import { characterCreation } from '../api/characterCreation';

interface Candidate {
  card: CharacterCard;
  aliases: string[];
}

/** 契约上 CharacterCard 字段全部必填，空白卡需要一份完整默认值对象 */
function blankCard(): CharacterCard {
  return {
    spec: 'chara_card_v2',
    spec_version: '2.0',
    name: '',
    description: '',
    appearance: '',
    traits_label: '能力与实力',
    traits: '',
    personality: '',
    scenario: '',
    first_mes: '',
    mes_example: '',
    alternate_greetings: [],
    system_prompt: null,
    post_history_instructions: null,
    creator_notes: '',
    creator: '',
    character_version: '',
    tags: [],
    extensions: {},
    avatar_path: null,
    source_format: 'ccv2',
  };
}

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function WorkshopCharacterPage() {
  const [searchParams] = useSearchParams();
  const initialSourceWorldId = searchParams.get('source_world');
  const books = useLorebookStore((s) => s.books);
  const loadBooks = useLorebookStore((s) => s.load);
  const updateCharacter = useCharacterStore((s) => s.updateCharacter);

  const [requirement, setRequirement] = useState('');
  const [detail, setDetail] = useState('');
  const [refIds, setRefIds] = useState<string[]>([]);
  const [count, setCount] = useState(3);
  const [generating, setGenerating] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [error, setError] = useState('');
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [pane, setPane] = useState<'form' | 'results'>('form');
  const [saving, setSaving] = useState(false);
  const [savedName, setSavedName] = useState('');
  const createdForRetry = useRef<import('../types').CharacterView | null>(null);
  const pendingAvatar = useRef<File | null>(null);
  const pendingFullBody = useRef<File | null>(null);
  // 保存成功后暂存“再生成一批”用的表单快照
  const lastForm = useRef({ requirement: '', detail: '', refIds: [] as string[], count: 3 });

  useEffect(() => {
    loadBooks().catch(() => undefined);
  }, [loadBooks]);

  useEffect(() => {
    if (!generating) {
      setElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    const tick = () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [generating]);

  interface GenerateForm { requirement: string; detail: string; refIds: string[]; count: number; }
  const runGenerate = async (form: GenerateForm) => {
    if (!form.requirement.trim() || generating) return;
    setGenerating(true); setPane('results'); setError(''); setSavedName('');
    lastForm.current = form;
    try {
      const result = await api.generateCharacters({ requirement: form.requirement.trim(), detail: form.detail.trim() || undefined, reference_lorebook_ids: form.refIds, count: form.count });
      setCandidates(result);
    } catch (reason) { setError(errMsg(reason)); }
    finally { setGenerating(false); }
  };
  const generate = () => runGenerate({ requirement, detail, refIds, count });

  // Retain the durable identity after partial success, and while edits continue during a save.
  const handleSave = async ({ card, aliases, runtime, source_world_id }: CharacterEditorPayload) => {
    const avatarToSave = pendingAvatar.current;
    const fullBodyToSave = pendingFullBody.current;
    setSaving(true);
    try {
      const created = createdForRetry.current || await characterCreation.create(card, source_world_id ?? null);
      if (!createdForRetry.current) useCharacterStore.getState().upsert(created);
      createdForRetry.current = created;
      const patch: CharacterPatch = { card, aliases, source_world_id: source_world_id ?? null };
      if (runtime) {
        patch.model = runtime.model.trim();
        patch.base_url = runtime.base_url.trim();
        patch.sampling = { max_tokens: runtime.max_tokens };
      }
      await updateCharacter(created.id, patch);
      if (avatarToSave) { await useCharacterStore.getState().uploadMedia(created.id, 'avatar', avatarToSave); if (pendingAvatar.current === avatarToSave) pendingAvatar.current = null; }
      if (fullBodyToSave) { await useCharacterStore.getState().uploadMedia(created.id, 'full-body', fullBodyToSave); if (pendingFullBody.current === fullBodyToSave) pendingFullBody.current = null; }
      setSavedName(card.name || created.card.name);
    } finally { setSaving(false); }
  };
  const handleSaveSucceeded = (_asCopy: boolean, hasLaterEdits = false) => {
    if (hasLaterEdits) return;
    createdForRetry.current = null; setSelected(null); setCandidates(null); setPane('results');
  };
  const toggleRef = (id: string) => setRefIds(ids => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id]);
  const regenerate = () => {
    const form = lastForm.current;
    setSavedName(''); setCandidates(null); setRequirement(form.requirement); setDetail(form.detail); setRefIds(form.refIds); setCount(form.count);
    void runGenerate(form);
  };
  const finish = () => { setSavedName(''); setCandidates(null); setRequirement(''); setDetail(''); setRefIds([]); setCount(3); setError(''); };

  return <WorkbenchDesk layout="form-main" className="workshop-desk" data-pane={pane}>
    <WorkshopPaneSwitch value={pane} onValueChange={setPane} />
    <WorkbenchColumn variant="form" className="workshop-form">
      <div className="workbench-column__head">
        <h2 className="workshop-column-title">生成角色</h2>
        <Button variant="tonal" size="sm" disabled={generating || saving} onClick={() => setSelected({ card: blankCard(), aliases: [] })}>写下人物设定</Button>
      </div>
      <div className="workbench-column__body">
        <Field label="一句话需求" required><Textarea rows={3} disabled={generating} value={requirement} onChange={e => setRequirement(e.target.value)} placeholder="例如：一位表面冷淡实则关心队友的军医，在末日小队中负责治疗" /></Field>
        <Field label="补充设定" optional className="workshop-grow"><WritingPanel showCount disabled={generating} value={detail} onChange={e => setDetail(e.target.value)} placeholder="背景、口癖、人物关系、行为禁忌等额外细节" /></Field>
        <Field label={<>参考世界书 <span className="workshop-hint">已选 {refIds.length} 本</span></>} optional group>
          {books.length === 0 ? <p className="workshop-hint">暂无世界书，可在世界书工坊生成或导入</p> : <div className="workshop-checklist">
            {books.map(book => <Checkbox key={book.id} disabled={generating} checked={refIds.includes(book.id)} onChange={() => toggleRef(book.id)} label={<span className="workshop-reference-label"><strong>{book.name}</strong><small>{book.entries.length} 条</small></span>} />)}
          </div>}
        </Field>
        <Field label="候选数量" group><Segmented aria-label="候选数量" value={String(count)} disabled={generating} onValueChange={value => setCount(Number(value))} options={[2, 3, 4, 5].map(value => ({ value: String(value), label: `${value} 个` }))} /></Field>
      </div>
      <div className="workbench-column__foot workshop-foot">
        <Button variant="primary" size="lg" skin="primary" loading={generating} disabled={!requirement.trim() || generating || saving} onClick={() => void generate()}>生成候选角色</Button>
        <p className="workshop-hint">候选只是草稿，编辑并保存后才会进入角色库。</p>
      </div>
    </WorkbenchColumn>
    <WorkbenchColumn variant="main" className="workshop-results">
      <div className="workbench-column__head"><h2 className="workshop-column-title">候选角色</h2>{candidates?.length ? <Tag tone="accent">{candidates.length}</Tag> : null}</div>
      <div className="workbench-column__body">
        {generating && <WorkshopNotice busy><p>模型正在生成候选卡，可能需要 1 分钟以上；<WorkshopWait seconds={elapsedSeconds} />。</p><p>离开本页后任务不会保留。</p></WorkshopNotice>}
        {error && <WorkshopNotice tone="danger" actions={<Button size="sm" disabled={generating} onClick={() => void generate()}>重试</Button>}><p>生成失败：{error}</p><p>你的输入已保留。</p></WorkshopNotice>}
        {savedName && <WorkshopNotice tone="success" actions={<><Button variant="tonal" size="sm" onClick={regenerate} disabled={generating}>再生成一批</Button><Button variant="ghost" size="sm" onClick={finish}>完成</Button></>}><p>角色「{savedName}」已保存到角色库，可在角色页查看与导出。</p></WorkshopNotice>}
        {candidates && candidates.length > 0 && <div className="workshop-candidate-grid">
          {candidates.map((candidate, index) => <Card asChild interactive key={index} className="workshop-candidate"><button type="button" disabled={generating || saving} onClick={() => setSelected(candidate)}>
            <div className="workshop-candidate__head"><Avatar name={candidate.card.name || '?'} size={40} dense /><div className="workshop-candidate__identity"><h3>{candidate.card.name || '（未命名）'}</h3>{candidate.aliases.length > 0 && <small>别名建议：{candidate.aliases.join(' / ')}</small>}</div></div>
            <p className="workshop-excerpt">{candidate.card.description || '（无描述）'}</p>
            {candidate.card.tags.length > 0 && <div className="workshop-tags">{candidate.card.tags.slice(0, 4).map(tag => <Tag key={tag}>{tag}</Tag>)}</div>}
            <span className="workshop-hint">编辑并保存 →</span>
          </button></Card>)}
        </div>}
        {!generating && !error && !candidates && !savedName && <div className="workshop-empty"><EmptyState icon={Users} title="还没有候选角色" description="写下一句话需求，选好参考世界书与数量后生成。候选会显示在这里。" /></div>}
        {candidates?.length === 0 && !generating && !error && <div className="workshop-empty"><EmptyState icon={Users} title="没有生成候选" description="未生成任何候选，请调整需求后重试。" /></div>}
      </div>
      <div className="workbench-column__foot"><p className="workshop-hint">{candidates?.length ? `共 ${candidates.length} 张候选 · 点击卡片编辑，保存后才会入库` : '候选生成和入库分别确认'}</p></div>
    </WorkbenchColumn>
    {selected && <CharacterEditor initialCard={selected.card} initialAliases={selected.aliases} initialSourceWorldId={initialSourceWorldId}
      onAvatarPicked={file => { pendingAvatar.current = file; }} onFullBodyPicked={file => { pendingFullBody.current = file; }}
      onSave={handleSave} onSaveSucceeded={handleSaveSucceeded}
      onClose={() => { pendingAvatar.current = null; pendingFullBody.current = null; createdForRetry.current = null; setSelected(null); }} busy={saving} />}
  </WorkbenchDesk>;
}
