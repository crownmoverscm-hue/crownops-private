'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const base='http://127.0.0.1:8095',errors=[];
 const truck=(id,name,order)=>({id,name,display_order:order,active:true,color_hex:'#C65A12',profile:{},warnings:[]});
 const trucks=[truck('Z','Zulu',1),truck('T10','Truck 10',2),truck('B','Bravo',3),truck('T2','Truck 2',4),truck('A','alpha',5)];
 const job=(id,date,ts,assigned=true,hours=2)=>({id,title:'TEST '+id,phase:'Moving',status:'Tentative',start_at:date+'T13:00:00Z',end_at:date+'T15:00:00Z',client:{client_name:'TEST '+id},crew:[{id:'E1',name:'TEST Mover'}],trucks:ts,quote:{movers:1,service_low:hours,service_high:hours},readiness:{missing:[]},assigned_to_me:assigned});
 const jobs=[job('ZULU','2026-11-15',[trucks[0]],false,9),job('ALPHA','2026-11-15',[trucks[4]]),job('UNASSIGNED','2026-11-15',[],false),job('MONDAY','2026-11-16',[trucks[2]])];
 const sources=['2026-11-15','2026-11-16','2026-11-30'].map((date,i)=>({id:'SOURCE'+i,date,client_name:'TEST SOURCE '+date,service:'Moving',source_type:'wix',time_label:'9:00 AM',crew_size:2,crew_raw:'TEST crew'}));
 for(const width of [320,390,768,1440])for(const role of ['owner','manager','sales','crew']){
  const context=await browser.newContext({viewport:{width,height:844}});
  await context.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(width+'/'+role+': '+e.message));
  await page.route('**/api/ux/final/schedule?*',route=>{
   const u=new URL(route.request().url()),start=u.searchParams.get('start'),end=u.searchParams.get('end'),mine=u.searchParams.get('scope')==='mine';
   return route.fulfill({json:jobs.filter(j=>j.start_at.slice(0,10)>=start&&j.start_at.slice(0,10)<=end&&(!mine||j.assigned_to_me))});
  });
  await page.route('**/api/vehicles',r=>r.fulfill({json:trucks}));
  await page.route('**/api/fleet',r=>r.fulfill({json:trucks}));
  await page.route('**/api/company/fleet',r=>r.fulfill({json:{editable:true,trucks:trucks.map(t=>({...t,reference:{},maintenance:[],documents:[],history:[]}))}}));
  await page.route('**/api/time-off',r=>r.fulfill({json:[]}));
  await page.route('**/api/schedule-identity',r=>r.fulfill({json:{id:'E1',matched:true}}));
  await page.route('**/api/schedule/availability?*',r=>{
   const u=new URL(r.request().url());return r.fulfill({json:{start:u.searchParams.get('start'),end:u.searchParams.get('end'),can_manage:role!=='crew',items:[{day:'2026-11-15',status:'Fully booked',version:1}]}});
  });
  await page.route('**/api/schedule-imports/events',r=>r.fulfill({json:sources}));
  await page.goto(base);await page.locator('#content h1').waitFor();
  await page.evaluate(()=>navigate('schedule'));await page.locator('.schedule-view-switch').waitFor();
  if(role==='crew'){await page.getByRole('button',{name:'All Jobs',exact:true}).click();await page.locator('[data-v5="jobs-all"][aria-pressed="true"]').waitFor();}
  await page.locator('[data-calendar-view="day"]').click();await page.locator('[data-calendar-view="day"][aria-pressed="true"]').waitFor();
  await page.locator('#calendar-jump').fill('2026-11-15');await page.locator('#calendar-jump').dispatchEvent('change');
  await page.locator('.weekly-day-column[data-schedule-date="2026-11-15"]').waitFor();
  assert.equal(await page.locator('.weekly-day-column').count(),1);
  assert.deepEqual(await page.locator('.weekly-dispatch-card .weekly-client').allTextContents(),['TEST ALPHA','TEST ZULU','TEST UNASSIGNED']);
  assert.equal(await page.locator('[data-action="open-imported-job"]').count(),1);
  assert.equal(await page.locator('[data-action="open-imported-job"]').getAttribute('data-id'),'SOURCE0');
  assert(await page.locator('.day-closed-label').isVisible());
  assert.deepEqual(await page.locator('.truck-legend-chip').allTextContents(),['alpha','Bravo','Truck 2','Truck 10','Zulu']);
  await page.locator('[data-action="calendar-next"]').click();await page.locator('.weekly-day-column[data-schedule-date="2026-11-16"]').waitFor();
  assert.equal(await page.locator('[data-action="open-imported-job"]').getAttribute('data-id'),'SOURCE1');
  await page.locator('[data-calendar-view="month"]').click();await page.locator('.schedule-month-day[data-schedule-date="2026-11-30"]').waitFor();
  assert.equal(await page.locator('.schedule-month-day').count(),42);
  const nov15=page.locator('.schedule-month-day[data-schedule-date="2026-11-15"]');
  assert.equal(await nov15.locator('[data-schedule-count]').innerText(),'4 services');
  assert(await nov15.locator('.schedule-month-closed').isVisible());
  assert.equal(await page.locator('.schedule-month-weekday').first().innerText(),'Mon');
  assert.equal(await page.locator('.schedule-month-weekday').last().innerText(),'Sun');
  await page.locator('#calendar-jump').fill('2026-12-31');await page.locator('#calendar-jump').dispatchEvent('change');
  await page.waitForFunction(()=>document.querySelector('.weekly-range-label')?.textContent==='December 2026');
  assert.equal(await page.locator('[data-calendar-view="month"]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-action="calendar-next"]').click();await page.waitForFunction(()=>document.querySelector('.weekly-range-label')?.textContent==='January 2027');
  await page.locator('[data-action="calendar-prev"]').click();await page.waitForFunction(()=>document.querySelector('.weekly-range-label')?.textContent==='December 2026');
  await page.locator('#calendar-jump').fill('2026-11-15');await page.locator('#calendar-jump').dispatchEvent('change');await page.locator('.schedule-month-day[data-schedule-date="2026-11-15"]').waitFor();
  await page.locator('[data-calendar-day="2026-11-15"]').first().click();await page.locator('[data-calendar-view="day"][aria-pressed="true"]').waitFor();
  assert.equal(await page.locator('#calendar-jump').inputValue(),'2026-11-15');
  await page.getByRole('button',{name:'My Jobs',exact:true}).click();await page.locator('[data-v5="jobs-mine"][aria-pressed="true"]').waitFor();
  assert.deepEqual(await page.locator('.weekly-dispatch-card .weekly-client').allTextContents(),['TEST ALPHA']);
  assert.equal(await page.locator('[data-action="open-imported-job"]').count(),0);
  await page.locator('[data-calendar-view="month"]').click();await page.locator('.schedule-month-day[data-schedule-date="2026-11-15"]').waitFor();
  assert.equal(await page.locator('.schedule-month-day[data-schedule-date="2026-11-15"] [data-schedule-count]').innerText(),'1 service');
  await page.getByRole('button',{name:'All Jobs',exact:true}).click();await page.locator('[data-v5="jobs-all"][aria-pressed="true"]').waitFor();
  await page.locator('[data-calendar-view="week"]').click();await page.locator('.schedule-week-view').waitFor();
  assert.equal(await page.locator('.weekly-day-column').count(),7);
  assert.equal(await page.locator('.weekly-day-column').first().getAttribute('data-schedule-date'),'2026-11-09');
  assert.equal(await page.locator('.weekly-day-column').last().getAttribute('data-schedule-date'),'2026-11-15');
  if(width<1024){
   await page.evaluate(()=>scrollTo(0,Math.min(700,document.documentElement.scrollHeight-innerHeight)));
   assert(await page.evaluate(()=>scrollY>100),'test must actually scroll');
   const top=await page.locator('.topbar').boundingBox();assert(Math.abs(top.y)<1,'header stays pinned '+width+'/'+role);
   await page.getByRole('button',{name:'Open navigation',exact:true}).click();assert(await page.locator('#sidebar').isVisible());
   await page.getByRole('button',{name:'Close navigation',exact:true}).click();
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal page overflow '+width+'/'+role);
  if(role==='owner'){
   await page.evaluate(()=>navigate('fleet'));await page.locator('.company-truck-card').first().waitFor();
   assert.deepEqual(await page.locator('.company-truck-card h2').allTextContents(),['alpha','Bravo','Truck 2','Truck 10','Zulu']);
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);await browser.close();
 console.log('PASS: pinned mobile navigation after scrolling; day/week/month, Monday-Sunday, year rollover, date jump, imported service placement, fully booked, My Jobs, alphabetical trucks; 4 widths x 4 roles.');
})().catch(e=>{console.error(e);process.exit(1);});
