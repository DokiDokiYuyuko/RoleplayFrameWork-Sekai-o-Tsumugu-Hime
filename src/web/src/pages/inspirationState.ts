import type { CardInspirationBrief, CardInspirationDraft, CharacterView } from '../types';

/** Compare the submitted snapshot, so a late save never clears newer typing. */
export function sameBrief(left: CardInspirationBrief, right: CardInspirationBrief): boolean {
  return left.requirement === right.requirement && left.detail === right.detail
    && left.borrow === right.borrow && left.avoid === right.avoid;
}

/** A page-local ownership epoch protects task switching, deletion and unmount. */
export function createRequestScope() {
  let epoch = 0;
  return {
    advance: () => ++epoch,
    capture: () => { const captured = epoch; return () => captured === epoch; },
  };
}

/** Historical drafts stay immutable; saved-role editing starts from one live CAS snapshot. */
export function committedInspirationSnapshot(draft: CardInspirationDraft, character: CharacterView) {
  if (draft.committed_character_id !== character.id || !Number.isInteger(character.revision)) {
    throw new Error('无法确认已保存角色的身份与修订，请重新读取后再编辑。');
  }
  return {
    draft: { ...draft, payload: structuredClone(character.card), aliases: [...character.aliases] },
    identity: { id: character.id, revision: character.revision },
  };
}
