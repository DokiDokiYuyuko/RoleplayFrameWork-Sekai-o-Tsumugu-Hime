import { useEffect, type RefObject } from 'react';

/** Presentation only: never intercept activation, remount a route or own its state. */
export function useMoonweaveMotion(enabled: boolean, intensity: number, pathname: string, route: RefObject<HTMLDivElement>) {
  useEffect(() => {
    if (!enabled || intensity <= 0) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (reduced.matches || !route.current?.animate) return;
    const animation = route.current.animate([
      { opacity: .25, transform: 'translateY(8px)' },
      { opacity: 1, transform: 'translateY(0)' },
    ], { duration: 220, easing: 'cubic-bezier(.2,.7,.2,1)' });
    const stop = () => { if (reduced.matches) animation.cancel(); };
    reduced.addEventListener('change', stop);
    return () => { animation.cancel(); reduced.removeEventListener('change', stop); };
  }, [enabled, intensity, pathname, route]);

  useEffect(() => {
    if (!enabled || intensity <= 0) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const layer = document.createElement('div');
    layer.className = 'mw-click-effects';
    layer.setAttribute('aria-hidden', 'true');
    document.body.append(layer);
    const pending = new Map<HTMLElement, number>();
    const clear = () => {
      pending.forEach(timer => window.clearTimeout(timer));
      pending.clear();
      layer.replaceChildren();
    };
    const show = (event: MouseEvent, keyboard = false) => {
      if (reduced.matches || !(event.target instanceof Element)) return;
      const control = event.target.closest<HTMLElement>('button, a[href], [role="button"]');
      if (!control || control.matches(':disabled, [aria-disabled="true"]') || control.closest('[inert]')) return;
      const box = control.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const glow = document.createElement('span');
      glow.className = 'mw-click-glow';
      glow.style.left = `${keyboard ? box.left + box.width / 2 : event.clientX}px`;
      glow.style.top = `${keyboard ? box.top + box.height / 2 : event.clientY}px`;
      glow.style.setProperty('--mw-effect-opacity', String(intensity));
      // A bounded, short-lived layer also works for dialogs rendered through portals.
      if (pending.size >= 8) {
        const oldest = pending.keys().next().value as HTMLElement;
        window.clearTimeout(pending.get(oldest));
        pending.delete(oldest); oldest.remove();
      }
      layer.append(glow);
      pending.set(glow, window.setTimeout(() => { glow.remove(); pending.delete(glow); }, 520));
    };
    const onPointer = (event: PointerEvent) => { if (event.button === 0) show(event); };
    const onKeyboardClick = (event: MouseEvent) => { if (event.detail === 0) show(event, true); };
    document.addEventListener('pointerdown', onPointer, { passive: true });
    document.addEventListener('click', onKeyboardClick, { passive: true });
    window.addEventListener('blur', clear);
    reduced.addEventListener('change', clear);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('click', onKeyboardClick);
      window.removeEventListener('blur', clear);
      reduced.removeEventListener('change', clear);
      clear(); layer.remove();
    };
  }, [enabled, intensity]);
}
