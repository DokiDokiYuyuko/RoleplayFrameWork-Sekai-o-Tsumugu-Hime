export interface BubbleStyle { id: string; name: string; note: string; ornament: 'star' | 'page' | 'crystal' | 'moon' | 'none' }
/** Compiled, local styles. Add a descriptor and its CSS once for both chat experiences. */
export const BUBBLE_STYLES: BubbleStyle[] = [
  { id: 'star-track', name: '星轨', note: '星芒角饰与双层细线', ornament: 'star' },
  { id: 'book-note', name: '书笺', note: '书页边线与金色页签', ornament: 'page' },
  { id: 'crystal', name: '水晶', note: '棱角细框与透明光面', ornament: 'crystal' },
  { id: 'moonlight', name: '月华', note: '柔和弧线与银色月牙', ornament: 'moon' },
  { id: 'plain', name: '简净', note: '清晰边框，专注长篇阅读', ornament: 'none' },
];
export function resolveBubbleStyle(id?: string): BubbleStyle {
  return BUBBLE_STYLES.find((style) => style.id === id) ?? BUBBLE_STYLES.find((style) => style.id === 'plain')!;
}
