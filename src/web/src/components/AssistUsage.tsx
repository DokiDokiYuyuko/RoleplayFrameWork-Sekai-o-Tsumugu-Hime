import { useEffect } from "react";
import { useChatStore } from "../store/chatStore";
import { useCostStore } from "../store/costStore";
import { useActiveCharacters } from "../store/useActiveCharacters";

/** 轻量用途中文名（by_purpose 分列，R34.4 起逐步扩充） */
const PURPOSE_LABEL: Record<string, string> = {
  assist: "辅助候选",
  judge: "输出校验",
  memory: "记忆",
  padding: "垫场",
  proactive: "主动发言",
  continue: "续写",
};

/**
 * 用量（token 成本）（M12-R41 v2：从全局 Tab「会话与存档」迁入辅助栏）。
 *
 * 按角色主账 + 轻量用途分列（辅助候选/校验/记忆…）+ 校验统计；会话级数据，随对话走。
 */
export function AssistUsage() {
  const session = useChatStore((s) =>
    s.sessions.find((x) => x.id === s.currentSessionId),
  );
  const characters = useActiveCharacters();
  const { report, load, error } = useCostStore();

  useEffect(() => {
    if (session) void load(session.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.branch_revision]);

  const charName = (id: string) =>
    characters.find((c) => c.id === id)?.card.name ?? id;
  const purposes = report?.by_purpose ? Object.entries(report.by_purpose) : [];

  return (
    <div className="v7-panel-section" data-testid="assist-usage">
      <div className="v7-panel-section-head">
        <h3>用量统计</h3>
        {session && (
          <button
            type="button"
            onClick={() => void load(session.id)}
            className="text-[10px] text-indigo-500 hover:underline"
          >
            刷新
          </button>
        )}
      </div>

      <table className="mt-2 w-full text-[11px]">
        <thead>
          <tr className="border-b border-slate-200 text-left text-[10px] text-slate-400">
            <th className="py-1 font-normal">角色</th>
            <th className="py-1 text-right font-normal">输入</th>
            <th className="py-1 text-right font-normal">输出</th>
            <th className="py-1 text-right font-normal">缓存</th>
          </tr>
        </thead>
        <tbody>
          {report &&
            Object.entries(report.by_character).map(([cid, b]) => (
              <tr key={cid} className="border-b border-slate-100">
                <td className="py-1.5 text-slate-600">{charName(cid)}</td>
                <td className="py-1.5 text-right font-mono text-slate-500">
                  {b.input_tokens.toLocaleString()}
                </td>
                <td className="py-1.5 text-right font-mono text-slate-500">
                  {b.output_tokens.toLocaleString()}
                </td>
                <td className="py-1.5 text-right font-mono text-slate-600">
                  {b.cached_tokens.toLocaleString()}
                </td>
              </tr>
            ))}
          {report && Object.keys(report.by_character).length === 0 && (
            <tr>
              <td
                colSpan={4}
                className="py-5 text-center text-[11px] text-slate-400"
              >
                暂无角色生成记录
              </td>
            </tr>
          )}
        </tbody>
        {report && (report.call_count ?? Object.keys(report.by_character).length) > 0 && (
          <tfoot>
            <tr>
              <td className="py-1.5 font-semibold text-slate-600">合计</td>
              <td className="py-1.5 text-right font-mono text-slate-600">
                {report.total.input_tokens.toLocaleString()}
              </td>
              <td className="py-1.5 text-right font-mono text-slate-600">
                {report.total.output_tokens.toLocaleString()}
              </td>
              <td className="py-1.5 text-right font-mono font-semibold text-indigo-600">
                {report.total.cached_tokens.toLocaleString()}
              </td>
            </tr>
          </tfoot>
        )}
      </table>

      {purposes.length > 0 && (
        <div className="mt-2 rounded-lg bg-slate-50 px-2.5 py-1.5">
          <div className="text-[10px] font-medium text-slate-400">
            调用用途（已包含在合计中）
          </div>
          {purposes.map(([purpose, b]) => (
            <div
              key={purpose}
              className="mt-1 flex items-center justify-between text-[11px] text-slate-500"
            >
              <span>{PURPOSE_LABEL[purpose] ?? purpose}</span>
              <span className="font-mono">
                ↑{b.input_tokens.toLocaleString()} ↓
                {b.output_tokens.toLocaleString()}
              </span>
            </div>
          ))}
          {report?.hygiene && (
            <div className="mt-1 text-[10px] text-slate-400">
              校验 {report.hygiene.checks} 次 · 违规{" "}
              {report.hygiene.violations_found} 次
              {report.hygiene.auto_corrected > 0 && (
                <span className="text-emerald-600">
                  {" "}
                  · 自动修正 {report.hygiene.auto_corrected} 次
                </span>
              )}
            </div>
          )}
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-xs text-rose-600">{error}</p>}
      {report?.usage_incomplete && <p role="status" className="mt-2 text-xs text-amber-700">旧记录或中断调用缺少部分用量，累计值可能不完整。</p>}
      <p className="mt-2 text-[10px] text-slate-400">
        统计本路线及其继承的已记录调用；合计包含辅助用途，按 token 展示。
      </p>
    </div>
  );
}
