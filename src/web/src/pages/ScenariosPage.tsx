import { queryClient } from '../queryClient';
import { resourceQueries } from '../features/resources/resourceQueries';
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { storiesClient as api } from '../features/stories/storiesClient';
import { useCharacterStore } from "../store/characterStore";
import { useChatStore } from "../store/chatStore";
import { useLorebookStore } from "../store/lorebookStore";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import type {
  ScenarioPackage,
  ScenarioPlayOptions,
  ScenarioSummary,
} from "../types";
import { ConfirmDialog } from "../design-system/ConfirmDialog";
import { WorkbenchPage, WorkbenchDesk, WorkbenchColumn } from '../design-system/Workbench';
import { Button, IconButton, TextInput, Textarea, Field, Select, Tag, Checkbox, Menu, EmptyState } from '../design-system';
import { ArrowLeft, ChevronLeft, ChevronRight, Clapperboard, Search, Settings2, Plus, Upload, Download, Eye, MoreHorizontal, X, Save, LibraryBig } from '../design-system/Icon';
import { SurfaceDialog } from '../design-system/SurfaceDialog';
import { Popover } from 'radix-ui';
import { scenarioQueries } from '../features/library/scenarioQueries';
import { SCENARIO_STEPS, POV_OPTIONS, DENSITY_OPTIONS, DIRECTOR_OPTIONS, relativeModified, scenarioPlayLabels, scenarioCreateInput } from '../features/library/workbenchPresentation';
import './scenarios-production.css';
import {
  collectTags,
  filterAndSortAssets,
  useAssetListFilters,
} from "../features/library/AssetListToolbar";

export default function ScenariosPage({
  onStarted,
}: {
  onStarted: () => void;
}) {
  const navigate = useNavigate();
  const { filters, setFilters } = useAssetListFilters();
  const characters = useCharacterStore((state) => state.characters);
  const books = useLorebookStore((state) => state.books);
  const { data: worlds = [] } = useQuery({
    ...resourceQueries.worlds(),
  });
  const fileInput = useRef<HTMLInputElement>(null);
  const scenariosQuery = useQuery(resourceQueries.scenarios());
  const scenarios = scenariosQuery.data ?? [];
  const filteredScenarios = filterAndSortAssets(
    scenarios,
    { ...filters, world: "" },
    (item) => ({
      id: item.id,
      name: item.title,
      description: item.description,
      tags: item.tags,
      aliases: item.character_names,
      updatedAt: item.updated_at ?? item.created_at,
    }),
  );
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [author, setAuthor] = useState("");
  const [packageLicense, setPackageLicense] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [tags, setTags] = useState("");
  const [selectedCharacters, setSelectedCharacters] = useState<string[]>([]);
  const [selectedBooks, setSelectedBooks] = useState<string[]>([]);
  const [selectedWorldId, setSelectedWorldId] = useState("");
  const [persona, setPersona] = useState("");
  const [instructions, setInstructions] = useState("");
  const [location, setLocation] = useState("开场");
  const [sceneDescription, setSceneDescription] = useState("");
  const [openingNarration, setOpeningNarration] = useState("");
  const [pov, setPov] = useState("");
  const [density, setDensity] = useState("");
  const [directorMode, setDirectorMode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<ScenarioPackage | null>(null);
  const [previewBusyId, setPreviewBusyId] = useState<string | null>(null);
  const [page, setPage] = useState<"browse" | "make">("browse");
  const [step, setStep] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<ScenarioSummary | null>(
    null,
  );
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [showPresetDetail, setShowPresetDetail] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [playOpen, setPlayOpen] = useState(false);
  const stepTitle = useRef<HTMLHeadingElement>(null);
  const stepBody = useRef<HTMLDivElement>(null);
  const selectedPreset = scenarios.find(item => item.id === selectedPresetId) ?? null;
  const packageQuery = useQuery({ ...scenarioQueries.detail(selectedPresetId ?? ''), enabled: Boolean(selectedPresetId) && page === 'browse' });
  useEffect(() => {
    if (page !== 'browse') return;
    if (!selectedPresetId || !scenarios.some(item => item.id === selectedPresetId)) setSelectedPresetId(scenarios[0]?.id ?? null);
  }, [scenarios, selectedPresetId, page]);
  const goStep = (index: number) => { setStep(Math.max(0, Math.min(4, index))); requestAnimationFrame(() => { stepTitle.current?.focus({ preventScroll: true }); if (stepBody.current) stepBody.current.scrollTop = 0; }); };
  const missing = [!title.trim() && '预设名称', !selectedCharacters.length && '至少一位角色'].filter(Boolean);
  const stepFilled = [Boolean(title.trim()), Boolean(selectedCharacters.length), Boolean(selectedWorldId || selectedBooks.length), Boolean(sceneDescription || openingNarration || persona || instructions), Boolean(pov || density || directorMode)];
  const clearFilters = () => setFilters({ query: '', tag: '', world: '', sort: 'updated' });

  const refresh = async () => { await queryClient.invalidateQueries({ queryKey: resourceQueries.scenarios().queryKey }); await queryClient.fetchQuery(resourceQueries.scenarios()); };
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, []);

  const create = async () => {
    if (busy || !title.trim() || selectedCharacters.length === 0) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const play: ScenarioPlayOptions = {
        narrative_pov: pov
          ? (pov as NonNullable<ScenarioPlayOptions["narrative_pov"]>)
          : null,
        narrative_density: density
          ? (density as NonNullable<ScenarioPlayOptions["narrative_density"]>)
          : null,
        director_mode: directorMode
          ? (directorMode as NonNullable<ScenarioPlayOptions["director_mode"]>)
          : null,
      };
      const created = await api.createScenario(scenarioCreateInput({
        title: title.trim(),
        description: description.trim(),
        author: author.trim(),
        license: packageLicense.trim(),
        source_url: sourceUrl.trim(),
        tags: tags
          .split(/[,，]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
        character_ids: selectedCharacters,
        lorebook_ids: selectedBooks,
        world_id: selectedWorldId || null,
        player_persona: persona,
        instructions: instructions.trim(),
        opening: {
          location: location.trim() || "开场",
          description: sceneDescription.trim(),
          narration: openingNarration.trim(),
          member_keys: [],
        },
        play,
      }));
      queryClient.setQueryData(scenarioQueries.detail(created.id).queryKey, created);
      setSelectedPresetId(created.id); setShowPresetDetail(true);
      setTitle("");
      setDescription("");
      setAuthor("");
      setPackageLicense("");
      setSourceUrl("");
      setTags("");
      setSelectedCharacters([]);
      setSelectedBooks([]);
      setSelectedWorldId("");
      setPersona("");
      setInstructions("");
      setLocation("开场");
      setSceneDescription("");
      setOpeningNarration("");
      setPov("");
      setDensity("");
      setDirectorMode("");
      await refresh();
      setNotice("预设已保存。它保存的是当前角色和世界书的剧情快照。");
      setPage("browse");
      setStep(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const start = async (id: string) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const session = await api.startScenario(id);
      await useChatStore.getState().loadSessions();
      await useChatStore.getState().selectSession(session.id);
      onStarted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!deleteTarget || deleteBusy) return;
    setDeleteBusy(true);
    try {
      await api.deleteScenario(deleteTarget.id);
      await refresh();
      setDeleteTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  };

  const showPreview = async (item: ScenarioSummary) => {
    setPreviewBusyId(item.id);
    setError("");
    try {
      setPreview(await queryClient.fetchQuery(scenarioQueries.detail(item.id)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewBusyId(null);
    }
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const imported = await api.importScenario(file);
      await refresh();
      setNotice(`已导入「${imported.title}」。`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  return (
    <WorkbenchPage className={`scenario-workspace scenario-page ${page === 'make' ? 'scenario-page--make' : ''} ${showPresetDetail ? 'has-detail' : ''}`}>
      <input ref={fileInput} type="file" accept=".mrpscenario,.json,.zip,application/json,application/zip" hidden onChange={event => void importFile(event.target.files?.[0])} />
      <header className="workbench-bar scenario-bar">
        {page === 'make' && <Button icon={ArrowLeft} variant="ghost" aria-label="返回浏览预设" onClick={() => setPage('browse')}>浏览预设</Button>}
        <div className="workbench-bar__title"><h1>{page === 'make' ? '制作预设' : '场景预设'}</h1><p>{page === 'make' ? '保存后角色和世界书会复制进预设；以后修改素材库，不会改写这份预设。' : '把角色、世界书、开场和剧情约定保存成一份可重复开局的预设。'}</p></div>
        {page === 'browse' && <div className="scenario-bar-actions"><Button icon={Upload} disabled={busy} onClick={() => fileInput.current?.click()}>导入场景包</Button><Button icon={Plus} onClick={() => setPage('make')}>制作预设</Button></div>}
      </header>
      {(error || notice) && <div className={`scenario-notice ${error ? 'scenario-notice--error' : ''}`} role={error ? 'alert' : 'status'}><span>{error || notice}</span><IconButton size="sm" label="关闭提示" icon={X} onClick={() => { setError(''); setNotice(''); }} /></div>}

      {page === 'make' ? <WorkbenchDesk layout="rail-main" className="scenario-make-desk">
        <WorkbenchColumn variant="rail" className="scenario-step-rail" aria-label="制作步骤">
          <header className="workbench-column__head"><div className="scenario-progress"><strong>制作进度</strong><span role="status">{stepFilled.filter(Boolean).length} / 5 已填写</span><progress value={stepFilled.filter(Boolean).length} max={5} aria-label="制作进度" /></div></header>
          <nav className="workbench-column__body" aria-label="场景预设制作目录"><ol className="scenario-step-list">{SCENARIO_STEPS.map((label, index) => <li key={label}><button type="button" disabled={busy} aria-current={step === index ? 'step' : undefined} className="scenario-step" onClick={() => goStep(index)}><span className="scenario-step-number">{String(index + 1).padStart(2, '0')}</span><span>{label}</span><span className={`scenario-step-dot ${stepFilled[index] ? 'is-filled' : ''}`} role="img" aria-label={stepFilled[index] ? '已填写' : '未填写'} /></button></li>)}</ol></nav>
        </WorkbenchColumn>
        <WorkbenchColumn variant="main" className="scenario-step-main" aria-label="制作步骤内容">
          <header className="workbench-column__head scenario-step-head"><span>{String(step + 1).padStart(2, '0')}</span><h2 ref={stepTitle} tabIndex={-1}>{SCENARIO_STEPS[step]}</h2>{(step === 1 || step === 2) && <Tag tone="region">已选 {step === 1 ? selectedCharacters.length : selectedBooks.length} / 20</Tag>}</header>
          <div ref={stepBody} className="workbench-column__body scenario-step-body"><fieldset disabled={busy} className="scenario-fieldset">
            {step === 0 && <><div className="scenario-form-grid">
              <Field label="预设名称" required><TextInput value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：示例开场" /></Field>
              <Field label="标签" hint="用逗号分隔"><TextInput value={tags} onChange={event => setTags(event.target.value)} placeholder="悬疑，慢热，现代" /></Field>
              <Field label="简介" className="scenario-span"><Textarea rows={3} value={description} onChange={event => setDescription(event.target.value)} placeholder="用几句话说明故事背景和体验重点" /></Field>
            </div><FormCollapse title="分享信息（可选）" id="scenario-share" open={shareOpen} onToggle={() => setShareOpen(!shareOpen)}><div className="scenario-form-grid">
              <Field label="作者"><TextInput value={author} onChange={event => setAuthor(event.target.value)} placeholder="显示在分享预设中" /></Field>
              <Field label="授权说明"><TextInput value={packageLicense} onChange={event => setPackageLicense(event.target.value)} placeholder="例如：保留署名后可改编" /></Field>
              <Field label="作品来源" optional className="scenario-span"><TextInput value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} placeholder="作品主页或原始来源链接" /></Field>
            </div></FormCollapse></>}
            {step === 1 && <AssetPicker label="登场角色" emptyMessage="角色库还是空的，先导入或创建角色。" items={characters.map(character => ({ id: character.id, name: character.card.name, detail: character.card.description || '（无角色简介）' }))} selected={selectedCharacters} onChange={setSelectedCharacters} />}
            {step === 2 && <><Field label="世界档案" optional className="scenario-world-field" hint="保存时会复制这个世界的档案与核心摘要；以后修改原世界不会改动预设。导出预设时会包含作者私密档案。"><Select aria-label="世界档案" value={selectedWorldId || '__none'} onValueChange={value => setSelectedWorldId(value === '__none' ? '' : value)} options={[{ value: '__none', label: '不绑定世界' }, ...worlds.filter(world => !world.archived).map(world => ({ value: world.id, label: world.title }))]} /></Field><AssetPicker label="绑定世界书" emptyMessage="暂时没有世界书，也可以只用角色设定创建。" helper="角色自身绑定的世界书也会自动一并打包。" items={books.map(book => ({ id: book.id, name: book.name, detail: `${book.entries.length} 条设定` }))} selected={selectedBooks} onChange={setSelectedBooks} /></>}
            {step === 3 && <div className="scenario-form-grid">
              <Field label="开场地点"><TextInput value={location} onChange={event => setLocation(event.target.value)} placeholder="例如：潮汐书店" /></Field>
              <Field label="时间与氛围"><TextInput value={sceneDescription} onChange={event => setSceneDescription(event.target.value)} placeholder="例如：深夜，暴雨刚停" /></Field>
              <Field label="开场旁白" optional className="scenario-span"><Textarea rows={3} value={openingNarration} onChange={event => setOpeningNarration(event.target.value)} /></Field>
              <Field label="玩家人设" optional className="scenario-span"><Textarea rows={3} value={persona} onChange={event => setPersona(event.target.value)} /></Field>
              <Field label="剧情约定" hint="这些文字会在每次生成时作为本局剧情设定提供给角色。" className="scenario-span"><Textarea rows={5} value={instructions} onChange={event => setInstructions(event.target.value)} /></Field>
            </div>}
            {step === 4 && <><FormCollapse title="可选：本局叙事方式" id="scenario-play" open={playOpen} onToggle={() => setPlayOpen(!playOpen)}><div className="scenario-form-grid scenario-play-grid">
              <Field label="叙事视角"><Select aria-label="叙事视角" value={pov || '__default'} onValueChange={value => setPov(value === '__default' ? '' : value)} options={POV_OPTIONS.map(item => ({ ...item, value: item.value || '__default' }))} /></Field>
              <Field label="叙事密度"><Select aria-label="叙事密度" value={density || '__default'} onValueChange={value => setDensity(value === '__default' ? '' : value)} options={DENSITY_OPTIONS.map(item => ({ ...item, value: item.value || '__default' }))} /></Field>
              <Field label="导演模式"><Select aria-label="导演模式" value={directorMode || '__default'} onValueChange={value => setDirectorMode(value === '__default' ? '' : value)} options={DIRECTOR_OPTIONS.map(item => ({ ...item, value: item.value || '__default' }))} /></Field>
            </div></FormCollapse><h3 className="scenario-section-title">保存内容核对</h3><dl className="scenario-summary">{[
              ['名称', title || '未填写'], ['角色', selectedCharacters.map(id => characters.find(character => character.id === id)?.card.name ?? id).join('、') || '未选择'], ['世界', worlds.find(world => world.id === selectedWorldId)?.title || '未选择'], ['世界书', selectedBooks.map(id => books.find(book => book.id === id)?.name ?? id).join('、') || '无'], ['开场', `${location || '开场'}；${sceneDescription}；${openingNarration || '无旁白'}`], ['玩家人设', persona || '无'], ['剧情约定', instructions || '无'], ['分享信息', [author, packageLicense, sourceUrl].filter(Boolean).join(' · ') || '未填写'], ['玩法', scenarioPlayLabels({ narrative_pov: pov as ScenarioPlayOptions['narrative_pov'], narrative_density: density as ScenarioPlayOptions['narrative_density'], director_mode: directorMode as ScenarioPlayOptions['director_mode'] }).join(' / ') || '沿用默认视角 / 默认密度 / 默认导演']
            ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></>}
          </fieldset></div>
          <footer className="workbench-column__foot scenario-step-foot"><span role="status">{step === 4 ? missing.length ? `还需要：${missing.join('、')}。` : '内容已齐，可以保存。' : `第 ${step + 1} / 5 步`}</span><Button variant="secondary" icon={ChevronLeft} disabled={step === 0 || busy} onClick={() => goStep(step - 1)}>上一步</Button>{step === 4 ? <Button variant="primary" skin="primary" icon={Save} disabled={busy || Boolean(missing.length)} loading={busy} onClick={() => void create()}>保存场景预设</Button> : <Button variant="tonal" disabled={busy} onClick={() => goStep(step + 1)}>下一步<ChevronRight size={16} /></Button>}</footer>
        </WorkbenchColumn>
      </WorkbenchDesk> : <WorkbenchDesk layout="list-main" className="scenario-browse-desk">
        <WorkbenchColumn variant="list" className="scenario-list-column" aria-label="场景预设列表">
          <header className="workbench-column__head"><h2>已保存的预设</h2><Tag>{scenarios.length}</Tag><IconButton label="制作预设" icon={Plus} variant="tonal" onClick={() => setPage('make')} /></header>
          <div className="workbench-column__band scenario-search"><TextInput leadingIcon={Search} type="search" aria-label="搜索场景预设" placeholder="搜索预设、标签或角色" value={filters.query} onChange={event => setFilters({ query: event.target.value })} /><Popover.Root><Popover.Trigger asChild><IconButton icon={Settings2} label="场景预设筛选" className={filters.query || filters.tag || filters.sort !== 'updated' ? 'has-filter' : ''} /></Popover.Trigger><Popover.Portal><Popover.Content className="scenario-filter-popover" sideOffset={8} collisionPadding={12} aria-label="筛选场景预设"><Field label="标签筛选"><Select aria-label="场景预设标签筛选" value={filters.tag || '__all'} onValueChange={value => setFilters({ tag: value === '__all' ? '' : value })} options={[{ value: '__all', label: '全部标签' }, ...collectTags(scenarios).map(tag => ({ value: tag, label: tag }))]} /></Field><Field label="排序"><Select aria-label="场景预设排序" value={filters.sort} onValueChange={value => setFilters({ sort: value === 'name' ? 'name' : 'updated' })} options={[{ value: 'updated', label: '最近修改' }, { value: 'name', label: '名称' }]} /></Field><Button variant="ghost" onClick={clearFilters}>清除筛选</Button></Popover.Content></Popover.Portal></Popover.Root></div>
          <div className="workbench-column__body scenario-list-body">{scenariosQuery.isLoading ? <p className="scenario-hint" role="status">正在读取场景预设…</p> : scenariosQuery.isError ? <div className="scenario-hint" role="alert"><p>场景预设读取失败。</p><Button onClick={() => void scenariosQuery.refetch()}>重试</Button></div> : !scenarios.length ? <p className="scenario-hint">还没有场景预设。</p> : !filteredScenarios.length ? <p className="scenario-hint">没有符合筛选的场景预设。<Button variant="ghost" onClick={clearFilters}>清除筛选</Button></p> : <ul className="scenario-rows">{filteredScenarios.map(item => <li key={item.id} className={`scenario-card scenario-row ${item.id === selectedPresetId ? 'is-selected' : ''}`}>
            <button type="button" className="scenario-row-select" title={item.title} aria-current={item.id === selectedPresetId || undefined} onClick={() => { setSelectedPresetId(item.id); setShowPresetDetail(true); }}><span className="scenario-row-tile"><Clapperboard size={20} /></span><span className="scenario-row-text"><strong>{item.title}</strong><span>{item.character_names.length} 角色 · {item.lorebook_count} 世界书 · {relativeModified(item.updated_at ?? item.created_at)}</span></span></button>
            <Menu trigger={<IconButton icon={MoreHorizontal} size="sm" className="scenario-row-more" label={`场景预设 ${item.title} 更多操作`} />} items={[{ id: 'preview', label: '预览', disabled: previewBusyId === item.id, onSelect: () => void showPreview(item) }, { id: 'export', label: '导出', onSelect: () => { window.location.href = `/api/v1/scenarios/${encodeURIComponent(item.id)}/export`; } }, { id: 'delete', label: '删除', tone: 'danger', disabled: busy || deleteBusy, onSelect: () => setDeleteTarget(item) }]} />
          </li>)}</ul>}</div>
          <footer className="workbench-column__foot scenario-list-foot"><span role="status">共 {filteredScenarios.length} 份</span></footer>
        </WorkbenchColumn>
        <WorkbenchColumn variant="main" className="scenario-detail-column" aria-label="预设内容">
          <header className="workbench-column__head scenario-detail-head"><Button className="scenario-mobile-back" variant="ghost" icon={ArrowLeft} size="sm" onClick={() => setShowPresetDetail(false)}>预设列表</Button><div className="scenario-detail-title"><h2 title={selectedPreset?.title}>{selectedPreset?.title || '未选择预设'}</h2>{selectedPreset && <p>{selectedPreset.author && `作者：${selectedPreset.author} · `}修订 {selectedPreset.revision}{selectedPreset.imported_at && <Tag tone="warning">分享包副本</Tag>}</p>}</div>{selectedPreset && <div className="scenario-detail-actions"><Button icon={Eye} disabled={previewBusyId === selectedPreset.id} loading={previewBusyId === selectedPreset.id} onClick={() => void showPreview(selectedPreset)}>预览</Button><a className="ui-btn ui-btn--secondary" href={`/api/v1/scenarios/${encodeURIComponent(selectedPreset.id)}/export`}><Download size={18} />导出</a><Button variant="primary" skin="primary" icon={ChevronRight} disabled={busy} loading={busy} onClick={() => void start(selectedPreset.id)}>开始故事</Button></div>}</header>
          <div className="workbench-column__body scenario-detail-body">{!selectedPreset ? <EmptyState icon={Clapperboard} title="选择一份预设" description="从左侧选择预设，查看内容并从这里开局。" action={<Button variant="tonal" onClick={() => setPage('make')}>制作预设</Button>} /> : <>
            <div className="scenario-detail-contents"><div className="scenario-stack"><PreviewSection title="说明"><p>{selectedPreset.description || '暂无简介'}</p></PreviewSection><PreviewSection title="标签">{selectedPreset.tags.length ? <div className="scenario-tags">{selectedPreset.tags.map(tag => <button key={tag} className="scenario-filter-tag" aria-pressed={filters.tag === tag} onClick={() => setFilters({ tag: filters.tag === tag ? '' : tag })}>{tag}</button>)}</div> : <p className="scenario-muted">没有标签</p>}</PreviewSection>{packageQuery.isLoading ? <p role="status">正在读取开场与约定…</p> : packageQuery.isError ? <div role="alert"><p>读取预设内容失败。</p><Button onClick={() => void packageQuery.refetch()}>重试</Button></div> : packageQuery.data && <ScenarioOpening item={packageQuery.data} />}</div><div className="scenario-stack">{packageQuery.data ? <ScenarioCast item={packageQuery.data} /> : <><PreviewSection title={`登场角色 · ${selectedPreset.character_names.length}`}><div className="scenario-tags">{selectedPreset.character_names.map((name, index) => <Tag key={`${index}:${name}`}>{name}</Tag>)}</div></PreviewSection><PreviewSection title={`世界书 · ${selectedPreset.lorebook_count}`}><p>世界书 {selectedPreset.lorebook_count} 本</p></PreviewSection></>}</div></div>
          </>}</div>
          <footer className="workbench-column__foot scenario-detail-foot"><span>{selectedPreset ? `修订 ${selectedPreset.revision} · 更新于 ${relativeModified(selectedPreset.updated_at ?? selectedPreset.created_at)}` : '导入的包会作为新预设保存，不会覆盖本地内容。'}</span>{selectedPreset && <Button variant="ghost" disabled={busy || deleteBusy} onClick={() => setDeleteTarget(selectedPreset)}>删除预设</Button>}</footer>
        </WorkbenchColumn>
      </WorkbenchDesk>}

      <ConfirmDialog open={Boolean(deleteTarget)} title={`删除预设「${deleteTarget?.title ?? ''}」`} description="删除这份预设不会影响已经开始的故事。" busy={deleteBusy} error={error} onClose={() => setDeleteTarget(null)} onConfirm={() => void remove()} />
      {preview && <ScenarioPreview package={preview} onClose={() => setPreview(null)} onStart={() => { setPreview(null); void start(preview.id); }} onSaveWorld={() => { if (busy) return; setBusy(true); setError(''); void api.saveScenarioWorld(preview.id).then(world => { setPreview(null); navigate(`/worlds/${encodeURIComponent(world.id)}`); }).catch(cause => setError(cause instanceof Error ? cause.message : String(cause))).finally(() => setBusy(false)); }} busy={busy} error={error} />}
    </WorkbenchPage>
  );
}

function ScenarioOpening({ item }: { item: ScenarioPackage }) {
  const labels = scenarioPlayLabels(item.play);
  return <><PreviewSection title="开场与约定"><p>地点：{item.opening.location || '开场'}{item.opening.description && ` · ${item.opening.description}`}</p>{item.opening.narration && <p className="scenario-quote">{item.opening.narration}</p>}</PreviewSection>{item.player_persona && <PreviewSection title="玩家人设"><p>{item.player_persona}</p></PreviewSection>}{item.instructions && <PreviewSection title="剧情约定"><p>{item.instructions}</p></PreviewSection>}{labels.length > 0 && <PreviewSection title="叙事选项"><p>{labels.join(' · ')}</p></PreviewSection>}</>;
}

function ScenarioCast({ item }: { item: ScenarioPackage }) {
  return <><PreviewSection title={`登场角色 · ${item.cast.length}`}><ul className="scenario-cast">{item.cast.map(member => <li key={member.key}><span className="scenario-cast-icon" aria-hidden="true">{member.card.name.slice(0, 1)}</span><span><strong>{member.card.name}</strong><span>{member.card.description || '（无角色简介）'}</span></span></li>)}</ul></PreviewSection><PreviewSection title={`世界书 · ${item.lorebooks.length}`}>{item.lorebooks.length ? <ul className="scenario-cast">{item.lorebooks.map(book => <li key={book.key}><span className="scenario-cast-icon" aria-hidden="true"><LibraryBig size={18} /></span><span><strong>{book.name}</strong><span>{book.entries.length} 条设定</span></span></li>)}</ul> : <p className="scenario-muted">没有绑定世界书</p>}</PreviewSection></>;
}

function ScenarioPreview({ package: item, onClose, onStart, onSaveWorld, busy, error }: { package: ScenarioPackage; onClose: () => void; onStart: () => void; onSaveWorld: () => void; busy: boolean; error: string }) {
  return <SurfaceDialog title={item.title} className="scenario-preview-dialog" onClose={onClose} busy={busy}>
    <header className="scenario-preview-head"><h2>{item.title}</h2><IconButton label="关闭" icon={X} disabled={busy} onClick={onClose} /></header>
    <div className="scenario-preview-body"><PreviewSection title="说明"><p>{item.description || '暂无简介'}</p></PreviewSection><ScenarioCast item={item} /><ScenarioOpening item={item} />{(item.author || item.license || item.source_url) && <PreviewSection title="分享信息"><p>{[item.author && `作者：${item.author}`, item.license && `授权：${item.license}`, item.source_url && `来源：${item.source_url}`].filter(Boolean).join('\n')}</p></PreviewSection>}{Object.keys(item.components ?? {}).length > 0 && <PreviewSection title="扩展模块"><p>{Object.keys(item.components).map(key => key === 'mrp.world_archive' ? '世界档案' : key).join('、')}</p></PreviewSection>}{error && <p role="alert" className="scenario-notice scenario-notice--error">{error}</p>}</div>
    <footer className="scenario-preview-foot">{item.components?.['mrp.world_archive'] && <Button disabled={busy} onClick={onSaveWorld}>将包内世界另存到世界库</Button>}<Button disabled={busy} onClick={onClose}>关闭</Button><Button variant="primary" skin="primary" disabled={busy} onClick={onStart}>从这个预设开局</Button></footer>
  </SurfaceDialog>;
}

function PreviewSection({ title, children }: { title: string; children: ReactNode }) { return <section className="scenario-section"><h3>{title}</h3>{children}</section>; }
function FormCollapse({ title, id, open, onToggle, children }: { title: string; id: string; open: boolean; onToggle: () => void; children: ReactNode }) { return <section className="scenario-collapse"><button type="button" className="scenario-collapse-toggle" aria-expanded={open} aria-controls={id} onClick={onToggle}><ChevronRight size={16} /><span>{title}</span></button><div className="scenario-collapse-body" id={id} hidden={!open}>{children}</div></section>; }

type PickerItem = { id: string; name: string; detail: string };
function AssetPicker({ label, helper, emptyMessage, items, selected, onChange }: { label: string; helper?: string; emptyMessage: string; items: PickerItem[]; selected: string[]; onChange: (next: string[]) => void }) {
  const [query, setQuery] = useState('');
  const selectedItems = selected.map(id => items.find(item => item.id === id)).filter((item): item is PickerItem => Boolean(item));
  const filteredItems = items.filter(item => `${item.name} ${item.detail}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const toggleItem = (id: string) => onChange(selected.includes(id) ? selected.filter(item => item !== id) : selected.length < 20 ? [...selected, id] : selected);
  return <div className="scenario-picker"><h3 className="scenario-picker-label">{label} · 已选 {selected.length}{selected.length >= 20 && '（最多 20 个）'}</h3>{helper && <p className="scenario-muted">{helper}</p>}
    <div className="scenario-picker-selected">{!selectedItems.length ? <span className="scenario-muted">尚未选择</span> : <>{selectedItems.slice(0, 3).map(item => <Tag tone="region" key={item.id} onRemove={() => toggleItem(item.id)} removeLabel={`移除${item.name}`}>{item.name}</Tag>)}{selectedItems.length > 3 && <span className="scenario-muted">另有 {selectedItems.length - 3} 个</span>}</>}</div>
    <TextInput leadingIcon={Search} type="search" aria-label={`搜索${label}`} value={query} onChange={event => setQuery(event.target.value)} placeholder={`搜索${label}名称或简介`} />
    <div className="scenario-picker-bulk"><span role="status">显示 {filteredItems.length} / {items.length} 个</span><Button variant="ghost" size="sm" disabled={selected.length >= 20 || !filteredItems.length || filteredItems.every(item => selected.includes(item.id))} onClick={() => onChange([...selected, ...filteredItems.filter(item => !selected.includes(item.id)).slice(0, 20 - selected.length).map(item => item.id)])}>全选当前结果</Button><Button variant="ghost" size="sm" disabled={!selected.length} onClick={() => onChange([])}>清空</Button></div>
    {!items.length ? <p className="scenario-muted">{emptyMessage}</p> : !filteredItems.length ? <p className="scenario-muted">没有匹配的内容</p> : <ul className="scenario-picker-list">{filteredItems.map(item => <li key={item.id}><Checkbox className="scenario-picker-row" checked={selected.includes(item.id)} disabled={!selected.includes(item.id) && selected.length >= 20} onChange={() => toggleItem(item.id)} label={<span className="scenario-picker-text"><strong title={item.name}>{item.name}</strong><span title={item.detail}>{item.detail}</span></span>} /></li>)}</ul>}
  </div>;
}
