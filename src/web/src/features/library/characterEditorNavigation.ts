/** Reveal an editor chapter, including every collapsed ancestor, before moving focus. */
export function revealCharacterChapter(target: HTMLElement | null): boolean {
  if (!target) return false;
  let ancestor: HTMLElement | null = target;
  while (ancestor) {
    if (ancestor.tagName === "DETAILS") (ancestor as HTMLDetailsElement).open = true;
    ancestor = ancestor.parentElement;
  }
  if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
  const panel = target.closest?.<HTMLElement>('.character-editor-main,.character-editor-companion');
  if (panel && target.closest?.('.character-editor-page')) panel.scrollTop = 0;
  else target.scrollIntoView({ block: "start", behavior: "auto" });
  const controls = target.querySelectorAll?.<HTMLElement>('textarea:not(:disabled):not([hidden]),input:not([type="hidden"]):not([type="file"]):not(:disabled):not([hidden]),.ui-select__trigger:not(:disabled),[role="button"][tabindex="0"]');
  const control = controls && Array.from(controls).find(element => !element.closest?.('[hidden]') && (!element.getClientRects || element.getClientRects().length > 0));
  (control ?? target).focus({ preventScroll: true });
  return true;
}
