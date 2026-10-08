import test from 'node:test';
import assert from 'node:assert/strict';
import { resolvePlayerIdentity, isBeforePlayerBoundary, playerDraftKey, playerAvatarUrl, currentPlayerAvatarUrl, canPersonSee } from '../src/utils/playerIdentity.js';

const session = { player_identity_id: 'a-return', player_identities: [
  { id: 'a-first', person_id: 'a', name: '向导', start_seq: 0, avatar_ref: 'old.png', character: { card: { name: '向导' } } },
  { id: 'b', person_id: 'b', name: '守卫', start_seq: 8 },
  { id: 'a-return', person_id: 'a', name: '向导', start_seq: 16 },
] };
test('A → B → A keeps message ownership and does not guess unbound historical identities', () => {
  assert.equal(resolvePlayerIdentity(session, { player_identity_id: 'a-first' }).id, 'a-first');
  assert.equal(resolvePlayerIdentity(session, { player_identity_id: 'b' }).name, '守卫');
  assert.equal(resolvePlayerIdentity(session).id, 'a-return');
  assert.equal(resolvePlayerIdentity(session, {}), null);
});
test('generation boundary blocks older stages while current messages remain usable', () => {
  assert.equal(isBeforePlayerBoundary(session, { seq: 2, player_identity_id: 'a-first' }), true);
  assert.equal(isBeforePlayerBoundary(session, { seq: 10, player_identity_id: 'b' }), true);
  assert.equal(isBeforePlayerBoundary(session, { seq: 20, player_identity_id: 'a-return' }), false);
  assert.equal(isBeforePlayerBoundary(undefined, { seq: 0 }), false);
});
test('draft storage separates both branch and stage; media URLs have no machine paths', () => {
  const keys = new Set([playerDraftKey('root', 'a-first'), playerDraftKey('root', 'b'), playerDraftKey('root', 'a-return'), playerDraftKey('child', 'a-return')]);
  assert.equal(keys.size, 4);
  assert.equal(playerAvatarUrl('branch space', session.player_identities[0]), '/api/v1/sessions/branch%20space/player/avatar/a-first');
  assert.equal(playerAvatarUrl('root', session.player_identities[1]), undefined);
});
test('memory owners use frozen character knowledge rather than account browse permissions', () => {
  const inner = { kind: 'inner', person_id: 'a', known_to: ['a'], visible_to: ['player'] };
  assert.equal(canPersonSee(inner, 'a'), true);
  assert.equal(canPersonSee(inner, 'b'), false);
  assert.equal(canPersonSee({ known_to: ['b'], visible_to: 'all' }, 'newcomer'), false);
  assert.equal(canPersonSee({ visible_to: 'all', control_event: true }, 'b'), false);
});

test('current controls recover library art without changing frozen historical avatar ownership', () => {
  const identity = { id: 'legacy', source_character_id: 'source / role', media_captured: true,
    avatar_ref: null, character: { updated_at: '2026-01-01' } };
  const before = structuredClone(identity);
  assert.equal(currentPlayerAvatarUrl('branch', identity), '/api/v1/characters/source%20%2F%20role/avatar?v=2026-01-01');
  assert.equal(playerAvatarUrl('branch', identity), undefined);
  assert.deepEqual(identity, before);
  assert.equal(currentPlayerAvatarUrl('branch', { ...identity, avatar_ref: 'frozen.png' }), '/api/v1/sessions/branch/player/avatar/legacy');
  assert.equal(currentPlayerAvatarUrl(null, identity), undefined);
  assert.equal(currentPlayerAvatarUrl('branch', { id: 'unknown' }), undefined);
});
