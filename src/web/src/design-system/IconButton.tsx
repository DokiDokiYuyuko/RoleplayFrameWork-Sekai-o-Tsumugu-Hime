import { forwardRef, type ButtonHTMLAttributes, type ComponentType } from "react";
import type { IconProps } from "./Icon";
import { buttonClass, type ButtonSize, type ButtonVariant } from "./Button";
import { Tooltip } from "./Tooltip";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label"> {
  /** Accessible name; also the tooltip text. */
  label: string;
  icon: ComponentType<IconProps>;
  variant?: ButtonVariant;
  size?: ButtonSize;
  tooltipSide?: "top" | "right" | "bottom" | "left";
}

/** Square, ghost by default. The label is required so no icon-only control ships unnamed. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon: Glyph, variant = "ghost", size, tooltipSide, className, type = "button", ...rest },
  ref,
) {
  return (
    <Tooltip label={label} side={tooltipSide}>
      <button ref={ref} type={type} className={buttonClass({ variant, size, icon: true, className })} aria-label={label} {...rest}>
        <Glyph className="ui-icon" />
      </button>
    </Tooltip>
  );
});
