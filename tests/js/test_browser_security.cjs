/* Execute the production HTMX guards with both visible and hidden pages. */
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const source = readFileSync(resolve(__dirname, '../../static/js/doctrack.js'), 'utf8');
const securityModule = source.slice(source.indexOf('(function () {'), source.indexOf('\n})();') + '\n})();'.length);

function harness(visibilityState, withHtmx = true) {
  let listener;
  const document = {visibilityState, addEventListener(name, handler) {
    assert.equal(name, 'htmx:beforeRequest'); listener = handler;
  }};
  const window = withHtmx ? {htmx: {config: {allowEval:true, allowScriptTags:true, selfRequestsOnly:false}}} : {};
  runInNewContext(securityModule, {window, document});
  return {window, listener};
}

test('HTMX cannot evaluate expressions, run response scripts or request another origin', () => {
  const {window} = harness('visible');
  assert.equal(window.htmx.config.allowEval, false);
  assert.equal(window.htmx.config.allowScriptTags, false);
  assert.equal(window.htmx.config.selfRequestsOnly, true);
});

for (const state of ['visible', 'hidden']) {
  for (const polling of [true, false]) {
    test(`${state} page, notification polling ${polling}`, () => {
      const {listener} = harness(state);
      let prevented = false;
      listener({detail:{elt:{hasAttribute:name => name === 'data-notification-poll' && polling}},
        preventDefault() {prevented = true;}});
      assert.equal(prevented, state === 'hidden' && polling);
    });
  }
}

test('guards tolerate a missing HTMX CDN and incomplete event details', () => {
  const {listener} = harness('hidden', false);
  listener({preventDefault() {assert.fail('an unrelated event must not be blocked');}});
});
