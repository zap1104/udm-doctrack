/* Test the production event handlers without browser automation or packages. */
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const source = readFileSync(resolve(__dirname, '../../static/js/doctrack.js'), 'utf8');
const anchor = source.indexOf('function monthsOf(');
assert.ok(anchor > 0, 'find the production turnaround module');
const start = source.lastIndexOf('(function () {', anchor);
const end = source.indexOf('\n})();', anchor) + '\n})();'.length;
const moduleSource = source.slice(start, end);

function classes(initial = []) {
  const values = new Set(initial);
  return {
    contains: key => values.has(key),
    remove: key => values.delete(key),
    toggle(key, enabled) {
      if (enabled === undefined) enabled = !values.has(key);
      if (enabled) values.add(key); else values.delete(key);
    },
  };
}

function harness(length = 12, width = 640, left = 20) {
  const events = {}, windowEvents = {}, boxes = [];
  const document = {
    addEventListener(name, fn) { (events[name] ||= []).push(fn); },
    querySelectorAll() { return boxes; },
  };
  const window = {addEventListener(name, fn) { windowEvents[name] = fn; }};
  function makeBox() {
    const live = {textContent: ''};
    const box = {
      classList: classes(), months: [], hovered: null,
      getBoundingClientRect: () => ({left, right: left + width, width}),
      querySelectorAll: () => box.months,
      querySelector(selector) {
        if (selector === '[data-trend-announce]') return live;
        return box.months.find(month => month.classList.contains('is-active')) ||
          (selector.includes(':hover') ? box.hovered : null);
      },
      matches: selector => selector === '[data-trend-hover]',
      closest: selector => selector === '[data-trend-hover]' ? box : null,
      contains: node => node === box || box.months.includes(node), live,
    };
    for (let index = 0; index < length; index++) {
      const tip = {
        style: {},
        get offsetWidth() { return Math.min(260, parseFloat(this.style.maxWidth) || 260); },
      };
      const month = {
        classList: classes(index >= length / 2 ? ['trend-hit--left'] : []), tip,
        querySelector: () => tip,
        getBoundingClientRect: () => ({left: left + index * width / length, width: width / length}),
        getAttribute: () => `Date ${index + 1}: verified average and sample count`,
        closest(selector) { return selector === '[data-trend-month]' ? month : box; },
      };
      box.months.push(month);
    }
    boxes.push(box);
    return box;
  }
  const box = makeBox();
  runInNewContext(moduleSource, {document, window});
  function dispatch(name, target, extra = {}) {
    const event = {target, preventDefault() { this.prevented = true; }, ...extra};
    (events[name] || []).forEach(fn => fn(event));
    return event;
  }
  return {box, boxes, makeBox, dispatch, windowEvents};
}

function active(box) { return box.months.filter(month => month.classList.contains('is-active')); }

for (const length of [1, 2, 3, 12, 28, 29, 30, 31]) {
  test(`keyboard navigation, bounds, live announcement and Escape (${length} dates)`, () => {
    const {box, dispatch} = harness(length);
    dispatch('focusin', box);
    assert.equal(active(box)[0], box.months.at(-1));
    assert.ok(box.live.textContent.includes(`Date ${length}`));
    for (const key of ['ArrowRight', 'End']) {
      assert.equal(dispatch('keydown', box, {key}).prevented, true);
      assert.equal(active(box)[0], box.months.at(-1));
    }
    dispatch('keydown', box, {key: 'Home'});
    for (let index = 0; index < length + 3; index++) dispatch('keydown', box, {key: 'ArrowLeft'});
    assert.equal(active(box)[0], box.months[0]);
    for (let index = 0; index < length + 3; index++) dispatch('keydown', box, {key: 'ArrowRight'});
    assert.equal(active(box)[0], box.months.at(-1));
    assert.equal(dispatch('keydown', box, {key: 'x'}).prevented, undefined);
    dispatch('keydown', box, {key: 'Escape'});
    assert.equal(active(box).length, 0);
    assert.equal(box.live.textContent, '');
    assert.equal(box.classList.contains('is-dismissed'), true);
    dispatch('pointermove', box.months[0], {pointerType: 'mouse'});
    assert.equal(box.classList.contains('is-dismissed'), false);
  });

  for (const width of [120, 180, 200, 260, 320, 768, 1440]) {
    for (const left of [0, 450]) {
      test(`tooltip remains inside plot: ${length} dates, width ${width}, offset ${left}`, () => {
        const {box, dispatch, windowEvents} = harness(length, width, left);
        for (const month of box.months) {
          dispatch('click', month);
          const actualLeft = month.getBoundingClientRect().left + parseFloat(month.tip.style.left);
          assert.ok(actualLeft >= left - 1e-8);
          assert.ok(actualLeft + month.tip.offsetWidth <= left + width + 1e-8);
          windowEvents.resize();
          assert.equal(active(box).length, 1);
          dispatch('click', month);
          assert.equal(active(box).length, 0);
          assert.equal(box.classList.contains('is-dismissed'), true);
        }
      });
    }
  }
}

test('mouse, touch, keyboard and outside clicks do not leave competing active popups', () => {
  const {box, makeBox, dispatch} = harness();
  const other = makeBox();
  dispatch('click', box.months[0]);
  dispatch('pointerover', box.months[4], {pointerType: 'touch'});
  assert.equal(active(box)[0], box.months[0]);
  dispatch('pointerover', box.months[4], {pointerType: 'mouse'});
  assert.equal(active(box).length, 0);
  dispatch('keydown', box, {key: 'Home'});
  dispatch('pointermove', box.months[4], {pointerType: 'mouse'});
  assert.equal(active(box).length, 0);
  dispatch('click', other.months[1]);
  assert.equal(active(box).length, 0);
  assert.equal(active(other).length, 1);
  dispatch('click', {});
  assert.equal(active(other).length, 0);
  dispatch('focusin', box);
  dispatch('focusout', box, {relatedTarget: {}});
  assert.equal(active(box).length, 0);
});

test('empty plots and replacement plots after HTMX are handled by delegated events', () => {
  const empty = harness(0);
  empty.dispatch('focusin', empty.box);
  empty.dispatch('keydown', empty.box, {key: 'ArrowLeft'});
  assert.equal(active(empty.box).length, 0);
  const {boxes, makeBox, dispatch} = harness(3);
  boxes.length = 0;
  const replacement = makeBox();
  dispatch('focusin', replacement);
  dispatch('keydown', replacement, {key: 'Home'});
  assert.equal(active(replacement)[0], replacement.months[0]);
});

test('the first pointer click on the last date opens it, then a second tap closes it', () => {
  for (const length of [1, 2, 12, 31]) {
    const {box, dispatch} = harness(length);
    const last = box.months.at(-1);
    dispatch('pointerdown', last);
    dispatch('focusin', box);
    assert.equal(active(box).length, 0);
    dispatch('click', last);
    assert.equal(active(box)[0], last);
    dispatch('pointerdown', last);
    dispatch('click', last);
    assert.equal(active(box).length, 0);
  }
});

test('ten thousand mixed interactions keep selection and announcements consistent', () => {
  const {box, dispatch} = harness(31);
  let seed = 17;
  for (let index = 0; index < 10000; index++) {
    seed = (seed * 48271) % 2147483647;
    const month = box.months[seed % 31];
    const action = seed % 7;
    if (action < 4) dispatch('keydown', box, {key: ['Home', 'End', 'ArrowLeft', 'ArrowRight'][action]});
    else if (action === 4) dispatch('click', month);
    else if (action === 5) dispatch('pointerover', month, {pointerType: 'mouse'});
    else dispatch('keydown', box, {key: 'Escape'});
    assert.ok(active(box).length <= 1);
    assert.equal(box.live.textContent, active(box)[0]?.getAttribute('aria-label') || '');
  }
});

test('mouse movement inside a selected date does not prevent the second click from closing it', () => {
  for (const length of [1, 2, 12, 31]) {
    const {box, dispatch} = harness(length);
    const selected = box.months.at(-1);
    dispatch('pointerover', selected, {pointerType: 'mouse'});
    dispatch('pointerdown', selected, {pointerType: 'mouse'});
    dispatch('focusin', box);
    dispatch('click', selected);
    dispatch('pointerover', selected, {pointerType: 'mouse'});
    dispatch('pointermove', selected, {pointerType: 'mouse'});
    assert.equal(active(box)[0], selected);
    dispatch('pointerdown', selected, {pointerType: 'mouse'});
    dispatch('click', selected);
    assert.equal(active(box).length, 0);
    assert.equal(box.classList.contains('is-dismissed'), true);
  }
});
