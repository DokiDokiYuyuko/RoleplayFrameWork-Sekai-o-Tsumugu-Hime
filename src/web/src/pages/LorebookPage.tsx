import { resourceQueries } from '../features/resources/resourceQueries';
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { LoadState } from "../components/LoadState";
import { Button, IconButton, TextInput, Textarea, Switch, Select, Menu, Tag, EmptyState } from "../design-system";
import { WorkbenchPage, WorkbenchDesk, WorkbenchColumn } from '../design-system/Workbench';
import { ArrowLeft, BookOpen, LibraryBig, Plus, Search, Settings2, MoreHorizontal, X, Sparkles, Trash2, Globe2 } from '../design-system/Icon';
import { Popover } from "radix-ui";
import "./lorebook-page.css";
import { useLorebookStore } from "../store/lorebookStore";
import type { ArchiveRecord, Lorebook, LorebookEntry } from "../types";
import { ConfirmDialog } from "../design-system/ConfirmDialog";
import { Dialog } from "radix-ui";
import { ImportCompatibilityReport } from '../components/ImportCompatibilityReport';
import { collectTags, filterAndSortAssets, useAssetListFilters } from '../features/library/AssetListToolbar';
import { useAssetSelection } from '../features/library/AssetSelectionBar';
import { SelectedBundleExportDialog } from '../features/library/SelectedBundleExportDialog';
import { LorebookEntryFields } from '../components/LorebookEntryFields';
import { groupLorebooks, relativeModified } from '../features/library/workbenchPresentation';

/** 后端三档插入锚（ST position 0..7 归并，见 shared/models.py InsertAnchor） */
const ANCHOR_LABEL: Record<LorebookEntry["anchor"], string> = {
  system: "角色定义前",
  near: "贴近最新消息",
  at_depth: "指定深度",
};

type ArchiveSource = { world_id: string; archive_id: string; archive_revision: number; reviewed_revision?: number };
function archiveSource(entry: LorebookEntry): ArchiveSource | null {
  const value = entry.extensions?.['mrp.archive_source'];
  return value && typeof value === 'object' ? value as ArchiveSource : null;
}

export default function LorebookPage({ scope }: { scope?: { worldId: string; bookIds: string[]; archiveRecords: ArchiveRecord[]; onCreated: (bookId: string) => Promise<void> } } = {}) {
  const navigate = useNavigate();
  const { loadStatus, loadError, load } = useLorebookStore();
  const { filters, setFilters, search } = useAssetListFilters();
  const { data: owners = {} } = useQuery({ ...resourceQueries.owners(), enabled: !scope });
  const { id, bookId: scopedBookId } = useParams();
  const bookId = scope ? scopedBookId : id;
  const {
    books,
    currentBookId,
    selectBook,
    createBook,
    updateBook,
    copyBook,
    deleteBook,
    updateEntries,
  } = useLorebookStore();
  const visibleBooks = scope ? books.filter((b) => scope.bookIds.includes(b.id)) : books;
  const book = visibleBooks.find((b) => b.id === currentBookId) ?? null;
  const filteredBooks = filterAndSortAssets(visibleBooks, scope ? { ...filters, world: '' } : filters, (item) => ({
    id: item.id, name: item.name, description: item.description, tags: item.tags,
    worldId: scope?.worldId ?? owners[item.id]?.world_id, updatedAt: item.updated_at,
  }));
  const selection = useAssetSelection(visibleBooks.map((item) => item.id));
  const [exportOpen, setExportOpen] = useState(false);
  const worldOptions = [...new Map(Object.values(owners).map((owner) => [owner.world_id, { id: owner.world_id, title: owner.world_title }])).values()];
  useEffect(() => {
    if (bookId && visibleBooks.some((item) => item.id === bookId)) selectBook(bookId);
    else if (scope && !visibleBooks.some((item) => item.id === currentBookId) && visibleBooks[0]) selectBook(visibleBooks[0].id);
  }, [bookId, books, selectBook, scope?.bookIds.join(',')]);
  const [showBookDetail, setShowBookDetail] = useState(Boolean(bookId));
  const [selectedEntryUid, setSelectedEntryUid] = useState<number | null>(null);
  const [entryPage, setEntryPage] = useState(1);
  const [entryBusy, setEntryBusy] = useState(false);
  useEffect(() => { setShowBookDetail(Boolean(bookId)); }, [bookId]);
  useEffect(() => { setSelectedEntryUid(null); setEntryPage(1); }, [book?.id]);
  const pageCount = Math.max(1, Math.ceil((book?.entries.length ?? 0) / 20));
  const activePage = Math.min(entryPage, pageCount);
  const pageEntries = book?.entries.slice((activePage - 1) * 20, activePage * 20) ?? [];
  const selectedEntry = book?.entries.find((entry) => entry.uid === selectedEntryUid);
  const [editing, setEditing] = useState<LorebookEntry | null>(null);
  const [bookDialog, setBookDialog] = useState<
    "create" | "edit" | "copy" | null
  >(null);
  const [dialogBookId, setDialogBookId] = useState<string | null>(null);
  const [newBookName, setNewBookName] = useState("");
  const [newBookDescription, setNewBookDescription] = useState("");
  const [newBookTags, setNewBookTags] = useState("");
  const [bookBusy, setBookBusy] = useState(false);
  const [bookError, setBookError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Lorebook | null>(null);
  const [entryDeleteTarget, setEntryDeleteTarget] =
    useState<LorebookEntry | null>(null);
  const [sourceReview, setSourceReview] = useState<{ entry: LorebookEntry; record: ArchiveRecord } | null>(null);
  const [reviewContent, setReviewContent] = useState("");
  const deskHost = useRef<HTMLDivElement>(null);
  const railTitle = useRef<HTMLHeadingElement>(null);
  const [entryDocked, setEntryDocked] = useState(false);
  useEffect(() => {
    const host = deskHost.current;
    if (!host) return;
    const measure = () => setEntryDocked(host.clientWidth >= 1500);
    measure();
    const observer = new ResizeObserver(measure); observer.observe(host);
    return () => observer.disconnect();
  }, []);
  useEffect(() => { if (selectedEntryUid !== null && !entryDocked) railTitle.current?.focus({ preventScroll: true }); }, [selectedEntryUid, entryDocked]);
  const closeEntry = () => {
    const uid = selectedEntryUid; setSelectedEntryUid(null);
    deskHost.current?.querySelector<HTMLElement>(`[data-entry-uid="${uid}"]`)?.focus({ preventScroll: true });
  };
  const chooseBook = (item: Lorebook) => {
    selectBook(item.id); setShowBookDetail(true);
    navigate((scope ? `/worlds/${encodeURIComponent(scope.worldId)}/lorebooks/${encodeURIComponent(item.id)}` : `/library/lorebooks/${encodeURIComponent(item.id)}`) + (search ? `?${search}` : ''));
  };
  const filtersActive = Boolean(filters.query || filters.tag || (!scope && filters.world) || filters.sort !== 'updated');
  const groupedBooks = groupLorebooks(filteredBooks, owners, Boolean(scope));
  const owner = book ? owners[book.id] : undefined;

  const errorMessage = (e: unknown) => {
    if (!(e instanceof Error)) return String(e);
    const message = e.message.replace(/^\d+:\s*/, "");
    try {
      return (JSON.parse(message) as { detail?: string }).detail ?? message;
    } catch {
      return message;
    }
  };

  const openBookDialog = (
    mode: "create" | "edit" | "copy",
    target?: Lorebook,
  ) => {
    setBookError("");
    setBookDialog(mode);
    setDialogBookId(target?.id ?? null);
    setNewBookName(
      mode === "copy" && target
        ? `${target.name} - 副本`
        : (target?.name ?? ""),
    );
    setNewBookDescription(target?.description ?? "");
    setNewBookTags((target?.tags ?? []).join('，'));
  };

  const saveBookDialog = async () => {
    if (!newBookName.trim() || bookBusy) return;
    setBookBusy(true);
    setBookError("");
    try {
      const input = {
        name: newBookName.trim(),
        description: newBookDescription.trim(),
        tags: [...new Set(newBookTags.split(/[,，\n]/).map((tag) => tag.trim()).filter(Boolean))],
      };
      if (input.tags.length > 30 || input.tags.some((tag) => tag.length > 120)) throw new Error('最多 30 个标签，每个标签最多 120 字。');
      if (bookDialog === "edit" && dialogBookId) {
        await updateBook(dialogBookId, input);
      } else if (bookDialog === "copy" && dialogBookId) {
        const created = await copyBook(dialogBookId, input);
        await scope?.onCreated(created.id);
        setShowBookDetail(true);
        if (!scope) navigate(`/library/lorebooks/${encodeURIComponent(created.id)}` + (search ? `?${search}` : ""));
      } else {
        const created = await createBook(input);
        await scope?.onCreated(created.id);
        setShowBookDetail(true);
        if (!scope) navigate(`/library/lorebooks/${encodeURIComponent(created.id)}` + (search ? `?${search}` : ""));
      }
      setNewBookName("");
      setNewBookDescription("");
      setNewBookTags("");
      setDialogBookId(null);
      setBookDialog(null);
    } catch (e) {
      setBookError(errorMessage(e));
    } finally {
      setBookBusy(false);
    }
  };

  const removeBook = async () => {
    if (!deleteTarget) return;
    setBookBusy(true);
    setBookError("");
    try {
      await deleteBook(deleteTarget.id);
      setDeleteTarget(null);
    } catch (e) {
      setBookError(errorMessage(e));
    } finally {
      setBookBusy(false);
    }
  };

  const mutateEntries = async (
    fn: (entries: LorebookEntry[]) => LorebookEntry[],
  ) => {
    if (!book || entryBusy) return false;
    setEntryBusy(true);
    setBookError("");
    try {
      await updateEntries(book.id, fn(book.entries));
      return true;
    } catch (e) {
      setBookError(errorMessage(e));
      return false;
    } finally { setEntryBusy(false); }
  };

  // uid 书内唯一且需单调——后端按 uid 定位条目
  const newEntry = (): LorebookEntry => ({
    uid: Math.max(0, ...(book?.entries.map((e) => e.uid) ?? [])) + 1,
    keys: [],
    secondary_keys: [],
    content: "",
    comment: "",
    enabled: true,
    constant: false,
    selective: false,
    selective_logic: 0,
    order: 100,
    anchor: "at_depth",
    depth: 4,
    probability: 100,
    extensions: {},
  });

  return (
    <WorkbenchPage className={`lorebook-page ${scope ? 'lorebook-page--scoped' : ''} ${showBookDetail ? 'has-detail' : ''}`}>
      <div ref={deskHost} className="lorebook-desk-host" onKeyDown={event => { if (event.key === 'Escape' && selectedEntry && !entryDocked && !editing && !sourceReview && !entryDeleteTarget) { event.preventDefault(); closeEntry(); } }}>
      <WorkbenchDesk layout="list-main-side" className="lorebook-desk" data-entry-open={selectedEntry ? 'true' : undefined}>
        <WorkbenchColumn className="workbench-column--list lorebook-sidebar" aria-label="世界书列表">
          <header className="workbench-column__head lorebook-sidebar-head"><h1>世界书</h1><Tag>{visibleBooks.length}</Tag><IconButton label="新建世界书" icon={Plus} variant="tonal" onClick={() => openBookDialog('create')} /></header>
          <div className="lorebook-band"><div className="lorebook-filters"><TextInput leadingIcon={Search} aria-label="搜索世界书" placeholder="搜索名称、简介或标签" value={filters.query} onChange={event => setFilters({ query: event.target.value })} />
            <Popover.Root><Popover.Trigger asChild><IconButton label={filtersActive ? '世界书筛选（已设置筛选条件）' : '世界书筛选'} icon={Settings2} className={filtersActive ? 'has-filter' : ''} /></Popover.Trigger><Popover.Portal><Popover.Content className="lorebook-filter-popover" sideOffset={8} collisionPadding={12} aria-label="筛选世界书">
              <Select aria-label="世界书标签筛选" value={filters.tag || 'all'} onValueChange={value => setFilters({ tag: value === 'all' ? '' : value })} options={[{ value: 'all', label: '全部标签' }, ...collectTags(visibleBooks).map(tag => ({ value: tag, label: tag }))]} />
              {!scope && <Select aria-label="世界书世界筛选" value={filters.world || 'all'} onValueChange={value => setFilters({ world: value === 'all' ? '' : value })} options={[{ value: 'all', label: '全部世界' }, { value: 'unassigned', label: '未指定世界' }, ...worldOptions.map(world => ({ value: world.id, label: world.title }))]} />}
              <Select aria-label="世界书排序" value={filters.sort} onValueChange={value => setFilters({ sort: value === 'name' ? 'name' : 'updated' })} options={[{ value: 'updated', label: '最近修改' }, { value: 'name', label: '名称' }]} />
              <Button variant="ghost" onClick={() => setFilters({ query: '', tag: '', world: '', sort: 'updated' })}>清除筛选</Button>
            </Popover.Content></Popover.Portal></Popover.Root>
          </div></div>
          <div className="workbench-column__body lorebook-list">
            {visibleBooks.length === 0 && loadStatus === 'ready' && <p className="lorebook-empty">暂无世界书，可新建空白书。</p>}
            {visibleBooks.length > 0 && filteredBooks.length === 0 && <p className="lorebook-empty">没有符合筛选的世界书。<Button variant="ghost" size="sm" onClick={() => setFilters({ query: '', tag: '', world: '', sort: 'updated' })}>清除筛选</Button></p>}
            {groupedBooks.map(group => <section className="lorebook-group" key={group.id} aria-label={group.title || '这个世界的世界书'}>
              {!scope && <h2 className="lorebook-group-head"><Globe2 size={14} /><span>{group.title}</span><span>{group.books.length}</span></h2>}
              <ul className="lorebook-rows">{group.books.map(item => <li key={item.id} className={`lorebook-list-item ${item.id === currentBookId ? 'is-selected' : ''} ${selection.selected.includes(item.id) && selection.selecting ? 'is-checked' : ''}`}>
                <button type="button" className="lorebook-select" title={item.name} role={selection.selecting ? 'checkbox' : undefined} aria-checked={selection.selecting ? selection.selected.includes(item.id) : undefined} aria-current={!selection.selecting && item.id === currentBookId ? 'true' : undefined} onClick={() => selection.selecting ? selection.toggle(item.id) : chooseBook(item)}>
                  <span className={`lorebook-tile ${selection.selecting ? 'lorebook-tile--check' : ''}`} aria-hidden="true">{selection.selecting ? (selection.selected.includes(item.id) ? '✓' : '') : owners[item.id] || scope ? <LibraryBig size={20} /> : <BookOpen size={20} />}</span>
                  <span className="lorebook-row-text"><strong>{item.name}</strong><span>{item.entries.length} 条目 · {relativeModified(item.updated_at)}</span></span>
                </button>
                <Menu trigger={<IconButton className="lorebook-row-more" size="sm" icon={MoreHorizontal} label={`世界书 ${item.name} 更多操作`} />} items={[
                  { id: 'edit', label: '编辑信息', disabled: bookBusy, onSelect: () => openBookDialog('edit', item) },
                  { id: 'copy', label: '复制', disabled: bookBusy, onSelect: () => openBookDialog('copy', item) },
                  ...(!scope && owners[item.id] ? [{ id: 'world', label: `前往归属世界「${owners[item.id].world_title}」`, onSelect: () => navigate(`/worlds/${encodeURIComponent(owners[item.id].world_id)}/lorebooks/${encodeURIComponent(item.id)}`) }] : []),
                  ...(!scope ? [{ id: 'delete', label: '删除', tone: 'danger' as const, disabled: bookBusy, onSelect: () => setDeleteTarget(item) }] : [])
                ]} />
              </li>)}</ul>
            </section>)}
          </div>
          <footer className={`workbench-column__foot lorebook-list-foot ${selection.selecting ? 'is-selecting' : ''}`} aria-label="素材选择">
            {!selection.selecting ? <><span role="status">共 {filteredBooks.length} 本</span><Button variant="ghost" size="sm" onClick={selection.start}>多选</Button></> : <>
              <span role="status">已选 {selection.selected.length} 项</span>
              <Button variant="ghost" size="sm" disabled={!filteredBooks.length} onClick={() => selection.toggleVisible(filteredBooks.map(item => item.id))}>{filteredBooks.every(item => selection.selected.includes(item.id)) && filteredBooks.length ? '取消当前结果' : '选择当前结果'}</Button>
              <Button variant="ghost" size="sm" disabled={!selection.selected.length} onClick={selection.clear}>清空</Button>
              <Button variant="tonal" size="sm" disabled={!selection.selected.length} onClick={() => setExportOpen(true)}>导出所选</Button>
              <Button variant="ghost" size="sm" onClick={selection.exit}>退出</Button>
            </>}
          </footer>
        </WorkbenchColumn>

        <WorkbenchColumn className="workbench-column--main lorebook-main" aria-label="世界书内容">
          <header className="workbench-column__head lorebook-detail-head">
            <Button className="lorebook-mobile-back" variant="ghost" icon={ArrowLeft} size="sm" onClick={() => { setShowBookDetail(false); navigate((scope ? `/worlds/${encodeURIComponent(scope.worldId)}/lorebooks` : '/library/lorebooks') + (search ? `?${search}` : '')); }}>世界书列表</Button>
            <div className="lorebook-detail-title"><h2 title={book?.name}>{book?.name ?? '未选择世界书'}</h2>{book && <><div className="lorebook-meta">{owner && <button className="lorebook-owner" onClick={() => navigate(`/worlds/${encodeURIComponent(owner.world_id)}/lorebooks/${encodeURIComponent(book.id)}`)}><Globe2 size={14} />{owner.world_title}</button>}<span>{book.entries.length} 条目</span>{book.tags?.map(tag => <button className="lorebook-tag" key={tag} aria-pressed={filters.tag === tag} onClick={() => setFilters({ tag: filters.tag === tag ? '' : tag })}>{tag}</button>)}</div><p className="lorebook-description">{book.description || '暂无简介'}</p></>}</div>
            {book && <div className="lorebook-detail-actions">
              {(scope?.worldId || owner?.world_id) && <Button variant="secondary" icon={Sparkles} size="sm" onClick={() => navigate(`/worlds/${encodeURIComponent(scope?.worldId || owner!.world_id)}/organize?category=lorebook&book=${encodeURIComponent(book.id)}`)}>AI 整理</Button>}
              <Menu trigger={<IconButton label="世界书更多操作" icon={MoreHorizontal} />} items={[{ id: 'edit', label: '编辑信息', disabled: bookBusy, onSelect: () => openBookDialog('edit', book) }, { id: 'copy', label: '复制', disabled: bookBusy, onSelect: () => openBookDialog('copy', book) }]} />
              <Button variant="primary" skin="primary" icon={Plus} disabled={entryBusy} onClick={() => setEditing(newEntry())}>新增条目</Button>
            </div>}
          </header>
          {(book?.import_report || bookError || loadError) && <div className="lorebook-band lorebook-detail-band"><ImportCompatibilityReport report={book?.import_report} />{bookError && <p role="alert" className="v7-error">{bookError}</p>}{loadError && <LoadState title="世界书读取失败" error={loadError} onRetry={() => void load().catch(() => undefined)} />}</div>}
          <div className="workbench-column__body lorebook-body">
            {!book ? (loadStatus === 'idle' || loadStatus === 'loading' ? <LoadState title="正在读取世界书" /> : <EmptyState icon={BookOpen} title="选择一本世界书" description="从左侧选择世界书，查看条目与注入设置；也可以新建空白书。" />) : !book.entries.length ? <p className="lorebook-empty">这本世界书还没有条目，点击“新增条目”开始添加。</p> : <div className="lorebook-table-container"><table className="lorebook-table" aria-label="世界书条目"><colgroup><col className="lorebook-col-enabled" /><col className="lorebook-col-name" /><col /><col className="lorebook-col-anchor" /><col className="lorebook-col-trigger" /><col className="lorebook-col-more" /></colgroup><thead><tr><th scope="col">启用</th><th scope="col">条目</th><th scope="col">内容</th><th scope="col">注入位置</th><th scope="col">触发方式</th><th scope="col"><span className="sr-only">更多操作</span></th></tr></thead><tbody>{pageEntries.map(entry => <tr key={entry.uid} data-entry-uid={entry.uid} tabIndex={0} className={selectedEntryUid === entry.uid ? 'is-selected' : ''} aria-current={selectedEntryUid === entry.uid || undefined} onClick={() => setSelectedEntryUid(entry.uid)} onKeyDown={event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) { event.preventDefault(); setSelectedEntryUid(entry.uid); } }}>
              <td className="lorebook-cell-switch" onClick={event => event.stopPropagation()}><Switch aria-label={`启用条目 ${entry.comment || entry.keys[0] || entry.uid}`} checked={entry.enabled} disabled={entryBusy} onChange={() => void mutateEntries(entries => entries.map(item => item.uid === entry.uid ? { ...item, enabled: !item.enabled } : item))} /></td>
              <td className="lorebook-cell-name"><strong title={entry.comment || entry.keys.join('、')}>{entry.comment || entry.keys[0] || `条目 ${entry.uid}`}</strong><div className="lorebook-keywords">{entry.keys.slice(0, 3).map((key, index) => <Tag key={`${index}:${key}`}>{key}</Tag>)}{entry.keys.length > 3 && <Tag>+{entry.keys.length - 3}</Tag>}</div></td>
              <td className="lorebook-cell-content"><span className="lorebook-content-preview">{entry.content || '条目内容为空'}</span></td><td className="lorebook-cell-anchor">{ANCHOR_LABEL[entry.anchor]}{entry.anchor === 'at_depth' && ` · ${entry.depth}`}</td><td className="lorebook-cell-trigger"><Tag>{entry.constant ? '常驻' : '关键词'}</Tag></td>
              <td className="lorebook-cell-more" onClick={event => event.stopPropagation()}><Menu trigger={<IconButton size="sm" label={`条目 ${entry.uid} 更多操作`} icon={MoreHorizontal} />} items={[{ id: 'view', label: '查看全文', onSelect: () => setSelectedEntryUid(entry.uid) }, { id: 'edit', label: '编辑', disabled: entryBusy, onSelect: () => setEditing(entry) }, { id: 'delete', label: '删除', tone: 'danger', disabled: entryBusy, onSelect: () => setEntryDeleteTarget(entry) }]} /></td>
            </tr>)}</tbody></table></div>}
          </div>
          {book && <footer className="workbench-column__foot lorebook-table-summary"><span>{book.entries.length} 条目 · {book.entries.filter(entry => entry.enabled).length} 启用 · {book.entries.filter(entry => entry.constant).length} 常驻</span><div><Button variant="ghost" size="sm" disabled={activePage <= 1} onClick={() => { setEntryPage(activePage - 1); setSelectedEntryUid(null); }}>上一页</Button><span role="status">{activePage} / {pageCount}</span><Button variant="ghost" size="sm" disabled={activePage >= pageCount} onClick={() => { setEntryPage(activePage + 1); setSelectedEntryUid(null); }}>下一页</Button></div></footer>}
        </WorkbenchColumn>

        <WorkbenchColumn className={`workbench-column--aside lorebook-entry-detail ${selectedEntry ? 'has-entry' : ''}`} aria-label={selectedEntry ? '条目详情' : '本书概览'} hidden={!entryDocked && !selectedEntry}>
          <header className="workbench-column__head"><h3 ref={railTitle} tabIndex={-1}>{selectedEntry ? selectedEntry.comment || selectedEntry.keys[0] || `条目 ${selectedEntry.uid}` : '本书概览'}</h3>{selectedEntry && <IconButton label="关闭条目详情" icon={X} onClick={closeEntry} />}</header>
          <div className="workbench-column__body">{selectedEntry ? <><p className="lorebook-entry-full">{selectedEntry.content || '条目内容为空'}</p><dl className="lorebook-kv">{Object.entries({ '主关键词': selectedEntry.keys.join('、') || '无', '辅助关键词': selectedEntry.secondary_keys.join('、') || '无', '启用': selectedEntry.enabled ? '是' : '否', '常驻': selectedEntry.constant ? '是' : '否', '辅助关键词筛选': selectedEntry.selective ? '是' : '否', '筛选逻辑': ['任一辅助关键词匹配', '并非所有辅助关键词匹配', '所有辅助关键词都不匹配', '所有辅助关键词匹配'][selectedEntry.selective_logic], '顺序': selectedEntry.order, '注入位置': ANCHOR_LABEL[selectedEntry.anchor], '深度': selectedEntry.depth, '概率': `${selectedEntry.probability}%`, '扩展字段': Object.keys(selectedEntry.extensions ?? {}).length ? JSON.stringify(selectedEntry.extensions, null, 2) : '无' }).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
            {(() => { const source = archiveSource(selectedEntry); const record = scope?.archiveRecords.find(item => item.id === source?.archive_id); if (!source || !record) return null; const stale = record.revision > Math.max(source.archive_revision, source.reviewed_revision ?? 0); return <p className="lorebook-source">来源：{record.title}{stale && <Button variant="tonal" size="sm" onClick={() => { setSourceReview({ entry: selectedEntry, record }); setReviewContent(record.summary || record.body.slice(0, 1000)); }}>来源有更新 · 比对</Button>}</p>; })()}
          </> : book ? <><h4>简介</h4><p className="lorebook-entry-full">{book.description || '暂无简介'}</p><h4>标签</h4><div className="lorebook-meta">{book.tags?.length ? book.tags.map(tag => <Tag key={tag}>{tag}</Tag>) : '没有标签'}</div><div className="lorebook-overview-stats">{[[book.entries.length, '条目'], [book.entries.filter(entry => entry.enabled).length, '启用'], [book.entries.filter(entry => entry.constant).length, '常驻']].map(([value, label]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div><p className="lorebook-muted">最近修改 · {relativeModified(book.updated_at)}</p></> : <p className="lorebook-muted">选中世界书后显示简介与统计。</p>}</div>
          <footer className="workbench-column__foot">{selectedEntry ? <><Button variant="tonal" disabled={entryBusy} onClick={() => setEditing(selectedEntry)}>编辑条目</Button><IconButton icon={Trash2} label="删除条目" disabled={entryBusy} onClick={() => setEntryDeleteTarget(selectedEntry)} /></> : <span className="lorebook-muted">点击表格中的一行，查看全文与全部字段。</span>}</footer>
        </WorkbenchColumn>
      </WorkbenchDesk>
      </div>

      {exportOpen && <SelectedBundleExportDialog characters={[]} lorebooks={visibleBooks.filter((item) => selection.selected.includes(item.id)).map((item) => ({ id: item.id, expected_revision: item.revision ?? 1 }))} onClose={() => setExportOpen(false)} />}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title={`删除世界书「${deleteTarget?.name ?? ""}」`}
        description="删除会移除整本世界书及其条目；仍被角色或会话引用时，服务端会拒绝删除。"
        busy={bookBusy}
        error={bookError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void removeBook()}
      />
      {sourceReview && <Dialog.Root open onOpenChange={(open) => { if (!open && !entryBusy) setSourceReview(null); }}><Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay"/><Dialog.Content className="v7-dialog-content worlds-source-review"><Dialog.Title>比对档案来源</Dialog.Title><Dialog.Description>档案已更新。选择采用新摘要，或保留手改并标记本次已核对。</Dialog.Description><div className="worlds-editor-grid"><div><h3>当前运行词条</h3><pre>{sourceReview.entry.content}</pre></div><div><h3>更新后的档案摘要</h3><pre>{sourceReview.record.summary || sourceReview.record.body.slice(0, 1000)}</pre></div></div><label className="worlds-field">准备采用的内容<Textarea rows={5} value={reviewContent} onChange={(e) => setReviewContent(e.target.value)}/></label><div className="worlds-modal-actions"><Button variant="secondary" disabled={entryBusy} onClick={() => setSourceReview(null)}>取消</Button><Button variant="secondary" disabled={entryBusy} onClick={() => { const target = sourceReview; void mutateEntries((entries) => entries.map((item) => item.uid === target.entry.uid ? { ...item, extensions: { ...item.extensions, 'mrp.archive_source': { ...archiveSource(item), reviewed_revision: target.record.revision } } } : item)).then((ok) => { if (ok) setSourceReview(null); }); }}>保留手改，标记已核对</Button><Button variant="primary" disabled={entryBusy || !reviewContent.trim()} onClick={() => { const target = sourceReview; void mutateEntries((entries) => entries.map((item) => item.uid === target.entry.uid ? { ...item, content: reviewContent.trim(), extensions: { ...item.extensions, 'mrp.archive_source': { ...archiveSource(item), archive_revision: target.record.revision, reviewed_revision: target.record.revision } } } : item)).then((ok) => { if (ok) setSourceReview(null); }); }}>采用新内容</Button></div></Dialog.Content></Dialog.Portal></Dialog.Root>}
      <ConfirmDialog
        busy={entryBusy}
        open={Boolean(entryDeleteTarget)}
        title="删除世界书条目？"
        description="该条目的关键词、内容和注入设置会一并删除。"
        error={bookError}
        onClose={() => setEntryDeleteTarget(null)}
        onConfirm={() =>
          void mutateEntries((entries) =>
            entries.filter((item) => item.uid !== entryDeleteTarget?.uid),
          ).then((saved) => {
            if (saved) setEntryDeleteTarget(null);
          })
        }
      />
      {bookDialog && (
        <Dialog.Root
          open
          onOpenChange={(open) => {
            if (!open && !bookBusy) setBookDialog(null);
          }}
        >
          <Dialog.Portal>
            <Dialog.Overlay className="v7-dialog-overlay" />
            <Dialog.Content className="v7-dialog-content lorebook-dialog">
              <Dialog.Title>
                {bookDialog === "create"
                  ? "新建空白世界书"
                  : bookDialog === "copy"
                    ? "复制世界书"
                    : "编辑世界书"}
              </Dialog.Title>
              <Dialog.Description className="sr-only">
                填写世界书名称和可选的简介，然后保存。
              </Dialog.Description>
              <label>
                名称 <span className="lorebook-required">*</span>
                <TextInput
                  autoFocus
                  value={newBookName}
                  onChange={(e) => setNewBookName(e.target.value)}
                  placeholder="例如：示例世界书"
                  maxLength={200}
                 
                />
              </label>
              <label>
                简介（可选）
                <Textarea
                  rows={3}
                  value={newBookDescription}
                  onChange={(e) => setNewBookDescription(e.target.value)}
                  maxLength={10000}
                 
                />
              </label>
              <label>
                标签（可选，用逗号分隔）
                <TextInput aria-label="世界书标签" value={newBookTags} onChange={(event) => setNewBookTags(event.target.value)}
                  maxLength={3600} />
              </label>
              {bookError && (
                <p role="alert">
                  {bookError}
                </p>
              )}
              <div className="v7-dialog-actions">
                <Button
                  type="button"
                  disabled={bookBusy}
                  onClick={() => setBookDialog(null)}
                  variant="secondary"
                >
                  取消
                </Button>
                <Button
                  type="button"
                  disabled={bookBusy || !newBookName.trim()}
                  onClick={() => void saveBookDialog()}
                  variant="primary"
                >
                  {bookBusy
                    ? "保存中…"
                    : bookDialog === "create"
                      ? "创建世界书"
                      : bookDialog === "copy"
                        ? "创建副本"
                        : "保存修改"}
                </Button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}

      {editing && (
        <EntryEditor
          entry={editing}
          error={bookError}
          onClose={() => setEditing(null)}
          onSave={(entry) =>
            mutateEntries((entries) => {
              const idx = entries.findIndex((x) => x.uid === entry.uid);
              return idx >= 0
                ? entries.map((x) => (x.uid === entry.uid ? entry : x))
                : [...entries, entry];
            }).then((saved) => {
              if (saved) setEditing(null);
            })
          }
        />
      )}
    </WorkbenchPage>
  );
}

/** The shared fields keep manual editing and organizer review consistent. */
function EntryEditor({ entry, error, onClose, onSave }: {
  entry: LorebookEntry; error: string; onClose: () => void; onSave: (entry: LorebookEntry) => Promise<void>;
}) {
  const [draft, setDraft] = useState<LorebookEntry>(entry);
  const [busy, setBusy] = useState(false);
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="v7-dialog-overlay" />
    <Dialog.Content className="v7-dialog-content lorebook-dialog">
      <Dialog.Title>{entry.content ? '编辑条目' : '新增条目'}</Dialog.Title>
      <Dialog.Description className="sr-only">编辑世界书条目的关键词、内容与注入设置。</Dialog.Description>
      <LorebookEntryFields value={draft} onChange={setDraft} />
      {error && <p role="alert" className="v7-error">保存失败：{error}</p>}
      <div className="v7-dialog-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button variant="primary" disabled={busy} loading={busy} onClick={() => { setBusy(true); void onSave(draft).finally(() => setBusy(false)); }}>保存</Button></div>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
