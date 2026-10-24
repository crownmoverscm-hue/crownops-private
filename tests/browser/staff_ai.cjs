const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [320,390,768,1440]){
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.addCookies([{name:'crownops_session',value:'owner-token',url:'http://127.0.0.1:8095'}]);
  const page=await context.newPage(),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8095');await page.locator('.topbar').waitFor();await page.locator('#content h1').waitFor();
  await page.evaluate(()=>navigate('connections'));
  const before=await (await context.request.get('http://127.0.0.1:8095/api/ai/status')).json();
  assert.equal(before.enabled,false);
  page.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push(r.url());});
  try{await page.getByRole('button',{name:'Open staff AI workspace',exact:true}).click({timeout:5000});}catch(e){throw Error('AI entry missing at width '+width+'; page errors='+JSON.stringify(errors)+'; content='+await page.locator('body').innerText());}
  const dialog=page.locator('#modal[open]');await dialog.getByRole('heading',{name:'Staff AI workspace',exact:true}).waitFor();
  assert(await dialog.getByText('AI generation is paused',{exact:true}).isVisible());
  assert(await dialog.getByRole('button',{name:'Analyze & draft',exact:true}).isDisabled());
  assert.equal(await dialog.getByRole('heading',{name:'Choose a quote template',exact:true}).count(),0);
  await dialog.locator('textarea[name=text]').fill('TEST ONLY fictional local move');
  assert.equal(await dialog.locator('textarea[name=text]').inputValue(),'TEST ONLY fictional local move');
  assert(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  assert.equal(writes.length,0,'Opening the workspace must perform no writes');
  const after=await (await context.request.get('http://127.0.0.1:8095/api/ai/status')).json();
  assert.equal(after.enabled,false);assert.equal(after.automatic_actions,false);
  // Close directly so unrelated unsaved-form confirmation does not obscure route isolation.
  await page.evaluate(()=>modal.close());await page.evaluate(()=>navigate('quotes'));
  await page.locator('[data-action="choose-quote"],[data-crm="quote-new"]').first().click();
  await page.locator('#modal[open]').getByRole('heading',{name:'Choose a quote template',exact:true}).waitFor();
  // Mocked UI-only coverage. This is not a live OpenAI quality or provenance test.
  await page.evaluate(()=>modal.close());
  await page.route('**/api/ai/status',route=>route.fulfill({json:{enabled:true,configured:true,model:'fictional-browser-model',provider_requests_allowed:true}}));
  const requests=[];
  await page.route('**/api/ai/analyze',async route=>{
   requests.push(route.request().postDataJSON());await new Promise(r=>setTimeout(r,120));
   await route.fulfill({json:{result:{summary:'Fictional summary',questions:['Preferred date?'],risk_flags:[],fields:{},draft_reply:'Fictional editable draft'},usage:{input_tokens:10,output_tokens:10},sent:false,applied:false}});
  });
  await page.evaluate(()=>aiCompose('FINISH-L0'));
  await page.locator('#ai-form textarea[name=text]').fill('TEST ONLY browser fixture');
  await page.locator('#ai-form input[name=consent_to_process]').check();
  await page.locator('#ai-form').evaluate(f=>{f.requestSubmit();f.requestSubmit();});
  await page.locator('#ai-draft-edit').waitFor();assert.equal(requests.length,1,'Busy form must prevent concurrent requests');
  await page.locator('#ai-draft-edit').fill('Edited reviewed draft');
  let saved;
  await page.route('**/api/ai/runs/*/draft',async route=>{saved=route.request().postDataJSON();await route.fulfill({json:{saved:true,sent:false}});});
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Save reviewed draft',exact:true}).click();
  await page.getByText('Saved to the selected client timeline. Not sent.',{exact:true}).waitFor();
  assert.equal(saved.body,'Edited reviewed draft');assert.equal(saved.authorize_save,true);assert.equal(saved.kind,'Draft reply');
  assert(await page.getByText('Not sent',{exact:true}).isVisible());
  assert(await page.locator('#modal').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  assert.equal(errors.length,0,errors.join('\n'));await context.close();
 }
 for(const role of ['owner','manager','sales','crew']){
  const c=await browser.newContext();await c.addCookies([{name:'crownops_session',value:role+'-token',url:'http://127.0.0.1:8095'}]);
  const me=await (await c.request.get('http://127.0.0.1:8095/api/me')).json();
  assert.equal((await c.request.get('http://127.0.0.1:8095/api/ai/status')).status(),role==='crew'?403:200);
  assert.equal((await c.request.post('http://127.0.0.1:8095/api/ai/analyze',{headers:{'X-CSRF-Token':me.csrf},data:{text:'TEST ONLY'}})).status(),role==='crew'?403:503);
  if(role!=='crew'){
   const p=await c.newPage();await p.goto('http://127.0.0.1:8095');await p.locator('.topbar').waitFor();
   await p.evaluate(()=>aiCompose());await p.getByRole('heading',{name:'Staff AI workspace',exact:true}).waitFor();
  }
  await c.close();
 }
 const anonymous=await browser.newContext();assert.equal((await anonymous.request.get('http://127.0.0.1:8095/api/ai/status')).status(),401);await anonymous.close();
 await browser.close();console.log('PASS: four viewport AI routes; paused generation; zero opening writes; quote-template isolation; mocked edit/save and repeated-submit UI; owner/manager/sales access; crew and anonymous denials.');
})().catch(e=>{console.error(e);process.exit(1)});
