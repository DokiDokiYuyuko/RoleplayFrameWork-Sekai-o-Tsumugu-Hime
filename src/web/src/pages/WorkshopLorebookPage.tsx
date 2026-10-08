import { useEffect, useState } from 'react';
import { libraryClient as api } from '../features/library/libraryClient';
import { exportLorebookUrl } from '../api/Api';
import { Button, EmptyState, Field, IconButton, Segmented, Select, Switch, Tag, Textarea, TextInput, WritingPanel } from '../design-system';
import { WorkbenchColumn, WorkbenchDesk } from '../design-system/Workbench';
import { LibraryBig, Plus, Trash2, Download } from '../design-system/Icon';
import { WorkshopDisclosure, WorkshopNotice, WorkshopPaneSwitch, WorkshopWait, useElapsedSeconds } from './WorkshopUi';
import { nextWorkshopPreviewUid } from '../utils/workshopPolicy';
import { useLorebookStore } from '../store/lorebookStore';
import type { InsertAnchor, Lorebook, LorebookEntry } from '../types';

/**
 * R29 世界书工坊：AI 生成新书 / 扩展现有书 → 预览编辑 → 入库。
 *
 * uid 策略：预览期新条目用负数占位（-1、-2…），入库/合并时统一重排——
 * 生成模式重排为 1..n；扩展模式重排为 max(旧书 uid)+1..+n，保证书内唯一。
 */

type Mode = 'generate' | 'extend';

/** 预览条目：isNew 在扩展模式下驱动左侧 indigo 竖条 */
interface PreviewItem {
  entry: LorebookEntry;
  isNew: boolean;
}

const ANCHOR_OPTS: { value: InsertAnchor; label: string; desc: string }[] = [
  { value: 'system', label: '世界设定区', desc: '插入在角色定义之前，适合核心设定' },
  { value: 'at_depth', label: '对话记录深度', desc: '按深度值插入对话历史中' },
  { value: 'near', label: '紧邻最新消息', desc: '贴近上下文末尾，适合即时状态' },
];

const makeEntry = (uid: number): LorebookEntry => ({
  uid,
  keys: [],
  secondary_keys: [],
  content: '',
  comment: '',
  enabled: true,
  constant: false,
  selective: false,
  selective_logic: 0,
  order: 100,
  anchor: 'at_depth',
  depth: 4,
  probability: 100,
  extensions: {},
});

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export default function WorkshopLorebookPage() {
  const { books, load, updateEntries } = useLorebookStore();

  useEffect(() => {
    if (useLorebookStore.getState().books.length === 0) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [mode, setMode] = useState<Mode>('generate');
  const [pane, setPane] = useState<'form' | 'results'>('form');

  // -- 生成表单 --
  const [topic, setTopic] = useState('');
  const [conceptsText, setConceptsText] = useState('');
  const [refBookId, setRefBookId] = useState('');
  const [genCount, setGenCount] = useState(8);

  // -- 扩展表单 --
  const [extBookId, setExtBookId] = useState('');
  const [direction, setDirection] = useState('');
  const [extCount, setExtCount] = useState(4);

  // -- 生成/扩展共用状态 --
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewName, setPreviewName] = useState('');
  const [preview, setPreview] = useState<PreviewItem[]>([]);
  /** 生成接口返回的整本（保留 scan_depth 等默认字段），入库时以它为底 */
  const [genBase, setGenBase] = useState<Lorebook | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedBook, setSavedBook] = useState<{ id: string; name: string } | null>(null);

  const elapsedSeconds = useElapsedSeconds(busy);

  const switchMode = (m: Mode) => {
    if (m === mode) return;
    setMode(m);
    setError(null);
    setPreview([]);
    setGenBase(null);
    setSavedBook(null);
    setPreviewName('');
  };

  const doGenerate = async () => {
    if (!topic.trim() || busy) return;
    setBusy(true);
    setPane('results');
    setError(null);
    setSavedBook(null);
    try {
      const concepts = conceptsText
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
      const book = await api.generateLorebook({
        topic: topic.trim(),
        concepts: concepts.length > 0 ? concepts : undefined,
        reference_lorebook_id: refBookId || undefined,
        count: genCount,
      });
      setGenBase(book);
      setPreviewName(book.name);
      // uid 换成负数占位，入库时统一重排
      setPreview(book.entries.map((e, i) => ({ entry: { ...e, uid: -(i + 1) }, isNew: true })));
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成失败，请重试');
    } finally {
      setBusy(false);
    }
  };

  const doExtend = async () => {
    if (!extBookId || !direction.trim() || busy) return;
    setBusy(true);
    setPane('results');
    setError(null);
    setSavedBook(null);
    try {
      const entries = await api.extendLorebook(extBookId, direction.trim(), extCount);
      setGenBase(null);
      setPreview(entries.map((e, i) => ({ entry: { ...e, uid: -(i + 1) }, isNew: true })));
    } catch (err) {
      setError(err instanceof Error ? err.message : '扩展失败，请重试');
    } finally {
      setBusy(false);
    }
  };

  // -- 预览编辑 --
  const patchEntry = (uid: number, patch: Partial<LorebookEntry>) =>
    setPreview((ps) =>
      ps.map((p) => (p.entry.uid === uid ? { ...p, entry: { ...p.entry, ...patch } } : p)),
    );
  const removeEntry = (uid: number) => setPreview((ps) => ps.filter((p) => p.entry.uid !== uid));
  const addEntry = () =>
    setPreview((ps) => [...ps, { entry: makeEntry(nextWorkshopPreviewUid(ps.map(item => item.entry))), isNew: true }]);

  // -- 入库 --
  const doCreate = async () => {
    if (preview.length === 0 || saving) return;
    setSaving(true);
    setError(null);
    try {
      const entries = preview.map((p, i) => ({ ...p.entry, uid: i + 1 }));
      const base: Lorebook =
        genBase ?? {
          id: '',
          name: '',
          description: '',
          entries: [],
          scan_depth: 4,
          token_budget: 2048,
          recursive_scanning: false,
          source_format: 'st',
        };
      const created = await api.createLorebook({
        ...base,
        name: previewName.trim() || '未命名世界书',
        entries,
      });
      await load(); // 刷新 store，左侧列表可见新书
      setSavedBook({ id: created.id, name: created.name });
      setPreview([]);
      setGenBase(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '入库失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  const doMerge = async () => {
    if (preview.length === 0 || saving) return;
    setSaving(true);
    setError(null);
    try {
      const fresh = useLorebookStore.getState().books.find((b) => b.id === extBookId);
      if (!fresh) throw new Error('目标世界书不存在，请重新选择');
      const maxUid = Math.max(0, ...fresh.entries.map((e) => e.uid));
      const newEntries = preview.map((p, i) => ({ ...p.entry, uid: maxUid + 1 + i }));
      await updateEntries(fresh.id, [...fresh.entries, ...newEntries]); // PATCH + 刷新 store
      setSavedBook({ id: fresh.id, name: fresh.name });
      setPreview([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : '合并失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  const extendTarget = books.find((b) => b.id === extBookId) ?? null;
  return <WorkbenchDesk layout="form-main" className="workshop-desk" data-pane={pane}>
    <WorkshopPaneSwitch value={pane} onValueChange={setPane} />
    <WorkbenchColumn variant="form" className="workshop-form">
      <div className="workbench-column__head"><h2 className="workshop-column-title">世界书工坊</h2></div>
      <div className="workbench-column__band"><Segmented aria-label="工坊模式" value={mode} disabled={busy || saving} onValueChange={value => switchMode(value as Mode)} options={[{ value: 'generate', label: '生成新书' }, { value: 'extend', label: '扩展现有书' }]} /></div>
      <div className="workbench-column__body">
        {mode === 'generate' ? <>
          <Field label="主题 / 世界观描述" required className="workshop-grow"><WritingPanel showCount disabled={busy} value={topic} onChange={event => setTopic(event.target.value)} placeholder="例：一个蒸汽朋克与魔法共存的浮空城邦世界，讲述各城邦间的贸易、阴谋与冒险……" /></Field>
          <Field label="想覆盖的概念" optional hint="每行一个，可留空"><Textarea rows={4} disabled={busy} value={conceptsText} onChange={event => setConceptsText(event.target.value)} placeholder={'例：\n三大浮空城邦\n禁咒公会\n城间航运与飞艇'} /></Field>
          <Field label="参考世界书" optional hint="生成时保持兼容"><Select value={refBookId || 'none'} disabled={busy} onValueChange={value => setRefBookId(value === 'none' ? '' : value)} options={[{ value: 'none', label: '不参考（全新生成）' }, ...books.map(book => ({ value: book.id, label: `${book.name}（${book.entries.length} 条）` }))]} /></Field>
          <Field label="生成条数" hint="1–20 条"><TextInput className="workshop-number" type="number" min={1} max={20} disabled={busy} value={genCount} onChange={event => setGenCount(clamp(Number(event.target.value) || 1, 1, 20))} /></Field>
        </> : <>
          <Field label="选择世界书" required hint={books.length ? undefined : '暂无世界书，请先在「世界书」页导入或生成。'}><Select value={extBookId} disabled={busy} placeholder="请选择…" onValueChange={setExtBookId} options={books.map(book => ({ value: book.id, label: `${book.name}（${book.entries.length} 条）` }))} /></Field>
          <Field label="补充方向" required className="workshop-grow"><WritingPanel showCount disabled={busy} value={direction} onChange={event => setDirection(event.target.value)} placeholder="例：补充城邦下层贫民区的势力划分、黑市与日常冲突……" /></Field>
          <Field label="新增条数" hint="1–20 条"><TextInput className="workshop-number" type="number" min={1} max={20} disabled={busy} value={extCount} onChange={event => setExtCount(clamp(Number(event.target.value) || 1, 1, 20))} /></Field>
        </>}
      </div>
      <div className="workbench-column__foot workshop-foot"><Button variant="primary" skin="primary" size="lg" loading={busy} disabled={busy || saving || (mode === 'generate' ? !topic.trim() : !extBookId || !direction.trim())} onClick={() => mode === 'generate' ? void doGenerate() : void doExtend()}>{mode === 'generate' ? '开始生成' : '开始扩展'}</Button><p className="workshop-hint">结果先预览编辑，确认后再入库。</p></div>
    </WorkbenchColumn>
    <WorkbenchColumn variant="main" className="workshop-results">
      <div className="workbench-column__head"><h2 className="workshop-column-title">预览条目</h2>{preview.length > 0 && <><Tag tone="accent">{preview.length}</Tag><div className="workshop-column-actions"><Button variant="tonal" size="sm" icon={Plus} disabled={busy || saving} onClick={addEntry}>新增空条目</Button></div></>}</div>
      <div className="workbench-column__body">
        {busy && <WorkshopNotice busy><p>{mode === 'generate' ? 'AI 生成中' : 'AI 扩展中'}，通常需要 10–30 秒；<WorkshopWait seconds={elapsedSeconds} />。</p></WorkshopNotice>}
        {!busy && error && <WorkshopNotice tone="danger" actions={<Button size="sm" onClick={() => mode === 'generate' ? void doGenerate() : void doExtend()}>重试</Button>}><p>{error}</p><p>你的输入和未保存的预览已保留。</p></WorkshopNotice>}
        {!busy && savedBook && <WorkshopNotice tone="success" actions={<Button variant="tonal" size="sm" icon={Download} onClick={() => window.open(exportLorebookUrl(savedBook.id), '_blank')}>导出 ST JSON</Button>}><p>已入库《{savedBook.name}》，可在「世界书」页查看编辑。</p></WorkshopNotice>}
        {!busy && preview.length > 0 && <>
          {mode === 'generate' ? <Field label="书名" hint="入库前可修改"><TextInput value={previewName} disabled={saving} onChange={event => setPreviewName(event.target.value)} placeholder="生成的书名可能是主题截断，建议改成语义完整的名字" /></Field> : <div><strong>合并到《{extendTarget?.name ?? '未选择'}》</strong>{extendTarget && <p className="workshop-hint">现有 {extendTarget.entries.length} 条，合并后 {extendTarget.entries.length + preview.length} 条；旧条目保持不动。</p>}</div>}
          <div className="workshop-entry-list">{preview.map((item, index) => <EntryCard key={item.entry.uid} entry={item.entry} showNewBar={item.isNew && mode === 'extend'} busy={saving} defaultOpen={index === 0} onChange={patch => patchEntry(item.entry.uid, patch)} onRemove={() => removeEntry(item.entry.uid)} />)}</div>
        </>}
        {!busy && preview.length === 0 && !error && !savedBook && <div className="workshop-empty"><EmptyState icon={LibraryBig} title="还没有预览条目" description="填写表单并生成，结果会在这里预览、编辑后入库。" /></div>}
      </div>
      <div className="workbench-column__foot workshop-foot"><p className="workshop-hint">共 {preview.length} 条{mode === 'extend' && preview.length > 0 ? ' · 带竖条标记的为新增条目' : ''}</p>{preview.length > 0 && !busy && <Button variant="tonal" loading={saving} disabled={saving} onClick={() => mode === 'generate' ? void doCreate() : void doMerge()}>{mode === 'generate' ? '一键入库' : '合并入库'}</Button>}</div>
    </WorkbenchColumn>
  </WorkbenchDesk>;
}

function EntryCard({ entry, showNewBar, busy, defaultOpen, onChange, onRemove }: {
  entry: LorebookEntry; showNewBar: boolean; busy: boolean; defaultOpen: boolean;
  onChange: (patch: Partial<LorebookEntry>) => void; onRemove: () => void;
}) {
  const [keyInput, setKeyInput] = useState('');
  const addKey = () => { const value = keyInput.trim(); if (!value || entry.keys.includes(value)) return; onChange({ keys: [...entry.keys, value] }); setKeyInput(''); };
  return <section className="workshop-entry" data-new={showNewBar}>
    <div className="workshop-entry__head"><div><h3>{entry.keys[0] || '新条目'}</h3><p className="workshop-hint">条目 #{entry.uid < 0 ? '新' : entry.uid} · {entry.constant ? '常驻注入' : '关键词触发'}{!entry.enabled ? ' · 停用' : ''}</p></div><IconButton label={`删除条目 ${entry.keys[0] || '新条目'}`} icon={Trash2} disabled={busy} onClick={onRemove} /></div>
    <div className="workshop-entry__body"><WorkshopDisclosure title="编辑条目" defaultOpen={defaultOpen}>
      <Field label={<>关键词 <code>keys</code></>} hint="回车添加；触发词命中即注入"><div className="workshop-row"><TextInput disabled={busy} value={keyInput} onChange={event => setKeyInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addKey(); } }} placeholder="输入关键词后回车" /><Button disabled={busy || !keyInput.trim()} onClick={addKey}>添加</Button></div></Field>
      <div className="workshop-tags">{entry.keys.map(key => busy ? <Tag key={key}>{key}</Tag> : <Tag key={key} onRemove={() => onChange({ keys: entry.keys.filter(item => item !== key) })} removeLabel={`删除关键词 ${key}`}>{key}</Tag>)}</div>
      <Field label={<>内容 <code>content</code></>}><Textarea rows={6} disabled={busy} value={entry.content} onChange={event => onChange({ content: event.target.value })} placeholder="条目内容，触发后注入上下文" /></Field>
      <Field label={<>插入位置 <code>anchor</code></>} group><div className="workshop-entry-choice">{ANCHOR_OPTS.map(option => <Button key={option.value} variant={entry.anchor === option.value ? 'tonal' : 'secondary'} aria-pressed={entry.anchor === option.value} disabled={busy} onClick={() => onChange({ anchor: option.value })}><strong>{option.label}</strong><small>{option.desc}</small></Button>)}</div></Field>
      <div className="workshop-numbers"><Field label={<>顺序 <code>order</code></>}><TextInput type="number" disabled={busy} value={entry.order} onChange={event => onChange({ order: Number(event.target.value) || 0 })} /></Field><Field label={<>深度 <code>depth</code></>}><TextInput type="number" min={0} max={16} disabled={busy} value={entry.depth} onChange={event => onChange({ depth: Number(event.target.value) || 0 })} /></Field><Field label="概率 %"><TextInput type="number" min={0} max={100} disabled={busy} value={entry.probability} onChange={event => onChange({ probability: Number(event.target.value) || 0 })} /></Field></div>
      <div className="workshop-switches"><Switch disabled={busy} checked={entry.constant} onChange={() => onChange({ constant: !entry.constant })} label="常驻注入" /><Switch disabled={busy} checked={entry.enabled} onChange={() => onChange({ enabled: !entry.enabled })} label="启用" /></div><p className="workshop-hint">常驻注入时，每回合无条件注入，不依赖关键词。</p>
    </WorkshopDisclosure></div>
  </section>;
}
