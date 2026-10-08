import { useEffect, useState } from 'react';
import { Dialog } from 'radix-ui';
import { X, Feather } from 'lucide-react';
import { useChatStore } from '../store/chatStore';
import { useWritingStore, writingContext, writingKey } from '../store/writingStore';
import './WritingAssistant.css';

export function WritingAssistant({ branch, identity, composer, onApply, onClose }: {
  branch: string; identity: string | null; composer: string;
  onApply(text: string): void; onClose(): void;
}) {
  const key = writingKey(branch, identity);
  const task = useWritingStore(s => s.tasks[key]);
  const edit = useWritingStore(s => s.edit);
  const generate = useWritingStore(s => s.generate);
  // Subscribe to context updates even when the write task itself did not change.
  useChatStore(s => s.messages);
  useChatStore(s => s.sessions);
  useChatStore(s => s.activeScene);
  useChatStore(s => s.sessionGroups);
  const sending = useChatStore(s => s.sending);
  const [confirm, setConfirm] = useState<{ next: string; source: string; append: boolean; observed: string } | null>(null);
  const [notice, setNotice] = useState('');
  useEffect(() => { useWritingStore.getState().openTask(key, composer); }, [key]);
  if (!task) return null;
  const staleContext = !!task.batch && (task.batch.stale || task.context !== writingContext());
  const oldTask = !!task.batch && task.resultRevision !== task.revision;
  const unavailable = staleContext || oldTask || sending;
  const adopt = (text: string, append: boolean) => {
    if (unavailable) return;
    const next = append ? [composer, text].filter(Boolean).join('\n\n') : text;
    if (composer !== task.composerAtStart && composer.trim()) { setConfirm({ next, source: text, append, observed: composer }); return; }
    onApply(next); setNotice('已填入聊天草稿，可继续编辑后发送。');
  };
  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="v7-dialog-overlay writing-overlay" />
    <Dialog.Content className="v7-dialog-content writing-dialog" aria-describedby="writing-description">
    <header className="writing-heading"><div className="writing-title"><Feather size={22} aria-hidden="true" /><Dialog.Title>故事代笔</Dialog.Title></div>
      <Dialog.Close className="v7-icon-btn" aria-label="关闭代笔"><X size={20} /></Dialog.Close></header>
    <div className="writing-workspace" data-testid="writing-assistant">
      <p id="writing-description" className="writing-intro">告诉助手你想写什么。故事是参考，写作要求决定任务；结果不会自动发送。</p>
      <div className="writing-fields">
        <label className="v7-field">写作要求<textarea aria-label="写作要求" value={task.intent}
          onChange={e => edit(key, { intent: e.target.value })} rows={5}
          placeholder="例如：补充几种不同的行动方案，说明各自的条件和代价。" /></label>
        <label className="v7-field">当前草稿<textarea aria-label="代笔当前草稿" value={task.draft}
          onChange={e => edit(key, { draft: e.target.value })} rows={5} />
          <button type="button" className="v7-btn v7-btn-soft" onClick={() => edit(key, { draft: composer })}>重新带入输入框</button></label>
      </div>
      <label className="v7-field writing-output">输出类型<select aria-label="代笔输出类型" value={task.type}
        onChange={e => edit(key, { type: e.target.value as 'draft' | 'material' })}>
        <option value="draft">完整草稿</option><option value="material">参考素材</option>
      </select></label>
      {task.base && <section className="writing-base"><strong>基于选中的方案调整</strong>
        <details><summary>查看原方案</summary><p>{task.base}</p></details>
        <button type="button" className="v7-btn v7-btn-soft" onClick={() => edit(key, { base: '' })}>取消选中</button>
        <p>在“写作要求”中填写调整要求，再生成新方案。</p></section>}
      {task.error && <p role="alert" className="writing-alert">{task.error}</p>}
      {task.batch?.warnings?.map((w, i) => <p className="writing-hint" key={i}>{w}</p>)}
      {(oldTask || staleContext) && <p role="status" className="writing-alert">
        {staleContext ? '故事或控制身份已变化，这批结果已过期。' : '要求或草稿已修改，这批结果属于旧版本。'}
        仍可复制，请重新生成后采纳。</p>}
      <div className="writing-results">{task.batch?.options.map((option, i) => <article key={i} className="writing-proposal">
        <h3>{option.title || `方案 ${i + 1}`}</h3><p>{option.text}</p>
        <div className="writing-actions">
          <button type="button" className="v7-btn v7-btn-primary" disabled={unavailable} onClick={() => adopt(option.text, false)}>替换草稿</button>
          <button type="button" className="v7-btn v7-btn-soft" disabled={unavailable} onClick={() => adopt(option.text, true)}>追加到草稿</button>
          <button type="button" className="v7-btn v7-btn-soft" onClick={() => {
            if (!navigator.clipboard) { setNotice('当前浏览器不支持自动复制，请选择正文手动复制。'); return; }
            void navigator.clipboard.writeText(option.text).then(
              () => setNotice('已复制。'), () => setNotice('复制失败，请选择正文手动复制。'));
          }}>复制</button>
          <button type="button" className="v7-btn v7-btn-soft" disabled={staleContext || task.generating}
            onClick={() => edit(key, { base: option.text })}>选择并调整</button>
        </div></article>)}</div>
      {confirm !== null && <section className="writing-confirm" role="alert">
        <strong>聊天输入框在生成后已修改，请确认这次更新。</strong>
        <div className="writing-fields"><div>当前输入<p>{composer}</p></div><div>更新后<p>{confirm.next}</p></div></div>
        <button type="button" className="v7-btn v7-btn-primary" disabled={unavailable} onClick={() => {
          if (composer !== confirm.observed) {
            setConfirm({ ...confirm, observed: composer, next: confirm.append ? [composer, confirm.source].filter(Boolean).join('\n\n') : confirm.source });
            setNotice('输入框再次变化，请核对更新后的内容。'); return;
          }
          onApply(confirm.next); setConfirm(null); setNotice('已更新聊天草稿，可撤销。');
        }}>确认更新草稿</button><button type="button" className="v7-btn v7-btn-soft" onClick={() => setConfirm(null)}>保留当前输入</button>
      </section>}
      {notice && <p role="status">{notice}</p>}
      <footer className="writing-footer"><span>使用全局辅助模型 · 不写入正式剧情</span>
        <button type="button" className="v7-btn v7-btn-primary" disabled={!task.intent.trim() || task.generating || sending}
          onClick={() => { setConfirm(null); void generate(key, branch, identity, composer); }}>
          {task.generating ? '正在构思…' : task.batch ? '重新生成三份方案' : '生成三份方案'}</button></footer>
    </div>
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
