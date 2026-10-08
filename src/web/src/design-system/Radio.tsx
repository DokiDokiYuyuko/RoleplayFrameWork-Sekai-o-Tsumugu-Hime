import { forwardRef, type InputHTMLAttributes } from "react";
import { cx } from "./internal/cx";
import type { NamedControl } from "./internal/named";
import "./primitives/radio.css";

export type RadioProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "className" | "size"> &
  NamedControl & {
    /** Applies to the clickable label wrapper; other attributes go on the `<input>`. */
    className?: string;
  };

/** Native radio. Group by sharing `name`; wrap a group in `role="radiogroup"` with a label (or a Field with `group`). */
export const Radio = forwardRef<HTMLInputElement, RadioProps>(function Radio({ label, className, ...rest }, ref) {
  return (
    <label className={cx("ui-radio", className)}>
      <input ref={ref} type="radio" className="ui-radio__input" {...rest} />
      <span className="ui-radio__dot" aria-hidden="true" />
      {label != null && <span className="ui-radio__label">{label}</span>}
    </label>
  );
});
