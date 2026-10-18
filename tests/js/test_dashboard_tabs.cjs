const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/dashboard-tabs.js'), 'utf8');

function setup({saved, hash = '', search = '', blocked = false} = {}) {
  const handlers = {};
  const disclosures = [{open: false}, {open: true}];
  let root;
  const makeRoot = () => {
    const parent = {
      dataset: {},
      tabs: [], panels: [], nav: {hidden: true},
      querySelectorAll(selector) { return selector.includes('dashboard-tab]') ? this.tabs : this.panels; },
      querySelector() { return this.nav; },
      contains(target) { return this.panels.includes(target); },
    };
    for (const name of ['overview', 'statistics']) {
      parent.tabs.push({
        dataset: {dashboardTab: name}, attributes: {}, tabIndex: -1,
        setAttribute(name, value) { this.attributes[name] = value; },
        closest(selector) { return selector.includes('workspace') ? parent : this; },
        focus() { this.focused = true; },
      });
      parent.panels.push({
        dataset: {dashboardPanel: name}, hidden: false,
        closest() { return this; }, scrollIntoView() { this.scrolled = true; },
      });
    }
    return parent;
  };
  root = makeRoot();
  const location = {hash, search};
  const document = {
    readyState: 'complete',
    querySelectorAll(selector) { return selector.includes('details.dashboard-disclosure') ? disclosures : (root ? [root] : []); },
    querySelector() { return root; },
    getElementById(id) { return id === 'turnaround-panel' ? root.panels[1] : null; },
    addEventListener(name, handler) { handlers[name] = handler; },
  };
  let resize = 0;
  const context = {
    document, location,
    sessionStorage: {
      getItem() { if (blocked) throw Error('blocked'); return saved; },
      setItem(_, value) { if (blocked) throw Error('blocked'); saved = value; },
    },
    window: {addEventListener(name, handler) { handlers[name] = handler; }, dispatchEvent() { resize++; }},
    requestAnimationFrame(fn) { fn(); }, Event: class {},
  };
  vm.runInNewContext(source, context);
  return {
    get root() { return root; }, get saved() { return saved; }, get resize() { return resize; },
    location, disclosures,
    event(name, event = {}) { handlers[name](event); },
    replace() { root = makeRoot(); handlers['htmx:afterSwap'](); },
  };
}

test('overview opens by default and charts remain rendered but hidden', () => {
  const app = setup();
  assert.equal(app.root.nav.hidden, false);
  assert.deepEqual(app.root.panels.map(panel => panel.hidden), [false, true]);
  assert.equal(app.root.tabs[0].attributes['aria-selected'], 'true');
});
test('click selects statistics, persists it and asks existing charts to resize', () => {
  const app = setup();
  const before = app.resize;
  app.event('click', {target: app.root.tabs[1]});
  assert.equal(app.saved, 'statistics');
  assert.deepEqual(app.root.panels.map(panel => panel.hidden), [true, false]);
  assert.equal(app.resize, before + 1);
});
test('arrow keys wrap and Home/End select and focus the associated tab', () => {
  const app = setup();
  const press = (index, key) => app.event('keydown', {target: app.root.tabs[index], key, preventDefault() {}});
  press(0, 'ArrowLeft');
  assert.equal(app.saved, 'statistics');
  assert.equal(app.root.tabs[1].focused, true);
  press(1, 'ArrowRight');
  assert.equal(app.saved, 'overview');
  press(0, 'End');
  assert.equal(app.saved, 'statistics');
  press(1, 'Home');
  assert.deepEqual(app.root.tabs.map(tab => tab.tabIndex), [0, -1]);
});
test('selection survives full panel replacement and history restoration', () => {
  const app = setup({saved: 'statistics'});
  app.replace();
  assert.deepEqual(app.root.panels.map(panel => panel.hidden), [true, false]);
  app.event('htmx:historyRestore');
  assert.equal(app.root.nav.hidden, false);
  assert.equal(app.saved, 'statistics');
});
test('blocked storage and invalid saved values still give a usable overview', () => {
  for (const options of [{blocked: true}, {saved: 'unknown'}]) {
    const app = setup(options);
    assert.deepEqual(app.root.panels.map(panel => panel.hidden), [false, true]);
  }
});
test('analytics links and turnaround anchors reveal statistics', () => {
  for (const options of [{search: '?month=2026-10'}, {saved: 'overview', hash: '#turnaround-panel'}]) {
    assert.equal(setup(options).saved, 'statistics');
  }
  const app = setup();
  app.location.hash = '#turnaround-panel';
  app.event('hashchange');
  assert.equal(app.saved, 'statistics');
  assert.equal(app.root.panels[1].scrolled, true);
  app.location.hash = '#%broken';
  assert.doesNotThrow(() => app.event('hashchange'));
});

test('printing reveals optional details and restores each previous open state', () => {
  const app = setup();
  app.event('beforeprint');
  assert.deepEqual(app.disclosures.map(detail => detail.open), [true, true]);
  app.event('beforeprint');
  app.event('afterprint');
  assert.deepEqual(app.disclosures.map(detail => detail.open), [false, true]);
});
