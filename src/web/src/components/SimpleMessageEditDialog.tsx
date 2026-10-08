import { Dialog } from 'radix-ui';
import './message-actions.css';

export interface SimpleEditPreview { updatedAt: string; futureCount: number; variantCount: number }
export function SimpleMessageEditDialog({ text, preview, busy, error, onText, onSave, onRefresh, onClose }: {
  text: string; preview: SimpleEditPreview; busy: boolean; error: string;
  onText: (value: string) => void; onSave: () => void; onRefresh: () => void; onClose: () => void;
}) {
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay" /><Dialog.Content className="v7-dialog-content sc-edit-dialog">
      <Dialog.Title>编辑聊天消息</Dialog.Title>
      <Dialog.Description>保存前请核对这次修改的影响；取消不会更改聊天或输入草稿。</Dialog.Description>
      {preview.futureCount > 0 && <p className="sc-edit-consequence">保存会移除这条消息之后的 {preview.futureCount} 条对话。</p>}
      {preview.variantCount > 1 && <p className="sc-edit-consequence">这条回复的 {preview.variantCount} 个候选将替换为本次编辑内容。</p>}
      <label className="sc-edit-field">消息内容<textarea autoFocus rows={7} value={text} disabled={busy}
        onChange={(event) => onText(event.target.value)} /></label>
      {error && <p role="alert" className="v7-error">{error}</p>}
      <div className="v7-dialog-actions">
        {error && <button type="button" className="v7-btn v7-btn-soft" disabled={busy} onClick={onRefresh}>重新核对影响范围</button>}
        <button type="button" className="v7-btn v7-btn-soft" disabled={busy} onClick={onClose}>取消</button>
        <button type="button" className={`v7-btn ${preview.futureCount > 0 ? 'v7-btn-danger' : 'v7-btn-primary'}`}
          disabled={busy || !text.trim()} onClick={onSave}>
          {busy ? '保存中…' : preview.futureCount > 0 ? `保存并移除后续 ${preview.futureCount} 条` : '保存消息'}
        </button>
      </div>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}
