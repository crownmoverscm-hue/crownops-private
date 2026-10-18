/* Wix notices use the same source-review and service-planning workflow. */
'use strict';
(function(){
 let offset=0,items=[],status=null;
 const office=()=>['owner','manager','sales','dispatch','accounting'].includes(S.user?.role);
 const editable=()=>['owner','manager','sales','dispatch'].includes(S.user?.role);
 const previous=X.pageRenderers.connections;
 const label=x=>x.event==='quote_accepted'?'Accepted quote':'Paid invoice notice';
 function buttons(x){
  return `<button type="button" data-wix-open="${h(x.id)}">Open notice</button>`+
   (x.calendar_id?` <button type="button" data-wix-calendar="${h(x.calendar_id)}">Plan service</button>`:'');
 }
 X.pageRenderers.connections=async function(){
  const html=await previous();if(!office())return html;
  try{
   const [s,result]=await Promise.all([api('/wix/status'),api('/wix/events?offset='+offset)]);status=s;items=result.items;
   const connection=s.paused?'Paused by owner':!s.configured?'Setup required':s.last_received?'Receiving Wix notices':s.last_test?'Delivery test passed':'Ready for delivery test';
   const rows=items.map(x=>`<article class="panel" style="margin-top:12px"><div class="row wrap"><strong>${h(x.payload.customer||'Client details needed')}</strong>${pill(x.status)}</div><p class="small">${h(label(x))} ${h(x.payload.number)} · ${h(x.payload.title)}</p><p>${h(x.note)}</p><p class="tiny muted">Received ${h(uxTimestampLabel(x.received_at))}${x.deliveries>1?' · Duplicate deliveries safely ignored: '+(x.deliveries-1):''}</p>${buttons(x)}</article>`).join('');
   return html+'<div class="spacer"></div>'+panel('Wix incoming',`<div class="row wrap">${pill(connection)}<button type="button" data-wix-refresh>Refresh</button>${S.user.role==='owner'&&s.configured?`<button type="button" data-wix-pause>${s.paused?'Resume Wix intake':'Pause Wix intake'}</button>`:''}</div><p>Accepted quotes with a clear service date appear in Schedule → All Jobs for planning. Fully booked days and possible duplicates stay here for review. Paid invoice notices need payment reconciliation.</p><p class="small muted">Services from October 19, 2026. Wix title dates use MM/DD/YYYY or YYYY-MM-DD. This connection runs on the server and does not depend on keeping ChatGPT or Wix open.</p><p class="small">Last received: ${h(uxTimestampLabel(s.last_received)||'No notices yet')} · ${result.total} notice${result.total===1?'':'s'}</p>${rows||empty('No Wix notices yet','New accepted quotes and paid invoices will appear after the Wix automations are connected.')}<div class="row wrap" style="margin-top:16px"><button type="button" data-wix-page="${Math.max(0,offset-50)}" ${offset?'':'disabled'}>Previous</button><span>${result.total?offset+1:0}–${Math.min(offset+50,result.total)} of ${result.total}</span><button type="button" data-wix-page="${offset+50}" ${offset+50<result.total?'':'disabled'}>Next</button></div>`);
  }catch(e){return html+panel('Wix incoming',`<div class="callout orange">${h(e.message)}</div><button type="button" data-wix-refresh>Retry</button>`);}
 };
 function notice(x){
  const p=x.payload,canReview=x.event==='quote_accepted'||['owner','manager','accounting'].includes(S.user.role);
  showModal('Wix '+label(x).toLowerCase(),`<p><strong>${h(p.customer||'Client details missing')}</strong> · ${h(p.number)}</p><p>${h(p.title)}</p><div class="callout orange">${h(x.note)}</div><div class="spacer"></div><dl><dt>Email / phone</dt><dd>${h(p.email)} / ${h(p.phone)}</dd><dt>Billing address (not pickup)</dt><dd>${h([p.address,p.city].filter(Boolean).join(', ')||'Not provided')}</dd><dt>Wix document total</dt><dd>${h(p.currency)} ${h(p.total||'Not provided')} — source information, not a verified receipt</dd></dl>${x.review_note?'<p><strong>Review note:</strong> '+h(x.review_note)+'</p>':''}${x.event==='quote_accepted'&&!x.calendar_id&&editable()?`<form id="wix-date-form" data-id="${h(x.id)}"><h3>Confirm the service date</h3><div class="grid2">${field('date','Service date',x.service_date,'date')}${field('time','Start time (optional)',x.start_time,'time')}</div><p class="small">Checks fully booked days and possible duplicates before adding a planning entry.</p><button type="submit" class="btn-primary">Add to schedule for planning</button></form>`:''}${canReview?`<form id="wix-review-form" data-id="${h(x.id)}"><h3>Finish reviewing this notice</h3>${textArea('note','What was checked or resolved?',x.review_note,3)}<button type="submit">Save review note</button></form>`:''}`,btn('close','Close'),true);
 }
 document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-wix-open],[data-wix-calendar],[data-wix-refresh],[data-wix-page],[data-wix-pause]');if(!b)return;
  e.preventDefault();b.disabled=true;
  try{
   if(b.dataset.wixOpen){const x=items.find(x=>x.id===b.dataset.wixOpen);if(x)notice(x);return;}
   if(b.dataset.wixCalendar){S.importedSchedule=await api('/schedule-imports/events');await openImportedJob(b.dataset.wixCalendar);return;}
   if(b.hasAttribute('data-wix-pause'))await api('/wix/settings',{method:'PUT',data:{paused:!status.paused}});
   if(b.hasAttribute('data-wix-page'))offset=Number(b.dataset.wixPage);
   await navigate('connections');
  }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
 });
 document.addEventListener('submit',async e=>{
  const f=e.target.closest('#wix-date-form,#wix-review-form');if(!f)return;e.preventDefault();
  const b=f.querySelector('button[type=submit]');if(b.disabled)return;b.disabled=true;
  try{
   await api('/wix/events/'+encodeURIComponent(f.dataset.id)+(f.id==='wix-date-form'?'/schedule':'/review'),{method:'POST',data:Object.fromEntries(new FormData(f))});
   modal.close();toast(f.id==='wix-date-form'?'Service added for planning.':'Review note saved.');await navigate('connections');
  }catch(ex){modalError(ex);}finally{if(b.isConnected)b.disabled=false;}
 });
})();
