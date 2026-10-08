import { cloneElement, type FocusEvent, type ReactElement } from "react";
import { Tooltip as RadixTooltip } from "radix-ui";
import "./primitives/tooltip.css";

/**
 * Names an icon-only or collapsed control on hover and keyboard focus. The trigger must accept a ref.
 * Focus the browser does not mark :focus-visible (a script moving focus after a mouse or touch
 * gesture, such as opening the drawer) does not open the tooltip.
 */
export function Tooltip({
  label,
  side = "bottom",
  children,
}: {
  label: string;
  side?: "top" | "right" | "bottom" | "left";
  children: ReactElement;
}) {
  const trigger = cloneElement(children, {
    onFocus: (event: FocusEvent<HTMLElement>) => {
      children.props.onFocus?.(event);
      // Radix skips its own focus handler when the event is default-prevented.
      if (!event.currentTarget.matches(":focus-visible")) event.preventDefault();
    },
  });
  return (
    <RadixTooltip.Provider delayDuration={300} skipDelayDuration={150}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{trigger}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content className="ui-tooltip" side={side} sideOffset={8}>
            {label}
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
