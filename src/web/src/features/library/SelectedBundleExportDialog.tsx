import { useEffect, useState } from 'react';
import { Dialog } from 'radix-ui';
import { Ornament } from '../../appearance/Ornament';
import { previewSelectedBundle, downloadSelectedBundle, type SelectedAssetRef, type SelectedBundlePreview } from './libraryApi';
import './library-tools.css';
export function SelectedBundleExportDialog({ characters, lorebooks, onClose }: { characters: SelectedAssetRef[]; lorebooks: SelectedAssetRef[]; onClose: () => void }) {
  const [includeBound, setIncludeBound] = useState(true);
  const [preview, setPreview] = useState<SelectedBundlePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [saved, setSaved] = useState(false);
  const selectionKey = JSON.stringify({ characters, lorebooks });
  useEffect(() => {
    let live = true; setLoading(true); setError(''); setPreview(null); setSaved(false);
    const input = JSON.parse(selectionKey) as { characters: SelectedAssetRef[]; lorebooks: SelectedAssetRef[] };
    void previewSelectedBundle({ ...input, include_bound_lorebooks: includeBound }).then((value) => { if (live) setPreview(value); })
      .catch((cause) => { if (live) setError(cause instanceof Error ? cause.message : '预览失败，请重试。'); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [selectionKey, includeBound, retry]);
  const save = async () => {
    if (!preview || loading || busy) return;
    setBusy(true); setError(''); setSaved(false);
    try { await downloadSelectedBundle(preview); setSaved(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '导出失败，请重试。'); }
    finally { setBusy(false); }
  };
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="v7-dialog-overlay" /><Dialog.Content className="v7-dialog-content asset-export-dialog moonweave-frame moonweave-frame--dialog">
      <Ornament variant="dialog" crest />
      <Dialog.Title>导出所选素材</Dialog.Title><Dialog.Description>这是对外分享的副本。核对素材、依赖与排除范围后下载，本地资料不变。</Dialog.Description>
      {characters.length > 0 && <label className="asset-selection-check"><input type="checkbox" checked={includeBound} disabled={busy}
        onChange={(event) => setIncludeBound(event.target.checked)} />附带角色绑定的世界书</label>}
      {!includeBound && characters.length > 0 && <p className="asset-export-scope">未随包导出的世界书绑定会从包内角色副本移除，原角色不变。</p>}
      {loading && <p role="status">正在核对所选素材…</p>}
      {preview && <><ul>{preview.items.map((item) => <li key={`${item.kind}:${item.id}`}>
        {item.kind === 'character' ? '角色' : '世界书'} · {item.title}{item.dependency ? '（角色依赖）' : ''}
      </li>)}</ul><p>共 {preview.items.length} 项；头像 {preview.avatars} 张。</p>
        <div className="asset-export-scope">{preview.notices.map((notice) => <p key={notice}>{notice}</p>)}</div></>}
      {error && <p role="alert" className="v7-error">{error}</p>}
      {saved && <p role="status">下载已开始，所选素材与选择状态仍保留。</p>}
      <div className="v7-dialog-actions">
        {error && <button type="button" className="v7-btn v7-btn-soft" disabled={busy} onClick={() => setRetry((value) => value + 1)}>重新预览</button>}
        <button type="button" className="v7-btn v7-btn-soft" disabled={busy} onClick={onClose}>关闭</button>
        <button type="button" className="v7-btn v7-btn-primary" aria-busy={busy} disabled={!preview || loading || busy} onClick={() => void save()}>{busy ? '打包中…' : '下载迁移包'}</button>
      </div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
