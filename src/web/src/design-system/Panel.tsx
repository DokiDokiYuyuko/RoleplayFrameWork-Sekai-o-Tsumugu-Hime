import { forwardRef, type HTMLAttributes } from "react";
import { Slot } from "radix-ui";
import { cx } from "./internal/cx";
import "./primitives/panel.css";

export interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  /** Padding 20 instead of 24. */
  compact?: boolean;
  /** Render the single child element (a `<section>`, `<form>`) instead of a `<div>`. */
  asChild?: boolean;
}

/** Larger content block: radius 16, padding 24. */
export const Panel = forwardRef<HTMLDivElement, PanelProps>(function Panel({ compact = false, asChild = false, className, ...rest }, ref) {
  const Comp = asChild ? Slot.Root : "div";
  return <Comp ref={ref} className={cx("ui-panel", compact && "ui-panel--compact", className)} {...rest} />;
});
