const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../../static/js/workspace-controls.js'), 'utf8');

function harness({value = '', finePointer = true, focused = false, formPresent = true} = {}) {
  function element() {
    const attrs = {}, handlers = {}, classes = new Set();
    return {attrs, handlers, classes, dataset: {}, hidden: true,
      classList: {add: key => classes.add(key), toggle(key, on) { if (on) classes.add(key); else classes.delete(key); }},
      setAttribute(key, value) { attrs[key] = value; },
      removeAttribute(key) { delete attrs[key]; },
      addEventListener(key, handler) { (handlers[key] ||= []).push(handler); },
      fire(key, event = {}) { (handlers[key] || []).forEach(handler => handler(event)); return event; },
      focus() { document.activeElement = this; this.fire('focus'); },
    };
  }
  const input = element(), trigger = element(), form = element(), events = {};
  input.value = value;
  form.querySelector = selector => selector.includes('input') ? input : trigger;
  form.contains = node => [input, trigger, form].includes(node);
  const document = {activeElement: focused ? input : null,
    querySelector: () => formPresent ? form : null,
    addEventListener(name, handler) { events[name] = handler; },
  };
  const media = {matches: finePointer, addEventListener(name, handler) { this[name] = handler; }};
  runInNewContext(source, {document, window: {matchMedia: () => media}});
  return {input, trigger, form, document, events, media};
}
const key = value => ({key: value, preventDefault() { this.prevented = true; }});

test('hover opens search on any page without stealing focus; clicking is not its expansion trigger', () => {
  const h = harness();
  assert.equal(h.input.tabIndex, -1);
  h.trigger.fire('click');
  assert.equal(h.form.classes.has('is-open'), false);
  h.form.fire('pointerenter', {pointerType: 'mouse'});
  assert.equal(h.form.classes.has('is-open'), true);
  assert.equal(h.document.activeElement, null);
  assert.equal(h.input.attrs['aria-hidden'], undefined);
  h.form.fire('pointerleave');
  assert.equal(h.form.classes.has('is-open'), false);
});

test('hover stays expanded while typing or when a query exists', () => {
  const h = harness();
  h.form.fire('pointerenter', {pointerType: 'mouse'});
  h.trigger.fire('click');
  h.form.fire('pointerleave');
  assert.equal(h.document.activeElement, h.input);
  assert.equal(h.form.classes.has('is-open'), true);
  h.input.value = 'memo';
  h.document.activeElement = null;
  h.form.fire('focusout', {relatedTarget: null});
  assert.equal(h.form.classes.has('is-open'), true);
});

test('keyboard activation and the shared slash shortcut open the actual input', () => {
  for (const value of ['Enter', ' ']) {
    const h = harness();
    assert.equal(h.trigger.fire('keydown', key(value)).prevented, true);
    assert.equal(h.document.activeElement, h.input);
    assert.equal(h.input.tabIndex, 0);
  }
  const h = harness();
  h.input.focus();
  assert.equal(h.form.classes.has('is-open'), true);
});

test('Escape returns focus and keeps text; Enter submission remains native', () => {
  const h = harness({value: 'purchase request'});
  assert.equal(h.input.fire('keydown', key('Enter')).prevented, undefined);
  assert.equal(h.form.handlers.submit, undefined);
  assert.equal(h.input.fire('keydown', key('Escape')).prevented, true);
  assert.equal(h.document.activeElement, h.trigger);
  assert.equal(h.trigger.attrs['aria-expanded'], 'false');
  assert.equal(h.input.value, 'purchase request');
});

test('touch devices have a visible, tabbable input without a tap to expand', () => {
  const h = harness({finePointer: false});
  assert.equal(h.trigger.attrs['aria-expanded'], 'true');
  assert.equal(h.input.tabIndex, 0);
  h.input.fire('keydown', key('Escape'));
  assert.equal(h.form.classes.has('is-open'), true);
});

test('pointer capability changes update visibility and HTMX does not duplicate handlers', () => {
  const h = harness();
  h.media.matches = false;
  h.media.change();
  assert.equal(h.form.classes.has('is-open'), true);
  h.media.matches = true;
  h.media.change();
  assert.equal(h.form.classes.has('is-open'), false);
  h.events['htmx:afterSwap']();
  h.events['htmx:historyRestore']();
  assert.equal(h.form.handlers.pointerenter.length, 1);
  assert.equal(h.trigger.handlers.keydown.length, 1);
  harness({formPresent: false});
});

test('restored query and focused inputs remain visible on initialization', () => {
  assert.equal(harness({value:'restored'}).form.classes.has('is-open'), true);
  assert.equal(harness({focused:true}).form.classes.has('is-open'), true);
});
