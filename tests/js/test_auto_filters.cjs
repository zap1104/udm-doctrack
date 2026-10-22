const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../../static/js/auto-filters.js'),'utf8');

function harness(withHtmx = true, options = {}) {
  const events = {}, windowEvents = {}, timers = new Map(), requests = [], navigations = [];
  let nextTimer = 0;
  const forms = [];
  const message = {textContent:''};
  const retry = {hidden:true, addEventListener: (name, fn) => { retry[name] = fn; }};
  const statusClasses = new Set();
  const status = {hidden:true, classList:{
    toggle(name, enabled) { enabled ? statusClasses.add(name) : statusClasses.delete(name); },
    contains(name) { return statusClasses.has(name); },
  }, querySelector:s => s === '[data-filter-message]' ? message : retry};
  const main = {
    attrs:{}, querySelectorAll:s => s === 'form[data-auto-filter]' ? forms : [],
    contains:() => false,
    setAttribute(k,v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
  };
  const document = {getElementById:id => id === 'main-content' ? main : status,
    addEventListener(name,fn) { (events[name] ||= []).push(fn); }};
  const window = {
    location:{href:'http://test/tracking/',origin:'http://test',pathname:'/tracking/',assign:url => navigations.push(url)},
    setTimeout(fn) { const id = ++nextTimer; timers.set(id,fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(name,fn) { windowEvents[name] = fn; },
  };
  if (withHtmx) window.htmx = {process(){},trigger(element,event) { requests.push({element,event}); }};
  function form() {
    const fallback = {hidden:false};
    const attrs = {'data-filter-mode':options.mode || 'change', 'data-filter-sync':options.sync === undefined ? 'dashboard' : options.sync};
    const f = {elements:[],isConnected:true,method:'get',id:'form-' + forms.length,attrs,
      getAttribute:name => attrs[name] || null, hasAttribute:name => name in attrs,
      querySelectorAll:() => [fallback],matches:s => s === 'form' || s.includes('form[data-auto-filter]')};
    forms.push(f); return {f,fallback};
  }
  function field(f,name,value,type='select') {
    const result = {name,value,type,disabled:false,closest:() => f,matches:s => s.includes(type === 'select' ? 'select' : 'input')};
    f.elements.push(result); return result;
  }
  const first = form(), office = field(first.f,'office','2');
  const second = form(), month = field(second.f,'month','2026-10');
  field(second.f,'office','1','hidden');
  class DOMParser {
    parseFromString() { return {querySelector:() => ({}),querySelectorAll:() => [],documentElement:{outerHTML:'<main id="main-content"></main>'}}; }
  }
  runInNewContext(source,{window,document,WeakSet,URL,DOMParser});
  function emit(name,detail,target) { (events[name] || []).forEach(fn => fn({detail,target})); }
  return {window,forms,first,second,office,month,main,status,message,retry,requests,navigations,emit,field,
    tick() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); },
    restore() { windowEvents.popstate?.(); emit('htmx:historyRestore'); }};
}

test('missing HTMX keeps native controls available', () => {
  const h = harness(false);
  assert.equal(h.first.fallback.hidden,false);
  assert.equal(h.status.hidden,true);
});
test('rapid changes across forms issue only the last automatic request', () => {
  const h = harness();
  h.emit('change',null,h.office); h.emit('change',null,h.month); h.tick();
  const submissions = h.requests.filter(r => r.event === 'filter-change');
  assert.equal(submissions.length,1);
  assert.equal(submissions[0].element,h.second.f);
  assert.equal(h.first.fallback.hidden,true);
});

test('submit-only dropdown changes wait for Search and retain a native submit button', () => {
  const h = harness(true, {mode:'submit'});
  h.emit('change',null,h.office); h.emit('change',null,h.month); h.tick();
  assert.equal(h.requests.length,0);
  assert.equal(h.first.fallback.hidden,false);
});

for (const type of ['date','checkbox','range']) test(`${type} fields never independently submit a search`, () => {
  const h = harness();
  const field = h.field(h.first.f,'search-control','2026-10-21',type);
  h.emit('change',null,field); h.tick();
  assert.equal(h.requests.length,0);
});

test('independent forms do not merge unfinished values without an explicit sync group', () => {
  const h = harness(true, {sync:''});
  const detail = {elt:h.first.f,parameters:{office:'2',q:'applied query',status:['RECEIVED','IN_PROCESS']}};
  h.emit('htmx:configRequest',detail);
  assert.deepEqual(detail.parameters,{office:'2',q:'applied query',status:['RECEIVED','IN_PROCESS']});
});

test('a synchronization group excludes unrelated forms and their pending dates', () => {
  const h = harness();
  h.second.f.attrs['data-filter-sync'] = 'another-panel';
  const detail = {elt:h.first.f,parameters:{office:'2',month:'2026-09'}};
  h.emit('htmx:configRequest',detail);
  assert.deepEqual(detail.parameters,{office:'2',month:'2026-09'});
});
test('request parameters retain repeated stages while using current visible values across forms', () => {
  const h = harness();
  const detail = {elt:h.first.f, parameters:{office:'1',month:'2026-09',status:['RECEIVED','IN_PROCESS'],per_page:'100'}};
  h.emit('htmx:configRequest',detail);
  assert.deepEqual(detail.parameters,{office:'2',month:'2026-10',status:['RECEIVED','IN_PROCESS'],per_page:'100'});
});
test('POST and unrelated fields do not initiate automatic filtering', () => {
  const h = harness();
  h.emit('change',null,{closest:() => null}); h.tick();
  assert.equal(h.requests.length,0);
});
test('changing a filter cancels an in-flight response before the debounce runs', () => {
  const h = harness(), xhr = {status:200};
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr});
  assert.equal(h.main.attrs['aria-busy'],'true');
  h.emit('change',null,h.month);
  const detail = {xhr,shouldSwap:true};
  h.emit('htmx:beforeSwap',detail);
  assert.equal(detail.shouldSwap,false);
  assert.equal(h.requests.at(-1).event,'htmx:abort');
});
test('normal filter feedback stays available to screen readers without being shown', () => {
  const h = harness(), xhr = {status:200};
  assert.equal(h.status.hidden,false);
  assert.equal(h.status.classList.contains('visually-hidden'),true);
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr});
  assert.equal(h.status.classList.contains('visually-hidden'),true);
  assert.match(h.message.textContent,/Updating results/);
  h.emit('htmx:afterRequest',{xhr,successful:true});
  assert.equal(h.status.classList.contains('visually-hidden'),true);
  assert.equal(h.retry.hidden,true);
  assert.match(h.message.textContent,/Results updated/);
});
for (const code of [500, 403]) test(`a failed update (${code}) visibly offers retry and clears the busy state`, () => {
  const h = harness(), xhr = {status:code};
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr});
  h.emit('htmx:afterRequest',{xhr,successful:false});
  assert.equal(h.retry.hidden,false);
  assert.equal(h.status.classList.contains('visually-hidden'),false);
  assert.equal(h.status.classList.contains('is-error'),true);
  assert.equal(h.main.attrs['aria-busy'],undefined);
  assert.match(h.message.textContent,/Previous results are still shown/);
  h.retry.click();
  assert.equal(h.requests.at(-1).event,'filter-change');
  const nextXhr = {status:200};
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr:nextXhr});
  assert.equal(h.status.classList.contains('visually-hidden'),true);
  assert.equal(h.status.classList.contains('is-error'),false);
  h.emit('htmx:afterRequest',{xhr:nextXhr,successful:true});
  assert.equal(h.status.classList.contains('visually-hidden'),true);
  assert.equal(h.retry.hidden,true);
});
test('history restoration cancels unfinished selections and restores enhancement', () => {
  const h = harness();
  h.emit('change',null,h.office); h.restore(); h.tick();
  assert.equal(h.requests.some(r => r.event === 'filter-change'),false);
  assert.equal(h.first.fallback.hidden,true);
});

test('a sign-in redirect navigates even when its response contains main content', () => {
  const h = harness(), xhr = {status:200,responseURL:'http://test/accounts/login/?next=%2Ftracking%2F'};
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr});
  const detail = {xhr,shouldSwap:true,serverResponse:'<main id="main-content"><form method="post"></form></main>'};
  h.emit('htmx:beforeSwap',detail);
  assert.equal(detail.shouldSwap,false,'sign-in must not be inserted into the workspace');
  assert.deepEqual(h.navigations,['http://test/accounts/login/?next=%2Ftracking%2F']);
});

test('same-page responses update in place', () => {
  const h = harness(), xhr = {status:200,responseURL:'http://test/tracking/?office=2'};
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr});
  const detail = {xhr,shouldSwap:true,serverResponse:'<main id="main-content">Results</main>'};
  h.emit('htmx:beforeSwap',detail);
  assert.equal(detail.shouldSwap,true);
  assert.deepEqual(h.navigations,[]);
});

test('an obsolete sign-in response cannot override the latest selection', () => {
  const h = harness(), xhr = {status:200,responseURL:'http://test/accounts/login/'};
  h.emit('htmx:beforeRequest',{elt:h.first.f,xhr});
  h.emit('change',null,h.office);
  const detail = {xhr,shouldSwap:true};
  h.emit('htmx:beforeSwap',detail);
  assert.equal(detail.shouldSwap,false);
  assert.deepEqual(h.navigations,[]);
});
