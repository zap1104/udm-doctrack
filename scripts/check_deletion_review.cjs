/* Offline browser QA using actual HTML from render_deletion_review_qa.py.
 *
 * PowerShell, after rendering fixtures:
 *   $env:NODE_PATH = '<bundled directory containing playwright>'
 *   $env:PLAYWRIGHT_CHROMIUM_EXECUTABLE = '<Chromium executable>'
 *   $env:HTMX_SCRIPT_PATH = '<htmx 1.9.12 minified script>'
 *   $env:BOOTSTRAP_STYLE_PATH = '<Bootstrap 5.3.3 minified stylesheet>'
 *   $env:BOOTSTRAP_SCRIPT_PATH = '<Bootstrap 5.3.3 bundle script>'
 *   node scripts/check_deletion_review.cjs
 *
 * No live application server, credentials, or external network is used. The
 * real application CSS/JS and pinned vendor files run in the browser. After a
 * deliberate POST, the harness explicitly follows the captured server redirect
 * and loads the real destination HTML at its actual URL. The redirect status
 * and destination are also recorded in the report.
 * This checks browser behavior, not backend authorization or concurrency.
 */
'use strict';

const assert = require('node:assert/strict');
const {existsSync, mkdirSync, readFileSync, writeFileSync} = require('node:fs');
const {extname, relative, resolve, sep} = require('node:path');
const {chromium} = require('playwright');

const root = resolve(__dirname, '..');
const output = resolve(root, 'docs/verification/deletion-review');
const fixturesPath = resolve(output, 'pages.json');
assert.ok(existsSync(fixturesPath), 'Render pages.json with scripts/render_deletion_review_qa.py first.');
const fixtures = JSON.parse(readFileSync(fixturesPath, 'utf8'));
assert.equal(fixtures.schemaVersion, 1, 'Unsupported page fixture schema.');
assert.equal(fixtures.origin, 'http://review.test', 'Unexpected fixture origin.');
const origin = fixtures.origin;
const screenshots = resolve(output, 'screenshots');
mkdirSync(screenshots, {recursive: true});

const vendorPaths = {
  htmx: resolve(process.env.HTMX_SCRIPT_PATH || resolve(root, 'static/vendor/htmx.min.js')),
  bootstrapStyle: resolve(process.env.BOOTSTRAP_STYLE_PATH || resolve(root, 'static/vendor/bootstrap.min.css')),
  bootstrapScript: resolve(process.env.BOOTSTRAP_SCRIPT_PATH || resolve(root, 'static/vendor/bootstrap.bundle.min.js')),
};
for (const [name, path] of Object.entries(vendorPaths)) {
  assert.ok(existsSync(path), `Missing ${name} asset. Set its documented environment variable: ${path}`);
}
if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) {
  assert.ok(existsSync(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE), 'PLAYWRIGHT_CHROMIUM_EXECUTABLE does not exist.');
}

const report = {
  generatedAt: new Date().toISOString(),
  fixtureGeneratedAt: fixtures.generatedAt,
  fixtureDate: fixtures.today,
  fixtureSource: relative(root, fixturesPath),
  scope: 'Offline, actual Django HTML and application assets; synthetic fixture transport; no live server writes.',
  vendorPaths,
  cases: [], consoleErrors: [], pageErrors: [], unhandledRequests: [], assetFailures: [], requests: [],
};
const assetCache = new Map();
const contentTypes = {
  '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ico': 'image/x-icon',
};

function assetPath(url) {
  // These exact URLs are the versions pinned in base.html. Keeping the real
  // integrity attributes also detects an incorrect vendor file supplied here.
  if (url.href === 'https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js' ||
      url.pathname === '/static/vendor/htmx.min.js') return vendorPaths.htmx;
  if (url.href === 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css' ||
      url.pathname === '/static/vendor/bootstrap.min.css') return vendorPaths.bootstrapStyle;
  if (url.href === 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js' ||
      url.pathname === '/static/vendor/bootstrap.bundle.min.js') return vendorPaths.bootstrapScript;
  if (url.origin !== origin || !url.pathname.startsWith('/static/')) return null;
  const directory = resolve(root, 'static');
  const path = resolve(directory, decodeURIComponent(url.pathname.slice('/static/'.length)));
  const within = relative(directory, path);
  if (within === '..' || within.startsWith(`..${sep}`) || resolve(path) === directory) return null;
  return path;
}

function bodyForAsset(path) {
  if (!assetCache.has(path)) assetCache.set(path, readFileSync(path));
  return assetCache.get(path);
}

async function createPage(browser, name, theme, viewport, fixtureOverride, pageOptions = {}) {
  const javaScriptEnabled = pageOptions.javaScriptEnabled !== false;
  const context = await browser.newContext({viewport, colorScheme: theme, reducedMotion: 'reduce', javaScriptEnabled});
  if (javaScriptEnabled) await context.addInitScript(chosen => localStorage.setItem('doctrack-theme', chosen), theme);
  const page = await context.newPage();
  const state = {posts: [], destination: null, redirect: null};
  page.on('console', message => {
    if (message.type() === 'error') report.consoleErrors.push({case: name, message: message.text()});
  });
  page.on('pageerror', error => report.pageErrors.push({case: name, message: error.message}));
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = assetPath(url);
    if (path) {
      if (!existsSync(path)) {
        report.assetFailures.push({case: name, url: url.href, path});
        return route.fulfill({status: 404, contentType: 'text/plain', body: 'Missing local fixture asset'});
      }
      return route.fulfill({status: 200, contentType: contentTypes[extname(path)] || 'application/octet-stream',
        headers: {'Access-Control-Allow-Origin': '*'}, body: bodyForAsset(path)});
    }
    const entry = {case: name, method: request.method(), url: url.href,
      htmx: request.headers()['hx-request'] === 'true', historyRestore: request.headers()['hx-history-restore-request'] === 'true'};
    report.requests.push(entry);
    if (url.origin !== origin) {
      report.unhandledRequests.push(entry);
      return route.fulfill({status: 404, body: 'External request has no offline fixture'});
    }
    let fixture;
    if (request.method() === 'POST' && url.pathname === fixtures.pages.sessionKeepalive.url) {
      const keepalive = fixtures.pages.sessionKeepalive;
      entry.fixture = 'sessionKeepalive';
      return route.fulfill({status: keepalive.status, contentType: keepalive.headers['Content-Type'], body: keepalive.html});
    }
    if (request.method() === 'POST' && url.pathname === fixtures.pages.expiredDeleted.url) {
      const denied = fixtures.pages.expiredRestorePost;
      const data = new URLSearchParams(request.postData());
      if (!Object.entries(denied.requestData).every(([key, value]) => data.get(key) === String(value))) {
        report.unhandledRequests.push(entry);
        return route.fulfill({status: 400, body: 'No captured expired-restoration response for this payload'});
      }
      state.posts.push({url: url.href, data: request.postData() || ''});
      entry.fixture = 'expiredRestorePost';
      entry.capturedStatus = denied.status;
      entry.capturedDenied = true;
      entry.validationErrors = denied.validationErrors;
      entry.postData = request.postData();
      return route.fulfill({status: denied.status, contentType: denied.headers['Content-Type'], body: denied.html});
    }
    if (request.method() === 'POST') {
      if (![fixtures.pages.review.url, fixtures.pages.restore.url].includes(url.pathname)) {
        report.unhandledRequests.push(entry);
        return route.fulfill({status: 400, body: 'Unexpected non-review fixture POST'});
      }
      state.posts.push({url: url.href, data: request.postData() || ''});
      const data = new URLSearchParams(request.postData());
      const decision = data.get('decision');
      if (pageOptions.validationOnly) {
        // Replay only the invalid payload shapes captured from the actual
        // Django view. This transport does not implement server validation.
        const errorKey = decision === 'KEEP' && data.get('next_review_date') === fixtures.today ? 'keepError' :
          decision === 'DELETE' && !data.get('next_review_date') && !data.has('confirmed') ? 'removalError' :
          decision === 'RESTORE' && data.get('next_review_date') === fixtures.today ? 'restoreError' : null;
        const response = errorKey && fixtures.pages[errorKey];
        if (!response || response.url !== url.pathname) {
          report.unhandledRequests.push(entry);
          return route.fulfill({status: 400, body: 'No captured validation response for this payload'});
        }
        entry.fixture = errorKey;
        entry.capturedStatus = response.status;
        entry.postData = request.postData();
        return route.fulfill({status: response.status, contentType: response.headers['Content-Type'], body: response.html});
      }
      const postKey = decision === 'DELETE' ? 'removePost' : decision === 'RESTORE' ? 'restorePost' : null;
      if (!postKey || fixtures.pages[postKey].url !== url.pathname) {
        report.unhandledRequests.push(entry);
        return route.fulfill({status: 400, body: 'Unexpected fixture POST'});
      }
      const captured = fixtures.pages[postKey];
      entry.fixture = postKey;
      entry.capturedStatus = captured.status;
      entry.capturedRedirect = captured.headers.Location;
      entry.postData = request.postData();
      state.destination = decision === 'DELETE' ? 'removalDestination' : 'restorationDestination';
      state.redirect = captured.headers.Location;
      entry.finalFixture = state.destination;
      // A route-fulfilled native redirect can bypass interception on its next
      // hop. Return the captured POST body, then the check follows Location
      // explicitly. The resulting GET uses its proper URL and server HTML.
      return route.fulfill({status: 200, contentType: 'text/html', body: captured.html});
    } else if (state.destination && url.pathname + url.search === fixtures.pages[state.destination].url) {
      fixture = fixtures.pages[state.destination];
      entry.fixture = state.destination;
    } else if (fixtureOverride && url.pathname === fixtures.pages[fixtureOverride].url) {
      fixture = fixtures.pages[fixtureOverride];
      entry.fixture = fixtureOverride;
    } else if (url.pathname === fixtures.queuePath) {
      const status = url.searchParams.get('status') || 'due';
      const key = !url.search ? 'queueDefault' : `queue${status[0].toUpperCase()}${status.slice(1)}`;
      fixture = fixtures.pages[key];
      entry.fixture = key;
    } else {
      const match = ['review', 'restore', 'expiredDeleted', 'repository', 'notificationCount'].find(key => fixtures.pages[key]?.url === url.pathname);
      if (match) { fixture = fixtures.pages[match]; entry.fixture = match; }
    }
    if (!fixture) {
      report.unhandledRequests.push(entry);
      return route.fulfill({status: 404, body: 'No page fixture for this URL'});
    }
    // Security/transport headers belong to the live deployment, which this
    // harness does not emulate. DOM, assets, forms, and request URLs are real.
    return route.fulfill({status: 200, contentType: fixture.headers['Content-Type'] || 'text/html', body: fixture.html});
  });
  return {page, context, state};
}

async function ready(page, fixtureName, theme) {
  await page.goto(origin + fixtures.pages[fixtureName].url);
  await page.locator('#main-content').waitFor();
  await page.waitForFunction(() => document.readyState === 'complete' && window.htmx && window.bootstrap);
  assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
  assert.equal(await page.locator('html').getAttribute('data-bs-theme'), theme);
  await page.evaluate(() => document.fonts.ready);
}

async function captureMain(page, caseResult, label) {
  const path = resolve(screenshots, `${caseResult.name}-${label}.png`);
  const savedState = await page.evaluateHandle(() => ({active: document.activeElement, x: window.scrollX, y: window.scrollY}));
  try {
    // Element screenshots auto-scroll a tall main region. Fixed and sticky
    // elements can then appear inside its clipped image even when offscreen.
    // Mobile captures use the whole document from its normal top position.
    // Restore focus and scrolling afterwards so keyboard checks keep their
    // real state; no CSS or application behavior is changed for the image.
    await page.evaluate(() => {
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      window.scrollTo({left: 0, top: 0, behavior: 'instant'});
    });
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const mobile = caseResult.viewport.width < 768;
    if (mobile) await page.screenshot({path, fullPage: true, animations: 'disabled'});
    else await page.locator('#main-content').screenshot({path, animations: 'disabled'});
    caseResult.screenshots.push(relative(root, path));
    const dimensions = await page.evaluate(() => {
      const width = document.documentElement.clientWidth;
      const overflow = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - width;
      const offenders = overflow > 0 ? Array.from(document.querySelectorAll('#main-content *')).filter(element => {
        const bounds = element.getBoundingClientRect();
        return bounds.width && bounds.right > width + 1;
      }).slice(0, 10).map(element => ({tag: element.tagName, id: element.id, className: element.className,
        text: element.textContent.trim().slice(0, 100), right: element.getBoundingClientRect().right})) : [];
      const header = document.querySelector('.app-topbar');
      const skip = document.querySelector('.skip-link');
      return {width, overflow: Math.max(0, overflow), offenders, capture: {
        scrollY: window.scrollY,
        headerPosition: header ? getComputedStyle(header).position : null,
        headerTop: header ? header.getBoundingClientRect().top : null,
        skipLinkFocused: skip === document.activeElement,
        skipLinkTop: skip ? skip.getBoundingClientRect().top : null,
      }};
    });
    caseResult.layout.push({label, screenshotMode: mobile ? 'full-page' : 'main-content', ...dimensions});
    assert.equal(dimensions.overflow, 0, `${caseResult.name}/${label}: horizontal overflow ${JSON.stringify(dimensions.offenders)}`);
  } finally {
    await page.evaluate(saved => {
      if (saved.active && saved.active.isConnected && saved.active.focus) saved.active.focus({preventScroll: true});
      window.scrollTo({left: saved.x, top: saved.y, behavior: 'instant'});
    }, savedState);
    await savedState.dispose();
  }
}

async function assertLabels(form) {
  const invalid = await form.evaluate(element => Array.from(element.elements).filter(field => {
    if (!field.matches('input:not([type="hidden"]):not([type="submit"]), select, textarea') ||
        field.disabled || !field.getClientRects().length) return false;
    return !field.labels?.length || !Array.from(field.labels).some(label => label.textContent.trim());
  }).map(field => ({name: field.name, id: field.id})));
  assert.deepEqual(invalid, [], 'Every visible editable field needs an associated text label.');
}

async function assertTabOrder(page, form, expectedNames) {
  await form.locator('[name="decision"]').focus();
  let previous = 'decision';
  for (const expected of expectedNames) {
    let focused;
    // Native date inputs can expose several internal keyboard stops while
    // document.activeElement remains the same input. Allow those stops.
    for (let attempt = 0; attempt < 10; attempt++) {
      await page.keyboard.press('Tab');
      focused = await page.evaluate(() => ({name: document.activeElement.name,
        submit: document.activeElement.hasAttribute('data-review-submit')}));
      if (focused.submit || focused.name !== previous) break;
    }
    if (expected === 'submit') assert.equal(focused.submit, true, 'Submit must be keyboard reachable.');
    else assert.equal(focused.name, expected, `Expected keyboard focus on ${expected}.`);
    previous = expected;
  }
}

async function followCapturedRedirect(page, state, destinationName) {
  assert.equal(state.destination, destinationName);
  const destination = fixtures.pages[destinationName];
  assert.equal(state.redirect, destination.url, 'Captured redirect and destination fixture must agree.');
  await page.goto(new URL(state.redirect, origin).href);
  await page.locator('#main-content').waitFor();
  assert.equal(new URL(page.url()).pathname + new URL(page.url()).search, destination.url);
}

async function assertRecoveryDeadline(page, documentKey) {
  const document = fixtures.documents[documentKey];
  assert.ok(document.purgeAtLabel, 'The fixture must provide its actual recovery deadline.');
  assert.equal(new Date(document.purgeAt) - new Date(document.deletedAt), 30 * 24 * 60 * 60 * 1000);
  const main = page.locator('#main-content');
  assert.equal(await main.getByText('Recovery deadline', {exact: true}).isVisible(), true);
  const deadline = main.locator('.deletion-review-metadata dd').filter({hasText: document.purgeAtLabel});
  assert.equal(await deadline.isVisible(), true, 'The recovery deadline must be visible in document metadata.');
  assert.equal((await deadline.textContent()).trim(), document.purgeAtLabel);
}

async function assertExpiredInfo(page) {
  const main = page.locator('#main-content');
  assert.equal(await main.getByRole('heading', {name: 'Awaiting permanent deletion', exact: true}).isVisible(), true);
  assert.equal(await main.getByRole('heading', {name: 'Recovery period ended', exact: true}).isVisible(), true);
  assert.match(await main.locator('.help-note').textContent(), /30-day recovery period has ended.*no longer be restored/i);
  assert.equal(await main.locator('[data-deletion-review-form]').count(), 0, 'Expired recovery must expose no decision form.');
  assert.equal(await main.locator('form').count(), 0);
  assert.equal(await main.locator('option[value="RESTORE"]').count(), 0);
  assert.equal(await main.getByRole('button', {name: /restore/i}).count(), 0);
  await assertRecoveryDeadline(page, 'expired');
}

async function checkExpiredRecovery(page, result, state, theme) {
  await ready(page, 'expiredDeleted', theme);
  await assertExpiredInfo(page);
  await captureMain(page, result, 'expired-recovery');
  assert.deepEqual(state.posts, [], 'Opening an expired document must not submit a decision.');
  const captured = fixtures.pages.expiredRestorePost;
  assert.ok(captured.validationErrors.decision?.length, 'The captured Django view must reject the restoration decision.');
  const requestPromise = page.waitForRequest(request => request.method() === 'POST' &&
    new URL(request.url()).pathname === fixtures.pages.expiredDeleted.url);
  // Replay the signed token from the review captured before its temporary row
  // was aged past expiry. The renderer verified the real Django rejection and
  // that no document, file metadata, or history row changed after this POST.
  const reply = await page.evaluate(async ({url, data}) => {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams(data)});
    const html = await response.text();
    const document = new DOMParser().parseFromString(html, 'text/html');
    return {status: response.status, text: document.querySelector('#main-content')?.textContent || '',
      formCount: document.querySelectorAll('#main-content form').length,
      restoreOptionCount: document.querySelectorAll('#main-content option[value="RESTORE"]').length};
  }, {url: origin + captured.url, data: captured.requestData});
  const request = await requestPromise;
  assert.equal(new URLSearchParams(request.postData()).get('decision'), 'RESTORE');
  assert.equal(reply.status, captured.status, 'Replay the actual server rejection status.');
  assert.match(reply.text, /30-day recovery period has ended.*no longer be restored/is);
  assert.equal(reply.formCount, 0);
  assert.equal(reply.restoreOptionCount, 0);
  assert.equal(state.posts.length, 1);
  assert.equal(state.destination, null, 'Rejected expired restoration must not redirect to a restored document.');
  result.checks.push('Expired recovery information and deadline, no decision form or RESTORE option, captured direct POST rejection using a previously opened review token');
}

async function checkQueue(page, result, state, theme) {
  await ready(page, 'queueDefault', theme);
  const main = page.locator('#main-content');
  await assertLabels(main.locator('form[data-auto-filter]'));
  assert.equal(await main.getByRole('button', {name: 'Search', exact: true}).isVisible(), true, 'Typed searches keep their visible submit button.');
  assert.equal(await main.getByRole('heading', {name: fixtures.documents.future.title, exact: true}).count(), 0, 'Reviews beyond 90 days are absent from the due queue.');
  await captureMain(page, result, 'queue');
  await page.evaluate(() => { window.__deletionReviewQaDocument = 'stable'; });
  const selector = '#main-content select[name="status"]';
  const response = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === fixtures.queuePath && url.searchParams.get('status') === 'soon' &&
      response.request().headers()['hx-request'] === 'true';
  });
  await page.locator(selector).focus();
  await page.locator(selector).selectOption('soon');
  await response;
  await main.getByRole('heading', {name: fixtures.documents.soon.title, exact: true}).waitFor();
  assert.equal(await main.getByRole('heading', {name: fixtures.documents.future.title, exact: true}).count(), 0, 'Reviews beyond 90 days are absent from the upcoming queue.');
  assert.equal(await page.locator(selector).inputValue(), 'soon');
  assert.equal(new URL(page.url()).searchParams.get('status'), 'soon');
  assert.equal(await page.evaluate(() => window.__deletionReviewQaDocument), 'stable', 'Queue filters must retain the document.');
  assert.equal(await page.evaluate(() => document.activeElement.name), 'status', 'Queue filter keeps keyboard focus.');
  await page.goBack();
  await page.waitForFunction(() => document.querySelector('#main-content select[name="status"]')?.value === 'due');
  await main.getByRole('heading', {name: fixtures.documents.due.title, exact: true}).waitFor();
  assert.equal(await page.evaluate(() => window.__deletionReviewQaDocument), 'stable', 'Browser Back restores queue status without reload.');
  assert.equal(new URL(page.url()).searchParams.get('status'), null);
  assert.deepEqual(state.posts, [], 'Queue filters must not submit review decisions.');
  assert.ok(report.requests.filter(request => request.case === result.name && new URL(request.url).pathname === fixtures.queuePath)
    .every(request => request.method === 'GET'), 'Queue updates and history restoration use GET.');
  for (const status of ['deleted', 'unscheduled']) {
    await page.locator(selector).selectOption(status);
    await main.getByRole('heading', {name: fixtures.documents[status].title, exact: true}).waitFor();
    assert.equal(await page.evaluate(() => window.__deletionReviewQaDocument), 'stable');
    assert.equal(await main.getByRole('heading', {name: fixtures.documents.future.title, exact: true}).count(), 0);
  }
  await page.goto(origin + fixtures.pages.repository.url);
  await main.getByRole('heading', {name: fixtures.documents.future.title, exact: true}).waitFor();
  result.checks.push('Associated filter labels, visible Search button, real HTMX updates, retained document and focus, Browser Back restores status, GET only, later reviews remain in the regular repository');
}

async function checkReview(page, result, state, theme) {
  await ready(page, 'review', theme);
  const form = page.locator('[data-deletion-review-form]');
  await page.waitForFunction(() => document.querySelector('[data-deletion-review-form]')?.dataset.reviewReady === 'true');
  const decision = form.locator('[name="decision"]');
  const date = form.locator('[name="next_review_date"]');
  const confirmed = form.locator('[name="confirmed"]');
  const nextDate = form.locator('[data-review-next-date]');
  const removal = form.locator('[data-review-removal]');
  assert.deepEqual(await decision.locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean)), ['KEEP', 'DELETE']);
  await decision.selectOption('KEEP');
  assert.equal(await nextDate.isVisible(), true);
  assert.equal(await date.isEnabled(), true);
  assert.equal(await date.evaluate(element => element.required), true);
  assert.equal(await date.getAttribute('min'), fixtures.tomorrow, 'Next review date must start after today.');
  assert.equal(await removal.isVisible(), false);
  assert.equal(await confirmed.isEnabled(), false);
  await date.fill(fixtures.futureDate);
  await form.locator('[name="reason"]').fill('Keep for an approved records check. Reference QA-KEEP-02.');
  await assertLabels(form);
  await assertTabOrder(page, form, ['next_review_date', 'reason', 'submit']);
  await captureMain(page, result, 'review-keep');

  await decision.selectOption('DELETE');
  assert.equal(await nextDate.isVisible(), false);
  assert.equal(await date.isEnabled(), false);
  assert.equal(await date.inputValue(), '', 'Choosing removal clears the previous next review date.');
  assert.equal(await date.evaluate(element => element.required), false);
  assert.equal(await removal.isVisible(), true);
  assert.equal(await confirmed.isEnabled(), true);
  assert.equal(await confirmed.evaluate(element => element.required), true, 'Removal requires approval confirmation.');
  assert.equal(await form.locator('[name="reason"]').evaluate(element => element.required), true);
  assert.match(await form.locator('[data-review-submit]').textContent(), /confirm|remov/i);
  await confirmed.check();
  await decision.selectOption('KEEP');
  assert.equal(await confirmed.isChecked(), false, 'Changing away from removal resets confirmation.');
  await decision.selectOption('DELETE');
  await assertLabels(form);
  await assertTabOrder(page, form, ['reason', 'confirmed', 'submit']);
  await form.locator('[name="reason"]').fill('Removal approved by authority QA-REMOVE-02.');
  await confirmed.check();
  await captureMain(page, result, 'review-remove');
  assert.deepEqual(state.posts, [], 'Selecting decisions, entering reasons, and confirming approval must not submit.');
  const postRequest = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === fixtures.pages.review.url);
  await form.locator('[data-review-submit]').click();
  const request = await postRequest;
  const data = new URLSearchParams(request.postData());
  assert.equal(new URL(request.url()).pathname, fixtures.pages.review.url);
  assert.equal(data.get('decision'), 'DELETE');
  assert.equal(data.get('confirmed'), 'on');
  assert.equal(data.has('next_review_date'), false, 'Disabled date must be omitted from a removal POST.');
  assert.ok(data.get('state_token'), 'Submission must retain the server state token.');
  await page.waitForLoadState('load');
  assert.equal(state.posts.length, 1, 'One deliberate click submits one decision.');
  await followCapturedRedirect(page, state, 'removalDestination');
  assert.equal(await page.locator('#main-content select[name="status"]').inputValue(), 'deleted');
  await page.locator('#main-content').getByRole('heading', {name: fixtures.documents.due.title, exact: true}).waitFor();
  result.checks.push('KEEP future date, DELETE hidden and disabled date, reset confirmation, required approval and reason, labels and keyboard order, deliberate click issues one correct POST');
}

async function checkRestore(page, result, state, theme) {
  await ready(page, 'restore', theme);
  await assertRecoveryDeadline(page, 'deleted');
  const notice = page.locator('#main-content .help-note').filter({hasText: '30 days after removal'});
  assert.equal(await notice.isVisible(), true, 'Restore pages must explain the 30-day recovery period.');
  assert.equal((await notice.locator('strong').textContent()).trim(), fixtures.documents.deleted.purgeAtLabel);
  const form = page.locator('[data-deletion-review-form]');
  await page.waitForFunction(() => document.querySelector('[data-deletion-review-form]')?.dataset.reviewReady === 'true');
  const decision = form.locator('[name="decision"]');
  assert.deepEqual(await decision.locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean)), ['RESTORE']);
  await decision.selectOption('RESTORE');
  const date = form.locator('[name="next_review_date"]');
  assert.equal(await date.isVisible(), true);
  assert.equal(await date.isEnabled(), true);
  assert.equal(await date.evaluate(element => element.required), true);
  assert.equal(await date.getAttribute('min'), fixtures.tomorrow);
  assert.equal(await form.locator('[data-review-removal]').isVisible(), false);
  assert.equal(await form.locator('[name="confirmed"]').isEnabled(), false);
  await date.fill(fixtures.futureDate);
  await form.locator('[name="reason"]').fill('Restore for an approved records check. Reference QA-RESTORE-02.');
  await assertLabels(form);
  await assertTabOrder(page, form, ['next_review_date', 'reason', 'submit']);
  await captureMain(page, result, 'restore');
  assert.deepEqual(state.posts, [], 'Selecting restoration and entering a date must not submit.');
  const postRequest = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === fixtures.pages.restore.url);
  await form.locator('[data-review-submit]').click();
  const request = await postRequest;
  const data = new URLSearchParams(request.postData());
  assert.equal(new URL(request.url()).pathname, fixtures.pages.restore.url);
  assert.equal(data.get('decision'), 'RESTORE');
  assert.equal(data.get('next_review_date'), fixtures.futureDate);
  assert.equal(data.has('confirmed'), false);
  await page.waitForLoadState('load');
  assert.equal(state.posts.length, 1);
  await followCapturedRedirect(page, state, 'restorationDestination');
  await page.locator('#main-content').getByRole('heading', {name: fixtures.documents.deleted.title, exact: true}).waitFor();
  result.checks.push('Visible 30-day recovery deadline, only RESTORE decision, required future date, hidden disabled removal confirmation, keyboard access, deliberate restoration POST');
}

async function checkErrors(page, result, state, theme, fixtureName) {
  await ready(page, fixtureName, theme);
  const summary = page.locator('[data-error-summary]');
  assert.equal(await summary.isVisible(), true);
  assert.equal(await summary.getAttribute('role'), 'alert');
  const reload = page.locator('#main-content').getByRole('link', {name: 'Reload this review', exact: true});
  assert.equal(await reload.isVisible(), true, 'Every failed review offers a visible reload link.');
  assert.equal(await reload.getAttribute('href'), fixtures.pages[fixtureName].url);
  assert.equal(await summary.evaluate(element => document.activeElement === element), true, 'Server errors receive keyboard focus.');
  const link = summary.locator('a[href^="#"]').first();
  if (await link.count()) {
    const targetId = (await link.getAttribute('href')).slice(1);
    await link.focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.id), targetId, 'Error links focus the invalid field.');
  }
  await assertLabels(page.locator('[data-deletion-review-form]'));
  await captureMain(page, result, fixtureName);
  assert.deepEqual(state.posts, []);
  result.checks.push('Real server error summary, visible reload recovery, automatic focus, keyboard links to invalid fields, labels and layout');
}

async function checkNoJavaScript(page, result, state) {
  const scenarios = [
    {page: 'review', choices: ['KEEP', 'DELETE'], decision: 'KEEP', date: fixtures.today,
      reason: 'QA review with approval reference QA-01.', errorField: 'next_review_date', error: /after today/i},
    {page: 'review', choices: ['KEEP', 'DELETE'], decision: 'DELETE', date: '',
      reason: 'QA review with approval reference QA-01.', errorField: 'confirmed', error: /confirm approval/i},
    {page: 'restore', choices: ['RESTORE'], decision: 'RESTORE', date: fixtures.today,
      reason: 'QA restoration review.', errorField: 'next_review_date', error: /after today/i},
  ];
  for (const [index, scenario] of scenarios.entries()) {
    const url = fixtures.pages[scenario.page].url;
    await page.goto(origin + url);
    const form = page.locator('[data-deletion-review-form]');
    await form.waitFor();
    assert.equal(await form.getAttribute('data-review-ready'), null, 'Application enhancement must be absent with JavaScript disabled.');
    const decision = form.locator('[name="decision"]');
    const options = await decision.locator('option').all();
    assert.deepEqual((await Promise.all(options.map(option => option.getAttribute('value')))).filter(Boolean), scenario.choices);
    await decision.selectOption(scenario.decision);
    await form.locator('[name="next_review_date"]').fill(scenario.date);
    await form.locator('[name="reason"]').fill(scenario.reason);
    await form.locator('[name="confirmed"]').uncheck();
    assert.equal(state.posts.length, index, 'Native decision controls submit only after an explicit click.');
    const requestPromise = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === url);
    await form.locator('[data-review-submit]').click();
    const request = await requestPromise;
    const data = new URLSearchParams(request.postData());
    assert.equal(data.get('decision'), scenario.decision);
    assert.equal(data.get('next_review_date'), scenario.date);
    assert.equal(data.get('reason'), scenario.reason);
    assert.equal(data.has('confirmed'), false);
    assert.ok(data.get('state_token'), 'Native forms preserve the review state token.');
    const summary = page.locator('[data-error-summary]');
    await summary.waitFor();
    assert.match(await summary.textContent(), scenario.error, 'Display the captured Django validation response.');
    assert.equal(await page.locator(`.form-field--error [name="${scenario.errorField}"]`).isVisible(), true);
    const reload = page.locator('#main-content').getByRole('link', {name: 'Reload this review', exact: true});
    assert.equal(await reload.isVisible(), true);
    assert.equal(await reload.getAttribute('href'), url);
    assert.equal(state.posts.length, index + 1);
  }
  await page.goto(origin + fixtures.pages.expiredDeleted.url);
  await assertExpiredInfo(page);
  assert.equal(state.posts.length, scenarios.length);
  result.checks.push('JavaScript disabled: native KEEP/DELETE choices, RESTORE-only choices, explicit POST and state token, captured future-date and missing-approval validation, visible reload recovery, expired info with no restoration form');
}

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try {
    const sizes = {desktop: {width: 1440, height: 1000}, mobile: {width: 390, height: 844}};
    for (const theme of ['light', 'dark']) {
      for (const [size, viewport] of Object.entries(sizes)) {
        for (const [type, check] of Object.entries({queue: checkQueue, review: checkReview, restore: checkRestore, expiredRecovery: checkExpiredRecovery})) {
          const result = {name: `${type}-${theme}-${size}`, theme, viewport, checks: [], screenshots: [], layout: [], passed: false};
          report.cases.push(result);
          const {page, context, state} = await createPage(browser, result.name, theme, viewport);
          try { await check(page, result, state, theme); result.passed = true; }
          catch (error) { result.error = error.stack || error.message; console.error(`FAIL ${result.name}: ${error.message}`); }
          finally { await context.close(); }
        }
      }
    }
    // Validation pages get both palettes at the narrowest size, where long
    // messages and invalid-field links are most likely to reveal layout issues.
    for (const theme of ['light', 'dark']) {
      for (const fixtureName of ['keepError', 'removalError', 'expiredError', 'restoreError']) {
        const viewport = sizes.mobile;
        const result = {name: `${fixtureName}-${theme}-mobile`, theme, viewport, checks: [], screenshots: [], layout: [], passed: false};
        report.cases.push(result);
        const {page, context, state} = await createPage(browser, result.name, theme, viewport, fixtureName);
        try { await checkErrors(page, result, state, theme, fixtureName); result.passed = true; }
        catch (error) { result.error = error.stack || error.message; console.error(`FAIL ${result.name}: ${error.message}`); }
        finally { await context.close(); }
      }
    }
    const result = {name: 'no-javascript-decision-validation', theme: 'light', viewport: sizes.mobile,
      javaScriptEnabled: false, checks: [], screenshots: [], layout: [], passed: false};
    report.cases.push(result);
    const {page, context, state} = await createPage(browser, result.name, 'light', sizes.mobile, null,
      {javaScriptEnabled: false, validationOnly: true});
    try { await checkNoJavaScript(page, result, state); result.passed = true; }
    catch (error) { result.error = error.stack || error.message; console.error(`FAIL ${result.name}: ${error.message}`); }
    finally { await context.close(); }
  } finally {
    await browser.close();
    report.passed = report.cases.every(result => result.passed) &&
      [report.consoleErrors, report.pageErrors, report.unhandledRequests, report.assetFailures].every(items => items.length === 0);
    const reportPath = resolve(output, 'browser-report.json');
    writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.log(`${report.passed ? 'PASS' : 'FAIL'}: ${report.cases.filter(result => result.passed).length}/${report.cases.length} cases; ` +
      `${report.consoleErrors.length} console errors; ${report.pageErrors.length} page errors; ` +
      `${report.unhandledRequests.length} unhandled requests; ${report.assetFailures.length} missing assets.`);
    console.log(`Report: ${reportPath}`);
    console.log(`Screenshots: ${screenshots}`);
    if (!report.passed) process.exitCode = 1;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
