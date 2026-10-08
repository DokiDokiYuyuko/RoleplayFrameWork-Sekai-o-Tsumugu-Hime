import type { ReactNode } from "react";

/** A control must carry a visible label or an explicit accessible name. */
export type NamedControl =
  | { label: ReactNode }
  | { label?: undefined; "aria-label": string }
  | { label?: undefined; "aria-labelledby": string };
