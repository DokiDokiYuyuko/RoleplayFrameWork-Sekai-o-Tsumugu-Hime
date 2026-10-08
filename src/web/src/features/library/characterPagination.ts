import { updateAssetFilters, type AssetListFilters } from "./assetList";
export const CHARACTER_PAGE_SIZE = 6;
export function readCharacterPage(params: URLSearchParams): number {
  const value = params.get("characterPage") ?? "1";
  return /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : 1;
}
export function paginateCharacters<T>(items: readonly T[], requestedPage: number) {
  const pages = Math.max(1, Math.ceil(items.length / CHARACTER_PAGE_SIZE));
  const page = Math.min(pages, Math.max(1, Number.isSafeInteger(requestedPage) ? requestedPage : 1));
  const start = (page - 1) * CHARACTER_PAGE_SIZE;
  const end = Math.min(start + CHARACTER_PAGE_SIZE, items.length);
  return { items: items.slice(start, end), total: items.length, page, pages, start, end };
}
export function characterPageParams(params: URLSearchParams, page: number): URLSearchParams {
  const next = new URLSearchParams(params);
  if (Number.isSafeInteger(page) && page > 1) next.set("characterPage", String(page));
  else next.delete("characterPage");
  return next;
}
/** Filter/sort changes return to page one while retaining unrelated route state. */
export function characterFilterParams(params: URLSearchParams, patch: Partial<AssetListFilters>): URLSearchParams {
  return characterPageParams(updateAssetFilters(params, patch), 1);
}
