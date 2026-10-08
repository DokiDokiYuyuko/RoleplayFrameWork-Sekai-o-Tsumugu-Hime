import { Panel } from '../design-system/Panel';
import { Button } from '../design-system/Button';
import { useStoryUiStore } from '../features/stories/storyUiStore';
import { storyRuntime } from '../features/stories/storyRuntime';
import { createClientId } from '../utils/clientId.js';
import { useCallback, useEffect, useState } from "react";
import { AssistPanel } from "../components/AssistPanel";
import { StoryContinuityPanel } from "../components/StoryContinuityPanel";
import { TurnRecoveryPanel } from "../components/TurnRecoveryPanel";
import { ChatErrorNotice } from "../components/ChatErrorNotice";
import { Composer } from "../components/Composer";
import { DirectorBanner } from "../components/DirectorBanner";
import { MemoryViewer } from "../components/MemoryViewer";
import { MessageList } from "../components/MessageList";
import { StoryNavigationPanel } from "../components/StoryNavigationPanel";
import { StoryPromptPresetDialog } from "../components/StoryPromptPresetDialog";
import { SessionSidebar } from "../components/SessionSidebar";
import { StoryContextPanel } from '../features/stories/StoryContextPanel';
import { Link, useNavigate, useParams } from "react-router";
import { BookOpen, GitBranch, Info, Menu, MoreHorizontal, Pin, Search, Sparkles } from "../design-system/Icon";
import { storiesClient as api } from '../features/stories/storiesClient';
import { readStoryLocation, writeStoryLocation } from "../api/navigation";
import type { Message } from "../types";
import type { InputGroupEditPart } from "../types";
import { useChatStore } from "../store/chatStore";
import { DropdownMenu } from 'radix-ui';
import { ActionDialog } from "../design-system/ActionDialog";
import "./story-workspace.css";
import "./story-production.css";
import { PublicArt } from "../design-system/PublicArt";
import { Ornament } from "../appearance/Ornament";

export default function ChatPage() {
  const navigate = useNavigate();
  const { storyId: routeStoryId } = useParams();
  const session = useChatStore((s) =>
    s.sessions.find((x) => x.id === s.currentSessionId),
  );
  const sseStatus = useChatStore((s) => s.sseStatus);
  const generationError = useChatStore((s) => s.generationError);
  const dismissGenerationError = useChatStore((s) => s.dismissGenerationError);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [continuityOpen, setContinuityOpen] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [promptPresetOpen, setPromptPresetOpen] = useState(false);
  const [headerMenuOpen, setHeaderMenuOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(() => window.innerWidth > 760);
  useEffect(() => {
    const compact = window.matchMedia('(max-width: 760px)');
    const closeOnCompact = () => { if (compact.matches) setContextOpen(false); };
    compact.addEventListener('change', closeOnCompact);
    return () => compact.removeEventListener('change', closeOnCompact);
  }, []);
  const locatedMessageId = useChatStore(s => s.locatedMessageId);
  const setLocatedMessageId = storyRuntime.setLocation;
  const [actionError, setActionError] = useState<string | null>(null);
  const dismissActionError = useCallback(() => setActionError(null), []);
  useEffect(() => { setActionError(null); }, [session?.id]);
  const [pendingAction, setPendingAction] = useState<{
    kind: "fork" | "event";
    message: Message;
  } | null>(null);
  const [actionTitle, setActionTitle] = useState("");
  const [forkWarning, setForkWarning] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  useEffect(() => {
    const onLocation = () => { const messageId = readStoryLocation().messageId;
      if (messageId && !useChatStore.getState().messages.some(message => message.id === messageId)) void useChatStore.getState().locateMessage(messageId);
      else setLocatedMessageId(messageId);
    };
    onLocation();
    window.addEventListener("mrp:location", onLocation);
    window.addEventListener("popstate", onLocation);
    return () => {
      window.removeEventListener("mrp:location", onLocation);
      window.removeEventListener("popstate", onLocation);
    };
  }, []);

  const forkAt = async (message: Message, title: string): Promise<string> => {
    const sid = useChatStore.getState().currentSessionId;
    if (!sid) throw new Error("请先选择路线");
    const point = await api.getBranchPoint(sid, message.id);
    if (!point.forkable) throw new Error(point.reason ?? "该节点无法安全分支");
    if ((point.history_warning ?? null) !== forkWarning) {
      setForkWarning(point.history_warning ?? null);
      throw new Error('历史记忆状态已变化，请检查提示后再次确认。');
    }
    const result = await api.forkBranch(sid, {
      message_id: message.id,
      title,
      expected_revision: point.branch_revision,
      idempotency_key: createClientId(),
    });
    await Promise.allSettled([useChatStore.getState().loadSessions(), useChatStore.getState().selectSession(result.branch_id)]);
    return result.branch_id;
  };
  const submitPendingAction = async () => {
    if (!pendingAction || !actionTitle.trim()) return;
    setActionBusy(true);
    setActionError(null);
    try {
      if (pendingAction.kind === "fork")
        await forkAt(pendingAction.message, actionTitle.trim());
      else {
        await api.createStoryEvent(pendingAction.message.session_id, {
          anchor_message_id: pendingAction.message.id,
          title: actionTitle.trim(),
          kind: "turning_point",
          visible_to: pendingAction.message.visible_to,
        });
        await useChatStore.getState().loadSessions();
      }
      setPendingAction(null);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActionBusy(false);
    }
  };
  const editInCurrentBranch = async (message: Message, content: string, branchRevision: number, fingerprint: string) => {
    await useChatStore.getState().editMessage(message.id, content, branchRevision, fingerprint);
  };
  const editInputGroupInCurrentBranch = async (messageId: string, parts: InputGroupEditPart[], branchRevision: number) => {
    await useChatStore.getState().editInputGroup(messageId, parts, branchRevision);
  };
  // M12-R41：右侧辅助栏（候选 / 导演与场景 / 叙事）
  const assistPanelOpen = useStoryUiStore((s) => s.assistPanelOpen);
  const assistPanelWide = useStoryUiStore((s) => s.assistPanelWide);
  const toggleAssistPanel = useStoryUiStore((s) => s.toggleAssistPanel);
  const hasCandidates = useChatStore((s) => s.options.length > 0);
  const pinnedCount = session?.pinned_facts?.length ?? 0;
  const setAssistTab = useStoryUiStore((s) => s.setAssistTab);
  const assistTab = useStoryUiStore((s) => s.assistTab);
  const storyId = routeStoryId ?? session?.story_id ?? session?.id ?? "";

  const showAssist = (tab: "assist" | "pinned") => {
    setMemoryOpen(false);
    setSidebarOpen(false);
    setHeaderMenuOpen(false);
    if (assistPanelOpen && assistTab === tab) {
      toggleAssistPanel();
      return;
    }
    setAssistTab(tab);
    if (!assistPanelOpen) toggleAssistPanel();
  };
  const showMemory = () => {
    if (assistPanelOpen) toggleAssistPanel();
    setSidebarOpen(false);
    setHeaderMenuOpen(false);
    setMemoryOpen(true);
  };

  // R32 快捷键：Ctrl+Alt+R 重roll最后一条角色消息 / Ctrl+Alt+←/→ 切换其候选
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || !e.altKey) return;
      const store = useChatStore.getState();
      const messages = store.messages;
      if (messages.length === 0) return;
      const last = messages[messages.length - 1];
      if (last.actor === "player" || last.status !== "final") return;
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        store.swipe(last.id).catch(() => undefined); // 失败提示由气泡内瞬态错误承担
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const active = last.active_variant;
        if (active == null || last.variants.length < 2) return;
        const next = e.key === "ArrowLeft" ? active - 1 : active + 1;
        if (next < 0 || next >= last.variants.length) return;
        e.preventDefault();
        store.switchVariant(last.id, next).catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={`story-workspace v7-chat ${assistPanelOpen ? "has-assist" : ""} ${assistPanelWide ? "assist-wide" : ""}`}>
      <div className={`v7-chat-sidebar ${sidebarOpen ? "is-open" : ""}`}>
        {sidebarOpen && <SessionSidebar
          onNavigate={() => setSidebarOpen(false)}
          onOpenWorldline={(storyId) =>
            navigate(
              `/stories/${encodeURIComponent(storyId ?? routeStoryId ?? session?.story_id ?? session?.id ?? "")}/worldline`,
            )
          }
        />}
      </div>
      <main className="v7-chat-main" aria-label="故事工作台">
        <header className="v7-chat-head story-workspace-header">
          <div className="story-workspace-heading flex w-full min-w-0 items-center gap-2 sm:w-auto">
            <Button variant="ghost"
              type="button"
              onClick={() => {
                if (assistPanelOpen) toggleAssistPanel();
                setMemoryOpen(false);
                setHeaderMenuOpen(false);
                setSidebarOpen(true);
              }}
              className="v7-story-menu"
              aria-label="打开故事列表"
            >
              <Menu size={19} />
            </Button>
            <BookOpen size={32} className="story-workspace-mark" aria-hidden="true" />
            <div className="story-workspace-nameplate min-w-0">
              <div className="story-workspace-meta">
                <Link className="story-workspace-home" to="/"><BookOpen size={13} /> 故事</Link>
                <span role="status" aria-live="polite" className={`story-connection ${sseStatus === "open" ? "is-online" : "is-offline"}`} title="实时连接状态">
                  {sseStatus === "open" ? "已连接" : sseStatus === "idle" ? "连接中" : sseStatus === "reconnecting" ? "重连中" : "已断开"}
                </span>
              </div>
              <h1 className="story-workspace-title">
                <span className="story-workspace-story">{session?.title ?? "未选择故事"}</span>
                {session?.branch_name && session.branch_name !== session.title && <span className="story-workspace-route"><GitBranch size={13} aria-hidden="true" />{session.branch_name}</span>}
              </h1>
            </div>
          </div>
          {session && (
            <div className="v7-chat-actions">
              <Button variant="ghost" type="button" className="story-workspace-continue" onClick={() => setContinuityOpen(true)}>继续故事</Button>
              <Button variant="ghost" type="button" className={`story-context-toggle ${contextOpen ? 'is-active' : ''}`} aria-label="故事资料" aria-controls="story-context" aria-expanded={contextOpen && !assistPanelOpen}
                onClick={() => { if (assistPanelOpen) { toggleAssistPanel(); setContextOpen(true); } else setContextOpen((open) => !open); }} title={contextOpen && !assistPanelOpen ? '收起故事资料' : '打开故事资料'}>
                <Info size={16} /><span className="v7-action-label">资料栏</span>
              </Button>
              <Button variant="ghost"
                type="button"
                onClick={() => showAssist("assist")}
                className={`${assistPanelOpen && assistTab === "assist" ? "is-active" : ""}`}
                title="辅助栏：候选 / 导演与场景 / 叙事"
                aria-label="创作工具"
                data-testid="assist-toggle"
              >
                <Sparkles size={16} /> <span className="v7-action-label">创作工具</span>
                {!assistPanelOpen && hasCandidates && (
                  <span
                    className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-indigo-500"
                    title="有一批候选待用"
                    data-testid="assist-badge"
                  />
                )}
              </Button>
              <DropdownMenu.Root open={headerMenuOpen} onOpenChange={setHeaderMenuOpen}>
                <DropdownMenu.Trigger asChild><Button variant="ghost" type="button" className={`${headerMenuOpen ? "is-active" : ""}`} aria-label="更多故事操作">
                  <MoreHorizontal size={18} /> <span className="v7-action-label">更多</span>
                </Button></DropdownMenu.Trigger>
                <DropdownMenu.Portal><DropdownMenu.Content className="message-action-menu story-workspace-menu" align="end" sideOffset={6}>
                  <DropdownMenu.Item asChild><Button variant="ghost" type="button" className="story-workspace-mobile-continue" onClick={() => { setHeaderMenuOpen(false); setContinuityOpen(true); }}><BookOpen size={16} /> 继续故事</Button></DropdownMenu.Item>
                  <DropdownMenu.Item asChild><Button variant="ghost" type="button" onClick={() => { setHeaderMenuOpen(false); setNavigationOpen(true); }}><Search size={16} /> 剧情导航</Button></DropdownMenu.Item>
                  <DropdownMenu.Item asChild><Link to={`/stories/${encodeURIComponent(storyId)}/worldline`}><GitBranch size={16} /> 世界线</Link></DropdownMenu.Item>
                  <DropdownMenu.Item asChild><Button variant="ghost" type="button" onClick={showMemory}><Info size={16} /> 角色记忆</Button></DropdownMenu.Item>
                  <DropdownMenu.Item asChild><Button variant="ghost" type="button" onClick={() => showAssist("pinned")}><Pin size={16} /> 固定信息{pinnedCount > 0 ? ` · ${pinnedCount}` : ""}</Button></DropdownMenu.Item>
                  <DropdownMenu.Item asChild><Button variant="ghost" type="button" onClick={() => { setHeaderMenuOpen(false); setPromptPresetOpen(true); }}><Sparkles size={16} /> 提示词方案</Button></DropdownMenu.Item>
                </DropdownMenu.Content></DropdownMenu.Portal>
              </DropdownMenu.Root>
            </div>
          )}
        </header>
        <DirectorBanner />
        {actionError && !pendingAction && <ChatErrorNotice error={actionError} onDismiss={dismissActionError} />}
        {generationError && <ChatErrorNotice error={generationError} onDismiss={dismissGenerationError} />}
        <TurnRecoveryPanel />
        <div className={`story-workspace-layout ${contextOpen && !assistPanelOpen && session ? 'has-context' : ''}`}>
        <Panel asChild><section className="story-conversation moonweave-frame moonweave-frame--quiet" aria-label="故事阅读与回应">
          <Ornament variant="main" tone="teal" />
          <PublicArt path="v3/ambient/ambient-teal-tide-edge.png" className="story-reading-ambient" aria-hidden="true" />
        <div className="v7-reader story-reading-frame">
          <MessageList
            locatedMessageId={locatedMessageId}
            onReturnLatest={() => {
              setLocatedMessageId(null);
              if (session) writeStoryLocation(session.story_id ?? session.id, session.id, null, true);
            }}
            onFork={async (message) => {
              if (assistPanelOpen) toggleAssistPanel();
              setMemoryOpen(false);
              setHeaderMenuOpen(false);
              try {
                const point = await api.getBranchPoint(message.session_id, message.id);
                if (!point.forkable) throw new Error(point.reason ?? '该节点无法分支');
                setForkWarning(point.history_warning ?? null);
                setPendingAction({ kind: "fork", message });
              } catch (cause) { setActionError(cause instanceof Error ? cause.message : '节点读取失败'); return; }
              setActionTitle(
                `${session?.branch_name ?? session?.title ?? "故事"} · 新路线`,
              );
            }}
            onEvent={async (message) => {
              if (assistPanelOpen) toggleAssistPanel();
              setMemoryOpen(false);
              setHeaderMenuOpen(false);
              setPendingAction({ kind: "event", message });
              setActionTitle(message.content.slice(0, 24));
            }}
            onEdit={editInCurrentBranch}
            onEditInputGroup={editInputGroupInCurrentBranch}
          />
        </div>
        <div className="v7-composer">
          <Composer />
        </div>
        </section></Panel>
        {contextOpen && !assistPanelOpen && session && <StoryContextPanel onClose={() => setContextOpen(false)} onOpenTools={() => showAssist('assist')} />}
        </div>
      </main>
      {assistPanelOpen && (
        <>
          <Button variant="ghost" type="button" className="v7-assist-shade" aria-label="关闭辅助栏" onClick={toggleAssistPanel} />
          <div className={`v7-assist-panel moonweave-frame moonweave-frame--panel ${assistPanelWide ? "is-wide" : ""}`}>
            <Ornament variant="panel" />
            <AssistPanel />
          </div>
        </>
      )}
      {memoryOpen && <MemoryViewer onClose={() => setMemoryOpen(false)} />}
      {continuityOpen && <StoryContinuityPanel onClose={() => setContinuityOpen(false)} />}
      {navigationOpen && storyId && <StoryNavigationPanel storyId={storyId} onClose={() => setNavigationOpen(false)} />}
      {promptPresetOpen && storyId && <StoryPromptPresetDialog storyId={storyId} currentId={session?.prompt_preset_id ?? null} onClose={() => setPromptPresetOpen(false)} />}
      {pendingAction && (
        <ActionDialog
          warning={pendingAction.kind === "fork" ? forkWarning : null}
          kind={pendingAction.kind}
          excerpt={pendingAction.message.content.slice(0, 180)}
          title={actionTitle}
          onTitleChange={setActionTitle}
          error={actionError}
          busy={actionBusy}
          onSubmit={() => void submitPendingAction()}
          onClose={() => {
            setPendingAction(null);
            setActionError(null);
          }}
        />
      )}
      {sidebarOpen && (
        <Button variant="ghost"
          aria-label="关闭故事列表"
          type="button"
          onClick={() => setSidebarOpen(false)}
          className="v7-chat-shade"
        />
      )}
    </div>
  );
}
