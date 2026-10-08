import { resourceQueries } from '../resources/resourceQueries';
import { storyQueries } from './storyQueries';
import { STChatImportDialog } from "../../components/STChatImportDialog";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  Archive,
  ArrowRight,
  BookOpen,
  ChevronRight,
  Download,
  LibraryBig,
  LoaderCircle,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  Upload,
  Users,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { storiesClient as api } from './storiesClient';
import { colorFor } from "../../api/adapters";
import { exportStoryUrl } from "../../api/Api";
import { DEFAULT_APPEARANCE, getThemePack } from "../../appearance/registry";
import { useAppearanceStore } from "../../appearance/store";
import { Avatar } from "../../components/common";
import { ConfirmDialog } from "../../design-system/ConfirmDialog";
import { useChatStore } from "../../store/chatStore";
import { useCharacterStore } from "../../store/characterStore";
import { useLorebookStore } from "../../store/lorebookStore";
import { useComposerStore } from "../../store/composerStore";
import type { CharacterView, Message, Session, StorySummary } from "../../types";
import { playerPersonaFromCard } from "../../utils/playerPersona";
import { compareNewestFirst, timestampValue } from "../../utils/timestamps";
import { archiveBranchAction, deleteStoryAction, importStoryAction, restoreStoryAction } from "./storyActions";
import "./homepage.css";

const storyPath = (story: StorySummary, branchId = story.latest_branch_id) =>
  `/stories/${encodeURIComponent(story.story_id)}/branches/${encodeURIComponent(branchId)}`;
const stamp = (value: string | number) => {
  const date = new Date(timestampValue(value));
  return Number.isNaN(date.getTime())
    ? ""
    : new Intl.DateTimeFormat("zh-CN", {
        month: "long",
        day: "numeric",
      }).format(date);
};

// Decorative cover choices stay attached to the story when the shelf is sorted.
const STORY_COVER_KEYS = ["story_cover_forest", "story_cover_ocean", "story_cover_moon"] as const;
const FALLBACK_STORY_COVERS = [
  "/brand/themes/astral/story-forest.jpg",
  "/brand/themes/astral/story-ocean.jpg",
  "/brand/themes/astral/story-moon.jpg",
];
function coverIndexFor(storyId: string) {
  let hash = 0;
  for (const char of storyId) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % STORY_COVER_KEYS.length;
}
function localCover(source?: string) {
  return source && /^\/[a-zA-Z0-9_./-]+$/.test(source) && !source.includes("..") ? source : "";
}

function StoryCover({ story, featured = false, path }: {
  story: StorySummary;
  featured?: boolean;
  path: string;
}) {
  const themeId = useAppearanceStore((state) => state.preferences.theme_id);
  const index = coverIndexFor(story.story_id);
  const key = STORY_COVER_KEYS[index];
  const chosen = localCover(getThemePack(themeId).assets?.[key]);
  const fallback = localCover(getThemePack(DEFAULT_APPEARANCE.theme_id).assets?.[key]);
  const worldCover = story.cover_id && /^[a-z0-9][a-z0-9-]{0,79}$/.test(story.cover_id)
    ? `/api/v1/world-covers/${story.cover_id}/image` : '';
  const candidates = [...new Set([worldCover, chosen, fallback, FALLBACK_STORY_COVERS[index], FALLBACK_STORY_COVERS[0]].filter(Boolean))];
  const selection = `${themeId}:${worldCover}:${chosen}`;
  const [failed, setFailed] = useState<{ selection: string; paths: string[] }>({ selection: "", paths: [] });
  const source = candidates.find((candidate) => failed.selection !== selection || !failed.paths.includes(candidate));
  return (
    <Link className={featured ? "home-featured-cover" : "story-card-cover"} to={path} aria-label={`继续故事：${story.title}`}>
      {source ? <img key={`${selection}:${source}`} src={source} alt="" aria-hidden="true" loading={featured ? "eager" : "lazy"}
        onError={() => setFailed((previous) => ({ selection, paths: previous.selection === selection ? [...previous.paths, source] : [source] }))} /> : <BookOpen className="home-cover-fallback" size={52} strokeWidth={1} aria-hidden="true" />}
    </Link>
  );
}

function StoryParticipants({ session, characters, featured = false }: {
  session?: Session;
  characters: CharacterView[];
  featured?: boolean;
}) {
  const ids = [...new Set([
    ...(session?.character_ids ?? []),
    ...(session?.player_character_id ? [session.player_character_id] : []),
  ])];
  if (!ids.length) return null;
  const roster = ids.map((id) => {
    const character = characters.find((item) => item.id === id);
    const nameIndex = session?.character_ids.indexOf(id) ?? -1;
    const name = character?.card.name || (nameIndex >= 0 ? session?.character_names?.[nameIndex] : "") || "角色";
    return { id, name, color: character?.color ?? colorFor(id), version: character?.updated_at ?? character?.revision };
  });
  const limit = featured ? 4 : 3;
  return (
    <div className={`home-participants${featured ? " home-participants-featured" : ""}`} role="group" aria-label={`参与角色：${roster.map((item) => item.name).join("、")}`}>
      {roster.slice(0, limit).map((item) => <span className="home-participant" key={item.id} title={item.name}>
        <Avatar name={item.name} color={item.color} size={featured ? "md" : "sm"} characterId={item.id} imageVersion={item.version} />
      </span>)}
      {roster.length > limit && <span className="home-participant-more" title={`${roster.length} 位参与角色`}>+{roster.length - limit}</span>}
    </div>
  );
}

function existingExcerpt(messages: Message[]) {
  const last = [...messages].reverse().find((message) =>
    message.status === "final" && message.visible_to === "all" &&
    (message.kind === "roleplay" || message.kind === "scene") && message.content.trim(),
  );
  if (!last) return "";
  const plain = last.content
    .replace(/```[\s\S]*?```/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/(^|\n)\s*#{1,6}\s+/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return plain.length > 120 ? `${plain.slice(0, 120)}…` : plain;
}

export function MoonOrnament() {
  return <svg className="home-title-moon" viewBox="0 0 80 72" fill="none" aria-hidden="true">
    <path d="M43 8C24 8 10 22 10 40s14 31 32 31c10 0 20-5 26-13-23 7-43-14-38-35 2-6 6-11 13-15Z" fill="currentColor" opacity=".72" />
    <ellipse cx="40" cy="46" rx="38" ry="15" transform="rotate(-20 40 46)" stroke="currentColor" strokeWidth=".8" />
    <path d="m62 12 2 8 8 2-8 2-2 8-2-8-8-2 8-2 2-8Z" fill="currentColor" />
  </svg>;
}

function StoryCard({ story, session, characters, excerpt }: {
  story: StorySummary;
  session?: Session;
  characters: CharacterView[];
  excerpt?: string;
}) {
  const cache = useQueryClient();
  const rootArchived = useChatStore((state) => state.sessions.find((item) => item.id === story.story_id)?.archived);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteMembership, setDeleteMembership] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const archive = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await archiveBranchAction(story.story_id, !rootArchived, cache);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "归档失败");
    } finally { setBusy(false); }
  };
  const remove = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await deleteStoryAction(story.story_id, cache, deleteMembership);
      setConfirmDelete(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败");
    } finally { setBusy(false); }
  };
  return (
    <article className="story-card">
      <StoryCover story={story} path={storyPath(story)} />
      <div className="story-card-copy">
        <h3><Link to={storyPath(story)}>{story.title}</Link></h3>
        <p className="story-card-excerpt">{excerpt || `${story.branch_count} 条路线，${story.event_count} 个故事事件。`}</p>
        <p className="story-card-branch">{session?.branch_name || "故事路线"}{session?.archived ? " · 已归档" : ""}</p>
        <div className="story-card-links">
          <Link to={storyPath(story)}>继续故事 <ChevronRight size={15} aria-hidden="true" /></Link>
          <Link to={`/stories/${encodeURIComponent(story.story_id)}/worldline`}>世界线</Link>
        </div>
        <div className="story-card-footer">
          <StoryParticipants session={session} characters={characters} />
          <span className="story-card-date">{stamp(story.updated_at)} 更新</span>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger className="home-icon-button" disabled={busy} aria-label={`更多操作：${story.title}`}>
              <MoreHorizontal size={19} aria-hidden="true" />
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className="home-dropdown" sideOffset={6} align="end">
                <DropdownMenu.Item asChild><a href={exportStoryUrl(story.story_id)}><Download size={15} aria-hidden="true" /> 导出故事</a></DropdownMenu.Item>
                <DropdownMenu.Item disabled={busy} onSelect={() => void archive()}><Archive size={15} aria-hidden="true" /> {rootArchived ? "取消归档主线" : "归档主线"}</DropdownMenu.Item>
                <DropdownMenu.Item disabled={busy} onSelect={() => { setDeleteMembership(story.membership_revision ?? undefined); setConfirmDelete(true); }} className="is-danger"><Trash2 size={15} aria-hidden="true" /> 删除故事</DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
        {error && <p className="home-message is-error" role="alert">{error}</p>}
      </div>
      <ConfirmDialog open={confirmDelete} title={`删除故事「${story.title}」`}
        description={`这会把全部 ${story.branch_count} 条路线、${story.save_count ?? 0} 个存档及相关消息和记忆移入回收区，之后可在书架恢复。角色卡与世界素材仍保留。`}
        busy={busy} error={error} onClose={() => setConfirmDelete(false)} onConfirm={() => void remove()} />
    </article>
  );
}

export function StoryHome() {
  const { data: stories = [], isLoading, error, refetch } = useQuery({
    ...storyQueries.stories(), refetchOnMount: "always",
  });
  const sessions = useChatStore((state) => state.sessions);
  const currentSessionId = useChatStore((state) => state.currentSessionId);
  const snapshotLoadedSessionId = useChatStore((state) => state.snapshotLoadedSessionId);
  const messages = useChatStore((state) => state.messages);
  const sessionCharacters = useChatStore((state) => state.sessionCharacters);
  const libraryCharacters = useCharacterStore((state) => state.characters);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"recent" | "title">("recent");
  const [importMessage, setImportMessage] = useState("");
  const [stImportOpen, setSTImportOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [showTrash, setShowTrash] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [purgeGeneration, setPurgeGeneration] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<string | null>(null);
  const [purgeError, setPurgeError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const cache = useQueryClient();
  const navigate = useNavigate();
  const { data: trashed = [], isLoading: trashLoading, error: trashError, refetch: refetchTrash } = useQuery({
    queryKey: ["story-trash"], queryFn: api.listStoryTrash, enabled: showTrash,
  });
  const { data: backupStatus } = useQuery({ queryKey: ["story-backup-status"], queryFn: api.getStoryBackupStatus, refetchInterval: 60_000 });
  const currentSession = sessions.find((item) => item.id === currentSessionId);
  const latest = useMemo(() => {
    const currentStory = currentSession && stories.find((story) => story.story_id === (currentSession.story_id ?? currentSession.id));
    return currentStory || [...stories].sort((a, b) => compareNewestFirst(a.updated_at, b.updated_at))[0];
  }, [stories, currentSession]);
  const latestSession = latest && (currentSession && (currentSession.story_id ?? currentSession.id) === latest.story_id
    ? currentSession : sessions.find((item) => item.id === latest.latest_branch_id));
  const loadedExcerpt = useMemo(() => snapshotLoadedSessionId && snapshotLoadedSessionId === currentSessionId
    ? existingExcerpt(messages) : "", [snapshotLoadedSessionId, currentSessionId, messages]);
  const charactersFor = (session?: Session) => session?.id === snapshotLoadedSessionId
    ? [...sessionCharacters, ...libraryCharacters.filter((item) => !sessionCharacters.some((character) => character.id === item.id))]
    : libraryCharacters;
  const rows = useMemo(() => stories.filter((story) => story.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
    .sort((a, b) => sort === "recent" ? compareNewestFirst(a.updated_at, b.updated_at) : a.title.localeCompare(b.title, "zh-CN")), [stories, query, sort]);
  const restore = async (storyId: string, generationId?: string | null) => {
    if (restoring) return;
    setRestoring(storyId);
    setImportMessage("");
    try {
      const result = await restoreStoryAction(storyId, cache, generationId);
      setImportMessage(`故事已恢复，包含 ${result.branches.length} 条路线和 ${result.saves.length} 个存档。`);
    } catch (cause) { setImportMessage(cause instanceof Error ? cause.message : "恢复失败"); }
    finally { setRestoring(null); }
  };
  const purge = async () => {
    if (!purgeTarget || restoring) return;
    setRestoring(purgeTarget);
    setPurgeError("");
    try {
      const result = await api.purgeStory(purgeTarget, purgeGeneration);
      await cache.invalidateQueries({ queryKey: ["story-trash"] }).catch(() => undefined);
      setImportMessage(result.cleanup_pending ? "故事变更已保存，后台清理待恢复；独立历史备份仍保留。" : "当前存储和该故事专属回收数据已永久清除；独立历史备份仍保留。");
      setPurgeTarget(null);
    } catch (cause) { setPurgeError(cause instanceof Error ? cause.message : "清除失败"); }
    finally { setRestoring(null); }
  };
  const importStory = async (file?: File) => {
    if (!file || importing) return;
    setImporting(true);
    setImportMessage("");
    try {
      const report = await importStoryAction(file, cache);
      const imported = (await cache.fetchQuery(storyQueries.stories())).find((story) => story.story_id === report.story_id);
      setImportMessage(`导入成功：${report.branches.length} 条路线、${report.saves.length} 个存档。`);
      if (imported) navigate(storyPath(imported));
    } catch (cause) { setImportMessage(cause instanceof Error ? cause.message : "导入失败"); }
    finally {
      setImporting(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };
  return (
    <main className="story-home">
      <header className="story-home-header">
        <div className="home-page-title"><MoonOrnament /><h1>故事</h1><span className="home-title-rule" aria-hidden="true" /></div>
        <Link className="home-button home-button-new" to="/stories/new"><Plus size={20} aria-hidden="true" /> 新建故事</Link>
      </header>
      {stImportOpen && <STChatImportDialog onClose={() => setSTImportOpen(false)} />}
      <input ref={fileInput} type="file" accept=".zip,application/zip" hidden onChange={(event) => void importStory(event.target.files?.[0])} />
      {importMessage && <div className="home-message" role="status">{importMessage}</div>}
      {backupStatus && Object.keys(backupStatus.errors).length > 0 && <div className="home-message is-error" role="alert">
        {Object.entries(backupStatus.errors).map(([id, message]) => <p key={id}>{stories.find((item) => item.story_id === id)?.title ?? id}：{message}</p>)}
      </div>}
      {showTrash && <section className="home-trash" aria-labelledby="home-trash-title">
        <div className="home-section-heading"><h2 id="home-trash-title">回收区 <small>{trashed.length}</small></h2><span className="home-section-rule" aria-hidden="true" /></div>
        {trashLoading ? <p className="home-inline-state" role="status"><LoaderCircle size={18} className="home-spinner" aria-hidden="true" /> 正在打开回收区…</p>
          : trashError ? <div className="home-message is-error" role="alert"><p>回收区加载失败：{String(trashError)}</p><button className="home-button home-button-soft" onClick={() => void refetchTrash()}>重新加载</button></div>
          : trashed.length === 0 ? <p className="home-inline-state">回收区没有故事。</p>
          : <div className="home-trash-grid">{trashed.map((entry) => <article key={entry.story_id} className="home-trash-card">
            <h3>{entry.title}</h3><p>{entry.branch_count} 条路线 · {entry.message_count} 条消息 · {entry.save_count} 个存档</p>
            {!!entry.external_avatar_characters?.length && <p>恢复后需重新设置头像：{entry.external_avatar_characters.join("、")}</p>}
            <div className="home-trash-actions"><button type="button" className="home-button home-button-primary" disabled={restoring !== null} onClick={() => void restore(entry.story_id, entry.generation_id)}>{restoring === entry.story_id ? "恢复中…" : "恢复故事"}</button>
              <button type="button" className="home-button home-button-soft" disabled={restoring !== null} onClick={() => { setPurgeError(""); setPurgeGeneration(entry.generation_id ?? null); setPurgeTarget(entry.story_id); }}>永久清除</button></div>
          </article>)}</div>}
      </section>}
      <ConfirmDialog open={Boolean(purgeTarget)} title="永久清除故事"
        description={`这会永久移除「${trashed.find((item) => item.story_id === purgeTarget)?.title ?? "该故事"}」的当前存储和专属回收数据，之后无法从回收区恢复。独立历史备份不随之删除。`}
        busy={restoring !== null} error={purgeError} onClose={() => setPurgeTarget(null)} onConfirm={() => void purge()} />
      {latest && <section className="home-continue" aria-labelledby="home-continue-title">
        <div className="home-section-heading"><h2 id="home-continue-title">继续故事</h2><span className="home-section-rule" aria-hidden="true" /></div>
        <article className="home-featured">
          <StoryCover story={latest} featured path={storyPath(latest, latestSession?.id)} />
          <div className="home-featured-copy">
            <span className="home-featured-date">{stamp(latest.updated_at)} 更新</span>
            <h3><Link to={storyPath(latest, latestSession?.id)}>{latest.title}</Link></h3>
            <p className="home-featured-branch">当前路线：{latestSession?.branch_name || "故事路线"}{latestSession?.archived ? " · 已归档" : ""}</p>
            <p className="home-featured-excerpt">{latestSession?.id === snapshotLoadedSessionId && loadedExcerpt ? loadedExcerpt
              : latestSession?.world_core_brief?.trim().slice(0, 120) || `${latest.branch_count} 条路线，${latest.event_count} 个故事事件。继续上次的旅程。`}</p>
            <div className="home-featured-bottom">
              <StoryParticipants session={latestSession || undefined} characters={charactersFor(latestSession || undefined)} featured />
              <div className="home-featured-actions"><Link className="home-button home-button-primary home-button-continue" to={storyPath(latest, latestSession?.id)}>继续故事</Link>
                <Link className="home-worldline-link" to={`/stories/${encodeURIComponent(latest.story_id)}/worldline`}>世界线 <ChevronRight size={17} aria-hidden="true" /></Link></div>
            </div>
          </div>
        </article>
      </section>}
      <section className="home-shelf" aria-labelledby="home-shelf-title">
        <div className="home-shelf-header">
          <div className="home-section-heading"><h2 id="home-shelf-title">我的故事 <small>{stories.length}</small></h2><span className="home-section-rule" aria-hidden="true" /></div>
          <div className="home-shelf-tools">
            <label className="home-search"><Search size={18} aria-hidden="true" /><input aria-label="搜索故事" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索故事" /></label>
            <select className="home-sort" aria-label="故事排序" value={sort} onChange={(event) => setSort(event.target.value as "recent" | "title")}><option value="recent">最近更新</option><option value="title">按名称</option></select>
            <button type="button" className="home-tool-button" onClick={() => setSTImportOpen(true)}>迁入酒馆聊天</button>
            <button type="button" className="home-tool-button" disabled={importing} onClick={() => fileInput.current?.click()}><Upload size={16} aria-hidden="true" /><span>{importing ? "导入中…" : "导入"}</span></button>
            <button type="button" className={`home-tool-button${showTrash ? " is-active" : ""}`} aria-expanded={showTrash} onClick={() => setShowTrash((value) => !value)}><Trash2 size={16} aria-hidden="true" /><span>回收区</span></button>
          </div>
        </div>
        {error && stories.length > 0 && <div className="home-message is-error" role="alert"><p>故事列表更新失败：{String(error)}</p><button className="home-button home-button-soft" onClick={() => void refetch()}>重新加载</button></div>}
        {isLoading ? <div className="home-loading" role="status"><p><LoaderCircle size={18} className="home-spinner" aria-hidden="true" /> 正在整理故事书架…</p><div className="home-loading-covers" aria-hidden="true"><span /><span /><span /></div></div>
          : error && stories.length === 0 ? <div className="home-empty home-error" role="alert"><BookOpen size={36} strokeWidth={1.2} aria-hidden="true" /><h3>故事列表加载失败</h3><p>{String(error)}</p><button className="home-button home-button-primary" onClick={() => void refetch()}>重新加载</button></div>
          : rows.length === 0 ? <div className="home-empty"><BookOpen size={40} strokeWidth={1.2} aria-hidden="true" /><h3>{query ? "没有找到匹配的故事" : "书架上还没有故事"}</h3><p>{query ? "试试别的关键词。" : "选择角色与世界，开始你的第一段故事。"}</p>
            {query ? <button className="home-button home-button-soft" onClick={() => setQuery("")}>清除搜索</button> : <Link className="home-button home-button-primary" to="/stories/new">创建第一个故事</Link>}</div>
          : <div className="home-story-grid">{rows.map((story) => {
            const session = sessions.find((item) => item.id === story.latest_branch_id);
            return <StoryCard key={story.story_id} story={story} session={session} characters={charactersFor(session)} excerpt={session?.id === snapshotLoadedSessionId ? loadedExcerpt : undefined} />;
          })}</div>}
      </section>
    </main>
  );
}


export function StoryWizard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const cache = useQueryClient();
  const characters = useCharacterStore((s) => s.characters);
  const books = useLorebookStore((s) => s.books);
  const { data: scenarios = [] } = useQuery({
    ...resourceQueries.scenarios(),
  });
  const { data: worlds = [] } = useQuery({ ...resourceQueries.worlds(), });
  const { data: worldOwners = {} } = useQuery({ ...resourceQueries.owners() });
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<"custom" | "scenario">("custom");
  const [scenarioId, setScenarioId] = useState("");
  const [title, setTitle] = useState("");
  const [characterQuery, setCharacterQuery] = useState('');
  const [bookQuery, setBookQuery] = useState('');
  const [selectedChars, setSelectedChars] = useState<string[]>([]);
  const [greetingChoices, setGreetingChoices] = useState<Record<string, number>>({});
  const [selectedBooks, setSelectedBooks] = useState<string[]>([]);
  const [selectedWorldId, setSelectedWorldId] = useState(searchParams.get('world') ?? "");
  const { data: selectedWorld } = useQuery({
    ...resourceQueries.world(selectedWorldId),
    enabled: Boolean(selectedWorldId),
  });
  useEffect(() => {
    if (selectedWorld && selectedWorld.id === selectedWorldId)
      setSelectedBooks(selectedWorld.lorebook_ids);
  }, [selectedWorldId, selectedWorld?.id]);
  const [persona, setPersona] = useState("我，玩家。");
  const [playerCharacterId, setPlayerCharacterId] = useState("");
  useEffect(() => {
    if (playerCharacterId && selectedChars.includes(playerCharacterId))
      setPlayerCharacterId("");
  }, [playerCharacterId, selectedChars]);
  const [opening, setOpening] = useState("");
  const [openingScene, setOpeningScene] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toggle = (
    values: string[],
    setValues: (next: string[]) => void,
    value: string,
  ) =>
    setValues(
      values.includes(value)
        ? values.filter((item) => item !== value)
        : [...values, value],
    );
  const launch = async () => {
    if (mode === "custom" && (!title.trim() || selectedChars.length === 0)) {
      setError("请填写故事名称并选择至少一位角色");
      return;
    }
    if (mode === "scenario" && !scenarioId) {
      setError("请选择场景预设");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const session =
        mode === "scenario"
          ? await api.startScenario(scenarioId, openingScene.trim())
          : await api.createSession({
              title: title.trim(),
              character_ids: selectedChars,
              lorebook_ids: selectedBooks,
              world_id: selectedWorldId || null,
              persona,
              player_character_id: playerCharacterId || null,
              opening_scene: openingScene.trim(),
              greeting_choices: Object.fromEntries(selectedChars.filter((id) => greetingChoices[id] !== undefined).map((id) => [id, greetingChoices[id]])),
            });
      await Promise.allSettled([useChatStore.getState().loadSessions(), cache.invalidateQueries({ queryKey: ["stories"] })]);
      if (opening.trim())
        useComposerStore.getState().requestPrefill(opening.trim());
      navigate(
        `/stories/${encodeURIComponent(session.story_id ?? session.id)}/branches/${encodeURIComponent(session.id)}`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "开局失败");
    } finally {
      setBusy(false);
    }
  };
  const canNext =
    step === 0
      ? mode === "custom" || Boolean(scenarioId)
      : step === 1
        ? mode === "scenario" ||
          (title.trim().length > 0 && selectedChars.length > 0)
        : true;
  return (
    <main className="v7-page v7-wizard">
      <Link className="v7-back" to="/">
        ← 返回故事书架
      </Link>
      <div className="v7-eyebrow">NEW STORY / 新故事</div>
      <h1>把故事的起点交给你。</h1>
      <div className="v7-stepper">
        {["选择起点", "人物与世界", "你的身份", "核对开局"].map(
          (name, index) => (
            <button
              key={name}
              className={step === index ? "is-active" : ""}
              onClick={() => setStep(index)}
              disabled={index > step + 1 || (index === step + 1 && !canNext)}
            >
              <span>{index + 1}</span>
              {name}
            </button>
          ),
        )}
      </div>
      <div className="v7-wizard-layout">
        <section className="v7-wizard-panel">
          {step === 0 && (
            <>
              <h2>从哪里开始？</h2>
              <p className="v7-muted">
                使用完整场景预设，或自由组合世界、角色与世界书。
              </p>
              <button
                className={`v7-choice ${mode === "custom" ? "is-selected" : ""}`}
                onClick={() => setMode("custom")}
              >
                <Plus />
                <strong>自定义故事</strong>
                <span>自己挑选世界、角色、世界书和玩家身份</span>
              </button>
              <div className="v7-choice-list">
                {scenarios.map((item) => (
                  <button
                    key={item.id}
                    className={`v7-choice ${mode === "scenario" && scenarioId === item.id ? "is-selected" : ""}`}
                    onClick={() => {
                      setMode("scenario");
                      setScenarioId(item.id);
                    }}
                  >
                    <BookOpen />
                    <strong>{item.title}</strong>
                    <span>
                      {item.description ||
                        `${item.character_names.length} 位角色 · ${item.lorebook_count} 本世界书`}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
          {step === 1 &&
            (mode === "scenario" ? (
              <>
                <h2>场景内容</h2>
                <p className="v7-muted">
                  预设已包含人物与世界。开局时会复制一份独立快照。
                </p>
                <div className="v7-summary-line">
                  {scenarios
                    .find((item) => item.id === scenarioId)
                    ?.character_names.join("、") || "已选场景"}
                </div>
              </>
            ) : (
              <>
                <h2>谁会走进这个世界？</h2>
                <label className="v7-field">
                  故事名称
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="例如：示例开场"
                  />
                </label>
                <h3>
                  参与角色 <small>至少选择一位</small>
                </h3>
                <label className="v7-field">查找角色<input value={characterQuery} onChange={(e) => setCharacterQuery(e.target.value)} placeholder="按名字查找，已选人物会保留" /></label>
                <div className="v7-pick-grid">
                  {characters.filter((item) => item.card.name.toLocaleLowerCase().includes(characterQuery.trim().toLocaleLowerCase())).map((item) => (
                    <button
                      key={item.id}
                      aria-pressed={selectedChars.includes(item.id)}
                      className={
                        selectedChars.includes(item.id) ? "is-selected" : ""
                      }
                      onClick={() =>
                        toggle(selectedChars, setSelectedChars, item.id)
                      }
                    >
                      <Users size={18} />
                      <strong>{item.card.name}</strong>
                      <span>{item.card.description || "角色"}</span>
                    </button>
                  ))}
                </div>
                {characters.length === 0 && (
                  <p>
                    暂无角色。先到 <Link to="/library/characters">素材库</Link>{" "}
                    添加角色。
                  </p>
                )}
                <h3>故事所在的世界 <small>可选</small></h3>
                {characters.filter((item) => selectedChars.includes(item.id) && item.card.alternate_greetings.length > 0).map((item) => <label className="v7-field" key={`greeting-${item.id}`}>
                  {item.card.name}的开场白
                  <select value={greetingChoices[item.id] ?? 0} onChange={(e) => setGreetingChoices((old) => ({ ...old, [item.id]: Number(e.target.value) }))}>
                    {[item.card.first_mes, ...item.card.alternate_greetings].map((text, index) => <option key={index} value={index}>{index === 0 ? '默认开场' : `备用开场 ${index}`}：{text.slice(0, 70)}</option>)}
                  </select>
                  <small>仅用于本次故事。填写统一开场场景时，以场景为准。</small>
                </label>)}
                <label className="v7-field">
                  <select value={selectedWorldId} onChange={(e) => { setSelectedWorldId(e.target.value); setSelectedBooks([]); }}>
                    <option value="">不选择世界</option>
                    {worlds.filter((world) => !world.archived).map((world) => <option value={world.id} key={world.id}>{world.title}</option>)}
                  </select>
                </label>
                {selectedWorld && <div className="v7-summary-line"><strong>{selectedWorld.title}</strong><p>{selectedWorld.core_brief || '这个世界还没有核心摘要。'}</p><small>开局时会保存当前版本和核心摘要快照。</small></div>}
                <h3>
                  世界书 <small>可选</small>
                </h3>
                <label className="v7-field">查找世界书<input value={bookQuery} onChange={(e) => setBookQuery(e.target.value)} placeholder="按名称查找" /></label>
                <div className="v7-pick-grid">
                  {books.filter((item) => item.name.toLocaleLowerCase().includes(bookQuery.trim().toLocaleLowerCase())).map((item) => (
                    <button
                      key={item.id}
                      aria-pressed={selectedBooks.includes(item.id)}
                      className={
                        selectedBooks.includes(item.id) ? "is-selected" : ""
                      }
                      onClick={() =>
                        toggle(selectedBooks, setSelectedBooks, item.id)
                      }
                    >
                      <LibraryBig size={18} />
                      <strong>{item.name}</strong>
                      <span>{item.entries.length} 条设定</span>
                    </button>
                  ))}
                </div>
                {selectedWorldId && selectedBooks.some((id) => worldOwners[id] && worldOwners[id].world_id !== selectedWorldId) && <p className="v7-error">已选世界书中有属于其他世界的书。它会被复制到本次故事快照，但后续编辑仍属于原世界；若要长期复用，请先复制并关联到当前世界。</p>}
              </>
            ))}
          {step === 2 && (
            <>
              <h2>你在故事里是谁？</h2>
              <p className="v7-muted">
                角色会据此认识和称呼你。你的第一句话会留在输入框；开场场景会直接写入故事。
              </p>
              {mode === "custom" && (
                <>
                  <label className="v7-field">
                    玩家角色卡 <small>可选，已选的参与角色不会出现在这里</small>
                    <select
                      value={playerCharacterId}
                      onChange={(e) => {
                        const id = e.target.value;
                        setPlayerCharacterId(id);
                        const card = characters.find((item) => item.id === id)?.card;
                        if (card) setPersona(playerPersonaFromCard(card));
                      }}
                    >
                      <option value="">手动填写身份</option>
                      {characters.filter((item) => !selectedChars.includes(item.id)).map((item) => (
                        <option key={item.id} value={item.id}>{item.card.name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="v7-field">
                    我的身份 <small>载入角色卡后仍可补充或修改，本局保存独立快照</small>
                    <textarea
                      rows={6}
                      value={persona}
                      onChange={(e) => setPersona(e.target.value)}
                      placeholder="我是谁？和角色是什么关系？"
                    />
                  </label>
                </>
              )}
              <label className="v7-field">
                第一句话或开场想法 <small>可留空</small>
                <textarea
                  rows={4}
                  value={opening}
                  onChange={(e) => setOpening(e.target.value)}
                  placeholder="雨刚停，我推开书店的门……"
                />
              </label>
              <label className="v7-field">
                开场场景 <small>可留空，创建故事时直接呈现给角色</small>
                <textarea
                  rows={6}
                  value={openingScene}
                  onChange={(e) => setOpeningScene(e.target.value)}
                  placeholder="例如：我和母亲刚从服装店来到陌生森林。店铺已经消失，她也亲眼看到了变化，但还不知道发生了什么。"
                />
              </label>
              <p className="v7-muted">填写后，这段文字会作为开场旁白保存；角色卡的固定开场白不会先出现。角色会在你发送第一句话后回应。如果使用场景预设，这段文字会替换预设的开场旁白。</p>
            </>
          )}
          {step === 3 && (
            <>
              <h2>准备好开篇</h2>
              <div className="v7-review">
                <div>
                  <span>故事起点</span>
                  <strong>
                    {mode === "scenario"
                      ? scenarios.find((item) => item.id === scenarioId)?.title
                      : title}
                  </strong>
                </div>
                <div>
                  <span>参与角色</span>
                  <strong>
                    {mode === "scenario"
                      ? scenarios
                          .find((item) => item.id === scenarioId)
                          ?.character_names.join("、")
                      : characters
                          .filter((item) => selectedChars.includes(item.id))
                          .map((item) => item.card.name)
                          .join("、")}
                  </strong>
                </div>
                <div>
                  <span>世界</span>
                  <strong>{mode === "scenario" ? "由预设配置" : (worlds.find((world) => world.id === selectedWorldId)?.title ?? "未选择")}</strong>
                </div>
                <div>
                  <span>世界书</span>
                  <strong>
                    {mode === "scenario"
                      ? "由预设配置"
                      : `${selectedBooks.length} 本`}
                  </strong>
                </div>
                <div>
                  <span>玩家身份</span>
                  <strong>{mode === "scenario" ? "由预设配置" : (characters.find((item) => item.id === playerCharacterId)?.card.name ?? "手动设定")}</strong>
                </div>
                <div>
                  <span>开场场景</span>
                  <strong>{openingScene.trim() ? openingScene.trim().slice(0, 100) : "沿用角色卡或预设开场"}</strong>
                </div>
                <div>
                  <span>第一句话草稿</span>
                  <strong>
                    {opening.trim()
                      ? opening.trim().slice(0, 60)
                      : "稍后在故事中写下"}
                  </strong>
                </div>
              </div>
            </>
          )}
          {error && (
            <div className="v7-error" role="alert">
              {error}
            </div>
          )}
          <div className="v7-wizard-actions">
            <button
              className="v7-btn v7-btn-soft"
              onClick={() => (step === 0 ? navigate("/") : setStep(step - 1))}
            >
              上一步
            </button>
            {step < 3 ? (
              <button
                className="v7-btn v7-btn-primary"
                disabled={!canNext}
                onClick={() => setStep(step + 1)}
              >
                下一步 <ArrowRight size={16} />
              </button>
            ) : (
              <button
                className="v7-btn v7-btn-primary"
                disabled={busy}
                onClick={() => void launch()}
              >
                {busy ? "正在开局…" : "进入故事"} <ArrowRight size={16} />
              </button>
            )}
          </div>
        </section>
        <aside className="v7-wizard-aside">
          <span className="v7-eyebrow">YOUR STORY</span>
          <BookOpen size={44} strokeWidth={1} />
          <h3>
            {mode === "scenario"
              ? (scenarios.find((item) => item.id === scenarioId)?.title ??
                "新的故事")
              : title || "新的故事"}
          </h3>
          <p>每一次对话，都是新章节的开始。</p>
          <div>
            {mode === "custom"
              ? `${selectedChars.length} 位角色 · ${selectedBooks.length} 本世界书`
              : "场景预设"}
          </div>
        </aside>
      </div>
    </main>
  );
}
