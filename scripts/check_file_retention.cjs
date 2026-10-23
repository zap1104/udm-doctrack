'use strict';
// Real login, CSRF and Django responses against an explicitly disposable server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium} = require('playwright');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const origin = new URL(manifest.origin);
assert(['localhost', '127.0.0.1'].includes(origin.hostname) && origin.port === '8774');
assert.equal(manifest.database, 'doctrack_file_retention_browser_20261023');
const out = path.join(manifest.root, '.vercel/file-retention-audit');
const screens = path.join(manifest.root, 'docs/verification/file-lifecycle/screenshots');
fs.mkdirSync(screens, {recursive:true});
const report = {checks:[], failures:[], pageErrors:[], screenshots:[], limitations:[
  'Desktop Chromium with emulated viewports; no physical device or screen reader.',
  '200% CSS layout zoom, not native browser or operating-system zoom.',
  'Disposable PostgreSQL and local private storage; OCR/email/providers simulated or disabled.',
]};
function record(name) {report.checks.push(name); fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(report,null,2)); console.log(name);}
const assets = {'bootstrap.min.css':'doctrack-bootstrap-5.3.3.min.css','bootstrap.bundle.min.js':'doctrack-bootstrap-5.3.3.min.js','htmx.min.js':'doctrack-htmx-1.9.12.min.js'};
async function contextFor(browser, nojs=false) {
  const context = await browser.newContext({baseURL:manifest.origin, viewport:{width:1366,height:900}, javaScriptEnabled:!nojs, acceptDownloads:true});
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!['http:','https:'].includes(url.protocol) || url.origin === origin.origin) return route.continue();
    const cached = assets[path.basename(url.pathname)];
    if (cached) {
      const file = path.join(process.env.TEMP,cached);
      assert(fs.existsSync(file), 'Pinned local vendor cache is required');
      return route.fulfill({path:file, contentType:cached.endsWith('.css')?'text/css':'application/javascript'});
    }
    return route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  page.setDefaultNavigationTimeout(90000);
  page.on('pageerror', error => report.pageErrors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  return {context,page};
}
async function login(page, role='admin') {
  await page.goto('/accounts/login/');
  await page.locator('[name=username]').fill(manifest.usernames[role]);
  await page.locator('[name=password]').fill(manifest.password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes('/login/')),page.getByRole('button',{name:'Sign In',exact:true}).click()]);
}
function file(name, body) {return {name,mimeType:'text/plain',buffer:Buffer.from(body)};}
function fixtureAction(action) {
  execFileSync(path.join(manifest.root,'.venv/Scripts/python.exe'),[path.join(out,'browser_state_checks.py'),action],{
    cwd:manifest.root,env:{...process.env,PYTHONPATH:out,DJANGO_SETTINGS_MODULE:'file_retention_browser_settings'},stdio:'pipe'});
}
function uploadRegion(page) {return page.locator('[data-upload-draft]').first();}
async function waitSaved(region, count=1) {await region.locator('[data-upload-file-list] li .badge').filter({hasText:/^Saved$/}).nth(count-1).waitFor();}
async function businessSubmit(page, button, endpoint, keyboard=false) {
  const response = page.waitForResponse(response => response.request().method()==='POST' && new URL(response.url()).pathname === endpoint);
  const navigation = page.waitForNavigation({waitUntil:'domcontentloaded'});
  const [result] = await Promise.all([response,navigation,keyboard?button.press('Enter'):button.click()]);
  assert(result.status() < 400, `Business POST failed: ${endpoint}, ${result.status()}`);
}
async function screenshots(page,prefix) {
  for (const theme of ['light','dark']) for(const width of [320,390,768,900,901,1366]) {
    await page.setViewportSize({width,height:900});
    await page.evaluate(()=>{scrollTo(0,0);document.activeElement?.blur();});
    if(await page.locator('html').getAttribute('data-bs-theme')!==theme) await page.locator('[data-theme-toggle]').click();
    assert.equal(await page.locator('html').getAttribute('data-bs-theme'),theme);
    const size=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert(size.scroll<=size.width+1,`${prefix} ${theme} ${width}px overflow: ${size.scroll}`);
    const name=`${prefix}-${theme}-${width}.jpg`; await page.screenshot({path:path.join(screens,name),fullPage:true,type:'jpeg',quality:65}); report.screenshots.push(name);
    if([320,1366].includes(width) && await page.locator('.file-card').count()) {
      const crop=`${prefix}-file-${theme}-${width}.jpg`;
      const clip=await page.locator('.file-card').first().evaluate(el=>{
        const rect=el.getBoundingClientRect();
        return {x:rect.left+scrollX,y:rect.top+scrollY,width:rect.width,height:rect.height,scale:1};
      });
      const session=await page.context().newCDPSession(page);
      try {
        const {data}=await session.send('Page.captureScreenshot',{format:'jpeg',quality:80,captureBeyondViewport:true,clip});
        fs.writeFileSync(path.join(screens,crop),Buffer.from(data,'base64'));
      } finally {await session.detach();}
      report.screenshots.push(crop);
    }
  }
  await page.setViewportSize({width:1366,height:900});
  record(`${prefix}: 320/390/768/900/901/1366px, both themes, no document overflow`);
}
async function version(page, external, number) {
  const endpoint = `/tracking/${external?'external/':''}${external?manifest.external:manifest.internal}/`;
  await page.goto(endpoint);
  const card = page.locator('.file-card').first();
  const section = card.locator('.file-new-version');
  if (!await section.evaluate(el=>el.open)) await section.locator('summary').click();
  const form = section.locator('form');
  const filename = number===7 ? `Signed final university approval ${'long filename '.repeat(9)}${number}.txt` : `Signed copy ${number}.txt`;
  await form.locator('input[type=file]').setInputFiles(file(filename, `Signed version ${number}`));
  await waitSaved(form.locator('[data-upload-draft]'));
  await form.locator('[name$=change_summary]').fill(`Added the director's signature ${number}`);
  await businessSubmit(page, form.getByRole('button',{name:'Upload new version',exact:true}), new URL(await form.getAttribute('action'),origin).pathname);
  await page.getByText(`V${number} · Current`,{exact:true}).waitFor();
  assert.equal(await page.locator('.file-history-row').count(),4);
  record(`${external?'External':'Internal'} V${number}: current plus four available older versions`);
}
async function main() {
  const browser = await chromium.launch({headless:true,executablePath:process.env.DOCTRACK_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  try {
    const {context,page} = await contextFor(browser);
    await login(page); record('Actual office-admin login and CSRF session');
    await page.goto('/uploads/saved/');
    while (await page.getByText('Discard saved form',{exact:true}).count()) {
      const card=page.locator('.card-udm').first(); await card.getByText('Discard saved form',{exact:true}).click();
      await card.locator('[name=confirm]').check();
      const form=card.locator('form');
      await businessSubmit(page,form.getByRole('button',{name:'Discard',exact:true}),new URL(await form.getAttribute('action'),origin).pathname);
    }
    await page.goto('/tracking/new/');
    const form = page.locator('form').filter({has:page.locator('[data-upload-draft]')}).first();
    await form.locator('[name=instructions]').fill('Review before dispatch');
    await form.locator('[name=receiving_offices]').selectOption(String(manifest.office_sup),{force:true});
    await form.locator('[name=deadline_choice][value=none]').check();
    await form.locator('input[type=file]').setInputFiles([file('Recovered signed request.txt','Recovered bytes'),{name:'invalid.exe',mimeType:'application/octet-stream',buffer:Buffer.from('invalid')}]);
    const region = uploadRegion(page);
    await waitSaved(region);
    const bad = region.locator('li').filter({hasText:'invalid.exe'});
    await bad.getByText('Not saved',{exact:true}).waitFor();
    await bad.getByRole('button',{name:'Remove',exact:true}).click();
    await businessSubmit(page, form.getByRole('button',{name:'Review',exact:true}), '/tracking/new/');
    await page.waitForURL(url=>url.searchParams.has('upload_draft'));
    await page.reload();
    await page.locator('[data-upload-file-list]').getByText('Recovered signed request.txt',{exact:true}).waitFor();
    assert.equal(await page.locator('input[type=file]').inputValue(),'');
    await screenshots(page,'create-error');
    await page.locator('[name=subject]').fill('Recovered through actual browser validation');
    await businessSubmit(page, page.getByRole('button',{name:'Review',exact:true}), '/tracking/new/');
    await page.waitForURL(/\/tracking\/\d+\/review\//);
    record('Partial-invalid batch, missing subject, refresh and corrected submit preserve one valid file');

    const tabs=[];
    for (const name of ['Independent A.txt','Independent B.txt']) {
      const tab=await context.newPage(); tabs.push(tab); await tab.goto('/tracking/new/');
      await tab.locator('input[type=file]').setInputFiles(file(name,name)); await waitSaved(uploadRegion(tab));
    }
    assert.notEqual(new URL(tabs[0].url()).searchParams.get('upload_draft'),new URL(tabs[1].url()).searchParams.get('upload_draft'));
    for (let i=0;i<2;i++) {await tabs[i].reload(); await uploadRegion(tabs[i]).getByText(`Independent ${i?'B':'A'}.txt`,{exact:true}).waitFor(); assert.equal(await uploadRegion(tabs[i]).locator('li').count(),1);}
    record('Two independent tabs retain separate private saved-file identities after refresh');

    const retryTab=tabs[0], retryRegion=uploadRegion(retryTab);
    let uploaded, release;
    const uploadCommitted=new Promise(resolve=>uploaded=resolve), held=new Promise(resolve=>release=resolve);
    let disconnected=true, businessPosts=0;
    retryTab.on('request',request=>{if(request.method()==='POST' && new URL(request.url()).pathname==='/tracking/new/') businessPosts++;});
    await retryTab.route('**/uploads/drafts/*/?format=json',route=>disconnected?route.abort():route.continue());
    await retryTab.route('**/uploads/drafts/*/files/',async route=>{
      await route.fetch({timeout:90000}); uploaded(); await held; await route.abort();
    });
    await retryTab.locator('input[type=file]').setInputFiles(file('Remove despite a lost acknowledgement.txt','Do not attach these bytes'));
    await uploadCommitted;
    const uncertain=retryRegion.locator('li').filter({hasText:'Remove despite a lost acknowledgement.txt'});
    await uncertain.getByRole('button',{name:'Remove',exact:true}).click(); release();
    await uncertain.getByText('Not saved',{exact:true}).waitFor();
    await retryTab.getByRole('button',{name:'Review',exact:true}).click();
    await retryRegion.locator('[data-upload-status]').filter({hasText:/could not|Retry|retry/}).waitFor();
    assert.equal(businessPosts,0,'An uncertain removal cannot submit business data');
    disconnected=false; await uncertain.getByRole('button',{name:'Retry',exact:true}).click(); await uncertain.waitFor({state:'detached'});
    await retryTab.unroute('**/uploads/drafts/*/files/'); await retryTab.unroute('**/uploads/drafts/*/?format=json');
    await retryTab.reload(); assert.equal(await retryRegion.locator('li').count(),1);
    record('Actual committed upload with lost acknowledgement/read, in-flight Remove, blocked submit and retry does not resurrect the file');

    await page.goto('/tracking/new/');
    await screenshots(page,'create');

    await page.goto(`/tracking/${manifest.internal}/`);
    assert.equal(await page.getByText('This record is completed but not yet approved',{exact:true}).count(),0);
    const inlineVersion=page.locator('.file-new-version').first(); await inlineVersion.locator('summary').click();
    await inlineVersion.locator('input[type=file]').setInputFiles(file('Unsubmitted next version.txt','Keep this separate version draft'));
    await waitSaved(inlineVersion.locator('[data-upload-draft]'));
    const inlineRemark=page.locator('#tab-remark form'); await inlineRemark.locator('[name=remark]').fill(' ');
    await inlineRemark.locator('input[type=file]').setInputFiles(file('Unsubmitted remark proof.txt','Keep this separate remark draft'));
    await waitSaved(inlineRemark.locator('[data-upload-draft]'));
    await businessSubmit(page,inlineRemark.getByRole('button',{name:'Save remark',exact:true}),`/tracking/${manifest.internal}/remark/`);
    assert.equal(await page.getByText('Unsubmitted next version.txt',{exact:true}).count(),1);
    assert.equal(await page.getByText('Unsubmitted remark proof.txt',{exact:true}).count(),1);
    await page.locator('.file-new-version [data-upload-draft]').first().getByRole('link',{name:'Preview',exact:true}).click();
    await page.getByText('Saved upload · Not yet submitted',{exact:true}).waitFor();
    const savedFocus=new URL(await page.getByRole('link',{name:'Back to saved form',exact:true}).getAttribute('href'),origin).hash.slice(1);
    await page.getByRole('link',{name:'Back to saved form',exact:true}).click();
    await page.waitForFunction(id=>document.activeElement?.id===id,savedFocus);
    assert.equal(await page.getByText('Unsubmitted next version.txt',{exact:true}).count(),1);
    assert.equal(await page.getByText('Unsubmitted remark proof.txt',{exact:true}).count(),1);
    for(const name of ['Unsubmitted next version.txt','Unsubmitted remark proof.txt']) {
      const pending=page.locator('[data-upload-file-list] li').filter({hasText:name});
      await pending.getByRole('button',{name:'Remove',exact:true}).click(); await pending.waitFor({state:'detached'});
    }
    record('Independent inline remark/version drafts survive invalid POST and saved preview Back; active work has no completed filing panel');
    const oldHistory=page.locator('.file-history').first(); await oldHistory.locator('summary').click();
    const oldLink=await oldHistory.locator('.file-history-row').filter({hasText:'V1 · TXT'}).getByRole('link',{name:/Preview/}).getAttribute('href');
    await version(page,false,6);
    const retired=await page.request.get(oldLink); assert.equal(retired.status(),410); assert.equal(retired.headers()['cache-control'],'private, no-store');
    await version(page,false,7);
    await page.locator('.file-history summary').first().click();
    await page.locator('.file-history-row').filter({hasText:'V3 · TXT'}).getByRole('link',{name:/Preview/}).click();
    await page.getByText('Viewing Version 3 of 7',{exact:false}).waitFor();
    await page.getByRole('link',{name:'Back to record',exact:true}).click();
    await page.waitForFunction(()=>document.activeElement?.id.startsWith('file-version-tracking-'));
    assert(await page.locator('.file-history').first().evaluate(el=>el.open));
    record('Retired signed link is private 410; retained older preview restores its exact focused history row');
    assert.equal(await page.locator('.file-discard').count(),0,'A dispatched file cannot offer draft discard');
    const versionAction=await page.locator('.file-new-version form').first().getAttribute('action');
    const refused=await page.request.post(new URL(versionAction.replace('/versions/','/discard/'),origin).href,{form:{
      csrfmiddlewaretoken:await page.locator('[name=csrfmiddlewaretoken]').first().inputValue(),
      expected_version:'7',reason:'Forged discard of dispatched fixture',confirm:'on'}});
    assert.equal(refused.status(),200);
    assert.match(await refused.text(),/Files can only be discarded before the first endorsement or dispatch/);
    assert.equal(await page.getByText('V7 · Current',{exact:true}).count(),1);
    record('Crafted active-record draft-discard POST returns a clear state error and preserves the current file');
    await screenshots(page,'tracking');

    await page.goto(`/tracking/${manifest.discard}/`);
    const discard=page.locator('.file-discard'); await discard.locator('summary').click();
    await discard.locator('[name=reason]').fill('Accidental upload in unsent browser fixture');
    await discard.locator('[name=confirm]').check();
    const discardEndpoint=new URL(await discard.locator('form').getAttribute('action'),origin).pathname;
    await businessSubmit(page,discard.getByRole('button',{name:'Discard file',exact:true}),discardEndpoint);
    await page.getByText('No files attached.',{exact:true}).waitFor(); record('Explicit unsent-draft discard removes its visible file without dispatch');
    await screenshots(page,'tracking-empty');

    await version(page,true,6); await version(page,true,7);
    await screenshots(page,'external-tracking');
    await page.goto(`/tracking/external/${manifest.external}/actions/complete/`);
    const externalChoice=page.locator('[name=archive_now]'); assert(!await externalChoice.isChecked()); await externalChoice.check();
    await businessSubmit(page,page.getByRole('button',{name:'Complete work',exact:true}),`/tracking/external/${manifest.external}/actions/complete/`);
    await page.waitForURL(/\/documents\/\d+\//);
    assert.equal(await page.locator('.file-card').count(),1); assert.equal(await page.locator('.file-history-row').count(),4); assert.equal(await page.locator('input[type=file]').count(),0);
    record('External authorized checked completion files one read-only logical file with retained history');

    await page.goto(`/tracking/${manifest.internal}/`); await page.locator('#tab-complete-button').click();
    assert(!await page.locator('#tab-complete [name=archive_now]').isChecked());
    await businessSubmit(page,page.getByRole('button',{name:'Mark as Completed',exact:true}),`/tracking/${manifest.internal}/complete/`);
    await page.getByRole('button',{name:'Approve into Document Repository',exact:true}).waitFor();
    record('Unchecked Internal completion remains Pending Filing with an explicit approval action');
    await businessSubmit(page,page.getByRole('button',{name:'Approve into Document Repository',exact:true}),`/tracking/${manifest.internal}/archive/`);
    await page.waitForURL(/\/documents\/\d+\//);
    const repositoryUrl=page.url();
    assert.equal(await page.locator('.file-card').count(),1); assert.equal(await page.locator('.file-new-version,.file-discard,input[type=file]').count(),0);
    const download=page.waitForEvent('download'); await page.locator('.file-card').first().getByRole('link',{name:/^Download/}).first().click();
    const saved=await download; assert.equal(fs.readFileSync(await saved.path(),'utf8'),'Signed version 7');
    record('Internal filing preserves exact current bytes, one logical file and a content-locked repository card');

    await screenshots(page,'repository');
    await page.setViewportSize({width:1366,height:900}); await page.evaluate(()=>{document.body.style.zoom='2';});
    assert(await page.getByText('V7 · Current',{exact:true}).isVisible());
    await page.screenshot({path:path.join(screens,'repository-css-zoom-200.jpg'),fullPage:true,type:'jpeg',quality:70});
    report.screenshots.push('repository-css-zoom-200.jpg'); await page.evaluate(()=>{document.body.style.zoom='';});
    await page.emulateMedia({media:'print'}); assert.equal(await page.locator('.file-discard:visible,.file-new-version:visible').count(),0); await page.emulateMedia({media:'screen'});
    record('200% CSS layout zoom and print keep repository meaning without mutation controls');

    const {context:nativeContext,page:native}=await contextFor(browser,true); await login(native);
    await native.goto('/tracking/new/'); await native.locator('[name=instructions]').fill('Native recovery');
    await native.locator('[name=receiving_offices]').selectOption(String(manifest.office_sup));
    await native.locator('[name=deadline_choice][value=none]').check();
    await native.locator('input[type=file]').setInputFiles(file('No JavaScript saved proof.txt','Native stored bytes'));
    await businessSubmit(native,native.getByRole('button',{name:'Review',exact:true}),'/tracking/new/',true); await native.reload();
    await native.locator('[data-upload-file-list] strong').filter({hasText:'No JavaScript saved proof.txt'}).waitFor(); await native.locator('[name=subject]').fill('Native recovery completed');
    await businessSubmit(native,native.getByRole('button',{name:'Review',exact:true}),'/tracking/new/',true); await native.waitForURL(/\/tracking\/\d+\/review\//);
    record('JavaScript-disabled multipart validation, refresh and finalization reuse saved bytes'); await nativeContext.close();
    const {context:viewerContext,page:viewer}=await contextFor(browser); await login(viewer,'viewer'); await viewer.goto(`/tracking/${manifest.internal}/`);
    assert.equal(await viewer.locator('.file-new-version,.file-discard').count(),0); record('Viewer history stays read-only'); await viewerContext.close();
    const {context:staffContext,page:staff}=await contextFor(browser); await login(staff,'staff'); await staff.goto('/tracking/new/');
    await staff.locator('input[type=file]').setInputFiles(file('Revoked permission saved proof.txt','Private fixture data')); await waitSaved(uploadRegion(staff));
    const savedUrl=staff.url(), savedPreview=await uploadRegion(staff).getByRole('link',{name:'Preview',exact:true}).getAttribute('href');
    fixtureAction('revoke_staff');
    try {assert.equal((await staff.goto(savedUrl)).status(),403); assert.equal((await staff.request.get(savedPreview)).status(),403);}
    finally {fixtureAction('restore_staff');}
    await staffContext.close(); record('A real role revocation refuses the saved form and its previously signed preview');
    await context.close();
    fixtureAction('final'); record('Saved backend state reconciles exact current/older IDs, checksums, both collections, draft discard and repeated physical cleanup');
    assert.deepEqual(report.pageErrors,[],'Browser JavaScript errors');
  } finally {await browser.close();}
}
main().catch(error=>{report.failures.push(error.stack);console.error(error.message);process.exitCode=1;}).finally(()=>fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(report,null,2)));
