'use strict';
// Only static assets are served from local files. All HTML, filters, actions,
// sessions, CSRF checks and downloads use the real isolated Django server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const report = {origin:manifest.origin, checks:[], errors:[], httpErrors:[], failedRequests:[], navigations:[], filterRequests:[], screenshots:[], limitations:[
  'Chromium emulation; physical devices and screen readers were not tested.',
  'Storage is local and OCR/email service checks are simulated.',
  'Print dialog invocation and print CSS are checked; physical printer output is unverified.',
  '200% layout zoom is CSS zoom emulation, not browser chrome or operating system magnification.',
]};
const record = (name, details={}) => {
  report.checks.push({...details,name});
  fs.writeFileSync(manifest.output,JSON.stringify(report,null,2));
};
const vendorDir = process.env.AUDIT_VENDOR_DIR || 'C:/Users/USER/AppData/Local/Temp';
const vendor = {
  'bootstrap.min.css':'doctrack-bootstrap-5.3.3.min.css',
  'bootstrap.bundle.min.js':'doctrack-bootstrap-5.3.3.min.js',
  'htmx.min.js':'doctrack-htmx-1.9.12.min.js',
};
const mime = filename => filename.endsWith('.css') ? 'text/css' : filename.endsWith('.js') ? 'application/javascript' : filename.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream';
async function configure(context) {
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    let local;
    if (url.pathname.startsWith('/static/')) {
      const candidate = path.resolve(manifest.root, '.' + decodeURIComponent(url.pathname));
      if (candidate.startsWith(path.resolve(manifest.root) + path.sep) && fs.existsSync(candidate)) local = candidate;
    }
    const base = path.basename(url.pathname);
    if (vendor[base]) local = path.join(vendorDir, vendor[base]);
    if (local && fs.existsSync(local)) return route.fulfill({path:local,contentType:mime(local)});
    if (url.origin !== manifest.origin) throw new Error('Unexpected external request: ' + url.origin + url.pathname);
    return route.continue();
  });
}
const pendingByPage = new WeakMap();
async function ready(page) {
  await page.locator('#main-content').waitFor({state:'attached'});
  await page.waitForFunction(() => Boolean(window.htmx && window.bootstrap));
  await page.waitForFunction(() => [...document.querySelectorAll('[data-auto-filter] [data-filter-fallback]')].every(button=>button.hidden));
}
async function navigate(page, url) {
  const began=Date.now();
  const response=await page.goto(url,{waitUntil:'domcontentloaded'});
  assert.ok(response && response.ok(), 'Navigation HTTP failure: '+url+' '+response?.status());
  await ready(page);
  report.navigations.push({url:page.url(),milliseconds:Date.now()-began,readyState:await page.evaluate(()=>document.readyState),pending:[...(pendingByPage.get(page)||[])]});
}
async function signIn(browser, username) {
  const context = await browser.newContext({baseURL:manifest.origin,viewport:{width:1366,height:950}, acceptDownloads:true});
  await configure(context);
  const page = await context.newPage();
  page.setDefaultNavigationTimeout(45000);
  const pending=new Set(); pendingByPage.set(page,pending);
  page.on('request',request=>pending.add(request.url()));
  page.on('requestfinished',request=>pending.delete(request.url()));
  page.on('requestfailed',request=>{pending.delete(request.url());report.failedRequests.push({username,url:request.url(),error:request.failure()?.errorText});});
  page.on('response',response=>{if(response.status()>=400 && !response.headers()['x-audit-injected']) report.httpErrors.push({username,url:response.url(),status:response.status()});});
  page.on('pageerror', error => report.errors.push({username,message:error.message}));
  page.on('dialog', dialog => dialog.accept());
  await navigate(page,manifest.origin + '/accounts/login/');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill('TestPass123!');
  await Promise.all([page.waitForURL(url => !url.pathname.includes('/login/'),{waitUntil:'domcontentloaded'}), page.getByRole('button',{name:'Sign In',exact:true}).click()]);
  assert.equal(new URL(page.url()).pathname, '/');
  record('Real authentication and CSRF', {username});
  return {context,page};
}
async function submit(page) {
  await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.locator('#main-content form[method=post] button[type=submit]').last().click()]);
}
async function snapshot(page, html) {
  return page.evaluate(source => {
    const documentToRead = source === null ? document : new DOMParser().parseFromString(source, 'text/html');
    const main = documentToRead.querySelector('#main-content');
    const ids = [...main.querySelectorAll('a[href]')]
      .map(a => new URL(a.getAttribute('href'), location.origin).pathname)
      .filter(value => /^\/(?:tracking\/(?:external\/)?|documents\/)\d+\/$/.test(value));
    return {ids:[...new Set(ids)].sort(), headings:[...main.querySelectorAll('h1,h2')].map(x=>x.textContent.replace(/\s+/g,' ').trim())};
  },html ?? null);
}
async function verifySwap(page, callback, label) {
  const marker = Math.random().toString(36);
  await page.evaluate(value => {window.__auditMarker=value;},marker);
  const [response] = await Promise.all([
    page.waitForResponse(response => response.request().headers()['hx-request'] === 'true' && response.request().method()==='GET'),
    callback(),
  ]);
  assert.ok(response.ok(), label + ': HTTP ' + response.status());
  await page.waitForFunction(url => location.href === url, response.url());
  await page.waitForFunction(() => !document.querySelector('#main-content[aria-busy=true]'));
  assert.equal(await page.evaluate(() => window.__auditMarker),marker,label + ': full reload');
  const ordinary = await page.context().request.get(response.url());
  assert.equal(ordinary.status(),200);
  assert.deepEqual(await snapshot(page),await snapshot(page,await ordinary.text()),label + ': HTMX differs from full page');
  report.filterRequests.push({label,url:response.url()});
  record('Live filter swap equals normal backend response without reload',{label});
}
async function checkFilterControls(page, route, role) {
  await navigate(page,manifest.origin + route);
  const fields = await page.locator('[data-auto-filter] select:not([disabled]), [data-auto-filter] input:not([disabled]):not([type=hidden]):not([type=submit]):not([type=button])').evaluateAll(nodes => nodes.map(node => ({name:node.name,type:node.type,id:node.id})).filter(node=>node.name));
  const tested = new Set();
  for (const field of fields) {
    if (tested.has(field.name)) continue;
    tested.add(field.name);
    await navigate(page,manifest.origin + route);
    const target = field.id ? page.locator('[id=' + JSON.stringify(field.id) + ']') : page.locator('[data-auto-filter] [name=' + JSON.stringify(field.name) + ']').first();
    if (!await target.count()) continue;
    const disclosures = await target.locator('xpath=ancestor::details').all();
    for (const disclosure of disclosures.reverse()) {
      if (await disclosure.getAttribute('open') === null) await disclosure.locator(':scope > summary').click();
    }
    if (field.type === 'checkbox') {
      const checked = await target.isChecked();
      await verifySwap(page,()=>target.setChecked(!checked),`${role} ${route} ${field.name}`);
    } else if (field.type === 'date') {
      await verifySwap(page,async()=>{await target.fill(manifest.today);await target.dispatchEvent('change');},`${role} ${route} ${field.name}`);
    } else if (field.type === 'range') {
      const range=await target.evaluate(node=>({value:Number(node.value),min:Number(node.min || 0)}));
      await verifySwap(page,()=>target.press(range.value<=range.min ? 'ArrowRight' : 'ArrowLeft'),`${role} ${route} ${field.name}`);
    } else if (field.type !== 'select-one' && field.type !== 'select-multiple') {
      const value=field.type==='number' ? manifest.today.slice(0,4) : 'Browser';
      await verifySwap(page,async()=>{await target.fill(value);await target.press('Enter');},`${role} ${route} ${field.name}`);
    } else {
      const options = await target.locator('option:not([disabled])').evaluateAll(nodes=>nodes.map(x=>({value:x.value,selected:x.selected})));
      const option = options.find(x=>x.value && !x.selected) || options.find(x=>!x.selected);
      if (!option) {record('Single-choice filter has no alternative',{role,route,field:field.name});continue;}
      await verifySwap(page,()=>target.selectOption(option.value,{force:true}),`${role} ${route} ${field.name}`);
    }
  }
}

async function reveal(target) {
  for(const disclosure of (await target.locator('xpath=ancestor::details').all()).reverse()) {
    if(await disclosure.getAttribute('open')===null) await disclosure.locator(':scope > summary').click();
  }
}
async function compareOrdinary(page,label) {
  const response=await page.context().request.get(page.url());
  assert.equal(response.status(),200,label);
  assert.deepEqual(await snapshot(page),await snapshot(page,await response.text()),label);
}
async function checkTrackingPills(page,role) {
  const route='/tracking/?office='+manifest.offices.MED;
  for(const key of ['scope','status','offices','overdue','sort','owner']) {
    await navigate(page,manifest.origin+route);
    const choices=await page.locator('.pill-toggle[data-filter-link]').evaluateAll((nodes,key)=>nodes.map(x=>({href:x.getAttribute('href'),label:x.textContent.trim()})).filter(x=>new URL(x.href,location.origin).searchParams.has(key)),key);
    assert.ok(choices.length,'Missing tracking '+key+' pills for '+role);
    const target=page.locator('.pill-toggle[data-filter-link][href='+JSON.stringify(choices[0].href)+']').first();
    await reveal(target);
    await verifySwap(page,()=>target.click(),role+' tracking '+key+' pill');
    const expected=new URL(choices[0].href,manifest.origin);
    assert.deepEqual(new URL(page.url()).searchParams.getAll(key),expected.searchParams.getAll(key));
  }
}
async function checkHistoryResetAndPages(page) {
  await navigate(page,manifest.origin+'/documents/');
  const tag=page.locator('[data-auto-filter] select[name=tag]');
  await reveal(tag);
  const option=await tag.locator('option').evaluateAll(nodes=>nodes.find(x=>x.value)?.value);
  assert.ok(option,'Seeded tag must be selectable');
  const original=page.url();
  await verifySwap(page,()=>tag.selectOption(option,{force:true}),'history tag selection');
  const filtered=page.url();
  await page.goBack({waitUntil:'domcontentloaded'});
  await page.waitForURL(original,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('select[name=tag]')?.value==='');
  await compareOrdinary(page,'Back restores server results');
  await page.goForward({waitUntil:'domcontentloaded'});
  await page.waitForURL(filtered,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(value=>document.querySelector('select[name=tag]')?.value===value,option);
  await compareOrdinary(page,'Forward restores server results');
  record('Back and forward restore selected filters and backend result IDs');
  await verifySwap(page,()=>page.locator('[data-filter-reset]').first().click(),'reset filters');
  assert.equal(new URL(page.url()).searchParams.get('tag'),null);
  await navigate(page,manifest.origin+'/documents/');
  const next=page.locator('.page-link[data-filter-link][rel=next]').first();
  assert.equal(await next.count(),1,'Seeded rows must create a second page');
  await verifySwap(page,()=>next.click(),'repository second page');
  assert.equal(new URL(page.url()).searchParams.get('page'),'2');
  await verifySwap(page,()=>page.locator('.page-size-option[data-filter-link]').filter({hasText:'50'}).first().click(),'page size 50');
  assert.equal(new URL(page.url()).searchParams.get('per_page'),'50');
  record('Reset, real second page and page-size control preserve backend results');
}
async function checkDelayedResponseAndRetry(page) {
  await navigate(page,manifest.origin+'/documents/');
  let unblock,started;
  const gate=new Promise(resolve=>{unblock=resolve;});
  const firstStarted=new Promise(resolve=>{started=resolve;});
  let delayed=false;
  const delayHandler=async route=>{
    const req=route.request(), url=new URL(req.url());
    if(req.headers()['hx-request']==='true' && url.searchParams.get('q')==='Browser Repository Alpha' && !delayed) {
      delayed=true;
      const response=await route.fetch();
      started();
      await gate;
      try { await route.fulfill({response}); } catch(error) {
        if(!/already handled|closed|cancelled|canceled|Invalid InterceptionId/i.test(error.message)) throw error;
      }
    } else await route.continue();
  };
  await page.route('**/documents/?**',delayHandler);
  const query=()=>page.locator('[data-auto-filter] input[name=q]');
  await query().fill('Browser Repository Alpha'); await query().press('Enter');
  await firstStarted;
  await verifySwap(page,async()=>{await query().fill('Browser Repository Beta');await query().press('Enter');},'rapid newest query');
  unblock();
  await page.unroute('**/documents/?**',delayHandler,{behavior:'wait'});
  assert.equal(new URL(page.url()).searchParams.get('q'),'Browser Repository Beta');
  await compareOrdinary(page,'Delayed old response cannot replace newest results');
  record('Real first response delayed while newer request wins',{delay:'first backend response held until newer swap'});

  await navigate(page,manifest.origin+'/documents/');
  const before=await snapshot(page);
  let injected=false;
  const errorHandler=async route=>{
    if(route.request().headers()['hx-request']==='true' && !injected) {
      injected=true;
      await route.fulfill({status:503,contentType:'text/plain',headers:{'X-Audit-Injected':'retry-503'},body:'Simulated audit transport failure'});
    } else await route.continue();
  };
  await page.route('**/documents/?**',errorHandler);
  await query().fill('Browser Page'); await query().press('Enter');
  const retry=page.locator('[data-filter-retry]');
  await retry.waitFor({state:'visible'});
  assert.deepEqual(await snapshot(page),before,'503 must preserve previous results');
  await verifySwap(page,()=>retry.click(),'retry after injected 503');
  await page.unroute('**/documents/?**',errorHandler,{behavior:'wait'});
  assert.equal(new URL(page.url()).searchParams.get('q'),'Browser Page');
  record('Injected 503 preserves results and Retry returns real backend result IDs');
}
async function checkPrintAndZoom(page,internalPath,role) {
  await navigate(page,manifest.origin+internalPath+'slip/');
  await page.evaluate(()=>{window.__auditPrintCalls=0;window.print=()=>{window.__auditPrintCalls++;};});
  await page.locator('[data-print-trigger]').click();
  assert.equal(await page.evaluate(()=>window.__auditPrintCalls),1);
  await page.emulateMedia({media:'print'});
  assert.equal(await page.locator('[data-print-trigger]').isVisible(),false);
  assert.ok((await page.locator('#main-content').innerText()).includes('Browser created and filed'));
  await page.emulateMedia({media:'screen'});
  record('Routing slip print action and print CSS',{role});
  await navigate(page,manifest.origin+'/tracking/');
  await page.evaluate(()=>{document.documentElement.style.zoom='2';});
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(()=>document.activeElement!==document.body));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.evaluate(()=>{document.documentElement.style.zoom='';});
  record('200% CSS zoom emulation retains layout and keyboard focus',{role});
}
async function checkExpiredSession(page) {
  await navigate(page,manifest.origin+'/documents/');
  await page.context().clearCookies({name:manifest.sessionCookie});
  await Promise.all([
    page.waitForURL(url=>url.pathname==='/accounts/login/',{waitUntil:'domcontentloaded'}),
    page.locator('[data-auto-filter] input[name=q]').fill('Expired session').then(()=>page.locator('[data-auto-filter] input[name=q]').press('Enter')),
  ]);
  await ready(page);
  assert.equal(await page.locator('[name=username]').count(),1);
  assert.equal(await page.locator('[name=password]').count(),1);
  record('Expired real server session redirects filter request to usable sign-in page');
}

async function run() {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || 'C:/Users/USER/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
  const browser = await chromium.launch({headless:true,executablePath});
  const sessions = {};
  try {
    for (const role of ['med','sup','med_admin','admin','viewer']) sessions[role] = await signIn(browser,role);
    const med=sessions.med.page, sup=sessions.sup.page, manager=sessions.med_admin.page, admin=sessions.admin.page;
    await navigate(med,manifest.origin + '/tracking/new/');
    await med.locator('[name=subject]').fill('Browser created and filed');
    await med.locator('[name=instructions]').fill('Review this document and complete it.');
    await med.locator('select[name=receiving_offices]').selectOption(String(manifest.offices.SUP),{force:true});
    await Promise.all([med.waitForURL(/\/tracking\/\d+\/review\//),med.getByRole('button',{name:'Review',exact:true}).click()]);
    await Promise.all([med.waitForURL(/\/tracking\/\d+\/$/),med.getByRole('button',{name:'Send Document',exact:true}).click()]);
    const internalPath=new URL(med.url()).pathname;
    await navigate(sup,manifest.origin+internalPath);
    await Promise.all([sup.waitForNavigation({waitUntil:'domcontentloaded'}),sup.getByRole('button',{name:'Confirm Receipt',exact:true}).click()]);
    await sup.locator('textarea[name=remark]').fill('Browser work update.');
    await Promise.all([sup.waitForNavigation({waitUntil:'domcontentloaded'}),sup.getByRole('button',{name:'Save remark',exact:true}).click()]);
    await sup.getByRole('tab',{name:'Complete',exact:true}).click();
    await Promise.all([sup.waitForNavigation({waitUntil:'domcontentloaded'}),sup.getByRole('button',{name:'Mark as Completed',exact:true}).click()]);
    await navigate(admin,manifest.origin+internalPath);
    await Promise.all([admin.waitForURL(/\/documents\/\d+\/$/),admin.getByRole('button',{name:'Approve into Document Repository',exact:true}).click()]);
    record('Internal create, endorse, receipt, work, complete and approved filing persist');

    await navigate(manager,manifest.origin + `/tracking/external/${manifest.external}/review/`);
    await manager.locator('[name=to_office]').selectOption(String(manifest.offices.MED));
    await manager.locator('[name=sent_at]').fill(manifest.past);
    await submit(manager);
    await navigate(med,manifest.origin + manifest.externalDraftUrl);
    await med.locator('a[href$="/actions/receipt/"]').click(); await submit(med);
    await med.locator('a[href$="/actions/remark/"]').click();
    await med.locator('[name=message]').fill('Browser external work update'); await submit(med);
    await med.locator('a[href$="/actions/complete/"]').click(); await submit(med);
    await navigate(manager,manifest.origin + manifest.externalDraftUrl);
    await manager.locator('a[href$="/actions/file/"]').click(); await submit(manager);
    assert.match(new URL(manager.url()).pathname,/^\/documents\/\d+\/$/);
    const downloadLink=manager.locator('a[href*="/documents/file/"]').first();
    const privateFile=await manager.context().request.get(await downloadLink.getAttribute('href'));
    assert.equal(privateFile.status(),200); assert.equal((await privateFile.body()).toString(),'Private browser audit file');
    record('External manual dispatch, internal receipt, work, completion and approved filing persist');

    for(const role of ['med','med_admin','admin','viewer']) {
      const page=sessions[role].page;
      for(const route of ['/tracking/?office='+manifest.offices.MED,'/documents/','/search/?mode=repository','/tracking/external/?office='+manifest.offices.MED,'/documents/external/']) await checkFilterControls(page,route,role);
      await checkTrackingPills(page,role);
      await checkPrintAndZoom(page,internalPath,role);
      if(role==='viewer') {
        for(const route of ['/tracking/new/','/documents/upload/','/tracking/external/new/']) {
          const response=await page.context().request.get(manifest.origin+route); assert.equal(response.status(),403);
          record('Viewer write route refused',{route});
        }
      }
      for(const theme of ['light','dark']) for(const width of [320,390,768,900,901,1366]) {
        await page.setViewportSize({width,height:950});
        await page.evaluate(value=>localStorage.setItem('doctrack-theme',value),theme);
        for(const route of ['/tracking/','/documents/external/']) {
          await navigate(page,manifest.origin+route);
          assert.equal(await page.locator('html').getAttribute('data-bs-theme'),theme);
          assert.equal(await page.locator('#main-content').count(),1);
          const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth);
          assert.ok(overflow<=2,`${role} ${theme} ${width} ${route}: ${overflow}px overflow`);
          await page.keyboard.press('Tab');
          assert.ok(await page.evaluate(()=>document.activeElement!==document.body));
          record('Responsive page and keyboard focus',{role,theme,width,route});
        }
      }
      await page.setViewportSize({width:1366,height:950});
    }
    await checkHistoryResetAndPages(med);
    await checkDelayedResponseAndRetry(med);
    await checkExpiredSession(sessions.viewer.page);
    assert.deepEqual(report.errors,[]);
    assert.deepEqual(report.httpErrors,[]);
    console.log(JSON.stringify({checks:report.checks.length,liveFilterRequests:report.filterRequests.length,errors:report.errors.length}));
  } finally {
    fs.writeFileSync(manifest.output,JSON.stringify(report,null,2));
    await browser.close();
  }
}
run().catch(error=>{report.failure=error.stack;fs.writeFileSync(manifest.output,JSON.stringify(report,null,2));console.error(error.stack);process.exitCode=1;});
