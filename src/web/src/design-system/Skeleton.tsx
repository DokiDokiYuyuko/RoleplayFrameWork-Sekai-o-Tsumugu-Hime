import type { CSSProperties, HTMLAttributes } from "react";
import { cx } from "./internal/cx";
import "./primitives/skeleton.css";

export interface SkeletonProps extends Omit<HTMLAttributes<HTMLSpanElement>, "children"> {
  shape?: "line" | "circle";
  /** CSS length or a pixel number. */
  width?: CSSProperties["width"];
  height?: CSSProperties["height"];
}

/** Loading placeholder. Hidden from assistive tech: announce loading on the container with `aria-busy`. */
export function Skeleton({ shape = "line", width, height, className, style, ...rest }: SkeletonProps) {
  return <span aria-hidden="true" className={cx("ui-skeleton", shape === "circle" && "ui-skeleton--circle", className)} style={{ width, height, ...style }} {...rest} />;
}
