import { AssistCandidates } from '../../components/AssistCandidates';
import { AssistDirector } from '../../components/AssistDirector';
import { AssistGroups } from '../../components/AssistGroups';
import { AssistStoryCharacters } from '../../components/AssistStoryCharacters';
import { AssistNarrative } from '../../components/AssistNarrative';
import { AssistRequestJson } from '../../components/AssistRequestJson';
import { AssistInspector } from '../../components/AssistInspector';
import { AssistSaves } from '../../components/AssistSaves';
import { AssistUsage } from '../../components/AssistUsage';
import { PinnedFactsPanel } from '../../components/PinnedFactsPanel';
import type { ComponentType } from 'react';
import type { AssistTab } from './storyUiStore';

/** Each panel declares its entry point; the tool deck owns only navigation/layout. */
export const STORY_PANELS: { id: AssistTab; label: string; sections: ComponentType[] }[] = [
  { id: 'assist', label: '辅助', sections: [AssistCandidates, AssistDirector, AssistGroups, AssistStoryCharacters, AssistNarrative] },
  { id: 'pinned', label: '固定信息', sections: [PinnedFactsPanel] },
  { id: 'inspect', label: '检查', sections: [AssistRequestJson, AssistInspector] },
  { id: 'saves', label: '存档', sections: [AssistSaves, AssistUsage] },
];
