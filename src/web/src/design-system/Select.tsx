import { forwardRef, type ReactNode } from "react";
import { Select as RadixSelect } from "radix-ui";
import { Check, ChevronDown } from "./Icon";
import { useFieldControl } from "./fieldContext";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/textinput.css";
import "./primitives/menu.css";
import "./primitives/select.css";

export interface SelectOption {
  /** Must not be an empty string (Radix reserves it for "no selection"). */
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export interface SelectProps {
  options: SelectOption[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  /** Filter-bar form: shows "prefix · value" in the trigger. */
  prefix?: string;
  size?: "sm" | "md" | "lg";
  tone?: "default" | "quiet";
  /** `auto` sizes the trigger to its content (filter bars); `full` fills the container. */
  width?: "full" | "auto";
  disabled?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean | "true" | "false";
}

/** Single-choice dropdown (Radix Select). The trigger is the same box as TextInput. Forwards its ref to the trigger. */
export const Select = forwardRef<HTMLButtonElement, SelectProps>(function Select(
  {
    options, value, defaultValue, onValueChange, placeholder, prefix, size = "md", tone = "default", width = "full",
    disabled, required, name, open, onOpenChange, className, id, ...aria
  },
  ref,
) {
  const wiring = useFieldControl({
    id,
    "aria-describedby": aria["aria-describedby"],
    "aria-invalid": aria["aria-invalid"],
  });
  return (
    <RadixSelect.Root
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
      required={required}
      name={name}
      open={open}
      onOpenChange={onOpenChange}
    >
      <RadixSelect.Trigger
        ref={ref}
        className={cx(
          "ui-input", size !== "md" && `ui-input--${size}`, tone === "quiet" && "ui-input--quiet",
          "ui-select__trigger", width === "auto" && "ui-select__trigger--auto", className,
        )}
        {...aria}
        {...wiring}
      >
        {prefix && <span className="ui-select__prefix">{prefix} ·</span>}
        <span className="ui-select__value"><RadixSelect.Value placeholder={placeholder} /></span>
        <RadixSelect.Icon asChild><ChevronDown className="ui-icon ui-icon--sm ui-select__caret" /></RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="ui-menu__content ui-select__content" position="popper" sideOffset={6} collisionPadding={12}>
          <RadixSelect.Viewport>
            {options.map((option) => (
              <RadixSelect.Item key={option.value} value={option.value} disabled={option.disabled} className="ui-menu__item">
                <RadixSelect.ItemText><span className="ui-menu__text">{option.label}</span></RadixSelect.ItemText>
                <RadixSelect.ItemIndicator className="ui-menu__check"><Check className="ui-icon ui-icon--sm" /></RadixSelect.ItemIndicator>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
});
