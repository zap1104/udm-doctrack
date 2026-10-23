'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('history enhancement keeps explicitly open saved/error forms accessible and still expands print history',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../../static/js/doctrack.js'),'utf8');
  const start=source.indexOf('  (function () {',source.indexOf('Folded history (<details'));
  const end=source.indexOf('  })();',start)+8;
  assert(start>=0 && end>start,'The production folded-history initializer must be present');
  const makeFold=(open,keep)=>({open,hasAttribute:name=>name==='data-keep-open' && keep});
  const history=makeFold(true,false), saved=makeFold(true,true), fresh=makeFold(false,false), listeners={};
  vm.runInNewContext(source.slice(start,end),{
    document:{querySelectorAll:()=>[history,saved,fresh]},
    window:{addEventListener:(name,callback)=>{listeners[name]=callback;}}
  });
  assert.equal(history.open,false); assert.equal(saved.open,true); assert.equal(fresh.open,false);
  listeners.beforeprint();
  assert([history,saved,fresh].every(fold=>fold.open));
});
