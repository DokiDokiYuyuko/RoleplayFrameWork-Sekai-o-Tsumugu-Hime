/** 与后端 parse_channels 相同的玩家通道切分规则。 */
export interface ChannelPart {
  kind: 'inner' | 'scene' | 'roleplay';
  text: string;
}

const MARKER_RE = /[（(]\s*(内心|画外音|旁白)\s*[：:]\s*(.*?)[）)]/gs;
const OPEN_RE = /[（(]\s*(?:内心|画外音|旁白)\s*[：:]/g;

export function parseChannelParts(content: string, defaultKind: 'scene' | 'roleplay' = 'roleplay'): ChannelPart[] {
  if (!content) return [];
  const parts: ChannelPart[] = [];
  let pos = 0;
  for (const match of content.matchAll(MARKER_RE)) {
    const start = match.index;
    const ordinary = content.slice(pos, start).trim();
    if (ordinary) parts.push({ kind: defaultKind, text: ordinary });
    const marked = match[2].trim();
    if (marked) parts.push({ kind: match[1] === '内心' ? 'inner' : 'scene', text: marked });
    pos = start + match[0].length;
  }
  const remaining = content.slice(pos).trim();
  if (remaining) parts.push({ kind: defaultKind, text: remaining });
  return parts.length ? parts : [{ kind: defaultKind, text: content }];
}

export function hasUnclosedChannelMarker(content: string): boolean {
  const complete = [...content.matchAll(MARKER_RE)].map((match) => [match.index, match.index + match[0].length]);
  for (const open of content.matchAll(OPEN_RE)) {
    if (!complete.some(([start, end]) => start <= open.index && open.index < end)) return true;
  }
  return false;
}
