import { forwardRef, type ComponentPropsWithoutRef, type ComponentType, type ElementType, type ReactElement, type ReactNode, type Ref } from "react";
import type { IconProps } from "./Icon";
import { Tooltip } from "./Tooltip";
import "./primitives/icon.css";
import "./primitives/navitem.css";

interface NavItemOwnProps<T extends ElementType> {
  /** Element or component to render: a router link, an anchor or a button. */
  as?: T;
  icon: ComponentType<IconProps>;
  /** Sets aria-current="page", which the primitive styles. */
  current?: boolean;
  /** Shown on hover and focus when the label is not visible (the icon rail). */
  tooltip?: string;
  children: ReactNode;
}

export type NavItemProps<T extends ElementType = "a"> = NavItemOwnProps<T> &
  Omit<ComponentPropsWithoutRef<T>, keyof NavItemOwnProps<T>>;

/** Internal signature; the exported NavItem re-types it per rendered element. */
const NavItemBase = forwardRef<Element, NavItemOwnProps<ElementType> & { className?: string }>(function NavItem(
  { as, icon: Glyph, current, tooltip, children, className, ...rest },
  ref,
) {
  const Component: ElementType = as ?? "a";
  const item = (
    <Component ref={ref} className={`ui-navitem${className ? ` ${className}` : ""}`} aria-current={current ? "page" : undefined} {...rest}>
      <Glyph className="ui-icon" />
      <span className="ui-navitem__text">{children}</span>
    </Component>
  );
  return tooltip ? <Tooltip label={tooltip} side="right">{item}</Tooltip> : item;
});

export const NavItem = NavItemBase as unknown as <T extends ElementType = "a">(
  props: NavItemProps<T> & { ref?: Ref<Element> },
) => ReactElement | null;
