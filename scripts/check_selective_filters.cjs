/* Real Chrome + HTMX regression fixture. No app credentials or network access.
 * NODE_PATH must contain Playwright. Optional overrides: HTMX_SCRIPT_PATH,
 * PLAYWRIGHT_CHROMIUM_EXECUTABLE, AUTO_FILTER_SCRIPT_PATH. FILTER_TEST selects
 * scenario names with a case-insensitive regular expression (unset runs all).
 */
const {readFileSync, existsSync} = require('node:fs');
const {resolve} = require('node:path');
const {createServer} = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = resolve(__dirname, '..');
const htmxPath = process.env.HTMX_SCRIPT_PATH || [
  resolve(root, 'static/vendor/htmx.min.js'),
  resolve(root, '.vercel/static-check-R2EnkA/vendor/htmx.min.js'),
  resolve(process.env.TEMP || '', 'doctrack-htmx-1.9.12.min.js'),
].find(existsSync);
assert.ok(htmxPath, 'Provide a local HTMX 1.9.12 file with HTMX_SCRIPT_PATH.');
// Every scenario in one run must exercise the same production source, even
// while another agent is fixing a failure identified by the preceding run.
const autoFilterSource = readFileSync(process.env.AUTO_FILTER_SCRIPT_PATH || resolve(root, 'static/js/auto-filters.js'));
const attrs = readFileSync(resolve(root, 'templates/partials/_auto_filter_attrs.html'), 'utf8')
  .replace('{{ request.path }}', '/fixture/')
  .replace(/ data-filter-mode="\{\{[^}]+\}\}"/, '')
  .replace(/\{% if filter_sync %\}[\s\S]*?\{% endif %\}/, '');
const delay = ms => new Promise(done => setTimeout(done, ms));
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function fixture(params = new URLSearchParams()) {
  const selectedStatus = new URLSearchParams(params), removedStatus = new URLSearchParams(params);
  selectedStatus.set('status', 'done');
  removedStatus.delete('status');
  const value = (name, fallback = '') => esc(params.get(name) || fallback);
  const options = (name, choices, fallback) => choices.map(choice => `<option value="${choice}"${(params.get(name) || fallback) === choice ? ' selected' : ''}>${choice}</option>`).join('');
  return `<!doctype html><html><head><title>Selective filters fixture</title><style>
    *{box-sizing:border-box}html{overflow-anchor:none}body{margin:0;font:16px Arial}#sidebar{height:120px}main{padding:24px}section{padding:18px;border:1px solid #ccc;margin-bottom:24px}form{margin-bottom:12px}label{display:inline-block;margin-right:12px}input,select,button{font:inherit}button,a{cursor:pointer}.visually-hidden{position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important}#filter-update-status.is-error{position:fixed;top:0;right:0;z-index:10;background:#fff;padding:16px}.table-responsive{overflow:auto;width:100%}table{width:1800px}td{padding:18px;border:1px solid #eee}.spacer{height:500px}
  </style></head><body><aside id="sidebar">Authenticated fixture shell</aside>
    <div id="filter-update-status" role="status" aria-live="polite" hidden><span data-filter-message></span><button type="button" data-filter-retry hidden>Retry</button></div>
    <main id="main-content" tabindex="-1" hx-history-elt hx-history="false">
      <section id="manual-region" data-filter-region><h1>Manual search</h1>
        <form id="manual-filters" method="get" action="/fixture/" ${attrs}>
          <label>Query<input id="query" name="q" value="${value('q')}"></label>
          <label>From<input id="date-from" type="date" name="date_from" value="${value('date_from')}"></label>
          <label>To<input id="date-to" type="date" name="date_to" value="${value('date_to')}"></label>
          <label>Status<select id="manual-status" name="status">${options('status', ['all','open','done'], 'all')}</select></label>
          <label><input id="archived" type="checkbox" name="archived" value="yes"${params.has('archived') ? ' checked' : ''}>Archived</label>
          <label>Relevance<input id="relevance" type="range" name="relevance" min="0" max="100" value="${value('relevance','25')}"></label>
          <button id="search" type="submit">Search</button>
        </form>
        <a id="clear" href="/fixture/" data-filter-reset data-filter-discard>Clear</a>
        <a id="mode" class="pill-toggle" href="/fixture/?view=cards" data-filter-discard>Cards</a>
        <a id="status-pill" class="pill-toggle" href="/fixture/?${esc(selectedStatus.toString())}">Done</a>
        <nav class="active-filters"><a id="remove-status" href="/fixture/?${esc(removedStatus.toString())}">Remove status</a></nav>
      </section>
      <section id="dashboard-region" data-filter-region><h2>Dashboard</h2>
        ${[['office',['1','2','3'],'1'],['month',['09','10','11'],'09'],['stage',['all','received','done'],'all']].map(([name, choices, fallback]) => `<form id="dashboard-${name}" method="get" action="/fixture/" data-filter-mode="change" data-filter-sync="dashboard" ${attrs}><label>${name}<select id="${name}" name="${name}">${options(name,choices,fallback)}</select></label><button type="submit" data-filter-fallback>Apply</button></form>`).join('')}
        <form id="reports-filter" method="get" action="/fixture/" data-filter-mode="change" data-filter-sync="reports" ${attrs}><label>Report scope<select id="scope" name="scope">${options('scope',['own','all'],'own')}</select></label><button type="submit" data-filter-fallback>Apply</button></form>
      </section>
      <section id="upper-region" data-filter-region style="height:${params.get('office') === '2' ? 900 : 430}px"><h2>Variable height upper panel</h2></section>
      <section id="results-region" data-filter-region><h2 id="results">Results ${esc(params.toString())}</h2><p id="viewport-marker">Visible result marker</p>
        <form id="results-filter" method="get" action="/fixture/" data-filter-mode="change" ${attrs}><label>Result size<select id="size" name="size"><option value="a">a</option><option value="b"${params.get('size') === 'b' ? ' selected' : ''}>b</option></select></label><button type="submit" data-filter-fallback>Apply</button></form>
        <div id="results-table" class="table-responsive" data-filter-scroll><table><tbody>${Array.from({length:12},(_,i)=>`<tr id="result-row-${i+1}"><td>Row ${i+1}</td><td>Wide content ${i+1}</td></tr>`).join('')}</tbody></table></div>
        <a id="next" class="page-link" href="/fixture/?${esc(new URLSearchParams({...Object.fromEntries(params),page:String((parseInt(params.get('page'),10)||1)+1)}).toString())}">Next</a>
        <details id="result-details"><summary>Details</summary>Open details survive updates.</details>
      </section><div class="spacer"></div>
    </main><script src="/htmx.js"></script><script src="/auto-filters.js"></script></body></html>`;
}

async function main() {
  const requests = [], errors = [];
  let nextResponse = null;
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/htmx.js' || url.pathname === '/auto-filters.js') {
      res.writeHead(200, {'Content-Type':'text/javascript'});
      res.end(url.pathname === '/htmx.js' ? readFileSync(htmxPath) : autoFilterSource);
      return;
    }
    if (url.pathname === '/accounts/login/') {
      res.writeHead(200, {'Content-Type':'text/html'});
      res.end('<!doctype html><html><body><main id="main-content"><form method="post"><input name="username"><button type="submit">Sign in</button></form></main></body></html>');
      return;
    }
    const isResult = Boolean(req.headers['hx-request']);
    if (isResult) requests.push(url);
    const behavior = isResult ? nextResponse || {} : {};
    // Chrome can retry a GET whose socket closes before response headers. Keep
    // dropping that retry too; only the visible Retry action clears the fault.
    if (isResult && !behavior.drop) nextResponse = null;
    setTimeout(() => {
      if (behavior.drop) return req.socket.destroy();
      if (behavior.login) {
        res.writeHead(302, {Location:'/accounts/login/?next='+encodeURIComponent(url.pathname+url.search)});
        return res.end();
      }
      res.writeHead(behavior.status || 200, {'Content-Type':'text/html'});
      let body = behavior.malformed ? '<p>Missing main region</p>' : fixture(url.searchParams);
      if (behavior.removeResults) body = body.replace(/<section id="results-region"[\s\S]*?<\/section>/, '');
      res.end(body);
    }, behavior.delay || 0);
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  try {
    let failed = 0, ran = 0;
    let page, insertedLogin = false;
    async function test(name, check, path = '/fixture/') {
      if (process.env.FILTER_TEST && !new RegExp(process.env.FILTER_TEST,'i').test(name)) return;
      ran++;
      requests.length = 0;
      errors.length = 0;
      nextResponse = null;
      const started = Date.now();
      if (!page) {
        page = await browser.newPage({viewport:{width:1280,height:800}});
        page.setDefaultTimeout(6000);
        page.on('pageerror', error => errors.push(error.message));
        await page.exposeFunction('recordInsertedLogin', () => {insertedLogin=true;});
        await page.addInitScript(() => {
          window.__settles = 0;
          document.addEventListener('htmx:afterSettle', () => { window.__settles++; });
        });
      }
      try {
        await page.goto(base+path);
        await page.waitForFunction(() => Boolean(window.htmx));
        await check(page);
        assert.deepEqual(errors,[], 'No browser errors');
        console.log('PASS: '+name+` (${Date.now()-started}ms)`);
      } catch (error) {
        failed++;
        console.error('FAIL: '+name+'\n'+error.stack);
      }
    }
    async function settled(page, count = 1) {
      await page.waitForFunction(n => window.__settles >= n, count);
      await delay(80);
    }
    async function draft(page, text = 'unfinished draft') {
      await page.locator('#query').fill(text);
      await page.locator('#date-from').fill('2026-10-01');
      await page.locator('#date-to').fill('2026-10-20');
      await page.selectOption('#manual-status','open');
      await page.locator('#archived').check();
      await page.locator('#relevance').fill('75');
      await page.locator('#relevance').dispatchEvent('change');
    }
    async function assertDraft(page, text = 'unfinished draft') {
      assert.equal(await page.locator('#query').inputValue(),text,'Unsubmitted text survives independent updates');
      assert.equal(await page.locator('#date-from').inputValue(),'2026-10-01');
      assert.equal(await page.locator('#date-to').inputValue(),'2026-10-20');
      assert.equal(await page.locator('#manual-status').inputValue(),'open');
      assert.equal(await page.locator('#archived').isChecked(),true);
      assert.equal(await page.locator('#relevance').inputValue(),'75');
    }
    async function markerY(page) {
      return page.locator('#viewport-marker').evaluate(el => el.getBoundingClientRect().top);
    }

    await test('manual date edits wait for Search and submit both values once', async page => {
      await page.locator('#date-from').fill('2026-10-01');
      await page.locator('#date-to').fill('2026-10-20');
      await page.locator('#date-to').blur();
      await delay(450);
      assert.equal(requests.length,0,'Manual date edits must send zero result requests before Search');
      await page.locator('#search').click();
      await settled(page);
      assert.equal(requests.length,1,'Search must send exactly one result request');
      assert.equal(requests[0].searchParams.get('date_from'),'2026-10-01');
      assert.equal(requests[0].searchParams.get('date_to'),'2026-10-20');
    });

    await test('manual default and submit mode keep dropdown, checkbox and range edits pending', async page => {
      for (const mode of ['default','submit']) {
        if (mode === 'submit') await page.locator('#manual-filters').evaluate(form => form.setAttribute('data-filter-mode','submit'));
        await page.selectOption('#manual-status',mode === 'default' ? 'open' : 'done');
        await page.locator('#archived').setChecked(mode === 'default');
        await page.locator('#relevance').fill(mode === 'default' ? '50' : '80');
        await page.locator('#relevance').dispatchEvent('change');
        await delay(450);
        assert.equal(requests.length,0,`${mode} mode must wait for Search for every control type`);
      }
      await page.locator('#search').click();
      await settled(page);
      assert.equal(requests.length,1);
      assert.equal(requests[0].searchParams.get('status'),'done');
      assert.equal(requests[0].searchParams.get('relevance'),'80');
    });

    await test('opted-in selects debounce and serialize only their sync group', async page => {
      await page.locator('#query').fill('manual draft must not leak');
      await page.selectOption('#scope','all');
      await settled(page);
      assert.equal(requests.length,1);
      assert.equal(requests[0].searchParams.get('scope'),'all');
      assert.equal(requests[0].searchParams.has('office'),false,'Other sync groups must not join Reports');
      assert.equal(requests[0].searchParams.has('q'),false,'Manual drafts must not join Reports');
      await page.selectOption('#office','2');
      await page.selectOption('#month','10');
      await page.selectOption('#stage','received');
      await delay(180);
      assert.equal(requests.length,1,'Auto select changes must keep the 300ms debounce');
      await settled(page,2);
      assert.equal(requests.length,2,'Rapid Dashboard changes send one combined request');
      assert.deepEqual(['office','month','stage'].map(key => requests[1].searchParams.get(key)),['2','10','received']);
      assert.equal(requests[1].searchParams.has('scope'),false,'Reports scope must not leak into Dashboard');
      assert.equal(requests[1].searchParams.has('q'),false,'Manual drafts must not leak into Dashboard');
    });

    await test('only selects autosubmit inside change-mode forms', async page => {
      await page.locator('#manual-filters').evaluate(form => form.setAttribute('data-filter-mode','change'));
      await page.locator('#date-from').fill('2026-10-01');
      await page.locator('#archived').check();
      await page.locator('#relevance').fill('70');
      await page.locator('#relevance').dispatchEvent('change');
      await delay(450);
      assert.equal(requests.length,0,'Change mode must still leave non-select controls manual');
      await page.selectOption('#manual-status','open');
      await settled(page);
      assert.equal(requests.length,1,'A change-mode select must autosubmit');
    });

    await test('unfinished manual values survive independent auto updates and pagination', async page => {
      await draft(page);
      await page.selectOption('#office','2');
      await settled(page);
      assert.equal(requests.length,1);
      assert.equal(requests[0].searchParams.has('q'),false);
      await assertDraft(page);
      await page.locator('#next').click();
      await settled(page,2);
      assert.equal(requests[1].searchParams.get('page'),'2');
      assert.equal(requests[1].searchParams.has('q'),false);
      await assertDraft(page);
      await page.locator('#search').click();
      await settled(page,3);
      assert.equal(requests[2].searchParams.get('q'),'unfinished draft');
      assert.equal(requests[2].searchParams.get('date_from'),'2026-10-01');
      assert.equal(requests[2].searchParams.get('date_to'),'2026-10-20');
    });

    for (const removal of [false, true]) await test(`explicit pill ${removal ? 'removal' : 'selection'} overrides its field while retaining unrelated drafts`, async page => {
      await draft(page);
      await page.locator(removal ? '#remove-status' : '#status-pill').click();
      await settled(page);
      assert.equal(await page.locator('#manual-status').inputValue(),removal ? 'all' : 'done','The explicit link selection must agree with the results');
      assert.equal(new URL(page.url()).searchParams.get('status'),removal ? null : 'done');
      assert.equal(await page.locator('#query').inputValue(),'unfinished draft');
      assert.equal(await page.locator('#date-from').inputValue(),'2026-10-01');
      assert.equal(await page.locator('#date-to').inputValue(),'2026-10-20');
      assert.equal(requests[0].searchParams.has('q'),false,'Unrelated drafts must not enter the pill request');
    }, removal ? '/fixture/?status=done' : '/fixture/');

    await test('Clear and deliberate mode changes discard pending manual edits', async page => {
      for (const link of ['#clear','#mode']) {
        await draft(page);
        const count = await page.evaluate(() => window.__settles);
        await page.locator(link).click();
        await settled(page,count+1);
        assert.equal(await page.locator('#query').inputValue(),'','Clear/mode changes must accept server field values');
        assert.equal(await page.locator('#date-from').inputValue(),'');
        assert.equal(await page.locator('#manual-status').inputValue(),'all');
        assert.equal(await page.locator('#archived').isChecked(),false);
      }
    });

    await test('edits made during a manual Search request survive its response', async page => {
      nextResponse = {delay:600};
      await page.locator('#query').fill('submitted query');
      await page.locator('#search').click();
      await page.waitForFunction(() => document.querySelector('#main-content').getAttribute('aria-busy')==='true');
      await page.locator('#query').fill('new typing while loading');
      await page.locator('#date-from').fill('2026-10-05');
      await settled(page);
      assert.equal(requests.length,1,'Typing while loading must not create another request');
      assert.equal(requests[0].searchParams.get('q'),'submitted query');
      assert.equal(await page.locator('#query').inputValue(),'new typing while loading');
      assert.equal(await page.locator('#date-from').inputValue(),'2026-10-05');
    });

    await test('stale delayed responses cannot erase a newer auto choice', async page => {
      nextResponse = {delay:700};
      await page.selectOption('#office','2');
      await page.waitForFunction(() => document.querySelector('#main-content').getAttribute('aria-busy') === 'true');
      await page.selectOption('#office','3');
      await settled(page);
      await delay(850);
      assert.equal(await page.locator('#office').inputValue(),'3');
      assert.equal(new URL(page.url()).searchParams.get('office'),'3');
      assert.match(await page.locator('#results').textContent(),/office=3/);
    });

    await test('network, permission, server and malformed failures retain results and retry successfully', async page => {
      let settleCount = 0;
      for (const behavior of [{drop:true},{status:403},{status:500},{malformed:true}]) {
        nextResponse = behavior;
        const before = await page.locator('#results').textContent();
        await page.selectOption('#office',settleCount % 2 ? '3' : '2');
        await page.waitForFunction(() => !document.querySelector('[data-filter-retry]').hidden);
        assert.equal(await page.locator('#results').textContent(),before,'Failed request must retain previous results');
        assert.equal(await page.locator('#main-content').getAttribute('aria-busy'),null,'Failed request must clear busy state');
        nextResponse = null;
        await page.locator('[data-filter-retry]').click();
        await settled(page,++settleCount);
        assert.equal(await page.locator('[data-filter-retry]').isVisible(),false);
      }
    });

    await test('viewport anchor, focused control and horizontal table survive changed panel heights', async page => {
      await page.locator('#result-details').evaluate(el => {el.open=true;});
      await page.locator('#results-table').evaluate(el => {el.scrollLeft=370;});
      await page.locator('#viewport-marker').evaluate(el => window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-260));
      const y = await markerY(page);
      await page.locator('#office').evaluate(el => {el.focus({preventScroll:true});});
      await page.selectOption('#office','2');
      await settled(page);
      const actual = await markerY(page);
      assert.ok(Math.abs(actual-y)<4,`Visible result marker must retain its viewport position when an upper panel grows (before ${y}, after ${actual})`);
      assert.equal(await page.evaluate(() => document.activeElement.id),'office','Filter focus survives replacement');
      assert.equal(await page.locator('#results-table').evaluate(el=>el.scrollLeft),370,'Horizontal table position survives replacement');
      assert.equal(await page.locator('#result-details').evaluate(el=>el.open),true);
    });

    await test('latest viewport at response time wins over request-time scroll position', async page => {
      nextResponse = {delay:600};
      await page.locator('#viewport-marker').evaluate(el => window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-100));
      await page.selectOption('#office','2');
      await page.waitForFunction(() => document.querySelector('#main-content').getAttribute('aria-busy')==='true');
      await page.locator('#viewport-marker').evaluate(el => window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-340));
      const latest = await markerY(page);
      await settled(page);
      const actual = await markerY(page);
      assert.ok(Math.abs(actual-latest)<4,`Response must preserve the latest viewport, including user scrolling during the request (before ${latest}, after ${actual})`);
    });

    await test('removed viewport anchor centers a surviving source region', async page => {
      nextResponse = {removeResults:true};
      await page.locator('#viewport-marker').evaluate(el => window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-200));
      await page.selectOption('#office','2');
      await settled(page);
      assert.equal(await page.locator('#results-region').count(),0);
      const placement = await page.locator('#dashboard-region').evaluate(el => ({center:el.getBoundingClientRect().top+el.getBoundingClientRect().height/2,viewport:window.innerHeight}));
      assert.ok(Math.abs(placement.center-placement.viewport/2)<4,`A removed viewport anchor should center the surviving source region (center ${placement.center}, viewport ${placement.viewport})`);
    });

    await test('removed viewport and source regions restore a clamped raw scroll position', async page => {
      nextResponse = {removeResults:true};
      await page.locator('#viewport-marker').evaluate(el => window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-150));
      const previous = await page.evaluate(() => window.scrollY);
      await page.selectOption('#size','b');
      await settled(page);
      const actual = await page.evaluate(() => ({scroll:window.scrollY,max:Math.max(0,document.documentElement.scrollHeight-window.innerHeight)}));
      assert.ok(Math.abs(actual.scroll-Math.min(previous,actual.max))<4,'No surviving anchor/source region must restore the prior raw scroll position, clamped to document bounds');
    });

    await test('intentional user scroll between swap and settle is not overwritten', async page => {
      await page.evaluate(() => {
        window.htmx.config.defaultSettleDelay=700;
        document.addEventListener('htmx:afterSwap', () => {
          window.__swapped=true;
        }, {once:true});
      });
      await page.locator('#viewport-marker').evaluate(el => window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-200));
      await page.selectOption('#office','2');
      await page.waitForFunction(() => window.__swapped);
      await page.mouse.move(800,400);
      await page.mouse.wheel(0,160);
      await delay(70);
      const intentionalScroll = await page.evaluate(() => window.scrollY);
      await settled(page);
      assert.equal(await page.evaluate(() => window.scrollY),intentionalScroll,'User scroll after swap must cancel pending settle restoration');
    });

    await test('history navigation restores server filters and keeps the current shell', async page => {
      await page.evaluate(() => {window.__fixtureShell=17;});
      await page.selectOption('#office','2');
      await settled(page);
      await draft(page,'draft to discard on history navigation');
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('#office')?.value==='1');
      assert.equal(await page.locator('#query').inputValue(),'','History must restore server fields instead of carrying manual drafts to another entry');
      assert.equal(await page.locator('#date-from').inputValue(),'');
      await page.goForward();
      await page.waitForFunction(() => document.querySelector('#office')?.value==='2');
      assert.equal(await page.evaluate(() => window.__fixtureShell),17,'History must preserve the authenticated document shell');
      await page.selectOption('#office','3');
      await page.waitForFunction(() => document.querySelector('#results')?.textContent.includes('office=3'));
    });

    for (const [failure, behavior] of [
      ['malformed 200',{malformed:true}], ['403',{status:403}],
      ['500',{status:500}], ['network',{drop:true}],
    ]) {
      await test(`history failed ${failure} response preserves results and retries the requested URL`, async page => {
        await page.selectOption('#office','2');
        await settled(page);
        const before = await page.locator('#results').textContent();
        nextResponse = behavior;
        const completed = behavior.drop
          ? page.waitForEvent('requestfailed',{predicate:req=>req.headers()['hx-history-restore-request']==='true'})
          : page.waitForResponse(response=>response.request().headers()['hx-history-restore-request']==='true');
        await page.goBack();
        await completed;
        await delay(100);
        assert.ok(await page.locator('form[data-auto-filter]').count()>0,'Failed history response must retain the existing filter forms');
        assert.equal(await page.locator('#results').textContent(),before,'Failed history response must retain the existing results');
        await page.waitForFunction(() => !document.querySelector('[data-filter-retry]').hidden);
        assert.equal(await page.locator('#main-content').getAttribute('aria-busy'),null);
        nextResponse = null;
        await page.locator('[data-filter-retry]').click();
        await page.waitForFunction(() => document.querySelector('#office')?.value==='1');
        assert.equal(new URL(page.url()).searchParams.get('office'),null,'History Retry must fetch the failed Back URL');
        assert.equal(await page.locator('[data-filter-retry]').isVisible(),false);
      });
    }

    await test('stale history Back response cannot overwrite a newer Back destination', async page => {
      await page.selectOption('#office','2');
      await settled(page);
      await page.selectOption('#office','3');
      await settled(page,2);
      nextResponse = {delay:850};
      const historyStarted = page.waitForRequest(req=>req.headers()['hx-history-restore-request']==='true');
      await page.goBack();
      await historyStarted;
      await delay(100);
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('#office')?.value==='1');
      await delay(1000);
      assert.equal(await page.locator('#office').inputValue(),'1','The older delayed Back response must not replace the newest Back destination');
      assert.equal(new URL(page.url()).searchParams.get('office'),null);
    });

    await test('pending history Back response cannot overwrite a newer normal filter', async page => {
      await page.selectOption('#office','2');
      await settled(page);
      nextResponse = {delay:850};
      const historyStarted = page.waitForRequest(req=>req.headers()['hx-history-restore-request']==='true');
      await page.goBack();
      await historyStarted;
      await delay(100);
      await page.selectOption('#office','3');
      await page.waitForFunction(() => document.querySelector('#office')?.value==='3' && document.querySelector('#results')?.textContent.includes('office=3'));
      await delay(1000);
      assert.equal(await page.locator('#office').inputValue(),'3','A delayed Back response must not overwrite a newer normal filter response');
      assert.equal(new URL(page.url()).searchParams.get('office'),'3');
    });

    await test('pagination keyboard focus survives links without IDs', async page => {
      await page.locator('#next').evaluate(link=>link.removeAttribute('id'));
      await page.locator('a.page-link').focus();
      await page.keyboard.press('Enter');
      await settled(page);
      const focused = await page.evaluate(() => {
        const element=document.activeElement;
        return {tag:element.tagName,region:element.closest('[data-filter-region]')?.id,link:element.matches('a.page-link'),id:element.id};
      });
      assert.notEqual(focused.tag,'BODY','Keyboard pagination must restore focus instead of dropping it to BODY');
      assert.ok(focused.link || focused.region==='results-region' || focused.id==='main-content','Keyboard pagination must focus the equivalent pager or a meaningful results fallback');
      assert.equal(new URL(page.url()).searchParams.get('page'),'2');
    });

    for (const direction of ['filter','back','forward']) {
      await test(`expired session ${direction} navigates the full shell to the login URL`, async page => {
        if (direction !== 'filter') {
          await page.selectOption('#office','2');
          await settled(page);
          if (direction === 'forward') {
            await page.goBack();
            await page.waitForFunction(() => document.querySelector('#office')?.value==='1');
          }
        }
        insertedLogin = false;
        await page.evaluate(() => new MutationObserver(() => {
          if (document.querySelector('#sidebar') && document.querySelector('input[name="username"]')) window.recordInsertedLogin();
        }).observe(document.querySelector('#main-content'), {childList:true,subtree:true}));
        nextResponse={login:true};
        if (direction === 'back') await page.goBack();
        else if (direction === 'forward') await page.goForward();
        else await page.selectOption('#office','2');
        await page.waitForSelector('input[name="username"]');
        assert.equal(new URL(page.url()).pathname,'/accounts/login/');
        assert.equal(await page.locator('#sidebar').count(),0,'Login must replace the authenticated shell');
        assert.equal(insertedLogin,false,'Sign-in content must never be inserted into the authenticated shell');
        assert.equal(await page.locator('form').evaluate(form=>new URL(form.action).pathname),'/accounts/login/');
      });
    }
    if (!process.env.FILTER_TEST || /no.?js|fallback/i.test(process.env.FILTER_TEST)) {
      ran++;
      const page = await browser.newPage({javaScriptEnabled:false});
      try {
        await page.goto(base+'/fixture/');
        assert.equal(await page.locator('[data-filter-fallback]').first().isVisible(),true,'No-JS forms retain their Apply fallback');
        await page.locator('#date-from').fill('2026-10-01');
        await page.locator('#date-to').fill('2026-10-20');
        await Promise.all([page.waitForNavigation(),page.locator('#search').click()]);
        assert.equal(new URL(page.url()).searchParams.get('date_from'),'2026-10-01');
        assert.equal(new URL(page.url()).searchParams.get('date_to'),'2026-10-20');
        console.log('PASS: no-JS native Search and auto-form Apply fallback');
      } finally {await page.close();}
    }
    assert.ok(ran,'FILTER_TEST did not match any browser scenarios');
    assert.equal(failed,0,`${failed} selective filter browser regression scenario(s) failed`);
    console.log(`PASS: ${ran} selective filter browser scenarios`);
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise(done => server.close(done));
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
