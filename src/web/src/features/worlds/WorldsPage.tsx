import { resourceQueries } from '../resources/resourceQueries';
import { useEffect, useRef, useState } from "react";
import { Dialog } from 'radix-ui';
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  NavLink,
  useLocation,
  useNavigate,
  useParams,
} from "react-router";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Download,
  Globe2,
  Plus,
  Upload,
  Users,
} from "lucide-react";
import { worldsClient as api } from './worldsClient';
import { compareNewestFirst } from "../../utils/timestamps";
import type { World } from "../../types";
import { createClientId } from '../../utils/clientId';
import { ArchivePanel } from "./ArchivePanel";
import { WorldOverviewEditor, type WorldOverviewValue } from "./WorldOverviewEditor";
import { WorldCoverPicker, WorldCoverImage } from './WorldCoverPicker';
import { WorldBooks } from "./WorldBooks";
import { Button, Cover, Menu, TextInput, Textarea, Checkbox, buttonClass } from '../../design-system';
import { ArrowLeft as WorkbenchBack } from '../../design-system/Icon';
import { worldCoverUrl } from './WorldCoverPicker';
import { resolveWorldBanner, worldCoverSigil } from '../../appearance/moonweaveAssets';
import { AssetListToolbar, filterAndSortAssets, useAssetListFilters } from '../library/AssetListToolbar';
import { LoadState } from "../../components/LoadState";
import "./worlds.css";
import "./worlds-production.css";
import "./world-organize.css";
import "./world-creation.css";

function message(error: unknown): string {
  if (!(error instanceof Error)) return "操作失败";
  const raw = error.message.replace(/^\d+:\s*/, "");
  try {
    return (JSON.parse(raw) as { detail?: string }).detail ?? raw;
  } catch {
    return raw;
  }
}

function WorldIndex() {
  const navigate = useNavigate();
  const cache = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const { data: worlds = [], isLoading, error: readError, refetch } = useQuery({
    ...resourceQueries.worlds(),
  });
  const { filters, setFilters } = useAssetListFilters();
  const query = filters.query;
  const [helpOpen, setHelpOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [coverId, setCoverId] = useState<string | null>(null);
  const [body, setBody] = useState('');
  const [authorOnly, setAuthorOnly] = useState(false);
  const draftId = useRef<string>(createClientId());
  const [recoverable, setRecoverable] = useState(() => { try { return localStorage.getItem('mrp.world.new.last') ?? ''; } catch { return ''; } });
  useEffect(() => {
    if (!creating || !(title || body || description || coverId)) return;
    try { localStorage.setItem(`mrp.world.new.${draftId.current}`, JSON.stringify({ title, description, body, authorOnly, coverId })); localStorage.setItem('mrp.world.new.last', draftId.current); } catch { /* creation works without storage */ }
  }, [creating, title, description, body, authorOnly, coverId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rows = filterAndSortAssets(worlds, { ...filters, tag: '', world: '' }, (world) => ({ id: world.id, name: world.title, description: world.description, updatedAt: world.updated_at }));
  const recentWorld = [...worlds].sort((a, b) => compareNewestFirst(a.updated_at, b.updated_at))[0];
  const gridWorlds = rows.filter((world) => world.id !== recentWorld?.id);
  const create = async () => {
    if (!title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const world = await api.createWorld({
        title: title.trim(),
        description: description.trim(),
        cover_id: coverId,
        manuscript_body: body,
        manuscript_visibility: authorOnly ? 'private' : 'public',
      });
      try { localStorage.removeItem(`mrp.world.new.${draftId.current}`); if (localStorage.getItem('mrp.world.new.last') === draftId.current) localStorage.removeItem('mrp.world.new.last'); } catch { /* saved on server */ }
      await cache.invalidateQueries({ queryKey: ["worlds"] });
      navigate(`/worlds/${world.id}`);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  };
  const importFile = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const payload: unknown = JSON.parse(await file.text());
      const world = await api.importWorld(payload);
      await cache.invalidateQueries({ queryKey: ["worlds"] });
      navigate(`/worlds/${world.id}`);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
    if (fileRef.current) fileRef.current.value = "";
  };
  return (
    <main className="worlds-page">
      <div className="worlds-heading">
        <div>
          <Link to="/library" className="worlds-back">
            <ArrowLeft size={15} /> 素材库
          </Link>
          <div className="v7-eyebrow">WORLD LIBRARY / 世界库</div>
          <h1>世界</h1>
          <p>名字加原稿就能保存和开局，之后再按需要整理。</p>
        </div>
        <div className="worlds-heading-actions">
          <Menu trigger={<Button aria-label="更多世界操作">更多</Button>} items={[{ id: "legacy", label: "从旧设定生成", onSelect: () => navigate("/library/imports?target=world") }]} />
          <Button aria-expanded={helpOpen} onClick={() => setHelpOpen(true)}>如何组织设定</Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
          >
            <Upload size={16} /> 导入世界
          </Button>
          <Button
            variant="primary" skin="primary"
            onClick={() => setCreating(true)}
          >
            <Plus size={16} /> 新建世界
          </Button>
        </div>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        hidden
        onChange={(e) => void importFile(e.target.files?.[0])}
      />
      {error && (
        <div className="v7-error" role="alert">
          {error}
        </div>
      )}
      {helpOpen && <Dialog.Root open onOpenChange={setHelpOpen}><Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay" /><Dialog.Content className="v7-dialog-content worlds-help"><Dialog.Title>从原稿到开局</Dialog.Title><Dialog.Description>自由原稿可以直接用于故事，之后再按需要整理。</Dialog.Description><ol><li>先写名字与原稿，保存不调用模型。</li><li>按需核对核心与词条，选中结果后采用。</li><li>持续修订，只重整变化的来源。</li></ol><Link to="/library/lorebooks?unowned=1">查看尚未归属的世界书</Link><Button onClick={() => setHelpOpen(false)}>关闭</Button></Dialog.Content></Dialog.Portal></Dialog.Root>}
      <section className="worlds-library">
        <AssetListToolbar label="世界" filters={filters} onChange={setFilters} count={rows.length} />
        {isLoading ? <LoadState title="正在打开世界库" /> : readError ? <LoadState title="世界库读取失败" error={message(readError)} onRetry={() => void refetch()} /> : <>
          {recentWorld && rows.some((world) => world.id === recentWorld.id) && <Link className="worlds-banner" to={`/worlds/${recentWorld.id}`}>
            <Cover name={recentWorld.title} src={resolveWorldBanner(recentWorld.cover_id)} tone="teal" className="worlds-banner-art" />
            <div className="worlds-banner-content"><span className="worlds-banner-label">最近编辑</span><h2>{recentWorld.title}</h2><p>{recentWorld.description || '一个等待你写下的世界。'}</p><WorldStats world={recentWorld} /></div>
            <span className="worlds-banner-enter">进入世界 <ArrowRight size={18} /></span>
          </Link>}
          {rows.length ? <div className="worlds-card-grid">{gridWorlds.map((world) => <Link className="worlds-card" to={`/worlds/${world.id}`} key={world.id}>
            <div className="worlds-card-image"><Cover name={world.title} src={worldCoverUrl(world.cover_id)} tone="teal" /><span className="worlds-card-title">{world.title}{world.archived && <small>已归档</small>}</span></div>
            <div className="worlds-card-body"><p className="worlds-card-desc">{world.description || '一个等待你写下的世界。'}</p><WorldStats world={world} /></div>
          </Link>)}</div> : <div className="worlds-empty"><Globe2 size={30} /><h3>{query ? '没有找到匹配的世界' : '还没有世界'}</h3><p>{query ? '试试其他关键词。' : '从一个名字开始，随后粘贴完整的背景原稿。'}</p>{!query && <Button variant="tonal" onClick={() => setCreating(true)}>新建世界</Button>}</div>}
        </>}
      </section>
      <Dialog.Root open={creating} onOpenChange={(open) => { if (!busy) setCreating(open); }}><Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay" />
          <Dialog.Content className="v7-dialog-content world-create-dialog">
            <Dialog.Title>新建世界</Dialog.Title>
            <Dialog.Description>名字加原稿即可保存，保存不调用模型。</Dialog.Description>
            {recoverable && <Button variant="secondary" onClick={() => { try { const saved = JSON.parse(localStorage.getItem(`mrp.world.new.${recoverable}`) ?? '{}'); setTitle(saved.title ?? ''); setDescription(saved.description ?? ''); setCoverId(saved.coverId ?? null); setBody(saved.body ?? ''); setAuthorOnly(saved.authorOnly ?? false); draftId.current = recoverable; setRecoverable(''); } catch { setError('本地草稿无法读取'); } }}>恢复上次未保存草稿</Button>}
            <label>
              世界名称
              <TextInput
                autoFocus
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="例如：示例世界"
              />
            </label>
            <WorldCoverPicker value={coverId} onChange={setCoverId} disabled={busy} />
            <label>
              世界原稿
              <Textarea
                rows={10}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="自由写或粘贴完整背景，之后可再整理"
              />
            </label>
            <Checkbox label="原稿仅供作者留存" checked={authorOnly} onChange={(e) => setAuthorOnly(e.target.checked)} />
            <details><summary>一句话简介（可选）</summary><Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></details>
            {error && <div className="v7-error">{error}</div>}
            <div className="worlds-modal-actions">
              <Button
                variant="secondary" disabled={busy}
                onClick={() => setCreating(false)}
              >
                取消
              </Button>
              <Button
                variant="primary"
                disabled={busy || !title.trim()}
                onClick={() => void create()}
              >
                {busy ? "保存中…" : "创建世界"}
              </Button>
            </div>
          </Dialog.Content></Dialog.Portal></Dialog.Root>
    </main>
  );
}

function WorldStats({ world }: { world: { background_count: number; biology_count: number; lorebook_count: number; cover_id?: string | null } }) {
  const sigil = worldCoverSigil(world.cover_id);
  return <span className="worlds-stats">{sigil ? <span className="worlds-sigil" style={{ maskImage: `url(${sigil})` }} aria-hidden="true" /> : <Globe2 size={20} aria-hidden="true" />}<span><strong>{world.background_count}</strong> 背景</span><span><strong>{world.biology_count}</strong> 生物卡</span><span><strong>{world.lorebook_count}</strong> 世界书</span></span>;
}

function WorldDetail({ worldId }: { worldId: string }) {
  const cache = useQueryClient();
  const { pathname } = useLocation();
  const section = pathname.includes("/archive/")
    ? "archive"
    : pathname.includes("/lorebooks")
      ? "lorebooks"
      : "overview";
  const {
    data: world,
    isLoading,
    error,
    refetch,
  } = useQuery({
    ...resourceQueries.world(worldId),
  });
  const [localError, setLocalError] = useState("");
  const [editingWorld, setEditingWorld] = useState(false);
  const apply = (next: World) => {
    cache.setQueryData(["world", worldId], next);
    void cache.invalidateQueries({ queryKey: ["worlds"] });
    void cache.invalidateQueries({ queryKey: ["world-owners"] });
  };
  if (isLoading) return <LoadState title="正在打开世界" />;
  if (error || !world)
    return (
      <main className="worlds-page">
        <LoadState title="世界资料读取失败" error={message(error)} onRetry={() => void refetch()} />
        <Link to="/worlds">返回世界库</Link>
      </main>
    );
  if (section === "lorebooks") return <main className="workbench-page worlds-lorebook-workspace">
    <header className="workbench-bar worlds-lorebook-context">
      <Link className={buttonClass({ variant: "ghost", size: "sm" })} to={`/worlds/${worldId}`}><WorkbenchBack size={15} aria-hidden="true" />{world.title}</Link>
      <nav className="worlds-tabs worlds-lorebook-tabs" aria-label="世界内容">
        <NavLink end to={`/worlds/${worldId}`}>概览</NavLink>
        <NavLink to={`/worlds/${worldId}/archive/background`}>世界档案</NavLink>
        <NavLink to={`/worlds/${worldId}/lorebooks`} className="active">世界书</NavLink>
      </nav>
      <Button variant="ghost" size="sm" onClick={() => setEditingWorld(true)}>编辑世界</Button>
    </header>
    {world.archived && <p className="worlds-archived">已归档</p>}
    {localError && <div className="v7-error" role="alert">{localError}</div>}
    <WorldBooks world={world} onChange={apply} onError={setLocalError} />
    {editingWorld && <WorldEditDialog key={world.id} world={world} onChange={apply} onClose={() => setEditingWorld(false)} />}
  </main>;
  return (
    <main className="worlds-page worlds-detail">
      <Link to="/worlds" className="worlds-back">
        <ArrowLeft size={15} /> 所有世界
      </Link>
      <div className="worlds-detail-head">

        <div>
          <h1>{world.title}</h1>
          <p>{world.description || "还没有填写世界简介"}</p>
        </div>
        {world.archived && <span className="worlds-archived">已归档</span>}
        <div className="worlds-heading-actions">
          <Link className={buttonClass({ variant: "secondary" })} to={`/worlds/${worldId}/organize`}>AI 整理</Link>
          <Button variant="secondary" onClick={() => setEditingWorld(true)}>编辑世界</Button>
        </div>
      </div>
      <WorldCoverImage coverId={world.cover_id} className="worlds-detail-cover" alt={`${world.title}的封面`} />
      <nav className="worlds-tabs" aria-label="世界内容">
        <NavLink end to={`/worlds/${worldId}`}>
          概览
        </NavLink>
        <NavLink
          to={`/worlds/${worldId}/archive/background`}
          className={section === "archive" ? "active" : ""}
        >
          世界档案
        </NavLink>
        <NavLink
          to={`/worlds/${worldId}/lorebooks`}
        >
          世界书
        </NavLink>
      </nav>
      {localError && (
        <div className="v7-error" role="alert">
          {localError}
        </div>
      )}
      {section === "archive" ? (
        <ArchivePanel world={world} onChange={apply} onError={setLocalError} />
      ) : (
        <WorldOverview world={world} onChange={apply} onError={setLocalError} />
      )}
      {editingWorld && <WorldEditDialog key={world.id} world={world} onChange={apply} onClose={() => setEditingWorld(false)} />}
    </main>
  );
}

function WorldEditDialog({ world, onChange, onClose }: { world: World; onChange: (next: World) => void; onClose: () => void }) {
  const [value, setValue] = useState<WorldOverviewValue>({ title: world.title, description: world.description, core_brief: world.core_brief, cover_id: world.cover_id ?? null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setBusy(true); setError('');
    try { onChange(await api.patchWorld(world.id, { expected_revision: world.revision, ...value })); onClose(); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="v7-dialog-overlay" />
    <Dialog.Content className="v7-dialog-content world-edit-dialog">
      <Dialog.Title>编辑世界</Dialog.Title><Dialog.Description>修改名称、简介和每轮使用的核心设定。</Dialog.Description>
      <WorldOverviewEditor value={value} onChange={setValue} onSave={() => void save()} busy={busy} />
      {error && <p className="v7-error" role="alert">{error}</p>}
      <Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}

function WorldOverview({
  world,
  onChange,
  onError,
}: {
  world: World;
  onChange: (next: World) => void;
  onError: (message: string) => void;
}) {
  const recent = [...world.archive_records]
    .filter((item) => item.kind === "background" || item.kind === "biology")
    .sort((a, b) => compareNewestFirst(a.updated_at, b.updated_at))
    .slice(0, 4);
  const unsupportedCount =
    world.archive_records.length -
    world.archive_records.filter(
      (item) => item.kind === "background" || item.kind === "biology",
    ).length;
  const download = () => {
    window.location.href = `/api/v1/worlds/${encodeURIComponent(world.id)}/export`;
  };
  return (
    <div className="worlds-overview">
      <div className="worlds-overview-main">
        <section className="worlds-panel worlds-brief">
          <div className="worlds-panel-head"><h2>世界核心摘要</h2></div>
          <p className="worlds-brief-text">{world.core_brief || '还没有核心摘要。可在“编辑世界”中填写每轮对话需要的世界规则。'}</p>
          <div className="worlds-note">核心摘要在开局时复制到故事；之后修改此处，不会改变旧故事。</div>
        </section>
        <div className="worlds-overview-tiles">
          <Link to={`/worlds/${world.id}/archive/background`}>
            <BookOpen />
            <strong>背景设定</strong>
            <span>
              {
                world.archive_records.filter((x) => x.kind === "background")
                  .length
              }{" "}
              篇文章
            </span>
            <ArrowRight size={17} />
          </Link>
          <Link to={`/worlds/${world.id}/archive/biology`}>
            <Users />
            <strong>生物设定</strong>
            <span>
              {world.archive_records.filter((x) => x.kind === "biology").length}{" "}
              张资料卡
            </span>
            <ArrowRight size={17} />
          </Link>
          <Link to={`/worlds/${world.id}/lorebooks`}>
            <BookOpen />
            <strong>世界书</strong>
            <span>{world.lorebook_ids.length} 本已关联</span>
            <ArrowRight size={17} />
          </Link>
        </div>
        {unsupportedCount > 0 && (
          <div className="worlds-note">
            另有 {unsupportedCount}{" "}
            条来自较新版本的档案。当前版本保留原始数据并在导出时带上，但尚不提供编辑界面。
          </div>
        )}
        <section className="worlds-panel">
          <div className="worlds-panel-head">
            <h2>最近编辑的档案</h2>
            <Link to={`/worlds/${world.id}/archive/background`}>
              进入档案 <ArrowRight size={15} />
            </Link>
          </div>
          {recent.length ? (
            <div className="worlds-recent">
              {recent.map((item) => (
                <Link
                  key={item.id}
                  to={`/worlds/${world.id}/archive/${item.kind}/${item.id}`}
                >
                  <span>{item.kind === "background" ? "背景" : "生物"}</span>
                  <strong>{item.title}</strong>
                  <small>
                    {item.summary || item.body.slice(0, 70) || "暂无摘要"}
                  </small>
                </Link>
              ))}
            </div>
          ) : (
            <p className="worlds-muted">
              档案还是空的。可以先把已有的长篇世界观粘贴成一篇“总览”文章。
            </p>
          )}
        </section>
      </div>
      <aside className="worlds-overview-aside">
        <div className="worlds-panel">
          <span className="v7-eyebrow">档案与运行</span>
          <h3>谁会看到这些设定？</h3>
          <p>
            {world.runtime_policy === 'raw' ? '直接使用公开背景原文；公开生物卡按提及投递。核心暂不重复投递。超出已知容量会明确提示。' : world.runtime_policy === 'compiled' ? '使用核心与世界书，完整档案留作作者资料，不重复发送到故事。' : '原有故事方式使用核心、公开背景和按提及选择的公开生物卡。作者资料不会直接投递。'}
          </p>
          {world.archived ? (
            <p>这个世界已归档。取消归档后可用于新故事。</p>
          ) : (
            <Link
              to={`/stories/new?world=${encodeURIComponent(world.id)}`}
              className={buttonClass({ variant: "tonal" })}
            >
              用这个世界开局 <ArrowRight size={15} />
            </Link>
          )}
        </div>
        <div className="worlds-panel">
          <h3>管理</h3>
          <Button className="worlds-text-action" onClick={download}>
            <Download size={16} /> 私密导出世界与关联的世界书
          </Button>
          <p className="worlds-muted">包含完整原稿、仅作者可见的资料和关联世界书；不包含角色库、故事与记忆。</p>
          <Button
            className="worlds-text-action"
            onClick={async () => {
              try {
                const next = await api.patchWorld(world.id, {
                  expected_revision: world.revision,
                  archived: !world.archived,
                });
                onChange(next);
              } catch (cause) {
                onError(message(cause));
              }
            }}
          >
            {world.archived ? "取消归档" : "归档世界"}
          </Button>
          <small>归档不会删除已有故事或世界书。</small>
        </div>
      </aside>
    </div>
  );
}

export default function WorldsPage() {
  const { worldId } = useParams();
  return worldId ? <WorldDetail key={worldId} worldId={worldId} /> : <WorldIndex />;
}
