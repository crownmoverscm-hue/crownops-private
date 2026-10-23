/* Clear current actions and accessible read views; no new permissions or background work. */
'use strict';
(function(){
 const phone=()=>matchMedia('(max-width:760px)').matches;
 const smallNavigation=()=>matchMedia('(max-width:1023px)').matches;
 const dirtyForms=new WeakSet();
 function finishTables(container){
  if(!container)return;
  container.querySelectorAll('table').forEach(table=>{
   if(table.classList.contains('finish-mobile-table')||table.closest('.staff-directory-table,.incident-report-table,.expense-ledger-table,#qs-document,.quote-layout,.client-lines')||table.querySelector('input,select,textarea'))return;
   const headings=[...(table.tHead?.rows[0]?.cells||[])];if(headings.length<3||headings.length>10)return;
   const records=[...table.tBodies].flatMap(body=>[...body.rows]);if(!records.length||records.some(row=>row.cells.length!==headings.length||[...row.cells].some(cell=>cell.colSpan!==1||cell.rowSpan!==1)))return;
   table.classList.add('finish-mobile-table');table.setAttribute('role','table');
   table.closest('.table-wrap')?.classList.add('finish-mobile-wrap');
   table.tHead.setAttribute('role','rowgroup');table.tHead.rows[0].setAttribute('role','row');
   headings.forEach(cell=>{cell.scope='col';cell.setAttribute('role','columnheader');});
   [...table.tBodies].forEach(body=>body.setAttribute('role','rowgroup'));
   records.forEach(row=>{
    row.setAttribute('role','row');[...row.cells].forEach((cell,index)=>{
     cell.setAttribute('role','cell');
     const label=(headings[index].textContent||'').trim()||(index===headings.length-1?'Actions':'Details');
     cell.dataset.label=label;
     const tag=document.createElement('span');tag.className='finish-cell-label';tag.setAttribute('aria-hidden','true');tag.textContent=label;cell.prepend(tag);
     if(index===0)cell.classList.add('finish-primary-cell');
     if(index===headings.length-1&&(!headings[index].textContent.trim()||/^(?:actions?|options?)$/i.test(label)))cell.classList.add('finish-actions-cell');
    });
   });
  });
 }
 function finishNavigation(){
  const sidebar=document.getElementById('sidebar');if(!sidebar)return;
  const open=sidebar.classList.contains('open'),compact=smallNavigation();
  sidebar.setAttribute('role','navigation');sidebar.setAttribute('aria-label','CrownOps workspace');
  sidebar.inert=compact&&!open;
  document.documentElement.classList.toggle('finish-navigation-open',open&&compact);
  if(!sidebar.querySelector('[data-workspace-close-menu]'))sidebar.querySelector('.brand')?.insertAdjacentHTML('beforeend','<button type="button" class="finish-menu-close btn-icon" data-workspace-close-menu aria-label="Close navigation">'+icon('close')+'</button>');
  const top=document.querySelector('.topbar');
  if(top&&!top.querySelector('.finish-mobile-brand'))top.querySelector('[data-action=menu]')?.insertAdjacentHTML('afterend','<div class="finish-mobile-brand"><img src="/static/icon.svg?v=15.0" alt="" width="34" height="34"><div><strong>CrownOps</strong><span data-workspace-location></span></div></div>');
  const location=top?.querySelector('[data-workspace-location]');
  if(location)location.textContent=document.querySelector('#content h1')?.textContent||uxLabel(S.page);
  sidebar.querySelectorAll('.nav-button[data-page]').forEach(button=>{
   if(button.dataset.page===S.page)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  });
  const label=sidebar.querySelector('.workspace-label');
  if(label&&!label.dataset.finished){
   const notes={owner:'Sales, operations and company oversight.',manager:'Coordinate the team and your own services.',sales:'Clients, estimates and next actions.',crew:'Your assigned services and team information.'};
   label.innerHTML='<span class="dot"></span>Team workspace<div class="tiny">'+h(notes[S.user?.role]||'Use your own team account.')+'</div>';label.dataset.finished='1';
  }
  const menu=document.querySelector('[data-action=menu]');if(menu){menu.setAttribute('aria-controls','sidebar');menu.setAttribute('aria-expanded',String(open));}
 }
 function finishConnection(){
  let banner=document.getElementById('workspace-connection');
  if(!banner){banner=document.createElement('div');banner.id='workspace-connection';banner.className='finish-connection';banner.setAttribute('role','status');document.body.append(banner);}
  banner.hidden=navigator.onLine;
  banner.textContent='You are offline. Keep this screen open. Reconnect before saving; pending changes are not confirmed.';
 }
 function finishSales(){
  const content=document.getElementById('content');if(S.page!=='sales'||!content)return;
  const search=content.querySelector('.filterbar:has(#crm-client-search)');
  if(search&&phone())content.querySelector('.page-head')?.after(search);
  const items=[content.querySelector('.intake-status')];
  for(const item of items){
   if(!item)continue;
   const wrapper=item.parentElement;
   if(phone()&&!wrapper.matches('.finish-sales-details')){
    const details=document.createElement('details');details.className='finish-sales-details';
    const summary=document.createElement('summary');
    summary.textContent=item.matches('.intake-status')?(item.querySelector('strong')?.textContent||'Inquiry import'):'Inquiry import';
    details.open=item.matches('.intake-status')&&/needs attention|paused/i.test(summary.textContent);
    item.before(details);details.append(summary,item);
   }else if(!phone()&&wrapper.matches('.finish-sales-details'))wrapper.replaceWith(item);
  }
 }
 function finishPage(){
  const content=document.getElementById('content');if(!content?.querySelector('h1'))return;
  content.dataset.workspaceRole=S.user?.role||'';content.dataset.workspacePage=S.page;
  if(S.user?.role==='crew'&&S.page==='schedule')content.querySelector('.page-head h1').textContent=S.jobScope==='mine'?'My Jobs':'All Jobs';
  if(S.user?.role==='crew'&&S.page==='invoices')content.querySelector('.page-head p').textContent='Prepare and follow invoices for your assigned services.';
  finishNavigation();finishTables(content);finishSales();finishConnection();
  for(const [selector,label] of [['[data-action=week-prev]','Previous week'],['[data-action=week-next]','Next week'],['[data-action=page-prev]','Previous page'],['[data-action=page-next]','Next page'],['[data-v5=off-prev]','Previous time-off period'],['[data-v5=off-next]','Next time-off period']])content.querySelectorAll(selector).forEach(button=>button.setAttribute('aria-label',label));
  if(S.page==='schedule'&&['crew','manager'].includes(S.user?.role)&&!content.querySelector('.finish-route-tip')){
   const text=S.user.role==='crew'?(S.jobScope==='mine'?'Open an assigned service for the crew brief, route, photos, materials and truck check.':'Use My Jobs to prepare your assigned services. All Jobs shows the wider company plan.'):(S.jobScope==='mine'?'These are your crew assignments. Your Manager access stays available while you lead the service.':'Use All Jobs to coordinate the team, or My Jobs to prepare the services you are working on.');
   const note=document.createElement('p');note.className='finish-route-tip';note.textContent=text;
   (content.querySelector('.job-scope')||content.querySelector('.page-head'))?.insertAdjacentElement('afterend',note);
  }
  if(S.page==='help'){
   const list=content.querySelector('.device-task-list');list?.closest('.panel')?.classList.add('finish-help-start');
   list?.querySelector('li button[data-page]')?.classList.add('btn-primary');
   const tasks=list?.closest('.grid2'),installation=content.querySelector('.device-install-grid')?.closest('.panel');
   if(tasks&&installation&&tasks.compareDocumentPosition(installation)&Node.DOCUMENT_POSITION_PRECEDING){tasks.classList.add('finish-help-grid');installation.before(tasks);}
  }
 }
 const previousShell=shell;
 shell=function(){const result=previousShell();finishNavigation();finishConnection();return result;};
 const previousNavigate=navigate;
 navigate=async function(page){const changed=S.page!==page;const result=await previousNavigate(page);finishPage();if(changed&&smallNavigation()&&!modal.open)scrollTo({top:0,behavior:'instant'});return result;};
 const previousModal=showModal;
 showModal=function(...args){
  const result=previousModal(...args),title=modal.querySelector('.modal-head h2');
  if(title){title.id='workspace-dialog-title';modal.setAttribute('aria-labelledby',title.id);}
  const invoiceForm=modal.querySelector('#invoice-form'),save=invoiceForm?.querySelector('button[type=submit]'),footer=modal.querySelector('.modal-foot');
  if(save&&footer){save.setAttribute('form','invoice-form');footer.append(save);}
  modal.querySelector('#modal-error')?.setAttribute('role','alert');finishTables(modal);fitDialog();return result;
 };
 const previousLogin=loginScreen;
 loginScreen=function(...args){
  document.documentElement.classList.remove('finish-navigation-open');
  const result=previousLogin(...args),code=document.querySelector('[name=mfa_code]');
  if(code){code.maxLength=6;code.pattern='[0-9]{6}';code.setAttribute('aria-describedby','authenticator-help');code.insertAdjacentHTML('afterend','<small id="authenticator-help">Open your authenticator app and use the current six-digit CrownOps code. It changes every 30 seconds. Leave blank if you have not enabled it.</small>');}
  return result;
 };
 document.addEventListener('click',event=>{
  if(event.target.closest('[data-workspace-close-menu]')){document.getElementById('sidebar')?.classList.remove('open');finishNavigation();document.querySelector('[data-action=menu]')?.focus();return;}
  const menu=event.target.closest('[data-action=menu]'),sidebar=document.getElementById('sidebar');
  if(menu){queueMicrotask(()=>{finishNavigation();if(sidebar?.classList.contains('open'))(sidebar.querySelector('[aria-current=page]')||sidebar.querySelector('button'))?.focus();});return;}
  if(sidebar?.classList.contains('open')&&!event.target.closest('#sidebar')){sidebar.classList.remove('open');finishNavigation();}
 });
 document.addEventListener('click',event=>{
  const sidebar=document.getElementById('sidebar');
  if(sidebar?.classList.contains('open')&&matchMedia('(max-width:1023px)').matches&&!event.target.closest('#sidebar,[data-action=menu]')){
   event.preventDefault();event.stopImmediatePropagation();sidebar.classList.remove('open');finishNavigation();
  }
 },true);
 document.addEventListener('keydown',event=>{
  const sidebar=document.getElementById('sidebar');if(event.key==='Escape'&&sidebar?.classList.contains('open')&&!modal.open){sidebar.classList.remove('open');finishNavigation();document.querySelector('[data-action=menu]')?.focus();}
  if(event.key==='Tab'&&sidebar?.classList.contains('open')&&matchMedia('(max-width:1023px)').matches){
   const controls=[...sidebar.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled)')].filter(element=>element.getClientRects().length);
   const first=controls[0],last=controls.at(-1);
   if(first&&event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
   else if(last&&!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
 });
 // Closing an edited form must not silently discard the staff member's work.
 function markEdited(event){const form=event.target.closest('#modal form');if(form)dirtyForms.add(form);}
 document.addEventListener('input',markEdited);document.addEventListener('change',markEdited);
 const hasEdits=()=>modal.open&&[...modal.querySelectorAll('form')].some(form=>dirtyForms.has(form));
 function allowClose(){return !hasEdits()||confirm('Discard the changes in this form? Choose Cancel to keep editing.');}
 window.addEventListener('click',event=>{
  if(event.target.closest('#modal [data-action=close],#modal [data-move-return]')&&!allowClose()){event.preventDefault();event.stopImmediatePropagation();}
 },true);
 window.addEventListener('cancel',event=>{if(event.target===modal&&!allowClose()){event.preventDefault();event.stopImmediatePropagation();}},true);
 window.addEventListener('beforeunload',event=>{if(hasEdits()){event.preventDefault();event.returnValue='';}});
 function fitDialog(){
  const viewport=window.visualViewport;
  const shrink=phone()&&viewport&&viewport.scale===1&&innerHeight-viewport.height>120;
  modal.style.setProperty('--visible-dialog-height',shrink?Math.max(180,viewport.height-16)+'px':'94dvh');
  modal.style.setProperty('--visible-dialog-top',shrink?viewport.offsetTop+8+'px':'auto');
  modal.classList.toggle('finish-keyboard-open',Boolean(shrink));
 }
 window.visualViewport?.addEventListener('resize',fitDialog);window.visualViewport?.addEventListener('scroll',fitDialog);
 window.addEventListener('resize',()=>{finishNavigation();finishSales();fitDialog();});
 window.addEventListener('offline',finishConnection);window.addEventListener('online',finishConnection);
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',finishPage,{once:true});else finishPage();
 window.CrownWorkspaceFinish={refresh:finishPage};
})();
