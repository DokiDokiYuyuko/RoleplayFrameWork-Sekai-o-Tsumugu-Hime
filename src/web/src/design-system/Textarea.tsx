import { forwardRef, useRef, type TextareaHTMLAttributes } from "react";
import { useAutoSizeTextarea } from "./useAutoSizeTextarea";
import { useFieldControl } from "./fieldContext";
import { cx } from "./internal/cx";
import { useMergedRef } from "./internal/useMergedRef";
import { useTextValue } from "./internal/useTextValue";
import "./primitives/textarea.css";

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

/** Multi-line input that grows with its text and shows no resize handle. Reads Field wiring from context. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, value, defaultValue, onInput, ...rest },
  ref,
) {
  const own = useRef<HTMLTextAreaElement>(null);
  const merged = useMergedRef(ref, own);
  const { text, track } = useTextValue(value, defaultValue);
  useAutoSizeTextarea(own, text);
  const wiring = useFieldControl({
    id: rest.id,
    "aria-describedby": rest["aria-describedby"],
    "aria-invalid": rest["aria-invalid"],
    "aria-required": rest["aria-required"],
  });
  return (
    <textarea
      ref={merged}
      className={cx("ui-textarea", className)}
      value={value}
      defaultValue={defaultValue}
      onInput={(event) => { track(event); onInput?.(event); }}
      {...rest}
      {...wiring}
    />
  );
});
