'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext(),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await context.addCookies([{name:'crownops_session',value:'sales-token',url:'http://127.0.0.1:8095'}]);
 let state='Not prepared',sends=0;
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
 }
 assert.equal(sends,4);assert.deepEqual(errors,[]);await browser.close();
 console.log('PASS: mocked QuickBooks preview, explicit send, acceptance and schedule handoff at four widths. No real provider or email.');
})().catch(error=>{console.error(error);process.exit(1);});
