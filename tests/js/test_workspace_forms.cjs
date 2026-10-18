const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../../static/js/workspace-forms.js'), 'utf8');

function harness({multiple = false, disabled = false, inputType = 'text', placeholder} = {}) {
  const elements = [], events = {}, changes = [];
  function element(tag) {
    const attrs = {}, listeners = {}, classes = new Set();
    const node = {tag, attrs, listeners, dataset: {}, children: [], textContent: '',
      classList: {add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value)},
      setAttribute(name, value) { attrs[name] = value; }, hasAttribute: name => name in attrs,
      appendChild(child) { if (child.parentNode) child.parentNode.children = child.parentNode.children.filter(x => x !== child); this.children.push(child); child.parentNode = child.parentElement = this; },
      insertBefore(child, before) { this.children.splice(this.children.indexOf(before), 0, child); child.parentNode = child.parentElement = this; },
      querySelector() { return this.children.find(x => x.tag === 'label'); },
      closest() { return null; }, contains(child) { return this.children.includes(child); },
      addEventListener(name, handler) { listeners[name] = handler; },
      dispatchEvent(event) { changes.push(event); if (listeners[event.type]) listeners[event.type](event); },
    };
    elements.push(node);
    return node;
  }
  const field = element('div'), label = element('label'), input = element('input');
  label.htmlFor = input.id = 'id_title'; label.textContent = 'Title required'; input.type = inputType;
  if (placeholder !== undefined) input.setAttribute('placeholder', placeholder);
  input.value = 'Existing value'; input.setAttribute('aria-describedby', 'id_title_helptext id_title_error');
  field.appendChild(label); field.appendChild(input);
  const uploadField = element('div'), file = element('input');
  file.multiple = multiple; file.disabled = disabled; file.files = [{name:'previous.pdf'}];
  uploadField.appendChild(file);
  const document = {createElement: element,
    querySelectorAll(selector) { return selector.includes('.form-field') ? (input.parentElement === field ? [input] : []) : [file]; },
    addEventListener(name, handler) { events[name] = handler; },
  };
  function Event(type, options) { this.type = type; Object.assign(this, options); }
  runInNewContext(source, {document, Event});
  const zone = elements.find(x => x.className === 'app-dropzone');
  return {input, label, file, field, zone, elements, events, changes};
}
function drag(files, types = ['Files']) {
  return {dataTransfer:{files, types}, preventDefault() { this.prevented = true; }};
}

test('floating labels preserve the same field, value, label and help/error relationships', () => {
  const h = harness({placeholder:'Document title'});
  assert.equal(h.input.parentElement.className, 'label-input');
  assert.equal(h.label.parentElement, h.input.parentElement);
  assert.equal(h.label.htmlFor, h.input.id);
  assert.equal(h.input.value, 'Existing value');
  assert.equal(h.input.attrs.placeholder, 'Document title');
  assert.equal(h.input.attrs['aria-describedby'], 'id_title_helptext id_title_error');
  h.events['htmx:afterSwap']();
  assert.equal(h.elements.filter(x => x.className === 'label-input').length, 1);
  assert.equal(h.elements.filter(x => x.className === 'app-dropzone').length, 1);
});

test('date controls keep their permanent label; empty text controls get only a placeholder sentinel', () => {
  assert.equal(harness({inputType:'date'}).input.parentElement.className, undefined);
  assert.equal(harness().input.attrs.placeholder, ' ');
});

test('a real dropped file updates the native field and invokes existing change checks', () => {
  const h = harness(), files = [{name:'report.pdf',size:32}];
  const event = drag(files);
  h.zone.listeners.drop(event);
  assert.equal(event.prevented, true);
  assert.equal(h.file.files, files);
  assert.equal(h.changes.length, 1);
  assert.equal(h.changes[0].type, 'change');
  assert.equal(h.changes[0].bubbles, true);
  assert.equal(h.elements.find(x => x.className === 'dropzone-status').textContent, 'Selected. Submit the form to continue.');
  assert.equal(h.zone.listeners.submit, undefined);
});

test('single-file fields retain their selection on multi-file drop, while multiple fields accept the files', () => {
  const files = [{name:'a.pdf'}, {name:'b.pdf'}], single = harness(), multi = harness({multiple:true});
  single.zone.listeners.drop(drag(files));
  assert.equal(single.file.files[0].name, 'previous.pdf');
  assert.equal(single.changes.length, 0);
  multi.zone.listeners.drop(drag(files));
  assert.equal(multi.file.files, files);
});

test('disabled fields cannot receive drops and unrelated drags are left alone', () => {
  const h = harness({disabled:true});
  h.zone.listeners.drop(drag([{name:'new.pdf'}]));
  assert.equal(h.file.files[0].name, 'previous.pdf');
  const text = drag([], ['text/plain']);
  h.zone.listeners.dragover(text);
  h.zone.listeners.drop(text);
  assert.equal(text.prevented, undefined);
});
