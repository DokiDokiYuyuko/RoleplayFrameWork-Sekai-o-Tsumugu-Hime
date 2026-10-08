import { useSyncExternalStore } from "react";

/** Appearance attributes are maintained by the registry; observers never mutate preferences. */
export function useAppearanceAttribute(name: string, fallback: string): string {
  return useSyncExternalStore((notify) => {
    if (typeof document === "undefined") return () => {};
    const observer = new MutationObserver(notify);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: [name] });
    return () => observer.disconnect();
  }, () => typeof document === "undefined" ? fallback : document.documentElement.getAttribute(name) ?? fallback, () => fallback);
}
