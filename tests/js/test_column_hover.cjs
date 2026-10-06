const {existsSync, readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const path = resolve(__dirname, '../../static/js/column-hover.js');
const source = existsSync(path) ? readFileSync(path, 'utf8') : '';

function classes() {
  const values = new Set();
  return {
    contains: key => values.has(key),
    add: key => values.add(key),
    remove: key => values.delete(key),
    toggle(key, enabled) { if (enabled) values.add(key); else values.delete(key); },
  };
}

function harness(width = 640, count = 6) {
  let plotHeight = 220;
  const events = {}, windowEvents = {}, charts = [];
  const document = {
    addEventListener(name, fn) { (events[name] ||= []).push(fn); },
    querySelectorAll: () => charts,
  };
  const window = {
    addEventListener(name, fn) { windowEvents[name] = fn; },
    getComputedStyle: () => ({backgroundColor: 'rgb(193, 130, 0)'}),
  };
  const tipParts = {key: {style: {}}, value: {textContent: ''}, label: {textContent: ''}};
  const tip = {
    hidden: true, style: {},
    querySelector(selector) {
      return selector === '[data-column-tooltip-key]' ? tipParts.key :
        selector === '[data-column-tooltip-value]' ? tipParts.value : tipParts.label;
    },
    get offsetWidth() { return Math.min(240, parseFloat(this.style.maxWidth) || 240); },
    offsetHeight: 36,
  };
  const live = {textContent: ''};
  const frame = {
    getBoundingClientRect: () => ({left: 20, top: 80, width, height: 240}),
    querySelector: selector => selector === '[data-column-tooltip]' ? tip : live,
  };
  const chart = {
    classList: classes(), items: [], groups: [],
    matches: selector => selector === '[data-hover-columns]',
    closest: selector => selector === '[data-column-hover-frame]' ? frame : chart,
    querySelectorAll: selector => selector === '[data-column-hover]' ? chart.items : chart.groups,
    getBoundingClientRect: () => ({left: 20, top: 80, width, height: plotHeight}),
    contains: target => target === chart || chart.items.includes(target),
  };
  for (let i = 0; i < count; i++) {
    const attrs = {
      'data-column-label': ['Created', 'Handovers', 'Completed'][i % 3],
      'data-column-month': i < 3 ? 'September 2026' : 'October 2026',
      'data-column-value': String(100 + i), 'data-column-series': 'handover', title: 'Native fallback',
    };
    const bar = {getBoundingClientRect: () => ({left: 20 + i * width / count, top: 120, width: 12, height: 120})};
    const item = {
      attrs, classList: classes(), isConnected: true,
      getAttribute: key => attrs[key],
      removeAttribute: key => { delete attrs[key]; },
      querySelector: () => bar,
      closest: selector => selector === '[data-column-hover]' ? item : chart,
      getBoundingClientRect: bar.getBoundingClientRect,
    };
    chart.items.push(item);
  }
  chart.groups = [{removeAttribute() {}}];
  charts.push(chart);
  runInNewContext(source, {document, window});
  const outside = {closest: () => null};
  function dispatch(name, target, extra = {}) {
    const event = {target, preventDefault() { this.prevented = true; }, ...extra};
    (events[name] || []).forEach(fn => fn(event));
    return event;
  }
  return {chart, charts, tip, tipParts, live, outside, dispatch, windowEvents,
    setWidth: w => { width = w; }, setPlotHeight: h => { plotHeight = h; }};
}

test('hover shows the exact category, month, value and key, then clears on exit', () => {
  const h = harness();
  const bar = h.chart.items[1];
  h.dispatch('pointermove', bar, {pointerType: 'mouse', clientX: 160, clientY: 160});
  assert.equal(h.tip.hidden, false);
  assert.equal(h.tipParts.value.textContent, '101');
  assert.equal(h.tipParts.label.textContent, 'Handovers · September 2026');
  assert.equal(h.tipParts.key.style.backgroundColor, 'rgb(193, 130, 0)');
  assert.ok(bar.classList.contains('is-active'));
  assert.ok(h.chart.classList.contains('has-active-column'));
  assert.equal(h.live.textContent, '', 'mouse movement does not spam the live region');
  assert.equal(bar.attrs.title, undefined, 'only the enhanced chart removes duplicate native tips');
  h.dispatch('pointerout', bar, {pointerType: 'mouse', relatedTarget: h.outside});
  assert.equal(h.tip.hidden, true);
  assert.ok(!h.chart.classList.contains('has-active-column'));
});

test('keyboard walks bars, announces their figures and dismisses with Escape', () => {
  const h = harness();
  h.dispatch('focusin', h.chart);
  assert.ok(h.chart.items.at(-1).classList.contains('is-active'));
  assert.equal(h.live.textContent, 'Completed · October 2026: 105');
  assert.equal(h.dispatch('keydown', h.chart, {key: 'Home'}).prevented, true);
  assert.ok(h.chart.items[0].classList.contains('is-active'));
  h.dispatch('keydown', h.chart, {key: 'ArrowRight'});
  assert.ok(h.chart.items[1].classList.contains('is-active'));
  h.dispatch('keydown', h.chart, {key: 'End'});
  h.dispatch('keydown', h.chart, {key: 'ArrowRight'});
  assert.ok(h.chart.items.at(-1).classList.contains('is-active'));
  h.dispatch('keydown', h.chart, {key: 'Escape'});
  assert.equal(h.tip.hidden, true);
  h.dispatch('keydown', h.chart, {key: 'ArrowLeft'});
  assert.equal(h.tip.hidden, false);
  h.dispatch('focusout', h.chart, {relatedTarget: h.outside});
  assert.equal(h.tip.hidden, true);
});

test('touch opens the tapped bar, toggles it closed and dismisses outside', () => {
  const h = harness();
  const bar = h.chart.items.at(-1);
  h.dispatch('pointerdown', bar, {pointerType: 'touch'});
  h.dispatch('focusin', h.chart);
  assert.equal(h.tip.hidden, true, 'pointer focus must not preselect the last bar');
  h.dispatch('click', bar, {clientX: 620, clientY: 180});
  assert.equal(h.tip.hidden, false);
  h.dispatch('pointerdown', bar, {pointerType: 'touch'});
  h.dispatch('click', bar, {clientX: 620, clientY: 180});
  assert.equal(h.tip.hidden, true);
  h.dispatch('click', h.chart.items[0], {clientX: 22, clientY: 100});
  assert.equal(h.tip.hidden, false);
  h.dispatch('click', h.outside);
  assert.equal(h.tip.hidden, true);
});

for (const width of [120, 240, 640, 1440]) {
  test(`tooltip stays inside the chart at both edges, width ${width}`, () => {
    const h = harness(width);
    for (const [bar, x, y] of [[h.chart.items[0], 20, 80], [h.chart.items.at(-1), 20 + width, 300]]) {
      h.dispatch('pointermove', bar, {pointerType: 'mouse', clientX: x, clientY: y});
      assert.equal(h.tip.hidden, false);
      assert.ok(parseFloat(h.tip.style.left) >= 8);
      assert.ok(parseFloat(h.tip.style.left) + h.tip.offsetWidth <= width - 8);
      assert.ok(parseFloat(h.tip.style.top) >= 8);
      assert.ok(parseFloat(h.tip.style.top) + h.tip.offsetHeight <= 220 - 8);
    }
  });
}

test('Escape stays dismissed while the pointer remains over the same bar', () => {
  const h = harness();
  const point = {pointerType: 'mouse', clientX: 60, clientY: 130};
  h.dispatch('pointermove', h.chart.items[0], point);
  h.dispatch('keydown', h.chart, {key: 'Escape'});
  h.dispatch('pointermove', h.chart.items[0], point);
  assert.equal(h.tip.hidden, true);
  h.dispatch('pointermove', h.chart.items[1], point);
  assert.equal(h.tip.hidden, false);
});

test('zero values and label-like markup are treated as text', () => {
  const h = harness();
  const bar = h.chart.items[0];
  bar.attrs['data-column-value'] = '0';
  bar.attrs['data-column-label'] = '<img onerror=alert(1)>';
  bar.querySelector = () => null;
  h.dispatch('pointermove', bar, {pointerType: 'mouse', clientX: 30, clientY: 280});
  assert.equal(h.tipParts.value.textContent, '0');
  assert.equal(h.tipParts.label.textContent, '<img onerror=alert(1)> · September 2026');
  assert.equal(h.tipParts.key.style.backgroundColor, 'var(--status-pending)');
});

test('resize repositions the selected tip, while swaps and print clear obsolete selections', () => {
  const h = harness();
  h.dispatch('focusin', h.chart);
  h.setWidth(140);
  h.windowEvents.resize();
  assert.ok(parseFloat(h.tip.style.left) + h.tip.offsetWidth <= 132);
  h.chart.items.at(-1).isConnected = false;
  h.dispatch('htmx:afterSwap', h.outside);
  assert.equal(h.tip.hidden, true);
  h.chart.items.at(-1).isConnected = true;
  h.dispatch('focusin', h.chart);
  h.windowEvents.beforeprint();
  assert.equal(h.tip.hidden, true);
});

test('switching to the existing phone table presentation clears the chart tooltip', () => {
  const h = harness();
  h.dispatch('focusin', h.chart);
  assert.equal(h.tip.hidden, false);
  h.setPlotHeight(0);
  h.windowEvents.resize();
  assert.equal(h.tip.hidden, true);
  assert.ok(!h.chart.classList.contains('has-active-column'));
});
