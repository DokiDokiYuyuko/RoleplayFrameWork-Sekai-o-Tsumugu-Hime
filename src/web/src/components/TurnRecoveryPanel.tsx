import { useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { ChevronDown, ChevronUp } from 'lucide-react';
import './chat-notices.css';

export function TurnRecoveryPanel() {
  const sid = useChatStore((s) => s.currentSessionId);
  const runs = useChatStore((s) => s.turnRuns);
  const controlTurn = useChatStore((s) => s.controlTurn);
  const actionBusy = useChatStore((s) => s.conversationActionBusy);
  const sending = useChatStore((s) => s.sending);
  const [busy, setBusy] = useState(false);
  const [collapsedRun, setCollapsedRun] = useState<string | null>(null);
  const localRuns = runs.filter((run) => run.session_id === sid);
  const latest = localRuns[localRuns.length - 1];
  const run = latest && ['running', 'failed', 'interrupted'].includes(latest.status) ? latest : null;
  if (!sid || !run) return null;
  const runKey = `${sid}:${run.operation_id}:${run.epoch}`;
  const collapsed = collapsedRun === runKey && run.status !== 'running';
  const act = async (action: 'resume' | 'stop') => {
    setBusy(true);
    try {
      await controlTurn(run.operation_id, action);
    } catch { /* The store owns the one dismissible action error. */ }
    finally { setBusy(false); }
  };
  const complete = run.slots.filter((slot) => slot.status === 'committed').length;
  return <div className={`chat-turn-recovery${collapsed ? ' is-collapsed' : ''}`} role="status">
    <div className="chat-turn-heading"><span>{run.status === 'running' ? '本轮正在回应' : '本轮尚未完成'} · 已保存 {complete}/{run.slots.length} 条回应</span>
      <div className="chat-turn-actions">
        {!collapsed && <button type="button" className="v7-btn v7-btn-soft" disabled={busy || Boolean(actionBusy) || (run.status !== 'running' && sending)} onClick={() => void act(run.status === 'running' ? 'stop' : 'resume')}>{busy || actionBusy === run.operation_id ? '处理中…' : run.status === 'running' ? '停止本轮' : '补完剩余回应'}</button>}
        {run.status !== 'running' && <button type="button" className="v7-btn v7-btn-ghost" aria-expanded={!collapsed} onClick={() => setCollapsedRun(collapsed ? null : runKey)}>{collapsed ? <ChevronDown size={15} /> : <ChevronUp size={15} />}{collapsed ? '查看恢复选项' : '收起提示'}</button>}
      </div></div>
    {!collapsed && run.status !== 'running' && <p className="chat-turn-help">已接受的输入和已完成回应已保留。补完会沿用本轮原来的回复顺序和上下文。</p>}
    {!collapsed && run.last_error && run.status !== 'running' && <details className="chat-turn-details"><summary>查看失败原因</summary><pre>{run.last_error}</pre></details>}
  </div>;
}
