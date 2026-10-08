import { createClientId } from '../utils/clientId.js';
import { useEffect, useState } from "react";
import { useChatStore } from "../store/chatStore";
import { useSaveStore } from "../store/saveStore";
import { storiesClient as api } from '../features/stories/storiesClient';

/**
 * 会话存档（M12-R41 v2：从全局 Tab「会话与存档」迁入辅助栏）。
 *
 * 存档是当前会话的快照：创建 / 列表 / 恢复（恢复到某存档 = 回滚当前对话）。
 * 真正的"会话管理"（新建/切换/编辑/删除）仍在左侧会话栏。
 */
export function AssistSaves() {
  const session = useChatStore((s) =>
    s.sessions.find((x) => x.id === s.currentSessionId),
  );
  const { saves, load, create, restore, loadError, restoreNotice } = useSaveStore();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [forking, setForking] = useState<{
    saveId: string;
    title: string;
  } | null>(null);

  useEffect(() => {
    if (session) void load(session.id).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id]);

  const createSave = async () => {
    if (!session || busy) return;
    setBusy(true);
    setError('');
    try {
      await create(session.id, name.trim() || `回合 ${session.turn} 存档`);
      setName("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建存档失败，请重试');
    } finally {
      setBusy(false);
    }
  };
  const forkSave = async () => {
    if (!forking || !forking.title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.forkSave(
        forking.saveId,
        forking.title.trim(),
        createClientId(),
      );
      await Promise.allSettled([useChatStore.getState().loadSessions(), useChatStore.getState().selectSession(result.branch_id)]);
      setForking(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "从存档开线失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="v7-panel-section" data-testid="assist-saves">
      <div className="v7-panel-section-head">
        <h3>存档</h3>
      </div>
      {error && (
        <p className="mt-2 rounded bg-rose-50 p-2 text-xs text-rose-700">
          {error}
        </p>
      )}
      {restoreNotice && <p role="status">{restoreNotice}</p>}
      {loadError && <p role="alert">{loadError} <button onClick={() => { if (session) void load(session.id).catch(() => undefined); }}>重新读取存档</button></p>}
      <p className="v7-panel-help">当前会话 · 第 {session?.turn ?? 0} 回合</p>

      <div className="mt-2 flex gap-1.5">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="存档名称（可空）"
          className="h-7 min-w-0 flex-1 rounded-md border border-slate-300 px-2 text-[11px] focus:border-indigo-400 focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void createSave()}
          disabled={!session || busy}
          data-testid="save-create"
          className="h-7 rounded-lg bg-indigo-500 px-2.5 text-[11px] font-medium text-white hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          创建存档
        </button>
      </div>

      <div className="mt-2 space-y-1.5">
        {saves.map((s) => (
          <div
            key={s.id}
            className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5"
          >
            <div className="min-w-0">
              <div className="truncate text-[11px] font-medium text-slate-700">
                {s.name}
              </div>
              <div className="text-[10px] text-slate-400">
                {new Date(s.created_at).toLocaleString("zh-CN")} · 回合 {s.turn}{" "}
                · {s.message_count} 条
              </div>
            </div>
            <div className="flex shrink-0 flex-col gap-1">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setForking({ saveId: s.id, title: `${s.name} · 新路线` })
                }
                className="rounded-md bg-indigo-500 px-2 py-1 text-[10px] text-white"
              >
                从存档开新线
              </button>
              <button
                type="button"
                onClick={() => void restore(s.id)}
                data-testid="save-restore"
                className="shrink-0 rounded-md border border-indigo-300 px-2 py-0.5 text-[10px] font-medium text-indigo-500 hover:bg-indigo-50"
                title="恢复到此存档（当前对话回滚）"
              >
                回滚当前线
              </button>
            </div>
          </div>
        ))}
        {forking && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void forkSave();
            }}
            className="rounded-lg border border-indigo-200 bg-indigo-50 p-2"
          >
            <label className="block text-[11px] text-slate-600">
              新路线名称
              <input
                autoFocus
                value={forking.title}
                onChange={(e) =>
                  setForking({ ...forking, title: e.target.value })
                }
                className="mt-1 w-full rounded border bg-white px-2 py-1.5 text-xs"
              />
            </label>
            <div className="mt-2 flex gap-2">
              <button
                type="submit"
                disabled={busy || !forking.title.trim()}
                className="rounded bg-indigo-500 px-2 py-1 text-[11px] text-white disabled:opacity-40"
              >
                创建并进入
              </button>
              <button
                type="button"
                onClick={() => setForking(null)}
                className="rounded border px-2 py-1 text-[11px]"
              >
                取消
              </button>
            </div>
          </form>
        )}
        {saves.length === 0 && (
          <div className="v7-panel-empty">
            暂无存档。为当前路线保存一个可返回的节点。
          </div>
        )}
      </div>
    </div>
  );
}
