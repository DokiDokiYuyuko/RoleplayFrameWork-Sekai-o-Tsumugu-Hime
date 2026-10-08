/** Bounded canvas effects: animation stops as soon as the last particle fades. */
export type Point = { x: number; y: number; time: number };
export type Pulse = Point & { seed: number };
export type EffectColors = {
  accent: string;
  primary: string;
  ink: string;
  secondary: string;
};
type Art = {
  ctx: CanvasRenderingContext2D;
  now: number;
  intensity: number;
  colors: EffectColors;
};
type TrailArt = Art & { points: Point[] };
type ClickArt = Art & { pulse: Pulse; progress: number };
export const TRAIL_LIFETIME = 650;
export const CLICK_LIFETIME = 850;
export const MAX_POINTS = 80;
export const MAX_PULSES = 8;

function diamond(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x, y - r * 1.6);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r * 1.6);
  ctx.lineTo(x - r, y);
  ctx.closePath();
  ctx.fill();
}
function ring(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0, r), 0, Math.PI * 2);
  ctx.stroke();
}
function petal(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  angle: number,
) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.ellipse(0, 0, r * 0.55, r, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}
const fade = (point: Point, now: number) =>
  Math.max(0, 1 - (now - point.time) / TRAIL_LIFETIME);

export const trailRenderers: Record<string, (art: TrailArt) => void> = {
  iridescent({ ctx, points, now, intensity }) {
    if (points.length < 2) return;
    const a = points[0],
      b = points[points.length - 1];
    const gradient = ctx.createLinearGradient(a.x, a.y, b.x + 0.01, b.y + 0.01);
    [
      "#af9be5",
      "#82b6ed",
      "#84d2d4",
      "#b6daae",
      "#f1dba3",
      "#edafc5",
      "#c8b5eb",
    ].forEach((c, i) => gradient.addColorStop(i / 6, c));
    ctx.strokeStyle = gradient;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (let ribbon = -1; ribbon <= 1; ribbon++) {
      ctx.lineWidth = ribbon === 0 ? 1.8 : 0.7;
      for (let i = 1; i < points.length; i++) {
        const p = points[i - 1],
          q = points[i],
          offset = Math.sin(i * 0.25 + ribbon) * ribbon * 2.2;
        ctx.globalAlpha =
          fade(p, now) * (0.35 + intensity * 0.38) * (ribbon === 0 ? 1 : 0.55);
        ctx.beginPath();
        ctx.moveTo(p.x, p.y + offset);
        ctx.lineTo(q.x, q.y + offset);
        ctx.stroke();
      }
    }
  },
  starlight({ ctx, points, now, intensity, colors }) {
    ctx.fillStyle = colors.accent;
    points.forEach((p, i) => {
      if (i % 5) return;
      const f = fade(p, now);
      ctx.globalAlpha = f * (0.4 + intensity * 0.5);
      diamond(ctx, p.x, p.y, (1 + intensity) * f);
    });
  },
  comet({ ctx, points, now, intensity, colors }) {
    ctx.strokeStyle = colors.primary;
    ctx.lineCap = "round";
    points.forEach((p, i) => {
      if (!i) return;
      const q = points[i - 1],
        f = fade(p, now);
      ctx.globalAlpha = f * 0.65 * intensity;
      ctx.lineWidth = 0.5 + 2 * f;
      ctx.beginPath();
      ctx.moveTo(q.x, q.y);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    });
    const end = points[points.length - 1];
    if (end) {
      ctx.fillStyle = colors.accent;
      ctx.globalAlpha = fade(end, now) * intensity;
      diamond(ctx, end.x, end.y, 2.8);
    }
  },
  petals({ ctx, points, now, intensity }) {
    ctx.fillStyle = "#d58aa7";
    points.forEach((p, i) => {
      if (i % 8) return;
      const f = fade(p, now),
        age = (now - p.time) / 1000;
      ctx.globalAlpha = f * 0.8 * intensity;
      petal(
        ctx,
        p.x + Math.sin(i) * age * 14,
        p.y + age * 20,
        2.5 + intensity,
        i * 0.8 + age,
      );
    });
  },
  fireflies({ ctx, points, now, intensity, colors }) {
    ctx.fillStyle = colors.accent;
    points.forEach((p, i) => {
      if (i % 6) return;
      const f = fade(p, now),
        x = p.x + Math.sin(i + now / 300) * 4,
        y = p.y + Math.cos(i) * 4;
      ctx.globalAlpha = f * intensity * 0.14;
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = f * intensity * 0.8;
      ctx.beginPath();
      ctx.arc(x, y, 1.5, 0, Math.PI * 2);
      ctx.fill();
    });
  },
  aurora({ ctx, points, now, intensity, colors }) {
    [colors.primary, colors.secondary, "#78bfae"].forEach((color, j) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      points.forEach((p, i) => {
        if (!i) return;
        const q = points[i - 1];
        ctx.globalAlpha = fade(p, now) * intensity * 0.35;
        const wave = (k: number) => Math.sin(k * 0.2 + j) * 4;
        ctx.beginPath();
        ctx.moveTo(q.x, q.y + wave(i - 1));
        ctx.lineTo(p.x, p.y + wave(i));
        ctx.stroke();
      });
    });
  },
  ink({ ctx, points, now, intensity, colors }) {
    ctx.fillStyle = colors.ink;
    points.forEach((p, i) => {
      if (i % 4) return;
      const f = fade(p, now);
      ctx.globalAlpha = f * intensity * 0.4;
      ctx.beginPath();
      ctx.ellipse(p.x, p.y, 1 + f * 2, 1 + f, Math.sin(i), 0, Math.PI * 2);
      ctx.fill();
    });
  },
  orbit({ ctx, points, now, intensity, colors }) {
    ctx.strokeStyle = colors.secondary;
    ctx.lineWidth = 0.7;
    points.forEach((p, i) => {
      if (i % 10) return;
      const f = fade(p, now);
      ctx.globalAlpha = f * intensity * 0.6;
      ring(ctx, p.x, p.y, 2.5 + (1 - f) * 5);
      ctx.fillStyle = colors.accent;
      diamond(ctx, p.x + 4 * f, p.y - 4 * f, 1.2);
    });
  },
};

export const clickRenderers: Record<string, (art: ClickArt) => void> = {
  "star-ring"({ ctx, pulse: p, progress: t, intensity, colors }) {
    const r = 5 + Math.sin((t * Math.PI) / 2) * (22 + intensity * 14);
    ctx.strokeStyle = colors.accent;
    ctx.lineWidth = 1;
    ctx.globalAlpha = (1 - t) * intensity * 0.65;
    ring(ctx, p.x, p.y, r);
    ctx.fillStyle = colors.accent;
    for (let i = 0; i < 6; i++) {
      const a = (i * Math.PI) / 3 + p.seed * 0.4;
      ctx.globalAlpha = (1 - t) * intensity;
      diamond(ctx, p.x + Math.cos(a) * r, p.y + Math.sin(a) * r, (1 - t) * 2.5);
    }
  },
  ripple({ ctx, pulse: p, progress: t, intensity, colors }) {
    ctx.strokeStyle = colors.primary;
    ctx.lineWidth = 1.2;
    for (let i = 0; i < 2; i++) {
      const phase = Math.max(0, t - i * 0.18);
      if (!phase) continue;
      ctx.globalAlpha = Math.max(0, 1 - t - i * 0.2) * intensity * 0.6;
      ring(ctx, p.x, p.y, 4 + phase * 44);
    }
  },
  petals({ ctx, pulse: p, progress: t, intensity }) {
    ctx.fillStyle = "#d58aa7";
    ctx.globalAlpha = (1 - t) * intensity * 0.85;
    for (let i = 0; i < 8; i++) {
      const a = (i * Math.PI) / 4 + p.seed * 0.3,
        r = Math.sin((t * Math.PI) / 2) * 30;
      petal(
        ctx,
        p.x + Math.cos(a) * r,
        p.y + Math.sin(a) * r + t * t * 12,
        (1 - t) * 4 + 1,
        a + t,
      );
    }
  },
  rune({ ctx, pulse: p, progress: t, intensity, colors }) {
    const r = 10 + Math.sin((t * Math.PI) / 2) * 23;
    ctx.strokeStyle = colors.secondary;
    ctx.lineWidth = 0.9;
    ctx.globalAlpha = (1 - t) * intensity * 0.75;
    ring(ctx, p.x, p.y, r);
    ring(ctx, p.x, p.y, r * 0.73);
    ctx.beginPath();
    for (let i = 0; i <= 6; i++) {
      const a = (i * Math.PI) / 3 + t * 0.25,
        x = p.x + Math.cos(a) * r,
        y = p.y + Math.sin(a) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.fillStyle = colors.accent;
    for (let i = 0; i < 6; i++) {
      const a = (i * Math.PI) / 3 + t * 0.25;
      diamond(ctx, p.x + Math.cos(a) * r, p.y + Math.sin(a) * r, 1.7 * (1 - t));
    }
  },
  burst({ ctx, pulse: p, progress: t, intensity, colors }) {
    ctx.lineCap = "round";
    ctx.lineWidth = 1.5;
    for (let i = 0; i < 10; i++) {
      const a = (i * Math.PI) / 5 + p.seed * 0.5,
        r = 4 + t * 34;
      ctx.strokeStyle = i % 2 ? colors.primary : colors.accent;
      ctx.globalAlpha = (1 - t) * intensity * 0.8;
      ctx.beginPath();
      ctx.moveTo(p.x + Math.cos(a) * r, p.y + Math.sin(a) * r);
      ctx.lineTo(
        p.x + Math.cos(a) * (r + 5 * (1 - t)),
        p.y + Math.sin(a) * (r + 5 * (1 - t)),
      );
      ctx.stroke();
    }
  },
  ink({ ctx, pulse: p, progress: t, intensity, colors }) {
    ctx.fillStyle = colors.ink;
    ctx.globalAlpha = (1 - t) * intensity * 0.25;
    for (let i = 0; i < 7; i++) {
      const a = (i * Math.PI * 2) / 7 + p.seed * 0.6,
        r = t * (12 + (i % 3) * 4);
      ctx.beginPath();
      ctx.ellipse(
        p.x + Math.cos(a) * r,
        p.y + Math.sin(a) * r,
        (1 - t) * (3 + (i % 3)) + 0.5,
        (1 - t) * 2 + 0.5,
        a,
        0,
        Math.PI * 2,
      );
      ctx.fill();
    }
  },
};

type EngineOptions = {
  ctx: CanvasRenderingContext2D;
  trailId: string;
  clickId: string;
  intensity: number;
  colors: EffectColors;
  enabled: () => boolean;
  size: () => { width: number; height: number };
  now: () => number;
  requestFrame: (callback: FrameRequestCallback) => number;
  cancelFrame: (id: number) => void;
};
export function createPointerEngine(options: EngineOptions) {
  const { ctx, colors, enabled, size, now, requestFrame, cancelFrame } =
    options;
  const intensity = Number.isFinite(options.intensity)
    ? Math.max(0, Math.min(1, options.intensity))
    : 0;
  const trail = trailRenderers[options.trailId],
    click = clickRenderers[options.clickId];
  let points: Point[] = [],
    pulses: Pulse[] = [],
    frame = 0,
    disposed = false,
    seed = 0;
  const active = () => !disposed && enabled() && intensity > 0;
  const wipe = () => {
    const s = size();
    ctx.clearRect(0, 0, s.width, s.height);
  };
  const clear = () => {
    if (frame) cancelFrame(frame);
    frame = 0;
    points = [];
    pulses = [];
    wipe();
  };
  const paint = (time: number) => {
    frame = 0;
    if (!active()) {
      clear();
      return;
    }
    points = points.filter((p) => time - p.time < TRAIL_LIFETIME);
    pulses = pulses.filter((p) => time - p.time < CLICK_LIFETIME);
    wipe();
    ctx.save();
    try {
      trail?.({ ctx, points, now: time, intensity, colors });
      for (const pulse of pulses)
        click?.({
          ctx,
          pulse,
          progress: Math.max(0, (time - pulse.time) / CLICK_LIFETIME),
          now: time,
          intensity,
          colors,
        });
    } finally {
      ctx.restore();
    }
    if (points.length || pulses.length) frame = requestFrame(paint);
  };
  const schedule = () => {
    if (!frame) frame = requestFrame(paint);
  };
  return {
    move(x: number, y: number) {
      if (!active() || !trail || !Number.isFinite(x) || !Number.isFinite(y))
        return;
      const time = now(),
        last = points[points.length - 1];
      if (last && Math.hypot(last.x - x, last.y - y) <= 2) return;
      if (
        last &&
        (time - last.time > 180 || Math.hypot(last.x - x, last.y - y) > 130)
      )
        points = [];
      points.push({ x, y, time });
      points = points.slice(-MAX_POINTS);
      let length = 0;
      for (let i = points.length - 1; i > 0; i--) {
        length += Math.hypot(
          points[i].x - points[i - 1].x,
          points[i].y - points[i - 1].y,
        );
        if (length > 200) {
          points = points.slice(i);
          break;
        }
      }
      schedule();
    },
    click(x: number, y: number) {
      if (!active() || !click || !Number.isFinite(x) || !Number.isFinite(y))
        return;
      pulses.push({ x, y, time: now(), seed: ++seed % 17 });
      pulses = pulses.slice(-MAX_PULSES);
      schedule();
    },
    clear,
    dispose() {
      disposed = true;
      clear();
    },
    stats: () => ({
      points: points.length,
      pulses: pulses.length,
      scheduled: Boolean(frame),
    }),
  };
}
