const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});let checked=0;
 for(const width of [320,390,768,1440]){
  const context=await browser.newContext({viewport:{width,height:900}});await context.addCookies([{name:'crownops_session',value:'owner-token',url:'http://127.0.0.1:8095'}]);
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:8095');await page.locator('#team-chat-open').waitFor();await page.locator('#team-chat-open').click();await page.locator('#team-chat[open]').waitFor();
  await page.locator('#tc-new').click();await page.locator('#tc-people input[value="sales"]').check();await page.locator('#tc-start').click();await page.locator('#tc-create').waitFor({state:'hidden'});
  await page.locator('#tc-compose textarea').fill('TEST private chat '+width);await page.locator('#tc-compose button').click();await page.getByText('TEST private chat '+width,{exact:true}).waitFor();
  await page.locator('#team-chat').getByRole('button',{name:'Clients',exact:true}).click();assert(await page.getByText('Client messaging remains',{exact:false}).isVisible());await page.locator('#team-chat').getByRole('button',{name:'AI Approvals',exact:true}).click();assert(await page.getByText('AI booking actions are not enabled.',{exact:false}).isVisible());
  assert(await page.locator('#team-chat').evaluate(e=>e.scrollWidth<=e.clientWidth+1));await page.getByRole('button',{name:'Close chat',exact:true}).click();assert.equal(errors.length,0,errors.join('\n'));checked++;await context.close();
 }
 for(const role of ['crew','sales','manager']){const c=await browser.newContext();await c.addCookies([{name:'crownops_session',value:role+'-token',url:'http://127.0.0.1:8095'}]);const p=await c.newPage();await p.goto('http://127.0.0.1:8095');await p.locator('.topbar').waitFor();const access=await c.request.get('http://127.0.0.1:8095/api/team-chat/access');assert.equal((await access.json()).enabled,false);assert.equal(await p.locator('#team-chat-open').count(),0);assert.equal((await c.request.get('http://127.0.0.1:8095/api/team-chat/threads')).status(),403);await c.close();}
 await browser.close();console.log('PASS: '+checked+' owner viewport flows, mobile containment, private DM send, disabled client/AI tabs; three non-owner pilot denials; no page errors.');
})().catch(e=>{console.error(e);process.exit(1)});
