/* Run with Playwright available on NODE_PATH; exercises real HTMX and app JS. */
const {readFileSync, existsSync} = require('node:fs');
const {resolve} = require('node:path');
const assert = require('node:assert/strict');
const {createServer} = require('node:http');
const {chromium} = require('playwright');
const root = resolve(__dirname, '..');
const attrs = readFileSync(resolve(root, 'templates/partials/_auto_filter_attrs.html'), 'utf8')
  .replace('{{ request.path }}', '/tracking/');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function html(params = new URLSearchParams()) {
  const office = params.get('office') || '1';
  return `<!doctype html><html><head><title>Filter test</title><link rel="stylesheet" href="/static/vendor/bootstrap.min.css"><link rel="stylesheet" href="/static/css/doctrack.css"></head><body>
    <aside id="sidebar">Stable navigation</aside>
    <div id="filter-update-status" class="filter-update-status no-print" role="status" aria-live="polite" hidden><span data-filter-message></span><button data-filter-retry hidden>Retry</button></div>
    <main id="main-content" tabindex="-1" hx-history-elt hx-history="false">
      <form method="get" action="/tracking/" ${attrs}>
        <label for="office">Office</label><select id="office" name="office">
          ${['1','2','3'].map(v => `<option value="${v}"${office === v ? ' selected' : ''}>Office ${v}</option>`).join('')}
        </select><button type="submit" data-filter-fallback>Apply</button>
        <input type="hidden" name="status" value="RECEIVED"><input type="hidden" name="status" value="IN_PROCESS">
        <input type="hidden" name="per_page" value="100">
        <input id="query" name="q" autofocus value="${params.get('q') || ''}"><button type="submit">Search</button>
        <input id="relevance" type="range" name="min_relevance" value="${params.get('min_relevance') || '30'}">
        <span id="relevance-value">30</span><datalist id="search-suggestions"></datalist>
      </form>
      <form method="get" ${attrs}><select name="month" id="month"><option value="2026-09">September</option><option value="2026-10"${params.get('month') === '2026-10' ? ' selected' : ''}>October</option></select><button data-filter-fallback>Apply</button></form>
      <p id="results">Results for office ${office}, ${params.get('month') || '2026-09'}</p>
      <details id="more"><summary>More</summary>Details</details>
      <a href="/tracking/" data-filter-reset>Clear filters</a>
      <a href="/tracking/99/">Open document</a>
      <button data-print-trigger>Print</button>
      <span hidden data-print-log="report" data-print-log-reference="${office}" data-print-log-url="/print-log/" data-print-log-csrf="test-token"></span>
      <span hidden data-search-enhance data-search-query-id="query" data-search-relevance-id="relevance" data-search-autocomplete-url="/autocomplete/"></span>
      <div class="column-chart"><span class="column-value">15</span></div>
    </main>
    <script src="/static/vendor/htmx.min.js"></script><script src="/static/js/doctrack.js"></script><script src="/static/js/auto-filters.js"></script>
  </body></html>`;
}

(async () => {
  const browser = await chromium.launch({headless: true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try {
    const page = await browser.newPage();
    await page.addInitScript(() => {
      localStorage.setItem('htmx-history-cache', JSON.stringify([{url:'/tracking/',content:'<div id="legacy">Old cached body</div>',title:'Old page',scroll:0}]));
    });
    const requests = [], errors = [], prints = [];
    let failNext = 0, malformedNext = false;
    async function assertQuietFeedback() {
      const feedback = await page.locator('#filter-update-status').evaluate(el => ({
        hidden:el.hidden, live:el.getAttribute('aria-live'),
        position:getComputedStyle(el).position, height:el.getBoundingClientRect().height,
        width:el.getBoundingClientRect().width, clip:getComputedStyle(el).clip,
        retryHidden:el.querySelector('[data-filter-retry]').hidden,
      }));
      assert.equal(feedback.hidden,false,'routine feedback must remain accessible');
      assert.equal(feedback.live,'polite');
      assert.equal(feedback.position,'absolute','routine feedback must occupy no layout space');
      assert.equal(feedback.height,1);
      assert.equal(feedback.width,1);
      assert.notEqual(feedback.clip,'auto','routine feedback must be visually clipped');
      assert.equal(feedback.retryHidden,true);
    }
    page.on('pageerror', e => { errors.push(e.message); console.error('Browser error:',e.message); });
    page.on('console', event => { if (event.type() === 'error') console.error('Browser console:',event.text()); });
    await page.route('http://filters.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname.startsWith('/static/')) {
        const path = url.pathname === '/static/vendor/htmx.min.js' && process.env.HTMX_SCRIPT_PATH ? process.env.HTMX_SCRIPT_PATH :
          url.pathname === '/static/vendor/bootstrap.min.css' && process.env.BOOTSTRAP_STYLE_PATH ? process.env.BOOTSTRAP_STYLE_PATH : resolve(root, url.pathname.slice(1));
        return route.fulfill({status: existsSync(path) ? 200 : 404, contentType:url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body:existsSync(path) ? readFileSync(path,'utf8') : ''});
      }
      if (url.pathname === '/print-log/') {
        prints.push(new URLSearchParams(request.postData()));
        return route.fulfill({status:200, body:'{}', contentType:'application/json'});
      }
      if (url.pathname === '/autocomplete/') return route.fulfill({status:200, body:'{"results":["memo"]}', contentType:'application/json'});
      if (request.headers()['hx-request']) {
        requests.push(url);
        if (failNext) { const status = failNext; failNext = 0; return route.fulfill({status, body:'Update failed'}); }
        if (malformedNext) { malformedNext = false; return route.fulfill({status:200, contentType:'text/html', body:'<p>Unavailable</p>'}); }
        await delay(150);
      }
      return route.fulfill({status:200, contentType:'text/html', body:html(url.searchParams)});
    });
    await page.goto('http://filters.test/tracking/');
    await assertQuietFeedback();
    const initialMainTop = await page.locator('#main-content').evaluate(el => el.getBoundingClientRect().top);
    await page.evaluate(() => { window.__stablePage = 7; window.__printCount = 0; window.print = () => { window.__printCount++; window.dispatchEvent(new Event('beforeprint')); }; });
    await page.locator('#more').evaluate(el => { el.open = true; });
    await page.locator('#office').focus();
    await page.selectOption('#office','2');
    await page.waitForFunction(() => document.querySelector('#main-content').getAttribute('aria-busy') === 'true');
    await assertQuietFeedback();
    assert.equal(await page.locator('#main-content').evaluate(el => el.getBoundingClientRect().top),initialMainTop);
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 2'), null, {timeout:4000});
    await assertQuietFeedback();
    assert.equal(await page.evaluate(() => window.__stablePage),7,'filtering must not reload the document');
    assert.equal(await page.locator('[data-filter-fallback]').first().isVisible(),false);
    assert.equal(await page.evaluate(() => document.activeElement.id),'office');
    assert.equal(await page.locator('#more').evaluate(el => el.open),true);
    assert.deepEqual(requests.at(-1).searchParams.getAll('status'),['RECEIVED','IN_PROCESS']);
    assert.equal(requests.at(-1).searchParams.get('per_page'),'100');
    assert.equal(await page.locator('.column-chart').evaluate(el => el.classList.contains('column-chart--measured')),true);
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('#results')?.textContent.includes('office 1'), null, {timeout:3000});
    await page.goForward();
    await page.waitForFunction(() => document.querySelector('#results')?.textContent.includes('office 2'));
    await page.locator('#relevance').fill('65');
    await page.locator('#relevance').dispatchEvent('input');
    assert.equal(await page.locator('#relevance-value').textContent(),'65','readout must work after replacement');
    await page.locator('[data-print-trigger]').click();
    await page.waitForFunction(() => window.__printCount === 1);
    await delay(100);
    assert.equal(prints.at(-1).get('reference'),'2','print auditing must use the current content');
    const before = requests.length;
    await page.selectOption('#office','1');
    await page.selectOption('#office','3');
    await page.selectOption('#month','2026-10');
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 3, 2026-10'));
    assert.equal(requests.length - before,1,'rapid choices across forms should be combined');
    assert.equal(new URL(page.url()).searchParams.get('office'),'3');
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 2'));
    assert.equal(await page.evaluate(() => window.__stablePage),7);
    await page.locator('#relevance').fill('75');
    await page.locator('#relevance').dispatchEvent('input');
    assert.equal(await page.locator('#relevance-value').textContent(),'75','history-restored search controls must be reinitialized');
    await page.goForward();
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 3'));
    failNext = 500;
    await page.selectOption('#office','2');
    await page.waitForFunction(() => !document.querySelector('[data-filter-retry]').hidden);
    assert.equal(await page.locator('[data-filter-retry]').isVisible(),true,'errors must visibly offer recovery');
    assert.match(await page.locator('#results').textContent(),/office 3/,'failed fetch must leave previous results visible');
    await page.locator('[data-filter-retry]').click();
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 2'));
    await assertQuietFeedback();
    failNext = 403;
    await page.selectOption('#office','3');
    await page.waitForFunction(() => !document.querySelector('[data-filter-retry]').hidden);
    assert.equal(await page.locator('[data-filter-retry]').isVisible(),true,'permission errors must visibly offer recovery');
    assert.match(await page.locator('#results').textContent(),/office 2/);
    await page.locator('[data-filter-retry]').click();
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 3'));
    await assertQuietFeedback();
    malformedNext = true;
    await page.selectOption('#office','2');
    await page.waitForFunction(() => !document.querySelector('[data-filter-retry]').hidden, null, {timeout:3000});
    await delay(100);
    assert.equal(await page.locator('[data-filter-retry]').isVisible(),true,'malformed successful responses must keep recovery available');
    assert.match(await page.locator('#results').textContent(),/office 3/);
    await page.locator('[data-filter-retry]').click();
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 2'));
    await page.locator('[data-filter-reset]').click();
    await page.waitForFunction(() => document.querySelector('#office').value === '1');
    assert.equal(await page.evaluate(() => window.__stablePage),7);
    const nextRequest = requests.length + 1;
    await page.selectOption('#office','2');
    while (requests.length < nextRequest) await delay(10);
    await page.selectOption('#office','3');
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 3'), null, {timeout:4000});
    assert.equal(new URL(page.url()).searchParams.get('office'),'3','a response during the next debounce must not erase the latest choice');
    assert.deepEqual(errors,[]);
    const noJs = await browser.newPage({javaScriptEnabled:false});
    await noJs.route('http://filters.test/**', route => route.fulfill({status:200, contentType:'text/html', body:html()}));
    await noJs.goto('http://filters.test/tracking/');
    assert.equal(await noJs.locator('[data-filter-fallback]').first().isVisible(),true);
    await checkExpiredSession(browser);
    console.log('PASS: hidden routine feedback with no layout space, visible server/permission errors, recovery, automatic updates, repeated filters, combined selections, focus, disclosures, charts, print, history, reset and no-JS fallback');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

async function checkExpiredSession(browser) {
  let expired = false;
  const loginPosts = [];
  // Use a real local redirect: route interception does not re-route every hop.
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname.startsWith('/static/')) {
      const path = url.pathname === '/static/vendor/htmx.min.js' && process.env.HTMX_SCRIPT_PATH ? process.env.HTMX_SCRIPT_PATH :
        url.pathname === '/static/vendor/bootstrap.min.css' && process.env.BOOTSTRAP_STYLE_PATH ? process.env.BOOTSTRAP_STYLE_PATH : resolve(root, url.pathname.slice(1));
      response.writeHead(existsSync(path) ? 200 : 404, {'Content-Type':url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      response.end(existsSync(path) ? readFileSync(path) : '');
      return;
    }
    if (url.pathname === '/accounts/login/') {
      if (request.method === 'POST') loginPosts.push(url.pathname);
      response.writeHead(200, {'Content-Type':'text/html'});
      // The real sign-in page also owns #main-content and posts to its URL.
      response.end('<!doctype html><html><body><main id="main-content"><form method="post"><label>Username<input name="username"></label><button type="submit">Sign In</button></form></main></body></html>');
      return;
    }
    if (expired) {
      response.writeHead(302, {Location:'/accounts/login/?next='+encodeURIComponent(url.pathname+url.search)});
      response.end();
      return;
    }
    response.writeHead(200, {'Content-Type':'text/html'});
    response.end(html(url.searchParams));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    for (const navigation of ['back','forward','filter']) {
      const page = await browser.newPage();
      let insertedSignIn = false;
      await page.exposeFunction('recordInsertedSignIn', () => { insertedSignIn = true; });
      expired = false;
      await page.goto('http://127.0.0.1:'+server.address().port+'/tracking/');
      if (navigation !== 'filter') {
        await page.selectOption('#office','2');
        await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 2'));
        if (navigation === 'forward') {
          await page.goBack();
          await page.waitForFunction(() => document.querySelector('#results').textContent.includes('office 1'));
        }
      }
      await page.evaluate(() => {
        new MutationObserver(() => {
          if (document.querySelector('#sidebar') && document.querySelector('input[name="username"]')) window.recordInsertedSignIn();
        }).observe(document.querySelector('#main-content'), {childList:true,subtree:true});
      });
      expired = true;
      if (navigation === 'back') await page.goBack();
      else if (navigation === 'forward') await page.goForward();
      else await page.selectOption('#office','2');
      await page.waitForSelector('input[name="username"]');
      assert.equal(new URL(page.url()).pathname,'/accounts/login/',`${navigation}: expired sessions must navigate to the sign-in URL`);
      assert.equal(await page.locator('#sidebar').count(),0,`${navigation}: sign-in must replace the authenticated shell`);
      assert.equal(insertedSignIn,false,`${navigation}: sign-in must never be inserted into the authenticated shell`);
      const signInRequest = page.waitForRequest(request => request.method() === 'POST');
      await page.locator('button[type="submit"]').click();
      assert.equal(new URL((await signInRequest).url()).pathname,'/accounts/login/',`${navigation}: sign-in must post to the login route`);
      await page.waitForLoadState();
      await page.close();
      console.log(`PASS: expired session ${navigation} navigates to sign-in with the correct form destination`);
    }
    assert.deepEqual(loginPosts,['/accounts/login/','/accounts/login/','/accounts/login/']);
  } finally { await new Promise(resolve => server.close(resolve)); }
}
