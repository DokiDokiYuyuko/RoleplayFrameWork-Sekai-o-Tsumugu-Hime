import { useRef, useState } from 'react';
import CharacterEditor from './CharacterEditor';
import type { CharacterEditorPayload } from './CharacterEditor';
import { useCharacterStore } from '../store/characterStore';
import type { Character, CharacterCard, CharacterView } from '../types';
import { characterCreation } from '../api/characterCreation';

/** 空白角色卡（新建模式初始值；后端 import 会补齐/归一化其余字段） */
export function blankCard(): CharacterCard {
  return {
    spec: 'chara_card_v2',
    spec_version: '2.0',
    name: '',
    description: '',
    appearance: '',
    traits_label: '能力与实力',
    traits: '',
    personality: '',
    scenario: '',
    first_mes: '',
    mes_example: '',
    alternate_greetings: [],
    system_prompt: null,
    post_history_instructions: null,
    creator_notes: '',
    creator: '',
    character_version: '',
    tags: [],
    extensions: {},
    avatar_path: null,
    source_format: 'ccv2',
  };
}

/**
 * 完整角色编辑器弹层（R31/R50）——角色页与聊天页信息弹层共用。
 *
 * - 有 `character`：编辑（保存走 updateCharacter）；「另存副本」走导入创建
 * - 无 `character`：**新建角色**（空白卡 → importCharacter 落库；
 *   新建模式下可选头像，创建成功后自动上传；成功后回调 + 关闭）
 */
export function CharacterEditorModal({
  character,
  presentation = "dialog",
  onClose,
  onCreated,
}: {
  character?: Character;
  presentation?: "dialog" | "page";
  onClose: () => void;
  /** 新建成功回调（角色页用于提示"已创建角色 X"） */
  onCreated?: (created: CharacterView) => void;
}) {
  const { updateCharacter } = useCharacterStore();
  const [saving, setSaving] = useState(false);
  const pendingFullBody = useRef<File | null>(null);
  const createdForRetry = useRef<CharacterView | null>(null);
  const pageCreation = useRef<CharacterView | null>(null);
  const pendingAvatar = useRef<File | null>(null); // 新建模式的待上传头像
  const editorRevision = useRef(character?.revision);

  return (
    <CharacterEditor
      presentation={presentation}
      initialRuntime={presentation === "page" && character ? {
        model: character.llm.inherit_model === true || (character.llm.inherit_model == null && character.llm.model === "deepseek/deepseek-v4-flash") ? "" : character.llm.model ?? "",
        base_url: character.llm.inherit_base_url === true || (character.llm.inherit_base_url == null && character.llm.base_url === "https://openrouter.ai/api/v1") ? "" : character.llm.base_url ?? "",
        max_tokens: Number(character.llm.sampling?.max_tokens ?? 0),
      } : undefined}
      initialCard={character?.card ?? blankCard()}
      initialAliases={character?.aliases ?? []}
      characterId={character?.id}
      initialSourceWorldId={character?.source_world_id ?? null}
      initialAuthoringSource={character?.authoring_source ?? null}
      onMediaRevision={revision => { editorRevision.current = revision; }}
      getCharacterRevision={() => editorRevision.current}
      busy={saving}
      onAvatarPicked={
        character ? undefined : (f) => {
          pendingAvatar.current = f;
        }
      }
      onFullBodyPicked={character ? undefined : (file) => { pendingFullBody.current = file; }}
      onSaveSucceeded={(asCopy, hasLaterEdits) => {
        if (asCopy) { createdForRetry.current = null; return; }
        if (hasLaterEdits) return;
        const created = pageCreation.current;
        pageCreation.current = null; createdForRetry.current = null;
        if (created) onCreated?.(created);
        if (presentation === 'dialog') onClose();
      }}
      onSave={async (payload: CharacterEditorPayload) => {
        if (!character || payload.asCopy) {
          // 新建 / 另存副本：走导入创建新角色
          setSaving(true);
          try {
            const previousCreation = createdForRetry.current;
            const created = previousCreation || await characterCreation.create(payload.card, payload.source_world_id ?? null);
            if (!previousCreation) useCharacterStore.getState().upsert(created);
            if (previousCreation) await updateCharacter(created.id, { expected_revision: useCharacterStore.getState().characters.find(item => item.id === created.id)?.revision ?? created.revision, card: payload.card });
            createdForRetry.current = created;
            if (pendingAvatar.current) {
              await useCharacterStore.getState().uploadMedia(created.id, 'avatar', pendingAvatar.current);
              pendingAvatar.current = null;
            }
            if (pendingFullBody.current) {
              await useCharacterStore.getState().uploadMedia(created.id, 'full-body', pendingFullBody.current);
              pendingFullBody.current = null;
            }
            if (payload.aliases.length > 0 || payload.runtime) {
              await updateCharacter(created.id, {
                aliases: payload.aliases,
                source_world_id: payload.source_world_id ?? null,
                authoring_source: character?.authoring_source ?? undefined,
                model: payload.runtime?.model,
                base_url: payload.runtime?.base_url,
                sampling: payload.runtime
                  ? {
                      ...((character?.llm as { sampling?: Record<string, unknown> } | undefined)?.sampling ?? {}),
                      max_tokens: payload.runtime.max_tokens,
                    }
                  : undefined,
              });
            }
            if (!character) {
              const committed = useCharacterStore.getState().characters.find(item => item.id === created.id) ?? created;
              pageCreation.current = committed;
            }
          } finally {
            setSaving(false);
          }
        } else {
          setSaving(true);
          try {
            await updateCharacter(character.id, {
              expected_revision: editorRevision.current,
              card: payload.card,
              source_world_id: payload.source_world_id ?? null,
              aliases: payload.aliases,
              model: payload.runtime?.model,
              base_url: payload.runtime?.base_url,
              sampling: payload.runtime
                ? {
                    ...((character.llm as { sampling?: Record<string, unknown> }).sampling ?? {}),
                    max_tokens: payload.runtime.max_tokens,
                  }
                : undefined,
            });
            editorRevision.current = useCharacterStore.getState().characters.find(item => item.id === character.id)?.revision;
          } finally {
            setSaving(false);
          }
        }
      }}
      onClose={onClose}
    />
  );
}
