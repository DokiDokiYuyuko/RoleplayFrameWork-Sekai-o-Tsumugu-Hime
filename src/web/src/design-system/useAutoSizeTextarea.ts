import { useLayoutEffect } from 'react';
import type { RefObject } from 'react';

/** Grow with the draft, including pasted/prefilled text, then scroll within the input. */
export function useAutoSizeTextarea(ref: RefObject<HTMLTextAreaElement>, value: string, scope?: string | null) {
  useLayoutEffect(() => {
    const input = ref.current;
    if (!input) return;
    const resize = () => {
      const style = getComputedStyle(input);
      const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
      const minimum = parseFloat(style.minHeight) || 64;
      const viewport = window.visualViewport?.height ?? window.innerHeight;
      const maximum = Math.max(minimum, Math.min(360, viewport * .36));
      const scrollTop = input.scrollTop;
      input.style.height = '0px';
      const wanted = input.scrollHeight + border;
      input.style.height = `${Math.min(maximum, Math.max(minimum, wanted))}px`;
      // A compact keyboard layout may cap the rendered height below the growth budget.
      const renderedMaximum = Math.min(maximum, input.getBoundingClientRect().height);
      input.style.overflowY = wanted > renderedMaximum ? 'auto' : 'hidden';
      input.scrollTop = scrollTop;
    };
    resize();
    let width = input.getBoundingClientRect().width;
    const observer = new ResizeObserver(() => {
      const nextWidth = input.getBoundingClientRect().width;
      if (nextWidth !== width) { width = nextWidth; resize(); }
    });
    observer.observe(input);
    window.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('resize', resize);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', resize);
      window.visualViewport?.removeEventListener('resize', resize);
    };
  }, [ref, value, scope]);
}
