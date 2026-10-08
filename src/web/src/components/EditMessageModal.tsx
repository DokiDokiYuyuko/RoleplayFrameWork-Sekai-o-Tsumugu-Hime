import { useEffect, useState } from 'react';
import type { InputGroupEditPart, Message } from '../types';
import { Modal } from './common';

/**
 * 编辑消息弹层。编辑历史内容不会自动创建世界线，也不会重写后续消息。
 * edited 消息再切换候选会被覆盖——有 variants 且已编辑过时附加提示。
 */
export function EditMessageModal({
  initial,
  hasFuture,
  hasVariants,
  edited,
  onSave,
  onClose,
}: {
  initial: string;
  hasFuture: boolean;
  hasVariants: boolean;
  edited: boolean;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
}) {
  const [text, setText] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Esc 关闭
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async () => {
    if (!text.trim() || saving) return;
    setSaving(true);
    try {
      await onSave(text);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败');
      setSaving(false); // 失败留在弹层，可重试或取消
    }
  };

  return (
    <Modal title="编辑消息" onClose={onClose}>
      {hasFuture && (
        <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-700">
          后续对话会保留，不会因本次编辑自动重写。需要探索不同后续时，可从目标消息手动新建世界线。
        </p>
      )}
      {edited && hasVariants && (
        <p className="mb-3 rounded-lg bg-slate-100 px-3 py-2 text-xs leading-relaxed text-slate-600">
          此消息编辑过且存在重roll 候选：保存后若切换候选，编辑内容会被覆盖。
        </p>
      )}
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save();
        }}
        rows={6}
        className="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm leading-relaxed focus:border-indigo-400 focus:outline-none"
      />
      {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          取消
        </button>
        <button
          type="button"
          onClick={save}
          disabled={!text.trim() || saving}
          className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm text-white hover:bg-indigo-600 disabled:opacity-50"
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
      <p className="mt-2 text-right text-[10px] text-slate-400">Ctrl+Enter 保存 · Esc 取消</p>
    </Modal>
  );
}

const CHANNEL_LABELS: Record<Message['kind'], string> = {
  roleplay: '台词',
  scene: '旁白 / 场景',
  inner: '内心（仅自己可见）',
  ooc: '补充说明',
  system_event: '系统记录',
};

/** Edit every channel segment created by one player submission as one unit. */
export function EditPlayerInputModal({
  messages,
  hasFuture,
  expectedBranchRevision,
  onSave,
  onClose,
}: {
  messages: Message[];
  hasFuture: boolean;
  expectedBranchRevision: number;
  onSave: (parts: InputGroupEditPart[], expectedBranchRevision: number) => Promise<void>;
  onClose: () => void;
}) {
  const [parts, setParts] = useState(() => messages.map((message) => ({
    message_id: message.id,
    expected_fingerprint: message.fingerprint,
    kind: message.kind,
    content: message.content,
  })));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasEmptyPart = parts.some((part) => !part.content.trim());
  const hasNestedMarker = parts.some((part) => /[（(]\s*(?:内心|画外音|旁白)\s*[：:]/.test(part.content));

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async () => {
    if (saving || hasEmptyPart || hasNestedMarker) return;
    setSaving(true);
    try {
      await onSave(parts.map(({ message_id, expected_fingerprint, content }) => ({
        message_id, expected_fingerprint, content,
      })), expectedBranchRevision);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败');
      setSaving(false);
    }
  };

  return (
    <Modal title="编辑本次输入" onClose={onClose}>
      {hasFuture && (
        <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-700">
          后续对话会保留，不会自动重写。需要尝试不同后续时，可从目标消息手动新建世界线。
        </p>
      )}
      <p className="mb-3 text-xs leading-relaxed text-slate-500">
        这次输入中的各段会一起保存。内心内容仍只对你可见；段落类型和顺序会保留。
      </p>
      <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
        {parts.map((part, index) => (
          <label key={part.message_id} className="block space-y-1.5">
            <span className="text-xs font-medium text-slate-600">
              第 {index + 1} 段 · {CHANNEL_LABELS[part.kind]}
            </span>
            <textarea
              autoFocus={index === 0}
              value={part.content}
              onChange={(event) => setParts((current) => current.map((item) =>
                item.message_id === part.message_id ? { ...item, content: event.target.value } : item))}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) void save();
              }}
              rows={Math.min(10, Math.max(3, Math.ceil(part.content.length / 48)))}
              className="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm leading-relaxed focus:border-indigo-400 focus:outline-none"
            />
          </label>
        ))}
      </div>
      {hasEmptyPart && <p className="mt-2 text-xs text-rose-600">每段都需要保留正文；本次编辑不会删除段落。</p>}
      {hasNestedMarker && <p className="mt-2 text-xs text-rose-600">每个框只编辑当前段落正文；请勿在段落里新增内心或旁白标记。</p>}
      {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
          取消
        </button>
        <button type="button" onClick={() => void save()} disabled={saving || hasEmptyPart || hasNestedMarker}
          className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm text-white hover:bg-indigo-600 disabled:opacity-50">
          {saving ? '保存中…' : '保存全部段落'}
        </button>
      </div>
      <p className="mt-2 text-right text-[10px] text-slate-400">Ctrl+Enter 保存 · Esc 取消</p>
    </Modal>
  );
}

/** R32.3 删除确认（小号） */
export function DeleteMessageModal({
  onDelete,
  onClose,
}: {
  onDelete: () => Promise<void>;
  onClose: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  return (
    <Modal title="删除消息" onClose={onClose}>
      <p className="text-sm leading-relaxed text-slate-600">
        删除后不可恢复，角色也将忘记这条消息（不进入后续上下文）。其他消息不受影响。
      </p>
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          取消
        </button>
        <button
          type="button"
          disabled={deleting}
          onClick={async () => {
            setDeleting(true);
            try {
              await onDelete();
              onClose();
            } catch {
              setDeleting(false);
            }
          }}
          className="rounded-lg bg-rose-500 px-3 py-1.5 text-sm text-white hover:bg-rose-600 disabled:opacity-50"
        >
          {deleting ? '删除中…' : '删除'}
        </button>
      </div>
    </Modal>
  );
}
