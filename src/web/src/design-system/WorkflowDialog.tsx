import { Dialog } from 'radix-ui';
import { useRef, type ReactNode } from 'react';
import { Ornament } from '../appearance/Ornament';

/** Story workflows share keyboard dismissal, focus trapping and focus return. */
export function WorkflowDialog({ title, onClose, children, centered = false, wide = false, narrow = false, returnFocus }: {
  title: string; onClose: () => void; children: ReactNode; centered?: boolean; wide?: boolean; narrow?: boolean; returnFocus?: HTMLElement | null;
}) {
  const opener = useRef<HTMLElement | null>(typeof document === 'undefined' ? null : document.activeElement as HTMLElement | null);
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="workflow-dialog-overlay fixed inset-0" />
      <Dialog.Content aria-describedby={undefined} onCloseAutoFocus={(event) => {
        event.preventDefault();
        const target = returnFocus ?? opener.current;
        if (target?.isConnected) target.focus();
      }} className={centered
        ? 'workflow-dialog-content moonweave-frame moonweave-frame--dialog fixed left-1/2 top-1/2 w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 outline-none'
        : `workflow-dialog-content moonweave-frame moonweave-frame--dialog fixed bottom-0 right-0 top-0 w-full ${narrow ? 'max-w-[480px]' : wide ? 'max-w-4xl' : 'max-w-3xl'} outline-none`}>
        <Ornament variant="dialog" />
        <Dialog.Title className="sr-only">{title}</Dialog.Title>
        {children}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
