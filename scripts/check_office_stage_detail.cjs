/* Bounded browser verification of genuine pending/working office detail and slips. */
const {readFileSync, existsSync, mkdirSync} = require('node:fs');
const {resolve, extname, sep} = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

const root = resolve(__dirname, '..');
const output = resolve(root, 'docs/verification/office-stage-detail');
const vendorDir = process.env.VENDOR_DIR || 'C:/Users/USER/AppData/Local/Temp';
const data = JSON.parse(readFileSync(resolve(root, 'docs/verification/direction-filter-pages.json'), 'utf8'));
const origin = 'http://office-stage-detail.test';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ||
  'C:/Users/USER/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
const mime = file => ({'.js':'application/javascript', '.css':'text/css', '.svg':'image/svg+xml',
                      '.png':'image/png', '.jpg':'image/jpeg', '.woff2':'font/woff2'}[extname(file)] || 'application/octet-stream');

assert.equal(data.pages.length, 39, 'Existing direction fixtures must remain intact');
assert.equal(data.detail_pages?.length, 4, 'Render both office perspectives on detail and routing slip first');
mkdirSync(output, {recursive:true});

async function serve(page, fixture, failures) {
  const observedPath = new URL(fixture.url, origin).pathname;
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    let vendor;
    if (url.pathname.includes('htmx') && url.pathname.endsWith('.js')) vendor = 'doctrack-htmx-1.9.12.min.js';
    else if (url.pathname.includes('bootstrap') && url.pathname.endsWith('.css')) vendor = 'doctrack-bootstrap-5.3.3.min.css';
    else if (url.pathname.includes('bootstrap') && url.pathname.endsWith('.js')) vendor = 'doctrack-bootstrap-5.3.3.min.js';
    if (vendor) {
      const file = resolve(vendorDir, vendor);
      assert.ok(existsSync(file), `Missing pinned vendor asset ${file}`);
      return route.fulfill({path:file, contentType:mime(file), headers:{'access-control-allow-origin':'*'}});
    }
    if (url.pathname.startsWith('/static/')) {
      const file = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (file.startsWith(root + sep) && existsSync(file)) return route.fulfill({path:file, contentType:mime(file)});
      failures.push(`Missing static asset: ${url.pathname}`);
      return route.fulfill({status:404, body:''});
    }
    if (url.origin === origin && url.pathname === observedPath) {
      return route.fulfill({contentType:'text/html', body:fixture.html});
    }
    return route.fulfill({contentType:'application/json', body:'{}'});
  });
}

async function checkFixture(browser, fixture, width, theme) {
  const context = await browser.newContext({viewport:{width,height:900},colorScheme:theme});
  const page = await context.newPage();
  const errors = [], failures = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  try {
    await context.addInitScript(theme => localStorage.setItem('doctrack-theme', theme), theme);
    await serve(page, fixture, failures);
    await page.goto(origin + fixture.url);
    const detail = fixture.surface === 'detail';
    if (detail) {
      const received = page.locator('[data-receipt-offices="received"]');
      const pending = page.locator('[data-receipt-offices="pending"]');
      assert.equal(await received.count(), 1);
      assert.equal(await pending.count(), 1);
      const confirmedCodes = (await received.locator('.office-badge-code').allTextContents()).map(code => code.trim());
      const pendingCodes = (await pending.locator('.office-badge-code').allTextContents()).map(code => code.trim());
      assert.ok(confirmedCodes.length > 0, 'Partial-receipt fixture must display confirmed recipients');
      assert.ok(pendingCodes.length > 0, 'Partial-receipt fixture must display outstanding recipients');
      assert.equal(confirmedCodes.some(code => pendingCodes.includes(code)), false, 'Receipt columns must be disjoint');
      assert.equal(await page.locator('th').filter({hasText:/^Received by$/}).count(), 1);
      assert.equal(await page.locator('th').filter({hasText:/^Awaiting receipt$/}).count(), 1);
    }
    const header = page.locator(detail ? '.page-head .sub' : '.routing-slip-header-status');
    const stage = header.locator('.pill:not(.pill-incoming):not(.pill-outgoing):not(.pill-overdue)');
    assert.equal(await stage.count(), 1);
    assert.equal((await stage.textContent()).trim(), fixture.stage_label);
    assert.equal(await stage.isVisible(), true);
    const perspective = header.locator(detail ? '> .text-muted.small' : '> span:not(.pill)');
    assert.equal((await perspective.textContent()).trim(), fixture.stage_scope_label);
    assert.equal(await perspective.isVisible(), true);
    assert.ok(fixture.stage_scope_label.includes(fixture.office_name));
    assert.equal(await perspective.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.left >= -1 && rect.right <= innerWidth + 1;
    }), true, 'Office stage perspective must remain readable inside the viewport');
    if (detail) {
      const receipt = page.getByRole('button', {name:'Confirm Receipt',exact:true});
      assert.equal(await receipt.count(), fixture.can_confirm ? 1 : 0);
      if (fixture.can_confirm) assert.equal(await receipt.isEnabled(), true);
      const work = page.getByRole('heading', {name:'Act on this document',exact:true});
      assert.equal(await work.count(), fixture.can_act ? 1 : 0);
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true,
                 'Horizontal page overflow');
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
    await page.screenshot({path:resolve(output, `${fixture.actor}-${fixture.surface}-${width}-${theme}.png`),fullPage:true});
    console.log(`PASS ${fixture.actor} ${fixture.surface} ${width}px ${theme}: ${fixture.stage_label}, named office perspective, authorized receipt/work controls, no errors or overflow`);
  } finally {await context.close();}
}

(async () => {
  const browser = await chromium.launch({headless:true,executablePath});
  try {
    for (const width of [1366,390]) {
      for (const theme of ['light','dark']) {
        for (const fixture of data.detail_pages) await checkFixture(browser, fixture, width, theme);
      }
    }
    console.log('PASS office stage detail/slip QA: 16 pending/working office configurations; existing 39 direction pages preserved.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
