import { forwardRef, type ButtonHTMLAttributes, type ComponentType } from "react";
import { LoaderCircle, type IconProps } from "./Icon";
import "./primitives/icon.css";
import "./primitives/button.css";
import { useButtonSkin } from "./useButtonSkin";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "tonal";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Leading glyph from design-system/Icon. */
  icon?: ComponentType<IconProps>;
  /** Keeps the width, hides the label and sets aria-busy; the caller still blocks the operation. */
  loading?: boolean;
  block?: boolean;
  skin?: boolean | "primary" | "secondary";
}

export function buttonClass({ variant = "secondary", size = "md", block = false, icon = false, className = "" }: { variant?: ButtonVariant; size?: ButtonSize; block?: boolean; icon?: boolean; className?: string }): string {
  return [
    "ui-btn",
    `ui-btn--${variant}`,
    size !== "md" && `ui-btn--${size}`,
    icon && "ui-btn--icon",
    block && "ui-btn--block",
    className,
  ].filter(Boolean).join(" ");
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, icon: Glyph, loading = false, block, skin, style, className, children, type = "button", ...rest },
  ref,
) {
  const skinRole = skin === true ? (variant === "primary" ? "primary" : "secondary") : skin || undefined;
  const artwork = useButtonSkin(skinRole);
  return (
    <button ref={ref} type={type} className={buttonClass({ variant, size, block, className })} data-skin={artwork.ready ? skinRole : undefined} style={{ ...artwork.style, ...style }} aria-busy={loading || undefined} {...rest}>
      {skinRole && <span className="ui-btn__skin" aria-hidden="true"><i className="ui-btn__skin-body" /><i className="ui-btn__skin-left" /><i className="ui-btn__skin-right" /></span>}
      {Glyph && <Glyph className="ui-icon" />}
      <span className="ui-btn__label">{children}</span>
      {loading && <LoaderCircle className="ui-icon ui-btn__spin" />}
    </button>
  );
});
