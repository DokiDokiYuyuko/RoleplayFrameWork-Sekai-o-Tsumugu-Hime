import { useState } from 'react';
import type { CharacterView } from '../types';
import { Avatar, Modal } from './common';
import { CharacterEditorModal } from './CharacterEditorModal';

/** 聊天内角色信息弹层：点击消息头像查看角色卡信息（只读）+ 一键打开完整编辑器。 */
export function CharacterInfoModal({
  character,
  onClose,
  readOnly = false,
  avatarSrc,
  snapshotAvatar = false,
}: {
  character: CharacterView;
  onClose: () => void;
  readOnly?: boolean;
  avatarSrc?: string;
  snapshotAvatar?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const card = character.card;

  if (editing) {
    return <CharacterEditorModal character={character} onClose={() => setEditing(false)} />;
  }

  return (
    <Modal title={card.name} onClose={onClose}>
      <div className="flex items-start gap-3">
        <Avatar name={card.name} color={character.color} src={avatarSrc} characterId={avatarSrc || snapshotAvatar ? undefined : character.id} imageVersion={character.updated_at ?? character.revision} />
        <div className="min-w-0 flex-1 text-xs text-slate-400">
          {character.aliases.length > 0 && <p>别名：{character.aliases.join(' / ')}</p>}
          <p className="mt-0.5">模型：{character.llm.model}</p>
          {card.creator && <p className="mt-0.5">作者：{card.creator}</p>}
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <Field label="人设">{card.description || '（未填写）'}</Field>
        {card.appearance && <Field label="身体与外貌">{card.appearance}</Field>}
        {card.traits && <Field label={card.traits_label || '能力与实力'}>{card.traits}</Field>}
        {card.personality && <Field label="性格">{card.personality}</Field>}
        {card.scenario && <Field label="场景设定">{card.scenario}</Field>}
        {card.first_mes && <Field label="开场白">{card.first_mes}</Field>}
        {card.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {card.tags.map((t) => (
              <span key={t} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          关闭
        </button>
        {!readOnly && <button
          type="button"
          onClick={() => setEditing(true)}
          className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm text-white hover:bg-indigo-600"
        >
          打开完整编辑器
        </button>}
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
        {children}
      </div>
    </div>
  );
}
