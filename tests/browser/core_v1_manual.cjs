/* Prepared focused browser qualification. Requires an already-approved Playwright+Chromium.
   Start the fictional fixture separately: PYTHONPATH=. python tests/browser/core_v1_fixture.py
   Then: node tests/browser/core_v1_manual.cjs
   No browser/runtime downloads are performed by this script. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8097';
(async()=>{
 const browser=await chromium.launch({headless:true});let external=0;let cases=0;
 try{
  for(const locale of ['en-CA','fr-CA'])for(const viewport of [{width:1280,height:900},{width:390,height:844}]){
   const ctx=await browser.newContext({locale,viewport});
   await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base){external++;return route.abort();}return route.continue();});
   const page=await ctx.newPage();await page.goto(base);
   await page.locator('#login-form [name=email]').fill('sales@example.invalid');
   await page.locator('#login-form [name=password]').fill('TEST-only-safe-password-138!');
   await page.locator('#login-form button').click();await page.locator('#workspace').waitFor({state:'visible'});
   const key=locale+'-'+viewport.width;
   const fill=async(subject)=>{for(const [name,value]of Object.entries({client_name:'TEST Browser '+key,email:'browser-'+key+'@example.invalid',phone:'514555'+String(100+cases).padStart(4,'0'),subject,body:'  TEST original / é\n\n',pickup:'TEST A',dropoff:'TEST B'}))await page.locator('#inquiry-form [name='+name+']').fill(value);};
   await fill('TEST first');await page.locator('#inquiry-form button').click();await page.locator('#inquiry-edit').waitFor();
   const original=await page.locator('#detail details pre').textContent();assert(original.includes('  TEST original / é\n\n'));
   await page.locator('#detail details summary').click();assert(await page.locator('#detail details pre').isVisible());
   await page.locator('#inquiry-edit [name=stage]').selectOption('In Progress');
   await page.locator('#inquiry-edit [name=reason]').fill('TEST browser staff review');
   await page.locator('#inquiry-edit button').click();await page.getByRole('status').filter({hasText:'Changes saved'}).waitFor();
   // Save current UI version, then make a winning authenticated change through the same session.
   await page.evaluate(async()=>{const me=await(await fetch('/api/me')).json();const list=await(await fetch('/api/core-v1/inquiries')).json();const detail=await(await fetch('/api/core-v1/inquiries/'+list[0].id)).json();const r=await fetch('/api/core-v1/inquiries/'+detail.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':me.csrf},body:JSON.stringify({expected_version:detail.version,pickup:'TEST winner'})});if(r.status!==200)throw Error('Fixture conflict setup failed');});
   await page.locator('#inquiry-edit [name=pickup]').fill('TEST stale UI');await page.locator('#inquiry-edit button').click();
   await page.getByRole('status').filter({hasText:'Refresh before retrying'}).waitFor();
   assert.equal(await page.locator('#inquiry-edit [name=pickup]').inputValue(),'TEST stale UI');
   await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#inquiry-edit [name=pickup]')?.value==='TEST winner');
   await page.locator('#detail textarea[name=body]').fill('TEST internal browser note');await page.locator('#detail form').filter({has:page.locator('textarea[name=body]')}).locator('button').click();
   await page.getByRole('status').filter({hasText:'Internal note saved'}).waitFor();
   await page.locator('#detail input[name=title]').fill('TEST explicit browser follow-up');await page.locator('#detail form').filter({has:page.locator('input[name=title]')}).locator('button').click();
   await page.getByRole('status').filter({hasText:'Follow-up added'}).waitFor();
   // Existing-client selection must ask staff rather than merging silently.
   await fill('TEST second');await page.locator('#inquiry-form button').click();await page.getByRole('status').filter({hasText:'Possible existing client found'}).waitFor();
   await page.evaluate(k=>{window.__coreBrowserKey=k},key);const cid=await page.evaluate(async()=>{const x=await(await fetch('/api/core-v1/clients')).json();return x.find(c=>c.client_name==='TEST Browser '+window.__coreBrowserKey).id;});
   await page.locator('#inquiry-form [name=client_id]').fill(cid);await page.locator('#inquiry-form button').click();await page.getByRole('status').filter({hasText:'Inquiry recorded'}).waitFor();
   assert(await page.locator('#detail details pre').textContent());
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.locator('#logout').click();await page.locator('#login-form').waitFor({state:'visible'});
   await page.locator('#login-form [name=email]').fill('crew@example.invalid');await page.locator('#login-form [name=password]').fill('TEST-only-safe-password-138!');await page.locator('#login-form button').click();
   await page.getByRole('status').filter({hasText:/permission|office|not permitted|access|does not allow/i}).waitFor();
   cases++;await ctx.close();
  }
  assert.equal(external,0);console.log(JSON.stringify({browser:'PASS',viewportLocaleCases:cases,externalAttempts:external}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
