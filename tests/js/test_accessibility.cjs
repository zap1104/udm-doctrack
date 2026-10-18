const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../../static/js/accessibility.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(fetcher) {
  const fields = {
    text_size: {value:'14'}, high_contrast: {checked:false}, motion: {value:'reduce',checked:false,dataset:{preferenceOn:'reduce',preferenceOff:'system'}},
    underline_links: {checked:false}, spacing: {value:'roomy',checked:false,dataset:{preferenceOn:'roomy',preferenceOff:'standard'}}, larger_targets: {checked:false}, csrfmiddlewaretoken: {value:'csrf-token'},
  };
  Object.entries(fields).forEach(([name, field]) => {field.name = name; field.dataset ||= {};});
  const events = {}, attrs = {}, requests = [], panelEvents = {};
  const account = {focused:false, focus() {this.focused = true;}};
  const panel = {addEventListener(name, fn) {panelEvents[name] = fn;}};
  const status = {textContent:''}, save = {hidden:false};
  const retry = {hidden:true, addEventListener(name, fn) {this[name] = fn;}};
  const form = {
    // Submit buttons named "action" mask the native form.action property.
    action:{toString:()=> '[object RadioNodeList]'},
    getAttribute:name=>name === 'action' ? '/accounts/accessibility/' : null,
    elements:{namedItem:name=>fields[name]},
    closest:()=>panel,
    addEventListener(name, fn) {events[name] = fn;},
    setAttribute(name, value) {attrs[name] = value;}, removeAttribute(name) {delete attrs[name];},
    querySelector(selector) {return selector.includes('status') ? status : selector.includes('retry') ? retry : save;},
  };
  const root = {attrs:{}, setAttribute(name,value) {this.attrs[name] = value;}};
  const fetch = async (url, options) => {
    requests.push({url, ...options});
    if (fetcher) return fetcher(url, options);
    return {ok:true, redirected:false, json:async()=>({preferences:{text_size:'14'}})};
  };
  runInNewContext(source, {document:{querySelector:selector=>selector.includes('topbar-account') ? account : form, documentElement:root}, window:{fetch, URLSearchParams}});
  function change(name, value) {
    if (fields[name].dataset.preferenceOn) fields[name].checked = value === true || value === fields[name].dataset.preferenceOn;
    else if ('checked' in fields[name]) fields[name].checked = value;
    else fields[name].value = value;
    events.change({target:fields[name]});
  }
  function reset() {events.submit({preventDefault(){}, submitter:{hasAttribute:()=>true}});}
  return {fields, attrs, requests, root, status, retry, save, change, reset, panelEvents, account};
}

test('choices apply immediately and save with CSRF to the account endpoint', async () => {
  const h = harness();
  h.change('text_size', '18');
  assert.equal(h.root.attrs['data-a11y-text-size'], '18');
  await tick();
  h.change('high_contrast', true); await tick();
  h.change('underline_links', true); await tick();
  h.change('motion', 'reduce'); await tick();
  h.change('spacing', 'roomy'); await tick();
  h.change('larger_targets', true); await tick();
  assert.equal(h.root.attrs['data-a11y-contrast'], 'high');
  assert.equal(h.root.attrs['data-a11y-links'], 'underline');
  assert.equal(h.root.attrs['data-a11y-motion'], 'reduce');
  assert.equal(h.root.attrs['data-a11y-spacing'], 'roomy');
  assert.equal(h.root.attrs['data-a11y-targets'], 'large');
  const request = h.requests.at(-1);
  assert.equal(request.url, '/accounts/accessibility/');
  assert.equal(request.method, 'POST');
  assert.equal(request.body.get('csrfmiddlewaretoken'), 'csrf-token');
  assert.equal(request.body.get('spacing'), 'roomy');
  assert.equal(request.body.get('larger_targets'), 'true');
  assert.equal(request.credentials, 'same-origin');
  assert.equal(h.status.textContent, 'Saved.');
  assert.equal(h.save.hidden, true);
});

test('reset during an in-flight save is persisted after that older request', async () => {
  let resolveFirst;
  let count = 0;
  const response = {ok:true, json:async()=>({preferences:{}})};
  const h = harness(() => ++count === 1 ? new Promise(resolve=>{resolveFirst=resolve;}) : Promise.resolve(response));
  h.change('text_size', '18');
  h.change('high_contrast', true);
  h.change('larger_targets', true);
  h.reset();
  assert.equal(h.requests.length, 1);
  assert.equal(h.root.attrs['data-a11y-text-size'], '14');
  assert.equal(h.root.attrs['data-a11y-motion'], 'system');
  assert.equal(h.fields.high_contrast.checked, false);
  assert.equal(h.fields.motion.checked, false);
  assert.equal(h.fields.spacing.checked, false);
  assert.equal(h.fields.motion.value, 'reduce');
  assert.equal(h.fields.spacing.value, 'roomy');
  assert.equal(h.fields.larger_targets.checked, false);
  assert.equal(h.root.attrs['data-a11y-targets'], 'standard');
  resolveFirst(response); await tick();
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].body.get('action'), 'reset');
  assert.equal(h.requests[1].body.get('text_size'), '14');
  assert.equal(h.status.textContent, 'Defaults restored.');
});

test('a failed save retains the newest choice for retry and reports it as unsaved', async () => {
  let rejectFirst;
  let count = 0;
  const h = harness(() => ++count === 1 ? new Promise((_, reject)=>{rejectFirst=reject;}) :
    Promise.resolve({ok:true,json:async()=>({preferences:{}})}));
  h.change('text_size', '16');
  h.change('text_size', '18');
  rejectFirst(new Error('offline')); await tick();
  assert.equal(h.retry.hidden, false);
  assert.match(h.status.textContent, /aren’t saved/);
  assert.equal(h.root.attrs['data-a11y-text-size'], '18');
  h.retry.click(); await tick();
  assert.equal(h.requests[1].body.get('text_size'), '18');
  assert.equal(h.status.textContent, 'Saved.');
  assert.equal(h.retry.hidden, true);
  assert.equal(h.attrs['aria-busy'], undefined);
});

test('an expired-session redirect is never reported as a successful save', async () => {
  const h = harness(async()=>({ok:true,redirected:true,json:async()=>({})}));
  h.change('spacing', 'roomy'); await tick();
  assert.equal(h.retry.hidden, false);
  assert.match(h.status.textContent, /sign in again/);
});

test('pages without a settings form keep their existing behavior', () => {
  runInNewContext(source, {document:{querySelector:()=>null}, window:{}});
});

test('closing the panel restores focus to the visible account-menu button', () => {
  const h = harness();
  h.panelEvents['hidden.bs.offcanvas']();
  assert.equal(h.account.focused, true);
});

test('choice switches save their canonical off values when unchecked', async () => {
  const h = harness();
  h.change('motion', true); await tick();
  h.change('spacing', true); await tick();
  assert.equal(h.root.attrs['data-a11y-motion'], 'reduce');
  assert.equal(h.root.attrs['data-a11y-spacing'], 'roomy');
  h.change('motion', false); await tick();
  h.change('spacing', false); await tick();
  assert.equal(h.requests.at(-1).body.get('motion'), 'system');
  assert.equal(h.requests.at(-1).body.get('spacing'), 'standard');
  assert.equal(h.root.attrs['data-a11y-motion'], 'system');
  assert.equal(h.root.attrs['data-a11y-spacing'], 'standard');
});

test('smaller text saves and reset restores the default text size', async () => {
  const h = harness();
  h.change('text_size', '12'); await tick();
  assert.equal(h.root.attrs['data-a11y-text-size'], '12');
  assert.equal(h.requests.at(-1).body.get('text_size'), '12');
  h.reset(); await tick();
  assert.equal(h.root.attrs['data-a11y-text-size'], '14');
});
