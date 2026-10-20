/* One service conversation, a separate handler, and a client-wide history. */
'use strict';
const IB={drafts:{},tab:'tasks',view:'followups',callStatus:'all',tasks:{},calls:{},historyKind:'all',scope:'team',status:'open',q:'',offset:0,lid:null,detail:null,historyOffset:0,request:null};
const inboxFollowups=X.pageRenderers.followups;
const inboxNav=nav.find(n=>n[0]==='communications');if(inboxNav)inboxNav[2]='Inbox';
function inboxTabs(){return '<nav class="inbox-tabs" aria-label="Inbox sections">'+[['tasks','Tasks'],['emails','Emails'],['forms','Forms'],['calls','Calls']].map(([v,n])=>`<button data-inbox-tab="${v}" class="${IB.tab===v?'selected':''}" aria-current="${IB.tab===v?'page':'false'}">${n}</button>`).join('')+'</nav>';}
function inboxPages(total,offset,size,mode){if(total<=size)return '';return `<div class="inbox-pagination"><span>${total?offset+1:0}–${Math.min(offset+size,total)} of ${total}</span><button data-inbox-page="${mode}" data-offset="${Math.max(0,offset-size)}" ${offset===0?'disabled':''}>Previous</button><button data-inbox-page="${mode}" data-offset="${offset+size}" ${offset+size>=total?'disabled':''}>Next</button></div>`;}
function inboxEvents(events,publicView=false){return events.map(e=>`<article class="inbox-event ${e.direction==='client'?'from-client':''}"><div class="row wrap"><strong>${h(e.title)}</strong>${e.status?pill(e.status):''}${!publicView?'<span class="inbox-visibility">'+(e.visibility==='staff'?'Internal · staff only':'Client-visible')+'</span>':''}</div><div class="tiny muted">${h(uxTimestampLabel(e.at))}${e.actor?' · '+h(e.actor):''}</div>${e.body?'<p class="inbox-body">'+h(e.body)+'</p>':''}</article>`).join('')||'<p class="muted">No interactions yet.</p>';}
function inboxDetail(d){
 const l=d.client,c=d.conversation,can=c.handler_id===S.user.id||c.task_handlers?.includes(S.user.id)||['owner','manager'].includes(S.user.role);
 const next=c.status==='Needs reply'?'Review the latest client update and record the next step.':c.status==='Waiting on client'?'Check back on '+uxDateLabel(c.remind_on)+'.':'Keep the outcome and next step in this service history.';
 const other=d.services.length>1?`<label>Service<select id="inbox-service">${d.services.map(s=>`<option value="${h(s.id)}" ${s.id===l.id?'selected':''}>${h(uxDateLabel(s.move_date,'Date to confirm'))} · ${h(s.stage)}</option>`).join('')}</select></label>`:'';
 const actions=!c.handler_id&&!c.task_handlers?.includes(S.user.id)?'<button class="btn-primary" data-inbox-action="claim">Claim client follow-up</button>':`<span class="pill">Handled by ${h(c.handler_name||S.user.name)}</span>`;
 return `<section class="inbox-detail" aria-label="Client conversation"><button class="inbox-back" data-inbox-action="back">← Back to follow-ups</button><header class="inbox-detail-head"><div><h2>${h(l.client_name)}</h2><p class="muted">Move: ${h(uxDateLabel(l.move_date,'Date to confirm'))} · Service owner: ${h(c.service_owner||'Unassigned')}</p></div><button data-inbox-action="client">Open client</button></header><div class="row wrap inbox-ownership">${actions}${c.handler_id===S.user.id||['owner','manager'].includes(S.user.role)?'<button data-inbox-action="handoff">Hand over</button>':''}${pill(c.status)}</div>${other}<div class="inbox-next"><strong>Next step</strong><p>${h(next)}</p></div><div class="row wrap inbox-outcomes">${can?`<button data-inbox-action="status" data-value="In progress">In progress</button><button data-inbox-action="status" data-value="Waiting on client">Waiting on client</button><button data-inbox-action="status" data-value="Resolved">Mark resolved</button>`:''}<button data-inbox-action="refresh">Refresh history</button></div><div class="inbox-history" tabindex="0" aria-label="Service interaction history">${inboxEvents(d.events)}</div>${inboxPages(d.total,d.offset,40,'history')}${can?`<form id="inbox-compose"><div class="grid2"><label>Write<select name="kind" id="inbox-compose-kind"><option value="email">Email reply</option><option value="portal">Portal reply</option><option value="note">Internal note</option><option value="call">Call outcome</option></select></label><label id="inbox-subject">Subject<input name="subject" value="Your Crown Movers service" maxlength="300"></label></div><p class="small muted" id="inbox-compose-hint">Prepare an email, then review the recipient and message before sending.</p><label>Message<textarea name="body" rows="4" maxlength="10000" required></textarea></label><label id="inbox-share" class="checkbox-label" hidden><input type="checkbox" name="share_with_client">Share this call recap in the client portal</label><div class="row wrap"><button type="submit" class="btn-primary" id="inbox-compose-submit">Review email</button><button type="button" data-inbox-action="files">Photos & move details</button></div></form>`:'<p class="callout">Claim this client follow-up to reply or record an outcome. The service owner stays unchanged.</p>'}</section>`;
}
function inboxSubtabs(){
 const choices=IB.tab==='emails'?[['followups','Incoming'],['drafts','Drafts & delivery'],['sent','Sent']]:IB.tab==='calls'?[['followups','Follow-ups'],['log','Call log']]:[];
 return choices.length?'<div class="row wrap inbox-subtabs">'+choices.map(([v,n])=>`<button data-inbox-view="${v}" class="${IB.view===v?'selected':''}">${n}</button>`).join('')+'</div>':'';
}
function inboxTaskCard(t){
 const can=t.owner_id===S.user.id||['owner','manager'].includes(S.user.role),done=t.status==='Done';
 return `<article class="inbox-task-card" data-task-id="${h(t.id)}"><div><div class="row wrap">${pill({emails:'Email',calls:'Call',forms:'Form / portal',tasks:'Task'}[t.channel])}${pill(done?'Completed':t.bucket==='overdue'?'Overdue':t.bucket==='today'?'Due today':'Open')}</div><h3>${h(t.client_name||'Internal / unmatched')}</h3><p class="small muted">${h(t.contact_phone||t.email||'')}${t.call_status?' · '+h(t.call_status):''}</p><p class="strong">${h(t.title)}</p><p class="muted">Due: ${h(uxDateLabel(t.due_date,'No due date'))} · ${h(t.owner_name||'Unclaimed')}</p>${t.move_date?'<p class="small muted">Move / service: '+h(uxDateLabel(t.move_date))+'</p>':''}</div><div class="row wrap inbox-task-actions">${!done&&!t.owner_id?`<button data-inbox-task="${h(t.id)}" data-task-action="claim">Claim</button>`:''}${can?`<button data-inbox-task="${h(t.id)}" data-task-action="assign">Assign</button>`:''}<button data-inbox-task="${h(t.id)}" data-task-action="open">Open</button>${t.lead_id?`<button data-inbox-client="${h(t.client_id||'')}" data-lead-id="${h(t.lead_id)}">Client</button>`:''}${can?`<button data-inbox-task="${h(t.id)}" data-task-action="${done?'reopen':'done'}">${done?'Reopen':'Done'}</button>`:''}</div></article>`;
}
X.pageRenderers.communications=async()=>{
 if(!['owner','manager','sales','dispatch'].includes(S.user.role))return empty('Office inbox','This section is for the sales and operations team.');
 const top=head('Inbox','Every follow-up. One clear next step.','<button class="btn-primary" data-action="new-lead">'+icon('plus')+'New inquiry</button>')+inboxTabs();
 if(IB.lid){IB.detail=await api('/inbox/'+encodeURIComponent(IB.lid)+'?offset='+IB.historyOffset);return top+inboxDetail(IB.detail);}
 const queue=IB.view==='followups'||!['emails','calls'].includes(IB.tab);
 const controls=`<form id="inbox-search" class="inbox-toolbar"><input name="q" value="${h(IB.q)}" placeholder="Search client, phone or follow-up" aria-label="Search inbox"><button type="submit">Search</button>${queue?`<select id="inbox-scope" aria-label="Follow-up ownership">${[['team','Team'],['mine','Mine'],['unclaimed','Unclaimed']].map(([v,n])=>`<option value="${v}" ${IB.scope===v?'selected':''}>${n}</option>`).join('')}</select><select id="inbox-status" aria-label="Follow-up status">${[['open','Open'],['today','Due today'],['overdue','Overdue'],['upcoming','Upcoming'],['completed','Completed'],['all','All statuses']].map(([v,n])=>`<option value="${v}" ${IB.status===v?'selected':''}>${n}</option>`).join('')}</select>`:IB.tab==='calls'?`<select id="inbox-call-status" aria-label="Call status">${['all','Answered','Missed','Voicemail','Recorded'].map(v=>`<option value="${v}" ${IB.callStatus===v?'selected':''}>${v==='all'?'All calls':v}</option>`).join('')}</select>`:''}</form>`;
 if(!queue&&IB.tab==='emails'){
  const r=await api('/inbox/activity?kind='+IB.view+'&q='+encodeURIComponent(IB.q)+'&offset='+IB.offset);
  return top+inboxSubtabs()+controls+panel(IB.view==='drafts'?'Drafts & delivery issues':'Sent email',r.items.map(m=>`<div class="inbox-activity"><div><strong>${h(m.client_name)}</strong><p>${h(m.subject)}</p><div class="tiny muted">${h(uxTimestampLabel(m.created_at))}</div>${m.error?'<p class="error">'+h(m.error)+'</p>':''}</div>${pill(m.status)}<button data-inbox-mail="${h(m.id)}">Review</button></div>`).join('')||empty('Nothing in this view','Try another search.'))+inboxPages(r.total,r.offset,30,'list');
 }
 if(!queue&&IB.tab==='calls'){
  const r=await api('/inbox-calls?q='+encodeURIComponent(IB.q)+'&status='+IB.callStatus+'&offset='+IB.offset);IB.calls=Object.fromEntries(r.items.map(c=>[c.id,c]));
  return top+inboxSubtabs()+'<p class="muted">Imported call notices and recorded outcomes. A complete UnifiCX feed still needs its phone-system connection.</p>'+controls+panel('Call log',r.items.map(c=>`<article class="inbox-task-card"><div><h3>${h(c.client_name)}</h3><p>${h(c.phone)} ${pill(c.status)}</p><p class="small muted">${h(uxTimestampLabel(c.created_at))}</p><p class="inbox-body">${h(c.body)}</p></div><div class="row wrap">${c.lead_id?`<button data-inbox-open="${h(c.lead_id)}">Open</button><button data-inbox-client="${h(c.client_id||'')}" data-lead-id="${h(c.lead_id)}">Client</button>`:''}${c.linkable?`<button data-inbox-link-call="${h(c.id)}">${c.lead_id?'Change client':'Link client'}</button>`:''}</div></article>`).join('')||empty('No call records','Calls appear here when imported or recorded.'))+inboxPages(r.total,r.offset,30,'list');
 }
 const r=await api('/inbox-tasks?channel='+IB.tab+'&scope='+IB.scope+'&status='+IB.status+'&q='+encodeURIComponent(IB.q)+'&offset='+IB.offset);IB.tasks=Object.fromEntries(r.items.map(t=>[t.id,t]));
 const summary=`<section class="panel"><div class="panel-head"><h2>${IB.tab==='tasks'?'Tasks · all follow-ups':{emails:'Email follow-ups',forms:'Forms & portal updates',calls:'Call follow-ups'}[IB.tab]}</h2></div><div class="inbox-summary">${[['open','Open'],['today','Due today'],['overdue','Overdue'],['upcoming','Upcoming']].map(([v,n])=>`<button data-inbox-bucket="${v}"><strong>${r.summary[v]}</strong><span>${n}</span></button>`).join('')}</div></section>`;
 const categories='';
 return top+inboxSubtabs()+summary+categories+controls+(IB.tab==='forms'?'<p class="muted">Quote requests, updated details, photo reviews and client portal messages.</p>':'')+`<section aria-label="Follow-ups">${r.items.map(inboxTaskCard).join('')||empty('All clear in this view','Try Team or another status to see more follow-ups.')}</section>`+inboxPages(r.total,r.offset,30,'list');
};
const inboxNavigate=navigate;
navigate=async function(page){if(page==='followups'){IB.tab='tasks';page='communications';}if(page==='outbox'){IB.tab='emails';IB.view='drafts';page='communications';}if(page==='calls'){IB.tab='calls';IB.view='log';page='communications';}const result=await inboxNavigate(page);const f=document.getElementById('inbox-compose'),d=IB.drafts[IB.lid];if(f&&d){for(const k of ['kind','subject','body'])f.elements[k].value=d[k];f.elements.share_with_client.checked=d.share_with_client;f.elements.kind.dispatchEvent(new Event('change',{bubbles:true}));}return result;};
async function inboxOpen(lid){IB.lid=lid;IB.historyOffset=0;IB.request=null;if(modal.open)modal.close();await navigate('communications');}
async function inboxMail(mid){
 const m=await api('/inbox/mail/'+encodeURIComponent(mid));IB.mail=m;
 const editable=m.status==='Draft'&&m.event_key.startsWith('conversation:')&&m.can_handle;
 const content=editable?`<form id="inbox-edit-mail"><label>Subject<input name="subject" value="${h(m.subject)}" required maxlength="300"></label><label>Message<textarea name="body" rows="8" required maxlength="10000">${h(m.body)}</textarea></label><button type="submit">Save changes</button></form>`:`<h3>${h(m.subject)}</h3><div class="inbox-body message-draft">${h(m.body)}</div>`;
 showModal('Review email',`<p><strong>To:</strong> ${h(m.recipient)}</p>${pill(m.status)}${content}${m.error?'<p class="callout">'+h(m.error)+'</p>':''}<p class="small muted">An email appears in the client history after the email provider confirms sending.</p>${!m.can_handle?'<p class="callout">'+(m.conversation.handler_id?'Ask '+h(m.conversation.handler_name)+' to send this reply or hand over the conversation.':'Claim the conversation before sending this email.')+'</p>':''}`,btn('close','Close')+(!m.can_handle&&!m.conversation.handler_id?'<button data-inbox-mail-claim>Claim client follow-up</button>':'')+(['Draft','Approved'].includes(m.status)&&m.can_handle?'<button class="btn-primary" data-inbox-send>Send email</button>':''),true);
}
async function inboxClientHistory(cid,offset=0){
 const r=await api('/crm/clients/'+encodeURIComponent(cid)+'/history?offset='+offset+'&kind='+IB.historyKind);
 showModal('Client interaction history', '<p class="muted">All linked services. Newest first. Internal notes stay staff-only.</p><label>Interaction type<select id="inbox-history-kind" data-client-id="'+h(cid)+'">'+[['all','All interactions'],['emails','Emails'],['forms','Forms & portal'],['calls','Calls'],['notes','Internal notes']].map(([v,n])=>'<option value="'+v+'" '+(IB.historyKind===v?'selected':'')+'>'+n+'</option>').join('')+'</select></label>'+r.items.map(e=>'<div class="tiny muted">Service: '+h(uxDateLabel(e.service_date,'Date to confirm'))+' <button class="btn-small" data-inbox-open="'+h(e.lead_id)+'">Open history</button></div>'+inboxEvents([e])).join('')+`<div class="inbox-pagination"><button data-inbox-client-history="${h(cid)}" data-offset="${Math.max(0,offset-40)}" ${offset===0?'disabled':''}>Previous</button><span>${r.total} interactions</span><button data-inbox-client-history="${h(cid)}" data-offset="${offset+40}" ${offset+40>=r.total?'disabled':''}>Next</button></div>`,btn('close','Close'),true);
}
const inboxClient=crmOpenClient;
crmOpenClient=async function(cid){await inboxClient(cid);modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin',`<button class="btn-primary" data-inbox-client-history="${h(cid)}">All interactions</button>`);};
const inboxLead=openLead;
openLead=async function(lid){await inboxLead(lid);if(['owner','manager','sales','dispatch'].includes(S.user.role))modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin',`<button data-inbox-open="${h(lid)}">Messages & history</button>`);};
const inboxAttention=uxAttentionButton;
uxAttentionButton=function(a){if(a.type==='email'&&a.lead_id)return `<button class="btn-small" data-inbox-open="${h(a.lead_id)}">Open history</button>`;if(a.type==='conversation')return `<button class="btn-small" data-inbox-open="${h(a.id)}">Open history</button>`;return inboxAttention(a);};
const inboxActionWorkspace=uxOpenActionWorkspace;
uxOpenActionWorkspace=async function(kind,id){if(kind==='email'){const r=await api('/ux/actions/email/'+encodeURIComponent(id)+'/workspace');return inboxOpen(r.client.id);}if(kind==='task')return inboxTaskOpen(id);return inboxActionWorkspace(kind,id);};
const inboxToday=dashboard;
dashboard=async function(){
 let html=await inboxToday();if(!['owner','manager','sales','dispatch'].includes(S.user.role))return html;
 const d=await api('/inbox?scope='+encodeURIComponent(UX.todayView||'team')+'&status=Needs%20reply');
 const day=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Toronto',weekday:'long',month:'long',day:'numeric'}).format(new Date());
 // Retain existing operational exceptions, remove the duplicate job-list dashboard.
 const template=document.createElement('template');template.innerHTML=html;
 for(const row of [...template.content.querySelectorAll('.attention-row')])if(row.querySelector('[data-inbox-open]'))row.remove();
 const jobPanel=[...template.content.querySelectorAll('.panel')].find(p=>p.querySelector('h2')?.textContent==="Today's jobs");if(jobPanel)jobPanel.remove();
 template.content.querySelector('.split')?.classList.add('inbox-today-single');
 const heading=template.content.querySelector('.page-head');heading?.insertAdjacentHTML('afterbegin','<div class="inbox-date">'+h(day)+'</div>');
 heading?.insertAdjacentHTML('afterend',`<section class="panel inbox-today"><div class="panel-head"><h2>Client updates needing review</h2><button data-page="communications">Open Inbox · ${d.total}</button></div><div class="panel-body">${d.items.slice(0,4).map(l=>`<div class="inbox-activity"><div><strong>${h(l.client_name)}</strong><p>Move: ${h(uxDateLabel(l.move_date,'Date to confirm'))} · ${h(l.handler_name||'Unclaimed')}</p></div><button data-inbox-open="${h(l.id)}">Review update</button></div>`).join('')||'<p>No client updates need review in this view.</p>'}</div></section>`);
 return template.innerHTML;
};
document.addEventListener('change',e=>{
 if(e.target.id==='inbox-scope'){IB.scope=e.target.value;IB.offset=0;IB.lid=null;navigate('communications');}
 if(e.target.id==='inbox-status'){IB.status=e.target.value;IB.offset=0;IB.lid=null;navigate('communications');}
 if(e.target.id==='inbox-service')inboxOpen(e.target.value);
 if(e.target.id==='inbox-compose-kind'){
  const k=e.target.value;document.getElementById('inbox-subject').hidden=k!=='email';document.getElementById('inbox-share').hidden=k!=='call';
  document.getElementById('inbox-compose-hint').textContent={email:'Review the recipient and message before sending.',portal:'Visible in this service’s client portal. No email notification is sent.',note:'Private to staff. Never shown in the client portal.',call:'Record the call outcome. Only a recap you explicitly share is visible to the client.'}[k];
  document.getElementById('inbox-compose-submit').textContent={email:'Review email',portal:'Post to client portal',note:'Save internal note',call:'Save call outcome'}[k];
 }
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-inbox-tab],[data-inbox-open],[data-inbox-action],[data-inbox-mail],[data-inbox-send],[data-inbox-mail-claim],[data-inbox-client-history],[data-inbox-page]');if(!b)return;e.preventDefault();if(b.disabled)return;b.disabled=true;
 try{
  if(b.dataset.inboxTab){IB.tab=b.dataset.inboxTab;IB.view='followups';IB.status='open';IB.offset=0;IB.lid=null;return await navigate('communications');}
  if(b.dataset.inboxOpen)return await inboxOpen(b.dataset.inboxOpen);
  if(b.dataset.inboxMail)return await inboxMail(b.dataset.inboxMail);
  if(b.dataset.inboxClientHistory)return await inboxClientHistory(b.dataset.inboxClientHistory,Number(b.dataset.offset||0));
  if(b.dataset.inboxPage){if(b.dataset.inboxPage==='history')IB.historyOffset=Number(b.dataset.offset);else IB.offset=Number(b.dataset.offset);return await navigate('communications');}
  if(b.hasAttribute('data-inbox-mail-claim')){await api('/inbox/'+IB.mail.lead_id+'/claim',{method:'POST',data:{review_token:IB.mail.conversation.review_token}});return await inboxMail(IB.mail.id);}
  if(b.hasAttribute('data-inbox-send')){const r=await api('/inbox/mail/'+encodeURIComponent(IB.mail.id)+'/send',{method:'POST',data:{confirmed:true,review_token:IB.mail.review_token}});toast(r.sent?'Email sent.':r.reason||'Email not sent.');return await inboxMail(IB.mail.id);}
  const c=IB.detail?.conversation,a=b.dataset.inboxAction;
  if(a==='back'){IB.lid=null;return await navigate('communications');}
  if(a==='refresh')return await navigate('communications');
  if(a==='client')return IB.detail.client_id?await crmOpenClient(IB.detail.client_id):await openLead(IB.lid);
  if(a==='files')return await clientFiles(IB.lid);
  if(a==='claim'){
   const controls=[...document.querySelectorAll('.inbox-detail input:not(:disabled),.inbox-detail textarea:not(:disabled),.inbox-detail select:not(:disabled),.inbox-detail button:not(:disabled)')];controls.forEach(x=>x.disabled=true);
   try{await api('/inbox/'+IB.lid+'/claim',{method:'POST',data:{review_token:c.review_token}});return await navigate('communications');}
   finally{controls.forEach(x=>{if(x.isConnected)x.disabled=false;});}
  }
  if(a==='handoff'){
   const staff=(await api('/ux/action-assignees')).filter(u=>u.role!=='accounting');
   return showModal('Hand over conversation',`<p>The service owner remains ${h(c.service_owner||'unassigned')}.</p><form id="inbox-handoff">${select('handler_id','Conversation handler',[['','Return to Unclaimed'],...staff.map(u=>[u.id,u.name])],c.handler_id||'')}${textArea('note','What does the next person need to know?','',3)}</form>`,btn('close','Cancel')+'<button form="inbox-handoff" type="submit" class="btn-primary">Hand over</button>');
  }
  if(a==='status'){
   const status=b.dataset.value;IB.nextStatus=status;
   return showModal(status,`<form id="inbox-status-form">${status==='Waiting on client'?field('remind_on','Check back on',today(),'date','min="'+today()+'" required'):''}${textArea('note','Outcome / next step (internal)','',3)}</form>`,btn('close','Cancel')+'<button form="inbox-status-form" type="submit" class="btn-primary">Save status</button>');
  }
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('#inbox-search,#inbox-compose,#inbox-handoff,#inbox-status-form,#inbox-edit-mail');if(!f)return;e.preventDefault();const b=e.submitter;if(b?.disabled)return;if(b)b.disabled=true;
 try{
  const d=Object.fromEntries(new FormData(f));
  if(f.id==='inbox-search'){IB.q=d.q;IB.offset=0;IB.lid=null;return await navigate('communications');}
  if(f.id==='inbox-edit-mail'){await api('/inbox/mail/'+IB.mail.id,{method:'PUT',data:{...d,review_token:IB.mail.review_token}});return await inboxMail(IB.mail.id);}
  d.review_token=IB.detail.conversation.review_token;
  if(f.id==='inbox-handoff')await api('/inbox/'+IB.lid+'/assign',{method:'POST',data:d});
  if(f.id==='inbox-status-form')await api('/inbox/'+IB.lid+'/status',{method:'POST',data:{...d,status:IB.nextStatus}});
  if(f.id==='inbox-compose'){
   if(!IB.request)IB.request=crypto.randomUUID();d.request_key=IB.request;d.share_with_client=f.share_with_client.checked;
   if(d.kind==='email'){const r=await api('/inbox/'+IB.lid+'/email',{method:'POST',data:d});IB.request=null;delete IB.drafts[IB.lid];f.reset();return await inboxMail(r.mail_id);}
   await api('/inbox/'+IB.lid+'/entries',{method:'POST',data:d});IB.request=null;delete IB.drafts[IB.lid];toast(d.kind==='portal'?'Message posted to the client portal.':'Saved to the service history.');
  }
  if(modal.open)modal.close();await navigate('communications');
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b?.isConnected)b.disabled=false;}
});

document.addEventListener('input',e=>{if(e.target.closest('#inbox-edit-mail')){const b=modal.querySelector('[data-inbox-send]');if(b){b.disabled=true;b.textContent='Save changes before sending';}}});

function inboxKeepDraft(e){const f=e.target.closest('#inbox-compose');if(f&&IB.lid)IB.drafts[IB.lid]={...Object.fromEntries(new FormData(f)),share_with_client:f.elements.share_with_client.checked};}
document.addEventListener('input',inboxKeepDraft);document.addEventListener('change',inboxKeepDraft);
window.addEventListener('beforeunload',e=>{if(Object.values(IB.drafts).some(d=>d.body?.trim())){e.preventDefault();e.returnValue='';}});

async function inboxTaskOpen(id){
 const t=await api('/inbox-tasks/'+encodeURIComponent(id));IB.activeTask=t;
 const owner=t.owner===S.user.id||t.owner===S.user.name,can=owner||['owner','manager'].includes(S.user.role),done=t.status==='Done';
 showModal(t.title,`<h3>${h(t.client_name||'Internal / unmatched')}</h3><p>${h(uxDateLabel(t.due_date,'No due date'))} · ${pill(done?'Completed':'Open')}</p>${inboxEvents(t.events)}${can?`<form id="inbox-task-outcome"><label>Outcome / next step<textarea name="note" rows="3" maxlength="2000"></textarea></label><label>Follow up on<input name="due_date" type="date" value="${h(t.due_date>=today()?t.due_date:today())}" min="${today()}"></label></form>`:'<p class="muted">Claim this follow-up to record the next step.</p>'}<div class="row wrap">${t.lead_id?`<button data-inbox-open="${h(t.lead_id)}">Open client history / reply</button><button data-inbox-client="${h(t.client_id||'')}" data-lead-id="${h(t.lead_id)}">Client</button>`:''}${t.call_ids.map(cid=>`<button data-inbox-task-call="${h(cid)}">Link / change client</button>`).join('')}${String(t.legacy_id||'').startsWith('gmail-match:')?'<button data-page="settings" data-action="close">Open Connections to match inquiry</button>':''}</div>`,btn('close','Close')+(!t.owner&&!done?`<button data-inbox-task="${h(t.id)}" data-task-action="claim">Claim</button>`:'')+(can?`<button form="inbox-task-outcome" type="submit" data-outcome="schedule">Save follow-up date</button><button form="inbox-task-outcome" type="submit" data-outcome="${done?'reopen':'done'}" class="btn-primary">${done?'Reopen':'Mark done'}</button>`:''),true);
}
async function inboxCallLink(cid){
 let c=IB.calls[cid];
 if(!c){const r=await api('/inbox-call/'+encodeURIComponent(cid));c=r;}
 IB.linkCall=c;
 showModal('Link call to client',`<p>${h(c.client_name||'Unmatched caller')} · ${h(c.phone||'')} · ${h(c.status||'')}</p><p class="muted">Choose the correct client and service. This moves this call and its follow-up only.</p><form id="inbox-call-search"><label>Client name, email or phone<input name="q" required minlength="2" placeholder="Search for the client"></label><button type="submit">Search clients</button></form><div class="inbox-client-matches" id="inbox-call-matches"></div>`,btn('close','Cancel'),true);
}
document.addEventListener('change',async e=>{
 try{
  if(e.target.id==='inbox-call-status'){IB.callStatus=e.target.value;IB.offset=0;await navigate('communications');}
  if(e.target.id==='inbox-history-kind'){IB.historyKind=e.target.value;await inboxClientHistory(e.target.dataset.clientId);}
 }catch(ex){toast(ex.message);}
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-inbox-view],[data-inbox-bucket],[data-inbox-task],[data-inbox-client],[data-inbox-link-call],[data-inbox-task-call],[data-inbox-link-choice],[data-inbox-link-confirm]');if(!b||b.disabled)return;e.preventDefault();b.disabled=true;
 try{
  if(b.dataset.inboxView){IB.view=b.dataset.inboxView;IB.offset=0;IB.lid=null;return await navigate('communications');}
  if(b.dataset.inboxBucket){IB.status=b.dataset.inboxBucket;IB.offset=0;return await navigate('communications');}
  if(b.hasAttribute('data-inbox-client'))return b.dataset.inboxClient?await crmOpenClient(b.dataset.inboxClient):await openLead(b.dataset.leadId);
  if(b.dataset.inboxLinkCall||b.dataset.inboxTaskCall)return await inboxCallLink(b.dataset.inboxLinkCall||b.dataset.inboxTaskCall);
  if(b.dataset.inboxLinkChoice){
   const choice=IB.linkChoices[b.dataset.inboxLinkChoice];IB.linkChoice=choice;
   return showModal('Confirm client match',`<p>Link this call to <strong>${h(choice.client_name)}</strong>?</p><p>${h(choice.email||'')} · ${h(choice.phone||'')}</p><p>Move / service: <strong>${h(uxDateLabel(choice.move_date,'Date to confirm'))}</strong></p><p>${h(choice.pickup||'')}</p><p class="muted">Other messages, files and services will stay where they are.</p>`,btn('close','Cancel')+'<button class="btn-primary" data-inbox-link-confirm>Link call</button>');
  }
  if(b.hasAttribute('data-inbox-link-confirm')){await api('/inbox-calls/'+encodeURIComponent(IB.linkCall.id)+'/link',{method:'POST',data:{lead_id:IB.linkChoice.id,confirmed:true,review_token:IB.linkCall.review_token}});modal.close();toast('Call linked to the client’s service.');IB.tab='calls';IB.view='log';IB.lid=null;return await navigate('communications');}
  const id=b.dataset.inboxTask,action=b.dataset.taskAction;
  if(action==='open')return await inboxTaskOpen(id);
  const t=await api('/inbox-tasks/'+encodeURIComponent(id));IB.activeTask=t;
  if(action==='assign'){
   const staff=(await api('/ux/action-assignees')).filter(u=>u.role!=='accounting');
   return showModal('Assign follow-up',`<p>${h(t.title)}</p><form id="inbox-task-assign">${select('user_id','Assigned to',[['','Unclaimed'],...staff.map(u=>[u.id,u.name])],t.owner||'')}${textArea('note','Handover note','',3)}</form>`,btn('close','Cancel')+'<button form="inbox-task-assign" type="submit" class="btn-primary">Save assignment</button>');
  }
  if(action==='done'||action==='reopen')return await inboxTaskOpen(id);
  await api('/inbox-tasks/'+encodeURIComponent(id),{method:'POST',data:{action,review_token:t.review_token}});
  if(modal.open)return await inboxTaskOpen(id);await navigate('communications');
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('#inbox-task-outcome,#inbox-task-assign,#inbox-call-search');if(!f)return;e.preventDefault();const b=e.submitter;if(b?.disabled)return;if(b)b.disabled=true;
 try{
  const d=Object.fromEntries(new FormData(f));
  if(f.id==='inbox-call-search'){
   const clients=await api('/crm/clients?q='+encodeURIComponent(d.q));IB.linkChoices={};
   const html=clients.flatMap(c=>c.services.map(s=>{IB.linkChoices[s.id]={...s,client_name:c.client_name,email:c.email,phone:c.phone};return `<button data-inbox-link-choice="${h(s.id)}"><strong>${h(c.client_name)}</strong><br>${h(c.email||c.phone||'')}<br>Move / service: ${h(uxDateLabel(s.move_date,'Date to confirm'))} · ${h(s.stage)}<br>${h(s.pickup||'')}</button>`;})).join('');
   document.getElementById('inbox-call-matches').innerHTML=html||'<p>No matching service found. Create the client’s inquiry first, then link this call.</p>';return;
  }
  const t=IB.activeTask;await api('/inbox-tasks/'+encodeURIComponent(t.id),{method:'POST',data:{...d,action:f.id==='inbox-task-assign'?'assign':b.dataset.outcome,review_token:t.review_token}});
  modal.close();await navigate('communications');toast('Follow-up saved.');
 }catch(ex){modalError(ex);}finally{if(b?.isConnected)b.disabled=false;}
});

document.addEventListener('click',e=>{if(e.target.closest('[data-page="communications"]')){IB.tab='tasks';IB.view='followups';IB.status='open';IB.offset=0;IB.lid=null;}},true);
