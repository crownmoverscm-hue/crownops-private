'use strict';
const {chromium,request}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const base='http://127.0.0.1:8095',client=await request.newContext({baseURL:base});
 for(const [id,title] of [['calendar','Moving | 11/15/2026 - 9am'],['review','Moving | Date TBD']]){
  const r=await client.post('/api/webhooks/wix',{data:{data:{connectionKey:'fictional-browser-wix-key-only-123456',event:'quote_accepted',documentId:id,number:'TEST-'+id,title,customer:'Fictional Wix '+id,email:id+'@example.invalid',total:'1200',currency:'CAD'}}});assert.equal(r.status(),200,await r.text());
 }
 const errors=[];
 for(const role of ['owner','manager','sales']){
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(role+': '+e.message));
  await page.goto(base);await page.locator('#content h1').waitFor();
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await page.locator('#sidebar [data-page="connections"]').click();
  await page.getByRole('heading',{name:'Wix incoming',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Pause Wix intake',exact:true}).count(),role==='owner'?1:0);
  for(const width of [390,768,1440]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.locator('body').evaluate(x=>x.scrollWidth<=innerWidth+1),true,role+' '+width+' overflow');
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('article').filter({hasText:'Fictional Wix review'}).filter({hasText:'Needs review'}).getByRole('button',{name:'Open notice'}).click();
  await page.getByRole('heading',{name:'Confirm the service date',exact:true}).waitFor();
  assert.ok((await page.getByRole('dialog').innerText()).includes('not a verified receipt'));
  if(role==='owner'){
   await page.getByLabel('Service date',{exact:true}).fill('2026-11-16');
   await page.getByLabel('Start time (optional)',{exact:true}).fill('10:30');
   await page.getByRole('button',{name:'Add to schedule for planning',exact:true}).click();
   await page.getByRole('dialog').waitFor({state:'hidden'});
  }else await page.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.locator('article').filter({hasText:'Fictional Wix calendar'}).getByRole('button',{name:'Plan service'}).click();
  await page.getByRole('dialog').waitFor();assert.ok((await page.getByRole('dialog').innerText()).includes('Accepted in Wix'));
  await page.getByRole('button',{name:'Close dialog',exact:true}).click();
  await context.close();
  if(role==='owner'){
   // Keep a fresh unknown-date notice so other roles exercise the review form.
   await client.post('/api/webhooks/wix',{data:{data:{connectionKey:'fictional-browser-wix-key-only-123456',event:'quote_accepted',documentId:'review-'+role,number:'TEST-review-new',title:'Moving | Date TBD',customer:'Fictional Wix review new',email:'next@example.invalid'}}});
  }
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({roles:3,widths:[390,768,1440],date_resolution:true,source_details:true,page_errors:errors}));
 await client.dispose();await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
