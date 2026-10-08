import { forwardRef, type InputHTMLAttributes } from "react";
import { cx } from "./internal/cx";
import type { NamedControl } from "./internal/named";
import "./primitives/switch.css";

export type SwitchProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "role" | "className" | "size"> &
  NamedControl & {
    /** Applies to the clickable label wrapper; other attributes go on the `<input>`. */
    className?: string;
  };

/** On/off setting: a native checkbox with `role="switch"`. Controlled or uncontrolled via `checked` / `defaultChecked`. */
export const Switch = forwardRef<HTMLInputElement, SwitchProps>(function Switch({ label, className, ...rest }, ref) {
  return (
    <label className={cx("ui-switch", className)}>
      <input ref={ref} type="checkbox" role="switch" className="ui-switch__input" {...rest} />
      <span className="ui-switch__track" aria-hidden="true" />
      {label != null && <span className="ui-switch__label">{label}</span>}
    </label>
  );
});
