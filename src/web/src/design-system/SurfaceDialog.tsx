import { Dialog } from 'radix-ui';
import { useRef, type ReactNode } from 'react';
/** Preserve a feature's surface geometry while sharing modal keyboard/focus ownership. */
export function SurfaceDialog({ title, className, children, onClose, busy = false, nested = false }: {
  title: string; className: string; children: ReactNode; onClose: () => void; busy?: boolean; nested?: boolean;
}) {
  const opener = useRef<HTMLElement | null>(typeof document === 'undefined' ? null : document.activeElement as HTMLElement | null);
  return <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="surface-dialog-overlay fixed inset-0" />
    <Dialog.Content aria-describedby={undefined} className={`moonweave-surface-host ${className}`} style={{ zIndex: `var(--layer-${nested ? 'confirm' : 'workflow'}-content)` }}
      onEscapeKeyDown={event => { if (busy) event.preventDefault(); }}
      onPointerDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}
      onCloseAutoFocus={event => { event.preventDefault(); if (opener.current?.isConnected) opener.current.focus(); }}>
      <Dialog.Title className="sr-only">{title}</Dialog.Title>{children}
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
