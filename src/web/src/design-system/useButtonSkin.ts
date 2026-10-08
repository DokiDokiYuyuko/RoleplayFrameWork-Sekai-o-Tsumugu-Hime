import { useEffect, useState, type CSSProperties } from "react";
import { moonweaveAsset } from "../appearance/moonweaveAssets";
import { useAppearanceAttribute } from "./internal/useAppearanceAttribute";

/** Probe every slice before replacing the reliable CSS button. */
export function useButtonSkin(role: "primary" | "secondary" | undefined) {
  const scheme = useAppearanceAttribute("data-color-scheme", "light");
  const enabled = useAppearanceAttribute(`data-${role ?? "primary"}-button-skin`, role === "secondary" ? "false" : "true");
  const [ready, setReady] = useState("");
  const night = scheme === "dark";
  const key = `${role}:${scheme}`;
  const style: Record<string, string> = {};
  const urls: string[] = [];
  for (const state of ["normal", "hover", "pressed"]) {
    const effective = role === "primary" && night && state === "pressed" ? "hover" : state;
    const stem = `btn-${role}${effective === "normal" ? "" : `-${effective}`}${night ? "-night" : ""}`;
    for (const part of ["left", "center", "right"]) {
      const url = moonweaveAsset(`v3/controls/slices/${stem}-${part}.png`); urls.push(url);
      style[`--skin-${state}-${part}`] = `url("${url}")`;
    }
    const primary = role === "primary";
    const bodyStart = primary ? (!night && effective === "hover" ? 22 : 23) : night && effective !== "hover" ? 19 : 18;
    const bodySpan = primary ? 76 : night && effective !== "hover" ? 95 : night ? 97 : 96;
    const capStart = primary ? night ? effective === "hover" ? 15 : 18 : 17 : 0;
    const capSpan = primary ? night ? effective === "hover" ? 102 : 96 : 97 : 132;
    style[`--skin-${state}-body-h`] = `calc(var(--btn-h) * ${132 / bodySpan})`;
    style[`--skin-${state}-body-y`] = `calc(var(--btn-h) * ${-bodyStart / bodySpan})`;
    style[`--skin-${state}-cap-h`] = `calc(var(--btn-h) * ${132 / capSpan})`;
    style[`--skin-${state}-cap-y`] = `calc(var(--btn-h) * ${-capStart / capSpan})`;
    style[`--skin-${state}-cap-w`] = `calc(var(--btn-h) * ${120 / capSpan})`;
  }
  useEffect(() => {
    let active = true;
    if (!role) return;
    Promise.all(urls.map((src) => new Promise<boolean>((resolve) => { const image = new Image(); image.onload = () => resolve(true); image.onerror = () => resolve(false); image.src = src; }))).then((results) => { if (active) setReady(results.every(Boolean) ? key : ""); });
    return () => { active = false; };
  }, [key, role]);
  return { ready: !!role && enabled === "true" && ready === key, style: style as CSSProperties };
}
