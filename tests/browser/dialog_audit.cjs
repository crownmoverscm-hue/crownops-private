'use strict';
const {chromium}=require('playwright'),fs=require('node:fs');
const base='http://127.0.0.1:8095';
const evidenceDir=process.env.CROWNOPS_BROWSER_OUTPUT||require('node:os').tmpdir();
fs.mkdirSync(evidenceDir,{recursive:true});
const cases={
 dashboard:['[data-action=new-lead]','[data-action=new-job]','[data-ux=action-assign]','[data-ux=attention]'],
 sales:['[data-crm=client-new]','[data-crm=client-open]'],
 quotes:['[data-crm=quote-new]','[data-crm=templates]','[data-crm=quote-open]'],
 followups:['[data-ux=callback-backlog-review]','[data-ux=action-workspace]'],
 communications:['[data-v4=new-email]','[data-v4=read-email]'],
 schedule:['[data-availability=open]','[data-v55=manage-trucks]'],
 invoices:['[data-ux=invoice-open]'],payments:['[data-action=new-payment]'],timeoff:['[data-v5=request-off]'],
 documents:['[data-v5=document-prepare]','[data-v5=template-new]','[data-v5=template-edit]'],
 portalhub:['[data-v4=portal-files]'],care:['[data-incident=new]'],
 team:['[data-action=invite]','[data-ux=employee-add]','[data-ux=employee-contact-import]','[data-ux=employee-open]'],
 contacts:['[data-company=contact-add]','[data-company=reference-import]'],expenses:['[data-expense=new]'],
 access:['[data-account=edit-access]'],rates:['[data-v5=rate-period-new]','[data-v4=daily-rate]'],
 inventory:['[data-v5=material-new]','[data-v5=material-edit]'],
 fleet:['[data-action=new-vehicle]','[data-company=truck-open]','[data-company=truck-edit]'],
 connections:['[data-ux=company-email-edit]','[data-gmail-match]'],
 policies:['[data-v4=policy][data-id=billing_policy]','[data-v4=policy][data-id=client_terms]','[data-v4=policy][data-id=deposit_policy]','[data-v4=policy][data-id=media_program]','[data-v4=policy][data-id=payroll_policy]','[data-v4=policy][data-id=automation_policy]'],
 account:['[data-v4=change-password]','[data-v4=mfa]'],help:['[data-crew-handoff=protocols]']
};
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']}),results=[],errors=[];fs.mkdirSync(evidenceDir+'/mobile-dialogs',{recursive:true});
 for(const width of [320,390,768]){
  const ctx=await browser.newContext({viewport:{width,height:width===320?568:844},isMobile:width<768,hasTouch:true});await ctx.addCookies([{name:'crownops_session',value:'owner-token',url:base}]);const p=await ctx.newPage();p.setDefaultTimeout(8000);p.on('dialog',d=>d.dismiss());p.on('pageerror',e=>errors.push(e.message));await p.goto(base);await p.locator('#content h1').waitFor();
  for(const [page,selectors] of Object.entries(process.env.RECHECK?{schedule:['[data-v55=manage-trucks]'],team:['[data-ux=employee-open]'],help:['[data-crew-handoff=protocols]']}:cases))for(const selector of selectors){
   await p.goto(base);await p.locator('#content h1').waitFor();
   await p.getByRole('button',{name:'Open navigation',exact:true}).click();await p.locator('#sidebar [data-page="'+page+'"]').click();await p.waitForFunction(page=>document.querySelector('#content')?.dataset.workspacePage===page,page,{timeout:8000});
   try{await p.locator('#content '+selector+':visible').first().click();if(selector==='[data-v55=manage-trucks]'){await p.waitForFunction(()=>document.querySelector('#content')?.dataset.workspacePage==='fleet');results.push({width,page,selector,navigation:'fleet'});continue;}await p.getByRole('dialog').waitFor();}catch(e){errors.push({width,page,selector,error:e.message,toast:await p.locator('#toast').textContent()});console.log('FAILED',width,page,selector);continue;}
   await p.getByRole('dialog').locator('h2').first().waitFor();
   await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const d=await p.getByRole('dialog').evaluate(m=>{
    const vis=x=>x.getClientRects().length&&getComputedStyle(x).visibility!=='hidden'&&!x.closest('thead');const r=m.getBoundingClientRect(),body=m.querySelector('.modal-body'),b=body.getBoundingClientRect();
    return {title:m.querySelector('h2').textContent,dialogOverflow:m.scrollWidth>m.clientWidth+2,bodyOverflow:body.scrollWidth>body.clientWidth+2,bodyHeight:b.height,bottom:r.bottom,viewport:innerHeight,
     clipped:[...m.querySelectorAll('*')].filter(vis).filter(x=>!x.matches('input,textarea,select')&&x.clientWidth>1&&['hidden','clip'].includes(getComputedStyle(x).overflowX)&&x.scrollWidth>x.clientWidth+2).map(x=>({cls:x.className,text:x.textContent.trim().slice(0,80),width:x.clientWidth,scroll:x.scrollWidth})),
     controls:[...m.querySelectorAll('button,a[href],input,select,textarea,summary')].filter(vis).map(x=>{const q=x.getBoundingClientRect();return {text:x.getAttribute('aria-label')||x.textContent.trim().slice(0,70),tag:x.tagName,attrs:Object.fromEntries([...x.attributes].filter(a=>a.name.startsWith('data-')||['name','type'].includes(a.name)).map(a=>[a.name,a.value])),w:q.width,h:q.height,x:q.x}})};
   });results.push({width,page,selector,...d});console.log(width,page,selector,d.title,d.dialogOverflow||d.bodyOverflow||d.clipped.length?'CHECK':'OK');
   if(width===390&&['invoice-open','client-open','quote-open','truck-open','new-lead'].some(v=>selector.includes(v)))await p.screenshot({path:evidenceDir+'/mobile-dialogs/'+page+'.png'});
   fs.writeFileSync(evidenceDir+(process.env.RECHECK?'/mobile-dialog-recheck.json':'/mobile-dialog-audit.json'),JSON.stringify({results,errors},null,2));
  }await ctx.close();
 }await browser.close();if(errors.length||results.some(x=>x.dialogOverflow||x.bodyOverflow||x.clipped?.length))throw Error('Dialog check failed; inspect the JSON evidence');console.log(JSON.stringify({dialogs:results.length,errors}));
})().catch(e=>{console.error(e);process.exit(1)});
