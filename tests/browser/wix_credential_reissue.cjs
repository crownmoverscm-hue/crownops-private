'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict'),crypto=require('node:crypto');
(async()=>{
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const context=await browser.newContext(),page=await context.newPage(),errors=[];let external=0;
page.on('pageerror',e=>errors.push(e.message));
await context.addCookies([{name:'crownops_session',value:'owner-token',url:'http://127.0.0.1:8095'}]);
await page.route('**/*',route=>{if(!route.request().url().startsWith('http://127.0.0.1:8095')){external++;return route.abort();}return route.continue();});
for(const width of [320,390,768,1440]){
 await page.setViewportSize({width,height:900});await page.goto('http://127.0.0.1:8095/wix-test-credential-reissue');
 await page.locator('#preflight').filter({hasText:'inventory_hash'}).waitFor();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow '+width);
 assert(await page.locator('#entry').isHidden());
 assert.match(await page.locator('#preflight').innerText(),/wix-pilot-inactive@example.invalid/);
 if(width===1440){
  await page.locator('#reason').fill('Disposable browser fixture reissue - no providers');await page.locator('#approve').check();await page.locator('#plan').click();
  await page.locator('#entry').waitFor({state:'visible'});
  const inputs=page.locator('input[type="password"]');assert.equal(await inputs.count(),10);
  for(let i=0;i<5;i++){const value=crypto.randomBytes(24).toString('base64url');await inputs.nth(i*2).fill(value);await inputs.nth(i*2+1).fill(value);}
  await page.locator('#execute-confirm').check();await page.locator('#execute').click();
  await page.locator('#preflight').filter({hasText:'"state": "Completed"'}).waitFor();
  assert(await page.locator('#entry').isHidden());
  assert(await page.evaluate(()=>Array.from(document.querySelectorAll('input[type="password"]')).every(x=>!x.value)));
 }
}
await context.addCookies([{name:'crownops_session',value:'sales-token',url:'http://127.0.0.1:8095'}]);
const denied=await page.goto('http://127.0.0.1:8095/wix-test-credential-reissue');assert.equal(denied.status(),403);
assert.equal(external,0);assert.deepEqual(errors,[]);await browser.close();
console.log('PASS disposable synthetic credential reissue: four widths, real auth/CSRF, owner-only one-use operation, cleared private inputs, no external requests.');
})().catch(()=>{console.error('FAIL credential reissue browser verification; credential inputs omitted.');process.exit(1);});
