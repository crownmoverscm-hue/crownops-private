'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const base='http://127.0.0.1:8095',errors=[];
 const context=await browser.newContext();const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const release=await (await page.request.get(base+'/api/app-release')).json();
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:900});
  await page.goto(base);await page.locator('#login-form').waitFor();
  assert.match(await page.locator('.login-form .app-release-details').innerText(),new RegExp(release.revision));
  assert.match(await page.locator('.login-form .app-release-details').innerText(),new RegExp(release.version.replaceAll('.','\\.')));
  assert.equal(await page.getByText('PRIVATE TEAM WORKSPACE',{exact:true}).count(),1);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'login overflow '+width);
  let r=await page.goto(base+'/quote?token=PRIVATE_TOKEN');assert.equal(r.status(),404);
  await page.getByRole('heading',{name:'Let’s get you to the right place'}).waitFor();
  assert.equal(await page.getByRole('link',{name:'Call Crown Movers'}).getAttribute('href'),'tel:+15146064030');
  assert(!(await page.locator('body').innerText()).includes('PRIVATE_TOKEN'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'error overflow '+width);
  await page.getByRole('link',{name:'Français'}).click();await page.getByRole('heading',{name:'Retrouvons votre lien'}).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'French error overflow '+width);
  if(width===390)await page.screenshot({path:'/tmp/crownops-recovery-mobile.png',fullPage:true});
 }
 for(const role of ['owner','manager','sales','crew']){
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  await page.goto(base);await page.locator('#content h1').waitFor();
  assert.match(await page.locator('.sidebar-foot .app-release-details').innerText(),new RegExp(release.revision));
  await page.evaluate(()=>navigate('help'));await page.locator('#content .app-release-details').waitFor();
  assert.match(await page.locator('#content .app-release-details').innerText(),new RegExp(release.revision));
 }
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: EN/FR recovery, version on sign-in/sidebar/help, four widths and four staff roles; no page errors.');
})().catch(e=>{console.error(e);process.exit(1);});
