import { useEffect, useRef, useState } from 'react';
import { SurfaceDialog } from '../design-system/SurfaceDialog';
import { Button } from '../design-system/Button';
import { cropGeometry, cropOutput, renderCharacterCrop, type CropKind } from '../features/library/characterCrop';
import './character-editor.css';

/** Selection is local until onSave resolves. A rejected upload retains both the old image and this crop. */
export function CharacterCropDialog({ source, kind, onCancel, onSave }: {
  source: File | string; kind: CropKind; onCancel: () => void; onSave: (file: File) => Promise<void>;
}) {
  const [url, setUrl] = useState('');
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [readAttempt, setReadAttempt] = useState(0);
  const image = useRef<HTMLImageElement>(null);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const size = cropOutput(kind);
  useEffect(() => {
    setReady(false); setError('');
    if (typeof source !== 'string' && source.size > 20 * 1024 * 1024) { setError('图片超过 20MB，请选择较小的文件'); return; }
    const next = typeof source === 'string' ? source : URL.createObjectURL(source);
    setUrl(next);
    return () => { if (typeof source !== 'string') URL.revokeObjectURL(next); };
  }, [source, readAttempt]);
  const adjust = (x: number, y: number) => setOffset({ x: Math.max(-1, Math.min(1, x)), y: Math.max(-1, Math.min(1, y)) });
  const save = async () => {
    if (!ready || busy || !image.current) return;
    setBusy(true); setError('');
    try { const file = await renderCharacterCrop(image.current, kind, zoom, offset.x, offset.y); await onSave(file); onCancel(); }
    catch (cause) { setError(`保存未完成：${cause instanceof Error ? cause.message : String(cause)}。选框已保留，可重试。`); }
    finally { setBusy(false); }
  };
  const geometry = ready && image.current ? cropGeometry(image.current.naturalWidth, image.current.naturalHeight, kind, zoom, offset.x, offset.y) : null;
  return <SurfaceDialog title={kind === 'avatar' ? '裁剪角色头像' : '裁剪角色画像'} className="character-crop-dialog" nested onClose={onCancel} busy={busy}>
    <div className="character-crop-surface">
      <h2>{kind === 'avatar' ? '裁剪角色头像' : '裁剪角色画像'}</h2>
      <p>拖动图片调整选框，滑块或滚轮缩放。方向键移动，+ / − 缩放。</p>
      <div className="character-crop-stage" data-kind={kind} tabIndex={0} role="group" aria-label="图片裁剪选框" style={{ aspectRatio: `${size.width} / ${size.height}` }}
        onPointerDown={event => { if (busy) return; event.currentTarget.setPointerCapture(event.pointerId); drag.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y }; }}
        onPointerMove={event => {
          if (!drag.current || !geometry || !image.current || busy) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const scaledWidth = image.current.naturalWidth * rect.width / geometry.sw;
          const scaledHeight = image.current.naturalHeight * rect.height / geometry.sh;
          adjust(drag.current.ox + (event.clientX - drag.current.x) / Math.max(1, (scaledWidth - rect.width) / 2), drag.current.oy + (event.clientY - drag.current.y) / Math.max(1, (scaledHeight - rect.height) / 2));
        }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
        onWheel={event => { if (!busy) setZoom(value => Math.max(1, Math.min(4, value - event.deltaY * .002))); }}
        onKeyDown={event => {
          if (busy) return;
          if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-'].includes(event.key)) event.preventDefault();
          const step = event.shiftKey ? .15 : .04;
          if (event.key === 'ArrowLeft') adjust(offset.x - step, offset.y);
          if (event.key === 'ArrowRight') adjust(offset.x + step, offset.y);
          if (event.key === 'ArrowUp') adjust(offset.x, offset.y - step);
          if (event.key === 'ArrowDown') adjust(offset.x, offset.y + step);
          if (event.key === '+' || event.key === '=') setZoom(value => Math.min(4, value + .1));
          if (event.key === '-') setZoom(value => Math.max(1, value - .1));
        }}>
        {url && <img key={`${url}:${readAttempt}`} ref={image} src={url} alt="裁剪中的角色图片" draggable={false}
          style={geometry && image.current ? { width: `${image.current.naturalWidth / geometry.sw * 100}%`, height: `${image.current.naturalHeight / geometry.sh * 100}%`, left: `${-geometry.sx / geometry.sw * 100}%`, top: `${-geometry.sy / geometry.sh * 100}%` } : undefined}
          onLoad={() => setReady(true)} onError={() => { setReady(false); setError('图片读取失败，请重试或选择其他图片'); }} />}
        <span className="character-crop-selection" aria-hidden="true" />
      </div>
      <label className="character-crop-zoom">缩放<input type="range" aria-label="裁剪缩放" min={1} max={4} step={.01} value={zoom} disabled={busy || !ready} onChange={event => setZoom(Number(event.target.value))} /></label>
      {ready && image.current && (image.current.naturalWidth < size.width || image.current.naturalHeight < size.height) && <p role="status">原图较小，输出会放大至 {size.width}×{size.height}，可能影响清晰度。</p>}
      {error && <p role="alert">{error}</p>}
      <div className="character-crop-actions"><Button disabled={busy} onClick={onCancel}>取消</Button><Button disabled={busy} onClick={() => { setZoom(1); setOffset({ x: 0, y: 0 }); if (!ready) setReadAttempt(value => value + 1); }}>重置</Button><Button variant="primary" disabled={busy || !ready} loading={busy} onClick={() => void save()}>{busy ? '保存中…' : '保存裁剪'}</Button></div>
    </div>
  </SurfaceDialog>;
}
