import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';
const require = createRequire(import.meta.url);
export function load(path, imports = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  new Function('require', 'exports', code)(name => imports[name] ?? storyImport(name, imports) ?? require(name), exports);
  return exports;
}
export const storyProjection = load('../src/features/stories/storyView.ts');
export const metadataModule = load('../src/features/stories/storySessionMetadata.ts');
export const storyUi = load('../src/features/stories/storyUiStore.ts');
export const storyReducer = load('../src/features/stories/storyEventReducer.ts', {
  './storyView': storyProjection, '../../utils/optimisticMessages': load('../src/utils/optimisticMessages.ts'),
});
export const storyRequest = load('../src/api/request.ts', {
  './lanSession': { LanSessionExpiredError: class extends Error {} }, '../utils/loadDiagnostics': { beginLoad: () => () => {} },
});
export const receiptModule = load('../src/utils/commandReceipt.ts');
export const sha256Module = load('../src/utils/sha256.ts');
export const connectionModule = load('../src/features/stories/storyConnection.ts');
export const storyCommandModule = load('../src/api/storyCommands.ts', { './request': storyRequest, '../utils/commandReceipt': receiptModule, '../utils/clientId.js': { createClientId }, '../utils/sha256': sha256Module });
export function storyImport(name, imports = {}) {
  if (name.endsWith('/settingsResource')) return load('../src/features/resources/settingsResource.ts');
  if (name.endsWith('/queryClient')) return { queryClient: load('../src/queryClient.ts') .queryClient };
  if (name.endsWith('/resourceQueries')) return { resourceQueries: load('../src/features/resources/resourceQueries.ts', { '../../api/client': imports['../api/client'] ?? { api: {} }, '../worlds/worldCreationApi': { worldCreationApi: {} } }).resourceQueries };
  const client = name.match(/\/(library|stories|settings|creation|simpleChat)Client$/)?.[1];
  if (client && imports['../api/client']?.api) return { [client + 'Client']: imports['../api/client'].api };
  if (name === '../api/request') return storyRequest;
  if (name.endsWith('/storySessionMetadata')) return metadataModule;
  if (name.endsWith('/storyConnection')) return connectionModule;
  if (name.endsWith('/storyQueries')) return { loadSessionSummaries: () => (imports['../features/stories/storyApi']?.storyApi ?? imports['../api/client']?.api).listSessions() };
  if (name.endsWith('/commandReceipt')) return receiptModule;
  if (name.endsWith('/storyEventReducer')) return storyReducer;
  if (name === './storyCommands') return { storyCommand: storyCommandModule.createStoryCommands(imports['./request']?.jsonRequest) };
  if (name === '../api/storyCommands') return { prepareStoryCommand: (_path, payload) => ({ id: payload.operation_id, payload }) };
  if (name.endsWith('/storyView')) return storyProjection;
  if (name.endsWith('/storyUiStore')) return storyUi;
}
