import { forwardRef, useState, type HTMLAttributes } from "react";
import { moonweaveAsset } from "../appearance/moonweaveAssets";
import { useAppearanceAttribute } from "./internal/useAppearanceAttribute";
import { cx } from "./internal/cx";
import "./primitives/avatar.css";
export type AvatarSize = 24 | 32 | 40 | 56 | 72;
export interface AvatarProps extends Omit<HTMLAttributes<HTMLSpanElement>, "children"> {
  name: string; src?: string; size?: AvatarSize; label?: string;
  /** Explicit frame overrides the global preference. Dense lists always use a thin ring. */
  frame?: string; dense?: boolean;
}
const frames: Record<string, string> = {
  "moon-silver": "v3/avatar-frames-v1-fixed/moon-silver.png", "iris-wreath": "avatar-frames/iris-wreath.png",
  "gilt-enamel": "avatar-frames/gilt-enamel.png", starburst: "avatar-frames/starburst.png",
  "tide-teal": "v3/avatar-frames-v1-fixed/tide-teal.png", "rose-vine": "v3/avatar-frames-v1-fixed/rose-vine.png",
  "silver-feather": "v3/avatar-frames/avatar-silver-feather-512.png", "vine-moon": "v3/avatar-frames/avatar-vine-moon-512.png",
  "frost-crystal": "v3/avatar-frames/avatar-frost-crystal-512.png", "woven-knot": "v3/avatar-frames/avatar-woven-knot-512.png",
};
export const Avatar = forwardRef<HTMLSpanElement, AvatarProps>(function Avatar({ name, src, size = 40, label, frame, dense = false, className, ...rest }, ref) {
  const globalFrame = useAppearanceAttribute("data-avatar-frame", "iris-wreath");
  const [failed, setFailed] = useState<string | null>(null);
  const [failedFrame, setFailedFrame] = useState<string | null>(null);
  const selected = frame ?? globalFrame;
  const path = !dense && size >= 32 ? frames[selected] : undefined;
  const showFrame = !!path && failedFrame !== path;
  return <span ref={ref} className={cx("ui-avatar", `ui-avatar--${size}`, className)} data-frame={showFrame ? "art" : selected === "none" && !dense ? "none" : "ring"} {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })} {...rest}>
    <span className="ui-avatar__face">{src && failed !== src ? <img className="ui-avatar__img" src={src} alt="" onError={() => setFailed(src)} /> : Array.from(name.trim())[0] ?? ""}</span>
    {showFrame && <img className="ui-avatar__frame" src={moonweaveAsset(path)} alt="" draggable={false} onError={() => setFailedFrame(path)} />}
  </span>;
});
