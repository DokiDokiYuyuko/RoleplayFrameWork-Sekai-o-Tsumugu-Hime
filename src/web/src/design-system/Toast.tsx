import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, CheckCircle2, Info } from "./Icon";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/toast.css";

export type ToastTone = "success" | "info" | "danger";

export interface ToastOptions {
  message: ReactNode;
  tone?: ToastTone;
  /** Milliseconds before it disappears. 0 keeps it until `dismiss`. Defaults to the provider's duration. */
  duration?: number;
}

export interface ToastApi {
  /** Shows a toast and returns its id. A plain string is a success toast. */
  show: (options: ToastOptions | string) => string;
  dismiss: (id: string) => void;
}

interface ToastEntry extends ToastOptions {
  id: string;
}

const ToastContext = createContext<ToastApi | null>(null);

const GLYPHS = { success: CheckCircle2, info: Info, danger: AlertCircle } as const;

function ToastItem({ entry, fallbackDuration, onDismiss }: { entry: ToastEntry; fallbackDuration: number; onDismiss: (id: string) => void }) {
  const [paused, setPaused] = useState(false);
  const tone = entry.tone ?? "success";
  const duration = entry.duration ?? fallbackDuration;
  useEffect(() => {
    if (paused || duration <= 0) return;
    const timer = window.setTimeout(() => onDismiss(entry.id), duration);
    return () => window.clearTimeout(timer);
  }, [paused, duration, entry.id, onDismiss]);
  const Glyph = GLYPHS[tone];
  return (
    <div className={cx("ui-toast", tone !== "success" && `ui-toast--${tone}`)} onPointerEnter={() => setPaused(true)} onPointerLeave={() => setPaused(false)}>
      <Glyph className="ui-icon" />
      <span>{entry.message}</span>
    </div>
  );
}

/**
 * Mount once near the app root. Toasts appear in a polite live region that never takes focus.
 * The region is a top-layer popover where supported so it stays above dialogs.
 */
export function ToastProvider({ children, label = "通知", duration = 3200, max = 3 }: { children: ReactNode; label?: string; duration?: number; max?: number }) {
  const [entries, setEntries] = useState<ToastEntry[]>([]);
  const counter = useRef(0);
  const region = useRef<HTMLDivElement>(null);

  const dismiss = useCallback((id: string) => setEntries((current) => current.filter((entry) => entry.id !== id)), []);
  const show = useCallback((options: ToastOptions | string) => {
    const id = `toast-${++counter.current}`;
    const entry: ToastEntry = { id, ...(typeof options === "string" ? { message: options } : options) };
    setEntries((current) => [...current, entry].slice(-max));
    return id;
  }, [max]);
  const api = useMemo<ToastApi>(() => ({ show, dismiss }), [show, dismiss]);

  // The live region stays mounted (and empty) so screen readers register it before the first message.
  useEffect(() => {
    const node = region.current;
    if (!node || typeof node.showPopover !== "function") return;
    try {
      if (!node.matches(":popover-open")) node.showPopover();
    } catch {
      // Popover unsupported or already open; the fixed-position fallback in toast.css applies.
    }
  }, []);

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div ref={region} className="ui-toasts" role="region" aria-label={label} aria-live="polite" {...{ popover: "manual" }}>
          {entries.map((entry) => <ToastItem key={entry.id} entry={entry} fallbackDuration={duration} onDismiss={dismiss} />)}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast must be used inside <ToastProvider>.");
  return api;
}
