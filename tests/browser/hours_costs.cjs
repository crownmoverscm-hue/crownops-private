'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const base='http://127.0.0.1:8095';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox']}),errors=[],checks=[];
 for(const role of ['crew','manager','owner','sales']){
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);const page=await context.newPage();page.on('pageerror',e=>errors.push(role+': '+e.message));
  await page.goto(base);await page.locator('#content h1').waitFor();
  const go=async key=>{if(await page.getByRole('button',{name:'Open navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();await page.locator('#sidebar [data-page="'+key+'"]').click();await page.waitForFunction(k=>document.querySelector('#content')?.dataset.workspacePage===k,key);};
  await go('schedule');await page.locator('#calendar-jump').fill('2026-11-15');
  await page.waitForFunction(()=>S.week?.getDay()===1);const days=await page.evaluate(()=>weekDays().map(dateIso));assert.equal(days[0],'2026-11-09');assert.equal(days[6],'2026-11-15');
  if(role==='crew'||role==='manager'){
   await page.evaluate(()=>openJob('J1'));await page.getByRole('dialog').waitFor();
   await page.locator('[data-final="team-hours"]').click();
   await page.getByLabel(role==='crew'?'My hours worked':'Hours worked by selected team',{exact:true}).fill('3');
   if(role==='crew')assert.equal(await page.locator('.simple-hours-row').count(),1);
   await page.getByRole('button',{name:role==='crew'?'Save my hours':'Save team hours',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
   await page.locator('#ux-pay-start').fill('2026-11-01');await page.locator('#ux-pay-end').fill('2026-11-30');await page.getByRole('button',{name:'Load payroll',exact:true}).click();
   await page.waitForFunction(()=>UX.payroll?.start==='2026-11-01');assert.ok((await page.locator('#content').innerText()).includes('3.00 h'));
   if(role==='manager'){
    page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Approve completed hours',exact:true}).click();
    await page.waitForFunction(()=>UX.payroll?.approved_hours===6);
    const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Export approved hours',exact:true}).click();const download=await downloadPromise;assert.ok(download.suggestedFilename().includes('approved-hours'));
    await page.screenshot({path:'/tmp/crownops-payroll-phone.png',fullPage:true});
   }
  }
  if(role==='owner'){
   await go('expenses');await page.getByRole('button',{name:'Job margins',exact:true}).click();
   await page.getByLabel('From',{exact:true}).fill('2026-11-01');await page.getByLabel('To',{exact:true}).fill('2026-11-30');await page.getByRole('button',{name:'Show margins',exact:true}).click();await page.getByText('No issued invoice',{exact:true}).first().waitFor();
   for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:844});assert.equal(await page.locator('body').evaluate(e=>e.scrollWidth<=innerWidth+1),true,'margin overflow '+width);}
   await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'QuickBooks review',exact:true}).click();await page.getByText('Fictional fuel supplier',{exact:true}).waitFor();
   await page.getByRole('button',{name:'Review / match',exact:true}).click();
   assert.equal(await page.locator('body').evaluate(e=>e.scrollWidth<=innerWidth+1),true,'review overflow');
   if(role==='owner'){
    await page.getByLabel('Category for a new / updated expense').selectOption('Fuel');await page.getByLabel('Related service for a new / updated expense').selectOption('J1');
    await page.getByRole('button',{name:'Approve expense review',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});await page.getByText('Imported',{exact:true}).waitFor();
   }else await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();
   await page.screenshot({path:'/tmp/crownops-quickbooks-review-phone.png',fullPage:true});
   await page.getByRole('button',{name:'Expense ledger',exact:true}).click();await page.locator('.expense-ledger-cards').getByText('Fictional fuel supplier',{exact:true}).waitFor();
  }else{
   assert.equal(await page.locator('#sidebar [data-page="expenses"]').count(),0);
   assert.equal((await page.request.get(base+'/api/expenses')).status(),403);
   assert.equal((await page.request.get(base+'/api/costs/margins')).status(),403);
   assert.equal((await page.request.get(base+'/api/costs/quickbooks')).status(),403);
  }
  checks.push(role+': Monday week, hours/cost permissions and workflows');await context.close();
 }
 assert.deepEqual(errors,[]);await browser.close();console.log(JSON.stringify({checks,page_errors:errors}));
})().catch(e=>{console.error(e);process.exit(1)});
