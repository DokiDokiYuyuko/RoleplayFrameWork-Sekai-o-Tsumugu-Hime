import { forwardRef, type InputHTMLAttributes } from "react";
import { Check } from "./Icon";
import { cx } from "./internal/cx";
import type { NamedControl } from "./internal/named";
import "./primitives/icon.css";
import "./primitives/checkbox.css";

export type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "className" | "size"> &
  NamedControl & {
    /** Applies to the clickable label wrapper; other attributes go on the `<input>`. */
    className?: string;
  };

/** Native checkbox with the system's box. Controlled or uncontrolled like `<input type="checkbox">`. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox({ label, className, ...rest }, ref) {
  return (
    <label className={cx("ui-check", className)}>
      <input ref={ref} type="checkbox" className="ui-check__input" {...rest} />
      <span className="ui-check__box" aria-hidden="true"><Check className="ui-icon ui-icon--sm" /></span>
      {label != null && <span className="ui-check__label">{label}</span>}
    </label>
  );
});
