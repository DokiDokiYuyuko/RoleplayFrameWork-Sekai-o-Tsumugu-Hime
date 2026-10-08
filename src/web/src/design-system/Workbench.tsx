import { forwardRef, type HTMLAttributes } from "react";
import { Ornament } from "../appearance/Ornament";
import { cx } from "./internal/cx";
import "./layouts/workbench.css";

export type WorkbenchLayout = "rail-main-side" | "rail-main" | "list-main-side" | "list-main" | "form-main" | "main";

/** A viewport-sized authoring page. Features own their toolbar and form state. */
export const WorkbenchPage = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  function WorkbenchPage({ className, ...props }, ref) {
    return <div ref={ref} className={cx("workbench-page", className)} {...props} />;
  },
);

export interface WorkbenchDeskProps extends HTMLAttributes<HTMLDivElement> {
  layout?: WorkbenchLayout;
  ornament?: boolean;
}

/** One complete frame with internal columns; decoration never belongs to fields. */
export const WorkbenchDesk = forwardRef<HTMLDivElement, WorkbenchDeskProps>(
  function WorkbenchDesk({ layout = "main", ornament = true, className, children, ...props }, ref) {
    return <div ref={ref} className={cx("workbench-desk", className)} data-layout={layout} {...props}>
      {ornament && <Ornament tone="iris" />}
      {children}
    </div>;
  },
);

export interface WorkbenchColumnProps extends HTMLAttributes<HTMLDivElement> {
  variant?: "rail" | "list" | "main" | "aside" | "form";
}

/** Head, body and foot remain feature markup, sharing one content left edge. */
export const WorkbenchColumn = forwardRef<HTMLDivElement, WorkbenchColumnProps>(
  function WorkbenchColumn({ variant, className, ...props }, ref) {
    return <div ref={ref} className={cx("workbench-column", variant ? `workbench-column--${variant}` : undefined, className)} {...props} />;
  },
);
