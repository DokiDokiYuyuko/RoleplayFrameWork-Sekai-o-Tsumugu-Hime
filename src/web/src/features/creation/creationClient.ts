import { api } from '../../api/client';
import type { Api } from '../../api/Api';

/** Public transport surface for creation. UI consumers cannot depend on unrelated endpoints. */
export const creationClient: Pick<Api, 'cancelLorebookAgentJob' | 'commitCardInspirationDraft' | 'commitLorebookAgentDraft' | 'createCardInspirationJob' | 'createLorebookAgentJob' | 'deleteCardInspirationJob' | 'editCardInspirationDraft' | 'editLorebookAgentDraft' | 'generateCardInspirationDrafts' | 'generateCharacters' | 'getCardInspirationJob' | 'getDiscoveredCard' | 'getLorebookAgentJob' | 'getLorebookAgentSources' | 'getWorld' | 'nextCardPage' | 'regenerateLorebookAgentDraft' | 'resumeLorebookAgentJob' | 'searchCards' | 'sendCardInspirationAgentTurn' | 'simulateLorebookAgentDraft' | 'updateCardInspirationBrief'> = {
  cancelLorebookAgentJob: api.cancelLorebookAgentJob,
  commitCardInspirationDraft: api.commitCardInspirationDraft,
  commitLorebookAgentDraft: api.commitLorebookAgentDraft,
  createCardInspirationJob: api.createCardInspirationJob,
  createLorebookAgentJob: api.createLorebookAgentJob,
  deleteCardInspirationJob: api.deleteCardInspirationJob,
  editCardInspirationDraft: api.editCardInspirationDraft,
  editLorebookAgentDraft: api.editLorebookAgentDraft,
  generateCardInspirationDrafts: api.generateCardInspirationDrafts,
  generateCharacters: api.generateCharacters,
  getCardInspirationJob: api.getCardInspirationJob,
  getDiscoveredCard: api.getDiscoveredCard,
  getLorebookAgentJob: api.getLorebookAgentJob,
  getLorebookAgentSources: api.getLorebookAgentSources,
  getWorld: api.getWorld,
  nextCardPage: api.nextCardPage,
  regenerateLorebookAgentDraft: api.regenerateLorebookAgentDraft,
  resumeLorebookAgentJob: api.resumeLorebookAgentJob,
  searchCards: api.searchCards,
  sendCardInspirationAgentTurn: api.sendCardInspirationAgentTurn,
  simulateLorebookAgentDraft: api.simulateLorebookAgentDraft,
  updateCardInspirationBrief: api.updateCardInspirationBrief,
};
