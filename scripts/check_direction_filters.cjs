/* Verify actual Django responses with the application's Bootstrap, HTMX and JS. */
const {readFileSync, existsSync, mkdirSync} = require('node:fs');
const {resolve, extname} = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

const root = resolve(__dirname, '..');
const output = resolve(root, 'docs/verification/direction-filters');
const vendorDir = process.env.VENDOR_DIR || 'C:/Users/USER/AppData/Local/Temp';
const data = JSON.parse(readFileSync(resolve(root, 'docs/verification/direction-filter-pages.json'), 'utf8'));
const origin = 'http://direction-filters.test';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ||
  'C:/Users/USER/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
mkdirSync(output, {recursive:true});

// Match real paths and repeated query values, regardless of serialization order.
// Empty native form values do not change server filters. Never discard a scope,
// stage, office or other nonempty filter to make a mismatching request pass.
function key(value) {
  const url = new URL(value, origin);
  return url.pathname + '?' + JSON.stringify([...url.searchParams].filter(([, value]) => value !== '').sort());
}
const fixtures = new Map(data.pages.map(page => [key(page.url), page]));
const initial = surface => data.pages.find(page => page.surface === surface && page.selection === 'default');
const mime = file => ({'.js':'application/javascript', '.css':'text/css', '.svg':'image/svg+xml',
                      '.png':'image/png', '.woff2':'font/woff2'}[extname(file)] || 'application/octet-stream');

async function serve(page, requests, failures) {
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
      if (file.startsWith(root + '\\') && existsSync(file)) return route.fulfill({path:file, contentType:mime(file)});
      failures.push(`Missing static asset: ${url.pathname}`);
      return route.fulfill({status:404, body:''});
    }
    if (['/', '/tracking/', '/search/', '/tracking/reports/'].includes(url.pathname)) {
      const fixture = fixtures.get(key(url.href));
      if (!fixture) {
        failures.push(`Unexpected request: ${url.pathname}${url.search}`);
        return route.fulfill({status:404, body:'Unexpected QA fixture request'});
      }
      requests.push({url:url.href, headers:route.request().headers(), surface:fixture.surface});
      const partial = route.request().headers()['hx-target'] === 'action-centre-queue';
      return route.fulfill({contentType:'text/html', body:partial ? fixture.partial_html : fixture.html});
    }
    return route.fulfill({contentType:'application/json', body:'{}'});
  });
}

async function checkState(page, fixture) {
  if (fixture.surface === 'tracking' || fixture.surface === 'search') {
    const table = page.locator(fixture.surface === 'tracking' ? '.tracking-records-table' : '.workspace-search .table-udm');
    assert.equal(await table.getByRole('columnheader', {name:'Received by', exact:true}).count(), 1);
    assert.equal(await table.getByRole('columnheader', {name:'Awaiting receipt', exact:true}).count(), 1);
    const receiptRows = await table.locator('tbody tr:has([data-receipt-offices])').evaluateAll(rows => rows.map(row => {
      const received = row.querySelector('[data-receipt-offices="received"]');
      const pending = row.querySelector('[data-receipt-offices="pending"]');
      const codes = cell => [...cell.querySelectorAll('.office-badge-code')].map(item => item.textContent.trim());
      return {received:codes(received), pending:codes(pending), text:pending.textContent.trim()};
    }));
    for (const row of receiptRows) {
      assert.equal(row.received.some(code => row.pending.includes(code)), false, 'Confirmed and awaiting columns overlap');
      assert.ok(row.text, 'Empty awaiting column must explain the receipt state');
    }
  }
  const nav = page.locator('.direction-filters');
  assert.equal(await nav.count(), 1);
  assert.equal(await nav.locator('.direction-filter-group').count(), 2);
  assert.deepEqual(await nav.locator('.direction-filter-heading strong').allTextContents(), ['Incoming','Outgoing']);
  for (const [index, group] of fixture.groups.entries()) {
    const rendered = nav.locator('.direction-filter-group').nth(index);
    if (group.disabled) {
      assert.equal(await rendered.locator('a').count(), 0);
      assert.equal(await rendered.locator('[aria-disabled="true"]').count(), 4);
      continue;
    }
    assert.equal(await rendered.locator('.direction-filter-stages a').count(), 3);
    for (const stage of group.stages) {
      const label = `${group.label} · ${stage.label}${stage.count !== null ? `, ${stage.count} documents` : ''}`;
      const link = rendered.getByRole('link', {name:label, exact:true});
      assert.equal(await link.count(), 1);
      assert.equal(key(await link.getAttribute('href')), key(stage.href));
      assert.equal(await link.getAttribute('aria-current'), stage.active ? 'true' : null);
      assert.equal(await link.evaluate(element => element.classList.contains('is-active')), stage.active);
      if (stage.count !== null) assert.equal(await link.locator('.desk-chip-count').innerText(), String(stage.count));
    }
    const parentName = `All ${group.label.toLowerCase()} documents${group.count !== null ? `, ${group.count} documents` : ''}`;
    const parent = rendered.getByRole('link', {name:parentName, exact:true});
    assert.equal(await parent.getAttribute('aria-current'), group.active ? 'true' : null);
    assert.equal(await parent.evaluate(element => element.classList.contains('is-active')), group.active);
    if (group.count !== null) {
      assert.equal(await parent.locator('.desk-chip-count').innerText(), String(group.count));
      assert.equal(group.count, group.stages.reduce((sum, stage) => sum + stage.count, 0));
    }
  }
  const active = fixture.groups.flatMap(group => [group.active, ...group.stages.map(stage => stage.active)]).filter(Boolean).length;
  assert.equal(await nav.locator('a.is-active').count(), active);
  if (fixture.selection === 'all-offices') {
    assert.match(await nav.innerText(), /Choose an office to use Incoming and Outgoing/);
  } else {
    const table = fixture.surface === 'dashboard' ? '#action-centre-queue .desk-records-table' :
      fixture.surface === 'search' ? '.workspace-search .table-udm' : '.tracking-records-table';
    const ids = await page.locator(`${table} tbody tr`).evaluateAll(rows => rows.map(row => {
      const link = [...row.querySelectorAll('a[href]')].find(link =>
        /^\/tracking\/\d+\/$/.test(new URL(link.getAttribute('href'), location.origin).pathname));
      return link ? Number(new URL(link.getAttribute('href'), location.origin).pathname.split('/')[2]) : null;
    }).filter(Boolean).sort((a, b) => a - b));
    assert.deepEqual(ids, fixture.record_ids, `Record set for ${fixture.url}`);
    if (fixture.surface === 'dashboard') {
      assert.match(await page.locator('#action-centre-queue .desk-block-title').innerText(), new RegExp(fixture.queue_label, 'i'));
      assert.equal(await page.locator('#action-centre-queue .desk-block-count').innerText(), `Showing ${fixture.total} of ${fixture.total}`);
      assert.equal(key(await page.locator('#action-centre-queue .action-centre-head > a').getAttribute('href')), key(fixture.tracking_url));
    } else if (fixture.surface === 'search') {
      assert.equal(await page.locator('.search-results-summary strong').innerText(), String(fixture.total));
    } else {
      assert.equal(await page.locator('.card-udm-head h2').last().innerText(), `${fixture.total} tracking record${fixture.total === 1 ? '' : 's'}`);
    }
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'Horizontal page overflow');
  const clipped = await nav.evaluate(element => [...element.querySelectorAll('a, strong')].some(child => {
    const rect = child.getBoundingClientRect();
    return rect.left < -1 || rect.right > innerWidth + 1;
  }));
  assert.equal(clipped, false, 'Direction control falls outside the viewport');
}

async function clickAndCheck(page, name, {javascript=true, preserve=false} = {}) {
  const link = page.locator('.direction-filters').getByRole('link', {name:new RegExp(`^${name}(?:, \\d+ documents)?$`)});
  const href = await link.getAttribute('href');
  const activeLabel = await link.getAttribute('aria-label');
  const fixture = fixtures.get(key(href));
  assert.ok(fixture, `Missing linked fixture: ${href}`);
  await link.click();
  await page.waitForFunction(expected => {
    const links = [...document.querySelectorAll('.direction-filters a')];
    const active = links.find(link => link.getAttribute('aria-current') === 'true');
    return active && active.getAttribute('aria-label') === expected;
  }, activeLabel);
  await page.waitForURL(url => key(url.href) === key(href));
  await checkState(page, fixture);
  if (javascript) assert.equal(await page.evaluate(() => window.__directionFilterSentinel), 'kept', 'Unexpected full page reload');
  if (preserve) {
    const query = new URL(page.url()).searchParams;
    assert.equal(query.get('office'), data.office);
    assert.equal(query.get('q'), data.query);
    assert.equal(query.get('per_page'), '100');
    assert.equal(query.get('overdue'), 'no');
    assert.deepEqual(query.getAll('offices').sort(), [...data.raised_by].sort());
    assert.equal(query.has('page'), false);
    if (fixture.surface === 'search') assert.equal(query.get('mode'), 'tracking');
  }
  return fixture;
}

async function runEnhanced(browser, width, theme, surface) {
  const context = await browser.newContext({viewport:{width, height:900}, colorScheme:theme});
  const page = await context.newPage();
  const errors = [], failures = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  try {
    await context.addInitScript(theme => localStorage.setItem('doctrack-theme', theme), theme);
    await serve(page, requests, failures);
    await page.goto(origin + initial(surface).url);
    await page.waitForFunction(() => window.htmx && document.querySelector('.direction-filters a[data-filter-link]'));
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
    assert.equal(await page.locator('html').getAttribute('data-bs-theme'), theme);
    await page.evaluate(() => {window.__directionFilterSentinel = 'kept';});
    await checkState(page, initial(surface));
    const preserve = surface !== 'dashboard';
    const details = surface === 'search' ? '#search-tracking-filter-options' : '#tracking-filter-options';
    if (preserve) await page.locator(details).evaluate(element => {element.open = false;});
    for (const direction of ['Incoming','Outgoing']) {
      for (const stage of ['Pending Receipt','Received','In Process']) {
        const fixture = await clickAndCheck(page, `${direction} · ${stage}`, {preserve});
        const params = new URL(page.url()).searchParams;
        if (surface === 'dashboard') {
          const slug = stage.toLowerCase().replaceAll(' ', '-');
          assert.equal(params.get('desk'), `${direction.toLowerCase()}-${slug}`);
        } else {
          assert.equal(params.get('scope'), direction.toLowerCase());
          assert.deepEqual(params.getAll('status'), [fixture.groups.find(group => group.label === direction).stages.find(item => item.label === stage).status]);
          assert.equal(await page.locator(details).evaluate(element => element.open), false, 'More filters disclosure state lost');
        }
      }
      await clickAndCheck(page, `All ${direction.toLowerCase()} documents`, {preserve});
      if (surface !== 'dashboard') assert.equal(new URL(page.url()).searchParams.has('status'), false);
    }
    const received = await clickAndCheck(page, 'Incoming · Received', {preserve});
    const working = await clickAndCheck(page, 'Outgoing · In Process', {preserve});
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('.direction-filters [aria-label^="Incoming · Received"][aria-current="true"]'));
    await checkState(page, received);
    await page.goForward();
    await page.waitForFunction(() => document.querySelector('.direction-filters [aria-label^="Outgoing · In Process"][aria-current="true"]'));
    await checkState(page, working);
    assert.equal(await page.evaluate(() => window.__directionFilterSentinel), 'kept');
    assert.ok(requests.slice(1).some(request => request.headers['hx-request'] === 'true'), 'No HTMX requests observed');
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
    await page.locator('.direction-filters').screenshot({path:resolve(output, `${surface}-${width}-${theme}-groups.png`)});
    await page.screenshot({path:resolve(output, `${surface}-${width}-${theme}.png`), fullPage:true});
    console.log(`PASS ${surface} ${width}px ${theme}: six stages, both parents, exact records/counts, active selection, preserved filters/disclosures, in-place updates, Back/Forward; no console errors or overflow`);
  } finally {await context.close();}
}

async function runNative(browser, surface) {
  const context = await browser.newContext({javaScriptEnabled:false, viewport:{width:390,height:900}});
  const page = await context.newPage();
  const requests = [], failures = [];
  try {
    await serve(page, requests, failures);
    await page.goto(origin + initial(surface).url);
    for (const direction of ['Incoming','Outgoing']) {
      for (const stage of ['Pending Receipt','Received','In Process']) {
        await clickAndCheck(page, `${direction} · ${stage}`, {javascript:false, preserve:surface !== 'dashboard'});
      }
      await clickAndCheck(page, `All ${direction.toLowerCase()} documents`, {javascript:false, preserve:surface !== 'dashboard'});
    }
    assert.equal(requests.some(request => request.headers['hx-request']), false);
    assert.deepEqual(failures, []);
    console.log(`PASS ${surface} without JavaScript: six native stage links and both parent links show the same selections and exact records`);
  } finally {await context.close();}
}

async function runKeyboard(browser, surface) {
  const context = await browser.newContext({viewport:{width:1366,height:900},colorScheme:'light'});
  const page = await context.newPage();
  const errors = [], failures = [], requests = [], focusFailures = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  const control = name => page.locator('.direction-filters').getByRole('link', {
    name:new RegExp(`^${name}(?:, \\d+ documents)?$`),
  });
  async function focused(name) {
    assert.equal(await control(name).evaluate(element => element === document.activeElement), true,
                 `Tab should focus ${name}`);
  }
  async function activate(name) {
    await focused(name);
    const link = control(name);
    const href = await link.getAttribute('href');
    const label = await link.getAttribute('aria-label');
    const fixture = fixtures.get(key(href));
    assert.ok(fixture, `Missing keyboard fixture: ${href}`);
    await page.keyboard.press('Enter');
    await page.waitForFunction(expected => {
      const active = document.querySelector('.direction-filters a[aria-current="true"]');
      return active && active.getAttribute('aria-label') === expected;
    }, label);
    await page.waitForURL(url => key(url.href) === key(href));
    await checkState(page, fixture);
    assert.equal(await page.evaluate(() => window.__directionFilterSentinel), 'kept', 'Keyboard activation reloaded the page');
    const retained = await control(name).evaluate(element => element === document.activeElement);
    if (!retained) {
      const actual = await page.evaluate(() => {
        const element = document.activeElement;
        return element.tagName + (element.id ? '#' + element.id : '') +
          (element.getAttribute('aria-label') ? ` (${element.getAttribute('aria-label')})` : '');
      });
      focusFailures.push(`${surface}: Enter on ${name} moved focus to ${actual} after the swap`);
      // Continue testing the next tab sequence, retaining this failed assertion.
      await control(name).focus();
    }
  }
  try {
    await serve(page, requests, failures);
    await page.goto(origin + initial(surface).url);
    await page.waitForFunction(() => window.htmx && document.querySelector('.direction-filters a[data-filter-link]'));
    await page.evaluate(() => {window.__directionFilterSentinel = 'kept';});
    await control('All incoming documents').focus();
    await page.keyboard.press('Tab');
    await activate('Incoming · Pending Receipt');
    for (const name of ['Incoming · Received','Incoming · In Process','All outgoing documents',
                        'Outgoing · Pending Receipt','Outgoing · Received','Outgoing · In Process']) {
      await page.keyboard.press('Tab');
      await focused(name);
    }
    await activate('Outgoing · In Process');
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
    if (focusFailures.length) {
      for (const failure of focusFailures) console.log(`FAIL keyboard ${failure}`);
    } else {
      console.log(`PASS keyboard ${surface}: Tab follows both groups; Enter selects Incoming Pending Receipt and Outgoing In Process in place and retains focus on each activated control`);
    }
    return focusFailures;
  } finally {await context.close();}
}

async function runDisabledAndReports(browser, width, theme) {
  const context = await browser.newContext({viewport:{width,height:900},colorScheme:theme});
  const page = await context.newPage();
  const errors = [], failures = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  try {
    await context.addInitScript(theme => localStorage.setItem('doctrack-theme', theme), theme);
    await serve(page, requests, failures);
    for (const surface of ['dashboard','tracking','search']) {
      const fixture = data.pages.find(page => page.surface === surface && page.selection === 'all-offices');
      await page.goto(origin + fixture.url);
      await checkState(page, fixture);
    }
    const report = data.pages.find(page => page.surface === 'reports');
    await page.goto(origin + report.url);
    for (const group of report.groups) {
      const section = page.locator('.report-direction').filter({has:page.locator(`#report-direction-${group.key}`)});
      assert.equal(await section.locator('.report-chart-summary strong').innerText(), String(group.total));
      assert.deepEqual(await section.locator('.report-stage-link strong').allTextContents(), group.stages.map(stage => String(stage.total)));
      const links = await section.locator('.report-stage-link').evaluateAll(links => links.map(link => link.getAttribute('href')));
      assert.deepEqual(links.map(key), group.stages.map(stage => key(stage.href)));
      const deskGroup = initial('dashboard').groups.find(item => item.key === group.key);
      assert.equal(group.total, deskGroup.count);
      assert.deepEqual(group.stages.map(stage => stage.total), deskGroup.stages.map(stage => stage.count));
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
    await page.locator('#report-document-stages').screenshot({path:resolve(output, `reports-${width}-${theme}.png`)});
    console.log(`PASS ${width}px ${theme}: all-office controls disabled on all three workspaces; Reports stages and totals match Dashboard; no console errors or overflow`);
  } finally {await context.close();}
}

(async () => {
  const browser = await chromium.launch({headless:true,executablePath});
  try {
    if (process.argv.includes('--keyboard-only')) {
      const failures = [];
      for (const surface of ['dashboard','tracking','search']) failures.push(...await runKeyboard(browser,surface));
      assert.deepEqual(failures, [], 'Keyboard focus must remain on the activated direction control after each HTMX swap');
      console.log('PASS keyboard direction/stage QA: all three workspaces retain keyboard focus after Enter activation.');
      return;
    }
    for (const width of [1366,390]) {
      for (const theme of ['light','dark']) {
        for (const surface of ['dashboard','tracking','search']) await runEnhanced(browser,width,theme,surface);
        await runDisabledAndReports(browser,width,theme);
      }
    }
    for (const surface of ['dashboard','tracking','search']) await runNative(browser,surface);
    console.log('PASS direction/stage browser QA: 12 enhanced workspace configurations, 4 disabled/Reports configurations and 3 JavaScript-disabled native flows.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
