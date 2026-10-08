import { useEffect, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { useCostStore } from '../store/costStore';
import { useSaveStore } from '../store/saveStore';
import { useActiveCharacters } from '../store/useActiveCharacters';

export default function SessionsPage() {
  const session = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId));
  const characters = useActiveCharacters();
  const { saves, load, create, restore } = useSaveStore();
  const { report, load: loadCost } = useCostStore();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (session) {
      void load(session.id);
      void loadCost(session.id);
    }
  }, [session?.id, load, loadCost]); // eslint-disable-line react-hooks/exhaustive-deps

  const charName = (id: string) =>
    characters.find((c) => c.id === id)?.card.name ?? id;

  const createSave = async () => {
    if (!session || busy) return;
    setBusy(true);
    try {
      await create(session.id, name.trim() || `回合 ${session.turn} 存档`);
      setName('');
      await loadCost(session.id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="workspace-page">
      <div className="workspace-body grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* 存档列表 */}
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h1 className="mb-1 text-base font-semibold text-slate-700">会话存档</h1>
          <p className="mb-4 text-xs text-slate-400">
            当前会话：{session?.title ?? '未选择'} · 第 {session?.turn ?? 0} 回合
          </p>

          <div className="mb-4 flex gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="存档名称"
              className="flex-1 rounded-md border border-slate-300 px-2.5 py-1.5 text-sm focus:border-indigo-400 focus:outline-none"
            />
            <button
              type="button"
              onClick={createSave}
              disabled={!session || busy}
              className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-600 disabled:opacity-40"
            >
              创建存档
            </button>
          </div>

          <div className="space-y-2">
            {saves.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2.5"
              >
                <div>
                  <div className="text-sm font-medium text-slate-700">{s.name}</div>
                  <div className="text-xs text-slate-400">
                    {new Date(s.created_at).toLocaleString('zh-CN')} · 回合 {s.turn} ·{' '}
                    {s.message_count} 条消息
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => restore(s.id)}
                  className="rounded-md border border-indigo-300 px-2.5 py-1 text-xs font-medium text-indigo-500 hover:bg-indigo-50"
                >
                  恢复
                </button>
              </div>
            ))}
            {saves.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-300 py-10 text-center text-sm text-slate-400">
                暂无存档
              </div>
            )}
          </div>
        </section>

        {/* 成本面板 */}
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-700">成本统计</h2>
            {session && (
              <button
                type="button"
                onClick={() => loadCost(session.id)}
                className="text-xs text-indigo-500 hover:underline"
              >
                刷新
              </button>
            )}
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs text-slate-400">
                <th className="py-2 font-normal">角色</th>
                <th className="py-2 text-right font-normal">input tokens</th>
                <th className="py-2 text-right font-normal">output tokens</th>
                <th className="py-2 text-right font-normal">cached</th>
              </tr>
            </thead>
            <tbody>
              {report &&
                Object.entries(report.by_character).map(([cid, bucket]) => (
                  <tr key={cid} className="border-b border-slate-100">
                    <td className="py-2.5 font-medium text-slate-700">{charName(cid)}</td>
                    <td className="py-2.5 text-right text-slate-500">
                      {bucket.input_tokens.toLocaleString()}
                    </td>
                    <td className="py-2.5 text-right text-slate-500">
                      {bucket.output_tokens.toLocaleString()}
                    </td>
                    <td className="py-2.5 text-right font-mono text-slate-700">
                      {bucket.cached_tokens.toLocaleString()}
                    </td>
                  </tr>
                ))}
              {report && Object.keys(report.by_character).length === 0 && (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-sm text-slate-400">
                    暂无生成记录
                  </td>
                </tr>
              )}
            </tbody>
            {report && Object.keys(report.by_character).length > 0 && (
              <tfoot>
                <tr>
                  <td className="py-2.5 font-semibold text-slate-700">合计</td>
                  <td className="py-2.5 text-right font-mono text-slate-600">
                    {report.total.input_tokens.toLocaleString()}
                  </td>
                  <td className="py-2.5 text-right font-mono text-slate-600">
                    {report.total.output_tokens.toLocaleString()}
                  </td>
                  <td className="py-2.5 text-right font-mono font-semibold text-indigo-600">
                    {report.total.cached_tokens.toLocaleString()}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
          <p className="mt-3 text-[10px] text-slate-400">
            按 token 统计（USD 单价表排期 v3）；各角色模型见角色页
          </p>

          {/* R34.4：校验（judge）独立账 + 自动修正统计 */}
          {report?.by_purpose && Object.keys(report.by_purpose).length > 0 && (
            <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>校验（judge，独立计费）</span>
                <span className="font-mono">
                  ↑{report.by_purpose.judge?.input_tokens.toLocaleString() ?? 0} ↓
                  {report.by_purpose.judge?.output_tokens.toLocaleString() ?? 0}
                </span>
              </div>
              {report.hygiene && (
                <div className="mt-1 text-[10px] text-slate-400">
                  校验 {report.hygiene.checks} 次 · 发现违规 {report.hygiene.violations_found} 次
                  {report.hygiene.auto_corrected > 0 && (
                    <span className="text-emerald-600"> · 自动修正 {report.hygiene.auto_corrected} 次</span>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
