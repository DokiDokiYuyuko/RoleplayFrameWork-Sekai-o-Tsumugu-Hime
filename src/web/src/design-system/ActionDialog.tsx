import { useEffect, useState } from "react";
import { Dialog } from "radix-ui";
import { Ornament } from '../appearance/Ornament';

export function ActionDialog({
  kind,
  excerpt,
  warning,
  title,
  onTitleChange,
  error,
  busy,
  onSubmit,
  onClose,
}: {
  kind: "fork" | "event";
  excerpt: string;
  warning?: string | null;
  title: string;
  onTitleChange: (value: string) => void;
  error: string | null;
  busy: boolean;
  onSubmit: () => void;
  onClose: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  useEffect(() => setAcknowledged(false), [warning]);
  const heading = kind === "fork" ? "从此处开新世界线" : "标记剧情事件";
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="v7-dialog-overlay" />
        <Dialog.Content className="v7-dialog-content moonweave-frame moonweave-frame--dialog" aria-label={heading}>
          <Ornament variant="dialog" crest />
          <Dialog.Title>{heading}</Dialog.Title>
          <Dialog.Description>
            以这条消息为剧情节点：{excerpt}
          </Dialog.Description>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (busy || !title.trim() || (warning && !acknowledged)) return;
              onSubmit();
            }}
          >
            <label>
              {kind === "fork" ? "路线名称" : "事件标题"}
              <input
                autoFocus
                value={title}
                onChange={(event) => onTitleChange(event.target.value)}
              />
            </label>
            {warning && <div role="alert"><p>{warning}</p><label><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />我了解历史记忆可能不完整，继续创建分支</label></div>}
            {error && (
              <p className="v7-error" role="alert">
                {error}
              </p>
            )}
            <div className="v7-dialog-actions">
              <button
                type="button"
                className="v7-btn v7-btn-soft"
                onClick={onClose}
              >
                取消
              </button>
              <button
                type="submit"
                className="v7-btn v7-btn-primary"
                aria-busy={busy}
                disabled={busy || !title.trim() || Boolean(warning && !acknowledged)}
              >
                {busy ? "处理中…" : "确定"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
