'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
let debugPage;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const base='http://127.0.0.1:8095',errors=[];
 for(const [index,role] of ['owner','manager','sales'].entries()){
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  const page=await context.newPage();debugPage=page;page.on('pageerror',e=>{errors.push(role+': '+e.message);console.error('Page error:',e.message);});page.on('response',async r=>{if(r.url().includes('/api/inbox')&&r.status()>=400)console.error('Inbox error:',r.status(),await r.text());});
  await page.goto(base);await page.locator('#content h1').waitFor();
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  assert.equal(await page.locator('#sidebar [data-page="followups"]').count(),0);
  await page.locator('#sidebar [data-page="communications"]').click();
  await page.getByRole('heading',{name:'Inbox',exact:true}).waitFor();
  await page.locator('[data-inbox-open="FINISH-L'+index+'"]').first().click();
  await page.locator('.inbox-detail-head').waitFor();
  // An owner/manager can already write before claiming; wait for the new
  // handler display, not for the form that was present before the request.
  let releaseClaim;const claimGate=new Promise(resolve=>{releaseClaim=resolve;});
  await page.route('**/api/inbox/*/claim',async route=>{await claimGate;await route.continue();});
  await page.getByRole('button',{name:'Claim conversation',exact:true}).click();
  try{if(role!=='sales')assert.equal(await page.locator('#inbox-compose [name="body"]').isDisabled(),true);}finally{releaseClaim();}
  await page.locator('.inbox-ownership').getByText(/^Handled by /).waitFor();
  await page.locator('#inbox-compose').waitFor();
  assert.ok((await page.locator('.inbox-detail').innerText()).includes('Service owner:'));
  for(const width of [390,768,1440]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.locator('body').evaluate(x=>x.scrollWidth<=innerWidth+1),true,role+' '+width+' overflow');
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('#inbox-compose-kind').selectOption('call');
  await page.locator('#inbox-compose [name="body"]').fill('Fictional call: elevator confirmed.');
  await page.getByRole('button',{name:'Save call outcome',exact:true}).click();
  await page.locator('.inbox-history').getByText('Fictional call: elevator confirmed.',{exact:true}).waitFor();
  await page.locator('#inbox-compose-kind').selectOption('note');
  await page.locator('#inbox-compose [name="body"]').fill('Fictional private staff note.');
  await page.getByRole('button',{name:'Save internal note',exact:true}).click();
  await page.locator('.inbox-history').getByText('Fictional private staff note.',{exact:true}).waitFor();
  if(role==='owner'&&process.env.CROWNOPS_SCREENSHOT_DIR){require('node:fs').mkdirSync(process.env.CROWNOPS_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:process.env.CROWNOPS_SCREENSHOT_DIR+'/inbox-phone.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:process.env.CROWNOPS_SCREENSHOT_DIR+'/inbox-desktop.png',fullPage:true});await page.setViewportSize({width:390,height:844});}
  await page.locator('#inbox-compose [name="subject"]').fill('Fictional unsent reply');
  await page.locator('#inbox-compose [name="body"]').fill('Fictional message — never sent.');
  await page.getByRole('button',{name:'Review email',exact:true}).click();
  await page.getByRole('heading',{name:'Review email',exact:true}).waitFor();
  await page.locator('#inbox-edit-mail [name="body"]').fill('Edited fictional draft — never sent.');
  assert.equal(await page.locator('[data-inbox-send]').isDisabled(),true);
  await page.getByRole('button',{name:'Save changes',exact:true}).click();
  await page.getByRole('button',{name:'Send email',exact:true}).waitFor(); // Never send test mail.
  assert.equal(await page.locator('#inbox-edit-mail [name="body"]').inputValue(),'Edited fictional draft — never sent.');
  await page.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.getByRole('button',{name:'Waiting on client',exact:true}).click();
  await page.getByRole('button',{name:'Save status',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  assert.ok((await page.locator('.inbox-ownership').innerText()).includes('Waiting on client'));
  await page.getByRole('button',{name:'Hand over',exact:true}).click();
  await page.locator('#inbox-handoff [name="handler_id"]').selectOption(role==='sales'?'manager':'sales');
  await page.locator('#inbox-handoff [name="note"]').fill('Fictional handoff: call client after lunch.');
  await page.locator('button[form="inbox-handoff"]').click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  for(const tab of ['calls','tasks','drafts','sent','conversations']){
   await page.locator('[data-inbox-tab="'+tab+'"]').click();
   await page.locator('[data-inbox-tab="'+tab+'"].selected').waitFor();
   assert.equal(await page.locator('body').evaluate(x=>x.scrollWidth<=innerWidth+1),true,tab+' overflow');
  }
  if(role==='owner'){
   // Use a real scoped invitation against this disposable installation.
   const invite=await page.evaluate(async()=>{const r=await api('/leads/FINISH-L0/portal-invite',{method:'POST',data:{}});return r.url;});
   const portalContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
   const portal=await portalContext.newPage();portal.on('pageerror',e=>errors.push('portal: '+e.message));
   await portal.goto(invite);await portal.locator('.portal-tabs [data-tab="help"]').click();
   await portal.locator('#portal-thread .inbox-event').first().waitFor();
   assert.equal((await portal.locator('#portal-thread').innerText()).includes('Fictional private staff note.'),false);
   await portal.locator('#portal-thread-form [name="body"]').fill('Fictional client: I uploaded my room photos.');
   await portal.locator('#portal-thread-form button[type="submit"]').click();
   await portal.locator('#portal-thread').getByText('Fictional client: I uploaded my room photos.',{exact:true}).waitFor();
   if(process.env.CROWNOPS_SCREENSHOT_DIR)await portal.screenshot({path:process.env.CROWNOPS_SCREENSHOT_DIR+'/portal-phone.png',fullPage:true});
   for(const width of [390,768,1440]){await portal.setViewportSize({width,height:900});assert.equal(await portal.locator('body').evaluate(x=>x.scrollWidth<=innerWidth+1),true,'portal '+width+' overflow');}
   await page.locator('[data-inbox-open="FINISH-L0"]').first().click();
   await page.locator('.inbox-history').getByText('Fictional client: I uploaded my room photos.',{exact:true}).waitFor();
   assert.ok((await page.locator('.inbox-ownership').innerText()).includes('Needs reply'));
   await portalContext.close();
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({staff_roles:3,client_portal:true,widths:[390,768,1440],claim_handoff:true,call_notes:true,draft_edit:true,waiting_reopened:true,emails_sent:0,page_errors:errors}));
 await browser.close();
})().catch(async e=>{console.error(e);if(debugPage)console.error(await debugPage.evaluate(()=>({toast:document.getElementById('toast')?.textContent,error:document.getElementById('modal-error')?.textContent,form:document.getElementById('inbox-compose')?.outerHTML})));process.exit(1)});
