/* Copyright (c) 2026 Crown Movers. See PROPRIETARY_NOTICE.md. */
'use strict';
(function(){
 let installPrompt=null;
 let installed=window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
 const copyright='<p class="device-copyright">&copy; 2026 Crown Movers. Private company workspace.</p>';
 const appAddress=()=>location.origin+'/';
 const refresh=()=>{if(S.user&&S.page==='help')navigate('help');};
 window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;refresh();});
 window.addEventListener('appinstalled',()=>{installed=true;installPrompt=null;refresh();});
 ensureNav(['help','phone','Help & install']);
 const previousAllowed=allowedPages;
 allowedPages=function(){return [...new Set([...previousAllowed(),'help'])];};
 const previousGroups=uxGroups;
 uxGroups=function(){const groups=previousGroups();const target=groups.find(g=>g[0]===(S.user.role==='crew'?'FIELD WORK':'COMPANY'));if(target&&!target[1].includes('help'))target[1].push('help');return groups;};

 function installInstructions(){return `<div class="device-install-grid">
  <section class="device-install-card"><h3>Samsung / Android</h3><p>Open CrownOps in Chrome. Open the three-dot menu, choose <strong>Install and create shortcut</strong>, then <strong>Install</strong>. Older Chrome versions may say <strong>Install app</strong> or <strong>Add to Home screen</strong>.</p><p class="tiny muted">Chrome gives the most consistent install steps on Samsung phones. Samsung Internet also supports a home-screen shortcut through its add-to-home-screen control.</p></section>
  <section class="device-install-card"><h3>iPhone / iPad</h3><p>Open CrownOps in Safari. Open <strong>Share</strong>, choose <strong>Add to Home Screen</strong>, enable <strong>Open as Web App</strong> if shown, then tap <strong>Add</strong>.</p><p class="tiny muted">If the option is missing, use Edit Actions in the share sheet. Sign in again if asked when opening the new icon.</p></section>
  <section class="device-install-card"><h3>Mac</h3><p>In Safari on macOS Sonoma 14 or later, open CrownOps and choose <strong>File &rarr; Add to Dock</strong>. Name it <strong>CrownOps</strong> and add it.</p><p class="tiny muted">On an older Mac, keep using CrownOps in Safari or Chrome. The same account and company records are available.</p></section>
  <section class="device-install-card"><h3>Windows / desktop Chrome</h3><p>Open CrownOps in Chrome. Use the install icon beside the address bar, or open the menu and find <strong>Cast, save and share &rarr; Install page as app</strong>.</p><p class="tiny muted">Menu wording varies by browser version. You can also bookmark CrownOps and keep using it in a browser tab.</p></section>
 </div>`;}

 const responsibilities={
  owner:{title:'Owner setup',items:[['Create separate staff logins and link crew accounts to their employee records.','settings'],['Import the company information workbook and review the proposed changes.','fleet'],['Review pay rates, activate current staff and approve drivers.','team'],['Review booking terms, deposit instructions, invoice details and taxes.','policies'],['Verify production connections, the full booking test and a backup restore.','launch']]},
  dispatch:{title:'Manager / dispatch setup',items:[['Verify the shared service contacts and their phone numbers.','contacts'],['Check each truck against its registration: VIN, fuel, dimensions, rated capacity and exact inspection expiry.','fleet'],['Resolve repair notes with the workshop; record completed work and the review evidence.','fleet'],['Flag missing staff pay rates or driver approvals to the owner.','team'],['Assign the test crew and trucks, then confirm the full blocked time and current pre-use check.','schedule']]},
  sales:{title:'Sales setup',items:[['Review Important contacts and the truck information you need for planning.','contacts'],['Create a clearly labelled TEST inquiry and confirm its inventory, access and timing.','sales'],['Prepare and review a test quote with the correct crew, trucks and charges.','quotes'],['Work with dispatch and the owner to check acceptance, the test deposit and assignments.','schedule']]},
  accounting:{title:'Accounting setup',items:[['Review invoice issuer details, taxes and the approved prices with the owner.','policies'],['Check the test invoice: actual time, minimums, materials and deposit credit.','invoices'],['Review employee time and payroll periods. Keep TEST payroll drafts unapproved.','payroll'],['Verify QuickBooks mappings and totals with the owner before real exports.','connections']]},
  crew:{title:'Employee setup',items:[['Sign in with your own account and confirm the correct employee name.','account'],['Search Important contacts and locate roadside help.','contacts'],['Confirm you can see your assigned test job, instructions and truck.','schedule'],['Complete the actual pre-use check for the assigned truck; report defects to dispatch.','schedule'],['Enter test hours and materials only for the labelled test job, then review the totals with your manager.','invoices']]}
 };

 X.pageRenderers.help=async function(){
  const tasks=responsibilities[S.user.role]||responsibilities.crew;
  const status=installed?'CrownOps is already open as an app.':installPrompt?'Your browser can install CrownOps directly.':'Use the steps below to add the CrownOps icon to your device.';
  const button=!installed&&installPrompt?'<button data-device="install" class="btn-primary">Install CrownOps</button>':'';
  const taskRows=tasks.items.map(([label,page])=>`<li><div class="grow">${h(label)}</div>${allowedPages().includes(page)?`<button class="btn-small" data-page="${h(page)}">Open</button>`:''}</li>`).join('');
  return head('Help & install','One company workspace, accessible from your computer or phone.')+
   `<section class="panel panel-body device-app-header"><img src="/static/icons/app-192.png" width="76" height="76" alt="CrownOps crown icon"><div class="grow"><h2>CrownOps</h2><p class="small muted">${status}</p><p class="tiny muted">Internet and your own staff login are required. Installing the icon does not grant extra access.</p></div>${button}</section><div class="spacer"></div>`+
   panel('Add the app to your device',installInstructions()+`<div class="device-address"><label class="grow"><span class="tiny muted">Company app address</span><input id="device-app-address" readonly value="${h(appAddress())}"></label><button data-device="copy-link" class="btn-soft">Copy app link</button></div>`)+
   `<div class="spacer"></div><div class="grid2">${panel(tasks.title,`<ol class="device-task-list">${taskRows}</ol>`)}${panel('Check your device before real bookings','<ol class="company-steps"><li>Sign in, open the menu and find Important contacts.</li><li>Search a contact and check that the call button is visible.</li><li>Open your assigned test job or the office Fleet screen, as your role permits.</li><li>Scroll long instructions and open a form. Check that every action button remains reachable.</li><li>Ask your manager to verify a TEST update, then sign out and sign back in.</li></ol><p class="tiny muted">If something is cut off, report the device model, browser and a screenshot to your manager.</p>')}</div>`+
   `<p class="small muted" style="margin-top:18px">Use your own login. Keep company records within the approved team workflow. Ask the owner to change access when someone joins, changes roles or leaves.</p>${copyright}`;
 };

 const previousLogin=loginScreen;
 loginScreen=function(){previousLogin();const form=document.querySelector('.login-form');if(form)form.insertAdjacentHTML('beforeend','<button type="button" data-device="install-help" class="btn-text">How to add the app icon</button>'+copyright);};

 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-device]');if(!button)return;event.preventDefault();
  try{
   if(button.dataset.device==='install-help')return showModal('Add CrownOps to your device',installInstructions()+'<p class="small muted">Use your own staff invitation or existing login. Internet is required.</p>',btn('close','Close'),true);
   if(button.dataset.device==='copy-link'){
    const input=document.getElementById('device-app-address');
    if(navigator.clipboard&&window.isSecureContext){try{await navigator.clipboard.writeText(appAddress());toast('App link copied.');return;}catch{}}
    input.focus();input.select();toast('Select and copy the app address.');return;
   }
   if(button.dataset.device==='install'){
    const prompt=installPrompt;if(!prompt)return refresh();button.disabled=true;
    await prompt.prompt();const choice=await prompt.userChoice;installPrompt=null;
    toast(choice.outcome==='accepted'?'Follow the browser prompt to finish installation.':'You can install later using the device steps.');refresh();
   }
  }catch(error){button.disabled=false;modal.open?modalError(error):toast(error.message);}
 });
})();
