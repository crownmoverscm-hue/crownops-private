/* One service conversation, a separate handler, and a client-wide history. */
'use strict';
const IB={drafts:{},tab:'conversations',scope:'team',status:'open',q:'',offset:0,lid:null,detail:null,historyOffset:0,request:null};
const inboxFollowups=X.pageRenderers.followups;
const inboxNav=nav.find(n=>n[0]==='communications');if(inboxNav)inboxNav[2]='Inbox';
function inboxTabs(){return '<nav class="inbox-tabs" aria-label="Inbox sections">'+[['conversations','Conversations'],['calls','Calls'],['tasks','Tasks'],['drafts','Drafts'],['sent','Sent']].map(([v,n])=>`<button data-inbox-tab="${v}" class="${IB.tab===v?'selected':''}" aria-current="${IB.tab===v?'page':'false'}">${n}</button>`).join('')+'</nav>';}
function inboxPages(total,offset,size,mode){if(total<=size)return '';return `<div class="inbox-pagination"><span>${total?offset+1:0}–${Math.min(offset+size,total)} of ${total}</span><button data-inbox-page="${mode}" data-offset="${Math.max(0,offset-size)}" ${offset===0?'disabled':''}>Previous</button><button data-inbox-page="${mode}" data-offset="${offset+size}" ${offset+size>=total?'disabled':''}>Next</button></div>`;}
function inboxEvents(events,publicView=false){return events.map(e=>`<article class="inbox-event ${e.direction==='client'?'from-client':''}"><div class="row wrap"><strong>${h(e.title)}</strong>${e.status?pill(e.status):''}${!publicView?'<span class="inbox-visibility">'+(e.visibility==='staff'?'Internal · staff only':'Client-visible')+'</span>':''}</div><div class="tiny muted">${h(uxTimestampLabel(e.at))}${e.actor?' · '+h(e.actor):''}</div>${e.body?'<p class="inbox-body">'+h(e.body)+'</p>':''}</article>`).join('')||'<p class="muted">No interactions yet.</p>';}
function inboxDetail(d){
 const l=d.client,c=d.conversation,can=c.handler_id===S.user.id||['owner','manager'].includes(S.user.role);
 const next=c.status==='Needs reply'?'Review the latest client update and record the next step.':c.status==='Waiting on client'?'Check back on '+uxDateLabel(c.remind_on)+'.':'Keep the outcome and next step in this service history.';
 const other=d.services.length>1?`<label>Service<select id="inbox-service">${d.services.map(s=>`<option value="${h(s.id)}" ${s.id===l.id?'selected':''}>${h(uxDateLabel(s.move_date,'Date to confirm'))} · ${h(s.stage)}</option>`).join('')}</select></label>`:'';
 const actions=!c.handler_id?'<button class="btn-primary" data-inbox-action="claim">Claim conversation</button>':`<span class="pill">Handled by ${h(c.handler_name)}</span>`;
 return `<section class="inbox-detail" aria-label="Client conversation"><button class="inbox-back" data-inbox-action="back">← All conversations</button><header class="inbox-detail-head"><div><h2>${h(l.client_name)}</h2><p class="muted">Move: ${h(uxDateLabel(l.move_date,'Date to confirm'))} · Service owner: ${h(c.service_owner||'Unassigned')}</p></div><button data-inbox-action="client">Open client</button></header><div class="row wrap inbox-ownership">${actions}${can?'<button data-inbox-action="handoff">Hand over</button>':''}${pill(c.status)}</div>${other}<div class="inbox-next"><strong>Next step</strong><p>${h(next)}</p></div><div class="row wrap inbox-outcomes">${can?`<button data-inbox-action="status" data-value="In progress">In progress</button><button data-inbox-action="status" data-value="Waiting on client">Waiting on client</button><button data-inbox-action="status" data-value="Resolved">Mark resolved</button>`:''}<button data-inbox-action="refresh">Refresh history</button></div><div class="inbox-history" tabindex="0" aria-label="Service interaction history">${inboxEvents(d.events)}</div>${inboxPages(d.total,d.offset,40,'history')}${can?`<form id="inbox-compose"><div class="grid2"><label>Write<select name="kind" id="inbox-compose-kind"><option value="email">Email reply</option><option value="portal">Portal reply</option><option value="note">Internal note</option><option value="call">Call outcome</option></select></label><label id="inbox-subject">Subject<input name="subject" value="Your Crown Movers service" maxlength="300"></label></div><p class="small muted" id="inbox-compose-hint">Prepare an email, then review the recipient and message before sending.</p><label>Message<textarea name="body" rows="4" maxlength="10000" required></textarea></label><label id="inbox-share" class="checkbox-label" hidden><input type="checkbox" name="share_with_client">Share this call recap in the client portal</label><div class="row wrap"><button type="submit" class="btn-primary" id="inbox-compose-submit">Review email</button><button type="button" data-inbox-action="files">Photos & move details</button></div></form>`:'<p class="callout">Claim this conversation to reply or record an outcome. The service owner stays unchanged.</p>'}</section>`;
}
X.pageRenderers.communications=async()=>{
 if(!['owner','manager','sales','dispatch'].includes(S.user.role))return empty('Office inbox','This section is for the sales and operations team.');
 const top=head('Inbox','Every conversation. One next step.','<button class="btn-primary" data-action="new-lead">'+icon('plus')+'New inquiry</button>')+inboxTabs();
 if(IB.tab==='tasks')return top+'<div class="inbox-task-view">'+(await inboxFollowups()).replace("<h1>Follow-ups</h1>","<h2>Tasks</h2>")+'</div>';
 const controls=`<form id="inbox-search" class="inbox-toolbar ${IB.lid?'inbox-toolbar-detail':''}"><input name="q" value="${h(IB.q)}" placeholder="Search client name, email or phone" aria-label="Search inbox"><button type="submit">Search</button>${IB.tab==='conversations'?`<select id="inbox-scope" aria-label="Conversation ownership"><option value="team" ${IB.scope==='team'?'selected':''}>Team</option><option value="mine" ${IB.scope==='mine'?'selected':''}>Mine</option><option value="unclaimed" ${IB.scope==='unclaimed'?'selected':''}>Unclaimed</option></select><select id="inbox-status" aria-label="Conversation status">${['open','all','Needs reply','In progress','Waiting on client','Resolved'].map(s=>`<option value="${s}" ${IB.status===s?'selected':''}>${s==='open'?'Open conversations':s==='all'?'All statuses':s}</option>`).join('')}</select>`:''}</form>`;
 if(IB.tab!=='conversations'){
  const r=await api('/inbox/activity?kind='+IB.tab+'&q='+encodeURIComponent(IB.q)+'&offset='+IB.offset);
  return top+controls+panel(IB.tab==='calls'?'Calls':IB.tab==='drafts'?'Drafts & delivery issues':'Sent email',r.items.map(m=>`<div class="inbox-activity"><div><strong>${h(m.client_name||'Client matching needed')}</strong><p>${h(m.subject)}</p><div class="tiny muted">${h(uxTimestampLabel(m.created_at))}</div>${m.error?'<p class="error">'+h(m.error)+'</p>':''}</div>${pill(m.status)}${IB.tab==='calls'?(m.lead_id?`<button data-inbox-open="${h(m.lead_id)}">Open service</button>`:''):`<button data-inbox-mail="${h(m.id)}">Review</button>`}</div>`).join('')||empty('Nothing in this view','Try another search.'))+inboxPages(r.total,r.offset,30,'list');
 }
 const r=await api('/inbox?scope='+IB.scope+'&status='+encodeURIComponent(IB.status)+'&q='+encodeURIComponent(IB.q)+'&offset='+IB.offset);
 if(IB.lid){try{IB.detail=await api('/inbox/'+encodeURIComponent(IB.lid)+'?offset='+IB.historyOffset);}catch(e){IB.lid=null;throw e;}}
 const list=r.items.map(l=>`<button class="inbox-client ${l.id===IB.lid?'selected':''}" data-inbox-open="${h(l.id)}"><strong>${h(l.client_name)}</strong><span>Move: ${h(uxDateLabel(l.move_date,'Date to confirm'))}</span><span>${h(l.status)}${l.new_activity?' · New update':''}</span><small>${h(l.handler_name?'Handled by '+l.handler_name:'Unclaimed')} · ${h(uxTimestampLabel(l.last_activity))}</small></button>`).join('')||'<p class="inbox-empty">No conversations in this view.</p>';
 return top+controls+`<div class="inbox-layout ${IB.lid?'has-detail':''}"><aside class="inbox-list" aria-label="Conversations">${list}${inboxPages(r.total,r.offset,30,'list')}</aside>${IB.lid?inboxDetail(IB.detail):'<section class="inbox-detail inbox-empty"><h2>Choose a conversation</h2><p>View messages, photos and service updates together.</p><p class="muted">Claiming a conversation does not change its service owner.</p></section>'}</div>`;
};
const inboxNavigate=navigate;
navigate=async function(page){if(page==='followups'){IB.tab='tasks';page='communications';}if(page==='outbox'){IB.tab='drafts';page='communications';}if(page==='calls'){IB.tab='calls';page='communications';}const result=await inboxNavigate(page);const f=document.getElementById('inbox-compose'),d=IB.drafts[IB.lid];if(f&&d){for(const k of ['kind','subject','body'])f.elements[k].value=d[k];f.elements.share_with_client.checked=d.share_with_client;f.elements.kind.dispatchEvent(new Event('change',{bubbles:true}));}return result;};
async function inboxOpen(lid){IB.tab='conversations';IB.lid=lid;IB.historyOffset=0;IB.request=null;if(modal.open)modal.close();await navigate('communications');}
async function inboxMail(mid){
 const m=await api('/inbox/mail/'+encodeURIComponent(mid));IB.mail=m;
 const editable=m.status==='Draft'&&m.event_key.startsWith('conversation:')&&m.can_handle;
 const content=editable?`<form id="inbox-edit-mail"><label>Subject<input name="subject" value="${h(m.subject)}" required maxlength="300"></label><label>Message<textarea name="body" rows="8" required maxlength="10000">${h(m.body)}</textarea></label><button type="submit">Save changes</button></form>`:`<h3>${h(m.subject)}</h3><div class="inbox-body message-draft">${h(m.body)}</div>`;
 showModal('Review email',`<p><strong>To:</strong> ${h(m.recipient)}</p>${pill(m.status)}${content}${m.error?'<p class="callout">'+h(m.error)+'</p>':''}<p class="small muted">An email appears in the client history after the email provider confirms sending.</p>${!m.can_handle?'<p class="callout">'+(m.conversation.handler_id?'Ask '+h(m.conversation.handler_name)+' to send this reply or hand over the conversation.':'Claim the conversation before sending this email.')+'</p>':''}`,btn('close','Close')+(!m.can_handle&&!m.conversation.handler_id?'<button data-inbox-mail-claim>Claim conversation</button>':'')+(['Draft','Approved'].includes(m.status)&&m.can_handle?'<button class="btn-primary" data-inbox-send>Send email</button>':''),true);
}
async function inboxClientHistory(cid,offset=0){
 const r=await api('/crm/clients/'+encodeURIComponent(cid)+'/history?offset='+offset);
 showModal('Client interaction history', '<p class="muted">All linked services. Internal notes stay staff-only.</p>'+r.items.map(e=>'<div class="tiny muted">Service: '+h(uxDateLabel(e.service_date,'Date to confirm'))+' <button class="btn-small" data-inbox-open="'+h(e.lead_id)+'">Open conversation</button></div>'+inboxEvents([e])).join('')+`<div class="inbox-pagination"><button data-inbox-client-history="${h(cid)}" data-offset="${Math.max(0,offset-40)}" ${offset===0?'disabled':''}>Previous</button><span>${r.total} interactions</span><button data-inbox-client-history="${h(cid)}" data-offset="${offset+40}" ${offset+40>=r.total?'disabled':''}>Next</button></div>`,btn('close','Close'),true);
}
const inboxClient=crmOpenClient;
crmOpenClient=async function(cid){await inboxClient(cid);modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin',`<button class="btn-primary" data-inbox-client-history="${h(cid)}">All interactions</button>`);};
const inboxLead=openLead;
openLead=async function(lid){await inboxLead(lid);if(['owner','manager','sales','dispatch'].includes(S.user.role))modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin',`<button data-inbox-open="${h(lid)}">Messages & history</button>`);};
const inboxAttention=uxAttentionButton;
uxAttentionButton=function(a){if(a.type==='email'&&a.lead_id)return `<button class="btn-small" data-inbox-open="${h(a.lead_id)}">Open conversation</button>`;if(a.type==='conversation')return `<button class="btn-small" data-inbox-open="${h(a.id)}">Open conversation</button>`;return inboxAttention(a);};
const inboxActionWorkspace=uxOpenActionWorkspace;
uxOpenActionWorkspace=async function(kind,id){if(kind==='email'){const r=await api('/ux/actions/email/'+encodeURIComponent(id)+'/workspace');return inboxOpen(r.client.id);}if(kind==='task'){const r=await api('/ux/actions/task/'+encodeURIComponent(id)+'/workspace');if(r.action.conversation)return inboxOpen(r.client.id);}return inboxActionWorkspace(kind,id);};
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
 heading?.insertAdjacentHTML('afterend',`<section class="panel inbox-today"><div class="panel-head"><h2>Conversations needing a reply</h2><button data-page="communications">Open Inbox · ${d.total}</button></div><div class="panel-body">${d.items.slice(0,4).map(l=>`<div class="inbox-activity"><div><strong>${h(l.client_name)}</strong><p>Move: ${h(uxDateLabel(l.move_date,'Date to confirm'))} · ${h(l.handler_name||'Unclaimed')}</p></div><button data-inbox-open="${h(l.id)}">Review update</button></div>`).join('')||'<p>No conversations need a reply in this view.</p>'}</div></section>`);
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
  if(b.dataset.inboxTab){IB.tab=b.dataset.inboxTab;IB.offset=0;IB.lid=null;return await navigate('communications');}
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
  if(a==='claim'){await api('/inbox/'+IB.lid+'/claim',{method:'POST',data:{review_token:c.review_token}});return await navigate('communications');}
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
