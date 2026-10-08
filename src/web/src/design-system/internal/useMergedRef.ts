import { useCallback, type MutableRefObject, type Ref } from "react";

/** One callback ref that feeds several refs (a forwarded ref plus an internal one). */
export function useMergedRef<T>(...refs: Array<Ref<T> | undefined>): (node: T | null) => void {
  // The refs themselves are the dependency list; their count is fixed per call site.
  return useCallback((node: T | null) => {
    for (const ref of refs) {
      if (typeof ref === "function") ref(node);
      else if (ref) (ref as MutableRefObject<T | null>).current = node;
    }
  }, refs);
}
