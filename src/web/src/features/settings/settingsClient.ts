import { api } from '../../api/client';
import type { Api } from '../../api/Api';

/** Public transport surface for settings. UI consumers cannot depend on unrelated endpoints. */
export const settingsClient: Pick<Api, 'deletePromptPreset' | 'deleteVoiceProfile' | 'disableTTS' | 'enableTTS' | 'getTTSStatus' | 'listVoiceProfiles' | 'previewPromptPresetImport' | 'savePromptPreset' | 'setDefaultVoiceProfile' | 'testSettingsConnection' | 'uploadVoiceProfile'> = {
  deletePromptPreset: api.deletePromptPreset,
  deleteVoiceProfile: api.deleteVoiceProfile,
  disableTTS: api.disableTTS,
  enableTTS: api.enableTTS,
  getTTSStatus: api.getTTSStatus,
  listVoiceProfiles: api.listVoiceProfiles,
  previewPromptPresetImport: api.previewPromptPresetImport,
  savePromptPreset: api.savePromptPreset,
  setDefaultVoiceProfile: api.setDefaultVoiceProfile,
  testSettingsConnection: api.testSettingsConnection,
  uploadVoiceProfile: api.uploadVoiceProfile,
};
