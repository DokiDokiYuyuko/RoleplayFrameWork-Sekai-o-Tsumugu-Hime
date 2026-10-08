/** Browser history for a story location. Message anchors never change branch state. */
export function readStoryLocation(): {
  storyId: string | null;
  branchId: string | null;
  messageId: string | null;
} {
  const query = new URLSearchParams(window.location.search);
  const match = window.location.pathname.match(
    /^\/stories\/([^/]+)\/branches\/([^/]+)\/?$/,
  );
  return {
    storyId: match ? decodeURIComponent(match[1]) : query.get("story"),
    branchId: match ? decodeURIComponent(match[2]) : query.get("branch"),
    messageId: query.get("message"),
  };
}

export function writeStoryLocation(
  storyId: string,
  branchId: string,
  messageId?: string | null,
  replace = false,
): void {
  const url = new URL(window.location.href);
  url.pathname = `/stories/${encodeURIComponent(storyId)}/branches/${encodeURIComponent(branchId)}`;
  url.searchParams.delete("story");
  url.searchParams.delete("branch");
  if (messageId) url.searchParams.set("message", messageId);
  else url.searchParams.delete("message");
  if (url.href !== window.location.href) {
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
  }
  window.dispatchEvent(new Event("mrp:location"));
}
