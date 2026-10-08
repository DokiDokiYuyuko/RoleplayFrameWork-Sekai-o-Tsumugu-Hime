import { forwardRef, type HTMLAttributes } from "react";
import { X } from "./Icon";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/tag.css";

export type TagTone = "neutral" | "accent" | "region" | "success" | "warning" | "danger";

type Removable =
  | { onRemove?: undefined; removeLabel?: undefined }
  /** The × button needs a name, e.g. "移除标签 向导". */
  | { onRemove: () => void; removeLabel: string };

export type TagProps = Omit<HTMLAttributes<HTMLSpanElement>, "onSelect"> & Removable & {
  tone?: TagTone;
  /** Status dot before the label. */
  dot?: boolean;
  /** Semibold label, for status badges. */
  strong?: boolean;
};

/** Pill for metadata and status; also exported as `Badge`. Removable when `onRemove` is given. */
export const Tag = forwardRef<HTMLSpanElement, TagProps>(function Tag(
  { tone = "neutral", dot = false, strong = false, onRemove, removeLabel, className, children, ...rest },
  ref,
) {
  return (
    <span
      ref={ref}
      className={cx("ui-tag", tone !== "neutral" && `ui-tag--${tone}`, dot && "ui-tag--dot", strong && "ui-tag--strong", onRemove && "ui-tag--removable", className)}
      {...rest}
    >
      {children}
      {onRemove && (
        <button type="button" className="ui-tag__x" aria-label={removeLabel} onClick={onRemove}>
          <X className="ui-icon ui-icon--sm" />
        </button>
      )}
    </span>
  );
});

export const Badge = Tag;
