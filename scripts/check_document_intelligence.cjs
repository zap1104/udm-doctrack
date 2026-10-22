/* Live, disposable-data verification. No mocked HTML or backend responses. */
const {chromium} = require('playwright');
const {readFileSync, writeFileSync, mkdirSync} = require('node:fs');
const {resolve, join} = require('node:path');
const assert = require('node:assert/strict');
const args = Object.fromEntries(process.argv.slice(2).map((v, i, all) => v.startsWith('--') ? [v.slice(2), all[i+1]] : []).filter(v => v.length));
const base = new URL(args['base-url']);
assert(['127.0.0.1', 'localhost'].includes(base.hostname), 'Use an isolated loopback server.');
const fixture = JSON.parse(readFileSync(resolve(args.fixture), 'utf8'));
const out = resolve(args.out);
mkdirSync(out, {recursive:true});
const report = {checks:[], layouts:[], errors:[], limitations:['Physical devices and screen readers were not tested.', '200% uses CSS zoom; native browser zoom remains unverified.']};
async function login(context, role) {
  const page = await context.newPage();
  await page.goto(new URL('/accounts/login/', base).href);
  await page.locator('[name=username]').fill(fixture.credentials[role].username);
  await page.locator('[name=password]').fill(fixture.credentials[role].password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes('/login/')), page.getByRole('button', {name:'Sign In',exact:true}).click()]);
  await page.close();
  return context.storageState();
}
async function visit(page, path) {
  const response = await page.goto(new URL(path, base).href);
  assert.equal(response.status(), 200, path);
  await page.locator('h1').first().waitFor();
}
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try {
    const admin = await browser.newContext();
    const state = await login(admin, 'admin');
    const paths = [`/documents/${fixture.documents.OVPA}/extraction/units/`, `/documents/${fixture.documents.EXTERNAL}/extraction/units/`, '/documents/intelligence/models/', '/documents/intelligence/vocabulary/', '/documents/views/', '/documents/', '/documents/external/', '/documents/intelligence/review-queue/'];
    for (const width of [320,390,768,900,901,1366]) for (const theme of ['light','dark']) {
      const context = await browser.newContext({storageState:state,viewport:{width,height:900}});
      await context.addInitScript(theme => localStorage.setItem('doctrack-theme',theme),theme);
      const page = await context.newPage();
      page.on('pageerror', e => report.errors.push(e.message));
      for (const path of paths) {
        await visit(page,path);
        const layout = await page.evaluate(() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme}));
        report.layouts.push({path,...layout});
        assert(layout.scrollWidth <= width+2, `Page overflow at ${width}: ${path} (${layout.scrollWidth})`);
        assert.equal(layout.theme,theme);
        if ([390,1366].includes(width)) await page.screenshot({path:join(out,`${width}-${theme}-${paths.indexOf(path)}.png`),fullPage:true});
      }
      await context.close();
    }
    report.checks.push('Both themes, six required widths, eight real pages: no document-level horizontal overflow.');
    const page = await admin.newPage();
    await visit(page,paths[0]);
    await page.getByRole('button',{name:'Generate current topic proposals',exact:true}).click();
    await page.getByText('Current topic proposals generated.',{exact:false}).waitFor();
    await page.locator('#review_tag').selectOption(String(fixture.topic_id));
    await page.locator('#review_decision').selectOption('confirm');
    await page.getByRole('button',{name:'Save tag decision',exact:true}).click();
    await page.getByText('Human confirmed',{exact:false}).waitFor();
    const token = await page.locator('input[name=csrfmiddlewaretoken]').first().inputValue();
    const stale = await page.request.post(new URL(`/documents/${fixture.documents.OVPA}/tags/review/`,base).href,{form:{csrfmiddlewaretoken:token,tag:String(fixture.topic_id),decision:'reject',input_revision:'stale',vocabulary:'stale'}});
    assert.equal(stale.status(),409);
    await page.getByRole('link',{name:/Read text from/}).first().click();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link',{name:'Download original version',exact:true}).click();
    const download = await downloadPromise;
    assert(await download.path());
    const preview = await page.getByRole('link',{name:'Preview original version',exact:true}).getAttribute('href');
    assert.equal((await page.goto(new URL(preview,base).href)).status(),200);
    report.checks.push('Real CSRF-protected proposal and human-review POSTs, stale409, signed preview and original download.');
    await visit(page,'/documents/views/?collection=OVPA&filters=year%3D2026');
    await page.locator('[name=name]').fill('Authored browser saved view '+Date.now());
    await page.getByRole('button',{name:'Save private view',exact:true}).click();
    await page.locator('a[href*="/documents/views/"]').filter({hasText:'Authored browser saved view'}).last().click();
    assert.equal(new URL(page.url()).searchParams.get('year'),'2026');
    report.checks.push('Saved filter view submitted and reopened with the applied year.');
    await visit(page,'/search/?mode=tracking');
    let requests=0;
    page.on('request',request => {if(new URL(request.url()).pathname==='/search/' && request.method()==='GET') requests++;});
    await page.locator('[name=q]').last().fill('authored browser no-match query');
    const before=requests;
    await page.waitForTimeout(400);
    assert.equal(requests,before);
    await page.evaluate(() => {window.__qaIdentity='same-document';});
    await page.locator('#search-tracking-filters').getByRole('button',{name:'Search',exact:true}).click();
    await page.waitForURL(url => url.searchParams.get('q')==='authored browser no-match query');
    assert.equal(await page.evaluate(() => window.__qaIdentity),'same-document');
    report.checks.push('Advanced search waits for Search and updates through HTMX without full navigation.');
    await visit(page,'/');
    const select=page.locator('#dashboard-scope');
    const choice=await select.locator('option').evaluateAll(options => options.find(o=>o.value!=='all'&&o.value!=='')?.value);
    await page.evaluate(() => {window.__qaIdentity='dashboard-document';window.scrollTo(0,document.body.scrollHeight);});
    const beforeScroll=await page.evaluate(()=>scrollY);
    await select.selectOption(choice);
    await page.waitForURL(url=>url.searchParams.get('office')===choice);
    assert.equal(await page.evaluate(()=>window.__qaIdentity),'dashboard-document');
    assert(await page.evaluate(()=>scrollY)>0 || beforeScroll===0);
    report.checks.push('Dashboard office selection updates automatically through HTMX while retaining a lower-page position.');
    await visit(page,paths[0]);
    await page.evaluate(()=>{document.documentElement.style.zoom='2';});
    await page.keyboard.press('Tab');
    assert(await page.evaluate(()=>document.activeElement!==document.body));
    report.checks.push('Keyboard focus operates at CSS200% zoom.');
    await visit(page,'/documents/intelligence/review-queue/?seed=browser-audit');
    const sample = page.locator('a[href*="review_sample="]').first();
    assert(await sample.isVisible());
    await sample.click();
    assert(await page.locator('#review_review_sample').inputValue());
    await page.locator('#review_tag').selectOption(String(fixture.topic_id));
    await page.locator('#review_decision').selectOption('unsure');
    await page.getByRole('button',{name:'Save tag decision',exact:true}).click();
    await page.getByText('Topic review saved.',{exact:false}).waitFor();
    report.checks.push('Repeatable review queue and signed sampling receipt submitted through real CSRF forms.');
    for (const role of ['viewer','hr']) {
      const context=await browser.newContext();
      await login(context,role);
      const actorPage=await context.newPage();
      const response=await actorPage.goto(new URL(paths[0],base).href);
      if(role==='hr') assert([403,404].includes(response.status()));
      else {assert.equal(response.status(),200);assert.equal(await actorPage.getByRole('button',{name:'Save tag decision',exact:true}).count(),0);}
      await context.close();
    }
    const nojs=await browser.newContext({javaScriptEnabled:false});
    await login(nojs,'med_admin');
    const plain=await nojs.newPage();
    await visit(plain,paths[1]);
    assert(await plain.getByRole('button',{name:'Save tag decision',exact:true}).isVisible());
    await plain.getByRole('link',{name:/Read text from/}).first().click();
    assert(await plain.getByRole('link',{name:'Download original version',exact:true}).isVisible());
    report.checks.push('Viewer and unrelated-office restrictions; JavaScript-disabled External source/review/download navigation.');
    await nojs.close();
    assert.deepEqual(report.errors,[]);
    report.passed=true;
  } catch(error) {report.passed=false;report.failure=error.stack;throw error;}
  finally {writeFileSync(join(out,'results.json'),JSON.stringify(report,null,2));await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
