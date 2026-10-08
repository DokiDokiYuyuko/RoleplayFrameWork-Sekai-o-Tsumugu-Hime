import { PublicArt } from '../design-system/PublicArt';
import { useState, type ReactNode, type CSSProperties } from 'react';
import { useAppearanceStore } from './store';
import { resolveBubbleStyle, type BubbleStyle } from './bubbleStyles';

function Ornament({ kind }: { kind: BubbleStyle['ornament'] }) {
  if (kind === 'none') return null;
  return <span className="bubble-frame__ornament" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.1">
    {kind === 'star' && <><path d="m12 2 2.3 7.7L22 12l-7.7 2.3L12 22l-2.3-7.7L2 12l7.7-2.3Z"/><circle cx="12" cy="12" r="2"/></>}
    {kind === 'page' && <><path d="M5 3h12l3 3v15H5Z M17 3v5h3 M8 10h8 M8 14h6"/><path d="M3 6v15"/></>}
    {kind === 'crystal' && <><path d="m12 2 8 9-8 11-8-11Z M4 11h16 M12 2l3 9-3 11-3-11Z"/></>}
    {kind === 'moon' && <><path d="M17 3A9 9 0 1 0 21 16 8 8 0 0 1 17 3Z"/><path d="m6 3 1 2 2 1-2 1-1 2-1-2-2-1 2-1Z"/></>}
  </svg></span>;
}

const bubbleArt: Record<string, { width: number; height: number; endWidth: number; endHeight: number }> = {
  'star-track': { width: 40, height: 39.3, endWidth: 96, endHeight: 26.25 },
  'book-note': { width: 40, height: 36.72, endWidth: 60.83, endHeight: 24 },
  crystal: { width: 34.38, height: 40, endWidth: 64, endHeight: 17.5 },
  moonlight: { width: 39.49, height: 40, endWidth: 37.81, endHeight: 24 },
};
const measuredCornerShift = new Map<string, number>();

/** Visual frame only: story identity, visibility and plain-chat operations remain in their own renderers. */
export function BubbleFrame({ children, speaker = 'npc', styleId, className = '' }: {
  children: ReactNode; speaker?: 'player' | 'npc' | 'inner' | 'scene'; styleId?: string; className?: string;
}) {
  const preference = useAppearanceStore((state) => state.preferences.bubble_style_id);
  const style = resolveBubbleStyle(styleId ?? preference);
  const [corner, setCorner] = useState<{ id: string; shift: number } | null>(null);
  const art = speaker === 'npc' ? bubbleArt[style.id] : undefined;
  const geometry = art ? {
    '--bubble-cw': `${art.width}px`, '--bubble-ch': `${art.height}px`,
    '--bubble-ew': `${art.endWidth}px`, '--bubble-eh': `${art.endHeight}px`,
    '--bubble-corner-shift': `${corner?.id === style.id ? corner.shift : measuredCornerShift.get(style.id) ?? 0}px`,
  } as CSSProperties : undefined;
  return <div className={`bubble-frame ${className}`} data-bubble-style={style.id} data-speaker={speaker} style={geometry}>
    <Ornament kind={style.ornament} />
    {art && <><PublicArt className="bubble-frame__corner-art" path={`v3/bubbles/bubble-${style.id}-corner.webp`} onLoad={(event) => {
      let shift = measuredCornerShift.get(style.id);
      if (shift === undefined) {
        const image = event.currentTarget;
        const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        try {
          ctx.drawImage(image, 0, 0);
          const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
          let depth = -Infinity;
          for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
            if (pixels[(y * canvas.width + x) * 4 + 3] < 16) continue;
            depth = Math.max(depth, Math.min(art.width / 2 - (x + .5) * art.width / canvas.width, (y + .5) * art.height / canvas.height - art.height / 2));
          }
          if (!Number.isFinite(depth)) return;
          shift = depth - 8; measuredCornerShift.set(style.id, shift);
        } catch { return; }
      }
      setCorner({ id: style.id, shift });
    }} /><PublicArt className="bubble-frame__end-art" path={`v3/bubbles/bubble-${style.id}-end.webp`} /></>}
    <div className="bubble-frame__content">{children}</div>
  </div>;
}
