import type { ComponentType, HTMLAttributes, ReactNode } from "react";
import type { IconProps } from "./Icon";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/emptystate.css";

export interface EmptyStateProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title: ReactNode;
  description?: ReactNode;
  /** Glyph in the region-coloured round mark. */
  icon?: ComponentType<IconProps>;
  /** Illustration URL. Hidden by default; the ornament layer decides at which levels it replaces the icon. */
  art?: string;
  /** One call to action (a secondary Button). */
  action?: ReactNode;
  /** `compact` is an inline strip for panels and side columns. */
  variant?: "page" | "compact";
  /** Heading element for the title; pick the level that fits the page outline. */
  headingLevel?: 2 | 3 | 4;
}

/** What to show when a list or panel has nothing yet, and what to do about it. */
export function EmptyState({ title, description, icon: Glyph, art, action, variant = "page", headingLevel = 3, className, ...rest }: EmptyStateProps) {
  const Heading = `h${headingLevel}` as "h2" | "h3" | "h4";
  return (
    <div className={cx("ui-empty", variant === "compact" && "ui-empty--compact", className)} {...rest}>
      {Glyph && <span className="ui-empty__icon" aria-hidden="true"><Glyph className="ui-icon ui-icon--lg" /></span>}
      {art && <img className="ui-empty__art" src={art} alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; event.currentTarget.parentElement?.setAttribute("data-art-failed", "true"); }} />}
      <Heading className="ui-empty__title">{title}</Heading>
      {description && <p className="ui-empty__text">{description}</p>}
      {action}
    </div>
  );
}
