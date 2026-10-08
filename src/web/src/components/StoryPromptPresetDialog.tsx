import { showCleanupPending } from '../features/stories/storyNotices';
import { queryClient } from '../queryClient';
import { storyQueries } from '../features/stories/storyQueries';
import type { StorySummary } from '../types';
import { SurfaceDialog } from '../design-system/SurfaceDialog';
import { resourceQueries } from '../features/resources/resourceQueries';
import { useRef, useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from '../store/chatStore';

export function StoryPromptPresetDialog({ storyId, currentId, onClose }: {
  storyId: string; currentId: string | null; onClose: () => void;
}) {
  const { data: presets = [] } = useQuery({ ...resourceQueries.promptPresets(), });
  const membership = useRef(queryClient.getQueryData<StorySummary[]>(storyQueries.stories().queryKey)?.find(item => item.story_id === storyId)?.membership_revision);
  const [selected, setSelected] = useState(currentId ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const apply = async () => {
    setBusy(true); setError('');
    try {
      const result = await api.setStoryPromptPreset(storyId, selected || null, membership.current);
      showCleanupPending(result);
      await Promise.allSettled([useChatStore.getState().loadSessions()]);
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '应用失败'); }
    finally { setBusy(false); }
  };
  return <SurfaceDialog title={"故事提示词方案"} className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/40 p-4" onClose={onClose} busy={busy} nested={false}>
    <section aria-label="故事提示词方案" className="v7-settings-card w-full max-w-lg bg-white p-6 shadow-xl">
      <h2 className="text-xl font-semibold">故事提示词方案</h2>
      <p className="mt-2 text-sm text-slate-500">应用到这段故事的所有路线，并为后续分叉保存方案快照。修改前会备份故事。</p>
      <label className="v7-field mt-4">选择方案<select value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">沿用全局默认</option>{presets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <p className="mt-2 text-sm"><Link to="/settings/prompt-presets" onClick={onClose}>编辑和导入提示词方案</Link></p>
      {error && <p className="v7-error" role="alert">{error}</p>}
      <div className="v7-settings-actions mt-5"><button type="button" className="v7-btn v7-btn-soft" onClick={onClose}>取消</button>
        <button type="button" className="v7-btn v7-btn-primary" disabled={busy} onClick={() => void apply()}>{busy ? '应用中…' : '应用到故事'}</button></div>
    </section>
  </SurfaceDialog>;
}
