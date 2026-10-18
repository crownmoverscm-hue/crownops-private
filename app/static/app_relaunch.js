/* Deliberate per-device updates: never reload another person's unsaved work. */
'use strict';
(function(){
 const loadedRevision=document.querySelector('meta[name="crownops-release"]')?.content||'';
 const resumeKey='crownops-relaunch-resume';
 let latestRevision='',checking=null,relaunching=false,lastCheck=0,writes=0;
 const previousApi=api;
 api=async function(path,options={}){
  const write=!['GET','HEAD','OPTIONS'].includes(String(options.method||'GET').toUpperCase());
  if(write)writes++;
  try{return await previousApi(path,options);}finally{if(write)writes--;}
 };
 const updateButton=cls=>`<button type="button" data-app-relaunch class="${cls||'btn-primary'}">Update &amp; relaunch</button>`;
 function statusText(){
  if(navigator.onLine===false)return 'Reconnect to check for the latest version.';
  return latestRevision&&latestRevision!==loadedRevision?'A newer version is available.':'Open the latest version of CrownOps on this device.';
 }
 function paint(){
  if(!S.user)return;
  const foot=document.querySelector('.sidebar-foot');
  if(foot&&!foot.querySelector('[data-app-relaunch]'))foot.insertAdjacentHTML('afterbegin',updateButton('nav-button app-relaunch-menu'));
  const main=document.querySelector('main');
  if(main&&!document.getElementById('app-update-notice')){
   const notice=document.createElement('section');notice.id='app-update-notice';notice.className='app-update-notice';notice.hidden=true;
   notice.setAttribute('role','status');notice.innerHTML='<div><strong>Update available</strong><p>Finish your current work, then relaunch for the latest version.</p></div>'+updateButton('btn-primary');
   main.querySelector('.topbar')?.after(notice);
  }
  const notice=document.getElementById('app-update-notice');
  if(notice)notice.hidden=!(latestRevision&&latestRevision!==loadedRevision);
  document.querySelectorAll('[data-app-update-status]').forEach(el=>el.textContent=statusText());
  document.querySelectorAll('[data-app-relaunch]').forEach(el=>{el.disabled=relaunching;el.textContent=relaunching?'Checking latest version…':'Update & relaunch';});
 }
 async function checkRelease(){
  if(checking)return checking;
  checking=(async()=>{
   if(navigator.onLine===false)throw Error('You are offline. Reconnect before updating CrownOps.');
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
   try{
    const response=await fetch('/api/app-release',{cache:'no-store',credentials:'same-origin',signal:controller.signal});
    if(!response.ok)throw Error('Could not reach the update service. Your current screen is still open. Try again shortly.');
    const result=await response.json();
    if(!/^[a-f0-9]{24}$/.test(result.revision||''))throw Error('The update check was incomplete. Please try again shortly.');
    latestRevision=result.revision;lastCheck=Date.now();paint();return result;
   }catch(error){
    if(error.name==='AbortError')throw Error('The update check timed out. Check your connection and try again.');
    throw error;
   }finally{clearTimeout(timer);}
  })();
  try{return await checking;}finally{checking=null;}
 }
 function blocked(){
  if(writes)return 'A save or upload is still running. Wait for it to finish before updating.';
  if(modal.open&&modal.querySelector('form'))return 'Save or close the open form before updating. Your information is still here.';
  return '';
 }
 async function relaunch(){
  if(relaunching)return;
  const reason=blocked();if(reason){toast(reason);return;}
  if(demo()){toast('App updates are available in your live CrownOps workspace.');return;}
  relaunching=true;paint();
  try{
   const release=await checkRelease();
   // Worker refresh is best effort: our worker always fetches live pages and
   // has no private/offline asset cache. A failed worker refresh must not erase
   // a session or prevent a fresh document/asset request.
   if('serviceWorker' in navigator){
    let timer;
    try{await Promise.race([navigator.serviceWorker.getRegistration().then(r=>r?.update()).catch(()=>{}),new Promise(resolve=>{timer=setTimeout(resolve,2000);})]);}
    finally{clearTimeout(timer);}
   }
   const changed=blocked();if(changed)throw Error(changed);
   if(navigator.onLine===false)throw Error('You are offline. Reconnect before updating CrownOps.');
   try{sessionStorage.setItem(resumeKey,JSON.stringify({user:S.user.id,page:S.page,week:S.week instanceof Date?S.week.toISOString():null,scope:S.jobScope,revision:release.revision}));}catch{}
   location.reload();
  }catch(error){toast(error.message||'Could not check for updates. Try again when connected.');}
  finally{relaunching=false;paint();}
 }
 async function backgroundCheck(){
  if(!S.user||demo()||document.hidden||Date.now()-lastCheck<30000)return;
  lastCheck=Date.now();try{await checkRelease();}catch{paint();}
 }
 const previousShell=shell;
 shell=function(){const result=previousShell();paint();return result;};
 const previousHelp=X.pageRenderers.help;
 X.pageRenderers.help=async function(){
  const html=await previousHelp();
  return `<section class="panel app-relaunch-panel"><div class="panel-body"><h2>Keep CrownOps up to date</h2><p data-app-update-status>${h(statusText())}</p><p>Save or close any open form, then tap Update &amp; relaunch. Your saved company records stay in CrownOps. Each person updates their own device.</p>${updateButton('btn-primary')}</div></section><div class="spacer"></div>`+html;
 };
 const previousBootstrap=bootstrap;
 bootstrap=async function(){
  const result=await previousBootstrap();if(!S.user)return result;
  let resume;try{resume=JSON.parse(sessionStorage.getItem(resumeKey)||'null');sessionStorage.removeItem(resumeKey);}catch{}
  if(resume&&resume.user===S.user.id){
   if(resume.week&&Number.isFinite(Date.parse(resume.week)))S.week=new Date(resume.week);
   if(['mine','all'].includes(resume.scope)){S.jobScope=resume.scope;S.scheduleUser=S.user.id;}
   if(allowedPages().includes(resume.page))await navigate(resume.page);
   toast(resume.revision===loadedRevision?'CrownOps relaunched with the latest version.':'CrownOps relaunched. Checking for updates…');
  }
  paint();backgroundCheck();return result;
 };
 document.addEventListener('click',e=>{if(!e.target.closest('[data-app-relaunch]'))return;e.preventDefault();relaunch();});
 document.addEventListener('visibilitychange',backgroundCheck);
 window.addEventListener('focus',backgroundCheck);window.addEventListener('online',()=>{lastCheck=0;backgroundCheck();});
 setInterval(backgroundCheck,300000);
})();
