import { forwardRef, useState, type HTMLAttributes } from "react";
import { PublicArt } from "./PublicArt";
import { useAppearanceAttribute } from "./internal/useAppearanceAttribute";
import { cx } from "./internal/cx";
import "./primitives/portrait.css";
export type PortraitTone = "iris" | "teal" | "rose" | "gold";
export interface PortraitProps extends HTMLAttributes<HTMLSpanElement> {
  name: string; src?: string; tone?: PortraitTone; ratio?: "portrait" | "wide" | "square"; label?: string;
  /** Disable only the placeholder initial; artwork/gradient fallback is retained. */
  showInitial?: boolean;
}
const plates = { iris: "silver-archive-room", teal: "teal-navigation-window", rose: "rose-greenhouse", gold: "champagne-orrery" };
export const Portrait = forwardRef<HTMLSpanElement, PortraitProps>(function Portrait({ name, src, tone = "iris", ratio = "portrait", label, showInitial = true, className, children, ...rest }, ref) {
  const [failed, setFailed] = useState<string | null>(null);
  const placeholder = useAppearanceAttribute("data-portrait-placeholder", "art");
  const image = !!src && failed !== src;
  return <span ref={ref} className={cx("ui-portrait", `ui-portrait--${ratio}`, className)} data-tone={tone} {...(label ? { role: "img", "aria-label": label } : {})} {...rest}>
    {image ? <img src={src} alt="" className="ui-portrait__image" onError={() => setFailed(src ?? null)} /> : <>
      {ratio === "portrait" && placeholder === "art" && <PublicArt path={`v3/cards/card-${plates[tone]}.webp`} className="ui-portrait__image" />}
      {showInitial && <span className="ui-portrait__initial" aria-hidden="true">{Array.from(name.trim())[0] ?? "◇"}</span>}
    </>}{children}
  </span>;
});
export const Cover = forwardRef<HTMLSpanElement, PortraitProps>(function Cover(props, ref) { return <Portrait {...props} ratio={props.ratio ?? "wide"} ref={ref} />; });
