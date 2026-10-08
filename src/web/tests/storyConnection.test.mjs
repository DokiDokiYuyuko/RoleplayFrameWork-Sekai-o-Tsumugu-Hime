import test from 'node:test';
import assert from 'node:assert/strict';
import { connectionModule } from './storySupport.mjs';

test('connection callbacks lose ownership across branch ABA and explicit close', () => {
  const sources = [], received = [], statuses = [], recoveries = [];
  const connection = connectionModule.createStoryConnection((branch, cursor, status, resync) => {
    const source = { branch, cursor, status, resync, closed: 0, handlers: {},
      addEventListener(type, handler) { this.handlers[type] = handler; }, close() { this.closed++; } };
    sources.push(source); return source;
  }, ['message.final']);
  const open = branch => connection.connect(branch, 'cursor', event => received.push(event),
    value => statuses.push(value), () => recoveries.push(branch));
  open('A'); open('B'); open('A');
  for (const source of sources) {
    source.status('open'); source.resync(); source.handlers['message.final']({ data: '{"type":"message.final"}' });
  }
  assert.equal(received.length, 1); assert.deepEqual(statuses, ['open']); assert.deepEqual(recoveries, ['A']);
  assert.equal(sources[0].closed, 1); assert.equal(sources[1].closed, 1);
  connection.close(); sources[2].status('closed'); sources[2].resync();
  assert.equal(connection.isConnected(), false); assert.equal(statuses.length, 1); assert.equal(recoveries.length, 1);
});

test('malformed transport data is ignored but domain handler errors are not swallowed', () => {
  let handler;
  const connection = connectionModule.createStoryConnection(() => ({ close() {}, addEventListener(_type, fn) { handler = fn; } }), ['event']);
  connection.connect('A', null, () => { throw new Error('domain invariant'); }, () => {}, () => {});
  assert.doesNotThrow(() => handler({ data: 'comment' }));
  assert.throws(() => handler({ data: '{}' }), /domain invariant/);
});
