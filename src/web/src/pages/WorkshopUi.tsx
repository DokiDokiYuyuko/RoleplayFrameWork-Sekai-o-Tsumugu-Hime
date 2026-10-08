import { useEffect, useState, type ReactNode } from 'react';
import { Collapsible } from 'radix-ui';
import { Button, Segmented } from '../design-system';
import { AlertCircle, CheckCircle2, ChevronDown, Info, LoaderCircle } from '../design-system/Icon';
import { formatWorkshopElapsed } from '../utils/workshopPolicy';
import './workshop-production.css';

/** Presentation-only helpers. Each workshop keeps ownership of its own drafts and commands. */
export function useElapsedSeconds(active: boolean, startedAt?: string) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) { setSeconds(0); return; }
    const parsed = startedAt ? Date.parse(startedAt) : NaN;
    const start = Number.isFinite(parsed) ? parsed : Date.now();
    const tick = () => setSeconds(Math.max(0, Math.floor((Date.now() - start) / 1000)));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);
  return seconds;
}

export function WorkshopWait({ seconds }: { seconds: number }) {
  return <span className="workshop-elapsed">已等待 {formatWorkshopElapsed(seconds)}</span>;
}

export function WorkshopPaneSwitch({ value, onValueChange }: { value: 'form' | 'results'; onValueChange: (value: 'form' | 'results') => void }) {
  return <div className="workshop-pane-switch"><Segmented aria-label="切换填写与结果" size="sm" value={value}
    onValueChange={next => onValueChange(next as 'form' | 'results')} options={[{ value: 'form', label: '填写' }, { value: 'results', label: '结果' }]} /></div>;
}

export function WorkshopNotice({ tone = 'info', busy = false, children, actions }: {
  tone?: 'info' | 'danger' | 'warning' | 'success'; busy?: boolean; children: ReactNode; actions?: ReactNode;
}) {
  const Glyph = busy ? LoaderCircle : tone === 'success' ? CheckCircle2 : tone === 'info' ? Info : AlertCircle;
  return <div className="workshop-notice" data-tone={tone} role={tone === 'danger' ? 'alert' : 'status'}>
    <Glyph className={`ui-icon${busy ? ' workshop-spin' : ''}`} />
    <div className="workshop-notice__body">{children}</div>
    {actions && <div className="workshop-notice__actions">{actions}</div>}
  </div>;
}

export function WorkshopDisclosure({ title, children, defaultOpen = false }: {
  title: ReactNode; children: ReactNode; defaultOpen?: boolean;
}) {
  return <Collapsible.Root className="workshop-disclosure" defaultOpen={defaultOpen}>
    <Collapsible.Trigger asChild><Button variant="ghost" className="workshop-disclosure__trigger">
      <ChevronDown className="ui-icon ui-icon--sm" /><span>{title}</span>
    </Button></Collapsible.Trigger>
    <Collapsible.Content className="workshop-disclosure__body">{children}</Collapsible.Content>
  </Collapsible.Root>;
}
