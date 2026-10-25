'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext(),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await context.addCookies([{name:'crownops_session',value:'sales-token',url:'http://127.0.0.1:8095'}]);
 let state='Not prepared',sends=0;
 let bridgePrepared=false,bridgeReviewed=false,bridgeSent=false,bridgeSends=0;
 await page.route('**/api/quotes/TEST-BRIDGE/booking-workflow**',async route=>{
  const url=route.request().url();let result;
  if(url.endsWith('/prepare')){assert.equal(route.request().postDataJSON().confirm,true);bridgePrepared=true;result={prepared:true};}
  else if(url.endsWith('/preview'))result=preview();
  else if(url.endsWith('/review')){assert.equal(route.request().postDataJSON().review_hash,'fixture-hash');bridgeReviewed=true;result={sent:false};}
  else if(url.endsWith('/send-email')){assert.equal(route.request().postDataJSON().confirm,true);bridgeSent=true;bridgeSends++;result={sent:true};}
  else result={available:true,workflow_enabled:bridgePrepared,quickbooks_estimate:{id:bridgePrepared?'88':null},email:{status:bridgeSent?'Sent':bridgeReviewed?'Draft':'Not prepared'},acceptance:{client_status:'Approved',quickbooks_sync:'Not accepted'}};
  await route.fulfill({json:result});
 });
 await page.route('**/api/quotes/TEST-QBO/booking-workflow',route=>route.fulfill({json:{available:false,workflow_enabled:false}}));
 const preview=()=>({state,enabled:true,external_id:'88',recipient:'fictional@example.invalid',basis:'high',expiry:'2026-11-14',
  provider_status:state==='Accepted'?'Accepted':'Pending',provider_email_status:state==='Sent'?'EmailSent':'NotSet',
  subtotal_cents:63600,tax_cents:9524,total_cents:73124,taxes:[{name:'TPS',amount_cents:3180},{name:'TVQ',amount_cents:6344}],
  memo:'Hi TEST — MUSE, thank you for considering Crown Movers. Accept to request booking; the date and team remain to be confirmed.',
  lines:[{description:'2 movers / 1 truck\nEstimated service: 3–4 hours',quantity:4,unit_price:135,amount_cents:54000},
   {description:'Transportation fee',quantity:1,unit_price:35,amount_cents:3500},{description:'Packing boxes',quantity:3,unit_price:12,amount_cents:3600},
   {description:'TEST — MUSE booking terms. These are fictional. No provider is contacted.'}],review_hash:'fixture-hash'});
 await page.route('**/api/quotes/TEST-QBO/quickbooks-delivery**',async route=>{
  const url=route.request().url();let result;
  if(url.endsWith('/prepare')){assert.equal(route.request().postDataJSON().confirm,true);state='Ready';result={prepared:true};}
  else if(url.endsWith('/send')){assert.equal(route.request().postDataJSON().recipient,'fictional@example.invalid');state='Sent';sends++;result={sent:true};}
  else if(url.endsWith('/sync')){state='Accepted';result={state,job_id:'TEST-JOB',message:'Ready for scheduling; confirmation required.'};}
  else result=state==='Not prepared'?{state,enabled:true}:preview();
  await route.fulfill({json:result});
 });
 page.on('dialog',dialog=>dialog.accept());
 let invoiceState='Ready',invoiceSends=0,accountVerified=false;
 await page.route('**/api/invoices/TEST-QBO-INVOICE/quickbooks-email**',async route=>{
  if(route.request().url().endsWith('/send')){invoiceSends++;invoiceState='Sent';return route.fulfill({json:{sent:true}});}
  return route.fulfill({json:{state:invoiceState,can_send:invoiceState==='Ready',recipient:'fictional@example.invalid',total_cents:73124,balance_cents:43124,
   memo:'Thank you for choosing Crown Movers. Your deposit has been applied.',lines:[{description:'Actual moving service',amount_cents:54000}],
   message:'TEST — MUSE fixture. No email or payment requested.',review_hash:'invoice-fixture'}});
 });
 await page.route('**/api/connections',async route=>{
  const response=await route.fetch(),data=await response.json();
  data.quickbooks={...data.quickbooks,authorized_at:'2026-10-21',state:accountVerified?'Account verified':'Authorization saved — account not checked',
   company_name:accountVerified?'TEST — MUSE Company':'',realm_id:'123',country:accountVerified?'CA':'',environment:'sandbox',writes_enabled:true,mappings_complete:true,estimate_email_enabled:true,invoice_email_enabled:true};
  await route.fulfill({json:data});
 });
 await page.route('**/api/quickbooks/test-connection',route=>{accountVerified=true;return route.fulfill({json:{ok:true,company_name:'TEST — MUSE Company'}});});
 await page.goto('http://127.0.0.1:8095');await page.locator('#content h1').waitFor();
 for(const width of [320,390,768,1440]){
  state='Not prepared';await page.setViewportSize({width,height:900});
  await page.evaluate(()=>openQuickBooksEstimate('TEST-QBO'));
  await page.getByRole('button',{name:'Prepare for review',exact:true}).click();
  const send=page.getByRole('button',{name:'Send through QuickBooks',exact:true});await send.waitFor();
  assert.match(await page.locator('#modal').innerText(),/TPS/);assert.match(await page.locator('#modal').innerText(),/TVQ/);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'page overflow '+width);
  assert(await page.locator('#modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'modal overflow '+width);
  if(width===390)await page.screenshot({path:'/tmp/crownops-quickbooks-preview-mobile.png',fullPage:true});
  await send.click();await page.getByRole('heading',{name:'QuickBooks estimate · Sent',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Send through QuickBooks',exact:true}).count(),0);
  await page.getByRole('button',{name:'Check acceptance',exact:true}).click();
  await page.getByRole('heading',{name:'QuickBooks estimate · Accepted',exact:true}).waitFor();
  assert.equal(await page.locator('#modal').getByRole('button',{name:'Open schedule',exact:true}).count(),1);
  await page.getByRole('button',{name:'Close',exact:true}).last().click();
  invoiceState='Ready';await page.evaluate(()=>openQuickBooksInvoice('TEST-QBO-INVOICE'));
  await page.getByRole('button',{name:'Send through QuickBooks',exact:true}).waitFor();
  assert.match(await page.locator('#modal').innerText(),/Current balance/);
  assert(await page.locator('#modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'invoice modal overflow '+width);
  await page.getByRole('button',{name:'Send through QuickBooks',exact:true}).click();
  await page.getByRole('heading',{name:'QuickBooks invoice · Sent',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Send through QuickBooks',exact:true}).count(),0);
  await page.locator('#modal [data-action="close"]').first().click();
  accountVerified=false;await page.evaluate(()=>navigate('connections'));
  await page.getByRole('heading',{name:'QuickBooks — company connection',exact:true}).waitFor();
  await page.getByRole('button',{name:'Check QuickBooks connection',exact:true}).click();
  await page.getByText('Account verified',{exact:true}).waitFor();
  assert.equal(await page.getByRole('heading',{name:'QuickBooks',exact:true}).count(),0,'no competing QuickBooks status card');
  assert.equal(await page.getByRole('heading',{name:'QuickBooks — company connection',exact:true}).count(),1);
  assert.match(await page.locator('#content').innerText(),/Company ID/i);
  assert.match(await page.locator('#content').innerText(),/Country verified by API/i);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'connection page overflow '+width);
  assert.match(await page.locator('#content').innerText(),/Configured environment: sandbox/);
  bridgePrepared=false;bridgeReviewed=false;bridgeSent=false;
  await page.evaluate(()=>openQuickBooksEstimate('TEST-BRIDGE'));
  await page.getByRole('heading',{name:'CrownOps email + QuickBooks sandbox',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Send through QuickBooks',exact:true}).count(),0);
  await page.getByRole('button',{name:'Prepare sandbox estimate',exact:true}).click();
  await page.getByRole('button',{name:'Approve totals and prepare email draft',exact:true}).click();
  await page.getByRole('button',{name:'Send approved test email',exact:true}).click();
  await page.getByText('Email: Sent',{exact:false}).waitFor();
  assert(await page.locator('#modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'combined modal overflow '+width);
  await page.locator('#modal [data-action="close"]').first().click();
 }
 // Server policy metadata controls the editor; no provider call is made here.
 await page.route('**/api/crm/quote-setup?**',async route=>{
  const response=await route.fetch(),data=await response.json();
  data.local_pricing_policy={version:'2026-10-25'};
  data.defaults={...data.defaults,local_fuel_per_truck:35.99,afternoon_moving_minimum:1};
  await route.fulfill({json:data});
 });
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:900});
  await page.evaluate(()=>crmQuoteBuilder('FINISH-L0'));
  await page.locator('[name=small_afternoon_job]').check();
  assert.equal(await page.locator('[name=minimum_hours]').inputValue(),'1');
  assert.equal(await page.locator('[name=local_fuel_rate]').inputValue(),'35.99');
  await page.locator('[name=local_fuel_rate]').fill('94');
  assert.equal(await page.locator('[name=local_fuel_rate]').inputValue(),'94');
  assert.match(await page.locator('#modal').innerText(),/700 rue Hodge/);
  assert(await page.locator('#modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'local pricing overflow '+width);
  await page.getByRole('button',{name:'Cancel',exact:true}).last().click();
 }
 assert.equal(sends,4);assert.equal(invoiceSends,4);assert.equal(bridgeSends,4);assert.deepEqual(errors,[]);await browser.close();
 console.log('PASS: mocked quote send/acceptance, invoice email and company verification at four widths. No real provider, email or payment.');
})().catch(error=>{console.error(error);process.exit(1);});
