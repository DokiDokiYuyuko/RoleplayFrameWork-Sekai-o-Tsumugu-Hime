import { useEffect, useRef, useState } from "react";

const PHONE = "(max-width: 899px)";
const CONTROLS = "a[href], button:not(:disabled)";

/**
 * Phone drawer behaviour for the shell: closes on route change and when the
 * viewport grows past the phone layout, makes the main column inert while
 * open, moves focus into the drawer, traps Tab, closes on Escape and returns
 * focus to the trigger on an explicit close.
 */
export function useNavDrawer(pathname: string) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const nav = useRef<HTMLElement>(null);
  const main = useRef<HTMLDivElement>(null);

  // Focus cannot move into an inert subtree, so lift inert before returning it to the trigger.
  const dismiss = () => {
    if (main.current) main.current.inert = false;
    setOpen(false);
    trigger.current?.focus();
  };

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    const phone = window.matchMedia(PHONE);
    const reset = () => { if (!phone.matches) setOpen(false); };
    phone.addEventListener("change", reset);
    return () => phone.removeEventListener("change", reset);
  }, []);
  useEffect(() => { if (main.current) main.current.inert = open; }, [open]);
  useEffect(() => {
    if (!open) return;
    nav.current?.querySelector<HTMLElement>(CONTROLS)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
      if (event.key !== "Tab" || !nav.current) return;
      const controls = Array.from(nav.current.querySelectorAll<HTMLElement>(CONTROLS));
      const start = controls[0], end = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === start) { event.preventDefault(); end?.focus(); }
      else if (!event.shiftKey && document.activeElement === end) { event.preventDefault(); start?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return {
    open,
    show: () => setOpen(true),
    close: dismiss,
    trigger,
    nav,
    main,
  };
}
