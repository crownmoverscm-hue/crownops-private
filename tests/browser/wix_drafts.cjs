'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const context=await browser.newContext(),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
await context.addCookies([{name:'crownops_session',value:'owner-token',url:'http://127.0.0.1:8095'}]);
let external=0;await page.route('**/*',route=>{if(!route.request().url().startsWith('http://127.0.0.1:8095')){external++;return route.abort();}return route.continue();});
// Hold initialization to prove the read control cannot accept a lost early click.
let releaseStatus,seenStatus;
const statusGate=new Promise(resolve=>releaseStatus=resolve),statusSeen=new Promise(resolve=>seenStatus=resolve);
await page.route('**/api/wix-drafts/status',async route=>{seenStatus();await statusGate;await route.continue();});
await page.goto('http://127.0.0.1:8095/wix-draft-sync');await statusSeen;
assert(await page.getByRole('button',{name:'Read local preflight',exact:true}).isDisabled());
releaseStatus();await page.locator('#release').filter({hasText:'CrownOps'}).waitFor();await page.unroute('**/api/wix-drafts/status');
for(const width of [320,390,768,1440]){
await page.setViewportSize({width,height:900});await page.goto('http://127.0.0.1:8095/wix-draft-sync');
await page.getByRole('button',{name:'Read local preflight',exact:true}).click();
await page.locator('#snapshot').filter({hasText:'snapshot_hash'}).waitFor();
assert.match(await page.locator('#snapshot').innerText(),/FULL TEST TERMS/);
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow '+width);
assert(await page.getByRole('button',{name:'Create unsent Wix draft — blocked',exact:true}).isDisabled());
if(width===1440){await page.locator('#reason').fill('Independent browser fixture snapshot review only');await page.locator('#confirm').check();await page.getByRole('button',{name:'Record local snapshot review — no provider write'}).click();await page.locator('#error').filter({hasText:'"provider_writes":0'}).waitFor();}
}
await context.addCookies([{name:'crownops_session',value:'sales-token',url:'http://127.0.0.1:8095'}]);await page.reload();await page.locator('#identity').filter({hasText:'sales'}).waitFor();await page.locator('#status').filter({hasText:'can_review'}).waitFor();assert(await page.locator('#access-panel').isHidden());
assert.equal(external,0);assert.deepEqual(errors,[]);await browser.close();console.log('PASS Wix draft readiness: delayed initialization, four widths, real fixture auth/preflight/local review, no external requests.');
})().catch(e=>{console.error(e);process.exit(1);});
