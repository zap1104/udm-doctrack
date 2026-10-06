/* Exercise the production error-summary focus behavior. */
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');

function run({withSummary = true, enhanced = false, missingTarget = false} = {}) {
  const events = {};
  const link = {getAttribute: () => '#id_offices', addEventListener: (name, handler) => {events[name] = handler;}};
  let summaryFocused = false;
  let fieldFocused = false;
  let filterFocused = false;
  const filter = {focus: () => {filterFocused = true;}, closest: () => null, scrollIntoView: () => {}};
  const wrapper = {querySelector: () => filter};
  const field = {focus: () => {fieldFocused = true;}, closest: selector => enhanced && selector === '.multiselect' ? wrapper : null,
    matches: selector => selector === 'select', scrollIntoView: () => {}};
  const summary = {focus: () => {summaryFocused = true;}, querySelectorAll: () => [link]};
  const document = {querySelector: () => withSummary ? summary : null, getElementById: () => missingTarget ? null : field};
  let source = '';
  try { source = readFileSync(resolve(__dirname, '../../static/js/form-errors.js'), 'utf8'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  runInNewContext(source, {document, window: {}});
  let prevented = false;
  if (events.click) events.click({preventDefault: () => {prevented = true;}});
  return {summaryFocused, fieldFocused, filterFocused, prevented};
}

test('failed submission focuses its error summary and linked native field', () => {
  assert.deepEqual(run(), {summaryFocused: true, fieldFocused: true, filterFocused: false, prevented: true});
});
test('receiving-office error links focus the visible enhanced control', () => {
  assert.deepEqual(run({enhanced: true}), {summaryFocused: true, fieldFocused: false, filterFocused: true, prevented: true});
});
test('a page without validation errors leaves focus alone', () => {
  assert.deepEqual(run({withSummary: false}), {summaryFocused: false, fieldFocused: false, filterFocused: false, prevented: false});
});
test('missing targets leave the native anchor behavior available', () => {
  assert.deepEqual(run({missingTarget: true}), {summaryFocused: true, fieldFocused: false, filterFocused: false, prevented: false});
});
