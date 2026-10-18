'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const base='http://127.0.0.1:8095';await context.addCookies([{name:'crownops_session',value:'owner-token',url:base}]);
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/security',r=>r.fulfill({json:{mfa_enabled:true,encryption_configured:true}}));
 await page.route('**/api/connections',async route=>{
   const response=await route.fetch(),data=await response.json();
   data.gmail.authorized_at='2026-10-18';data.quickbooks.authorized_at='2026-10-18';
   data.connection_health={gmail:{state:'connected',message:'Connection renews automatically.'},quickbooks:{state:'retrying',message:'Temporary problem. Retrying automatically; saved connection retained.'}};
   await route.fulfill({json:data});
 });
 await page.goto(base);await page.locator('#content h1').waitFor();
 const go=async key=>{await page.getByRole('button',{name:'Open navigation',exact:true}).click();await page.locator('#sidebar [data-page="'+key+'"]').click();await page.waitForFunction(k=>document.querySelector('#content').dataset.workspacePage===k,key);};
 await go('connections');
 assert.equal(await page.getByRole('button',{name:'Reconnect Gmail',exact:true}).isVisible(),false);
 assert.ok((await page.locator('#content').innerText()).includes('Connected · automatic renewal'));
 assert.ok((await page.locator('#content').innerText()).includes('Retrying automatically'));
 for(const width of [390,768,1440]){await page.setViewportSize({width,height:844});assert.equal(await page.locator('body').evaluate(e=>e.scrollWidth<=innerWidth+1),true);}
 await page.setViewportSize({width:390,height:844});
 await go('account');await page.getByRole('button',{name:'Turn off authenticator',exact:true}).click();
 assert.equal(await page.getByLabel('Current password',{exact:true}).isVisible(),true);
 assert.equal(await page.getByLabel('Current six-digit code',{exact:true}).isVisible(),true);
 assert.ok((await page.getByRole('dialog').innerText()).includes('Your password will still be required'));
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({automatic_status:true,reconnect_tucked_away:true,transient_status:true,three_widths_no_overflow:true,authenticator_off_form:true,page_errors:errors}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
