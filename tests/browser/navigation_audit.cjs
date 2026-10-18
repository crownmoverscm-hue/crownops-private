'use strict';
const {chromium}=require('playwright'),fs=require('node:fs');
const base='http://127.0.0.1:8095';
const evidenceDir=process.env.CROWNOPS_BROWSER_OUTPUT||require('node:os').tmpdir();
fs.mkdirSync(evidenceDir,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']}),results=[],errors=[];
 fs.mkdirSync(evidenceDir+'/mobile-before',{recursive:true});
 for(const width of [320,390,430,768,1024,1440])for(const role of (width===390?['owner','manager','sales','crew']:['owner'])){
  const ctx=await browser.newContext({viewport:{width,height:844},isMobile:width<768,hasTouch:width<768});await ctx.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);const p=await ctx.newPage();p.on('pageerror',e=>errors.push({width,role,error:e.message}));await p.goto(base);await p.locator('#content h1').waitFor();
  const targets=await p.locator('#sidebar .nav-button[data-page]').evaluateAll(bs=>bs.map(b=>b.dataset.page));
  for(const target of targets){
   if(width<1024)await p.getByRole('button',{name:'Open navigation',exact:true}).click();
   await p.locator('#sidebar .nav-button[data-page="'+target+'"]').click();
   await p.waitForFunction(target=>document.querySelector('#content')?.dataset.workspacePage===target&&document.querySelector('#content h1'),target);
   await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));const details=await p.evaluate(()=>{
    const content=document.querySelector('#content');const visible=x=>x.getClientRects().length&&getComputedStyle(x).visibility!=='hidden'&&!x.closest('.finish-mobile-table>thead');
    const box=x=>{const r=x.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}};
    return {heading:content.querySelector('h1')?.textContent,overflow:document.documentElement.scrollWidth>innerWidth+1,sidebar:box(document.querySelector('#sidebar')),
     overflowing:[...content.querySelectorAll('*')].filter(visible).filter(x=>x.getBoundingClientRect().right>innerWidth+2).slice(0,12).map(x=>({tag:x.tagName,cls:x.className,text:x.textContent?.trim().slice(0,80),...box(x)})),
     clipped:[...content.querySelectorAll('*')].filter(visible).filter(x=>!x.matches('input,textarea,select')&&['hidden','clip'].includes(getComputedStyle(x).overflowX)&&x.scrollWidth>x.clientWidth+2).map(x=>({cls:x.className,text:x.textContent.trim().slice(0,100),scroll:x.scrollWidth,width:x.clientWidth})),
     actions:[...content.querySelectorAll('button,a[href],summary')].filter(visible).map(x=>({text:x.getAttribute('aria-label')||x.textContent.trim(),attrs:Object.fromEntries([...x.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value])),...box(x)}))};
   });results.push({width,role,target,...details});
   if(width===390&&role==='owner'&&['dashboard','sales','schedule','help','account','quotes'].includes(target))await p.screenshot({path:evidenceDir+'/mobile-after/'+target+'.png',fullPage:true});
  }await ctx.close();
 }
 fs.writeFileSync(evidenceDir+'/mobile-audit-after.json',JSON.stringify({results,errors},null,2));await browser.close();if(errors.length||results.some(x=>x.overflow||x.clipped.length))throw Error('Navigation layout failed; inspect the JSON evidence');console.log(JSON.stringify({pages:results.length,errors,failures:results.filter(x=>x.overflow||x.clipped.length).map(x=>({width:x.width,role:x.role,page:x.target,overflow:x.overflow,clipped:x.clipped,over:x.overflowing.slice(0,4)}))},null,2));
})().catch(e=>{console.error(e);process.exit(1)});
