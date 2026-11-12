'use strict';
// Real authenticated requests, synthetic files, and a private simulated mailbox.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const lines = readline.createInterface({input:process.stdin});
const queue=[]; let receiver;
lines.on('line', line=>{if(receiver){const resolve=receiver;receiver=null;resolve(line);}else queue.push(line);});
function code(){process.stdout.write(JSON.stringify({event:'code'})+'\n');return queue.length?Promise.resolve(queue.shift()):new Promise(resolve=>{receiver=resolve;});}
const report={checks:0,previews:0,errors:[],externalRequests:[],cases:[],screenshots:[],limitations:['Chromium emulation only.','Synthetic files; email/OCR/cloud storage simulated.','Signed-link expiry is covered separately; this long matrix uses a one-hour test TTL.']};
function check(value,message){if(!value)throw new Error(message);report.checks++;}
const vendor={ 'bootstrap.min.css':'doctrack-bootstrap-5.3.3.min.css','bootstrap.bundle.min.js':'doctrack-bootstrap-5.3.3.min.js','htmx.min.js':'doctrack-htmx-1.9.12.min.js'};
async function configure(context){
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());let local;
    if(url.pathname.startsWith('/static/')){const candidate=path.resolve(manifest.root,'.'+decodeURIComponent(url.pathname));if(candidate.startsWith(path.resolve(manifest.root)+path.sep)&&fs.existsSync(candidate))local=candidate;}
    if(vendor[path.basename(url.pathname)])local=path.join(process.env.TEMP,vendor[path.basename(url.pathname)]);
    if(local&&fs.existsSync(local)){
      const ext=path.extname(local);const types={'.js':'application/javascript','.mjs':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.wasm':'application/wasm'};
      return route.fulfill({path:local,contentType:types[ext]||'application/octet-stream'});
    }
    if(url.origin!==manifest.origin){report.externalRequests.push(url.origin+url.pathname);return route.abort();}
    return route.continue();
  });
}
async function login(browser,javascript=true){
  const context=await browser.newContext({baseURL:manifest.origin,viewport:{width:1366,height:950},javaScriptEnabled:javascript});await configure(context);
  const page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',error=>report.errors.push(error.message));page.on('dialog',async dialog=>{report.errors.push('Unexpected dialog');await dialog.dismiss();});
  await page.goto(manifest.origin+'/accounts/login/');
  await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('TestPass123!');
  await Promise.all([page.waitForURL('**/accounts/otp/'),page.locator('form[method=post] button[type=submit]').first().click()]);
  const refused=await context.request.get(manifest.origin+'/tracking/');check((await refused.text()).includes('Sign in to DocTrack'),'Password-only preview access');
  await page.locator('[name=code]').fill((await code()).trim());
  await Promise.all([page.waitForURL(url=>url.pathname!='/accounts/otp/'),page.locator('button[type=submit]').first().click()]);
  return {context,page};
}
let browser;
(async()=>{
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const {context,page}=await login(browser);
  for(const item of manifest.cases){
    const response=await page.goto(manifest.origin+item.preview);check(response.status()===200,'Preview page unavailable: '+item.area+' '+item.format);
    check(response.headers()['cache-control'].includes('no-store'),'Preview cache policy');
    check(response.headers()['x-content-type-options']==='nosniff','Preview MIME policy');
    const root=page.locator('[data-file-preview]');
    check((await root.innerText()).includes(`Viewing Version ${item.version} of ${item.bad?1:2}`),'Wrong history label');
    if(item.version===1&&!item.bad)check(await root.getByText('Earlier version',{exact:true}).count()===1,'Missing older-version warning');
    if(item.bad){await root.getByText(/Preview unavailable/).first().waitFor();}
    else if(['pdf','docx','jpg','png'].includes(item.format)){
      await root.locator('[data-preview-status]').getByText('Preview ready. The original file is unchanged.',{exact:true}).waitFor();
      if(item.format==='pdf'){
        check(await root.locator('canvas').count()===1,'PDF canvas missing');check(await root.locator('.textLayer').innerText().then(text=>text.includes(item.marker)),'PDF selectable text missing');
        await root.locator('[data-next]').click();await root.locator('[data-page-count]').getByText('Page 2 of 2',{exact:true}).waitFor();
        check(await root.locator('[data-next]').isDisabled(),'PDF final-page boundary');
        await root.locator('[data-zoom-in]').click();check(await root.locator('[data-zoom]').innerText()==='125%','PDF zoom readout');
      }else if(item.format==='docx'){
        const frame=page.frameLocator('iframe[title="Approximate DOCX preview"]');
        check((await frame.locator('body').innerText()).includes(item.marker),'DOCX text missing');
        check((await frame.locator('table').innerText()).includes('Formatted table evidence'),'DOCX table missing');
        check(await frame.locator('img').count()>=1,'DOCX embedded image missing');
        check(await frame.locator('img').first().evaluate(image=>image.decode().then(()=>image.naturalWidth>0)),'DOCX embedded image did not decode');
        check(await frame.locator('a[href]').count()===0,'DOCX hyperlink remained active');
        check(await page.locator('iframe').getAttribute('sandbox')==='allow-same-origin','DOCX sandbox policy');
      }else{check(await root.locator('img').evaluate(image=>image.naturalWidth===64&&image.naturalHeight===48),'Image dimensions changed');await root.locator('[data-zoom-in]').click();check(await root.locator('[data-zoom]').innerText()==='125%','Image zoom');}
    }else{
      check((await root.innerText()).includes(item.marker),'Text/table evidence missing');check(await root.getByText(/Preview truncated/).count()===1,'Missing truncation disclosure');
      check(await root.locator('img,script').count()===0,'Text/table became active HTML');
      if(item.format==='xlsx'){
        check(await root.locator('tbody tr').count()===200,'XLSX row limit');
        check(await root.locator('tbody tr').nth(1).innerText().then(text=>!text.includes('HYPERLINK')&&!text.includes('Click')),'Spreadsheet formula executed');
        await root.locator('[name=worksheet]').selectOption('Second sheet');await Promise.all([page.waitForNavigation(),root.getByRole('button',{name:'Show worksheet'}).click()]);
        check((await page.locator('[data-file-preview]').innerText()).includes('Second worksheet evidence'),'Worksheet navigation failed');
      }
    }
    check(!(await page.evaluate(()=>Boolean(window.__previewExploit))),'Uploaded active content executed');
    if(item.area==='repository'&&item.version===2&&['pdf','docx','jpg','xlsx'].includes(item.format)){
      for(const [width,theme] of [[390,'dark'],[1366,'light']]){
        await page.setViewportSize({width,height:900});await page.evaluate(value=>{document.documentElement.dataset.theme=value;document.documentElement.dataset.bsTheme=value;},theme);
        check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2),'Preview overflowed the whole viewport');
        const screenshot=path.join(manifest.output,`${item.format}-${theme}-${width}.png`);await page.screenshot({path:screenshot,fullPage:true});report.screenshots.push(screenshot);
      }
    }
    const download=page.getByRole('link',{name:'Download original',exact:true});const downloaded=await context.request.get(await download.getAttribute('href'));
    check(downloaded.status()===200,'Original download unavailable');check(crypto.createHash('sha256').update(await downloaded.body()).digest('hex')===item.sha256,'Original bytes changed');
    const back=page.getByRole('link',{name:'Back to record',exact:true});await Promise.all([page.waitForNavigation(),back.click()]);check(new URL(page.url()).pathname.startsWith('/tracking/')||new URL(page.url()).pathname.startsWith('/documents/'),'Back navigation failed');
    report.previews++;report.cases.push({area:item.area,format:item.format,version:item.version,bad:item.bad});
    fs.writeFileSync(path.join(manifest.output,'results.json'),JSON.stringify(report,null,2));
  }
  const nojs=await login(browser,false);const pdf=manifest.cases.find(item=>item.format==='pdf'&&!item.bad);
  await nojs.page.goto(manifest.origin+pdf.preview);check((await nojs.page.locator('body').innerText()).includes('JavaScript is needed for this preview'),'Missing no-JavaScript fallback');
  check(await nojs.page.getByRole('link',{name:'Download original',exact:true}).count()===1,'No-JavaScript download missing');
  check(report.errors.length===0,'Browser errors: '+report.errors.join('; '));check(report.externalRequests.length===0,'Unexpected external resource requested');
  process.stdout.write(JSON.stringify({event:'complete',previews:report.previews,checks:report.checks})+'\n');
})().catch(error=>{report.failure=error.stack;process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(async()=>{fs.writeFileSync(path.join(manifest.output,'results.json'),JSON.stringify(report,null,2));if(browser)await browser.close();lines.close();process.stdin.destroy();});
