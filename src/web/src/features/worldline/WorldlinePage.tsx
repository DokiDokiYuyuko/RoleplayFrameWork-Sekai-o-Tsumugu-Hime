import { useWorldlinePages } from './useWorldlinePages';
import { storyQueries } from '../stories/storyQueries';
import { createClientId } from '../../utils/clientId.js';
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ArrowLeft,
  Archive,
  GitBranch,
  ListTree,
  Map as MapIcon,
  Search,
  Waypoints,
  X,
} from "lucide-react";
import { worldlineClient as api } from './worldlineClient';
import { useChatStore } from "../../store/chatStore";
import { writeStoryLocation } from "../../api/navigation";
import type { BranchPoint, BranchSummary, Message } from "../../types";

type Selection = { branch: BranchSummary; messageId: string | null };
type Form = {
  kind: "fork" | "rename" | "event";
  title: string;
  eventId?: string;
};

function BranchNode({
  data,
}: NodeProps<
  Node<{
    branch: BranchSummary;
    active: boolean;
    selected: boolean;
    pick: (branch: BranchSummary, messageId: string | null) => void;
  }>
>) {
  const { branch, active, selected, pick } = data;
  return (
    <div
      className={`v7-flow-node ${active ? "is-current" : ""} ${selected ? "is-selected" : ""} ${branch.archived ? "is-archived" : ""}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <button onClick={() => pick(branch, branch.last_message_id)}>
        <span className="v7-flow-kicker">
          {active
            ? "● 当前运行路线"
            : branch.parent_branch_id
              ? "分支路线"
              : "故事起点"}
        </span>
        <strong>{branch.name}</strong>
        <small>
          {branch.message_count} 条消息 · {branch.events.length} 个事件
        </small>
      </button>
      {branch.events.length > 0 && (
        <div className="v7-flow-events">
          {branch.events.slice(0, 3).map((event) => (
            <button
              key={event.id}
              title={event.title}
              onClick={() => pick(branch, event.anchor_message_id)}
            >
              ◆ {event.title}
            </button>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
const nodeTypes = { branch: BranchNode };

export function WorldlinePage() {
  const { storyId = "" } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const currentBranchId = useChatStore((s) => s.currentSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const { data: storyCards = [] } = useQuery({
    ...storyQueries.stories(),
  });
  const storyTitle =
    storyCards.find((story) => story.story_id === storyId)?.title ??
    sessions.find((s) => s.id === storyId)?.title ??
    "故事";
  const returnBranchId = sessions.some(
    (session) =>
      session.id === currentBranchId &&
      (session.story_id ?? session.id) === storyId,
  )
    ? currentBranchId
    : storyCards.find((story) => story.story_id === storyId)?.latest_branch_id;
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"graph" | "list">(() =>
    window.innerWidth < 760 ? "list" : "graph",
  );
  const [selected, setSelected] = useState<Selection | null>(null);
  const [point, setPoint] = useState<BranchPoint | null>(null);
  const [history, setHistory] = useState<Message[] | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const worldline = useWorldlinePages(storyId, search);
  const { page, branches, isLoading, isError, nextCursor: cursor, refresh: refetch } = worldline;
  // Earlier story links could use a branch id in place of the story id.
  // Resolve those bookmarked URLs to the canonical worldline.
  useEffect(() => {
    if (isLoading || (!isError && page?.branches.length) || storyCards.some((story) => story.story_id === storyId) || !sessions.some((s) => s.id === storyId)) return;
    const canonical = sessions.find(session => session.id === storyId)?.story_id;
    if (canonical && canonical !== storyId) navigate(`/stories/${encodeURIComponent(canonical)}/worldline`, { replace: true });
  }, [isLoading, isError, page, sessions, storyCards, storyId, navigate]);
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 220);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    if (!selected?.messageId) {
      setPoint(null);
      return;
    }
    let live = true;
    setPoint(null);
    void api
      .getBranchPoint(selected.branch.id, selected.messageId)
      .then((result) => {
        if (live) setPoint(result);
      })
      .catch((cause) => {
        if (live)
          setError(cause instanceof Error ? cause.message : "节点预览失败");
      });
    return () => {
      live = false;
    };
  }, [selected]);
  const pick = (branch: BranchSummary, messageId: string | null) => {
    setSelected({ branch, messageId });
    setHistory(null);
    setForm(null);
    setError("");
  };
  const byId = useMemo(
    () => new Map(branches.map((b) => [b.id, b])),
    [branches],
  );
  const graph = useMemo(() => {
    const columns = new Map<number, number>();
    const depth = (branch: BranchSummary) => {
      let result = 0;
      let parent = branch.parent_branch_id;
      const seen = new Set([branch.id]);
      while (parent && byId.has(parent) && !seen.has(parent)) {
        result++;
        seen.add(parent);
        parent = byId.get(parent)!.parent_branch_id;
      }
      return result;
    };
    const nodes: Node[] = branches.map((branch) => {
      const level = depth(branch);
      const row = columns.get(level) ?? 0;
      columns.set(level, row + 1);
      return {
        id: branch.id,
        type: "branch",
        position: { x: 48 + level * 310, y: 48 + row * 220 },
        data: {
          branch,
          active: branch.id === currentBranchId,
          selected: branch.id === selected?.branch.id,
          pick,
        },
        draggable: false,
      };
    });
    const edges: Edge[] = branches
      .filter(
        (branch) =>
          branch.parent_branch_id && byId.has(branch.parent_branch_id),
      )
      .map((branch) => ({
        id: `${branch.parent_branch_id}-${branch.id}`,
        source: branch.parent_branch_id!,
        target: branch.id,
        type: "smoothstep",
        style: { stroke: "#b4c9c5", strokeWidth: 2 },
      }));
    return { nodes, edges };
  }, [branches, byId, currentBranchId, selected]);
  const loadMore = async () => {
    if (!cursor) return;
    setBusy(true);
    try {
      await worldline.loadMore();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载更多失败");
    } finally {
      setBusy(false);
    }
  };
  const switchBranch = async (id: string) => {
    setBusy(true);
    try {
      await useChatStore.getState().loadSessions();
      await useChatStore.getState().selectSession(id, false);
      navigate(
        `/stories/${encodeURIComponent(storyId)}/branches/${encodeURIComponent(id)}`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "切换路线失败");
    } finally {
      setBusy(false);
    }
  };
  const locate = async () => {
    if (!selected?.messageId) return;
    if (selected.branch.id === currentBranchId) {
      writeStoryLocation(storyId, currentBranchId, selected.messageId);
      return;
    }
    setBusy(true);
    try {
      const { messages } = await api.getStoryView(selected.branch.id, { around: selected.messageId ?? undefined });
      setHistory(messages);
      setTimeout(
        () =>
          document
            .getElementById(`worldline-preview-${selected.messageId}`)
            ?.scrollIntoView({ block: "center" }),
        30,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "消息定位失败");
    } finally {
      setBusy(false);
    }
  };
  const refresh = async () => {
    await Promise.all([
      refetch(),
      useChatStore.getState().loadSessions(),
      cache.invalidateQueries({ queryKey: ["stories"] }),
    ]);
  };
  const submit = async () => {
    if (!selected || !form?.title.trim()) return;
    setBusy(true);
    setError("");
    try {
      if (form.kind === "fork") {
        if (!selected.messageId || !point?.forkable) return;
        const currentPoint = await api.getBranchPoint(selected.branch.id, selected.messageId);
        if (!currentPoint.forkable) throw new Error(currentPoint.reason ?? '该节点无法安全分支');
        if ((currentPoint.history_warning ?? null) !== (point.history_warning ?? null)) { setPoint(currentPoint); throw new Error('历史记忆状态已变化，请检查提示后再次确认。'); }
        const created = await api.forkBranch(selected.branch.id, {
          message_id: selected.messageId,
          title: form.title.trim(),
          expected_revision: currentPoint.branch_revision,
          idempotency_key: createClientId(),
        });
        await switchBranch(created.branch_id);
      } else if (form.kind === "rename") {
        const snapshot = await api.getSessionSetup(selected.branch.id);
        await api.patchBranch(selected.branch.id, {
          name: form.title.trim(),
          expected_revision: snapshot.meta.branch_revision,
        });
        setSelected(null);
        await refresh();
      } else if (form.eventId) {
        await api.updateStoryEvent(selected.branch.id, form.eventId, {
          title: form.title.trim(),
        });
        setSelected(null);
        await refresh();
      }
      setForm(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  const archive = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const snapshot = await api.getSessionSetup(selected.branch.id);
      await api.patchBranch(selected.branch.id, {
        archived: !selected.branch.archived,
        expected_revision: snapshot.meta.branch_revision,
      });
      setSelected(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "归档失败");
    } finally {
      setBusy(false);
    }
  };
  const deleteEvent = async (id: string) => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.deleteStoryEvent(selected.branch.id, id);
      setSelected(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除事件失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="v7-worldline">
      <header className="v7-worldline-head">
        <div>
          <Link
            className="v7-back"
            to={
              returnBranchId
                ? `/stories/${encodeURIComponent(storyId)}/branches/${encodeURIComponent(returnBranchId)}`
                : "/"
            }
          >
            <ArrowLeft size={15} /> 返回故事
          </Link>
          <span className="v7-eyebrow">STORY MAP / {storyTitle}</span>
          <h1>
            世界线{" "}
            <small>{page?.total_branches ?? branches.length} 条路线</small>
          </h1>
          <p>选择节点预览，再决定定位、切换或开新线。</p>
        </div>
        <div className="v7-worldline-controls">
          <label className="v7-search">
            <Search size={17} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索路线或事件"
              aria-label="搜索路线或事件"
            />
          </label>
          <div className="v7-segment">
            <button
              aria-pressed={view === "graph"}
              onClick={() => setView("graph")}
            >
              <MapIcon size={16} /> 关系图
            </button>
            <button
              aria-pressed={view === "list"}
              onClick={() => setView("list")}
            >
              <ListTree size={16} /> 列表
            </button>
          </div>
        </div>
      </header>
      <div className={`v7-worldline-body ${selected ? "has-selection" : ""}`}>
        <section className="v7-worldline-canvas" aria-label="世界线浏览">
          {isLoading ? (
            <div className="v7-empty">正在读取世界线…</div>
          ) : isError ? (
            <div className="v7-empty">世界线读取失败，请从故事列表重新进入。</div>
          ) : branches.length === 0 ? (
            <div className="v7-empty">没有匹配的路线或事件。</div>
          ) : view === "graph" ? (
            <ReactFlow
              nodes={graph.nodes}
              edges={graph.edges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ maxZoom: 1 }}
              maxZoom={1.5}
              nodesDraggable={false}
              nodesConnectable={false}
              onNodeClick={(_, node) => {
                const branch = node.data.branch as BranchSummary;
                pick(branch, branch.last_message_id);
              }}
              panOnDrag
              selectionOnDrag={false}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#dbe4de" gap={24} />
              <Controls showInteractive={false} />
            </ReactFlow>
          ) : (
            <div className="v7-worldline-list">
              {branches.map((branch) => (
                <article
                  key={branch.id}
                  className={`v7-worldline-row ${selected?.branch.id === branch.id ? "is-selected" : ""}`}
                  style={{ marginLeft: branch.parent_branch_id ? 28 : 0 }}
                >
                  <button onClick={() => pick(branch, branch.last_message_id)}>
                    <span>
                      {branch.id === currentBranchId
                        ? "● 当前运行路线"
                        : branch.parent_branch_id
                          ? "↳ 分支路线"
                          : "● 故事起点"}
                    </span>
                    <strong>{branch.name}</strong>
                    <small>
                      {branch.message_count} 条消息 · {branch.events.length}{" "}
                      个事件{branch.archived ? " · 已归档" : ""}
                    </small>
                  </button>
                  {branch.events.map((event) => (
                    <button
                      key={event.id}
                      className="v7-event-pill"
                      onClick={() => pick(branch, event.anchor_message_id)}
                    >
                      ◆ {event.title}
                    </button>
                  ))}
                </article>
              ))}
            </div>
          )}
          {cursor && (
            <button
              className="v7-btn v7-btn-soft v7-more"
              disabled={busy}
              onClick={() => void loadMore()}
            >
              加载更多路线
            </button>
          )}
        </section>
        {selected && (
          <button
            type="button"
            className="v7-worldline-detail-shade"
            aria-label="关闭节点详情"
            onClick={() => setSelected(null)}
          />
        )}
        <aside className="v7-worldline-detail">
          {selected ? (
            <>
              <button
                type="button"
                className="v7-worldline-mobile-close"
                onClick={() => setSelected(null)}
              >
                <X size={16} /> 收起详情
              </button>
              <span className="v7-eyebrow">SELECTED NODE / 所选节点</span>
              <h2>{selected.branch.name}</h2>
              <p className="v7-muted">
                {selected.branch.id === currentBranchId
                  ? "当前运行路线"
                  : selected.branch.parent_branch_id
                    ? `源自 ${byId.get(selected.branch.parent_branch_id)?.name ?? "上一条路线"}`
                    : "故事起点"}
              </p>
              <div className="v7-detail-stat">
                <span>
                  <GitBranch size={15} /> {selected.branch.message_count} 条消息
                </span>
                <span>
                  <Waypoints size={15} /> {selected.branch.events.length} 个事件
                </span>
              </div>
              {selected.messageId && (
                <div className="v7-preview">
                  <strong>节点消息</strong>
                  {point ? (
                    <>
                      <p>{point.message.content}</p>
                      {point.history_warning && <p role="alert">{point.history_warning}</p>}
                      {!point.forkable && <small>{point.reason}</small>}
                    </>
                  ) : (
                    <p>正在读取消息…</p>
                  )}
                </div>
              )}
              <div className="v7-detail-actions">
                {selected.messageId && (
                  <button
                    className="v7-btn v7-btn-primary"
                    disabled={busy}
                    onClick={() => void locate()}
                  >
                    定位到消息
                  </button>
                )}
                <button
                  className="v7-btn v7-btn-soft"
                  disabled={busy || selected.branch.id === currentBranchId}
                  onClick={() => void switchBranch(selected.branch.id)}
                >
                  {selected.branch.id === currentBranchId
                    ? "正在此路线"
                    : "切换到这条路线"}
                </button>
                {selected.messageId && (
                  <button
                    className="v7-btn v7-btn-soft"
                    disabled={busy || !point?.forkable}
                    title={point?.reason ?? ""}
                    onClick={() =>
                      setForm({
                        kind: "fork",
                        title: `${selected.branch.name} · 新路线`,
                      })
                    }
                  >
                    从此处开新线
                  </button>
                )}
              </div>
              <div className="v7-detail-secondary">
                <button
                  onClick={() =>
                    setForm({ kind: "rename", title: selected.branch.name })
                  }
                >
                  修改路线名称
                </button>
                <button onClick={() => void archive()}>
                  <Archive size={14} />{" "}
                  {selected.branch.archived ? "取消归档" : "归档路线"}
                </button>
              </div>
              {form && (
                <form
                  className="v7-inline-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void submit();
                  }}
                >
                  <label>
                    {form.kind === "fork"
                      ? "新路线名称"
                      : form.kind === "rename"
                        ? "路线名称"
                        : "事件标题"}
                    <input
                      autoFocus
                      value={form.title}
                      onChange={(e) =>
                        setForm({ ...form, title: e.target.value })
                      }
                    />
                  </label>
                  <div>
                    <button
                      className="v7-btn v7-btn-primary"
                      disabled={busy || !form.title.trim()}
                    >
                      确认
                    </button>
                    <button
                      type="button"
                      className="v7-btn v7-btn-soft"
                      onClick={() => setForm(null)}
                    >
                      取消
                    </button>
                  </div>
                </form>
              )}
              {selected.branch.events.length > 0 && (
                <div className="v7-detail-events">
                  <h3>剧情事件</h3>
                  {selected.branch.events.map((event) => (
                    <div key={event.id}>
                      <button
                        onClick={() =>
                          pick(selected.branch, event.anchor_message_id)
                        }
                      >
                        ◆ {event.title}
                      </button>
                      <button
                        onClick={() =>
                          setForm({
                            kind: "event",
                            title: event.title,
                            eventId: event.id,
                          })
                        }
                      >
                        编辑
                      </button>
                      <button onClick={() => void deleteEvent(event.id)}>
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {history && (
                <div className="v7-readonly-history">
                  <strong>这条路线的只读记录</strong>
                  <p>当前运行路线没有切换。</p>
                  {history.map((message) => (
                    <div
                      key={message.id}
                      id={`worldline-preview-${message.id}`}
                      className={
                        message.id === selected.messageId ? "is-target" : ""
                      }
                    >
                      <small>{message.actor}</small>
                      <p>{message.content}</p>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="v7-detail-empty">
              <GitBranch size={42} strokeWidth={1} />
              <h2>选择一条路线</h2>
              <p>先查看剧情节点，再决定下一步。当前故事不会因预览而改变。</p>
            </div>
          )}
          {error && (
            <div className="v7-error" role="alert">
              {error}
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
