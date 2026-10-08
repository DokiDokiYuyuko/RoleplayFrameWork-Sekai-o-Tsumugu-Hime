import "./page-art.css";
import { PublicArt } from "../design-system/PublicArt";

export type PageArtKind = "story" | "characters" | "character-editor" | "crest" | "worldline" | "memory" | "connections" | "voice" | "unselected-route" | "unfinished-manuscript";

const art: Record<PageArtKind, { file: string; width: number; height: number }> = {
  worldline: { file: "v3/pageart/page-worldline.png", width: 320, height: 200 },
  memory: { file: "v3/pageart/page-memory.png", width: 320, height: 200 },
  connections: { file: "v3/pageart/page-connections.png", width: 320, height: 200 },
  voice: { file: "v3/pageart/page-voice.png", width: 320, height: 200 },
  "unselected-route": { file: "v3/pageart/empty-unselected-route.png", width: 320, height: 200 },
  "unfinished-manuscript": { file: "v3/pageart/empty-unfinished-manuscript.png", width: 320, height: 200 },
  story: { file: "picturebook/moon-bridge-v1.png", width: 640, height: 360 },
  characters: { file: "moonweave/v1/characters-enchanted-library.webp", width: 640, height: 400 },
  "character-editor": { file: "picturebook/editor-character-v1.png", width: 240, height: 240 },
  crest: { file: "moonweave/v1/moonweave-crest.webp", width: 192, height: 192 },
};

/** Public, decorative picture-book art; never reads character or story media. */
export function PageArt({ kind, className = "" }: { kind: PageArtKind; className?: string }) {
  const asset = art[kind];
  return (
    <span className={`page-art page-art--${kind} ${className}`.trim()} aria-hidden="true">
      {asset.file.startsWith("v3/") ? <PublicArt path={asset.file} className="page-art__image" width={asset.width} height={asset.height} loading="lazy" /> : <img
        key={kind}
        className="page-art__image"
        src={`${import.meta.env.BASE_URL}brand/${asset.file}`}
        width={asset.width}
        height={asset.height}
        alt=""
        loading="lazy"
        decoding="async"
        draggable={false}
        onError={(event) => {
          const img = event.currentTarget;
          if (asset.file.endsWith(".webp") && !img.dataset.fallback) {
            img.dataset.fallback = "png";
            img.src = `${import.meta.env.BASE_URL}brand/${asset.file.replace(/\.webp$/, ".png")}`;
          } else img.hidden = true;
        }}
      />}
    </span>
  );
}
