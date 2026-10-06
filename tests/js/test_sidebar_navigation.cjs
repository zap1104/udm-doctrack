/* Run the production drawer controls with keyboard and responsive state. */
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const source = readFileSync(resolve(__dirname, '../../static/js/doctrack.js'), 'utf8');
const start = source.indexOf('  var sidebarToggle =');
const stop = source.indexOf('  document.querySelectorAll(".form-field--error', start);
const controls = source.slice(start, stop);

function harness(mobile = true) {
  const events = {}, windowEvents = {}, bodyClasses = new Set();
  const document = {
    activeElement: null,
    body: {classList: {
      contains: value => bodyClasses.has(value),
      toggle(value, enabled) { if (enabled) bodyClasses.add(value); else bodyClasses.delete(value); },
    }},
    addEventListener(name, handler) { events[name] = handler; },
  };
  function element(name) {
    const attrs = {}, handlers = {};
    return {
      name, attrs, handlers, hidden: false,
      setAttribute(key, value) { attrs[key] = value; },
      removeAttribute(key) { delete attrs[key]; },
      addEventListener(key, handler) { handlers[key] = handler; },
      getClientRects: () => [{}],
      focus() { document.activeElement = this; },
    };
  }
  const toggle = element('toggle'), first = element('brand'), close = element('close');
  const last = element('last link'), backdrop = element('backdrop'), main = element('main');
  const items = [first, close, last], sidebar = element('sidebar');
  sidebar.contains = element => items.includes(element);
  sidebar.querySelector = () => first;
  sidebar.querySelectorAll = selector => selector === 'a' ? [first, last] : items;
  document.activeElement = toggle;
  document.querySelector = selector => selector === '[data-sidebar-toggle]' ? toggle : main;
  document.querySelectorAll = () => [close, backdrop];
  document.getElementById = () => sidebar;
  const window = {
    matchMedia: () => ({matches: mobile}),
    setTimeout: handler => handler(),
    addEventListener(name, handler) { windowEvents[name] = handler; },
  };
  runInNewContext(controls, {document, window});
  function key(key, shiftKey = false, target = document.activeElement) {
    const event = {key, shiftKey, target, preventDefault() { this.prevented = true; }};
    events.keydown(event);
    return event;
  }
  return {document, toggle, first, close, last, backdrop, main, sidebar, bodyClasses, key,
    resize(value) { mobile = value; windowEvents.resize(); }};
}

test('closed mobile navigation is inaccessible while the page remains usable', () => {
  const h = harness();
  assert.equal(h.sidebar.inert, true);
  assert.equal(h.sidebar.attrs['aria-hidden'], 'true');
  assert.equal(h.main.inert, false);
  assert.equal(h.document.activeElement, h.toggle);
});

test('opening the mobile drawer disables the covered page and focuses navigation', () => {
  const h = harness();
  h.toggle.handlers.click();
  assert.equal(h.sidebar.inert, false);
  assert.notEqual(h.sidebar.attrs['aria-hidden'], 'true');
  assert.equal(h.main.inert, true);
  assert.equal(h.toggle.attrs['aria-expanded'], 'true');
  assert.equal(h.document.activeElement, h.first);
});

test('Tab and Shift+Tab wrap inside the open mobile drawer', () => {
  const h = harness();
  h.toggle.handlers.click();
  h.last.focus();
  assert.equal(h.key('Tab').prevented, true);
  assert.equal(h.document.activeElement, h.first);
  assert.equal(h.key('Tab', true).prevented, true);
  assert.equal(h.document.activeElement, h.last);
  h.close.focus();
  assert.notEqual(h.key('Tab').prevented, true, 'normal interior tab order remains native');
});

for (const closer of ['close', 'backdrop', 'Escape']) {
  test(`${closer} closes the drawer and returns focus to its launch button`, () => {
    const h = harness();
    h.toggle.handlers.click();
    if (closer === 'Escape') h.key('Escape');
    else h[closer].handlers.click();
    assert.equal(h.sidebar.inert, true);
    assert.equal(h.main.inert, false);
    assert.equal(h.toggle.attrs['aria-expanded'], 'false');
    assert.equal(h.document.activeElement, h.toggle);
    assert.notEqual(h.key('Tab').prevented, true, 'closed drawers do not trap the page');
  });
}

test('responsive transitions restore desktop navigation and leave page focus free', () => {
  const h = harness();
  h.toggle.handlers.click();
  h.resize(false);
  assert.equal(h.sidebar.inert, false);
  assert.notEqual(h.sidebar.attrs['aria-hidden'], 'true');
  assert.equal(h.main.inert, false);
  assert.equal(h.bodyClasses.has('sidebar-open'), false);
  assert.notEqual(h.key('Tab').prevented, true);
  h.resize(true);
  assert.equal(h.sidebar.inert, true);
});

test('desktop navigation never disables the main page', () => {
  const h = harness(false);
  assert.equal(h.sidebar.inert, false);
  h.toggle.handlers.click();
  assert.equal(h.main.inert, false);
  assert.equal(h.bodyClasses.has('sidebar-open'), false);
});

test('following a mobile navigation link releases the covered page', () => {
  const h = harness();
  h.toggle.handlers.click();
  h.last.handlers.click();
  assert.equal(h.sidebar.inert, true);
  assert.equal(h.main.inert, false);
  assert.equal(h.bodyClasses.has('sidebar-open'), false);
});

test('an idle-warning dialog keeps its own keyboard controls when the drawer is open', () => {
  const h = harness();
  h.toggle.handlers.click();
  const dialogButton = {closest: selector => selector === 'dialog[open]' ? {} : null};
  h.document.activeElement = dialogButton;
  assert.notEqual(h.key('Tab', false, dialogButton).prevented, true);
  assert.notEqual(h.key('Escape', false, dialogButton).prevented, true);
  assert.equal(h.document.activeElement, dialogButton);
});
