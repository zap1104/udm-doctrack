'use strict';
// Browser QA uses captured Django responses and local assets. Every backend mutation is blocked.
const {readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync} = require('node:fs');
const {resolve, extname, sep} = require('node:path');
const {createHash} = require('node:crypto');
const {chromium} = require('playwright');
const root = resolve(__dirname, '..');
const output = resolve(root, 'docs/verification/external-offices-2026-10-15');
const fixtures = JSON.parse(readFileSync(resolve(output, 'pages.json'), 'utf8'));
const origin = fixtures.origin;
const values = (name, defaults) => process.env[name] ? process.env[name].split(',').map(value => value.trim()).filter(Boolean) : defaults;
const widths = values('EXTERNAL_QA_WIDTHS', [320, 390, 768, 901, 1366]).map(Number);
const themes = values('EXTERNAL_QA_THEMES', ['light', 'dark']);
const selectedRoles = values('EXTERNAL_QA_ROLES', []);
const selectedSurfaces = values('EXTERNAL_QA_SURFACES', []);
// Keep the initial browser-report.json failure diagnostic intact by default.
const reportStem = process.env.EXTERNAL_QA_REPORT_STEM || 'browser-current';
const extendedDiagnostic = process.env.EXTERNAL_QA_DIAGNOSTIC === '1';
const checkInteractions = ['1','filters'].includes(process.env.EXTERNAL_QA_INTERACTIONS);
const checkShellInteractions = process.env.EXTERNAL_QA_INTERACTIONS === '1';
const menuDiagnostic = process.env.EXTERNAL_QA_MENU_DIAGNOSTIC === '1';
const menuActivation = process.env.EXTERNAL_QA_MENU_ACTIVATION || 'touch';
const menuFocusCandidate = process.env.EXTERNAL_QA_MENU_FOCUS_CANDIDATE === '1';
if (!/^[a-z0-9-]+$/.test(reportStem)) throw new Error('Invalid EXTERNAL_QA_REPORT_STEM.');
if (widths.some(value => !Number.isInteger(value) || value < 1)) throw new Error('Invalid EXTERNAL_QA_WIDTHS.');
if (themes.some(value => !['light','dark'].includes(value))) throw new Error('Invalid EXTERNAL_QA_THEMES.');
if (selectedRoles.some(value => !fixtures.pages.some(item => item.role === value))) throw new Error('Unknown EXTERNAL_QA_ROLES.');
if (selectedSurfaces.some(value => !fixtures.pages.some(item => item.surface === value))) throw new Error('Unknown EXTERNAL_QA_SURFACES.');
const vendorRoot = process.env.EXTERNAL_QA_VENDOR_DIR || process.env.TEMP;
const vendor = {
  'bootstrap.min.css': resolve(vendorRoot, 'doctrack-bootstrap-5.3.3.min.css'),
  'bootstrap.bundle.min.js': resolve(vendorRoot, 'doctrack-bootstrap-5.3.3.min.js'),
  'htmx.min.js': resolve(vendorRoot, 'doctrack-htmx-1.9.12.min.js'),
};
const hashFiles = ['static/css/external.css','static/css/workspace.css','static/css/doctrack.css','static/js/doctrack.js','static/js/theme-init.js','static/js/auto-filters.js','templates/base.html','templates/partials/_sidebar.html','templates/partials/_topbar.html','templates/partials/_auto_filter_attrs.html',...['templates/tracking/external','templates/documents/external'].flatMap(dir => readdirSync(resolve(root,dir),{recursive:true}).filter(name => name.endsWith('.html')).map(name => dir + '/' + name.replaceAll('\\','/')))];
const report = {generatedAt: new Date().toISOString(), scope: fixtures.scope, widths, themes,roles:selectedRoles,surfaces:selectedSurfaces,keyboardMethod:extendedDiagnostic ? 'Original programmatic focus and 100ms wait, with diagnostic follow-up' : 'Normal Tab navigation and animation-frame polling until native scroll settles (1500ms limit)',
  menuProbe:menuDiagnostic ? {activation:menuActivation,focusCandidate:menuFocusCandidate ? 'Browser-only intercepted asset: requestAnimationFrame with open-state guard' : 'Unmodified product asset'} : undefined,
  interactionScope:checkShellInteractions ? 'Theme, mobile menu and automatic filters' : checkInteractions ? 'Automatic filters only; shell interactions covered by separate diagnostics' : 'Layout and keyboard checks only',
  fixturesHash:createHash('sha256').update(readFileSync(resolve(output,'pages.json'))).digest('hex'),
  interactionLimitations:'HTMX requests are answered with the captured original HTML for the same role and path. These checks verify automatic GET transport, query preservation, main-content replacement, focus and absence of full reload. They do not prove filtered result data or live backend mutations.',
  cases: [], errors: [], blockedRequests: [], transportRequests: [], menuDiagnostics: [], screenshots: [],
  hashes: Object.fromEntries(hashFiles.map(name => [name, createHash('sha256').update(readFileSync(resolve(root, name))).digest('hex')]))};
const mime = file => ({'.css':'text/css', '.js':'application/javascript', '.svg':'image/svg+xml', '.png':'image/png', '.ico':'image/x-icon', '.woff2':'font/woff2'}[extname(file)] || 'application/octet-stream');
const roles = [...new Set(fixtures.pages.map(item => item.role))].filter(role => !selectedRoles.length || selectedRoles.includes(role));
const scrollerState = el => ({active:document.activeElement === el,focusVisible:el.matches(':focus-visible'),outlineStyle:getComputedStyle(el).outlineStyle,outlineWidth:getComputedStyle(el).outlineWidth,outlineColor:getComputedStyle(el).outlineColor,scrollLeft:el.scrollLeft,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,documentHasFocus:document.hasFocus()});
mkdirSync(output, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try {
    for (const width of widths) for (const theme of themes) {
      const context = await browser.newContext({viewport:{width,height:1000}, colorScheme:theme, hasTouch:true, reducedMotion:'reduce'});
      await context.addInitScript(value => {localStorage.setItem('doctrack-theme', value); window.print = () => {};}, theme);
      for (const role of roles) {
        // Full-page screenshots reset touch emulation in the bundled Chromium.
        // A new page gives every case the requested pointer settings again.
        for (const fixture of fixtures.pages.filter(item => item.role === role && (!selectedSurfaces.length || selectedSurfaces.includes(item.surface)))) {
        const page = await context.newPage();
        page.setDefaultTimeout(5000);
        page.on('pageerror', error => report.errors.push({role,width,theme,message:error.message}));
        await page.route('**/*', async route => {
          const request = route.request(), url = new URL(request.url());
          if (request.method() !== 'GET') {
            if (url.pathname === '/accounts/session/keep-alive/') return route.fulfill({contentType:'application/json',body:'{"seconds_remaining":1800}'});
            report.blockedRequests.push({role,path:url.pathname,method:request.method()});
            return route.fulfill({status:405,body:'Offline browser QA blocks backend writes.'});
          }
          let asset;
          if (url.pathname.includes('bootstrap') && url.pathname.endsWith('.css')) asset = vendor['bootstrap.min.css'];
          else if (url.pathname.includes('bootstrap') && url.pathname.endsWith('.js')) asset = vendor['bootstrap.bundle.min.js'];
          else if (url.pathname.includes('htmx') && url.pathname.endsWith('.js')) asset = vendor['htmx.min.js'];
          else if (url.pathname.startsWith('/static/')) {
            const candidate = resolve(root, '.' + decodeURIComponent(url.pathname));
            if (candidate.startsWith(root + sep) && existsSync(candidate)) asset = candidate;
          }
          if (asset && existsSync(asset)) {
            if (menuFocusCandidate && asset === resolve(root,'static/js/doctrack.js')) {
              const original = readFileSync(asset,'utf8');
              const needle = 'if (firstLink) window.setTimeout(function () { firstLink.focus(); }, 0);';
              if (!original.includes(needle)) throw new Error('Sidebar focus candidate source did not match.');
              return route.fulfill({contentType:mime(asset),body:original.replace(needle,"if (firstLink) window.requestAnimationFrame(function () { if (document.body.classList.contains('sidebar-open')) firstLink.focus(); });")});
            }
            return route.fulfill({path:asset,contentType:mime(asset),headers:{'access-control-allow-origin':'*'}});
          }
          if (checkInteractions && request.headers()['hx-request'] === 'true') {
            const transportFixture = fixtures.pages.find(item => item.role === role && new URL(item.url, origin).pathname === url.pathname);
            if (transportFixture) {
              report.transportRequests.push({role,width,theme,method:request.method(),path:url.pathname,query:url.search,fixtureSurface:transportFixture.surface,response:'Captured original HTML for transport verification'});
              return route.fulfill({contentType:'text/html',body:transportFixture.html});
            }
          }
          const fixture = fixtures.pages.find(item => item.role === role && new URL(item.url, origin).pathname + new URL(item.url, origin).search === url.pathname + url.search);
          if (fixture) return route.fulfill({contentType:'text/html',body:fixture.html});
          report.errors.push({role,width,theme,message:'Uncaptured request ' + url.pathname});
          return route.fulfill({status:404,body:'Uncaptured QA resource.'});
        });
          let stage = 'load';
          try {
            await page.goto(origin + fixture.url);
            await page.waitForFunction(() => window.htmx && window.bootstrap);
            if (menuDiagnostic && width <= 900 && fixture.surface === 'external-tracking') {
              await page.evaluate(() => {
                window.externalQAFocusTrace=[];
                const describe = el => el.tagName + '.' + el.className;
                ['focusin','focusout','pointerdown','pointerup','click'].forEach(type => document.addEventListener(type,event=>window.externalQAFocusTrace.push({type,target:describe(event.target),active:describe(document.activeElement),time:performance.now()}),true));
                const nativeFocus=HTMLElement.prototype.focus;
                HTMLElement.prototype.focus=function(...args){window.externalQAFocusTrace.push({type:'focus-call',target:describe(this),time:performance.now()});const result=nativeFocus.apply(this,args);window.externalQAFocusTrace.push({type:'focus-return',active:describe(document.activeElement),time:performance.now()});return result;};
              });
              if (menuActivation === 'keyboard') {
                await page.locator('[data-sidebar-toggle]').focus();
                await page.keyboard.press('Enter');
              } else await page.locator('[data-sidebar-toggle]').tap();
              await page.waitForTimeout(300);
              const diagnostic=await page.evaluate(()=>({active:document.activeElement.tagName + '.' + document.activeElement.className,focusInside:document.getElementById('main-sidebar').contains(document.activeElement),trace:window.externalQAFocusTrace}));
              report.menuDiagnostics.push({role,width,theme,...diagnostic});
              await page.keyboard.press('Escape');
            }
            if (extendedDiagnostic) await page.evaluate(() => {
              window.externalQAKeyEvents = [];
              ['keydown','keyup'].forEach(type => document.addEventListener(type, event => {
                queueMicrotask(() => window.externalQAKeyEvents.push({type,key:event.key,defaultPrevented:event.defaultPrevented,target:event.target.className,time:performance.now()}));
              }));
            });
            const state = await page.evaluate(() => {
              const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && !el.closest('[inert]');
              const controls = [...document.querySelectorAll('.external-workspace .btn, .external-nav a, .workspace-page .btn')].filter(visible);
              const short = controls.filter(el => {const r = el.getBoundingClientRect(); return r.width < 43.5 || r.height < 43.5;}).map(el => ({text:el.textContent.trim().slice(0,80),width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}));
              const scrollers = [...document.querySelectorAll('.table-responsive')].filter(visible).map(el => ({overflow:el.scrollWidth > el.clientWidth + 1,tabindex:el.getAttribute('tabindex'),role:el.getAttribute('role'),label:el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')}));
              return {overflow:document.documentElement.scrollWidth-innerWidth,theme:document.documentElement.dataset.theme,pointer:{coarse:matchMedia('(pointer: coarse)').matches,fine:matchMedia('(pointer: fine)').matches,maxTouchPoints:navigator.maxTouchPoints},short,scrollers};
            });
            const failures = [];
            if (state.overflow > 1) failures.push('Full page horizontal overflow ' + state.overflow);
            if (state.theme !== theme) failures.push('Theme mismatch');
            if (!state.pointer.coarse) failures.push('Touch emulation lost its coarse primary pointer');
            if (state.short.length) failures.push('Controls smaller than 44px');
            if (state.scrollers.some(item => item.overflow && (item.tabindex !== '0' || item.role !== 'region' || !item.label))) failures.push('Overflowing table has no named keyboard focus region');
            let focusChecks = 0;
            stage = 'keyboard tables';
            const focusDiagnostics = [];
            for (const scroller of await page.locator('.table-responsive').all()) {
              if (await scroller.evaluate(el => el.scrollWidth > el.clientWidth + 1)) {
                const beforeFocus = await scroller.evaluate(scrollerState);
                let tabSteps = 0;
                if (extendedDiagnostic) await scroller.focus();
                else while (!(await scroller.evaluate(el => document.activeElement === el)) && tabSteps < 100) {
                  await page.keyboard.press('Tab');
                  tabSteps++;
                }
                const afterFocus = await scroller.evaluate(scrollerState);
                const focused = afterFocus.active && afterFocus.focusVisible && afterFocus.outlineStyle !== 'none' && parseFloat(afterFocus.outlineWidth) > 0;
                await page.keyboard.press('ArrowRight');
                let scrollWait;
                if (extendedDiagnostic) await page.waitForTimeout(100);
                else scrollWait = await scroller.evaluate(el => new Promise(resolve => {
                  const start = performance.now();
                  let last = el.scrollLeft, stableFrames = 0;
                  const sample = () => {
                    const current = el.scrollLeft;
                    stableFrames = current > 0 && current === last ? stableFrames + 1 : 0;
                    if (stableFrames >= 3 || performance.now() - start >= 1500) return resolve({settled:stableFrames >= 3,elapsedMs:Math.round(performance.now()-start),scrollLeft:current});
                    last = current;
                    requestAnimationFrame(sample);
                  };
                  requestAnimationFrame(sample);
                }));
                const afterKey = await scroller.evaluate(scrollerState);
                const moved = afterKey.scrollLeft > 0 && (extendedDiagnostic || scrollWait.settled);
                if (!focused || !moved) failures.push('Table cannot be focused and scrolled with keyboard');
                const diagnostic = {beforeFocus,afterFocus,afterKey,tabSteps,scrollWait};
                if (extendedDiagnostic) {
                  await page.waitForTimeout(400);
                  diagnostic.after500ms = await scroller.evaluate(scrollerState);
                  await page.keyboard.press('ArrowRight');
                  await page.waitForTimeout(100);
                  diagnostic.afterSecondKey = await scroller.evaluate(scrollerState);
                  diagnostic.keyEvents = await page.evaluate(() => window.externalQAKeyEvents);
                  await page.goto(origin + fixture.url);
                  await page.waitForFunction(() => window.htmx && window.bootstrap);
                  diagnostic.tabStops = [];
                  for (let count = 0; count < 100; count++) {
                    await page.keyboard.press('Tab');
                    diagnostic.tabStops.push(await page.evaluate(() => ({tag:document.activeElement.tagName,id:document.activeElement.id,text:document.activeElement.textContent.trim().slice(0,80),target:document.activeElement.matches('.table-responsive')})));
                    if (diagnostic.tabStops.at(-1).target) break;
                  }
                  diagnostic.afterTab = await scroller.evaluate(scrollerState);
                  await page.keyboard.press('ArrowRight');
                  await page.waitForTimeout(100);
                  diagnostic.afterTabArrow = await scroller.evaluate(scrollerState);
                }
                focusDiagnostics.push(diagnostic);
                await scroller.evaluate(el => {el.scrollLeft = 0;});
                focusChecks++;
              }
            }
            if (role === 'viewer' && await page.locator('a[href="/tracking/external/new/"],a[href="/documents/external/upload/"]').count()) failures.push('Viewer offered an external create action');
            const interactions = [];
            if (checkShellInteractions && fixture.surface === 'external-tracking') {
              stage = 'theme toggle';
              const themeToggle = page.locator('[data-theme-toggle]');
              const initialBackground = await page.locator('body').evaluate(el => getComputedStyle(el).backgroundColor);
              await themeToggle.tap();
              const otherTheme = theme === 'light' ? 'dark' : 'light';
              await page.waitForFunction(({expected,background}) => document.documentElement.dataset.theme === expected && getComputedStyle(document.body).backgroundColor !== background,{expected:otherTheme,background:initialBackground});
              const toggled = await page.evaluate(() => ({theme:document.documentElement.dataset.theme,bootstrapTheme:document.documentElement.dataset.bsTheme,stored:localStorage.getItem('doctrack-theme'),background:getComputedStyle(document.body).backgroundColor}));
              const themePassed = toggled.theme === otherTheme && toggled.bootstrapTheme === otherTheme && toggled.stored === otherTheme && toggled.background !== initialBackground;
              interactions.push({name:'Theme toggle updates both themes, saved preference and page color',passed:themePassed,state:toggled});
              if (!themePassed) failures.push('Theme toggle did not update the rendered theme');
              await themeToggle.tap();
              await page.waitForFunction(({expected,background}) => document.documentElement.dataset.theme === expected && getComputedStyle(document.body).backgroundColor === background,{expected:theme,background:initialBackground});
              if (width <= 900) {
                stage = 'mobile menu';
                await page.locator('[data-sidebar-toggle]').tap();
                try { await page.waitForFunction(() => document.getElementById('main-sidebar').contains(document.activeElement)); }
                catch (error) {
                  const diagnostic = await page.evaluate(() => {
                    const sidebar = document.getElementById('main-sidebar'), first = sidebar.querySelector('a,button');
                    const before = {bodyClass:document.body.className,active:document.activeElement.outerHTML.slice(0,250),sidebarInert:sidebar.inert,inertAncestor:sidebar.closest('[inert]')?.tagName,sidebarVisibility:getComputedStyle(sidebar).visibility,firstVisibility:getComputedStyle(first).visibility,firstDisplay:getComputedStyle(first).display,firstRect:first.getBoundingClientRect().toJSON(),documentHasFocus:document.hasFocus(),mainInert:document.querySelector('.app-main').inert,expanded:document.querySelector('[data-sidebar-toggle]').getAttribute('aria-expanded')};
                    first.focus();
                    return {...before,manualFocusWorked:document.activeElement===first};
                  });
                  throw new Error(error.message + ' ' + JSON.stringify(diagnostic));
                }
                const opened = await page.evaluate(() => ({open:document.body.classList.contains('sidebar-open'),expanded:document.querySelector('[data-sidebar-toggle]').getAttribute('aria-expanded'),sidebarInert:document.getElementById('main-sidebar').inert,mainInert:document.querySelector('.app-main').inert,focusInside:document.getElementById('main-sidebar').contains(document.activeElement)}));
                await page.keyboard.press('Shift+Tab');
                const trapped = await page.evaluate(() => document.getElementById('main-sidebar').contains(document.activeElement));
                await page.keyboard.press('Escape');
                const closed = await page.evaluate(() => ({open:document.body.classList.contains('sidebar-open'),expanded:document.querySelector('[data-sidebar-toggle]').getAttribute('aria-expanded'),sidebarInert:document.getElementById('main-sidebar').inert,mainInert:document.querySelector('.app-main').inert,focusReturned:document.activeElement.matches('[data-sidebar-toggle]')}));
                const sidebarPassed = opened.open && opened.expanded === 'true' && !opened.sidebarInert && opened.mainInert && opened.focusInside && trapped && !closed.open && closed.expanded === 'false' && closed.sidebarInert && !closed.mainInert && closed.focusReturned;
                interactions.push({name:'Mobile menu opens, keeps Tab inside, closes with Escape and returns focus',passed:sidebarPassed,opened,trapped,closed});
                if (!sidebarPassed) failures.push('Mobile navigation focus or open/close state failed');
              }
            }
            if (checkInteractions && ['external-tracking','external-reports','external-repository','external-repository-reports'].includes(fixture.surface)) {
              stage = 'automatic filters';
              const filter = page.locator('main form[data-auto-filter]').first();
              if (!(await filter.count())) throw new Error('Expected automatic filter form is missing.');
              await filter.locator('input[name="q"]').fill('Keyboard QA');
              if (await filter.locator('input[name="year"]').count()) await filter.locator('input[name="year"]').fill('2026');
              const select = filter.locator(await filter.locator('select[name="direction"]').count() ? 'select[name="direction"]' : 'select[name="source"]');
              const selection = await select.evaluate(el => ({id:el.id,name:el.name,value:[...el.options].find(option=>option.value && !option.disabled).value}));
              await page.evaluate(() => {window.externalQAStableToken='same-document';window.externalQAOriginalMain=document.getElementById('main-content');window.externalQAOriginalForm=document.querySelector('main form[data-auto-filter]');});
              const requestPromise = page.waitForRequest(request => request.method()==='GET' && request.headers()['hx-request']==='true' && new URL(request.url()).pathname===new URL(fixture.url,origin).pathname);
              await select.focus();
              await select.selectOption(selection.value);
              const expected = await filter.evaluate(el => ({method:el.method,query:new URLSearchParams(new FormData(el)).toString(),labelled:[...el.querySelectorAll('input:not([type="hidden"]),select')].every(field => field.labels.length > 0)}));
              const request = await requestPromise;
              await page.waitForFunction(id => !window.externalQAOriginalForm.isConnected && document.getElementById('main-content').getAttribute('aria-busy')!=='true' && document.activeElement.id===id,selection.id);
              const actual = await page.evaluate(() => ({stableWindow:window.externalQAStableToken==='same-document',sameMain:window.externalQAOriginalMain===document.getElementById('main-content'),formReplaced:!window.externalQAOriginalForm.isConnected,focus:document.activeElement.id,url:location.href}));
              const query = new URL(request.url()).searchParams;
              const preserved = [...new URLSearchParams(expected.query)].every(([name,value]) => query.getAll(name).includes(value));
              const filterPassed = expected.method==='get' && expected.labelled && preserved && query.get('q')==='Keyboard QA' && query.get(selection.name)===selection.value && actual.stableWindow && actual.sameMain && actual.formReplaced && actual.focus===selection.id && new URL(actual.url).searchParams.get(selection.name)===selection.value;
              interactions.push({name:'Automatic select GET preserves filters, replaces main content, keeps focus and avoids a full reload',passed:filterPassed,expected,requestedQuery:query.toString(),selection,actual});
              if (!filterPassed) failures.push('Automatic in-place filter transport or query preservation failed');
            }
            const result = {role,surface:fixture.surface,width,theme,...state,focusChecks,focusDiagnostics,interactions,failures};
            report.cases.push(result);
            if (failures.length) console.log(JSON.stringify({failure:result}));
            const capture = (fixture.surface === 'external-tracking' && [320,768,1366].includes(width)) || (role === 'admin' && [390,1366].includes(width) && ['external-reports','external-detail','external-slip','external-correction','external-repository','external-upload'].includes(fixture.surface));
            if (capture) {
              await page.evaluate(() => {document.activeElement?.blur();window.scrollTo(0,0);});
              const name = `${reportStem === 'browser' ? '' : reportStem + '-'}${fixture.surface}-${role}-${width}-${theme}.png`;
              await page.screenshot({path:resolve(output,name),fullPage:true});
              result.pointerAfterScreenshot = await page.evaluate(() => ({coarse:matchMedia('(pointer: coarse)').matches,fine:matchMedia('(pointer: fine)').matches,maxTouchPoints:navigator.maxTouchPoints}));
              report.screenshots.push(name);
            }
            if (fixture.surface === 'external-slip' && width === 1366) {
              await page.emulateMedia({media:'print'});
              const printState = await page.evaluate(() => ({paper:!!document.querySelector('.external-paper'),nav:getComputedStyle(document.querySelector('.external-nav')).display,actions:getComputedStyle(document.querySelector('.external-actions')).display,text:document.querySelector('.external-paper').textContent}));
              if (!printState.paper || printState.nav !== 'none' || printState.actions !== 'none' || !printState.text.includes('Complete custody history')) failures.push('Printed slip omits history or shows navigation/actions');
              await page.emulateMedia({media:'screen'});
            }
          } catch (error) {report.errors.push({role,surface:fixture.surface,width,theme,stage,message:error.message});}
        await page.close();
        }
      }
      await context.close();
      writeFileSync(resolve(output,`${reportStem}-progress.json`), JSON.stringify(report,null,2));
      console.log(`Checked ${width}px ${theme}: ${report.cases.filter(item=>item.width===width&&item.theme===theme).length} real role/page responses.`);
    }
  } finally {
    await browser.close();
    report.summary = {cases:report.cases.length,failedCases:report.cases.filter(item=>item.failures.length).length,errors:report.errors.length,blockedRequests:report.blockedRequests.length,keyboardScrollerChecks:report.cases.reduce((count,item)=>count+item.focusChecks,0),interactionChecks:report.cases.reduce((count,item)=>count+item.interactions.length,0),screenshots:report.screenshots.length};
    writeFileSync(resolve(output,`${reportStem}-report.json`), JSON.stringify(report,null,2));
    console.log(JSON.stringify(report.summary));
    if (report.summary.failedCases || report.summary.errors || report.summary.blockedRequests) process.exitCode = 1;
  }
})().catch(error => {console.error(error);process.exitCode=1;});
