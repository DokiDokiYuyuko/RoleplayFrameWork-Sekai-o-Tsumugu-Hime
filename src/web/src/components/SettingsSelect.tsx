import { Children, isValidElement, type ReactNode } from "react";
import { Select, type SelectProps } from "../design-system/Select";

const EMPTY = "__settings_empty_selection__";
type OptionProps = {
  value?: string | number;
  children?: ReactNode;
  disabled?: boolean;
};

/** Preserve the existing settings option lists while using the shared keyboard-accessible picker. */
export function SettingsSelect({
  children,
  value,
  onValueChange,
  ...props
}: Omit<SelectProps, "options"> & { children: ReactNode }) {
  const options = Children.toArray(children)
    .filter(isValidElement<OptionProps>)
    .map((child) => ({
      value: String(child.props.value ?? "") || EMPTY,
      label: child.props.children,
      disabled: child.props.disabled,
    }));
  return (
    <Select
      {...props}
      value={value === "" ? EMPTY : value}
      options={options}
      onValueChange={(next) => onValueChange?.(next === EMPTY ? "" : next)}
    />
  );
}
