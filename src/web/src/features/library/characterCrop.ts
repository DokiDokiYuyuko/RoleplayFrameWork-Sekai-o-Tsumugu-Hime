export type CropKind = 'avatar' | 'portrait';
export const cropOutput = (kind: CropKind) => kind === 'avatar' ? { width: 512, height: 512 } : { width: 720, height: 1280 };
/** Normalized offsets allow the same selection to survive a responsive viewport change. */
export function cropGeometry(width: number, height: number, kind: CropKind, zoom = 1, x = 0, y = 0) {
  if (!(width > 0 && height > 0)) throw new Error('图片尺寸无效');
  const out = cropOutput(kind);
  const scale = Math.max(out.width / width, out.height / height) * Math.max(1, Math.min(4, zoom));
  const sw = out.width / scale, sh = out.height / scale;
  const limitX = (width - sw) / 2, limitY = (height - sh) / 2;
  return { sx: limitX - Math.max(-1, Math.min(1, x)) * limitX, sy: limitY - Math.max(-1, Math.min(1, y)) * limitY, sw, sh, ...out };
}
export async function renderCharacterCrop(image: HTMLImageElement, kind: CropKind, zoom: number, x: number, y: number): Promise<File> {
  const crop = cropGeometry(image.naturalWidth, image.naturalHeight, kind, zoom, x, y);
  const canvas = document.createElement('canvas');
  canvas.width = crop.width; canvas.height = crop.height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('当前浏览器无法裁剪图片，请换用支持画布的浏览器');
  ctx.drawImage(image, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, crop.width, crop.height);
  const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片裁剪失败，请重试')), 'image/png'));
  return new File([blob], `${kind}-crop.png`, { type: 'image/png' });
}
