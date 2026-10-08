import { useEffect, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { useInspectorStore } from '../store/inspectorStore';
import { useActiveCharacters } from '../store/useActiveCharacters';
import type { PromptSectionKind } from '../types';

/** 分节着色：world=蓝、memory=紫、system=靛、history=灰、instructions=绿 */
const SECTION_STYLE: Record<PromptSectionKind, { border: string; bg: string; text: string; label: string }> = {
  world_core: { border: 'border-indigo-500', bg: 'bg-indigo-50', text: 'text-indigo-700', label: '世界核心' },
  player: { border: 'border-indigo-400', bg: 'bg-indigo-50', text: 'text-indigo-700', label: '玩家身份' },
  group: { border: 'border-fuchsia-400', bg: 'bg-fuchsia-50', text: 'text-fuchsia-700', label: '群体设定' },
  scene_frame: { border: 'border-teal-500', bg: 'bg-teal-50', text: 'text-teal-800', label: '共同场景' },
  scene: { border: 'border-cyan-400', bg: 'bg-cyan-50', text: 'text-cyan-700', label: '场景设定' },
  world: { border: 'border-blue-400', bg: 'bg-blue-50', text: 'text-blue-600', label: '世界设定' },
  memory: { border: 'border-purple-400', bg: 'bg-purple-50', text: 'text-purple-600', label: '相关记忆' },
  system: { border: 'border-indigo-400', bg: 'bg-indigo-50', text: 'text-indigo-600', label: '系统上下文' },
  history: { border: 'border-slate-400', bg: 'bg-slate-100', text: 'text-slate-600', label: '对话记录' },
  instructions: { border: 'border-emerald-400', bg: 'bg-emerald-50', text: 'text-emerald-600', label: '指令' },
};

export default function InspectorPage() {
  const session = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId));
  const characters = useActiveCharacters();
  const groups = useChatStore((s) => s.sessionGroups);
  const { prompt, loading, load } = useInspectorStore();

  const sessionChars = characters.filter((c) => session?.character_ids.includes(c.id));
  const inspectables = [
    ...sessionChars.map((character) => ({ id: character.id, label: character.card.name })),
    ...(session ? groups : []).map((group) => ({ id: group.id, label: `群体 · ${group.label}` })),
  ];
  const [cid, setCid] = useState(sessionChars[0]?.id ?? '');
  const [turn, setTurn] = useState(session?.turn ?? 1);

  // 会话异步加载完成后同步默认选项
  useEffect(() => {
    if (!cid && inspectables.length > 0) setCid(inspectables[0].id);
    if (session && turn > session.turn) setTurn(session.turn);
  }, [session, inspectables.length, cid, turn]);

  const query = () => {
    if (session && cid) void load(session.id, cid, turn);
  };

  return (
    <div className="workspace-page">
      <div className="workspace-body space-y-5">
        <h1 className="text-base font-semibold text-slate-700">注入检查器</h1>
        <p className="text-xs text-slate-400">
          查看任一角色在某回合实际收到的完整上下文（世界书 / 记忆 / 可见消息 / 指令）与 token 分布 ——
          用于解释"为什么角色这么说"。
        </p>

        {/* TurnPicker */}
        <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">角色</label>
            <select
              value={cid}
              onChange={(e) => setCid(e.target.value)}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm"
            >
              {inspectables.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">回合</label>
            <select
              value={turn}
              onChange={(e) => setTurn(Number(e.target.value))}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm"
            >
              {Array.from({ length: session?.turn ?? 0 }, (_, i) => i + 1).map((t) => (
                <option key={t} value={t}>
                  第 {t} 回合
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={query}
            disabled={!session || !cid || loading}
            className="rounded-lg bg-indigo-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-600 disabled:opacity-40"
          >
            {loading ? '查询中…' : '查询'}
          </button>
        </div>

        {/* PromptView */}
        {prompt && (
          <div className="space-y-4">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>
                {inspectables.find((item) => item.id === prompt.character_id)?.label ?? prompt.character_id} · 第{' '}
                {prompt.turn} 回合 · 共 {prompt.sections.length} 节
              </span>
              <span>合计 ≈ {prompt.total_tokens} tokens</span>
            </div>
            {prompt.sections.map((sec) => {
              const style = SECTION_STYLE[sec.kind];
              return (
                <section key={sec.kind} className={`rounded-xl border-l-4 ${style.border} ${style.bg} p-4`}>
                  <header className="mb-2 flex items-center justify-between">
                    <h2 className={`text-sm font-semibold ${style.text}`}>
                      {style.label} · {sec.title}
                    </h2>
                    <span className={`rounded-full bg-white/70 px-2 py-0.5 text-xs font-medium ${style.text}`}>
                      {sec.tokens} tokens
                    </span>
                  </header>
                  <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap font-sans text-xs leading-relaxed text-slate-600">
                    {sec.content}
                  </pre>
                </section>
              );
            })}
          </div>
        )}

        {!prompt && !loading && (
          <div className="rounded-xl border border-dashed border-slate-300 py-16 text-center text-sm text-slate-400">
            选择角色与回合后点击"查询"
          </div>
        )}
      </div>
    </div>
  );
}
