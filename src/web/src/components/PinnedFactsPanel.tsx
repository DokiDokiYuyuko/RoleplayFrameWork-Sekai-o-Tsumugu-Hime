import { useEffect, useState } from "react";
import { useActiveCharacters } from "../store/useActiveCharacters";
import { useChatStore } from "../store/chatStore";
import type { Visibility } from "../types";

export function PinnedFactsPanel() {
  const session = useChatStore((state) =>
    state.sessions.find((item) => item.id === state.currentSessionId),
  );
  const messages = useChatStore((state) => state.messages);
  const createPinnedFact = useChatStore((state) => state.createPinnedFact);
  const updatePinnedFact = useChatStore((state) => state.updatePinnedFact);
  const deletePinnedFact = useChatStore((state) => state.deletePinnedFact);
  const characters = useActiveCharacters();
  const facts = session?.pinned_facts ?? [];
  const [newContent, setNewContent] = useState("");
  const [newScope, setNewScope] = useState("all");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setNewContent("");
    setNewScope("all");
    setEditingId(null);
    setError("");
  }, [session?.id]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const add = () => {
    const content = newContent.trim();
    if (!content || !session || facts.length >= 20) return;
    const visible_to: Visibility = newScope === "all" ? "all" : [newScope];
    void run(async () => {
      await createPinnedFact({ content, visible_to });
      setNewContent("");
    });
  };

  const saveEdit = (factId: string) => {
    const content = editContent.trim();
    if (!content) return;
    void run(async () => {
      await updatePinnedFact(factId, content);
      setEditingId(null);
    });
  };

  const scopeLabel = (visibleTo: Visibility) =>
    visibleTo === "all"
      ? "全体角色可见"
      : `仅 ${visibleTo.map((id) => characters.find((character) => character.id === id)?.card.name ?? id).join("、")} 可见`;

  return (
    <section className="v7-panel-section" data-testid="pinned-facts-panel">
      <div className="v7-panel-section-head">
        <h3>固定信息</h3>
        <span className="v7-panel-count">{facts.length} / 20</span>
      </div>
      <p className="v7-panel-help">
        让角色在后续回应中持续参考重要事实，适合本段剧情的约定或关键物品去向。
        剧情变化后，请编辑或取消固定。
      </p>
      <details className="v7-panel-help">
        <summary className="cursor-pointer">与记忆有什么区别？</summary>
        <p>过去的经历可用消息菜单里的“记住这件事”保存；固定信息用于每轮提醒角色当前必须留意的事实。
          从消息固定会沿用原消息的可见范围，手动添加请确认提供给哪些角色。最多 20 条，总量约 700 tokens，请保持简短。</p>
      </details>
      {error && (
        <p
          role="alert"
          className="mt-2 rounded-md bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700"
        >
          {error}
        </p>
      )}

      {facts.length === 0 ? (
        <div className="v7-panel-empty">
          还没有固定信息。在消息的“消息操作”菜单中选择“固定信息”，或在下方写一句事实。
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {facts.map((fact) => {
            const source = fact.source_message_id
              ? messages.find(
                  (message) => message.id === fact.source_message_id,
                )
              : null;
            const sourceName =
              fact.source_actor === "director"
                ? "场景旁白"
                : fact.source_actor === "player"
                  ? "你"
                  : (characters.find(
                      (character) => character.id === fact.source_actor,
                    )?.card.name ?? "角色消息");
            return (
              <article
                key={fact.id}
                className="rounded-lg border border-amber-100 bg-amber-50/50 p-2.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] text-slate-400">
                      {fact.source_message_id
                        ? source
                          ? `来自 ${sourceName} · 第 ${source.turn} 回合`
                          : `来自 ${sourceName} · 来源消息已移除`
                        : "手动固定"}
                      {" · "}
                      {scopeLabel(fact.visible_to)}
                    </p>
                    {editingId === fact.id ? (
                      <textarea
                        value={editContent}
                        onChange={(event) => setEditContent(event.target.value)}
                        maxLength={10000}
                        rows={3}
                        className="mt-1.5 w-full resize-y rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs leading-relaxed text-slate-700 focus:border-indigo-400 focus:outline-none"
                      />
                    ) : (
                      <p className="mt-1.5 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">
                        {fact.content}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {editingId === fact.id ? (
                      <>
                        <button
                          type="button"
                          disabled={busy || !editContent.trim()}
                          onClick={() => saveEdit(fact.id)}
                          className="rounded px-1.5 py-1 text-[10px] text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setEditingId(null)}
                          className="rounded px-1.5 py-1 text-[10px] text-slate-400 hover:bg-slate-100"
                        >
                          取消
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => {
                            setEditingId(fact.id);
                            setEditContent(fact.content);
                          }}
                          className="rounded px-1.5 py-1 text-[10px] text-slate-500 hover:bg-white"
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void run(() => deletePinnedFact(fact.id))
                          }
                          className="rounded px-1.5 py-1 text-[10px] text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                        >
                          取消固定
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <div className="mt-4 border-t border-slate-100 pt-3">
        <label
          className="block text-[11px] font-medium text-slate-500"
          htmlFor="pinned-fact-new"
        >
          手动添加事实或剧情约定
        </label>
        <textarea
          id="pinned-fact-new"
          value={newContent}
          onChange={(event) => setNewContent(event.target.value)}
          maxLength={10000}
          rows={3}
          placeholder="例如：备用钥匙目前在周野手里；林知夏还不知道。"
          className="mt-1.5 w-full resize-y rounded-md border border-slate-300 px-2.5 py-2 text-xs leading-relaxed focus:border-indigo-400 focus:outline-none"
        />
        <div className="mt-1.5 flex items-center gap-2">
          <select
            value={newScope}
            onChange={(event) => setNewScope(event.target.value)}
            className="h-7 min-w-0 flex-1 rounded-md border border-slate-300 px-1.5 text-[11px] text-slate-600"
          >
            <option value="all">提供给全体角色</option>
            {characters.map((character) => (
              <option key={character.id} value={character.id}>
                只提供给 {character.card.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={!newContent.trim() || busy || facts.length >= 20}
            onClick={add}
            className="h-7 shrink-0 rounded-md bg-indigo-600 px-2.5 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            固定
          </button>
        </div>
      </div>
    </section>
  );
}
