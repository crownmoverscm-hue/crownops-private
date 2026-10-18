'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const base='http://127.0.0.1:8095',errors=[],checks=[];
 for(const role of ['owner','manager','sales','crew']){
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(role+': '+e.message));
  await page.goto(base);await page.locator('#content h1').waitFor();
  const go=async key=>{
   if(await page.getByRole('button',{name:'Open navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
   await page.locator('#sidebar [data-page="'+key+'"]').click();await page.waitForFunction(k=>document.querySelector('#content')?.dataset.workspacePage===k,key);await page.locator('.doc-browser').waitFor();
  };
  await go('invoices');assert.ok((await page.locator('#content').innerText()).includes('Outstanding'));
  for(const width of [390,768,1440]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.locator('body').evaluate(e=>e.scrollWidth<=innerWidth+1),true,role+' '+width+' overflow');
   await page.locator('[data-doc-menu]').first().click();
   const menu=page.getByRole('menu');await menu.waitFor();
   if(role==='owner')await page.screenshot({path:'/tmp/crownops-invoice-menu-'+width+'.png',fullPage:false});
   const box=await menu.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width+1&&box.y>=0&&box.y+box.height<=901);
   assert.equal(await menu.getByRole('menuitem',{name:'Set due date',exact:true}).count(),['owner','manager'].includes(role)?1:0);
   await page.keyboard.press('ArrowDown');assert.equal(await page.locator(':focus').textContent(),'Edit draft');
   await page.keyboard.press('Escape');assert.equal(await menu.count(),0);assert.equal(await page.locator('[data-doc-menu]').first().getAttribute('aria-expanded'),'false');
  }
  await page.setViewportSize({width:390,height:844});
  if(role==='owner'){
   await page.locator('[data-doc-menu]').first().click();await page.getByRole('menuitem',{name:'Set due date',exact:true}).click();
   await page.getByLabel('Agreed payment due date').fill('2026-11-30');await page.getByRole('button',{name:'Save due date',exact:true}).click();
   await page.getByRole('dialog').waitFor({state:'hidden'});await page.locator('.doc-browser').waitFor();assert.ok((await page.locator('.doc-browser').innerText()).includes('Nov 30'));
  }
  await page.locator('[data-doc-menu]').first().click();await page.getByRole('menuitem',{name:'Preview / details',exact:true}).click();await page.getByRole('dialog').waitFor();
  assert.ok((await page.getByRole('dialog').innerText()).includes('TEST-WORKSPACE'));
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).last().click();
  if(role!=='crew'){
   await go('quotes');await page.waitForFunction(()=>document.querySelector('#content h1')?.textContent==='Estimates');assert.ok((await page.locator('#content').innerText()).toLowerCase().includes('client activity'));
   await page.getByLabel('Status',{exact:true}).selectOption('Expired');await page.getByText('No estimates in this view',{exact:true}).waitFor();
   await page.getByLabel('Status',{exact:true}).selectOption('all');await page.locator('[data-doc-menu]').first().waitFor();
   await page.locator('[data-doc-menu]').first().click();
   assert.equal(await page.getByRole('menuitem',{name:'Duplicate as new revision',exact:true}).count(),1);
   if(role==='owner')await page.screenshot({path:'/tmp/crownops-quote-menu-phone.png',fullPage:false});
   const popupPromise=page.waitForEvent('popup');await page.getByRole('menuitem',{name:'Print / save PDF',exact:true}).click();const popup=await popupPromise;await popup.waitForLoadState();
   assert.ok((await popup.locator('body').innerText()).includes('Moving estimate'));await popup.close();
   await page.getByLabel('Search estimates',{exact:true}).fill('no match exists');await page.getByText('No estimates in this view',{exact:true}).waitFor();
  }else{
   const res=await page.request.get(base+'/api/documents/quotes');assert.equal(res.status(),403);
  }
  checks.push(role+': menus, keyboard, responsive lists, role permissions');await context.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({checks,page_errors:errors}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
