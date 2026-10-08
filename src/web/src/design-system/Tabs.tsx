import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { Tabs as RadixTabs } from "radix-ui";
import { cx } from "./internal/cx";
import "./primitives/tabs.css";

/** In-page sections with an underline indicator. Arrow keys, Home/End and roving focus come from Radix. */
export const Tabs = RadixTabs.Root;

type ListProps = Omit<ComponentPropsWithoutRef<typeof RadixTabs.List>, "aria-label" | "aria-labelledby"> &
  ({ "aria-label": string } | { "aria-labelledby": string });

/** The tab strip. It needs an accessible name. */
export const TabsList = forwardRef<ElementRef<typeof RadixTabs.List>, ListProps>(function TabsList({ className, ...rest }, ref) {
  return <RadixTabs.List ref={ref} className={cx("ui-tabs", className)} {...rest} />;
});

export const TabsTrigger = forwardRef<ElementRef<typeof RadixTabs.Trigger>, ComponentPropsWithoutRef<typeof RadixTabs.Trigger>>(
  function TabsTrigger({ className, ...rest }, ref) {
    return <RadixTabs.Trigger ref={ref} className={cx("ui-tab", className)} {...rest} />;
  },
);

export const TabsContent = forwardRef<ElementRef<typeof RadixTabs.Content>, ComponentPropsWithoutRef<typeof RadixTabs.Content>>(
  function TabsContent({ className, ...rest }, ref) {
    return <RadixTabs.Content ref={ref} className={cx("ui-tabs__panel", className)} {...rest} />;
  },
);
