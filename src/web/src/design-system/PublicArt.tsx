import { useState, type ImgHTMLAttributes } from "react";
import { moonweaveAsset } from "../appearance/moonweaveAssets";

/** Public artwork only. Failed art disappears while the host retains its CSS fallback. */
export function PublicArt({ path, ...props }: Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & { path: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  if (failed === path) return null;
  return <img {...props} src={moonweaveAsset(path)} alt={props.alt ?? ""} draggable={false} decoding="async" onError={(event) => { setFailed(path); props.onError?.(event); }} />;
}
