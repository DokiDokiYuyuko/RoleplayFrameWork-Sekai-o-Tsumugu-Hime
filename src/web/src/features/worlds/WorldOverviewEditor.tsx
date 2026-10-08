import { WorldCoverPicker } from './WorldCoverPicker';

export interface WorldOverviewValue {
  title: string;
  description: string;
  core_brief: string;
  cover_id?: string | null;
}

export function WorldOverviewEditor({ value, onChange, onSave, busy = false, saveLabel = "保存修改" }: {
  value: WorldOverviewValue;
  onChange: (value: WorldOverviewValue) => void;
  onSave: () => void;
  busy?: boolean;
  saveLabel?: string;
}) {
  const change = (patch: Partial<WorldOverviewValue>) => onChange({ ...value, ...patch });
  return (
    <div className="worlds-overview-form">
      <label>世界名称<input value={value.title} onChange={(event) => change({ title: event.target.value })} /></label>
      {'cover_id' in value && <WorldCoverPicker value={value.cover_id ?? null} onChange={(cover_id) => change({ cover_id })} disabled={busy} />}
      <label>简介<textarea rows={3} value={value.description} onChange={(event) => change({ description: event.target.value })} /></label>
      <label>核心设定<textarea rows={8} value={value.core_brief} onChange={(event) => change({ core_brief: event.target.value })} placeholder="填写每轮对话都应知道的世界规则；完整原稿可保存在背景档案。" /></label>
      <small>这里不设固定 token 上限；内容会进入故事的每轮上下文。</small>
      <button className="v7-btn v7-btn-primary" disabled={busy || !value.title.trim()} onClick={onSave}>{busy ? "保存中…" : saveLabel}</button>
    </div>
  );
}
