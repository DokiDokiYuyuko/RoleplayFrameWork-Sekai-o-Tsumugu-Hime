import { forwardRef, type ReactNode } from "react";
import { RadioGroup } from "radix-ui";
import { cx } from "./internal/cx";
import "./primitives/segmented.css";

export interface SegmentedOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export type SegmentedProps = {
  options: SegmentedOption[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
  name?: string;
  className?: string;
} & ({ "aria-label": string } | { "aria-labelledby": string });

/** Pill group for a single choice (mode switches). A radio group: arrow keys move and select, Tab enters the checked option. */
export const Segmented = forwardRef<HTMLDivElement, SegmentedProps>(function Segmented(
  { options, value, defaultValue, onValueChange, size = "md", disabled, name, className, ...labelling },
  ref,
) {
  return (
    <RadioGroup.Root
      ref={ref}
      className={cx("ui-segmented", size !== "md" && `ui-segmented--${size}`, className)}
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
      name={name}
      orientation="horizontal"
      {...labelling}
    >
      {options.map((option) => (
        <RadioGroup.Item key={option.value} value={option.value} disabled={option.disabled} className="ui-segmented__btn">
          {option.label}
        </RadioGroup.Item>
      ))}
    </RadioGroup.Root>
  );
});
