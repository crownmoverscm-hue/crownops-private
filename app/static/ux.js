/* CrownOps v5.4 simplified job review pass: simple defaults, useful exceptions, clean role-based UX. */
'use strict';
const UX={payStart:null,payEnd:null,dirStart:null,dirEnd:null,searchResults:[],directory:null,payroll:null,estimateStatus:'all',estimateSearch:'',followupStatus:'open',followupSearch:'',followupScope:'active',invoiceStatus:'all',invoiceSearch:'',qboSetup:null};

// ---------- Navigation: show the things people actually use ----------
ensureNav(['communications','mail','Communications']);
for(const [key,label] of [['dashboard','Today'],['quotes','Estimates'],['followups','Follow-ups'],['invoices','Invoices'],['team','Employee directory'],['payroll','Payroll'],['inventory','Materials'],['rates','Pricing & rates'],['documents','Documents & approvals']]){const n=nav.find(x=>x[0]===key);if(n)n[2]=label;}

function uxGroups(){
 const role=S.user.role;
 if(role==='crew')return [
   ['FIELD WORK',['schedule','timeoff','team','invoices','account']]
 ];
 const full=[
   ['TODAY',['dashboard']],
   ['SALES',['sales','quotes','followups','communications']],
   ['OPERATIONS',['schedule','invoices','payments','timeoff','documents','portalhub','care']],
   ['TEAM',['team','payroll']],
   ['COMPANY',['rates','inventory','fleet','connections','settings','account']]
 ];
 return full.map(([g,items])=>[g,items.filter(k=>allowedPages().includes(k))]).filter(x=>x[1].length);
}
function uxLabel(key){if(S.user.role==='crew'&&key==='schedule')return 'Jobs';return nav.find(n=>n[0]===key)?.[2]||key;}
const uxOldShell=shell;
shell=function(){
 const allowed=allowedPages(),role=S.user.role,groups=uxGroups();
 const navHtml=groups.map(([group,items])=>`<div class="nav-label">${h(group)}</div>`+items.map(k=>{const n=nav.find(x=>x[0]===k);if(!n)return '';return `<button class="nav-button ${S.page===k?'active':''}" data-page="${h(k)}">${icon(n[1])}<span>${h(uxLabel(k))}</span></button>`;}).join('')).join('');
 const search=role==='crew'?'':`<div class="searchbox global-search">${icon('search')}<input id="ux-global-search" placeholder="Search clients, jobs, invoices..." aria-label="Global search"></div>`;
 root.innerHTML=`<div class="app"><aside class="sidebar" id="sidebar"><div class="brand"><div class="brand-icon">${crown}</div><div><strong>CrownOps</strong><small>CROWN MOVERS</small></div></div>${navHtml}<div class="sidebar-foot"><div class="workspace-label"><span class="dot"></span>Private UX review<div class="tiny" style="color:#91aacd;margin-top:4px">Simple first. Exceptions only when needed.</div></div><div class="profile"><div class="avatar">${h(initials(S.user.name))}</div><div><div style="color:white;font-size:12px;font-weight:600">${h(S.user.name)}</div><div style="font-size:10px">${h(role==='owner'?'manager':role)} access</div></div>${btn('logout',icon('logout'),'logout','aria-label="Sign out"')}</div></div></aside><main class="main"><header class="topbar">${btn('menu',icon('menu'),'mobile-toggle btn-icon','aria-label="Open navigation"')}<div class="breadcrumb">Crown Movers ${icon('right')} <b id="crumb">${h(uxLabel(S.page))}</b></div><div class="top-right">${search}<span class="date-label">Montr&eacute;al time &middot; ET</span><div class="avatar">${h(initials(S.user.name))}</div></div></header><div class="pilot-strip" id="pilot-strip">${icon('shield')}<span>Release-candidate review. Gmail inbox sync is manual/read-only; QuickBooks sandbox may be connected, while production accounting, payments and production schedule actions remain off.</span></div><section class="content" id="content"></section></main></div>`;
 const gs=document.getElementById('ux-global-search');if(gs)gs.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();uxSearch(gs.value);}});
};

// ---------- Today: answer “what needs my attention?” ----------
const uxOldDashboard=dashboard;
dashboard=async function(){
 if(S.user.role==='crew')return uxOldDashboard();
 const d=await api('/ux/today');
 const metrics=[['Jobs today',d.stats.jobs_today,'calendar'],['Needs attention',d.stats.attention,'shield'],['Follow-ups',d.stats.open_followups,'phone'],['Payments to verify',d.stats.unverified_payments,'wallet']];
 const attention=d.attention.length?d.attention.slice(0,12).map(a=>`<div class="attention-row"><div class="attention-dot ${a.priority==='High'?'high':''}"></div><div class="grow"><div class="small strong">${h(a.title)}</div><div class="tiny muted">${h(a.detail||'')}</div></div>${uxAttentionButton(a)}</div>`).join(''):empty('Nothing urgent right now','CrownOps will surface exceptions here instead of making you hunt through every section.');
 const jobs=d.jobs.length?d.jobs.map(j=>`<button class="today-job" data-ux="open-job" data-id="${h(j.id)}"><div><div class="strong">${h(j.title)}</div><div class="tiny muted">${h(j.phase)} &middot; ${timeLabel(j.start_at)} &middot; ${h(j.crew_names.join(', ')||'Crew needed')}</div></div><div>${j.readiness.ready?'<span class="pill teal">Ready</span>':`<span class="pill orange">${j.readiness.missing.length} item${j.readiness.missing.length===1?'':'s'}</span>`}</div></button>`).join(''):empty('No jobs today','Upcoming services will appear here automatically.');
 return head('Today','The things that need your attention, without checking five screens.',btn('new-lead',icon('plus')+'New inquiry','btn-primary')+btn('new-job','New job'))+
 `<div class="metric-grid">${metrics.map(m=>`<div class="panel metric"><div class="label"><span>${m[0]}</span><span class="ico">${icon(m[2])}</span></div><div class="value">${Number(m[1]||0).toLocaleString()}</div><div class="foot">Current review workspace</div></div>`).join('')}</div>`+
 `<div class="split">${panel('Needs attention',attention)}${panel("Today's jobs",jobs)}</div><div class="spacer"></div>`+
 `<div class="grid3">${panel('Sales',`<p class="small muted">New inquiries, quotes and client conversations.</p>${btn('go-sales','Open sales','btn-soft')}`)}${panel('Operations',`<p class="small muted">Schedule, invoices, time off and field work.</p>${btn('go-schedule','Open schedule','btn-soft')}`)}${panel('Company setup',`<p class="small muted">Rates, materials, documents, fleet and connections.</p><button data-page="settings" class="btn-soft">Open setup</button>`)}</div>`;
};
function uxAttentionButton(a){
 const attrs=`data-ux="attention" data-kind="${h(a.type)}" data-id="${h(a.id||'')}" data-lead="${h(a.lead_id||'')}"`;
 return `<button class="btn-small" ${attrs}>Open</button>`;
}

// ---------- Communications: calls, follow-ups and outgoing email in one place ----------
X.pageRenderers.communications=async()=>{
 const [tasks,calls,mail,inbox]=await Promise.all([api('/tasks?scope=active'),api('/calls'),api('/outbound'),api('/ux/inbox')]);
 const due=tasks.slice(0,30),recentCalls=calls.slice(0,25),out=mail.slice(0,25);
 const openInbox=inbox.filter(m=>!m.resolved_at),answered=inbox.filter(m=>m.resolved_at);
 const inboxHtml=openInbox.length?openInbox.map(m=>`<div class="task-row"><div class="grow"><div class="small strong">${h(m.client_name)}</div><div class="tiny muted">${h(m.subject||'Client email')} &middot; ${h(m.created_at)}</div><div class="tiny">${h((m.body||'').slice(0,140))}</div></div>${m.claimed_name?`<span class="pill blue">${h(m.claimed_name)}</span>`:'<span class="pill orange">Unclaimed</span>'}${m.claimed_by?`<button class="btn-small" data-ux="inbox-resolve" data-id="${h(m.id)}">Mark answered</button>`:`<button class="btn-small" data-ux="inbox-claim" data-id="${h(m.id)}">Claim</button>`}<button class="btn-small" data-ux="open-client" data-id="${h(m.lead_id)}">Client</button></div>`).join(''):empty('Inbox is clear','New client email will appear here and can be claimed by one team member.');
 return head('Communications','One shared place for client email, follow-ups and calls.',xbtn('new-email','Prepare email','','btn-primary'))+
 `<div class="grid3"><div class="panel metric"><div class="label"><span>Unanswered email</span>${icon('mail')}</div><div class="value">${openInbox.length}</div></div><div class="panel metric"><div class="label"><span>Open follow-ups</span>${icon('check')}</div><div class="value">${tasks.filter(x=>x.status!=='Done').length}</div></div><div class="panel metric"><div class="label"><span>Recent calls</span>${icon('phone')}</div><div class="value">${calls.length}</div></div></div><div class="spacer"></div>`+
 panel('Shared client inbox',inboxHtml)+
 `<div class="spacer"></div>${panel('Follow-ups',xtable(['Client','Action','Due','Status',''],due.map(t=>[h(t.client_name||'Unmatched'),h(t.title),h(t.due_date||''),pill(t.status),t.lead_id?`<button class="btn-small" data-ux="open-client" data-id="${h(t.lead_id)}">Open</button>`:''])))}<div class="spacer"></div><div class="grid2">${panel('Recent calls',recentCalls.length?recentCalls.map(c=>`<div class="activity-row"><span class="activity-dot"></span><div><div class="small strong">${h(c.client_name||'Unmatched call')}</div><div class="tiny muted">${h(c.created_at)} &middot; ${h(c.details?.phone||c.details?.Phone||'')}</div></div></div>`).join(''):empty('No call activity','Connected call events will appear here.'))}${panel('Outgoing email',out.length?out.map(m=>`<div class="task-row"><div class="grow"><div class="small strong">${h(m.client_name||m.recipient)}</div><div class="tiny muted">${h(m.subject)}</div></div>${pill(m.status)}${xbtn('read-email','Open',m.id,'btn-small')}</div>`).join(''):empty('No email activity','Drafts and sent messages will appear here.'))}</div>`;
};
// ---------- Client project: lifecycle + full activity, without more data entry ----------
const uxOldOpenLead=openLead;
openLead=async function(id){
 const overview=await api('/ux/project/'+encodeURIComponent(id));
 await uxOldOpenLead(id);
 const body=modal.querySelector('.modal-body');if(!body)return;
 const foot=modal.querySelector('.modal-foot');
 if(foot&&may(['owner','sales','dispatch']))foot.insertAdjacentHTML('afterbegin',`<button class="btn-soft" data-ux="new-followup" data-id="${h(id)}">${icon('check')}Add follow-up</button>`);
 const steps=overview.lifecycle.steps.map((s,i)=>`<div class="lifecycle-step ${i<overview.lifecycle.current_index?'done':i===overview.lifecycle.current_index?'current':''}"><span>${i+1}</span><small>${h(s)}</small></div>`).join('');
 const notReady=overview.jobs.flatMap(j=>j.readiness.ready?[]:j.readiness.missing);
 const activity=overview.timeline.slice(0,12).map(t=>`<div class="activity-row"><span class="activity-dot"></span><div class="grow"><div class="tiny strong">${h(t.title)}</div><div class="tiny muted">${h(t.kind)} &middot; ${h(t.at)}${t.detail?' &middot; '+h(t.detail):''}</div></div>${t.status?pill(t.status):''}</div>`).join('')||'<p class="tiny muted">No project activity yet.</p>';
 const block=`<div class="ux-project-overview"><div class="lifecycle">${steps}</div><div class="spacer"></div><div class="callout ${notReady.length?'orange':'teal'}"><strong>${notReady.length?'Next: resolve the exceptions':'Project flow is clear'}</strong>${h(notReady.length?[...new Set(notReady)].join(', '):'CrownOps will surface the next exception instead of asking for repeated confirmations.')}</div><div class="spacer"></div><details class="ux-details"><summary>Full project activity (${overview.timeline.length})</summary><div class="timeline-list">${activity}</div></details><div class="spacer"></div></div>`;
 const err=body.querySelector('.modal-error');if(err)err.insertAdjacentHTML('afterend',block);else body.insertAdjacentHTML('afterbegin',block);
 // Remove the older short timeline so there is only one source of truth on screen.
 [...body.querySelectorAll('h3')].forEach(h3=>{if(h3.textContent.trim()==='Client timeline'){const p=h3.parentElement;if(p)p.style.display='none';}});
};

// ---------- Jobs: readiness is visible, not another approval form ----------
const uxOldOpenJob=openJob;
openJob=async function(id){
 const ready=await api('/ux/jobs/'+encodeURIComponent(id)+'/readiness').catch(()=>null);
 await uxOldOpenJob(id);
 if(!ready)return;const body=modal.querySelector('.modal-body');if(!body)return;
 const tag=`<div class="callout ${ready.ready?'teal':'orange'}"><strong>${ready.ready?'Job ready':'Job needs attention'}</strong>${h(ready.ready?'Crew, client details and planning checks are in place.':ready.missing.join(', '))}</div><div class="spacer"></div>`;
 const err=body.querySelector('.modal-error');if(err)err.insertAdjacentHTML('afterend',tag);
};

// ---------- Employee directory: public staff contact view, private manager details ----------
function uxDefaultRange(){const end=new Date(today()+'T12:00:00'),start=new Date(end);start.setDate(start.getDate()-13);return [ymd(start),ymd(end)];}
teamPage=async function(){
 if(!UX.dirStart){[UX.dirStart,UX.dirEnd]=uxDefaultRange();}
 let d=null,warning='';
 try{
   d=await api(`/ux/employee-directory?start=${encodeURIComponent(UX.dirStart)}&end=${encodeURIComponent(UX.dirEnd)}`);
 }catch(err){
   const basic=await api('/employees');
   const privateView=may(['owner','accounting']);
   d={start:UX.dirStart,end:UX.dirEnd,private:privateView,employees:basic.map(e=>({
     id:e.id,full_name:e.name||'Employee',role:e.role||'Mover',active:Boolean(e.active),
     phone:'',emergency_contact:{name:'',phone:''},email:'',home_address:'',start_date:'',
     driver_verified:Boolean(e.driver_verified),hourly_cents:Number(e.hourly_cents||0),
     license_uploaded:false,license_name:'',team_lead:false,notes:'',
     current_hours:0,current_jobs:0,pending_time_entries:0
   }))};
   warning=`<div class="callout orange"><strong>Basic directory loaded.</strong>Private employee profile details are temporarily unavailable, but team access and invitations still work.</div><div class="spacer"></div>`;
 }
 UX.directory=d;
 const actions=may(['owner'])?`${btn('invite',icon('plus')+'Invite team member','btn-primary')} ${d.private?`<button class="btn-soft" data-ux="employee-add">Add employee</button>`:''}`:(d.private?`<button class="btn-primary" data-ux="employee-add">${icon('plus')}Add employee</button>`:'');
 const filters=d.private?`<div class="filterbar"><label class="small">Payroll hours from <input id="ux-dir-start" type="date" value="${h(d.start)}"></label><label class="small">to <input id="ux-dir-end" type="date" value="${h(d.end)}"></label><button data-ux="directory-load">Load</button><div class="grow"></div><span class="tiny muted">Private fields are manager-only</span></div>`:'';
 const rows_=d.employees.map(e=>d.private?[`<div class="client-name">${h(e.full_name)}</div><div class="tiny muted">${h(e.role)}</div>`,h(e.phone||'—'),h(e.start_date?String(e.start_date).slice(0,4):'—'),`${Number(e.current_hours||0).toFixed(2)} h<div class="tiny muted">${Number(e.current_jobs||0)} job${Number(e.current_jobs||0)===1?'':'s'}</div>`,money(Number(e.hourly_cents||0))+'/h',e.pending_time_entries?`<span class="pill orange">${e.pending_time_entries} pending</span>`:'<span class="pill teal">Current</span>',`<button class="btn-small" data-ux="employee-open" data-id="${h(e.id)}">Open</button>`]:[`<div class="client-name">${h(e.full_name)}</div><div class="tiny muted">${h(e.role)}</div>`,h(e.phone||'—'),`${h(e.emergency_contact?.name||'—')}<br><span class="tiny muted">${h(e.emergency_contact?.phone||'')}</span>`]);
 return head('Employee directory',d.private?'Contact, employment and current-payroll information in one place.':'Company contacts. Private HR and payroll information is hidden.',actions)+warning+filters+panel('Team',xtable(d.private?['Employee','Phone','Started','Current payroll','Pay rate','Status','']:['Employee','Phone','Emergency contact'],rows_));
};
function uxEmployeeForm(e){
 if(!e)return;
 showModal(h(e.full_name),`<form id="ux-employee-form" data-ux-form="employee"><input type="hidden" name="employee_id" value="${h(e.id)}"><div class="grid2">${field('email','Company / employee email',e.email||'','email')}${field('phone','Phone',e.phone||'')}${field('home_address','Home address',e.home_address||'')}${field('start_date','Start date',e.start_date||'','date')}${field('emergency_name','Emergency contact',e.emergency_contact.name||'')}${field('emergency_phone','Emergency phone',e.emergency_contact.phone||'')}${field('role','Role',e.role||'Mover')}${field('hourly_rate','Hourly pay rate',e.hourly_cents/100,'number','step="0.01" min="0"')}${checkedField('team_lead','Team Lead',e.team_lead)}${checkedField('driver_verified','Driver authorization verified',e.driver_verified)}${checkedField('active','Active employee',e.active)}${textArea('notes','Internal manager note',e.notes||'',3)}</div></form><div class="spacer"></div><div class="panel"><div class="panel-head"><h3>Driver licence</h3>${e.license_uploaded?`<a class="button-link" target="_blank" href="/api/ux/employee-directory/${encodeURIComponent(e.id)}/license">View ${h(e.license_name)}</a>`:''}</div><div class="panel-body"><form id="ux-license-form" data-ux-form="license"><input type="hidden" name="employee_id" value="${h(e.id)}"><input type="file" name="file" accept="image/jpeg,image/png,application/pdf" required><div class="spacer"></div><button type="submit" class="btn-small">Upload licence</button></form></div></div>`,`${btn('close','Cancel')}<button type="submit" form="ux-employee-form" class="btn-primary">Save employee</button>`,true);
}

// ---------- Payroll: one total-hours number per employee, selectable date range ----------
X.pageRenderers.payroll=async()=>{
 if(!UX.payStart){[UX.payStart,UX.payEnd]=uxDefaultRange();}
 const d=await api(`/ux/payroll-summary?start=${encodeURIComponent(UX.payStart)}&end=${encodeURIComponent(UX.payEnd)}`);UX.payroll=d;
 const actions=d.private?`<button class="btn-primary" data-ux="payroll-approve">Approve current range</button>`:'';
 const rows_=d.employees.filter(e=>e.hours||e.pending_entries).map(e=>[h(e.employee_name),h(e.jobs),`<strong>${e.hours.toFixed(2)} h</strong>`,pill(e.status),...(d.private?[money(e.hourly_cents)+'/h',money(e.gross_cents)]:[]),`<button class="btn-small" data-ux="payroll-detail" data-id="${h(e.employee_id)}">Details</button>`]);
 return head('Payroll','Choose the dates. CrownOps totals employee hours automatically.',actions)+`<div class="filterbar payroll-range"><label class="small">From <input id="ux-pay-start" type="date" value="${h(d.start)}"></label><label class="small">To <input id="ux-pay-end" type="date" value="${h(d.end)}"></label><button data-ux="payroll-load">Load payroll</button><div class="grow"></div><span class="pill blue">${d.total_hours.toFixed(2)} total hours</span></div>`+xnote('Payroll uses one total-hours value per employee. CrownOps does not ask managers to separately track service and transport time here.','teal')+panel('Payroll summary',rows_.length?xtable(['Employee','Jobs','Hours','State',...(d.private?['Rate','Gross estimate']:[]),''],rows_):empty('No hours in this range','Choose another date range or complete a service and enter the team hours.'));
};
function uxPayrollDetail(e){const canEdit=Boolean(UX.payroll?.private);const rows_=e.details.map(d=>[h(d.date),h(d.job_title),d.hours.toFixed(2)+' h',pill(d.status),canEdit?`<button class="btn-small" data-ux="payroll-time-edit" data-id="${h(d.time_id)}" data-hours="${h(d.hours)}">Edit</button>`:'']);showModal(h(e.employee_name)+' | '+e.hours.toFixed(2)+' hours',panel('Jobs in this payroll range',xtable(['Date','Job','Hours','State',''],rows_)),btn('close','Close'),true);}

// ---------- Team hours: enter once, change only exceptions ----------
addTime=async function(jid){
 const data=await api('/ux/jobs/'+encodeURIComponent(jid)+'/crew-hours');UX.crewHours=data;
 const existing=data.employees.filter(e=>e.total_hours!=null);const common=data.suggested_total_hours??(existing.length?existing[0].total_hours:'');
 const rowsHtml=data.employees.map(e=>{const has=e.total_hours!=null,same=has&&Number(e.total_hours)===Number(common);return `<div class="simple-hours-row" data-employee="${h(e.id)}"><label class="checkbox-label"><input type="checkbox" name="common_${h(e.id)}" ${!has||same?'checked':''}><span><strong>${h(e.name)}</strong><br><span class="tiny muted">Use team hours</span></span></label>${field('hours_'+e.id,'Individual hours',has?e.total_hours:'','number',`${!has||same?'disabled':''} step="0.25" min="0" max="24"`)}${field('note_'+e.id,'Optional note',has?e.note:'','text',`${!has||same?'disabled':''}`)}</div>`;}).join('');
 showModal('Team hours | '+h(data.job.title),`<form id="ux-crew-hours-form" data-ux-form="crew-hours"><input type="hidden" name="job_id" value="${h(jid)}"><div class="callout teal"><strong>Enter the hours once.</strong>Everyone is selected by default. Uncheck only the employee who worked a different amount.</div><div class="spacer"></div>${field('team_hours','Hours worked by selected team',common,'number','step="0.25" min="0" max="24" required')}<div class="row wrap" style="margin-top:12px">${data.suggested_total_hours!=null?`<button type="button" data-ux="crew-copy-suggested" class="btn-soft">Use invoice/job hours: ${h(data.suggested_total_hours)} h</button>`:''}<button type="button" data-ux="crew-select-all">Select all</button></div><div class="spacer"></div><div class="simple-hours-list">${rowsHtml}</div>${textArea('note','Optional team note','',2)}<p class="tiny muted">Each employee still gets an individual payroll record automatically.</p></form>`,`${btn('close','Cancel')}<button type="submit" form="ux-crew-hours-form" class="btn-primary">Save team hours</button>`,true);
};

// ---------- Connections: make company email + QuickBooks intent obvious ----------
const uxOldConnections=X.pageRenderers.connections;
X.pageRenderers.connections=async()=>{
 const [s,r,c,review]=await Promise.all([api('/connections'),api('/readiness'),api('/ux/company-communications'),api('/gmail/review').catch(e=>{UX.gmailReviewError=e.message;return [];})]);X.cache.connections=s;UX.companyComms=c;
 const reviewRows=review.length?review.map(x=>{const p=x.payload?.parsed||{};const detail=x.event_type.includes('payment')?((p.payer||'Payment')+' · $'+(p.amount||'')):((p.caller||p.phone||x.payload?.subject||'Gmail item'));return `<div class="task-row"><div class="grow"><div class="small strong">${h(x.event_type.replace('gmail_','').replaceAll('_',' '))}</div><div class="tiny muted">${h(detail)}</div></div><button class="btn-small" data-ux="gmail-reviewed" data-id="${h(x.id)}">Reviewed</button></div>`;}).join(''):empty('No Gmail exceptions waiting','Quote requests, missed calls and voicemails flow into Sales. Only items that still need human review stay here.');
 return head('Connections','Company accounts only. Nothing is treated as connected until it passes a real test.')+
 `<div class="grid2">${panel('Company email',`<div class="info-grid"><div><div class="info-label">Connected Gmail / storage</div><div class="strong">${h(c.connected_mailbox||'crownmovers.cm@gmail.com')}</div></div><div><div class="info-label">Client sees From</div><div class="strong">${h(c.send_as||'info@crownmovers.ca')}</div></div><div><div class="info-label">Reply-To</div><div class="strong">${h(c.reply_to||'info@crownmovers.ca')}</div></div></div><div class="spacer"></div>${pill(s.gmail?.authorized_at?'Gmail authorized':'Not connected')}<p class="small muted">Gmail stays read-only. Sync imports New Quote Request, Missed Calls and Voice Mail notices into CrownOps Sales without changing, deleting or sending Gmail messages. Outbound sending remains separately disabled until live external actions are approved.</p>${may(['owner'])?`<div class="row wrap">${xbtn('connect-gmail',s.gmail?.authorized_at?'Reconnect Gmail':'Connect Gmail','gmail','btn-primary')}<button data-ux="gmail-sync" class="btn-soft">Sync inquiry inbox (read only)</button><button data-ux="company-email-edit">Email rules</button></div>`:''}`)}${panel('QuickBooks',`${pill(s.quickbooks?.authorized_at?'QuickBooks authorized':'Not connected')}<p class="small muted">CrownOps can sync customers, estimates, invoices and verified payments after the accounting mappings are reviewed.</p>${s.quickbooks?.authorized_at?`<div class='tiny muted'>Environment: ${h(s.quickbooks?.environment||'sandbox')} &middot; Mappings: ${s.quickbooks?.mappings_complete?'ready':'setup required'} &middot; Test writes: ${s.quickbooks?.writes_enabled?'enabled':'locked'}</div><div class='spacer'></div><button data-ux='qbo-test' class='btn-soft'>Test connection</button> <button data-ux='qbo-setup' class='btn-soft'>QuickBooks setup</button><div class='spacer'></div>`:''}${may(['owner'])?xbtn('connect-quickbooks',s.quickbooks?.authorized_at?'Reconnect QuickBooks':'Connect QuickBooks','quickbooks','btn-primary'):''}`)}</div><div class="spacer"></div>`+
 panel('Gmail exceptions',(UX.gmailReviewError?`<div class="callout orange"><strong>Gmail review queue needs attention.</strong>${h(UX.gmailReviewError)}. You can still use the read-only sync button above.</div><div class="spacer"></div>`:'')+reviewRows)+
 `<div class="spacer"></div><div class="grid3">${[['square','Payments'],['ai','AI assistant'],['intake','Forms / calls'],['scanner','Upload safety']].map(([k,n])=>panel(n,`${pill(s[k]?.configured?'Configured / test required':'Not configured')}<p class="tiny muted">Keep this off until the core workflow is approved.</p>`)).join('')}</div><div class="spacer"></div>${panel('Connection activity',xtable(['Provider','Action','Status','Details'],(s.operations||[]).slice(0,30).map(o=>[h(o.provider),h(o.kind),pill(o.status),h(o.error||o.object_id)])))} `;
};

// ---------- Estimates, follow-ups and invoices: browse like a real operating app ----------
function uxOverviewMetric(label,value,foot=''){
 return `<div class="ux-overview-metric"><div class="ux-overview-value">${value}</div><div class="ux-overview-label">${h(label)}</div>${foot?`<div class="ux-overview-foot">${h(foot)}</div>`:''}</div>`;
}
function uxStatusButton(kind,value,label,count,current){
 return `<button class="ux-filter-chip ${current===value?'selected':''}" data-ux="${kind}" data-value="${h(value)}">${h(label)}<span>${Number(count||0).toLocaleString()}</span></button>`;
}
function uxInvoiceState(i){
 const total=Number(i.calculation?.total_cents||0),balance=Number(i.calculation?.balance_cents||0);
 if(i.status==='Draft')return 'Draft';
 if(balance<=0)return 'Paid';
 if(balance<total)return 'Partial';
 return 'Balance due';
}

X.pageRenderers.quotes=async()=>{
 const all=await api('/quotes');
 const counts={all:all.length,Draft:0,Approved:0,Accepted:0};
 all.forEach(q=>{counts[q.status]=(counts[q.status]||0)+1;});
 const search=String(UX.estimateSearch||'').trim().toLowerCase();
 const filtered=all.filter(q=>(UX.estimateStatus==='all'||q.status===UX.estimateStatus)&&(!search||[q.client_name,q.status,q.inputs?.move_date,q.inputs?.pickup,q.inputs?.dropoff].join(' ').toLowerCase().includes(search)));
 const rows=filtered.map(q=>[
   `<div class="client-name">${h(q.client_name)}</div><div class="tiny muted">Revision ${q.revision}</div>`,
   h(q.inputs?.move_date||'Date to confirm'),
   `${q.result?.movers||'—'} movers / ${q.result?.trucks||'—'} truck${Number(q.result?.trucks||0)===1?'':'s'}`,
   `<span class="strong">${compact(q.result?.low_cents||0)} – ${compact(q.result?.high_cents||0)}</span><div class="tiny muted">before tax</div>`,
   pill(q.status),
   `<button class="btn-small" data-ux="estimate-open" data-id="${h(q.id)}">Open</button>`
 ]);
 const overview=`<div class="ux-overview-grid">${[
   uxOverviewMetric('Total estimates',counts.all),
   uxOverviewMetric('Draft',counts.Draft),
   uxOverviewMetric('Approved',counts.Approved),
   uxOverviewMetric('Accepted',counts.Accepted)
 ].join('')}</div>`;
 const filters=`<div class="ux-browser-toolbar"><div class="ux-filter-chips">${[
   uxStatusButton('estimate-filter','all','All',counts.all,UX.estimateStatus),
   uxStatusButton('estimate-filter','Draft','Draft',counts.Draft,UX.estimateStatus),
   uxStatusButton('estimate-filter','Approved','Approved',counts.Approved,UX.estimateStatus),
   uxStatusButton('estimate-filter','Accepted','Accepted',counts.Accepted,UX.estimateStatus)
 ].join('')}</div><div class="searchbox ux-browser-search">${icon('search')}<input id="ux-estimate-search" value="${h(UX.estimateSearch)}" placeholder="Search client, date or address" aria-label="Search estimates"></div></div>`;
 return head('Estimates','Create, review and browse every moving estimate in one place.',btn('choose-quote',icon('plus')+'New estimate','btn-primary')+btn('choose-ai',icon('spark')+'AI quote assistant','btn-soft'))+
   panel('Overview',overview)+'<div class="spacer"></div>'+
   `<div class="panel"><div class="panel-head"><h2>Estimates</h2><span class="tiny muted">${filtered.length.toLocaleString()} shown</span></div>${filters}<div class="panel-body flush">${rows.length?xtable(['Client','Move date','Crew','Estimate','Status',''],rows):empty('No estimates in this view','Change the filters or create a new estimate.')}</div></div>`;
};

X.pageRenderers.followups=async()=>{
 const all=await api('/tasks?scope='+encodeURIComponent(UX.followupScope));
 const nowDay=today();
 const state=t=>{
   if(t.status==='Done')return 'completed';
   if(t.due_date&&t.due_date<nowDay)return 'overdue';
   if(t.due_date===nowDay)return 'today';
   return 'upcoming';
 };
 const counts={open:0,today:0,overdue:0,upcoming:0,completed:0};
 all.forEach(t=>{const s=state(t);counts[s]++;if(s!=='completed')counts.open++;});
 const search=String(UX.followupSearch||'').trim().toLowerCase();
 const filtered=all.filter(t=>{
   const s=state(t);
   const statusOk=UX.followupStatus==='all'||(UX.followupStatus==='open'?s!=='completed':s===UX.followupStatus);
   return statusOk&&(!search||[t.client_name,t.phone,t.email,t.title].join(' ').toLowerCase().includes(search));
 }).sort((a,b)=>{
   const rank=x=>{const s=state(x);return s==='overdue'?0:s==='today'?1:s==='upcoming'?2:3;};
   return rank(a)-rank(b)||String(a.due_date||'9999').localeCompare(String(b.due_date||'9999'));
 });
 const rows=filtered.map(t=>[
   `<div class="client-name">${h(t.client_name||'Internal / unmatched')}</div><div class="tiny muted">${h(t.phone||t.email||'')}</div>`,
   `<div class="strong">${h(t.title)}</div>`,
   h(t.due_date||'No date'),
   t.owner?(t.owner===S.user.id?'<span class="pill blue">You</span>':'<span class="pill gray">Assigned</span>'):'<span class="pill orange">Unassigned</span>',
   pill(state(t)==='completed'?'Done':state(t)==='overdue'?'Overdue':state(t)==='today'?'Due today':'Upcoming'),
   `<div class="row">${t.lead_id?`<button class="btn-small" data-ux="open-client" data-id="${h(t.lead_id)}">Client</button>`:''}${t.status!=='Done'&&may(['owner','sales','dispatch'])?`<button class="btn-small" data-ux="followup-complete" data-id="${h(t.id)}">Done</button>`:''}</div>`
 ]);
 const overview=`<div class="ux-overview-grid">${[
   uxOverviewMetric('Open',counts.open),
   uxOverviewMetric('Due today',counts.today),
   uxOverviewMetric('Overdue',counts.overdue),
   uxOverviewMetric('Upcoming',counts.upcoming)
 ].join('')}</div>`;
 const filters=`<div class="ux-browser-toolbar"><div class="ux-filter-chips">${[
   uxStatusButton('followup-filter','open','Open',counts.open,UX.followupStatus),
   uxStatusButton('followup-filter','today','Due today',counts.today,UX.followupStatus),
   uxStatusButton('followup-filter','overdue','Overdue',counts.overdue,UX.followupStatus),
   uxStatusButton('followup-filter','upcoming','Upcoming',counts.upcoming,UX.followupStatus),
   uxStatusButton('followup-filter','completed','Completed',counts.completed,UX.followupStatus)
 ].join('')}</div><div class="ux-browser-controls"><select id="ux-followup-scope" class="input-inline" aria-label="Follow-up source"><option value="active" ${UX.followupScope==='active'?'selected':''}>Current work</option><option value="imported" ${UX.followupScope==='imported'?'selected':''}>Imported backlog</option></select><div class="searchbox ux-browser-search">${icon('search')}<input id="ux-followup-search" value="${h(UX.followupSearch)}" placeholder="Search client or action" aria-label="Search follow-ups"></div></div></div>`;
 return head('Follow-ups','Every callback, missing detail and next action your sales team needs to finish.',btn('go-sales','Open clients','btn-soft'))+
   panel('Overview',overview)+'<div class="spacer"></div>'+
   `<div class="panel"><div class="panel-head"><h2>Follow-ups</h2><span class="tiny muted">${filtered.length.toLocaleString()} shown</span></div>${filters}<div class="panel-body flush">${rows.length?xtable(['Client','Action','Due','Owner','Status',''],rows):empty('No follow-ups in this view','Your team is clear here, or the current filters have no matches.')}</div></div>`;
};

X.pageRenderers.invoices=async()=>{
 const all=await api('/invoices');
 const search=String(UX.invoiceSearch||'').trim().toLowerCase();
 const derived=all.map(i=>({...i,display_state:uxInvoiceState(i)}));
 const counts={all:derived.length,Draft:0,'Balance due':0,Partial:0,Paid:0};
 derived.forEach(i=>counts[i.display_state]++);
 const outstanding=derived.filter(i=>i.status!=='Draft').reduce((sum,i)=>sum+Math.max(0,Number(i.calculation?.balance_cents||0)),0);
 const paid=derived.filter(i=>i.status!=='Draft').reduce((sum,i)=>sum+Math.max(0,Number(i.calculation?.total_cents||0)-Number(i.calculation?.balance_cents||0)),0);
 const filtered=derived.filter(i=>(UX.invoiceStatus==='all'||i.display_state===UX.invoiceStatus)&&(!search||[i.number,i.client_name,i.service_date,i.display_state].join(' ').toLowerCase().includes(search)));
 const rows=filtered.map(i=>[
   `<span class="strong">${h(i.number)}</span><div class="tiny muted">Revision ${i.revision}</div>`,
   h(i.client_name),
   h(i.service_date||'—'),
   `<span class="strong">${money(i.calculation?.total_cents||0)}</span>`,
   `<span class="${Number(i.calculation?.balance_cents||0)>0?'strong':''}">${money(i.calculation?.balance_cents||0)}</span>`,
   pill(i.display_state),
   `<button class="btn-small" data-ux="invoice-open" data-id="${h(i.id)}">Open</button>`
 ]);
 const overview=`<div class="ux-overview-grid ux-overview-money">${[
   uxOverviewMetric('Outstanding',money(outstanding)),
   uxOverviewMetric('Recorded paid',money(paid)),
   uxOverviewMetric('Paid invoices',counts.Paid),
   uxOverviewMetric('Draft invoices',counts.Draft)
 ].join('')}</div>`;
 const filters=`<div class="ux-browser-toolbar"><div class="ux-filter-chips">${[
   uxStatusButton('invoice-filter','all','All',counts.all,UX.invoiceStatus),
   uxStatusButton('invoice-filter','Draft','Draft',counts.Draft,UX.invoiceStatus),
   uxStatusButton('invoice-filter','Balance due','Balance due',counts['Balance due'],UX.invoiceStatus),
   uxStatusButton('invoice-filter','Partial','Partial',counts.Partial,UX.invoiceStatus),
   uxStatusButton('invoice-filter','Paid','Paid',counts.Paid,UX.invoiceStatus)
 ].join('')}</div><div class="searchbox ux-browser-search">${icon('search')}<input id="ux-invoice-search" value="${h(UX.invoiceSearch)}" placeholder="Search invoice or client" aria-label="Search invoices"></div></div>`;
 return head('Invoices','Browse every job invoice, balance and payment state from one screen.',btn('go-schedule','Open schedule','btn-soft'))+
   panel('Overview',overview)+'<div class="spacer"></div>'+
   `<div class="panel"><div class="panel-head"><h2>Invoices</h2><span class="tiny muted">${filtered.length.toLocaleString()} shown</span></div>${filters}<div class="panel-body flush">${rows.length?xtable(['Invoice','Customer','Service date','Total','Balance','Status',''],rows):empty('No invoices in this view','Finalized job billing will appear here automatically.')}</div></div>`;
};

// ---------- Settings: one calm place to find low-frequency configuration ----------
const uxOldSettings=settingsPage;
settingsPage=async function(){
 if(S.user.role==='crew')return uxOldSettings();
 return head('Company setup','Low-frequency settings live here so day-to-day work stays clean.')+`<div class="grid3">${[
  ['Pricing & rates','Hourly rates by date and crew size.','rates'],['Materials','Selling prices, cost and stock.','inventory'],['Documents & approvals','Liability and acknowledgement templates.','documents'],['Fleet','Vehicles, inspections and maintenance.','fleet'],['Connections','Company email, QuickBooks and providers.','connections'],['Security & imports','Accounts, workbook imports and technical settings.','settings-legacy']
 ].map(([t,d,p])=>panel(t,`<p class="small muted">${d}</p><button data-ux="setup-go" data-page-target="${p}" class="btn-soft">Open</button>`)).join('')}</div>`;
};

// ---------- Search ----------
async function uxSearch(q){
 q=String(q||'').trim();if(q.length<2)return toast('Type at least two characters.');
 const r=await api('/ux/search?q='+encodeURIComponent(q));UX.searchResults=r;
 const body=r.length?r.map(x=>`<button class="search-result" data-ux="search-open" data-type="${h(x.type)}" data-id="${h(x.id)}" data-lead="${h(x.lead_id||'')}"><span class="pill gray">${h(x.type)}</span><div class="grow"><div class="strong">${h(x.title)}</div><div class="tiny muted">${h(x.detail||'')}</div></div>${icon('right')}</button>`).join(''):empty('No matches','Try the client name, phone, address, job or invoice number.');
 showModal('Search CrownOps',body,btn('close','Close'));
}

// ---------- Forms / actions ----------
document.addEventListener('click',async e=>{
 const el=e.target.closest('[data-ux]');if(!el)return;e.preventDefault();const a=el.dataset.ux,id=el.dataset.id;
 try{
  if(a==='open-job')return openJob(id);
  if(a==='open-client')return openLead(id);
  if(a==='attention'){
    const k=el.dataset.kind,lid=el.dataset.lead;
    if(k==='job')return openJob(id);if(k==='timeoff')return navigate('timeoff');if(k==='task'&&lid)return openLead(lid);if(k==='payment')return navigate('payments');if(k==='gmail')return navigate('connections');if(k==='client-change')return navigate('portalhub');if(k==='fleet')return navigate('fleet');
  }
  if(a==='search-open'){modal.close();if(el.dataset.type==='client')return openLead(id);if(el.dataset.type==='job')return openJob(id);if(el.dataset.type==='invoice')return openInvoice(id);}
  if(a==='estimate-open')return openQuote(id);
  if(a==='new-followup'){return showModal('Add follow-up',`<form id="ux-followup-form" data-ux-form="followup"><input type="hidden" name="lead_id" value="${h(id)}"><div class="grid2">${select('title','Next action',['Call back client','Send estimate','Confirm missing details','Check decision / follow up','Confirm deposit / payment','Other follow-up'],'Call back client')}${field('due_date','Due date',today(),'date','required')}</div></form>`,`${btn('close','Cancel')}<button type="submit" form="ux-followup-form" class="btn-primary">Add follow-up</button>`);}
  if(a==='invoice-open')return openInvoice(id);
  if(a==='estimate-filter'){UX.estimateStatus=el.dataset.value||'all';return navigate('quotes');}
  if(a==='followup-filter'){UX.followupStatus=el.dataset.value||'open';return navigate('followups');}
  if(a==='invoice-filter'){UX.invoiceStatus=el.dataset.value||'all';return navigate('invoices');}
  if(a==='followup-complete'){await api('/tasks/'+encodeURIComponent(id)+'/complete',{method:'POST'});toast('Follow-up completed.');return navigate('followups');}

  if(a==='directory-load'){UX.dirStart=document.getElementById('ux-dir-start').value;UX.dirEnd=document.getElementById('ux-dir-end').value;return navigate('team');}
  if(a==='employee-open'){const emp=UX.directory?.employees.find(x=>x.id===id);return uxEmployeeForm(emp);}
  if(a==='employee-add')return showModal('Add employee',`<form id="ux-add-employee" data-ux-form="employee-add"><div class="grid2">${field('name','Full name','','text','required')}${field('role','Role','Mover','text','required')}${field('hourly_rate','Hourly pay rate',0,'number','step="0.01" min="0"')}</div></form>`,`${btn('close','Cancel')}<button type="submit" form="ux-add-employee" class="btn-primary">Add employee</button>`);
  if(a==='payroll-load'){UX.payStart=document.getElementById('ux-pay-start').value;UX.payEnd=document.getElementById('ux-pay-end').value;return navigate('payroll');}
  if(a==='payroll-detail'){const emp=UX.payroll?.employees.find(x=>x.employee_id===id);if(emp)return uxPayrollDetail(emp);}
  if(a==='payroll-time-edit'){const hours=prompt('Total paid hours for this job:',el.dataset.hours||'');if(hours===null)return;await api('/ux/payroll-time/'+encodeURIComponent(id),{method:'PATCH',data:{hours:Number(hours)}});toast('Hours updated.');modal.close();return navigate('payroll');}
  if(a==='inbox-claim'){await api('/ux/inbox/'+encodeURIComponent(id)+'/claim',{method:'POST'});toast('Email and client assigned to you.');return navigate('communications');}
  if(a==='inbox-resolve'){await api('/ux/inbox/'+encodeURIComponent(id)+'/resolve',{method:'POST'});toast('Email marked answered.');return navigate('communications');}
  if(a==='payroll-approve'){if(!confirm(`Approve all submitted employee hours from ${UX.payroll.start} through ${UX.payroll.end}?`))return;const r=await api('/ux/payroll-summary/approve',{method:'POST',data:{start:UX.payroll.start,end:UX.payroll.end}});toast(`${r.approved} time entr${r.approved===1?'y':'ies'} approved.`);return navigate('payroll');}
  if(a==='crew-copy-suggested'){document.querySelector('#ux-crew-hours-form [name=team_hours]').value=UX.crewHours.suggested_total_hours;return;}
  if(a==='crew-select-all'){document.querySelectorAll('#ux-crew-hours-form input[name^="common_"]').forEach(c=>{c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));});return;}
  if(a==='gmail-sync'){const rr=await api('/gmail/sync-readonly',{method:'POST',data:{days:14,max_per_feed:30}});toast(`Inquiry sync: ${rr.new_leads} new lead(s), ${rr.promoted_existing_calls||0} existing call notice(s) promoted, ${rr.new_events} new Gmail item(s).`);return navigate('connections');}
  if(a==='gmail-reviewed'){await api('/gmail/review/'+encodeURIComponent(id)+'/reviewed',{method:'POST'});toast('Marked reviewed.');return navigate('connections');}
  if(a==='qbo-test'){const q=await api('/quickbooks/test-connection');toast('QuickBooks '+q.environment+' connected: '+(q.company_name||q.realm_id));return navigate('connections');}
  if(a==='qbo-setup'){
    const q=await api('/quickbooks/setup');UX.qboSetup=q;const rec=q.recommendations||{};
    const serviceRows=q.items.filter(x=>String(x.type).toLowerCase()==='service');
    const serviceValue=q.mappings.service_item_id||rec.service_item_id||'';
    const taxValue=q.mappings.tax_code_id||rec.tax_code_id||'';
    const depositValue=q.mappings.deposit_account_id||rec.deposit_account_id||'';
    const discountValue=q.mappings.discount_account_id||'';
    const items=[['','Choose a moving-service item'],...serviceRows.map(x=>[x.id,x.name+(x.id===rec.service_item_id?' — recommended for test':'')])];
    const qcTaxes=q.quebec_tax_codes||[];
    const taxes=[['','Choose GST/QST QC'],...qcTaxes.map(x=>[x.id,x.name+(x.id===rec.tax_code_id?' — recommended':'')])];
    const accounts=[['','Choose where received payments wait'],...q.accounts.map(x=>[x.id,x.name+(x.subtype?' - '+x.subtype:'')+(x.id===rec.deposit_account_id?' — recommended':'')])];
    const discountAccounts=[['','No discount account yet'],...q.accounts.filter(x=>/discount/i.test(x.name||'')).map(x=>[x.id,x.name+(x.id===rec.discount_account_id?' — recommended':'')]),...q.accounts.filter(x=>!/discount/i.test(x.name||'')).map(x=>[x.id,x.name+(x.subtype?' - '+x.subtype:'')])];
    const env=q.environment==='sandbox'?'TEST SANDBOX':'LIVE COMPANY';
    return showModal('QuickBooks for Crown Movers',`<form id="ux-qbo-setup" data-ux-form="qbo-setup">
      <div class="callout teal"><strong>${h(q.company_name||'QuickBooks company')} · ${env}</strong><div class="small muted" style="margin-top:6px">This is not general accounting setup. CrownOps only needs three simple defaults for moving jobs. Saving this screen does not create an invoice or payment.</div></div>
      <div class="spacer"></div>
      <div class="stack">
        <div class="panel panel-body"><div class="eyebrow">1 · WHAT WE SELL</div><h3>Moving service</h3><p class="small muted">QuickBooks needs one Product/Service name for Crown Movers' labour and moving charges. Only Service items are shown here; inventory and event categories are hidden.</p>${select('service_item_id','QuickBooks service item',items,serviceValue,'required')}</div>
        <div class="panel panel-body"><div class="eyebrow">2 · TAX ON A NORMAL QUEBEC MOVE</div><h3>GST + QST (14.975%)</h3><p class="small muted">CrownOps will only accept the normal combined Quebec code here: <strong>GST/QST QC</strong>. It will not let you use HST ON, GST/HST Adjustment, Exempt, Out of Scope or Zero-rated for a normal Quebec move.</p>${q.quebec_tax_ready?select('tax_code_id','Default Quebec tax code',taxes,taxValue,'required'):`<div class="callout orange"><strong>Quebec sales tax is not set up in this QuickBooks sandbox yet.</strong><div class="small" style="margin-top:6px">Open the sandbox company in QuickBooks → <strong>All apps → Sales Tax → Overview → Manage sales tax → Add tax → Provincial → Quebec</strong>. Finish the Quebec tax setup, then come back here and reopen QuickBooks Setup. You should then see <strong>GST/QST QC</strong>.</div></div>`}</div>
        <div class="panel panel-body"><div class="eyebrow">3 · WHEN A CLIENT PAYS</div><h3>Payment holding account</h3><p class="small muted"><strong>Undeposited Funds</strong> is the normal test choice: QuickBooks records the payment as received, then it can be matched to the actual bank deposit later.</p>${select('deposit_account_id','Received payments go to',accounts,depositValue,'required')}</div>
        <details class="panel panel-body"><summary class="strong">Advanced: discounts</summary><div class="spacer"></div><p class="small muted">Leave this blank for now unless you are testing an estimate or invoice with a discount. CrownOps will refuse to export a discounted invoice until this account is reviewed.</p>${select('discount_account_id','Discounts-given account',discountAccounts,discountValue)}</details>
      </div>
      ${q.errors?.length?`<div class="spacer"></div><div class="callout orange">QuickBooks could not load: ${h(q.errors.join(', '))}. Reconnect or retry before saving.</div>`:''}
    </form>`,`${btn('close','Cancel')}${q.quebec_tax_ready?'<button type="submit" form="ux-qbo-setup" class="btn-primary">Save moving-company setup</button>':'<button type="button" class="btn-primary" data-action="close">Set up Quebec tax first</button>'}`,true);
  }
  if(a==='company-email-edit'){const c=UX.companyComms||{};return showModal('Company email rules',`<form id="ux-email-rules" data-ux-form="company-email"><div class="grid2">${field('connected_mailbox','Connected Gmail / storage',c.connected_mailbox||'crownmovers.cm@gmail.com','email','required')}${field('send_as','Client sees From',c.send_as||'info@crownmovers.ca','email','required')}${field('reply_to','Reply-To',c.reply_to||'info@crownmovers.ca','email','required')}${checkedField('approved','Approved for production after connection testing',Boolean(c.approved))}${textArea('note','Internal note',c.note||'',3)}</div></form>`,`${btn('close','Cancel')}<button type="submit" form="ux-email-rules" class="btn-primary">Save rules</button>`);}
  if(a==='setup-go'){const target=el.dataset.pageTarget;if(target==='settings-legacy'){const html=await uxOldSettings();document.getElementById('content').innerHTML=html;return;}return navigate(target);}
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}
});

document.addEventListener('change',e=>{
 if(e.target.matches('#ux-crew-hours-form input[name^="common_"]')){const row=e.target.closest('.simple-hours-row');row.querySelectorAll('input[name^="hours_"],input[name^="note_"]').forEach(i=>{i.disabled=e.target.checked;if(e.target.checked)i.value='';});}
 if(e.target.id==='ux-followup-scope'){UX.followupScope=e.target.value;return navigate('followups');}
});

document.addEventListener('keydown',e=>{
 if(e.key!=='Enter')return;
 if(e.target.id==='ux-estimate-search'){e.preventDefault();UX.estimateSearch=e.target.value;return navigate('quotes');}
 if(e.target.id==='ux-followup-search'){e.preventDefault();UX.followupSearch=e.target.value;return navigate('followups');}
 if(e.target.id==='ux-invoice-search'){e.preventDefault();UX.invoiceSearch=e.target.value;return navigate('invoices');}
});

document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-ux-form]');if(!f)return;e.preventDefault();const kind=f.dataset.uxForm,d=readForm(f);
 try{
  if(kind==='employee'){
    const eid=d.employee_id;await api('/ux/employee-directory/'+encodeURIComponent(eid),{method:'PATCH',data:{...d,active:f.active.checked,team_lead:f.team_lead.checked,driver_verified:f.driver_verified.checked}});modal.close();toast('Employee profile saved.');return navigate('team');
  }
  if(kind==='employee-add'){await api('/employees',{method:'POST',data:d});modal.close();toast('Employee added to the directory.');return navigate('team');}
  if(kind==='license'){const eid=d.employee_id;const fd=new FormData(f);fd.delete('employee_id');await api('/ux/employee-directory/'+encodeURIComponent(eid)+'/license',{method:'POST',form:fd});toast('Licence file uploaded.');return navigate('team');}
  if(kind==='crew-hours'){
    const team=UX.crewHours,employee_ids=team.employees.map(x=>x.id),exceptions={};
    for(const emp of team.employees){const common=f.elements['common_'+emp.id].checked;if(!common)exceptions[emp.id]={total_hours:Number(f.elements['hours_'+emp.id].value),note:f.elements['note_'+emp.id].value};}
    await api('/ux/jobs/'+encodeURIComponent(d.job_id)+'/crew-hours',{method:'POST',data:{employee_ids,team_hours:Number(d.team_hours),exceptions,note:d.note}});toast('Team hours saved.');modal.close();return navigate('team');
  }
  if(kind==='followup'){await api('/tasks',{method:'POST',data:{lead_id:d.lead_id,title:d.title,due_date:d.due_date}});modal.close();toast('Follow-up added.');return navigate('followups');}
  if(kind==='qbo-setup'){await api('/quickbooks/setup',{method:'PATCH',data:d});modal.close();toast('QuickBooks mapping saved.');return navigate('connections');}
  if(kind==='company-email'){const r=await api('/ux/company-communications',{method:'PATCH',data:{...d,approved:f.approved.checked}});UX.companyComms=r;modal.close();toast('Company email rules saved.');return navigate('connections');}
 }catch(ex){modalError(ex);}
});

// Keep the direct page actions working after simplifying the sidebar.
document.addEventListener('click',e=>{const p=e.target.closest('[data-page]');if(p&&!p.classList.contains('nav-button')){e.preventDefault();navigate(p.dataset.page);}});

// First screen: field users go to My Jobs; everyone else goes to Today.
const uxBootstrap=bootstrap;
bootstrap=async function(){const out=await uxBootstrap();if(S.user?.role==='crew'){S.jobScope='mine';if(S.page!=='schedule')await navigate('schedule');}else if(S.user&&S.page!=='dashboard'){await navigate('dashboard');}return out;};
bootstrap();
