import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "./internal/cx";
import "./primitives/divider.css";

export interface DividerProps extends Omit<HTMLAttributes<HTMLElement>, "children"> {
  /** Centred text between fading rules, e.g. a scene change. Without it the divider is a plain rule. */
  label?: ReactNode;
}

/** Plain rule, or a labelled separator that fades out toward both edges. */
export function Divider({ label, className, ...rest }: DividerProps) {
  if (label == null) return <hr className={cx("ui-divider", className)} {...rest} />;
  return (
    <div role="separator" className={cx("ui-divider ui-divider--label", className)} {...rest}>
      <span className="ui-divider__text">{label}</span>
    </div>
  );
}
