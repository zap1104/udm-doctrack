'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modulePath = path.join(__dirname, '../../static/js/upload-drafts.js');
function controller(options = {}) {
  assert.ok(fs.existsSync(modulePath), 'Progressive uploads need a recovery controller');
  const {UploadQueue} = require(modulePath);
  return new UploadQueue(options);
}
const file = (name = 'proof.txt') => ({name, size: 12, type: 'text/plain'});
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject}; }
function backend() {
  const calls = [], saved = new Map(); let revision = 0;
  async function transport(action, payload) {
    calls.push([action, payload]);
    if (action === 'open' || action === 'read') return {id: 'manifest-1', revision, files: [...saved.values()]};
    assert.equal(payload.expected_revision, revision);
    if (action === 'upload') {
      const item = {id: `saved-${saved.size + 1}`, client_upload_id: payload.client_upload_id, name: payload.file.name,
        size: payload.file.size, preview_url: '/files/preview/upload/safe/'};
      saved.set(item.id, item); revision++;
      return {id: 'manifest-1', revision, files: [...saved.values()], accepted_ids: [item.id], errors: []};
    }
    if (action === 'remove') {saved.delete(payload.file_id); revision++; return {id: 'manifest-1', revision, files: [...saved.values()]};}
    if (action === 'save') {revision++; return {id: 'manifest-1', revision, files: [...saved.values()]};}
    throw new Error(`Unknown action ${action}`);
  }
  return {calls, saved, transport};
}
test('selection saves files privately without submitting the business form', async () => {
  const server = backend(), announced = []; let published = 0;
  const queue = controller({transport: server.transport, announce: value => announced.push(value), publishDraft: () => published++});
  const [key] = queue.add([file()]); await queue.whenIdle();
  assert.equal(queue.rows.get(key).state, 'saved');
  assert.equal(server.calls.filter(([action]) => action === 'upload').length, 1);
  assert.equal(published, 1); assert.ok(announced.some(value => /saved/i.test(value)));
});
test('several selections serialize every revision-bearing request', async () => {
  const server = backend(); let active = 0, maxActive = 0;
  const queue = controller({transport: async (...args) => {active++; maxActive = Math.max(maxActive, active); await Promise.resolve();
    try {return await server.transport(...args);} finally {active--;}}});
  queue.add([file('one.txt'), file('two.txt')]); queue.add([file('three.txt')]);
  await queue.whenIdle(); assert.equal(maxActive, 1); assert.equal(server.saved.size, 3); assert.equal(queue.revision, 3);
});
test('one rejected file keeps its neighbor saved and can be retried', async () => {
  const server = backend(); let refused = true;
  const queue = controller({transport: async (action, payload) => {
    if (action === 'upload' && payload.file.name === 'retry.txt' && refused) return {id: 'manifest-1', revision: 1,
      files: [...server.saved.values()], accepted_ids: [], errors: [{message: 'File could not be saved. Retry.'}]};
    return server.transport(action, payload);
  }});
  const [first, second] = queue.add([file('one.txt'), file('retry.txt')]); await queue.whenIdle();
  assert.equal(queue.rows.get(first).state, 'saved'); assert.equal(queue.rows.get(second).state, 'error');
  refused = false; queue.retry(second); await queue.whenIdle(); assert.equal(queue.rows.get(second).state, 'saved');
});
test('removing a file while its upload runs never resurrects a saved card', async () => {
  const server = backend(), started = deferred(), release = deferred(); let resurrected = false;
  const queue = controller({transport: async (action, payload) => {
    if (action === 'upload') {started.resolve(); await release.promise;}
    return server.transport(action, payload);
  }, changed: rows => {if (queue.rows.size && [...rows.values()].some(row => row.removeRequested && row.state === 'saved')) resurrected = true;}});
  const [key] = queue.add([file()]); await started.promise; queue.remove(key); release.resolve(); await queue.whenIdle();
  assert.equal(server.saved.size, 0); assert.equal(queue.rows.get(key).state, 'removed'); assert.equal(resurrected, false);
});
test('an ambiguous upload response is reconciled using its client identity', async () => {
  const server = backend(); let loseResponse = true;
  const queue = controller({transport: async (action, payload) => {
    const answer = await server.transport(action, payload);
    if (action === 'upload' && loseResponse) {loseResponse = false; throw new TypeError('Network response lost');}
    return answer;
  }});
  const [key] = queue.add([file()]); await queue.whenIdle();
  assert.equal(queue.rows.get(key).state, 'saved'); assert.equal(server.saved.size, 1);
  assert.equal(server.calls.filter(([action]) => action === 'upload').length, 1);
  assert.ok(server.calls.some(([action]) => action === 'read'));
});
for (const withValues of [false, true]) test(`removing after a committed upload with both responses lost stays blocked (${withValues})`, async () => {
  const server = backend(), started = deferred(), release = deferred(); let offline = true, submits = 0;
  const queue = controller({readValues: withValues ? () => ({subject:['Keep']}) : undefined,
    transport: async (action, payload) => {
      if(action === 'read' && offline) throw new TypeError('Connection lost');
      if(action === 'upload') {
        const answer=await server.transport(action,payload); started.resolve(); await release.promise;
        throw new TypeError('Acknowledgement lost');
      }
      if(action === 'save' && offline) throw new TypeError('Connection lost');
      return server.transport(action,payload);
    }});
  const [key]=queue.add([file()]); await started.promise; queue.remove(key); release.resolve(); await queue.whenIdle();
  assert.equal(server.saved.size,1);
  assert.equal(await queue.submit(()=>submits++),false); assert.equal(submits,0);
  offline=false; queue.retry(key); await queue.whenIdle();
  assert.equal(server.saved.size,0); assert.equal(queue.rows.get(key).state,'removed');
  assert.equal(await queue.submit(()=>submits++),true); assert.equal(submits,1);
});
test('explicit submission waits for uploads and runs only once', async () => {
  const server = backend(), started = deferred(), release = deferred(); let submits = 0;
  const queue = controller({transport: async (action, payload) => {if (action === 'upload') {started.resolve(); await release.promise;}
    return server.transport(action, payload);}});
  queue.add([file()]); await started.promise;
  const first = queue.submit(() => submits++), second = queue.submit(() => submits++);
  assert.equal(submits, 0); release.resolve(); await Promise.all([first, second]); assert.equal(submits, 1);
});
test('failed uploads block submission until retry or explicit removal', async () => {
  const server = backend(); let submits = 0;
  const queue = controller({transport: async (action, payload) => action === 'upload' ? {id: 'manifest-1', revision: 0,
    files: [], accepted_ids: [], errors: [{message: 'Not saved'}]} : server.transport(action, payload)});
  const [key] = queue.add([file()]); await queue.whenIdle();
  assert.equal(await queue.submit(() => submits++), false); assert.equal(submits, 0);
  queue.remove(key); await queue.whenIdle(); assert.equal(await queue.submit(() => submits++), true); assert.equal(submits, 1);
});
test('stale revisions cannot overwrite acknowledged state', async () => {
  const server = backend(); let stale = false;
  const queue = controller({transport: async (action, payload) => {
    if (stale) return {id: 'manifest-1', revision: 0, files: []};
    return server.transport(action, payload);
  }});
  queue.add([file()]); await queue.whenIdle(); assert.equal(queue.revision, 1);
  stale = true; const [key] = queue.add([file('later.txt')]); await queue.whenIdle();
  assert.equal(queue.revision, 1); assert.equal(queue.rows.get(key).state, 'error');
});
test('dispose stops late responses from repainting or clearing another form', async () => {
  const server = backend(), started = deferred(), release = deferred(); let paints = 0;
  const queue = controller({transport: async (action, payload) => {if (action === 'upload') {started.resolve(); await release.promise;}
    return server.transport(action, payload);}, changed: () => paints++});
  queue.add([file()]); await started.promise; queue.dispose(); const before = paints;
  release.resolve(); await queue.whenIdle(); assert.equal(paints, before);
});
test('restoring the form URL preserves scope, unrelated parameters and hash', () => {
  controller({transport: async () => ({})});
  const {recoveryUrl} = require(modulePath);
  assert.equal(recoveryUrl('https://example.invalid/tracking/7/?office=all&other=keep#tab-route', 'opaque-id'),
    'https://example.invalid/tracking/7/?office=all&other=keep&upload_draft=opaque-id#tab-route');
});
test('lost form-state acknowledgement is reconciled before explicit submit', async () => {
  const server = backend();
  const queue = controller({readValues: () => ({subject: ['Typed title']}), transport: async (action, payload) => {
    const result = await server.transport(action, payload);
    if (action === 'save') throw new TypeError('Saved, but response was lost');
    return result;
  }});
  queue.add([file()]); await queue.whenIdle();
  assert.equal(queue.revision, 2); assert.ok(server.calls.some(([action]) => action === 'read'));
});
test('separate inline-form slots survive another form updating the same page URL', () => {
  controller({transport: async () => ({})});
  const {recoveryUrl} = require(modulePath);
  const first = recoveryUrl('https://example.invalid/tracking/7/?office=all', 'remarks-id', 'upload_draft_tracking_remark_0');
  const next = new URL(recoveryUrl(first, 'version-id', 'upload_draft_tracking_version_2'));
  assert.equal(next.searchParams.get('upload_draft_tracking_remark_0'), 'remarks-id');
  assert.equal(next.searchParams.get('upload_draft_tracking_version_2'), 'version-id');
});
test('unconfirmed form state blocks submission until the connection recovers', async () => {
  const server = backend(); let disconnected = true, submits = 0;
  const queue = controller({readValues: () => ({subject: ['Typed title']}), transport: async (action, payload) => {
    if (action === 'read' && disconnected) throw new TypeError('Connection unavailable');
    const result = await server.transport(action, payload);
    if (action === 'save') throw new TypeError('Saved, but response was lost');
    return result;
  }});
  queue.add([file()]); await queue.whenIdle();
  assert.equal(await queue.submit(() => submits++), false); assert.equal(submits, 0);
  disconnected = false;
  assert.equal(await queue.submit(() => submits++), true); assert.equal(submits, 1); assert.equal(queue.revision, 2);
});
