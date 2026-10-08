import { forwardRef, type HTMLAttributes } from "react";
import { Search } from "./Icon";
import { TextInput, type TextInputProps } from "./TextInput";
import { cx } from "./internal/cx";
import "./primitives/filterbar.css";

export type FilterBarProps = HTMLAttributes<HTMLDivElement>;

/**
 * One wrapping row of md-height controls. Put the search first and selects (with `prefix`, `width="auto"`)
 * after it. Role `search` by default; give it an `aria-label` such as "筛选角色".
 */
export const FilterBar = forwardRef<HTMLDivElement, FilterBarProps>(function FilterBar({ className, role = "search", ...rest }, ref) {
  return <div ref={ref} role={role} className={cx("ui-filterbar", className)} {...rest} />;
});

/** TextInput with the search glyph, sized to flex within a FilterBar. Needs `aria-label`. */
export const FilterBarSearch = forwardRef<HTMLInputElement, Omit<TextInputProps, "leadingIcon" | "type"> & { "aria-label": string }>(
  function FilterBarSearch({ className, ...rest }, ref) {
    return <TextInput ref={ref} type="search" leadingIcon={Search} autoComplete="off" className={cx("ui-filterbar__search", className)} {...rest} />;
  },
);

/** Pushes the controls after it to the right edge. */
export function FilterBarSpacer() {
  return <span className="ui-filterbar__spacer" aria-hidden="true" />;
}
