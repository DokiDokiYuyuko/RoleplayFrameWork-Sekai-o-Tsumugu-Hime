import { useRef } from 'react';
import { Dialog } from "radix-ui";
import { Ornament } from '../appearance/Ornament';

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认删除",
  busy = false,
  error,
  onClose,
  onConfirm,
  returnFocus,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  busy?: boolean;
  error?: string;
  onClose: () => void;
  onConfirm: () => void;
  returnFocus?: HTMLElement | null;
}) {
  const previousOpen = useRef(false);
  const opener = useRef<HTMLElement | null>(null);
  if (open && !previousOpen.current) opener.current = typeof document === 'undefined' ? null : document.activeElement as HTMLElement;
  previousOpen.current = open;
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="v7-dialog-overlay" />
        <Dialog.Content className="v7-dialog-content moonweave-frame moonweave-frame--dialog" onCloseAutoFocus={event => {
          event.preventDefault();
          const target = returnFocus ?? opener.current;
          if (target?.isConnected) target.focus();
        }}>
          <Ornament variant="dialog" crest />
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>{description}</Dialog.Description>
          {error && (
            <p className="v7-error" role="alert">
              {error}
            </p>
          )}
          <div className="v7-dialog-actions">
            <button
              type="button"
              className="v7-btn v7-btn-soft"
              disabled={busy}
              onClick={onClose}
            >
              取消
            </button>
            <button
              type="button"
              className="v7-btn v7-btn-danger"
              disabled={busy}
              aria-busy={busy}
              onClick={onConfirm}
            >
              {busy ? "处理中…" : confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
