'use strict';
// Live Django requests, real login and CSRF. Only static assets use local files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const output = path.join(manifest.root, '.vercel/file-versions/browser-results.json');
const report = {checks:[], errors:[], blocked:[], screenshots:[], limitations:['Chromium emulation only; physical devices and screen readers unverified.','200% CSS layout zoom emulation, not operating-system magnification.','Local isolated storage and simulated OCR/email; no live provider checks.']};
function check(name) { report.checks.push(name); fs.writeFileSync(output, JSON.stringify(report,null,2)); }
const assets = {'bootstrap.min.css':'doctrack-bootstrap-5.3.3.min.css','bootstrap.bundle.min.js':'doctrack-bootstrap-5.3.3.min.js','htmx.min.js':'doctrack-htmx-1.9.12.min.js'};
async function setup(browser, nojs=false) {
  const context = await browser.newContext({baseURL:manifest.origin,viewport:{width:1366,height:950},acceptDownloads:true,javaScriptEnabled:!nojs});
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!['http:','https:'].includes(url.protocol)) return route.continue();
    let local;
    if(url.pathname.startsWith('/static/') && !url.pathname.startsWith('/static/vendor/file-preview/')) local=path.resolve(manifest.root,'.'+decodeURIComponent(url.pathname));
    if(assets[path.basename(url.pathname)]) local=path.join(process.env.TEMP,assets[path.basename(url.pathname)]);
    if(local && local.startsWith(manifest.root) || local && local.startsWith(process.env.TEMP)) {
      if(fs.existsSync(local)) return route.fulfill({path:local,contentType:/\.(m?js)$/.test(local)?'application/javascript':local.endsWith('.css')?'text/css':undefined});
    }
    if(url.origin!==manifest.origin) { report.blocked.push(url.origin+url.pathname); return route.abort(); }
    return route.continue();
  });
  const page=await context.newPage();
  page.on('pageerror',error=>report.errors.push(error.message));
  page.setDefaultTimeout(20000);
  return {context,page};
}
async function login(page,username='versions_med') {
  await page.goto('/accounts/login/');
  await page.locator('[name=username]').fill(username); await page.locator('[name=password]').fill(manifest.password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes('/login/')),page.getByRole('button',{name:'Sign In',exact:true}).click()]);
}
async function ready(page) {
  await page.locator('[data-preview-status]').filter({hasText:'Preview ready.'}).waitFor();
}
async function previewFromRecord(page, filename) {
  await page.goto(`/tracking/${manifest.record}/`);
  const row=page.locator('.tracking-record-files li').filter({has:page.locator('.fw-semibold',{hasText:filename})}).first();
  await row.getByRole('link',{name:'Preview',exact:true}).click();
  await ready(page);
}
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const {context,page}=await setup(browser); await login(page); check('Actual login and CSRF-protected session');
    await previewFromRecord(page,'signed.pdf');
    assert.equal(await page.locator('[data-preview-surface] canvas').count(),1);
    await page.getByRole('button',{name:'Next page'}).click();
    await page.locator('[data-page-count]').filter({hasText:'Page 2 of 2'}).waitFor();
    await page.getByRole('button',{name:'Zoom in',exact:true}).click();
    await page.locator('[data-zoom]').filter({hasText:'125%'}).waitFor();
    await page.getByRole('link',{name:'Back to record'}).click();
    await page.waitForFunction(id=>document.activeElement.id===id,`file-version-tracking-${manifest.current_pdf}`);
    assert.equal(await page.evaluate(()=>document.activeElement.id),`file-version-tracking-${manifest.current_pdf}`);
    check('PDF page navigation, zoom and exact-version return focus');
    const old=page.getByRole('link',{name:'Preview Version 1',exact:true,includeHidden:true}).first();
    await old.locator('xpath=ancestor::details').locator('summary').click(); await old.click(); await ready(page);
    assert.ok(await page.locator('iframe').getAttribute('sandbox')==='allow-same-origin');
    const frame=page.frameLocator('iframe');
    await frame.getByText('Signed document history',{exact:true}).waitFor();
    assert.ok((await frame.locator('table').innerText()).includes('MED'));
    await page.waitForFunction(()=>[...document.querySelector('iframe').contentDocument.querySelectorAll('img')].some(image=>image.complete && image.naturalWidth>0));
    assert.ok((await page.locator('h1').innerText()).includes('original.docx'));
    check('Earlier DOCX formatted text and table in scriptless sandbox');
    for(const theme of ['light','dark']) {
      for(const width of [320,390,768,900,901,1366]) {
        await page.setViewportSize({width,height:950});
        if (await page.locator('html').getAttribute('data-theme') !== theme) await page.locator('[data-theme-toggle]').click();
        assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
        assert.equal(await page.locator('html').getAttribute('data-bs-theme'),theme);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),`Overflow at ${width} ${theme}`);
        const filename=`.vercel/file-versions/docx-${width}-${theme}.png`;
        await page.screenshot({path:path.join(manifest.root,filename),fullPage:true}); report.screenshots.push(filename);
        await page.getByRole('link',{name:'Back to record'}).click();
        await page.waitForFunction(()=>document.activeElement?.id.startsWith('file-version-'));
        assert.ok(await page.evaluate(()=>document.activeElement.id.startsWith('file-version-')));
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),`Record overflow at ${width} ${theme}`);
        await page.getByRole('link',{name:'Preview Version 1',exact:true}).first().click(); await ready(page);
      }
    }
    check('Six widths, both themes, history disclosure and focus restoration');
    await page.evaluate(()=>document.body.style.zoom='2');
    assert.ok(await page.getByRole('link',{name:'Download original'}).isVisible());
    await page.evaluate(()=>document.body.style.zoom='');
    await page.keyboard.press('Tab'); assert.ok(await page.evaluate(()=>document.activeElement!==document.body));
    check('Keyboard controls and 200% layout zoom');
    await page.setViewportSize({width:1366,height:950});
    for(const [filename,kind] of [['image.png','image'],['notes.txt','txt'],['values.csv','table'],['book.xlsx','table']]) {
      await page.goto(`/tracking/${manifest.record}/`);
      const row=page.locator('.tracking-record-files li').filter({has:page.locator('.fw-semibold',{hasText:filename})}).first();
      await row.getByRole('link',{name:'Preview',exact:true}).click();
      assert.equal(await page.locator('[data-file-preview]').getAttribute('data-kind'),kind);
      if(kind==='image') await ready(page); else assert.ok(await page.locator('pre, table').count());
    }
    await page.locator('#worksheet').selectOption('Second'); await page.getByRole('button',{name:'Show worksheet'}).click();
    assert.ok((await page.locator('table').innerText()).includes('Additional values'));
    check('PNG, escaped TXT/CSV and bounded multi-sheet XLSX values');
    await page.goto(`/tracking/${manifest.record}/`);
    const legacy=page.locator('.tracking-record-files li').filter({has:page.locator('.fw-semibold',{hasText:'legacy.doc'})}).first();
    await legacy.getByRole('link',{name:'Preview',exact:true}).click();
    assert.ok((await page.locator('[data-file-preview]').innerText()).includes('Preview unavailable'));
    const downloadEvent=page.waitForEvent('download'); await page.getByRole('link',{name:'Download original'}).click();
    const download=await downloadEvent; assert.equal(download.suggestedFilename(),'legacy.doc'); check('Unsupported format fallback and authorized original download');
    await page.goto(`/tracking/${manifest.record}/`);
    const form=page.locator(`form[action*="/files/${manifest.group}/versions/"]`);
    await form.locator('xpath=ancestor::details').locator('summary').click();
    await form.locator('input[type=file]').setInputFiles({name:'revised.txt',mimeType:'text/plain',buffer:Buffer.from('Third version supplied during active Tracking')});
    await form.locator('textarea').fill('Updated office wording');
    await form.getByRole('button',{name:'Upload new version',exact:true}).click();
    await page.locator('.tracking-record-files').getByText('Version 3',{exact:false}).first().waitFor();
    check('CSRF-protected new version saved from Tracking');
    await page.goto(`/tracking/external/${manifest.external}/`);
    const externalForm=page.locator('form[action*="/versions/"]').first();
    await externalForm.locator('xpath=ancestor::details').locator('summary').click();
    await externalForm.locator('input[type=file]').setInputFiles({name:'external-updated.txt',mimeType:'text/plain',buffer:Buffer.from('External revised copy')});
    await externalForm.locator('textarea').fill('Manually supplied external revision'); await externalForm.getByRole('button',{name:'Upload new version',exact:true}).click();
    await page.locator('.tracking-record-files').getByText('Version 2',{exact:false}).first().waitFor(); check('External version upload keeps its separate workflow and revision check');
    await page.goto(`/documents/${manifest.document}/`);
    assert.equal(await page.getByRole('button',{name:'Add files',exact:true}).count(),0);
    await page.getByRole('link',{name:'Preview',exact:true}).first().click(); await ready(page); check('Repository read-only file list and working preview');
    const nojs=await setup(browser,true); await login(nojs.page); await nojs.page.goto(`/tracking/${manifest.record}/`);
    await nojs.page.getByText('Version history (2 earlier)',{exact:true}).click();
    assert.ok(await nojs.page.getByRole('link',{name:'Preview Version 1',exact:true}).isVisible());
    await nojs.page.getByRole('link',{name:'Preview Version 1',exact:true}).click();
    assert.ok(await nojs.page.getByRole('link',{name:'Download original'}).isVisible()); check('JavaScript-disabled version history and downloads');
    await nojs.context.close();
    assert.deepEqual(report.errors,[]); assert.deepEqual(report.blocked,[]); check('No runtime JavaScript errors or external document-resource requests');
    await context.close();
  } finally { fs.writeFileSync(output,JSON.stringify(report,null,2)); await browser.close(); }
})().catch(error=>{report.failure=error.stack;fs.writeFileSync(output,JSON.stringify(report,null,2));console.error(error.stack);process.exitCode=1;});
