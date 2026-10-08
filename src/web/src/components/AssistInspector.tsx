import { ContextCorrectionPanel } from "./ContextCorrectionPanel";
import { useEffect, useState } from "react";
import { useChatStore } from "../store/chatStore";
import { useInspectorStore } from "../store/inspectorStore";
import { useActiveCharacters } from "../store/useActiveCharacters";
import type { InspectionTarget, PromptSectionKind } from "../types";
import { generationIdForMessage } from '../utils/inspectionTarget.js';

/** 分节着色：world=蓝、memory=紫、system=琥珀、history=灰、instructions=绿 */
const SECTION_STYLE: Record<
  PromptSectionKind,
  { border: string; bg: string; text: string; label: string }
> = {
  world_core: {
    border: "border-indigo-500",
    bg: "bg-indigo-50",
    text: "text-indigo-700",
    label: "世界核心",
  },
  player: {
    border: "border-indigo-400",
    bg: "bg-indigo-50",
    text: "text-indigo-700",
    label: "玩家身份",
  },
  group: {
    border: "border-fuchsia-400",
    bg: "bg-fuchsia-50",
    text: "text-fuchsia-700",
    label: "群体设定",
  },
  scene: {
    border: "border-cyan-400",
    bg: "bg-cyan-50",
    text: "text-cyan-700",
    label: "场景设定",
  },
  scene_frame: {
    border: "border-teal-500",
    bg: "bg-teal-50",
    text: "text-teal-800",
    label: "共同场景",
  },
  world: {
    border: "border-blue-400",
    bg: "bg-blue-50",
    text: "text-blue-600",
    label: "世界设定",
  },
  memory: {
    border: "border-purple-400",
    bg: "bg-purple-50",
    text: "text-purple-600",
    label: "相关记忆",
  },
  system: {
    border: "border-amber-400",
    bg: "bg-amber-50",
    text: "text-amber-700",
    label: "系统上下文",
  },
  history: {
    border: "border-slate-400",
    bg: "bg-slate-100",
    text: "text-slate-600",
    label: "对话记录",
  },
  instructions: {
    border: "border-emerald-400",
    bg: "bg-emerald-50",
    text: "text-emerald-600",
    label: "指令",
  },
};
/** 未知分节兜底（后端未来新增 kind 也不至于白屏） */
const FALLBACK_STYLE = {
  border: "border-slate-300",
  bg: "bg-slate-50",
  text: "text-slate-500",
  label: "其他",
};

/**
 * 注入检查器（M12-R41 v2：从全局 Tab 迁入辅助栏「检查」页签）。
 *
 * 查看某角色在某回合实际收到的完整上下文（世界书/记忆/可见消息/指令）与 token 分布。
 * 支持从「导演与场景」区点「查看本轮上下文」直接联动（requestInspect → inspectTarget）。
 */
export function AssistInspector() {
  const session = useChatStore((s) =>
    s.sessions.find((x) => x.id === s.currentSessionId),
  );
  const characters = useActiveCharacters();
  const groups = useChatStore((s) => s.sessionGroups);
  const { prompt, loading, source, notice, load, preview, clear } = useInspectorStore();
  const target = useChatStore((s) => s.inspectTarget);
  const messages = useChatStore((s) => s.messages);

  const sessionChars = characters.filter((c) =>
    session?.character_ids.includes(c.id),
  );
  const sessionGroups = session ? groups : [];
  const inspectables = [
    ...sessionChars.map((character) => ({ id: character.id, label: character.card.name })),
    ...sessionGroups.map((group) => ({ id: group.id, label: `群体 · ${group.label}` })),
  ];
  const [cid, setCid] = useState("");
  const [turn, setTurn] = useState(1);
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [err, setErr] = useState("");
  const [selectedMessage, setSelectedMessage] = useState<InspectionTarget | null>(null);
  const selectedBubble = messages.find((message) => message.id === selectedMessage?.messageId);
  const activeGenerationId = generationIdForMessage(selectedBubble);
  const inspectingPreviousCandidate = Boolean(selectedMessage?.generationId && activeGenerationId && selectedMessage.generationId !== activeGenerationId);

  // 会话切换：清空结果与本地选择（防跨会话串台）
  useEffect(() => {
    clear();
    setErr("");
    setCid("");
    setTurn(1);
    setSelectedMessage(null);
    setCorrectionOpen(false);
  }, [session?.id, clear]);

  // 默认值：第一个角色 + 当前回合
  useEffect(() => {
    if (!session) return;
    if (!cid && inspectables.length > 0) setCid(inspectables[0].id);
    setTurn((prev) =>
      prev <= session.turn ? Math.max(prev, 1) : Math.max(1, session.turn),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.turn, inspectables.length]);

  const query = async (c = cid, t = turn, messageTarget = selectedMessage) => {
    if (!session || !c) return;
    setErr("");
    try {
      await load(session.id, c, t, messageTarget);
    } catch {
      clear();
      setErr(messageTarget?.generationId ? "这条回应的上下文记录不可用（尚未保存或已清理）。" : messageTarget?.messageId ? "这条旧记录没有单次生成标识，且该回合的上下文记录不可用。" : "该回合没有这个角色或群体的上下文记录（未在该回合发言或已清理）");
    }
  };
  const queryPreview = async () => {
    if (!session || !cid || sessionGroups.some((group) => group.id === cid)) return;
    setErr("");
    setSelectedMessage(null);
    try {
      await preview(session.id, cid);
    } catch (e) {
      clear();
      setErr(e instanceof Error ? e.message : "当前设定预览失败");
    }
  };

  // 导演区「查看本轮上下文」联动：设目标 → 自动查询
  useEffect(() => {
    if (!target || !session) return;
    setCid(target.characterId);
    setTurn(target.turn);
    setSelectedMessage(target.messageId ? target : null);
    void query(target.characterId, target.turn, target.messageId ? target : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, session?.id]);

  return (
    <div className="v7-panel-section" data-testid="assist-inspector" data-inspection-message-id={selectedMessage?.messageId}>
      <div className="v7-panel-section-head">
        <h3>上下文检查</h3>
      </div>
      <p className="v7-panel-help">
        查看角色或群体在某回合实际收到的资料，核对回应采用的输入依据。
      </p>

      <div className="v7-inspector-controls">
        <div className="min-w-0 flex-1">
          <select
            value={cid}
            onChange={(e) => { setCid(e.target.value); setSelectedMessage(null); }}
            aria-label="检查上下文人物或群体"
            className="h-7 w-full rounded-md border border-slate-300 px-1.5 text-[11px] text-slate-600"
          >
            {inspectables.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <select
          value={turn}
          onChange={(e) => { setTurn(Number(e.target.value)); setSelectedMessage(null); }}
          aria-label="检查上下文回合"
          className="h-7 rounded-md border border-slate-300 px-1.5 text-[11px] text-slate-600"
        >
          {Array.from({ length: session?.turn ?? 0 }, (_, i) => i + 1).map(
            (t) => (
              <option key={t} value={t}>
                第 {t} 回合
              </option>
            ),
          )}
        </select>
        <button
          type="button"
          onClick={() => void query()}
          disabled={!session || !cid || loading}
          data-testid="inspect-query"
          className="h-7 rounded-lg bg-indigo-500 px-3 text-[11px] font-medium text-white hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? "查询中…" : "查询"}
        </button>
        {!sessionGroups.some((group) => group.id === cid) && (
          <button
            type="button"
            onClick={() => void queryPreview()}
            disabled={!session || !cid || loading}
            className="h-7 rounded-lg border border-indigo-200 px-2 text-[11px] text-indigo-700 disabled:opacity-40"
          >
            预览下一轮
          </button>
        )}
      </div>

      {selectedMessage?.messageId && selectedMessage.generationId && <button type="button" className="v7-btn v7-btn-soft mt-3" onClick={() => setCorrectionOpen(true)}>核对与修订依据</button>}
      {correctionOpen && selectedMessage?.messageId && selectedMessage.generationId && <ContextCorrectionPanel messageId={selectedMessage.messageId} generationId={selectedMessage.generationId} onClose={() => setCorrectionOpen(false)} />}
      {notice && <p className="v7-panel-help" role="status">{notice}</p>}
      {inspectingPreviousCandidate && <p className="v7-panel-help" role="status">正在查看此前候选的上下文。查看当前候选时，请重新点击该消息的“查看这条上下文”。</p>}

      {err && (
        <p className="mt-2 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-700">
          {err}
        </p>
      )}

      {prompt && (
        <div className="mt-3 space-y-2">
          <p
            className={`rounded px-2 py-1.5 text-[11px] ${prompt.mode === "preview" ? "bg-blue-50 text-blue-700" : "bg-emerald-50 text-emerald-700"}`}
          >
            {prompt.mode === "preview"
              ? "按当前设定重算的下一轮预览，发送时可能因剧情或记忆变化而不同。"
              : source === 'generation' ? "所选这次回应实际使用的上下文记录。" : "该回合实际组装并发送的上下文记录。"}{" "}
            · 当前路线 {session?.branch_name ?? session?.title}
          </p>
          <div className="flex items-center justify-between text-[10px] text-slate-400">
            <span>
              {inspectables.find((item) => item.id === prompt.character_id)?.label ?? prompt.character_id}{" "}
              · 第 {prompt.turn} 回合 · {prompt.sections.length} 节
            </span>
            <span>合计 ≈ {prompt.total_tokens} tokens</span>
          </div>
          {prompt.sections.map((sec) => {
            const style = SECTION_STYLE[sec.kind] ?? FALLBACK_STYLE;
            return (
              <section
                key={sec.kind}
                className={`rounded-lg border-l-4 ${style.border} ${style.bg} px-3 py-2`}
              >
                <header className="mb-1 flex items-center justify-between">
                  <h3 className={`text-[11px] font-semibold ${style.text}`}>
                    {style.label} · {sec.title}
                  </h3>
                  <span
                    className={`rounded-full bg-white/70 px-1.5 py-0.5 text-[10px] font-medium ${style.text}`}
                  >
                    {sec.tokens}
                  </span>
                </header>
                <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-slate-600">
                  {sec.content}
                </pre>
              </section>
            );
          })}
          {prompt.sources && (
            <section className="rounded-lg border bg-white p-3 text-[11px] text-slate-600">
              <p className="mb-2 text-slate-500">实际配置：{prompt.model_id || '未知模型'} · {prompt.gateway || '未知接口'}{prompt.model_provider ? ` · ${prompt.model_provider}` : ''}</p>
              <h3 className="font-semibold">进入上下文的来源与预算</h3>
              <p className="text-slate-400">
                当前路线 {prompt.branch_id ?? session?.id} · 模型窗口 {prompt.context_limit?.toLocaleString() ?? "未知"} tokens
                · 可用输入 {prompt.budget_tokens?.toLocaleString() ?? "未知，尽量保留原文"} tokens
                · 输出预留 {prompt.output_reserve?.toLocaleString() ?? "—"} tokens
                · 来源 {prompt.capacity_source ?? "未知"}。这里是本地估计，已发送请求以请求记录为准。
                {prompt.capacity_fetched_at && <> 元数据获取于 {new Date(prompt.capacity_fetched_at).toLocaleString("zh-CN")}。</>}
              </p>
              {prompt.sources.map((source, index) => (
                <p
                  key={`${source.entry_id}-${index}`}
                  className="mt-1 border-t pt-1"
                >
                  <span
                    className={
                      source.included ? "text-emerald-700" : "text-amber-700"
                    }
                  >
                    {source.included ? "已进入" : "未进入"}
                  </span>{" "}
                  · {source.source} · {source.entry_id} · {source.tokens} tokens
                  · 顺序 {source.order} / 锚点 {source.anchor} ·{" "}
                  {source.included ? source.reason : source.excluded_reason}
                </p>
              ))}
            </section>
          )}
          {prompt.pinned_facts && prompt.pinned_facts.length > 0 && (
            <section className="rounded-lg border bg-white p-3 text-[11px]">
              <h3 className="font-semibold">固定信息</h3>
              {prompt.pinned_facts.map((fact) => (
                <p key={fact.id} className="mt-1">
                  {fact.included ? "✓" : "–"} {fact.id} · {fact.reason} · 可见：
                  {fact.visible_to === "all"
                    ? "全部角色"
                    : fact.visible_to.join("、")}
                </p>
              ))}
            </section>
          )}
          {prompt.not_triggered && prompt.not_triggered.length > 0 && (
            <details className="rounded-lg border bg-white p-3 text-[11px]">
              <summary>
                本轮未进入的世界书条目（{prompt.not_triggered.length}）
              </summary>
              {prompt.not_triggered.map((entry) => (
                <p key={entry.entry_id} className="mt-1">
                  {entry.title} · {entry.reason}
                </p>
              ))}
            </details>
          )}
          {(prompt.omitted_message_ids?.length ?? 0) > 0 && (
            <p className="text-[11px] text-slate-500">
              因历史预算略去 {prompt.omitted_message_ids?.length} 条较早消息。
            </p>
          )}
        </div>
      )}

      {!prompt && !loading && !err && (
        <div className="v7-panel-empty">
          选角色/群体与回合后点「查询」，或从「导演」区点「查看本轮上下文」
        </div>
      )}
    </div>
  );
}
