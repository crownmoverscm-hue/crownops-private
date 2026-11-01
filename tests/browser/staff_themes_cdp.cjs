/* Actual Chrome via built-in CDP; no npm/browser downloads. All data is disposable fictional. */
'use strict';
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const out=path.resolve('evidence/staff-themes');fs.mkdirSync(out,{recursive:true});const base='http://127.0.0.1:8098',delay=n=>new Promise(r=>setTimeout(r,n));
class CDP{
 constructor(ws){this.ws=ws;this.id=0;this.pending=new Map();this.events=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}}else for(const fn of this.events.get(m.method)||[])fn(m);};}
 cmd(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++this.id;this.pending.set(id,{resolve,reject});this.ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));setTimeout(()=>{if(this.pending.delete(id))reject(Error('CDP timeout '+method));},15000).unref();});}
 on(method,fn){this.events.set(method,[...(this.events.get(method)||[]),fn]);}
 async eval(sid,expression){const r=await this.cmd('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sid);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
 async wait(sid,expression){for(let n=0;n<120;n++){try{if(await this.eval(sid,expression))return;}catch(_){}await delay(100);}throw Error('Wait failed: '+expression);}
}
(async()=>{
 let fixture,chrome,cdp,profile,lastSid;const results=[],areaChecks=[],errors=[],external=[],writes=[];
 try{
  const bin=process.env.CHROME_BIN||spawnSync('sh',['-c','command -v google-chrome || command -v chromium || command -v chromium-browser'],{encoding:'utf8'}).stdout.trim();
  if(!bin||!fs.existsSync(bin)){fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify({status:'NOT TESTED',reason:'No preinstalled browser. No download attempted.'}));throw Error('Browser unavailable');}
  fixture=spawn(process.env.PYTHON_BIN||'python',['tests/browser/staff_themes_fixture.py'],{env:{...process.env,PYTHONPATH:'.'},stdio:['pipe',fs.openSync(path.join(out,'fixture.log'),'w'),fs.openSync(path.join(out,'fixture-errors.log'),'w')]});
  let ready=false;for(let n=0;n<100;n++){try{if((await fetch(base+'/api/health')).ok){ready=true;break;}}catch(_){}await delay(100);}assert(ready,'Fixture startup failed');
  profile=fs.mkdtempSync(path.join(os.tmpdir(),'crownops-themes-browser-'));
  chrome=spawn(bin,['--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore',fs.openSync(path.join(out,'chrome.log'),'w'),fs.openSync(path.join(out,'chrome-errors.log'),'w')]});
  const portFile=path.join(profile,'DevToolsActivePort');for(let n=0;n<100&&!fs.existsSync(portFile);n++)await delay(100);assert(fs.existsSync(portFile),'Chrome debug port missing');
  const port=Number(fs.readFileSync(portFile,'utf8').split('\n')[0]),info=await(await fetch('http://127.0.0.1:'+port+'/json/version')).json();
  const ws=new WebSocket(info.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});cdp=new CDP(ws);
  cdp.on('Fetch.requestPaused',m=>{const req=m.params.request,allowed=req.url.startsWith(base+'/')&&['GET','HEAD'].includes(req.method);if(!req.url.startsWith(base+'/'))external.push(req.url);if(!['GET','HEAD'].includes(req.method))writes.push(req.method+' '+req.url);cdp.cmd(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:m.params.requestId}:{requestId:m.params.requestId,errorReason:'BlockedByClient'},m.sessionId).catch(e=>errors.push(String(e)));});
  cdp.on('Runtime.exceptionThrown',m=>errors.push(m.params.exceptionDetails.text));
  const cookie=async(sid,value)=>cdp.cmd('Network.setCookie',{name:'crownops_session',value,url:base,httpOnly:true},sid);
  const reloadStaff=async(sid,id)=>{const origin=await cdp.eval(sid,'performance.timeOrigin');await cdp.cmd('Page.reload',{},sid);await cdp.wait(sid,'performance.timeOrigin!=='+origin+' && document.readyState==="complete" && !!window.CrownOpsThemes && CrownOpsThemes.getStaff()==='+JSON.stringify(id)+' && !!document.querySelector("#staff-theme-nav")');};
  const shot=async(sid,file,selector)=>{let clip;if(selector){const rect=await cdp.eval(sid,`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height,scale:1};})()`);clip=rect;}const r=await cdp.cmd('Page.captureScreenshot',{format:'png',...(clip?{clip}:{}),captureBeyondViewport:!!clip},sid);fs.writeFileSync(path.join(out,file),Buffer.from(r.data,'base64'));};
  for(const width of [1440,390]){
   const {browserContextId}=await cdp.cmd('Target.createBrowserContext');const {targetId}=await cdp.cmd('Target.createTarget',{url:'about:blank',browserContextId});const {sessionId:sid}=await cdp.cmd('Target.attachToTarget',{targetId,flatten:true});lastSid=sid;
   for(const domain of ['Page','Runtime','Network'])await cdp.cmd(domain+'.enable',{},sid);await cdp.cmd('Fetch.enable',{patterns:[{urlPattern:'*'}]},sid);
   await cdp.cmd('Page.addScriptToEvaluateOnNewDocument',{source:'window.__themePaints=[];requestAnimationFrame(function sample(){if(document.querySelector(".app .sidebar"))window.__themePaints.push(document.documentElement.dataset.staffTheme);if(window.__themePaints.length<12)requestAnimationFrame(sample);});'},sid);
   await cdp.cmd('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<600},sid);
   await cookie(sid,'TEST-theme-owner-token');await cdp.cmd('Page.navigate',{url:base},sid);
   await cdp.wait(sid,'!!document.querySelector("#staff-theme-nav") && !!document.querySelector("#content h1")');
   assert.equal(await cdp.eval(sid,'CrownOpsThemes.getTheme()'),'black');
   for(const theme of ['black','light','ocean']){
    await cdp.eval(sid,`(()=>{const s=document.querySelector('#staff-theme-nav');s.value=${JSON.stringify(theme)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await cdp.wait(sid,`document.documentElement.dataset.staffTheme===${JSON.stringify(theme)}`);
    if(width<600){await cdp.eval(sid,'document.querySelector("[data-action=menu]").click()');await cdp.wait(sid,'document.querySelector(".sidebar").classList.contains("open") && Math.abs(document.querySelector(".sidebar").getBoundingClientRect().left)<1');}
    assert(!(await cdp.eval(sid,'document.documentElement.scrollWidth>innerWidth+1')),'Horizontal overflow');
    const branding=await cdp.eval(sid,`(()=>{const e=document.querySelector('.sidebar .brand-icon'),s=getComputedStyle(e),img=e.querySelector('img');return {padding:s.paddingTop,width:e.getBoundingClientRect().width,src:img.getAttribute('src'),fit:getComputedStyle(img).objectFit,filter:getComputedStyle(img).filter};})()`);
    assert.equal(branding.padding,'8px');assert(branding.src.includes('/static/icon.svg'));assert.equal(branding.fit,'contain');assert.equal(branding.filter,'none');
    if(width<600)assert(await cdp.eval(sid,'(()=>{const r=document.querySelector(".sidebar .brand-icon").getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.width>=60;})()'),'Mobile logo must be fully visible after drawer transition');
    await shot(sid,`${width<600?'mobile':'desktop'}-${theme}.png`);
    if(width===1440&&theme==='black')await shot(sid,'logo-padding-closeup.png','.sidebar .brand');
    if(width<600){assert(await cdp.eval(sid,'!!document.querySelector(".finish-mobile-brand .staff-theme-mobile-subtitle")'));await cdp.eval(sid,'document.querySelector("[data-workspace-close-menu]").click()');}
    await cdp.eval(sid,'navigate("settings")');await cdp.wait(sid,'!!document.querySelector("#staff-theme-settings")');
    assert.equal(await cdp.eval(sid,'document.querySelector("#staff-theme-settings").value'),theme);
    await shot(sid,`settings-${width}-${theme}.png`);
    // Switch while a real staff editor field contains unsaved text: no reload, navigation or value loss.
    await cdp.eval(sid,`(()=>{const i=document.createElement('textarea');i.id='theme-unsaved-probe';i.value='TEST unsaved text';document.querySelector('#content').append(i);window.themeDocumentMark=performance.timeOrigin;document.querySelector('#staff-theme-settings').focus();})()`);
    await cdp.cmd('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowDown',code:'ArrowDown'},sid);await cdp.cmd('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowDown',code:'ArrowDown'},sid);
    assert(await cdp.eval(sid,'document.querySelector("#theme-unsaved-probe").value==="TEST unsaved text" && performance.timeOrigin===window.themeDocumentMark'));
    assert(await cdp.eval(sid,'parseFloat(getComputedStyle(document.querySelector("#staff-theme-settings")).outlineWidth)>=3'));
    await cdp.eval(sid,'document.querySelector("#theme-unsaved-probe").remove();CrownOpsThemes.select('+JSON.stringify(theme)+')');
    await cdp.eval(sid,'navigate("dashboard")');await cdp.wait(sid,'!!document.querySelector("#content h1") && !document.querySelector("#staff-theme-settings")');
    results.push({width,theme,status:'PASS',logo:branding,keyboard:true,noUnsavedLoss:true});
   }
   for(const view of ['sales','communications','schedule']){
    await cdp.eval(sid,'navigate('+JSON.stringify(view)+')');
    await cdp.wait(sid,'!!document.querySelector("#content h1")');
    assert(!(await cdp.eval(sid,'document.querySelector("#content").textContent.includes("This section could not load")')),'Staff view failed: '+view);
    assert(!(await cdp.eval(sid,'document.documentElement.scrollWidth>innerWidth+1')),'Staff view overflow: '+view);
    for(const theme of ['black','light','ocean']){
     await cdp.eval(sid,'CrownOpsThemes.select('+JSON.stringify(theme)+')');
     const expected={black:'rgb(245, 246, 248)',light:'rgb(242, 244, 247)',ocean:'rgb(237, 244, 248)'}[theme];
     assert.equal(await cdp.eval(sid,'getComputedStyle(document.querySelector("#content")).backgroundColor'),expected);
     assert(!(await cdp.eval(sid,'document.documentElement.scrollWidth>innerWidth+1')),'Area overflow '+view+' '+theme);
     areaChecks.push({width,view,theme,status:'PASS'});
    }
    if(width===1440)await shot(sid,'staff-'+view+'.png');
   }
   const semantic=[];for(const theme of ['black','light','ocean']){await cdp.eval(sid,'CrownOpsThemes.select('+JSON.stringify(theme)+')');semantic.push(await cdp.eval(sid,`(()=>{let p=document.getElementById('theme-semantic-probe');if(!p){p=document.createElement('div');p.id='theme-semantic-probe';p.hidden=true;p.innerHTML='<span class="pill teal">TEST success</span><span class="callout orange">TEST warning</span><button class="btn-danger">TEST error</button>';document.body.append(p);}return Array.from(p.children,e=>({color:getComputedStyle(e).color,background:getComputedStyle(e).backgroundColor}));})()`));}assert.deepEqual(semantic[0],semantic[1]);assert.deepEqual(semantic[1],semantic[2]);
   await cdp.eval(sid,'crmQuoteBuilder()');await cdp.wait(sid,'!!document.querySelector("#crm-quote-form")');
   const retained=await cdp.eval(sid,`(()=>{const f=document.querySelector('#crm-quote-form');const e=f.elements.quote_title||f.querySelector('input[type=text]');e.value='TEST unsaved quote theme';window.quoteThemeMark=performance.timeOrigin;return JSON.stringify([...new FormData(f)]);})()`);
   for(const theme of ['black','light','ocean']){
    await cdp.eval(sid,'CrownOpsThemes.select('+JSON.stringify(theme)+')');
    assert.equal(await cdp.eval(sid,'JSON.stringify([...new FormData(document.querySelector("#crm-quote-form"))])'),retained);
    assert(await cdp.eval(sid,'performance.timeOrigin===window.quoteThemeMark && document.querySelector("#modal").open'));
    if(width===1440)await shot(sid,'quote-editor-'+theme+'.png');
   }
   await cdp.eval(sid,'modal.close()');
   await cdp.eval(sid,'CrownOpsThemes.select("ocean")');await reloadStaff(sid,'THEME-A');assert.equal(await cdp.eval(sid,'CrownOpsThemes.getTheme()'),'ocean');await cdp.wait(sid,'window.__themePaints.length>0');assert(await cdp.eval(sid,'window.__themePaints.every(t=>t==="ocean")'),'Persisted theme flashed at first staff paint');
   await cookie(sid,'TEST-theme-sales-token');await reloadStaff(sid,'THEME-B');assert.equal(await cdp.eval(sid,'CrownOpsThemes.getTheme()'),'black');await cdp.eval(sid,'CrownOpsThemes.select("light")');
   await cookie(sid,'TEST-theme-owner-token');await reloadStaff(sid,'THEME-A');assert.equal(await cdp.eval(sid,'CrownOpsThemes.getTheme()'),'ocean');
   await cdp.eval(sid,'CrownOpsThemes.setLanguage("fr");navigate("settings")');await cdp.wait(sid,'document.querySelector(".staff-theme-settings [data-theme-label]")?.textContent==="Thème"');
   assert.deepEqual(await cdp.eval(sid,'Array.from(document.querySelector("#staff-theme-settings").options,o=>o.textContent)'),['Black View','Light View','Ocean Blue View']);await shot(sid,`settings-fr-${width}.png`);
   await cdp.eval(sid,'loginScreen()');assert.equal(await cdp.eval(sid,'CrownOpsThemes.getTheme()'),'black');
   if(width===1440){await cdp.cmd('Page.navigate',{url:base+'/theme-logo-comparison'},sid);await cdp.wait(sid,'!!document.querySelector("#logo-comparison .brand-icon img") && Array.from(document.images).every(i=>i.complete)');await shot(sid,'logo-comparison.png','#logo-comparison');}
   await cdp.cmd('Target.disposeBrowserContext',{browserContextId});
  }
  assert.equal(external.length,0);assert.equal(writes.length,0);assert.equal(errors.length,0,JSON.stringify(errors));
  fixture.stdin.write('snapshot\n');for(let n=0;n<50&&!fs.existsSync(path.join(out,'fixture-readback.json'));n++)await delay(100);
  const readback=JSON.parse(fs.readFileSync(path.join(out,'fixture-readback.json'),'utf8'));assert.equal(readback.external_attempts.length,0);assert(Object.values(readback.prohibited_business_counts).every(n=>n===0));
  fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify({status:'PASS',browser:info.Browser,node:process.version,results,areaChecks,semanticColorsStable:true,externalRequests:external,nonGetRequests:writes,pageErrors:errors,readback,scope:'Actual staff shell, Settings, account/reload persistence, mobile drawer, keyboard and theme-label EN/FR; seeded fictional sessions, not real authentication or business-flow acceptance.'},null,2));
 }catch(e){let state;try{if(cdp&&lastSid){state=await cdp.eval(lastSid,'({title:document.querySelector("#content h1")?.textContent,body:document.body.innerText.slice(0,1800)})');await cdp.cmd('Page.captureScreenshot',{format:'png'},lastSid).then(r=>fs.writeFileSync(path.join(out,'failure.png'),Buffer.from(r.data,'base64')));}}catch(_){}fs.writeFileSync(path.join(out,'browser-failure.json'),JSON.stringify({error:String(e),state,errors,external,writes},null,2));process.exitCode=1;
 }finally{if(cdp)cdp.ws.close();if(chrome)chrome.kill('SIGTERM');if(fixture)fixture.kill('SIGTERM');if(chrome){for(let n=0;n<30&&chrome.exitCode===null;n++)await delay(100);}if(profile){try{fs.rmSync(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100});}catch(e){fs.writeFileSync(path.join(out,'cleanup-note.txt'),'Disposable browser profile cleanup incomplete; runner teardown removes it.');}}}
})();
