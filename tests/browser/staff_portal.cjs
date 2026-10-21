'use strict';
const {chromium}=require('playwright'), assert=require('node:assert/strict'),fs=require('node:fs');
let debugPage;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CROWNOPS_CHROMIUM_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const base='http://127.0.0.1:8095',errors=[],contexts=[];
 async function login(role){
  const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});contexts.push(ctx);
  await ctx.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  const p=await ctx.newPage();debugPage=p;p.on('pageerror',e=>errors.push(role+': '+e.message));
  await p.goto(base);await p.locator('#content h1').waitFor();
  await p.evaluate(()=>navigate('account'));await p.getByRole('heading',{name:'My Portal',exact:true}).waitFor();return p;
 }
 async function tab(p,label){debugPage=p;await p.locator('.mp-tabs').getByRole('button',{name:label,exact:true}).click();await p.locator('.mp-content h2').filter({hasText:label}).waitFor();}
 async function submit(p,label){await p.getByRole('button',{name:label,exact:true}).click();await p.getByRole('dialog').waitFor({state:'hidden'});}
 async function checkWidths(p,role){for(const width of [320,390,768,1440]){await p.setViewportSize({width,height:900});const overflow=await p.evaluate(()=>Array.from(document.querySelectorAll('body *')).filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.right>innerWidth+1&&r.left>=0;}).map(e=>e.tagName+'.'+e.className).slice(0,12));assert.ok(await p.locator('body').evaluate(x=>x.scrollWidth<=innerWidth+1),role+' overflow at '+width+': '+overflow.join(', '));if(process.env.CROWNOPS_SCREENSHOT_DIR){fs.mkdirSync(process.env.CROWNOPS_SCREENSHOT_DIR,{recursive:true});await p.screenshot({path:process.env.CROWNOPS_SCREENSHOT_DIR+'/portal-'+role+'-'+width+'.png',fullPage:true});}}await p.setViewportSize({width:390,height:844});}
 const crew=await login('crew');let candidateLoads=0;
 crew.on('request',r=>{if(r.url().endsWith('/replacements'))candidateLoads++;});
 assert.equal(await crew.getByText('Friday replacement',{exact:true}).count(),0);
 await tab(crew,'My hours');await crew.getByText('3.5 h',{exact:true}).first().waitFor();assert.equal(await crew.locator('.mp-content input').count(),0);
 await crew.getByRole('button',{name:'Ask about my hours',exact:true}).click();await crew.getByLabel('Message',{exact:true}).fill('Please check my recorded hours.');await submit(crew,'Send to management');
 await tab(crew,'My information');await crew.getByLabel('Contact phone',{exact:true}).fill('5145550189');await crew.getByRole('button',{name:'Save contact information',exact:true}).click();await crew.waitForFunction(()=>document.querySelector('[name=phone]')?.value==='5145550189');
 await tab(crew,'My availability');await crew.getByRole('checkbox',{name:'Friday',exact:true}).check();await crew.getByLabel('Friday available from',{exact:true}).fill('08:00');await crew.getByLabel('Friday available until',{exact:true}).fill('18:00');await crew.getByRole('button',{name:'Save availability',exact:true}).click();await crew.waitForFunction(async()=>{const x=await api('/my-portal');return x.availability.version===1;});assert.equal(await crew.getByRole('checkbox',{name:'Sunday',exact:true}).isChecked(),false);
 await checkWidths(crew,'availability');assert.equal(candidateLoads,0);
 await tab(crew,'Upcoming shifts');await crew.getByRole('button',{name:'Give Shift Away',exact:true}).click();assert.equal(candidateLoads,1);
 await crew.getByRole('checkbox',{name:'Friday replacement',exact:true}).waitFor();assert.equal(await crew.getByText('Sunday replacement',{exact:true}).count(),0);
 await crew.getByRole('checkbox',{name:'Friday replacement',exact:true}).check();await submit(crew,'Request coverage');
 await tab(crew,'Requests');await crew.locator('.mp-content').getByText('Awaiting coworker',{exact:true}).waitFor();
 const replacement=await login('workerE3');await tab(replacement,'Requests');await replacement.getByRole('button',{name:'I can cover this',exact:true}).click();await replacement.locator('.mp-content').getByText('Awaiting manager',{exact:true}).waitFor();
 const manager=await login('manager');await tab(manager,'Requests');await manager.getByRole('button',{name:'Review request',exact:true}).click();await manager.getByLabel('Decision',{exact:true}).selectOption('Approved');await submit(manager,'Save decision');
 await replacement.evaluate(()=>navigate('account'));await tab(replacement,'Upcoming shifts');await replacement.locator('.mp-content').getByRole('heading',{name:'Test move',exact:true}).waitFor();
 await crew.evaluate(()=>navigate('account'));await tab(crew,'Upcoming shifts');await crew.getByText('No upcoming assignments. Your next assigned service will appear here.',{exact:true}).waitFor();
 await tab(manager,'Messages');await manager.getByRole('button',{name:'Reply',exact:true}).click();await manager.getByLabel('Reply',{exact:true}).fill('Your recorded hours have been reviewed.');await submit(manager,'Send reply');
 await crew.evaluate(()=>navigate('account'));await tab(crew,'Messages');await crew.getByText('Your recorded hours have been reviewed.',{exact:true}).waitFor();
 await tab(crew,'Requests');await crew.getByRole('button',{name:'Request a Day Off',exact:true}).click();await crew.getByLabel('First day off',{exact:true}).fill('2099-02-01');await crew.getByLabel('Last day off',{exact:true}).fill('2099-02-01');await submit(crew,'Send request');await crew.locator('.mp-content').getByText('Pending',{exact:true}).waitFor();
 const owner=await login('owner');await tab(owner,'Documents');await owner.getByRole('button',{name:'Create or send a document',exact:true}).click();await owner.getByLabel('Document title',{exact:true}).fill('Safe loading protocol');await owner.getByRole('checkbox',{name:'Crew · crew',exact:true}).check();await owner.getByLabel('Write a document here, or attach a file below',{exact:true}).fill('Check straps before departure.');await submit(owner,'Publish & notify');
 await crew.evaluate(()=>navigate('account'));await tab(crew,'Documents');await crew.getByRole('button',{name:'Open document',exact:true}).click();await crew.getByText('Check straps before departure.',{exact:true}).waitFor();
 const download=crew.waitForEvent('download');await crew.getByRole('link',{name:'Download document',exact:true}).click();assert.ok((await download).suggestedFilename().endsWith('.txt'));await submit(crew,'I have read this');
 await owner.evaluate(()=>navigate('account'));await owner.getByRole('button',{name:'View acknowledgements',exact:true}).click();await owner.getByText('Crew · Acknowledged',{exact:true}).waitFor();await owner.getByRole('button',{name:'Close',exact:true}).last().click();
 await tab(replacement,'Documents');assert.equal(await replacement.getByText('Safe loading protocol',{exact:true}).count(),0);
 await owner.getByRole('button',{name:'Create or send a document',exact:true}).click();await owner.getByLabel('Document title',{exact:true}).fill('All-staff handbook');await owner.getByLabel('Send to',{exact:true}).selectOption('all');await owner.locator('input[type=file]').setInputFiles({name:'handbook.txt',mimeType:'text/plain',buffer:Buffer.from('Fictional handbook')});await submit(owner,'Publish & notify');
 await replacement.evaluate(()=>navigate('account'));await replacement.getByText('All-staff handbook',{exact:true}).waitFor();
 await checkWidths(crew,'documents');await checkWidths(owner,'owner-documents');
 assert.deepEqual(errors,[]);for(const ctx of contexts)await ctx.close();await browser.close();console.log(JSON.stringify({coverage_request_accept_approve:true,explicit_availability_only:true,hours_read_only:true,private_documents_and_downloads:true,owner_publishing:true,manager_messages:true,time_off:true,widths:[320,390,768,1440],page_errors:errors}));
})().catch(async e=>{console.error(e);if(debugPage)console.error(await debugPage.evaluate(()=>({toast:document.getElementById('toast')?.textContent,error:document.querySelector('.login-error')?.textContent,content:document.getElementById('content')?.textContent?.slice(-1500)})).catch(()=>null));process.exit(1);});
