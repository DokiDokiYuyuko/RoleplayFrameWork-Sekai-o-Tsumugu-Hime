import { PublicArt } from "../design-system/PublicArt";
import { useAppearanceAttribute } from "../design-system/internal/useAppearanceAttribute";
import "./moonweave-frame.css";
export function Ornament({ variant = "main", crest = false, tone = "iris" }: { variant?: "main" | "panel" | "dialog"; crest?: boolean; tone?: "iris" | "teal" | "rose" }) {
  const night = useAppearanceAttribute("data-color-scheme", "light") === "dark";
  return <span className="moonweave-ornament" data-variant={variant} aria-hidden="true">
    {(["tl", "tr", "bl", "br"] as const).map((corner) => <span key={corner} className={`moonweave-ornament__corner moonweave-ornament__corner--${corner}`}><PublicArt path={`frames/corner-${tone}${night ? "-night" : ""}-${corner}.webp`} /></span>)}
    {crest && <PublicArt path={`frames/${variant === "dialog" ? "dialog-crest" : `medallion-${tone}`}${night ? "-night" : ""}.webp`} className="moonweave-ornament__crest" />}
  </span>;
}
