'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 for(const width of [320,390,768,1440]){
  const ctx=await browser.newContext({viewport:{width,height:900}});await ctx.addCookies([{name:'crownops_session',value:'owner-token',url:'http://127.0.0.1:8095'}]);const p=await ctx.newPage();
  await p.goto('http://127.0.0.1:8095');await p.locator('#content h1').waitFor();
  await p.route('**/api/crm/quotes/calculate',r=>r.fulfill({status:422,json:{detail:'No active pricing calendar. Draft was not saved.'}}));
  await p.route('**/api/crm/quotes',r=>r.fulfill({status:422,json:{detail:'No active pricing calendar. Draft was not saved.'}}));
  await p.evaluate(()=>crmQuoteBuilder('FINISH-L0'));
  await p.getByRole('button',{name:'Calculate',exact:true}).click();
  await p.locator('#modal-error').filter({hasText:'Draft was not saved'}).waitFor();
  await p.getByRole('button',{name:'Save draft',exact:true}).click();
  await p.locator('#modal-error').filter({hasText:'Draft was not saved'}).waitFor();
  assert.equal(await p.getByRole('button',{name:'Close dialog',exact:true}).isEnabled(),true);
  await p.getByRole('button',{name:'Close dialog',exact:true}).click();assert.equal(await p.getByRole('dialog').isVisible(),false);
  await p.evaluate(()=>crmQuoteBuilder('FINISH-L0'));
  // Exercise bounded transport and dismissal during a stalled save, no provider involved.
  await p.route('**/api/test-stalled-save',()=>{});
  const timeoutResult=await p.evaluate(async()=>{try{await api('/test-stalled-save',{method:'POST',data:{},timeoutMs:25});return 'unexpected';}catch(e){return e.message;}});
  assert.match(timeoutResult,/completion is unknown/);
  await p.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await p.getByRole('dialog').isVisible(),false);
  // Muse reproduction: a native required-field failure followed by restored input.
  // Exercise the unsaved-change confirmation explicitly; automation otherwise
  // dismisses native confirm dialogs and intentionally leaves the editor open.
  for(const close of ['Cancel','Close dialog','Escape']){
   await p.evaluate(()=>crmQuoteBuilder('FINISH-L0'));
   const date=p.locator('#crm-quote-form input[name=move_date]');
   const original=await date.inputValue();await date.fill('');
   await p.getByRole('button',{name:'Save draft',exact:true}).click();
   await p.locator('#modal-error').filter({hasText:'Draft was not saved'}).waitFor();
   await date.fill(original);
   const dismiss=()=>close==='Escape'?p.keyboard.press('Escape'):p.getByRole('button',{name:close,exact:true}).click();
   await dismiss();await p.getByRole('button',{name:'Keep editing',exact:true}).click();assert.equal(await p.getByRole('dialog').isVisible(),true,'Keep editing preserves the form');
   await dismiss();await p.getByRole('button',{name:'Discard changes',exact:true}).click();assert.equal(await p.getByRole('dialog').isVisible(),false,'Discard closes after validation recovery: '+close);
  }
  await ctx.close();console.log(JSON.stringify({width,missingCalendar:'PASS',timeout:'PASS',dismissal:'PASS'}));
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
