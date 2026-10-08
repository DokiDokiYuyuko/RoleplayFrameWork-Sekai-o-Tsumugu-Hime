import { forwardRef, type HTMLAttributes } from "react";
import { Slot } from "radix-ui";
import { cx } from "./internal/cx";
import "./primitives/card.css";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Hover raises the card and focus shows a ring. Pair with `asChild` on a link or button, or give it your own role and tabIndex. */
  interactive?: boolean;
  /** Render the single child element (an `<a>`, `<button>`) instead of a `<div>`, passing the card's props to it. */
  asChild?: boolean;
}

/** Surface for a unit of content. Static by default. No ornament, no gold buttons. */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card({ interactive = false, asChild = false, className, ...rest }, ref) {
  const Comp = asChild ? Slot.Root : "div";
  return <Comp ref={ref} className={cx("ui-card", interactive && "ui-card--interactive", className)} {...rest} />;
});

/** Card heading. Choose the level that fits the page outline. */
export function CardTitle({ as: Tag = "h3", className, ...rest }: HTMLAttributes<HTMLHeadingElement> & { as?: "h2" | "h3" | "h4" }) {
  return <Tag className={cx("ui-card__title", className)} {...rest} />;
}
