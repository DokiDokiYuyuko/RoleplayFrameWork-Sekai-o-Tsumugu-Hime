import { useState } from 'react';
import type { Scene } from '../../types';
import { Button } from '../../design-system/Button';
import { SurfaceDialog } from '../../design-system/SurfaceDialog';
import { BUILTIN_SCENE_IMAGES, resolveSceneImage } from '../../appearance/moonweaveAssets';
import { storiesClient } from './storiesClient';
import { storyRuntime } from './storyRuntime';
import './scene-images.css';

/** Explicit selected artwork only; an unavailable selection preserves text and identity. */
export function SceneImage({ scene }: { scene: Scene }) {
  const src = resolveSceneImage(scene.builtin_image_id);
  const [failed, setFailed] = useState<string | null>(null);
  if (!src) return null;
  return failed === src ? <p className="scene-image-failure" role="status">所选场景图暂不可显示</p>
    : <img className="story-scene-image" src={src} alt={`${scene.title}的场景图`} decoding="async" onError={() => setFailed(src)} />;
}

/** The draft is local; only a durable command result changes the current scene. */
export function SceneImagePicker({ scene, branchId, revision, disabled = false }: {
  scene: Scene; branchId: string; revision: number; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(scene.builtin_image_id ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (busy) return;
    const ownsBranch = storyRuntime.capture(branchId);
    setBusy(true); setError(null);
    try {
      const committed = await storiesClient.patchSceneImage(branchId, scene.id, {
        builtin_image_id: draft, expected_branch_revision: Math.max(revision, storyRuntime.sceneImageRevision(branchId, scene.id)),
      });
      if (!ownsBranch()) return;
      if (!storyRuntime.applySceneImage(branchId, committed)) {
        setError('场景或世界线已更新，已保存的选择将在重新载入后显示。'); return;
      }
      setOpen(false);
    } catch (cause) {
      if (ownsBranch()) setError(cause instanceof Error ? cause.message : '场景图保存失败，请重试。');
    } finally { if (ownsBranch()) setBusy(false); }
  };
  return <>
    <Button variant="ghost" size="sm" disabled={disabled} onClick={() => { setDraft(scene.builtin_image_id ?? null); setError(null); setOpen(true); }}>选择场景图</Button>
    {open && <SurfaceDialog title="选择场景图" className="scene-image-picker" busy={busy} onClose={() => setOpen(false)}>
      <div className="scene-image-picker__head"><h2>选择场景图</h2><Button variant="ghost" disabled={busy} onClick={() => setOpen(false)} aria-label="关闭场景图库">关闭</Button></div>
      <p className="scene-image-picker__hint">为「{scene.title}」选择一张图。时间与天气由你选择，图片不会改变场景文字。</p>
      <div className="scene-image-picker__grid" role="group" aria-label="内置场景图库">
        <Button variant="ghost" className="scene-image-picker__choice" aria-pressed={draft === null} disabled={busy} onClick={() => setDraft(null)}><span className="scene-image-picker__empty">仅显示文字</span></Button>
        {BUILTIN_SCENE_IMAGES.map(image => <Button key={image.id} variant="ghost" className="scene-image-picker__choice" aria-pressed={draft === image.id} disabled={busy} onClick={() => setDraft(image.id)}>
          <img src={image.image_url} alt="" decoding="async" onError={event => { event.currentTarget.style.visibility = 'hidden'; }} /><span>{image.name}{image.variant ? ` · ${image.variant}` : ''}</span>
        </Button>)}
      </div>
      {error && <p role="alert" className="scene-image-picker__error">{error}</p>}
      <div className="scene-image-picker__foot"><Button variant="secondary" disabled={busy} onClick={() => setOpen(false)}>取消</Button><Button variant="primary" disabled={busy} loading={busy} onClick={() => void save()}>{error ? '重试保存' : '保存场景图'}</Button></div>
    </SurfaceDialog>}
  </>;
}
