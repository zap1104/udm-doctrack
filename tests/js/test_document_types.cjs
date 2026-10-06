const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { runInNewContext } = require('node:vm');

function setup(values, initialMain = '') {
  const events = {};
  const selection = { selectedOptions: values.map(value => ({value, textContent: value})) };
  const mainField = { hidden: false };
  const main = {value: initialMain, required: false, replacements: 0, attributes: {}, options: [],
    replaceChildren(option) { this.options = [option]; this.replacements++; },
    add(option) { this.options.push(option); },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name] || null; },
    closest() { return null; }};
  const picker = {querySelector(selector) { return selector.includes('selection') ? selection : selector.includes('field') ? mainField : main; },
    addEventListener(name, fn) { events[name] = fn; }};
  const source = readFileSync(resolve(__dirname, '../../static/js/document-types.js'), 'utf8');
  runInNewContext(source, {document: {querySelectorAll: () => [picker]}, Option: function(text, value) {this.text = text; this.value = value;}});
  return {selection, main, mainField, events};
}

test('one type chooses its own main type and hides the extra choice', () => {
  const {main, mainField} = setup(['Memo']);
  assert.equal(main.value, 'Memo'); assert.equal(main.required, false); assert.equal(mainField.hidden, true);
});
test('adding a second type keeps the main type and exposes the required choice', () => {
  const state = setup(['Memo'], 'Memo');
  state.selection.selectedOptions.push({value: 'Letter', textContent: 'Letter'});
  state.events.change({target: state.selection});
  assert.equal(state.main.value, 'Memo'); assert.equal(state.main.required, true); assert.equal(state.mainField.hidden, false);
});
test('opening or changing the main dropdown does not rebuild its options', () => {
  const state = setup(['Memo', 'Letter'], 'Memo');
  const before = state.main.replacements;
  state.events.click({target: state.main}); state.main.value = 'Letter'; state.events.change({target: state.main});
  assert.equal(state.main.replacements, before); assert.equal(state.main.value, 'Letter');
});
test('clearing the enhanced selection clears its main type too', () => {
  const state = setup(['Memo', 'Letter'], 'Memo');
  state.selection.selectedOptions = [];
  state.events.click({target: {closest: () => ({})}});
  assert.equal(state.main.value, ''); assert.equal(state.main.required, false); assert.equal(state.mainField.hidden, true);
});
