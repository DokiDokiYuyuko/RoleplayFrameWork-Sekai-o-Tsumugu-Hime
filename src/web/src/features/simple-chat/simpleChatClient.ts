import { api } from '../../api/client';
import type { Api } from '../../api/Api';

/** Public transport surface for simple-chat. UI consumers cannot depend on unrelated endpoints. */
export const simpleChatClient: Pick<Api, 'createSimpleChat' | 'deleteSimpleChat' | 'getSimpleChat' | 'patchSimpleChat' | 'regenerateSimpleChatMessage' | 'sendSimpleChatMessage' | 'stopSimpleChatGeneration' | 'switchSimpleChatVariant'> = {
  createSimpleChat: api.createSimpleChat,
  deleteSimpleChat: api.deleteSimpleChat,
  getSimpleChat: api.getSimpleChat,
  patchSimpleChat: api.patchSimpleChat,
  regenerateSimpleChatMessage: api.regenerateSimpleChatMessage,
  sendSimpleChatMessage: api.sendSimpleChatMessage,
  stopSimpleChatGeneration: api.stopSimpleChatGeneration,
  switchSimpleChatVariant: api.switchSimpleChatVariant,
};
