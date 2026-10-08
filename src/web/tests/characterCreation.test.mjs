import test from 'node:test';
import assert from 'node:assert/strict';
import { appendVoiceExample, applyAiCandidate, characterDraftKey, formatVoiceExample, personaSignature, trialHistoryError } from '../src/utils/characterCreation.js';
const card = {name:'合成向导', description:'前文🌙保持原样，旧口吻，后文保持原样。', appearance:'银发', personality:'耐心', scenario:'门口', mes_example:'', extensions:{unknown:'preserved'}};
test('independent creations and existing editor drafts never share keys', () => {
  assert.notEqual(characterDraftKey(undefined,'a'), characterDraftKey(undefined,'b'));
  assert.equal(characterDraftKey('id','a'), characterDraftKey('id','b'));
});
test('selected replacement preserves Unicode prefix, suffix and hidden imported fields', () => {
  const start = card.description.indexOf('旧口吻');
  const result = applyAiCandidate(card, {field:'description',original:card.description,value:'新口吻',signature:personaSignature(card),selection:{start,end:start+3}});
  assert.equal(result.description,'前文🌙保持原样，新口吻，后文保持原样。');
  assert.deepEqual(result.extensions,card.extensions);
  assert.equal(result.appearance,'银发');
  assert.equal(card.description.includes('旧口吻'),true);
});
test('candidate cannot overwrite edits to its field or related identity', () => {
  const candidate = {field:'description',original:card.description,value:'candidate',signature:personaSignature(card)};
  assert.throws(() => applyAiCandidate({...card,description:'手修'},candidate),/过期/);
  assert.throws(() => applyAiCandidate({...card,name:'另一人'},candidate),/过期/);
  assert.equal(applyAiCandidate({...card,tags:['组织标签']},candidate).description,'candidate');
});
test('short trial supports six complete turns and rejects forged identities and over-budget history', () => {
  const history = Array.from({length:10},(_,i) => ({role:i%2?'assistant':'user',content:'合成消息'}));
  assert.equal(trialHistoryError(history),'');
  assert.match(trialHistoryError([...history,{role:'user',content:'q'},{role:'assistant',content:'a'}]),/六轮/);
  assert.match(trialHistoryError([{role:'system',content:'x'},{role:'assistant',content:'a'}]),/身份/);
  assert.match(trialHistoryError(history.map(row => ({...row,content:'文'.repeat(2001)}))),/20000/);
});
test('voice example append keeps existing prose and does not duplicate after retry', () => {
  const example = formatVoiceExample('你好\n朋友','你好，合成玩家。');
  const existing = '<START>\n{{user}}: 原示例\n{{char}}: 保留';
  const appended = appendVoiceExample(existing,example);
  assert.ok(appended.startsWith(existing+'\n\n'));
  assert.equal(appendVoiceExample(appended,example),appended);
  assert.ok(appended.includes('{{user}}: 你好\n朋友'));
});
