import type { ComponentType, ReactElement, ReactNode } from "react";
import { DropdownMenu } from "radix-ui";
import type { IconProps } from "./Icon";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/menu.css";

export interface MenuItemSpec {
  /** Stable key for the item. */
  id: string;
  label: ReactNode;
  icon?: ComponentType<IconProps>;
  onSelect?: (event: Event) => void;
  disabled?: boolean;
  /** Danger items are always placed last, after a separator. */
  tone?: "default" | "danger";
}

export interface MenuProps {
  /** Focusable element that opens the menu (Button, IconButton). It must accept a ref. */
  trigger: ReactElement;
  items: MenuItemSpec[];
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  className?: string;
}

/** Action menu. Keyboard, focus return and typeahead come from Radix; the danger-last rule is enforced here. */
export function Menu({ trigger, items, open, defaultOpen, onOpenChange, side = "bottom", align = "end", className }: MenuProps) {
  const regular = items.filter((item) => item.tone !== "danger");
  const danger = items.filter((item) => item.tone === "danger");
  const render = (item: MenuItemSpec) => {
    const Glyph = item.icon;
    return (
      <DropdownMenu.Item
        key={item.id}
        className={cx("ui-menu__item", item.tone === "danger" && "ui-menu__item--danger")}
        disabled={item.disabled}
        onSelect={item.onSelect}
      >
        {Glyph && <Glyph className="ui-icon" />}
        <span className="ui-menu__text">{item.label}</span>
      </DropdownMenu.Item>
    );
  };
  return (
    <DropdownMenu.Root open={open} defaultOpen={defaultOpen} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className={cx("ui-menu__content", className)} side={side} align={align} sideOffset={6} collisionPadding={12}>
          {regular.map(render)}
          {regular.length > 0 && danger.length > 0 && <DropdownMenu.Separator className="ui-menu__sep" />}
          {danger.map(render)}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
