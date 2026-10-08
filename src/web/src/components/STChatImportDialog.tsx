import { resourceQueries } from '../features/resources/resourceQueries';
import { WorkflowDialog } from "../design-system/WorkflowDialog";
import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { X } from 'lucide-react';
import { storyReviewApi } from '../features/stories/storyReviewApi';
import { useChatStore } from '../store/chatStore';

interface Preview { source_sha256: string; user_name: string; character_names: string[]; message_count: number; variant_count: number; warnings: string[]; degraded_fields: string[] }
export function STChatImportDialog({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [character, setCharacter] = useState('');
  const [player, setPlayer] = useState('');
  const [title, setTitle] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const epoch = useRef(0);
  const { data: characters = [] } = useQuery({ ...resourceQueries.characters(), });
  const navigate = useNavigate();
  const cache = useQueryClient();
  const choose = async (chosen: File | null) => {
    const request = ++epoch.current;
    setFile(chosen); setPreview(null); setAccepted(false); setError('');
    if (!chosen) return;
    setBusy(true);
    try {
      const result = await storyReviewApi.previewST<Preview>(chosen);
      if (request === epoch.current) { setPreview(result); setPlayer(result.user_name); setTitle(chosen.name.replace(/\.jsonl$/i, '')); }
    } catch (cause) { if (request === epoch.current) setError(cause instanceof Error ? cause.message : '预检失败'); }
    finally { if (request === epoch.current) setBusy(false); }
  };
  const commit = async () => {
    if (!file || !preview || !character || !accepted) return;
    setBusy(true); setError('');
    try {
      const result = await storyReviewApi.importST(file,
        { source_sha256: preview.source_sha256, character_id: character, user_name: player, title, acknowledge_degraded: 'true' });
      await Promise.allSettled([useChatStore.getState().loadSessions(), cache.invalidateQueries({ queryKey: ['stories'] })]);
      onClose(); navigate(`/stories/${encodeURIComponent(result.story_id)}/branches/${encodeURIComponent(result.branch_id)}`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '迁入失败'); }
    finally { setBusy(false); }
  };
  return <WorkflowDialog title="迁入酒馆聊天" onClose={onClose} centered>
    <section className="max-h-[90vh] w-full overflow-y-auto rounded-xl bg-[var(--surface,#fff)] p-5 shadow-xl">
    <header className="mb-4 flex justify-between"><div><h2 className="text-xl font-semibold">迁入酒馆聊天</h2><p className="mt-1 text-sm text-[var(--muted)]">读取 SillyTavern 单角色 JSONL，保留正文与可识别的候选。</p></div><button type="button" className="v7-icon-btn" aria-label="关闭聊天迁入" disabled={busy} onClick={onClose}><X size={18} /></button></header>
    <label className="v7-field mb-4">聊天文件<input type="file" accept=".jsonl" disabled={busy} onChange={(e) => void choose(e.target.files?.[0] ?? null)} /></label>
    {busy && !preview && <p role="status">正在预检…</p>}{error && <p role="alert" className="mb-3 text-sm text-[var(--danger)]">{error}</p>}
    {preview && <div className="space-y-4"><p className="text-sm">识别到 {preview.message_count} 条消息、{preview.variant_count} 个候选。人物：{preview.character_names.join('、')}</p>
      <label className="v7-field">映射到角色<select aria-label="映射到角色" value={character} onChange={(e) => setCharacter(e.target.value)}><option value="">选择已有角色卡</option>{characters.map((c) => <option key={c.id} value={c.id}>{c.card.name}</option>)}</select></label>
      <label className="v7-field">玩家称呼<input value={player} maxLength={120} onChange={(e) => setPlayer(e.target.value)} /></label><label className="v7-field">故事名称<input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
      <div className="rounded-xl border border-[var(--line)] bg-[var(--warning-soft)] p-4 text-sm text-[var(--ink)]"><strong>迁入前核对</strong>{[...preview.warnings, ...preview.degraded_fields].map((item, i) => <p key={i} className="mt-2">{item}</p>)}<p className="mt-2">外部历史没有本项目的场景状态、角色知情范围和生成依据；迁入不会补造这些记录。迁入完成后的新消息会按本项目规则记录。</p></div>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />我已核对上述变化，同意建立新故事。</label>
      <button type="button" className="v7-btn v7-btn-primary" disabled={busy || !character || !accepted || !player.trim() || !title.trim()} onClick={() => void commit()}>{busy ? '迁入中…' : '迁入为新故事'}</button>
    </div>}
  </section></WorkflowDialog>;
}
