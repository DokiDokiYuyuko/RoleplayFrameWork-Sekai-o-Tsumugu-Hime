import { useEffect, useRef } from "react";
import { useAppearanceStore } from "./store";
import {
  TRAIL_OPTIONS,
  CLICK_EFFECT_OPTIONS,
  resolveAppearanceOption,
} from "./registry";
import { createPointerEngine } from "./pointerArt";

export function PointerEffects() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { trail_id, click_effect_id, effect_intensity, theme_id } =
    useAppearanceStore((state) => state.preferences);
  useEffect(() => {
    const canvas = canvasRef.current,
      ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const style = getComputedStyle(document.documentElement);
    const color = (name: string, fallback: string) =>
      style.getPropertyValue(name).trim() || fallback;
    const engine = createPointerEngine({
      ctx,
      trailId: resolveAppearanceOption(TRAIL_OPTIONS, trail_id, "iridescent")
        .id,
      clickId: resolveAppearanceOption(
        CLICK_EFFECT_OPTIONS,
        click_effect_id,
        "none",
      ).id,
      intensity: effect_intensity,
      colors: {
        accent: color("--gold", "#ac854e"),
        primary: color("--sea", "#365da8"),
        ink: color("--ink", "#263552"),
        secondary: color("--lilac", "#9381c4"),
      },
      enabled: () => fine.matches && !reduced.matches && !document.hidden,
      size: () => ({ width: window.innerWidth, height: window.innerHeight }),
      now: () => performance.now(),
      requestFrame: requestAnimationFrame,
      cancelFrame: cancelAnimationFrame,
    });
    const clear = () => engine.clear();
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.ceil(window.innerWidth * ratio);
      canvas.height = Math.ceil(window.innerHeight * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      clear();
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== "touch")
        engine.move(event.clientX, event.clientY);
    };
    const click = (event: PointerEvent) => {
      if (
        event.pointerType !== "touch" &&
        event.button === 0 &&
        event.isPrimary !== false
      )
        engine.click(event.clientX, event.clientY);
    };
    const exit = (event: PointerEvent) => {
      if (!event.relatedTarget) clear();
    };
    resize();
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("pointerdown", click, { passive: true });
    window.addEventListener("pointerout", exit, { passive: true });
    window.addEventListener("resize", resize);
    window.addEventListener("blur", clear);
    document.addEventListener("visibilitychange", clear);
    fine.addEventListener("change", clear);
    reduced.addEventListener("change", clear);
    return () => {
      engine.dispose();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerdown", click);
      window.removeEventListener("pointerout", exit);
      window.removeEventListener("resize", resize);
      window.removeEventListener("blur", clear);
      document.removeEventListener("visibilitychange", clear);
      fine.removeEventListener("change", clear);
      reduced.removeEventListener("change", clear);
    };
  }, [trail_id, click_effect_id, effect_intensity, theme_id]);
  return (
    <canvas ref={canvasRef} className="pointer-effects" aria-hidden="true" />
  );
}
