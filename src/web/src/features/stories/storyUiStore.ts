import { create } from 'zustand';
export type AssistTab = 'assist' | 'pinned' | 'inspect' | 'saves';
const tabs: AssistTab[] = ['assist', 'pinned', 'inspect', 'saves'];
function read(key: string) { try { return localStorage.getItem('mrp.' + key); } catch { return null; } }
function write(key: string, value: string) { try { localStorage.setItem('mrp.' + key, value); } catch { /* Optional preference cache. */ } }
interface StoryUiState {
  assistPanelOpen: boolean; assistPanelWide: boolean; assistTab: AssistTab;
  toggleAssistPanel(): void; toggleAssistWidth(): void; setAssistTab(tab: AssistTab): void; openPanel(tab: AssistTab): void;
}
const dock = typeof window !== 'undefined' && window.innerWidth > 1400;
const cachedTab = read('assistTab') as AssistTab;
export const useStoryUiStore = create<StoryUiState>()((set, get) => ({
  assistPanelOpen: dock && read('assistPanelOpen') === '1',
  assistPanelWide: dock && read('assistPanelWide') === '1',
  assistTab: tabs.includes(cachedTab) ? cachedTab : 'assist',
  toggleAssistPanel() { const open = !get().assistPanelOpen; write('assistPanelOpen', open ? '1' : '0'); set({ assistPanelOpen: open }); },
  toggleAssistWidth() { const wide = !get().assistPanelWide; write('assistPanelWide', wide ? '1' : '0'); set({ assistPanelWide: wide }); },
  setAssistTab(tab) { if (!tabs.includes(tab)) return; write('assistTab', tab); set({ assistTab: tab }); },
  openPanel(tab) { get().setAssistTab(tab); write('assistPanelOpen', '1'); set({ assistPanelOpen: true }); },
}));
