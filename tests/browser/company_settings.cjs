'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[],writes=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.addCookies([{name:'crownops_session',value:'owner-token',url:'http://127.0.0.1:8095'}]);
  await page.route('**/*',route=>{const r=route.request();if(!r.url().startsWith('http://127.0.0.1:8095')){external.push(r.url());return route.abort();}if(r.method()!=='GET'){writes.push(r.method()+' '+r.url());return route.abort();}return route.continue();});
  await page.goto('http://127.0.0.1:8095');await page.locator('#content h1').waitFor();
  await page.evaluate(()=>navigate('settings'));await page.locator('[data-page-target="settings-legacy"]').click();
  await page.getByRole('heading',{name:'Settings & imports',exact:true}).waitFor();
  await page.getByText('LEGACY-MALFORMED-WIX-TEST: Expected a seasonal rate list;', {exact:false}).waitFor();
  assert(await page.getByRole('heading',{name:'Team access',exact:true}).isVisible());
  assert(await page.getByRole('heading',{name:'Fleet verification',exact:true}).isVisible());
  assert.equal(await page.locator('[data-action="approve-pricing"][data-id="LEGACY-MALFORMED-WIX-TEST"]').count(),0);
  await page.evaluate(()=>navigate('settings'));await page.locator('[data-page-target="rates"]').click();
  await page.getByRole('heading',{name:'Pricing & rates',exact:true}).waitFor();
  assert(await page.getByRole('heading',{name:'Annual hourly rates',exact:true}).isVisible());
  assert(await page.getByRole('heading',{name:'Exact-day overrides',exact:true}).isVisible());
  await page.evaluate(()=>navigate('launch'));await page.getByRole('heading',{name:'Hosting & recovery',exact:true}).waitFor();
  const status=await page.evaluate(()=>api('/hosting-status'));assert.equal(status.mode,'test');assert.equal(status.backup.available,false);
  assert(await page.getByText('No verified portable recovery copy is available yet.',{exact:true}).isVisible());
  const rates=await page.evaluate(()=>api('/pricing'));assert.deepEqual(rates.find(x=>x.id==='LEGACY-MALFORMED-WIX-TEST').rates,{});
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);assert.deepEqual(external,[]);await context.close();
 }
 await browser.close();console.log('PASS Company setup: legacy malformed rates visible, Security & imports and Pricing & rates open, Hosting & recovery read-only, 390/1440 widths, zero mutations/external requests.');
})().catch(e=>{console.error(e);process.exit(1);});
