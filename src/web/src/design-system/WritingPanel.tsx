import { forwardRef, useId, useRef, type ReactNode, type TextareaHTMLAttributes } from "react";
import { useAutoSizeTextarea } from "./useAutoSizeTextarea";
import { useFieldControl } from "./fieldContext";
import { cx } from "./internal/cx";
import { useMergedRef } from "./internal/useMergedRef";
import { useTextValue } from "./internal/useTextValue";
import "./primitives/writingpanel.css";

export interface WritingPanelProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "className" | "title"> {
  title?: ReactNode;
  /** Header controls, right-aligned (IconButtons). */
  tools?: ReactNode;
  footer?: ReactNode;
  /** Shows the non-whitespace character count in the footer. */
  showCount?: boolean;
  /** Reading font and size, for story text and long manuscripts. */
  reading?: boolean;
  /** Applies to the outer panel. */
  className?: string;
}

/** One bordered panel around a borderless textarea; the focus ring surrounds the whole panel. */
export const WritingPanel = forwardRef<HTMLTextAreaElement, WritingPanelProps>(function WritingPanel(
  { title, tools, footer, showCount = false, reading = false, className, value, defaultValue, onInput, ...rest },
  ref,
) {
  const titleId = `ui-writing-${useId()}`;
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
  const named = rest["aria-label"] || rest["aria-labelledby"] ? undefined : title ? titleId : undefined;
  return (
    <div className={cx("ui-writing", reading && "ui-writing--reading", className)}>
      {(title || tools) && (
        <div className={cx("ui-writing__head", !title && "ui-writing__head--tools")}>
          {title && <h3 className="ui-writing__title" id={titleId}>{title}</h3>}
          {tools}
        </div>
      )}
      <textarea
        ref={merged}
        className="ui-writing__area"
        value={value}
        defaultValue={defaultValue}
        aria-labelledby={named}
        onInput={(event) => { track(event); onInput?.(event); }}
        {...rest}
        {...wiring}
      />
      {(footer || showCount) && (
        <div className="ui-writing__foot">
          {footer}
          {showCount && <span className="ui-writing__count">{Array.from(text.replace(/\s/g, "")).length} 字</span>}
        </div>
      )}
    </div>
  );
});
