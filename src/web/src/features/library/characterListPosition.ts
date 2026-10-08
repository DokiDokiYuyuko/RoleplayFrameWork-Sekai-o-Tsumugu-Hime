interface CharacterListPosition {
  top: number;
  left: number;
  characterId: string | null;
}
const positions = new Map<string, CharacterListPosition>();
const keyFor = (search: string) => `mrp.character-library.position:${search}`;

/** The list restores its nearest scrolling ancestor, independently of shared data caches. */
export function characterListScrollOwner(list: HTMLElement): HTMLElement {
  let ancestor = list.parentElement;
  while (ancestor) {
    const overflow = list.ownerDocument.defaultView?.getComputedStyle(ancestor).overflowY;
    if (overflow === "auto" || overflow === "scroll") return ancestor;
    ancestor = ancestor.parentElement;
  }
  return list.ownerDocument.scrollingElement as HTMLElement ?? list;
}
export function rememberCharacterListPosition(list: HTMLElement, search: string, characterId: string | null) {
  const owner = characterListScrollOwner(list);
  const position = { top: owner.scrollTop, left: owner.scrollLeft, characterId };
  positions.set(keyFor(search), position);
  try { sessionStorage.setItem(keyFor(search), JSON.stringify(position)); } catch { /* Per-tab UI memory remains available without storage. */ }
}
export function restoreCharacterListPosition(list: HTMLElement, search: string): boolean {
  let position = positions.get(keyFor(search));
  if (!position) {
    try {
      const stored = JSON.parse(sessionStorage.getItem(keyFor(search)) || "null");
      if (stored && Number.isFinite(stored.top) && Number.isFinite(stored.left) && (typeof stored.characterId === "string" || stored.characterId === null)) position = stored;
    } catch { /* Optional browser storage. */ }
  }
  if (!position) return false;
  const owner = characterListScrollOwner(list);
  owner.scrollTo({ top: position.top, left: position.left, behavior: "auto" });
  const edit = position.characterId === null ? list.querySelector<HTMLElement>('[data-testid="character-create"]')
    : Array.from(list.querySelectorAll<HTMLElement>('[data-character-edit]')).find(button => button.getAttribute('data-character-edit') === position.characterId);
  const target = edit ?? list.querySelector<HTMLElement>('[aria-label="搜索角色"]');
  target?.focus({ preventScroll: true });
  // A save may change the active sort or filter. Keep the surviving focus target visible.
  if (target) {
    const viewport = owner.getBoundingClientRect();
    const rect = target.getBoundingClientRect();
    if (rect.bottom < viewport.top || rect.top > viewport.bottom) target.scrollIntoView({ block: "nearest", behavior: "auto" });
  }
  return true;
}
