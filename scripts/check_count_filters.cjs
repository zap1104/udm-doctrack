/* Verify actual Django-rendered forms using the application's HTMX and JS. */
const {readFileSync, existsSync} = require('node:fs');
const {resolve} = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = resolve(__dirname, '..');
const data = JSON.parse(readFileSync(resolve(root, 'docs/verification/count-filter-pages.json'), 'utf8'));
function key(url) {
  const u = new URL(url, 'http://count-filters.test');
  return JSON.stringify([...u.searchParams].filter(([k, v]) => v && k !== 'page').sort());
}
const pages = new Map(data.pages.map(p => [key(p.url), p]));
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try {
    for (const width of [1366, 390]) {
      const page = await browser.newPage({viewport:{width, height:900}});
      const errors = [], requests = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const u = new URL(route.request().url());
        let vendor;
        if (u.pathname.includes('htmx') && u.pathname.endsWith('.js')) vendor = 'doctrack-htmx-1.9.12.min.js';
        else if (u.pathname.includes('bootstrap') && u.pathname.endsWith('.css')) vendor = 'doctrack-bootstrap-5.3.3.min.css';
        else if (u.pathname.includes('bootstrap') && u.pathname.endsWith('.js')) vendor = 'doctrack-bootstrap-5.3.3.min.js';
        if (vendor) return route.fulfill({path:resolve(process.env.TEMP, vendor), contentType:vendor.endsWith('.css') ? 'text/css' : 'application/javascript'});
        if (u.pathname.startsWith('/static/')) {
          const file = resolve(root, '.' + u.pathname);
          if (existsSync(file)) return route.fulfill({path:file});
          return route.fulfill({status:404, body:''});
        }
        if (u.pathname === '/search/') {
          requests.push(u);
          const fixture = pages.get(key(u.href));
          if (!fixture) throw new Error('Unexpected filter request: ' + u.search);
          return route.fulfill({contentType:'text/html', body:fixture.html});
        }
        return route.fulfill({contentType:'application/json', body:'{}'});
      });
      await page.goto('http://count-filters.test' + data.pages[0].url);
      await page.waitForFunction(() => window.htmx);
      await page.evaluate(() => { window.__countFilterSentinel = 'still here'; });
      assert.equal(await page.locator('.search-results-summary strong').innerText(), '2');
      await page.locator('#id_q').fill('QA received');
      await page.locator('.workspace-search-form button[type="submit"]').click();
      await page.waitForFunction(() => document.querySelector('.search-results-summary strong')?.textContent === '1');
      assert.deepEqual(requests.at(-1).searchParams.getAll('status'), ['PENDING_RECEIPT','RECEIVED']);
      assert.equal(requests.at(-1).searchParams.get('overdue'), 'no');
      await page.locator('#search-as-office').focus();
      await page.selectOption('#search-as-office', data.other_office);
      await page.waitForFunction(() => document.querySelector('.search-results-summary strong')?.textContent === '0');
      await page.waitForFunction(() => document.activeElement.id === 'search-as-office');
      assert.equal(await page.evaluate(() => window.__countFilterSentinel), 'still here');
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('.search-results-summary strong')?.textContent === '1');
      await page.goForward();
      await page.waitForFunction(() => document.querySelector('.search-results-summary strong')?.textContent === '0');
      assert.equal(await page.evaluate(() => window.__countFilterSentinel), 'still here');
      assert.deepEqual(errors, []);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await page.screenshot({path:resolve(root, `docs/verification/count-filters-${width}.png`), fullPage:true});
      console.log(`PASS ${width}px: search, repeated filters, office change, focus, Back/Forward and in-place updates; no JS errors or horizontal overflow`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
