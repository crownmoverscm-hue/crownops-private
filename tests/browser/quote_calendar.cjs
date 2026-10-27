'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox']});
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
  // A successful corrected Calculate clears only this editor's old error.
  await p.unroute('**/api/crm/quotes/calculate');
  await p.route('**/api/crm/quotes/calculate',r=>r.fulfill({json:{low_cents:48599,high_cents:48599,service_low_cents:30000,service_high_cents:30000,transport_cents:15000,fuel_cents:3599,materials_cents:0,movers:2,trucks:1,hourly_rate_cents:10000}}));
  await p.route('**/api/crm/quote-setup?**',async route=>{const response=await route.fetch();const data=await response.json();data.suggested_hourly_rate=100;data.rate_source='TEST ONLY UI fixture';await route.fulfill({json:data});});
  await p.evaluate(()=>crmQuoteBuilder('FINISH-L0'));
  const correctedDate=p.locator('#crm-quote-form input[name=move_date]');
  await correctedDate.fill('');await p.getByRole('button',{name:'Calculate',exact:true}).click();
  await p.locator('#modal-error').filter({hasText:'Draft was not saved'}).waitFor();
  const restoredRate=p.waitForResponse(r=>r.url().includes('/api/crm/quote-setup?')&&r.url().includes('move_date=2026-11-15'));
  await correctedDate.fill('2026-11-15');await restoredRate;
  // Keep this a calculator/UI test: no quote-save route or provider is called.
  await p.locator('#crm-quote-form input[name=hourly_rate]').fill('100');
  await p.locator('#crm-quote-form input[name=fixed_transport]').fill('150');
  await p.getByRole('button',{name:'Calculate',exact:true}).click();
  await p.locator('#crm-quote-result .quote-price').waitFor();
  await p.locator('#modal-error').waitFor({state:'hidden'});
  assert.equal(await p.locator('#modal-error').textContent(),'','corrected calculation clears stale validation');
  assert.equal(await p.locator('#modal-error').isVisible(),false);
  assert.equal(await p.getByRole('dialog').isVisible(),true);
  // A later invalid attempt must still show an error after the successful clear.
  await correctedDate.fill('');await p.getByRole('button',{name:'Calculate',exact:true}).click();
  await p.locator('#modal-error').filter({hasText:'Draft was not saved'}).waitFor();
  await p.getByRole('button',{name:'Cancel',exact:true}).click();
  await p.getByRole('button',{name:'Discard changes',exact:true}).click();
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
  // Read-only sandbox send preflight renders linkage and separate gates.
  let preflightMutations=0, approvalCalls=0, sendCalls=0, approved=false;
  await p.evaluate(()=>{S.user.role="owner";S.user.is_master_owner=true;});
  await p.route('**/api/quotes/QUOTE-08dd48a364a34f66/booking-workflow',r=>r.fulfill({json:{available:false,workflow_enabled:false}}));
  await p.route('**/api/quotes/QUOTE-08dd48a364a34f66/quickbooks-delivery',r=>{
   if(r.request().method()!=='GET')preflightMutations++;
   return r.fulfill({json:{send_only_preflight:true,test_only:true,state:'Ready',company_name:'Sandbox Company CA 5634',country:'CA',environment:'sandbox',realm_id:'9341458310238189',external_id:'185',quote_id:'QUOTE-08dd48a364a34f66',revision:2,service_id:'TEST-SERVICE',customer_id:'10',revisions:[{id:'TEST-R1',revision:1},{id:'QUOTE-08dd48a364a34f66',revision:2}],subtotal_cents:48599,gst_cents:2430,qst_cents:4848,total_cents:55877,snapshot_hash:'TEST-SNAPSHOT',customer_email:'testonly.fictional@example.com',bill_email:'testonly.fictional@example.com',recipient:'jonathanprobonas15@gmail.com',send_to_warning:'Intuit sendTo can update Estimate.BillEmail.Address.',cc_bcc:{provider_defaults_verified_absent:false},sender:'Unknown — QuickBooks controls sender',reply_to:'Unknown — verify actual headers',message_preview_note:'Saved estimate only; exact email preview unavailable.',memo:'TEST ONLY',lines:[],operation:'POST existing Estimate 185 send',duplicate_protection:'Durable one-send slot; no automatic retry.',send_attempt:{},provider_status:'Pending',provider_email_status:'NotSet',review_hash:'TEST-REVIEW',approval_eligible:!approved,owner_approved:approved,can_send:approved,send_configured:true,approval:approved?{approved_name:'TEST Owner',approved_at:'2026-10-26T22:00:00Z',current:true,evidence_reference:'SIMULATED sandbox settings'}:{},blockers:approved?[]:['Separate owner approval required.']}});
  });
  await p.route('**/api/quotes/QUOTE-08dd48a364a34f66/sandbox-send-approval',r=>{
   approvalCalls++;const data=r.request().postDataJSON();
   assert.equal(data.estimate_id,'185');assert.equal(data.recipient,'jonathanprobonas15@gmail.com');
   assert.equal(data.review_hash,'TEST-REVIEW');assert.equal(data.no_cc_bcc_confirmed,true);
   assert.equal(data.ack_unknown_delivery,true);assert.equal(data.ack_bill_email_change,true);
   approved=true;return r.fulfill({json:{approved:true,email_sent:false}});
  });
  await p.route('**/api/quotes/QUOTE-08dd48a364a34f66/sandbox-send',r=>{sendCalls++;return r.fulfill({status:409,json:{detail:'SIMULATED safety block'}});});
  await p.evaluate(()=>openQuickBooksEstimate('QUOTE-08dd48a364a34f66'));
  await p.getByRole('heading',{name:'TEST ONLY · Estimate 185 send preflight',exact:true}).waitFor();
  assert.match(await p.locator('#modal').innerText(),/QuickBooks Customer ID/);
  assert.match(await p.locator('#modal').innerText(),/558.77/);
  assert.match(await p.locator('#modal').innerText(),/testonly.fictional@example.com/);
  assert.match(await p.locator('#modal').innerText(),/jonathanprobonas15@gmail.com/);
  assert.equal(await p.getByRole('button',{name:'Send disabled — owner approval required',exact:true}).isEnabled(),false);
  assert(await p.locator('#modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'preflight modal overflow '+width);
  assert.equal(preflightMutations,0);
  const approve=p.getByRole('button',{name:'Approve one test email — do not send',exact:true});
  await approve.click();assert.equal(approvalCalls,0,'unchecked approval stays local');
  await p.locator('#qbo-approval-reference').fill('SIMULATED sandbox settings review');
  for(const id of ['qbo-approval-copies','qbo-approval-bill','qbo-approval-unknown'])await p.locator('#'+id).check();
  await approve.click();
  const send=p.getByRole('button',{name:'Send ONE QuickBooks test email',exact:true});await send.waitFor();
  assert.equal(await send.isEnabled(),true);assert.equal(approvalCalls,1);assert.equal(sendCalls,0,'approval never sends');
  assert.match(await p.locator('#modal').innerText(),/Approval recorded/);
  assert(await p.locator('#modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'approved preflight overflow '+width);
  p.once('dialog',d=>d.dismiss());await send.click();assert.equal(sendCalls,0,'cancelled send never submits');
  await p.evaluate(()=>{S.user.is_master_owner=false;});
  await p.evaluate(()=>openQuickBooksEstimate('QUOTE-08dd48a364a34f66'));
  assert.equal(await p.getByRole('button',{name:'Send disabled — Master owner required',exact:true}).isEnabled(),false);
  assert.equal(await p.getByRole('button',{name:'Approve one test email — do not send',exact:true}).count(),0);
  await p.getByRole('button',{name:'Close',exact:true}).last().click();
  await ctx.close();console.log(JSON.stringify({width,missingCalendar:'PASS',timeout:'PASS',dismissal:'PASS',validationRecovery:'PASS',sendPreflight:'PASS'}));
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
