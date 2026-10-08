import { forwardRef, type ComponentType, type CSSProperties, type InputHTMLAttributes, type ReactNode } from "react";
import type { IconProps } from "./Icon";
import { useFieldControl } from "./fieldContext";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/textinput.css";

export type TextInputSize = "sm" | "md" | "lg";

export interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size" | "className" | "style"> {
  size?: TextInputSize;
  /** `quiet` drops the outline until hover or focus (composer rows). */
  tone?: "default" | "quiet";
  /** Glyph before the text, e.g. Search. */
  leadingIcon?: ComponentType<IconProps>;
  /** Trailing slot for one small control, e.g. a reveal IconButton. */
  trailing?: ReactNode;
  /** `className` and `style` go on the outer box; every other attribute goes on the `<input>`. */
  className?: string;
  style?: CSSProperties;
}

/** Single-line input. Inside a Field it picks up id, aria-describedby, aria-invalid and aria-required. */
export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { size = "md", tone = "default", leadingIcon: Glyph, trailing, className, style, ...rest },
  ref,
) {
  const wiring = useFieldControl({
    id: rest.id,
    "aria-describedby": rest["aria-describedby"],
    "aria-invalid": rest["aria-invalid"],
    "aria-required": rest["aria-required"],
  });
  return (
    <div className={cx("ui-input", size !== "md" && `ui-input--${size}`, tone === "quiet" && "ui-input--quiet", className)} style={style}>
      {Glyph && <Glyph className="ui-icon" />}
      <input ref={ref} className="ui-input__el" {...rest} {...wiring} />
      {trailing && <span className="ui-input__trailing">{trailing}</span>}
    </div>
  );
});
