import { useId, type HTMLAttributes, type ReactNode } from "react";
import { AlertCircle } from "./Icon";
import { FieldContext } from "./fieldContext";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/field.css";

export interface FieldProps extends Omit<HTMLAttributes<HTMLDivElement>, "children"> {
  label: ReactNode;
  hint?: ReactNode;
  /** Replaces the hint while set and marks the control invalid. */
  error?: ReactNode;
  required?: boolean;
  /** Shows the "optional" mark next to the label. */
  optional?: boolean;
  optionalText?: string;
  /** Id of the wrapped control; generated when omitted. */
  controlId?: string;
  /** For radio lists and Segmented, which have no single labelable element. */
  group?: boolean;
  children: ReactNode;
}

/** Label, optional/required mark, hint and error for one control. Its children read the wiring from context. */
export function Field({
  label, hint, error, required, optional, optionalText = "可选", controlId, group = false, className, children, ...rest
}: FieldProps) {
  const base = `ui-field-${useId()}`;
  const id = controlId ?? base;
  const labelId = `${base}-label`;
  const hintId = `${base}-hint`;
  const errorId = `${base}-error`;
  const hasError = Boolean(error);
  const describedBy = hasError ? errorId : hint ? hintId : undefined;
  const mark = (
    <>
      {label}
      {required && <span className="ui-field__req" aria-hidden="true">*</span>}
      {optional && !required && <span className="ui-field__opt">{optionalText}</span>}
    </>
  );
  return (
    <FieldContext.Provider value={{ id, describedBy, invalid: hasError, required }}>
      <div
        className={cx("ui-field", className)}
        {...(group ? { role: "group", "aria-labelledby": labelId, "aria-describedby": describedBy } : {})}
        {...rest}
      >
        {group
          ? <span className="ui-field__label" id={labelId}>{mark}</span>
          : <label className="ui-field__label" htmlFor={id}>{mark}</label>}
        {children}
        {hasError
          ? <p className="ui-field__error" id={errorId}><AlertCircle className="ui-icon ui-icon--sm" />{error}</p>
          : hint ? <p className="ui-field__hint" id={hintId}>{hint}</p> : null}
      </div>
    </FieldContext.Provider>
  );
}
