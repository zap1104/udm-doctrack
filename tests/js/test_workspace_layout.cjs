const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../../static/js/workspace-layout.js'), 'utf8');

function harness({interactiveHeader = false, chart = false, empty = false} = {}) {
  function node(textContent = '') {
    return {textContent, dataset: {}, attrs: {}, classes: new Set(), children: [],
      classList: {add(value) { this.owner.classes.add(value); }},
      setAttribute(key, value) { this.attrs[key] = value; },
      hasAttribute(key) { return key in this.attrs; },
      insertBefore(child) { this.children.unshift(child); },
      querySelector() { return null; },
    };
  }
  const headers = ['Select for receipt', 'Document', 'Status', 'Action'].map(node);
  const cells = ['', 'A long full subject with its tracking number', 'Pending Receipt', 'Confirm Receipt'].map(node);
  const checkbox = {checked: true, name: 'record_ids', value: '631'};
  cells[0].children = [checkbox];
  cells[3].querySelector = () => ({type:'button'});
  const row = node(); row.children = empty ? [node('No records')] : cells;
  if (empty) row.children[0].attrs.colspan = '4';
  const headerRow = node(), head = node(), body = node(), table = node();
  table.classList.owner = table;
  head.querySelector = () => interactiveHeader ? {} : null;
  head.querySelectorAll = selector => selector === 'th' ? headers : [headerRow];
  body.querySelectorAll = () => [row];
  row.querySelector = () => empty ? row.children[0] : null;
  table.closest = () => chart ? {} : null;
  table.querySelector = selector => selector === 'thead' ? head : body;
  const events = {};
  const document = {querySelectorAll: () => [table], createElement: () => node(),
    addEventListener(name, handler) { events[name] = handler; }};
  runInNewContext(source, {document});
  return {table, headers, row, cells, checkbox, events};
}

test('responsive labels keep table relationships, action text and the native selected checkbox', () => {
  const h = harness();
  assert.equal(h.table.attrs.role, 'table');
  assert.equal(h.headers[1].attrs.role, 'columnheader');
  assert.equal(h.cells[1].attrs.role, 'cell');
  assert.equal(h.cells[0].dataset.cellKind, 'selection');
  assert.equal(h.cells[1].dataset.cellKind, 'primary');
  assert.equal(h.cells[3].dataset.cellKind, 'actions');
  assert.equal(h.cells[2].children[0].textContent, 'Status');
  assert.equal(h.cells[2].children[0].attrs['aria-hidden'], 'true');
  assert.equal(h.cells[0].children[1], h.checkbox);
  assert.equal(h.checkbox.checked, true);
  assert.equal(h.checkbox.name, 'record_ids');
  assert.equal(h.cells[3].textContent, 'Confirm Receipt');
});

test('HTMX refreshes cannot add duplicate visible labels', () => {
  const h = harness();
  h.events['htmx:afterSwap'](); h.events['htmx:historyRestore']();
  assert.equal(h.cells[1].children.length, 1);
});

test('chart tables and interactive headers keep their current presentation', () => {
  for (const options of [{chart:true}, {interactiveHeader:true}]) {
    const h = harness(options);
    assert.equal(h.table.dataset.recordLayoutReady, undefined);
    assert.equal(h.cells[1].children.length, 0);
  }
});

test('empty-state colspan content remains a single labelled-table row', () => {
  const h = harness({empty:true});
  assert.equal(h.row.children.length, 1);
  assert.equal(h.row.children[0].attrs.colspan, '4');
  assert.equal(h.row.children[0].textContent, 'No records');
});
