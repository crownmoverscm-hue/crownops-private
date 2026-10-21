/* Employee self-service stays inside My Portal; replacements are loaded only on request. */
'use strict';
(function(){
 const item=nav.find(n=>n[0]==='account');if(item)item[2]='My Portal';
 const securityPage=X.pageRenderers.account;
 let data=null,tab='shifts',cover=null,lastUnread=-1;
 const days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
 const action=(key,label,id='',cls='btn-soft')=>`<button type="button" class="${cls}" data-mp="${key}" data-id="${h(id)}">${label}</button>`;
 const stamp=value=>new Date(value).toLocaleString('en-CA',{timeZone:'America/Toronto',weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
 const hours=minutes=>(minutes/60).toLocaleString('en-CA',{maximumFractionDigits:2});
 const badge=status=>`<span class="mp-status ${['Approved','Acknowledged'].includes(status)?'mp-approved':status==='Declined'?'mp-declined':'mp-pending'}">${h(status)}</span>`;
 const blank=message=>`<p class="mp-empty">${h(message)}</p>`;
 function form(title,kind,body,label='Save'){
  showModal(title,`<form id="mp-form" data-mp-form="${kind}">${body}<div class="login-error" role="alert"></div></form>`,btn('close','Cancel')+`<button class="btn-primary" type="submit" form="mp-form">${label}</button>`);
 }
 async function refresh(){await navigate('account');await updateNotice();}
 function shiftCards(){return data.jobs.length?data.jobs.map(j=>`<article class="mp-card"><div class="mp-card-title"><h3>${h(j.title)}</h3>${badge(j.status)}</div><p class="mp-date">${h(stamp(j.start_at))}</p><p class="muted">Until ${h(stamp(j.end_at))} · Montréal time</p><div class="row wrap">${action('job','View assignment',j.id)}${['Tentative','Confirmed'].includes(j.status)?action('give','Give Shift Away',j.id):''}</div></article>`).join(''):blank('No upcoming assignments. Your next assigned service will appear here.');}
 function hoursPanel(){return !data.period?blank('Your manager needs to set the company payroll cycle before hours can be shown here.'):`<p class="mp-date">${h(uxDateLabel(data.period.start))} – ${h(uxDateLabel(data.period.end))}</p><div class="mp-stats"><div><strong>${hours(data.total_minutes)} h</strong><span>Recorded this cycle</span></div><div><strong>${hours(data.approved_minutes)} h</strong><span>Approved</span></div><div><strong>${hours(data.total_minutes-data.approved_minutes)} h</strong><span>Awaiting review</span></div></div><p class="muted">Through ${h(uxDateLabel(data.as_of))}. These are recorded hours, not scheduled hours. Missing entries are not counted.</p><p>Hours are read-only here. Contact a manager if anything needs correcting.</p>${action('contact-hours','Ask about my hours')}<div class="mp-list">${data.hours.map(t=>`<article class="mp-card"><strong>${h(t.job)}</strong><p>${h(uxDateLabel(t.date))} · ${hours(t.minutes)} hours</p>${badge(t.status)}</article>`).join('')||blank('No hours recorded in this cycle yet.')}</div>`;}
 function availabilityPanel(){
  if(!data.availability)return blank('Ask a manager to link your employee record first.');
  const cfg=data.availability;
  return `<p>Select a day to set your available hours. Unchecked days are not offered for shift coverage. Existing assignments do not change.</p><form data-mp-form="availability"><div class="mp-week">${days.map((day,i)=>{const w=cfg.weekly.find(w=>w.day===i);return `<div class="mp-availability-row ${w?'':'mp-unavailable'}"><label><input type="checkbox" name="day_${i}" ${w?'checked':''}>${day}</label><label ${w?'':'hidden'}>From<input aria-label="${day} available from" name="start_${i}" type="time" value="${h(w?.start||'08:00')}"></label><label ${w?'':'hidden'}>Until<input aria-label="${day} available until" name="end_${i}" type="time" value="${h(w?.end==='24:00'?'00:00':w?.end||'18:00')}"></label></div>`;}).join('')}</div><p class="muted small">Montréal time. An end time of midnight means the end of that day. Split overnight availability across two days.</p><button class="btn-primary" type="submit">Save availability</button><div class="login-error" role="alert"></div></form><hr><h3>Specific-date exceptions</h3><p>Override your usual hours for one date, or mark that date unavailable.</p>${action('exception','Add a date exception')}<div class="mp-list">${cfg.exceptions.map(e=>`<div class="mp-card"><strong>${h(uxDateLabel(e.date))}</strong><p>${e.windows.length?e.windows.map(w=>h(w.start)+'–'+h(w.end)).join(', '):'Not available'}</p>${action('remove-exception','Remove exception',e.date)}</div>`).join('')}</div>`;
 }
 function requestCards(){return data.requests.map(r=>`<article class="mp-card"><div class="mp-card-title"><h3>${h(r.title)}</h3>${badge(r.stale?'Needs manager attention':r.status)}</div><p>${h(stamp(r.start_at))}</p><p>${h(r.source_name)} requested coverage${r.target_name?' · '+h(r.target_name)+' accepted':''}</p>${r.message?`<p class="mp-pre">${h(r.message)}</p>`:''}${r.review_note?`<p><strong>Manager:</strong> ${h(r.review_note)}</p>`:''}<div class="row wrap">${r.can_accept?action('accept','I can cover this',r.id,'btn-primary'):''}${r.can_review?action('review','Review request',r.id):''}${r.can_cancel?action('cancel','Cancel request',r.id):''}</div></article>`).join('')||blank('No shift coverage requests. Open an upcoming assignment and choose Give Shift Away when needed.');}
 function documentsPanel(){return `${data.can_publish?`<div class="row wrap">${action('publish','Create or send a document','','btn-primary')}</div>`:''}<div class="mp-list">${data.documents.map(d=>`<article class="mp-card"><div class="mp-card-title"><h3>${h(d.title)}</h3>${badge(d.ack_at?'Acknowledged':d.requires_ack?'Please acknowledge':d.read_at?'Opened':'New')}</div><p class="muted">${h(stamp(d.created_at))}</p><div class="row wrap">${action('document','Open document',d.id)}<a class="btn-soft" href="/api/my-portal/documents/${encodeURIComponent(d.id)}/download">Download</a></div></article>`).join('')||blank('Documents shared with you will appear here.')}</div>${data.can_publish?`<h3>Documents you sent</h3>${data.published_documents.map(d=>`<div class="mp-card"><strong>${h(d.title)}</strong>${action('document','View acknowledgements',d.id)}</div>`).join('')||blank('No documents sent yet.')}`:''}`;}
 function messagesPanel(){return `${action('contact','Contact a manager','','btn-primary')}<div class="mp-list">${data.help_requests.map(m=>`<article class="mp-card"><h3>${h(m.subject)}</h3><p class="muted">${h(m.name)} · ${h(stamp(m.created_at))}</p><p class="mp-pre">${h(m.body)}</p>${m.reply?`<div class="mp-reply"><strong>Manager reply</strong><p class="mp-pre">${h(m.reply)}</p></div>`:badge('Awaiting manager')}${data.can_manage&&!m.reply?action('reply','Reply',m.id):''}</article>`).join('')}</div>`;}
 X.pageRenderers.account=async function(){
  data=await api('/my-portal');
  const tabs=[['shifts','Upcoming shifts'],['hours','My hours'],['availability','My availability'],['requests','Requests'],['documents','Documents'],['messages','Messages'],['info','My information']];
  const notices=data.notifications.filter(n=>!n.read_at);
  let body='';
  if(tab==='shifts')body=shiftCards();
  if(tab==='hours')body=hoursPanel();
  if(tab==='availability')body=availabilityPanel();
  if(tab==='requests')body=`<div class="row wrap">${data.identity.matched?action('off','Request a Day Off','','btn-primary'):''}${data.can_manage?'<button data-page="timeoff" class="btn-soft">Review team time off</button>':''}</div><h3 class="mp-section">Shift coverage</h3><p>The current assignment stays in place until a manager approves the replacement.</p>${requestCards()}<h3 class="mp-section">My time-off requests</h3>${data.time_off.map(r=>`<div class="mp-card"><strong>${h(uxDateLabel(r.start_date))} – ${h(uxDateLabel(r.end_date))}</strong>${badge(r.status)}${r.review_note?`<p>${h(r.review_note)}</p>`:''}</div>`).join('')||blank('No time-off requests yet.')}`;
  if(tab==='documents')body=documentsPanel();
  if(tab==='messages')body=messagesPanel();
  if(tab==='info')body=(data.identity.matched?`<form data-mp-form="profile">${field('phone','Contact phone',data.profile.phone,'tel')}${field('email','Contact email',data.profile.email,'email')}<p class="muted">Your sign-in email remains ${h(S.user.email)}. Contact an owner to change account access.</p><button type="submit" class="btn-primary">Save contact information</button><div class="login-error" role="alert"></div></form>`:'')+`<div class="mp-section">${action('security','Password & authenticator settings')}</div>`;
  return head('My Portal','Your work, hours, requests and important documents in one place.')+`<div class="mp-portal">${!data.identity.matched?'<div class="callout orange">Your account needs a linked employee record for personal shifts, hours and availability. Documents and messages are still available.</div>':''}${notices.length?`<details class="mp-notices" aria-label="New portal notifications"><summary>${notices.length} new notification${notices.length===1?'':'s'}</summary>${notices.slice(0,8).map(n=>`<div class="mp-notice"><span>${h(n.title)}</span><div class="row wrap">${action('notice','Open',n.id)}${action('read','Mark read',n.id)}</div></div>`).join('')}</details>`:''}<nav class="mp-tabs" aria-label="My Portal sections">${tabs.map(([key,label])=>`<button type="button" data-mp="tab" data-id="${key}" aria-pressed="${tab===key}" class="${tab===key?'selected':''}">${label}</button>`).join('')}</nav><section class="mp-content" aria-label="${h(tabs.find(t=>t[0]===tab)?.[1])}"><h2>${h(tabs.find(t=>t[0]===tab)?.[1])}</h2>${body}</section></div>`;
 };
 async function openDocument(id){const d=await api('/my-portal/documents/'+encodeURIComponent(id));showModal(d.title,`${d.body?`<div class="mp-pre">${h(d.body)}</div>`:`<p>Download ${h(d.filename)} to read this document.</p>`}<p><a class="btn-soft" href="/api/my-portal/documents/${encodeURIComponent(id)}/download">Download document</a></p>${d.ack_at?badge('Acknowledged'):''}${d.recipients?`<h3>Recipients</h3>${d.recipients.map(r=>`<p>${h(r.name)} · ${r.ack_at?'Acknowledged':r.read_at?'Opened':'Not opened'}</p>`).join('')}`:''}`,btn('close','Close')+(d.can_ack&&!d.ack_at?action('acknowledge','I have read this',id,'btn-primary'):''));}
 document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-mp]');if(!b)return;event.preventDefault();const a=b.dataset.mp,id=b.dataset.id;
  try{
   if(a==='tab'){tab=id;return refresh();}
   if(a==='job')return openJob(id);
   if(a==='security'){const html=await securityPage();return showModal('Account security',html,btn('close','Close'));}
   if(a==='give'){
    cover=await api('/my-portal/jobs/'+encodeURIComponent(id)+'/replacements');
    const options=cover.employees.map(e=>`<label class="mp-choice"><input type="checkbox" name="employee_ids" value="${h(e.id)}">${h(e.name)}</label>`).join('');
    if(!options)return showModal('Give Shift Away','<p>No eligible coworkers have explicitly marked themselves available for this entire assignment.</p><p>Contact a manager for emergency coverage. Your assignment has not changed.</p>',btn('close','Close')+action('contact','Contact a manager'));
    return form('Give Shift Away','give',`<p><strong>${h(cover.job.title)}</strong><br>${h(stamp(cover.job.start_at))} – ${h(stamp(cover.job.end_at))}</p><p>Available for the entire assignment, without a schedule conflict, and qualified to replace you.</p>${action('select-all','Select everyone listed')}<div class="mp-list">${options}</div>${textArea('message','Message to selected coworkers (optional)','',3)}<p class="callout">Your shift changes only after a coworker accepts and a manager approves. For an immediate emergency, contact your manager directly.</p>`,'Request coverage');
   }
   if(a==='select-all'){document.querySelectorAll('#mp-form [name="employee_ids"]').forEach(x=>{x.checked=true;});return;}
   if(a==='contact'||a==='contact-hours')return form('Contact a manager','contact',field('subject','Subject',a==='contact-hours'?'Please review my payroll hours':'','','required')+textArea('body','Message','',5),'Send to management');
   if(a==='reply')return form('Reply to employee','reply',`<input type="hidden" name="id" value="${h(id)}">`+textArea('reply','Reply','',5),'Send reply');
   if(a==='off')return form('Request a Day Off','off',field('start_date','First day off',today(),'date','required')+field('end_date','Last day off',today(),'date','required')+textArea('note','Private note to management (optional)','',3),'Send request');
   if(a==='exception')return form('Availability for one date','exception',field('date','Date',today(),'date','required')+select('available','Availability',[['no','Not available'],['yes','Available during these hours']],'no')+field('start','From','08:00','time')+field('end','Until','18:00','time')+'<p class="muted">Midnight as the end time means the end of this date.</p>');
   if(a==='remove-exception'){await api('/my-portal/availability',{method:'PUT',data:{...data.availability,exceptions:data.availability.exceptions.filter(e=>e.date!==id)}});return refresh();}
   if(a==='accept'||a==='cancel'){b.disabled=true;await api('/my-portal/requests/'+encodeURIComponent(id)+'/'+a,{method:'POST',data:{}});toast(a==='accept'?'Accepted. Waiting for manager approval.':'Request cancelled.');return refresh();}
   if(a==='review'){const r=data.requests.find(r=>r.id===id);return form('Review coverage','review',`<input type="hidden" name="id" value="${h(id)}"><p>${h(r.source_name)} → ${h(r.target_name||'No coworker accepted yet')}</p><p>${h(r.title)} · ${h(stamp(r.start_at))}</p><p>Review team qualifications, travel between jobs and payroll impact before approving.</p>`+select('decision','Decision',[...(r.status==='Awaiting manager'&&!r.stale?[['Approved','Approve replacement']]:[]),['Declined','Decline request']],'Declined')+textArea('note','Note to the employees','',3),'Save decision');}
   if(a==='publish'){
    const recipients=await api('/my-portal/document-recipients');
    return form('Create or send a document','publish',field('title','Document title','','text','required')+select('audience','Send to',[['selected','Selected staff'],['all','All active staff']],'selected')+`<div class="mp-recipients">${recipients.map(e=>`<label class="mp-choice"><input type="checkbox" name="recipients" value="${h(e.id)}">${h(e.name)} · ${h(e.role)}</label>`).join('')}</div>`+textArea('body','Write a document here, or attach a file below','',6)+'<label class="field">Attach PDF, DOCX, JPG, PNG or TXT (up to 10 MB)<input type="file" name="file" accept=".pdf,.docx,.jpg,.jpeg,.png,.txt"></label><p class="small muted">An attached file replaces the text above. Publish a new document when instructions change so earlier acknowledgements remain accurate.</p><label class="mp-choice"><input name="requires_ack" type="checkbox" checked>Ask recipients to confirm they have read it</label><p>Recipients receive an in-app notification in My Portal.</p>','Publish & notify');
   }
   if(a==='document')return openDocument(id);
   if(a==='acknowledge'){b.disabled=true;await api('/my-portal/documents/'+encodeURIComponent(id)+'/acknowledge',{method:'POST',data:{}});modal.close();toast('Acknowledgement saved.');return refresh();}
   if(a==='read'){await api('/my-portal/notifications/'+encodeURIComponent(id)+'/read',{method:'POST',data:{}});return refresh();}
   if(a==='notice'){const n=data.notifications.find(n=>n.id===id);await api('/my-portal/notifications/'+encodeURIComponent(id)+'/read',{method:'POST',data:{}});tab=n.kind==='document'?'documents':n.kind==='message'?'messages':'requests';await refresh();if(n.kind==='document')return openDocument(n.object_id);}
  }catch(e){b.disabled=false;modal.open?modalError(e):toast(e.message);}
 });
 document.addEventListener('change',event=>{if(event.target.matches('.mp-availability-row input[type=checkbox]')){const row=event.target.closest('.mp-availability-row');row.classList.toggle('mp-unavailable',!event.target.checked);row.querySelectorAll('label:not(:first-child)').forEach(x=>{x.hidden=!event.target.checked;});}if(event.target.matches('[data-mp-form="publish"] [name="audience"]'))document.querySelector('.mp-recipients').hidden=event.target.value==='all';});
 document.addEventListener('submit',async event=>{
  const f=event.target;if(!f.matches('[data-mp-form]'))return;event.preventDefault();const b=event.submitter;b.disabled=true;const kind=f.dataset.mpForm,fd=new FormData(f),v=Object.fromEntries(fd);f.querySelector('.login-error').textContent='';
  try{
   if(kind==='profile')await api('/my-portal/profile',{method:'PATCH',data:v});
   if(kind==='availability'){const weekly=days.flatMap((_,i)=>v['day_'+i]?[{day:i,start:v['start_'+i],end:v['end_'+i]==='00:00'?'24:00':v['end_'+i]}]:[]);await api('/my-portal/availability',{method:'PUT',data:{...data.availability,weekly}});}
   if(kind==='exception')await api('/my-portal/availability',{method:'PUT',data:{...data.availability,exceptions:[...data.availability.exceptions.filter(e=>e.date!==v.date),{date:v.date,windows:v.available==='yes'?[{start:v.start,end:v.end==='00:00'?'24:00':v.end}]:[]}]}});
   if(kind==='give')await api('/my-portal/jobs/'+encodeURIComponent(cover.job.id)+'/give-away',{method:'POST',data:{version:cover.job.version,employee_ids:fd.getAll('employee_ids'),message:v.message}});
   if(kind==='review')await api('/my-portal/requests/'+encodeURIComponent(v.id)+'/review',{method:'POST',data:{decision:v.decision,note:v.note}});
   if(kind==='contact')await api('/my-portal/contact-manager',{method:'POST',data:v});
   if(kind==='reply')await api('/my-portal/messages/'+encodeURIComponent(v.id)+'/reply',{method:'POST',data:{reply:v.reply}});
   if(kind==='off')await api('/my-portal/time-off',{method:'POST',data:v});
   if(kind==='publish'){
    const recipients=v.audience==='all'?'all':fd.getAll('recipients');
    if(v.file?.size){const upload=new FormData();upload.set('title',v.title);upload.set('recipients',JSON.stringify(recipients));upload.set('requires_ack',String(Boolean(v.requires_ack)));upload.set('file',v.file);await api('/my-portal/documents/upload',{method:'POST',form:upload});}
    else await api('/my-portal/documents',{method:'POST',data:{title:v.title,body:v.body,recipients,requires_ack:Boolean(v.requires_ack)}});
   }
   if(modal.open)modal.close();toast(kind==='give'?'Coverage requested. Your assignment remains until approval.':kind==='publish'?'Document published. Recipients have an in-app notification.':'Saved.');await refresh();
  }catch(e){f.querySelector('.login-error').textContent=e.message;}finally{b.disabled=false;}
 });
 async function updateNotice(){
  if(!S.user||demo()||!navigator.onLine)return;
  try{const n=await api('/my-portal/notifications');const top=document.querySelector('.topbar');let b=document.querySelector('[data-mp-notifications]');if(top&&!b){b=document.createElement('button');b.type='button';b.className='btn-soft mp-notification-button';b.dataset.page='account';b.dataset.mpNotifications='';top.append(b);}if(b){b.innerHTML=icon('users')+'<span class="mp-notice-label">My Portal</span>'+(n.unread?'<span class="mp-notice-count">'+n.unread+'</span>':'');b.setAttribute('aria-label',n.unread?'My Portal, '+n.unread+' unread notifications':'My Portal');}if(lastUnread>=0&&n.unread>lastUnread)toast('You have a new notification in My Portal.');lastUnread=n.unread;}catch{/* A temporary network failure must not erase the current screen. */}
 }
 const oldShell=shell;shell=function(){oldShell();updateNotice();};
 setInterval(()=>{if(!document.hidden)updateNotice();},60000);
})();
