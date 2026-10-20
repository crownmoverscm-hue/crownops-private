'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
let debugPage;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const base='http://127.0.0.1:8095',errors=[];
 for(const role of ['owner','manager','crew','sales']){
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);const page=await context.newPage();debugPage=page;page.on('pageerror',e=>errors.push(role+': '+e.message));
  await page.goto(base);await page.locator('#content h1').waitFor();
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  // Sales may not have Payroll in navigation; permission checks remain server-side.
  if(role==='sales'){
   const denied=await page.evaluate(async()=>{const c=await api('/payroll-periods');const r=await fetch('/api/payroll-periods',{method:'POST',headers:{'Content-Type':'application/json','x-csrf-token':'test-csrf'},body:JSON.stringify({period_days:7,period_anchor:'2026-11-09',review_token:c.review_token})});return {can:c.can_configure,status:r.status};});assert.deepEqual(denied,{can:false,status:403});await context.close();continue;
  }
  await page.locator('#sidebar [data-page="payroll"]').click();await page.getByRole('heading',{name:'Payroll & hours',exact:true}).waitFor();
  if(role==='owner'||role==='manager'){
   await page.getByRole('button',{name:role==='owner'?'Set up payroll period':'Payroll period settings',exact:true}).click();
   await page.getByLabel('How often do you run payroll?',{exact:true}).selectOption(role==='owner'?'7':'14');
   await page.getByLabel('First day of one payroll period',{exact:true}).fill(role==='owner'?'2026-11-09':'2026-11-11');
   await page.locator('#payroll-period-preview').getByText(role==='owner'?/Monday through Sunday/:/Wednesday through Tuesday/).waitFor();
   if(process.env.CROWNOPS_SCREENSHOT_DIR){fs.mkdirSync(process.env.CROWNOPS_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:process.env.CROWNOPS_SCREENSHOT_DIR+'/payroll-setup-'+role+'.png',fullPage:true});}
   await page.getByRole('button',{name:'Save payroll schedule',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
   await page.waitForFunction(days=>UX.payCalendar?.configured&&UX.payCalendar.period_days===days,role==='owner'?7:14);
   const c=await page.evaluate(()=>UX.payCalendar);assert.equal(await page.locator('#ux-pay-start').inputValue(),c.current.start);assert.equal(await page.locator('#ux-pay-end').inputValue(),c.current.end);
   await page.getByRole('button',{name:'Previous period',exact:true}).click();await page.waitForFunction(day=>UX.payroll?.start===day,c.previous.start);
   await page.getByRole('button',{name:'Next period',exact:true}).click();await page.waitForFunction(day=>UX.payroll?.start===day,c.current.start);
   await page.getByRole('button',{name:'Current period',exact:true}).click();
   if(role==='owner'){
    const r=await page.evaluate(()=>api('/ux/jobs/J1/crew-hours',{method:'POST',data:{employee_ids:['E1','E2'],team_hours:3,note:'Fictional payroll calendar QA'}}));assert.ok(r);
   }
   await page.locator('#ux-pay-start').fill('2026-11-01');await page.locator('#ux-pay-end').fill('2026-11-30');await page.getByRole('button',{name:'Load payroll',exact:true}).click();
   await page.waitForFunction(()=>UX.payroll?.start==='2026-11-01'&&UX.payroll?.end==='2026-11-30');await page.getByText('Custom date range',{exact:true}).waitFor();
   if(role==='owner'){page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Approve completed hours',exact:true}).click();await page.waitForFunction(()=>UX.payroll?.approved_hours===6);}
   assert.equal(await page.evaluate(()=>UX.payroll.approved_hours),6);
   const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Export approved hours',exact:true}).click();assert.ok((await downloaded).suggestedFilename().includes('2026-11-01-2026-11-30'));
   await page.reload();await page.locator('#content h1').waitFor();
   if(await page.locator('#sidebar [data-page="payroll"]').isVisible())await page.locator('#sidebar [data-page="payroll"]').click();else{await page.getByRole('button',{name:'Open navigation',exact:true}).click();await page.locator('#sidebar [data-page="payroll"]').click();}
   await page.getByRole('button',{name:'Payroll period settings',exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>UX.payCalendar.period_days),role==='owner'?7:14);
  }else{
   await page.locator('.payroll-calendar').waitFor();assert.equal(await page.getByRole('button',{name:'Payroll period settings',exact:true}).count(),0);
   assert.equal(await page.getByRole('button',{name:'Export approved hours',exact:true}).count(),0);
   assert.equal(await page.evaluate(()=>UX.payCalendar.period_days),14);
  }
  for(const width of [390,768,1440]){
   await page.setViewportSize({width,height:900});assert.equal(await page.locator('body').evaluate(x=>x.scrollWidth<=innerWidth+1),true,role+' '+width+' overflow');
   if(process.env.CROWNOPS_SCREENSHOT_DIR&&role==='manager')await page.screenshot({path:process.env.CROWNOPS_SCREENSHOT_DIR+'/payroll-'+width+'.png',fullPage:true});
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);await browser.close();console.log(JSON.stringify({owner_manager_settings:true,weekly_biweekly:true,custom_export:true,approvals_preserved:true,crew_sales_restricted:true,widths:[390,768,1440],page_errors:errors}));
})().catch(async e=>{console.error(e);if(debugPage)console.error(await debugPage.evaluate(()=>({toast:document.getElementById('toast')?.textContent,error:document.getElementById('modal-error')?.textContent})).catch(()=>null));process.exit(1)});
