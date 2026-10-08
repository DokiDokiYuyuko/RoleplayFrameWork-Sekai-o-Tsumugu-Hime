import { useState } from 'react';
import WorkshopCharacterPage from './WorkshopCharacterPage';
import WorkshopLorebookPage from './WorkshopLorebookPage';
import LorebookAgentPage from './LorebookAgentPage';
import CardInspirationPage from './CardInspirationPage';
import { WorkbenchPage } from '../design-system/Workbench';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../design-system';
import './workshop-production.css';

type Sub = 'character' | 'inspiration' | 'lorebook' | 'agent';

/** 角色工坊、手工世界书工坊与可审阅的世界书 Agent。 */
export default function WorkshopPage() {
  const [sub, setSub] = useState<Sub>('character');
  const tabs: [Sub, string][] = [
    ['character', '角色工坊'],
    ['inspiration', '角色卡灵感'],
    ['lorebook', '世界书工坊'],
    ['agent', '世界书 Agent'],
  ];
  return <Tabs value={sub} onValueChange={value => setSub(value as Sub)} className="workshop-tabs-root">
    <WorkbenchPage className="workshop-page">
      <header className="workbench-bar workshop-bar">
        <h1>创作工坊</h1>
        <TabsList className="workshop-tabs" aria-label="创作工坊">
          {tabs.map(([id, label]) => <TabsTrigger key={id} value={id}>{label}</TabsTrigger>)}
        </TabsList>
      </header>
      <TabsContent value="character" className="workshop-workspace"><WorkshopCharacterPage /></TabsContent>
      <TabsContent value="inspiration" className="workshop-workspace"><CardInspirationPage /></TabsContent>
      <TabsContent value="lorebook" className="workshop-workspace"><WorkshopLorebookPage /></TabsContent>
      <TabsContent value="agent" className="workshop-workspace"><LorebookAgentPage /></TabsContent>
    </WorkbenchPage>
  </Tabs>;
}
