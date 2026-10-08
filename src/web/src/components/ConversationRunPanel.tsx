import type { ConversationRun } from '../types';
import { isConversationActive } from '../store/chatStore';

const statusLabels: Record<ConversationRun['status'], string> = {
  queued: '等待开始', running: '交谈进行中', paused: '已暂停', awaiting_user: '等待你回应',
  completed: '本段已结束', failed: '交谈遇到问题', interrupted: '交谈已中断', cancelled: '已停止',
};
const reasons: Record<string, string> = {
  max_replies: '达到本次条数', natural_stop: '暂时没有需要接续的话', needs_user: '需要你来决定或回应',
  user_paused: '已按你的要求暂停', user_stopped: '已按你的要求停止', generation_failed: '回复生成失败',
  scene_changed: '场景已变化', control_changed: '控制人物已变化', service_restarted: '服务重新启动，已完成内容已保留',
};
export function ConversationRunPanel({ run, speakerName, busy, contextMatches, readOnly = false, history = false, maxReplies, onControl, onIntervene }: {
  run: ConversationRun; speakerName: string | null; busy: boolean; contextMatches: boolean; readOnly?: boolean; history?: boolean; maxReplies: number;
  onControl: (action: 'pause' | 'stop' | 'resume', additionalReplies?: number) => void;
  onIntervene: () => void;
}) {
  const active = !readOnly && !history && isConversationActive(run);
  const resumable = !history && ['paused', 'awaiting_user', 'failed', 'interrupted', 'completed'].includes(run.status);
  return <section className="conversation-run-panel" aria-label={history ? '交谈历史记录' : '交谈运行状态'}>
    <div className="conversation-run-heading" role={history ? undefined : 'status'} aria-live={history ? undefined : 'polite'}>
      <strong>{readOnly ? '此前交谈记录' : run.pause_requested && active ? '正在暂停，等待当前回复完成' : statusLabels[run.status]}</strong>
      <span>已完成 {run.completed_replies} / {run.max_replies} 条</span>
    </div>
    {active && <p>{speakerName ? `${speakerName} ${run.stage === 'committing' ? '的回复正在保存' : '正在发言'}` : '正在安排下一位发言者'}</p>}
    {!active && run.stop_reason && <p>{reasons[run.stop_reason] ?? '本段交谈已结束'}</p>}
    {run.last_error && <p role={history ? undefined : 'alert'}>{history ? '此前问题：' : ''}{run.last_error}</p>}
    {run.usage_incomplete && <p role={history ? undefined : 'status'}>上游用量可能未全部返回，目前仅统计已确认用量。</p>}
    {readOnly && <p>这段交谈属于另一条路线，仅供查看。</p>}
    <div className="conversation-run-actions">
      {active && <>
        <button type="button" aria-label="暂停交谈" disabled={busy || run.pause_requested} onClick={() => onControl('pause')}>暂停</button>
        <button type="button" aria-label="立即停止交谈" disabled={busy} onClick={() => onControl('stop')}>立即停止</button>
        <button type="button" aria-label="干预交谈" disabled={busy} onClick={onIntervene}>暂停并干预</button>
      </>}
      {resumable && <>
        <button type="button" aria-label={run.status === 'completed' ? '再聊一段' : '继续交谈'} disabled={busy || readOnly || !contextMatches}
          title={contextMatches ? undefined : '场景或控制人物已变化，请重新开始交谈'}
          onClick={() => onControl('resume', run.status === 'completed' ? maxReplies : undefined)}>
          {run.status === 'completed' ? `再聊 ${maxReplies} 条` : '继续交谈'}</button>
        {run.status !== 'completed' && <button type="button" aria-label="立即停止交谈" disabled={busy || readOnly} onClick={() => onControl('stop')}>结束本次交谈</button>}
        <button type="button" aria-label="干预交谈" disabled={busy || readOnly} onClick={onIntervene}>回到输入框</button>
      </>}
    </div>
  </section>;
}
