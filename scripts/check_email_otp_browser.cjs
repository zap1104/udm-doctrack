/* Opt-in real browser journey. Codes arrive on stdin from the simulated mailbox,
   and are never written to output or screenshots. */
const { chromium } = require('playwright');
const readline = require('node:readline');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.OTP_BROWSER_BASE;
const output = process.env.OTP_BROWSER_OUTPUT;
const target = process.env.OTP_BROWSER_TARGET;
const administrator = process.env.OTP_BROWSER_ADMIN;
const documentId = process.env.OTP_BROWSER_DOCUMENT;
const lines = readline.createInterface({ input: process.stdin });
const queue = [];
let receiver;
lines.on('line', line => { if (receiver) { const next=receiver; receiver=null; next(line); } else queue.push(line); });
function nextCode(user) {
  process.stdout.write(JSON.stringify({event:'code', user})+'\n');
  return queue.length ? Promise.resolve(queue.shift()) : new Promise(resolve => { receiver=resolve; });
}
let checks = 0;
let browser;
const errors = [];
function check(value, text) { if (!value) throw new Error(text); checks++; }

(async () => {
  fs.mkdirSync(output, {recursive:true});
  browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  async function login(context, user) {
    const page=await context.newPage();
    page.on('pageerror', error=>errors.push(error.message));
    await page.goto(base+'/accounts/login/');
    await page.locator('[name="username"]').fill(user);
    await page.locator('[name="password"]').fill('TestPass123!');
    await Promise.all([page.waitForURL('**/accounts/otp/'),page.locator('form[method="post"] button[type="submit"]').first().click()]);
    check(await page.locator('[name="code"]').count()===1,'Email code field absent');
    const protectedResponse=await context.request.get(base+'/tracking/');
    check((await protectedResponse.text()).includes('Sign in to DocTrack'),'Password-only browser gained record access');
    const code=await nextCode(user);
    await page.locator('[name="code"]').fill(code.trim());
    await Promise.all([page.waitForURL(url=>url.pathname!='/accounts/otp/'),page.locator('button[type="submit"]').first().click()]);
    check(!page.url().includes('/accounts/login/'),'OTP did not complete sign-in');
    return page;
  }
  for (const user of ['med','med_admin','viewer','admin']) {
    const context=await browser.newContext({viewport:{width:1366,height:900}});
    const page=await login(context,user);
    const response=await page.goto(base+'/tracking/');
    check(response.status()===200,'Tracking unavailable for '+user);
    if(user==='viewer') {
      const refused=await context.request.get(base+'/tracking/new/');
      check(refused.status()===403,'Viewer gained creation access');
    }
    if(user==='admin') {
      await page.goto(base+`/django-admin/accounts/user/${administrator}/change/`);
      await page.locator('[name="role"]').selectOption('USER');
      await Promise.all([
        page.waitForResponse(response=>response.request().method()==='POST' && response.url().includes(`/accounts/user/${administrator}/change/`)),
        page.locator('input[name="_save"]').click()
      ]);
      await page.getByText('You cannot remove your own administrator role.',{exact:true}).waitFor();
      check(await page.locator('.errornote').count()===1,'Self-demotion was not refused');
      await page.goto(base+`/django-admin/documents/document/${documentId}/change/`);
      check(await page.locator('[name="ocr_text"], [name="ocr_status"], [name="page_count"]').count()===0,'Blocked extraction controls remain editable');
      await page.locator('[name="title"]').fill('Browser corrected metadata');
      await Promise.all([page.waitForLoadState('load'),page.locator('input[name="_save"]').click()]);
      await page.waitForURL('**/django-admin/documents/document/');
      check(await page.getByText('Browser corrected metadata',{exact:true}).count()>0,'Allowed metadata correction did not save');
      for(const query of ['', '?q=Browser%20external%20metadata']) {
        await page.goto(base+'/documents/external/'+query);
        check(await page.getByText('Browser external metadata',{exact:true}).count()>0,'External metadata disappeared');
        check(!(await page.locator('body').innerText()).includes('private-browser-external-evidence'),'External card exposed restricted extracted text');
      }
      await page.goto(base+'/django-admin/accounts/permissionuser/');
      check(await page.getByRole('link',{name:'med',exact:true}).count()===1,'User permissions table missing');
      await page.goto(base+`/django-admin/accounts/permissionuser/${target}/change/`);
      await page.locator('[name="tracking__create"]').selectOption('block');
      await page.locator('[name="reason"]').fill('Browser verification: block Tracking creation');
      await Promise.all([page.waitForLoadState('load'),page.locator('input[type="submit"]').click()]);
      await page.waitForURL('**/change/');
      check(await page.locator('[name="tracking__create"]').inputValue()==='block','Permission save did not persist');
      for (const theme of ['light','dark']) {
        for(const width of [320,390,768,900,901,1366]) {
          await page.setViewportSize({width,height:900});
          await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.dataset.bsTheme=theme;},theme);
          check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2),'Permission matrix overflows viewport '+width);
          await page.screenshot({path:path.join(output,`permissions-${theme}-${width}.png`),fullPage:true});
        }
      }
      await page.setViewportSize({width:1366,height:900});
      await page.evaluate(()=>document.documentElement.style.zoom='200%');
      check(await page.locator('[name="tracking__create"]').isVisible(),'Matrix unusable at 200% zoom');
      await page.evaluate(()=>document.documentElement.style.zoom='');
      await page.keyboard.press('Tab');
      check(await page.evaluate(()=>document.activeElement!==document.body),'Keyboard focus lost');
      const shares=await page.goto(base+'/django-admin/record-sharing/');
      check(shares.status()===200,'Record sharing table unavailable');
    }
    await context.close();
  }
  const restricted=await browser.newContext();
  await login(restricted,'med');
  check((await restricted.request.get(base+'/tracking/new/')).status()===403,'Saved restriction not enforced on real backend');
  await restricted.close();
  const native=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:844}});
  const nativePage=await login(native,'med');
  check((await nativePage.goto(base+'/tracking/')).status()===200,'No-JavaScript login failed');
  await native.close();
  check(errors.length===0,'Browser runtime errors: '+errors.join('; '));
  await browser.close();
  process.stdout.write(JSON.stringify({event:'complete',checks,errors})+'\n');
  lines.close();
})().catch(async error=>{process.stderr.write(error.stack+'\n');if(browser) await browser.close();process.exitCode=1;lines.close();});
