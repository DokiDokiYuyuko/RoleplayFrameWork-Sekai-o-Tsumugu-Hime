import { Popover } from 'radix-ui';
import type { ReactNode } from 'react';
import { useRef } from 'react';
import { X } from 'lucide-react';

/** A shared portal keeps composer controls out of the conversation's layout flow. */
export function ComposerPanel({ title, children, onClose }: {
  title: string; children: ReactNode; onClose: () => void;
}) {
  const previousFocus = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  return <Popover.Portal>
    <Popover.Content ref={contentRef} className="v7-composer-panel v7-composer-popover" side="top" align="start"
      sideOffset={10} collisionPadding={12} sticky="always" aria-label={title}
      onOpenAutoFocus={() => { previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        if (document.activeElement === document.body || contentRef.current?.contains(document.activeElement)) previousFocus.current?.focus({ preventScroll: true });
      }}
      onInteractOutside={(event) => {
        if (event.target instanceof Element && event.target.closest('[data-composer-controls], .v7-response-style-trigger')) event.preventDefault();
      }}>
      <div className="v7-composer-panel-head">
        <strong>{title}</strong>
        <button type="button" onClick={onClose} aria-label={`关闭${title}`}><X size={17} /></button>
      </div>
      <div className="v7-composer-panel-content">{children}</div>
    </Popover.Content>
  </Popover.Portal>;
}
