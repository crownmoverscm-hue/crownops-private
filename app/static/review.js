/* CrownOps v5 review-build workflows. Loaded after app.js + platform.js. */
'use strict';

// ---------- Navigation and field-user simplification ----------
const ensureNav=(item)=>{if(!nav.some(n=>n[0]===item[0]))nav.push(item);};
ensureNav(['timeoff','calendar','Time off']);
ensureNav(['documents','file','Documents & approvals']);
const inventoryNav=nav.find(n=>n[0]==='inventory');if(inventoryNav)inventoryNav[2]='Materials & inventory';
const rateNav=nav.find(n=>n[0]==='rates');if(rateNav)rateNav[2]='Pricing & rates';
const scheduleNav=nav.find(n=>n[0]==='schedule');if(scheduleNav)scheduleNav[2]='Schedule';
const order=['dashboard','sales','quotes','schedule','invoices','followups','payments','team','timeoff','payroll','inventory','documents','fleet','care','calls','outbox','portalhub','automations','rates','connections','policies','settings','account'];
nav.sort((a,b)=>order.indexOf(a[0])-order.indexOf(b[0]));
allowedPages=function(){
 if(S.user.role==='owner'||S.user.role==='sales')return nav.map(x=>x[0]);
 if(S.user.role==='crew')return ['schedule','timeoff','invoices','team','documents','account'];
 if(S.user.role==='dispatch')return ['schedule','invoices','team','timeoff','inventory','documents','fleet','care','portalhub','account'];
 return ['schedule','invoices','payments','team','timeoff','inventory','documents','portalhub','fleet','payroll','care','connections','policies','account'];
};

S.jobScope='mine';
const reviewBaseShell=shell;
shell=function(){
 const role=S.user.role,allowed=allowedPages();
 if(role!=='crew')return reviewBaseShell();
 const crewOrder=['schedule','timeoff','invoices','team','documents','account'];
 const items=crewOrder.map(k=>nav.find(n=>n[0]===k)).filter(Boolean);
 const navHtml='<div class="nav-label">FIELD WORK</div>'+items.map(n=>{const label=n[0]==='schedule'?'Jobs':n[2];return `<button class="nav-button ${S.page===n[0]?'active':''}" data-page="${n[0]}">${icon(n[1])}<span>${h(label)}</span></button>`;}).join('');
 root.innerHTML=`<div class="app"><aside class="sidebar" id="sidebar"><div class="brand"><div class="brand-icon">${crown}</div><div><strong>CrownOps</strong><small>CROWN MOVERS</small></div></div>${navHtml}<div class="sidebar-foot"><div class="workspace-label"><span class="dot"></span>Private review workspace<div class="tiny" style="color:#91aacd;margin-top:4px">Your assigned jobs open first. Live systems remain unchanged.</div></div><div class="profile"><div class="avatar">${h(initials(S.user.name))}</div><div><div style="color:white;font-size:12px;font-weight:600">${h(S.user.name)}</div><div style="font-size:10px">Mover access</div></div>${btn('logout',icon('logout'),'logout','aria-label="Sign out"')}</div></div></aside><main class="main"><header class="topbar">${btn('menu',icon('menu'),'mobile-toggle btn-icon','aria-label="Open navigation"')}<div class="breadcrumb">Crown Movers ${icon('right')} <b id="crumb">Jobs</b></div><div class="top-right"><span class="date-label">Montr&eacute;al time &middot; ET</span><div class="avatar">${h(initials(S.user.name))}</div></div></header><div class="pilot-strip" id="pilot-strip">${icon('shield')}<span>Private review build v5.0. Test only; live schedule, Gmail, accounting and payments are not synchronized.</span></div><section class="content" id="content"></section></main></div>`;
};

// ---------- Small helpers ----------
const ymd=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const addDays=(d,n)=>{const x=new Date(d);x.setDate(x.getDate()+n);return x;};
const monthStart=v=>{const d=v?new Date(v+'T12:00:00'):new Date(today()+'T12:00:00');return new Date(d.getFullYear(),d.getMonth(),1,12);};
const within=(day,a,b)=>day>=a&&day<=b;
const v5btn=(action,label,id='',cls='',attrs='')=>`<button class="${cls}" data-v5="${action}" data-id="${h(id)}" ${attrs}>${label}</button>`;
const v5f=(kind,title,body,button='Save',context={})=>{X.formContext=context;showModal(title,`<form id="v5form" data-v5-form="${kind}"><div class="grid2">${body}</div></form>`,`${btn('close','Cancel')}<button type="submit" form="v5form" class="btn-primary">${button}</button>`,true);};
const noLiveSend=`<div class="callout orange"><strong>Review build only</strong>Email/text delivery remains draft/copy-only until Crown connects and tests the approved providers. Nothing on this page sends automatically.</div><div class="spacer"></div>`;

// ---------- Manager notifications on Command Center ----------
const reviewBaseDashboard=dashboard;
dashboard=async function(){
 const html=await reviewBaseDashboard();
 if(S.user.role!=='owner')return html;
 const [notices,offs]=await Promise.all([api('/manager-notifications'),api('/time-off')]);
 const unread=notices.filter(n=>n.status==='Unread'),pending=offs.filter(r=>r.status==='Pending');
 return html+`<div class="spacer"></div><div class="grid2">${panel('Manager notifications',unread.length?unread.slice(0,6).map(n=>`<div class="task-row"><div class="grow"><div class="small strong">${h(n.message)}</div><div class="tiny muted">${h(n.created_at)}</div></div>${v5btn('notice-read','Mark read',n.id,'btn-small')}</div>`).join(''):empty('No unread manager notifications','New time-off requests and cancellations will appear here.'))}${panel('Pending time off',pending.length?pending.slice(0,6).map(r=>`<div class="task-row"><div class="grow"><div class="small strong">${h(r.employee_name)}</div><div class="tiny muted">${h(r.start_date)} to ${h(r.end_date)}</div></div>${v5btn('open-timeoff','Review',r.id,'btn-small')}</div>`).join(''):empty('No time-off requests waiting','Employee requests will appear here for approval.'))}</div>`;
};

// ---------- My Jobs / All Jobs and approved OFF blocks ----------
schedulePage=async function(){
 const isCrew=S.user.role==='crew';
 if(isCrew&&!['mine','all'].includes(S.jobScope))S.jobScope='mine';
 const [jobs, imported, off]=await Promise.all([
   api('/jobs'),
   isCrew?Promise.resolve([]):api('/schedule-imports/events'),
   api('/time-off').catch(()=>[])
 ]);
 S.importedSchedule=imported;
 const working=(isCrew&&S.jobScope==='mine'?jobs.filter(j=>j.crew_ids.includes(S.user.employee_id)):jobs);
 const all=[...working.map(j=>({id:j.id,date:localDay(j.start_at),time:timeLabel(j.start_at),title:j.title,phase:j.phase,status:j.status,crew:j.crew_names.join(' + '),crewCount:j.crew_names.length,truck:j.truck_names.join(', '),imported:false})),
   ...(!isCrew?imported.filter(j=>!j.promoted_job_id).map(j=>({id:j.id,date:j.date,time:j.time_label,title:j.client_name,phase:j.service,status:'Imported - review',crew:j.crew_raw,truck:j.truck_label,imported:true})):[])].sort((a,b)=>(a.date+a.time).localeCompare(b.date+b.time));
 const approvedOff=off.filter(r=>r.status==='Approved');
 const days=weekDays(),ids=days.map(dateIso),wk=all.filter(j=>ids.includes(j.date));
 const card=j=>`<button class="job-card" data-action="${j.imported?'open-imported-job':'open-job'}" data-id="${h(j.id)}"><div class="status">${h(j.time)} &middot; ${h(j.status)}</div><strong>${h(j.title)}</strong><div>${pill(j.phase)}</div><small>${j.imported?h(j.crew||'Crew needs review'):`<b>${h(j.crewCount||0)} Movers</b> &middot; ${h(j.crew||'Crew needs review')}`}</small><span class="tiny strong">${h(j.truck||'Truck needs review')}</span></button>`;
 const offForDay=day=>approvedOff.filter(r=>within(day,r.start_date,r.end_date));
 const offCard=r=>`<div class="off-card"><div class="status">OFF &middot; approved</div><strong>${h(r.employee_name)}</strong><small>${h(r.start_date===r.end_date?r.start_date:r.start_date+' to '+r.end_date)}</small></div>`;
 const scope=isCrew?`<div class="segmented job-scope">${v5btn('jobs-mine','My Jobs','',''+(S.jobScope==='mine'?'selected':''))}${v5btn('jobs-all','All Jobs','',''+(S.jobScope==='all'?'selected':''))}</div>`:'';
 const title=isCrew?'Jobs':'Schedule';
 const sub=isCrew?(S.jobScope==='mine'?'Jobs assigned to you. Switch to All Jobs anytime.':'Company jobs. Jobs you are not assigned to are read-only.'):'Working jobs, approved staff time off, and imported Crown Schedule records.';
 return head(title,sub,may(['owner','sales','dispatch'])?btn('new-job',icon('plus')+'New job','btn-primary'):'')+
 (isCrew?scope+'<div class="spacer"></div>':`<div class="callout orange"><strong>${imported.length} original schedule entries imported for review</strong>Imported source entries are not new confirmations. Approved time off is shown directly on this calendar and blocks assignment.</div><div class="spacer"></div>`)+
 `<div class="calendar-tools"><div class="row">${btn('week-prev',icon('left'),'btn-icon')}${btn('week-today','Today')}${btn('week-next',icon('right'),'btn-icon')}<span class="date-range">${dateLabel(ids[0])} &ndash; ${dateLabel(ids[6])}</span><input type="date" id="calendar-jump" aria-label="Jump to schedule date" value="${ids[0]}" style="max-width:150px"></div><div class="segmented">${btn('view-week','Week',S.scheduleView==='week'?'selected':'')}${!isCrew?btn('view-list','All imported dates',S.scheduleView==='list'?'selected':''):''}</div></div>
 ${S.scheduleView==='week'||isCrew?`<div class="panel calendar-scroll"><div class="week-grid">${days.map(d=>{const day=dateIso(d);return `<div class="day-col"><div class="day-head"><small>${d.toLocaleDateString('en-CA',{weekday:'short'})}</small><b>${d.getDate()}</b></div><div class="day-jobs">${offForDay(day).map(offCard).join('')}${wk.filter(j=>j.date===day).map(card).join('')}</div></div>`;}).join('')}</div></div>`:
 `<div class="panel"><div class="table-wrap"><table><thead><tr><th>Date</th><th>Client / phase</th><th>Time</th><th>Crew / truck</th><th>Status</th><th></th></tr></thead><tbody>${all.map(j=>`<tr><td>${h(j.date)}</td><td><strong>${h(j.title)}</strong><br>${h(j.phase)}</td><td>${h(j.time)}</td><td>${h(j.crew)}<br>${h(j.truck)}</td><td>${pill(j.status)}</td><td>${btn(j.imported?'open-imported-job':'open-job','Open','btn-small',`data-id="${h(j.id)}"`)}</td></tr>`).join('')}</tbody></table></div></div>`}
 <p class="source-warning">Approved OFF dates are a hard scheduling block. Pending requests are not OFF until management approves them.</p>`;
};

// ---------- Time Off ----------
X.timeOffView='month';X.timeOffMonth=monthStart();
function timeOffMonthGrid(items){
 const start=new Date(X.timeOffMonth),first=new Date(start);first.setDate(1-first.getDay());
 const cells=Array.from({length:42},(_,i)=>addDays(first,i));
 return `<div class="month-grid">${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(x=>`<div class="month-head">${x}</div>`).join('')}${cells.map(d=>{const day=ymd(d),outside=d.getMonth()!==start.getMonth(),events=items.filter(r=>within(day,r.start_date,r.end_date));return `<div class="month-day ${outside?'outside':''}"><b>${d.getDate()}</b>${events.map(r=>`<div class="timeoff-chip ${r.status.toLowerCase()}">${h(r.employee_name)} &middot; ${h(r.status)}</div>`).join('')}</div>`;}).join('')}</div>`;
}
function timeOffWeekGrid(items){
 const base=new Date(X.timeOffMonth);base.setDate(base.getDate()-base.getDay());
 const days=Array.from({length:7},(_,i)=>addDays(base,i));
 return `<div class="week-grid timeoff-week">${days.map(d=>{const day=ymd(d),events=items.filter(r=>within(day,r.start_date,r.end_date));return `<div class="day-col"><div class="day-head"><small>${d.toLocaleDateString('en-CA',{weekday:'short'})}</small><b>${d.getDate()}</b></div><div class="day-jobs">${events.map(r=>`<div class="off-card ${r.status.toLowerCase()}"><strong>${h(r.employee_name)}</strong><small>${h(r.status)}</small></div>`).join('')}</div></div>`;}).join('')}</div>`;
}
X.pageRenderers.timeoff=async()=>{
 const items=await api('/time-off');X.cache.timeoff=items;
 const pending=items.filter(r=>r.status==='Pending');
 const actions=S.user.role==='crew'?v5btn('request-off','Request OFF','','btn-primary'):may(['owner'])?v5btn('request-off','Add request','','btn-primary'):'';
 const view=`<div class="calendar-tools"><div class="row">${v5btn('off-prev',icon('left'),'','btn-icon')}${v5btn('off-today','Today')}${v5btn('off-next',icon('right'),'','btn-icon')}<span class="date-range">${X.timeOffMonth.toLocaleDateString('en-CA',{month:'long',year:'numeric'})}</span></div><div class="segmented">${v5btn('off-month','Month','',''+(X.timeOffView==='month'?'selected':''))}${v5btn('off-week','Week','',''+(X.timeOffView==='week'?'selected':''))}</div></div>`;
 const queue=S.user.role==='owner'&&pending.length?panel('Pending approval',xtable(['Employee','Dates','Note',''],pending.map(r=>[h(r.employee_name),h(r.start_date)+' &rarr; '+h(r.end_date),h(r.note||''),v5btn('timeoff-review','Review',r.id,'btn-small')]))):'';
 const list=panel(S.user.role==='crew'?'My requests':'All requests',xtable(['Employee','Dates','Status','Requested',''],items.map(r=>[h(r.employee_name),h(r.start_date)+' &rarr; '+h(r.end_date),pill(r.status),h(r.requested_at.slice(0,10)),S.user.role==='crew'&&['Pending','Approved'].includes(r.status)?v5btn('timeoff-cancel',r.status==='Pending'?'Withdraw':'Cancel OFF',r.id,'btn-small'):S.user.role==='owner'&&r.status==='Pending'?v5btn('timeoff-review','Review',r.id,'btn-small'):''])));
 return head('Time off',S.user.role==='crew'?'Pick the days you want off. Management approves the request before it becomes a scheduling block.':'Review pending requests and see collective approved OFF dates.',actions)+
 xnote('Approved OFF dates cannot be assigned to a job by anyone. Only the employee can cancel their own approved OFF request and become available again.','teal')+view+`<div class="panel calendar-scroll">${X.timeOffView==='month'?timeOffMonthGrid(items):timeOffWeekGrid(items)}</div><div class="spacer"></div>${queue}${queue?'<div class="spacer"></div>':''}${list}`;
};
async function requestOffForm(){
 const employees=S.user.role==='owner'?await api('/employees'):[];
 const who=S.user.role==='owner'?select('employee_id','Employee',employees.filter(e=>e.active).map(e=>[e.id,e.name]),employees[0]?.id||'','required'):'';
 v5f('timeoff-request','Request time off',who+field('start_date','First day off',today(),'date','required')+field('end_date','Last day off',today(),'date','required')+textArea('note','Optional note / reason','',3),'Submit request');
}
async function reviewTimeOff(id){const r=X.cache.timeoff.find(x=>x.id===id);v5f('timeoff-review','Review time off',`<div class="full callout"><strong>${h(r.employee_name)}</strong>${h(r.start_date)} to ${h(r.end_date)}<br>${h(r.note||'No note supplied')}</div>`+select('decision','Decision',[['Approved','Approve OFF'],['Declined','Decline']],'Approved')+textArea('note','Manager note','',3),'Save decision',{id});}

// ---------- Bulk team hours ----------
const reviewBaseAddTime=addTime;
addTime=async function(jid){
 if(S.user.role==='crew')return reviewBaseAddTime(jid);
 const data=await api('/jobs/'+jid+'/team-hours');X.cache.teamHours=data;
 const existing=data.employees.filter(e=>e.service_minutes!=null);
 let commonS=data.invoice_service_hours??(existing.length?existing[0].service_minutes/60:'');
 let commonT=data.approved_transport_hours??(existing.length?existing[0].transport_minutes/60:0);
 const rowsHtml=data.employees.map(e=>{const has=e.service_minutes!=null;const same=has&&Number(e.service_minutes/60)===Number(commonS)&&Number(e.transport_minutes/60)===Number(commonT);return `<div class="team-hour-row" data-employee="${h(e.id)}"><label class="checkbox-label"><input type="checkbox" name="common_${h(e.id)}" ${!has||same?'checked':''}> <span><strong>${h(e.name)}</strong><br><span class="tiny muted">Use the team hours</span></span></label><div>${field('exs_'+e.id,'Individual service',has?e.service_minutes/60:'','number',`${!has||same?'disabled':''} step="0.25" min="0" max="24"`)}</div><div>${field('ext_'+e.id,'Individual transport',has?e.transport_minutes/60:'','number',`${!has||same?'disabled':''} step="0.25" min="0" max="24"`)}</div><div>${field('exn_'+e.id,'Reason',has?e.note:'','text',`${!has||same?'disabled':''}`)}</div></div>`;}).join('');
 showModal('Team hours | '+h(data.job.title),`<form id="team-hours-form" data-v5-form="team-hours"><input type="hidden" name="job_id" value="${h(jid)}"><div class="callout teal"><strong>Enter once for the team, adjust only the exceptions.</strong>Everyone is selected for the common hours by default. Uncheck a worker only when that person worked different service or transport time.</div><div class="spacer"></div><div class="grid2">${field('service_hours','Team service hours',commonS,'number','step="0.25" min="0" max="24" required')}${field('transport_hours','Team transport hours',commonT,'number','step="0.25" min="0" max="24" required')}</div><div class="row wrap" style="margin-top:12px">${v5btn('copy-invoice-team','Copy final-invoice hours','','btn-soft')}${v5btn('team-select-all','Select all')}${v5btn('team-exceptions-clear','Clear exceptions')}</div><div class="spacer"></div><div class="team-hours-grid"><div class="team-hours-head">Use team hours</div><div class="team-hours-head">Service exception</div><div class="team-hours-head">Transport exception</div><div class="team-hours-head">Reason</div>${rowsHtml}</div>${textArea('note','Team time note','Actual hours confirmed for assigned team.',2)}<div class="spacer"></div><p class="tiny muted">Each employee still receives an individual time record behind the scenes. Approved time is never silently overwritten.</p></form>`,`${btn('close','Cancel')}${data.job.status==='Completed'&&may(['owner','accounting'])?v5btn('team-hours-approve','Approve submitted team hours',jid,'btn-soft'):''}<button type="submit" form="team-hours-form" class="btn-primary">Save team hours</button>`,true);
};
const reviewBaseTeam=teamPage;
teamPage=async function(){
 const html=await reviewBaseTeam();
 if(!may(['owner','dispatch','accounting']))return html;
 const jobs=(await api('/jobs')).filter(j=>['In progress','Completed'].includes(j.status));
 return html+`<div class="spacer"></div>${panel('Service team hours',jobs.length?xtable(['Service','Status','Assigned team',''],jobs.map(j=>[h(j.title)+'<br><span class="tiny muted">'+h(j.phase)+'</span>',pill(j.status),h(j.crew_names.join(', ')),v5btn('team-hours-open','Enter / review team hours',j.id,'btn-small')])):empty('No services awaiting team hours','Start or complete a job, then enter common team hours once and adjust exceptions.'))}`;
};

// ---------- Pricing & rates ----------
X.rateYear=new Date().getFullYear();
X.pageRenderers.rates=async()=>{
 const [periods,daily]=await Promise.all([api('/annual-rates?year='+X.rateYear),api('/daily-rates')]);X.cache.ratePeriods=periods;X.cache.dailyRates=daily;
 const yearOptions=[X.rateYear-1,X.rateYear,X.rateYear+1,X.rateYear+2].map(y=>`<option ${y===X.rateYear?'selected':''}>${y}</option>`).join('');
 const actions=may(['owner'])?`${v5btn('rate-period-new','Add rate period','','btn-primary')}${xbtn('daily-rate','Exact-day override')}`:'';
 const table=xtable(['Date range','Label','2 movers','3 movers','4 movers','5 movers','6 movers','State',''],periods.map(r=>[h(r.start_date)+' &rarr; '+h(r.end_date),h(r.label),money(r.rate_2_cents),money(r.rate_3_cents),money(r.rate_4_cents),money(r.rate_5_cents),money(r.rate_6_cents),pill(r.active?'Active':'Inactive'),may(['owner'])?v5btn('rate-period-edit','Edit',r.id,'btn-small'):'']));
 const exact=daily.filter(r=>String(r.service_date).startsWith(String(X.rateYear))).slice(0,100);
 return head('Pricing & rates','Manage the hourly crew rates used for NEW estimates. Accepted quote revisions remain frozen.',actions)+xnote('Rate priority for a new estimate: exact-day override → active annual rate period → imported seasonal snapshot. Changing future rates never rewrites an accepted quote.','teal')+`<div class="filterbar"><label class="row small">Year <select id="v5-rate-year">${yearOptions}</select></label></div>${panel('Annual hourly rates',table)}<div class="spacer"></div>${panel('Exact-day overrides',xtable(['Date','Crew','Hourly rate','Note'],exact.map(r=>[h(r.service_date),h(r.movers)+' movers',money(r.hourly_cents),h(r.note)])))}`;
};
function ratePeriodForm(r=null){
 const data=r||{start_date:`${X.rateYear}-01-01`,end_date:`${X.rateYear}-03-31`,label:'',rate_2_cents:13500,rate_3_cents:17500,rate_4_cents:22000,rate_5_cents:26000,rate_6_cents:31500,active:1,version:1,note:''};
 v5f('rate-period',r?'Edit rate period':'Add rate period',field('label','Period name',data.label,'text','required')+field('start_date','Start date',data.start_date,'date','required')+field('end_date','End date',data.end_date,'date','required')+[2,3,4,5,6].map(n=>field('rate_'+n,n+' movers hourly rate',(data['rate_'+n+'_cents']||0)/100,'number','step="0.01" min="1" required')).join('')+checkedField('active','Active for new quotes',Boolean(data.active))+textArea('note','Management approval note',data.note||'',3),r?'Save changes':'Add period',{id:r?.id||null,version:r?.version||null});
}

// ---------- Materials ----------
inventoryPage=async function(){
 const items=await api('/materials');X.cache.materials=items;
 const actions=may(['owner'])?v5btn('material-new','Add material','','btn-primary'):'';
 return head('Materials & inventory','One editable list for material pricing and stock used by new job billing setups.',actions)+xnote('Managers control catalogue prices. New invoice setups snapshot the active material prices so later price changes do not rewrite an existing job invoice.','teal')+panel('Material catalogue',xtable(['Material','Category','Sell price',may(['owner','accounting'])?'Cost':'Stock','Quantity / minimum','Tax','State',''],items.map(m=>[h(m.name)+'<br><span class="tiny muted">'+h(m.code)+'</span>',h(m.category),money(m.sell_cents),may(['owner','accounting'])?money(m.cost_cents):h(m.location),h(m.quantity)+' / '+h(m.minimum),m.taxable?'Taxable':'Review',pill(m.active?'Active':'Inactive'),may(['owner'])?v5btn('material-edit','Edit',m.id,'btn-small'):''])));
};
function materialForm(m=null){
 const d=m||{code:'',name:'',category:'Moving material',sell_cents:0,cost_cents:0,quantity:0,minimum:0,location:'Warehouse',taxable:1,active:1,version:1};
 const code=m?`<div class="form-field"><label>Material code</label><input value="${h(d.code)}" disabled></div>`:field('code','Material code','','text','required');
 v5f('material',m?'Edit material':'Add material',code+field('name','Material name',d.name,'text','required')+field('category','Category',d.category,'text','required')+field('sell_price','Client selling price',d.sell_cents/100,'number','step="0.01" min="0" required')+field('cost_price','Internal cost',d.cost_cents/100,'number','step="0.01" min="0"')+field('quantity','Current quantity',d.quantity,'number','step="1" min="0"')+field('minimum','Reorder minimum',d.minimum,'number','step="1" min="0"')+field('location','Location',d.location)+checkedField('taxable','Taxable / follow approved billing policy',Boolean(d.taxable))+checkedField('active','Active and available for new jobs',Boolean(d.active)),m?'Save material':'Add material',{id:m?.id||null,version:m?.version||null});
}
// New invoice setups use the managed material catalogue.
setupInvoice=async function(jid){const [jobs,quotes,materials]=await Promise.all([api('/jobs'),api('/quotes'),api('/materials')]);const j=jobs.find(x=>x.id===jid);if(!j)throw Error('Job not found.');const q=quotes.find(x=>x.id===j.quote_id),r=q?.result||{};showModal('Manager: approve billing for '+h(j.title),`<p class="small">Review the accepted job terms before enabling team invoicing. Material prices below come from the current active manager catalogue and are frozen into this job once approved.</p><form id="invoice-setup-form" data-form="invoice-setup"><input type="hidden" name="job_id" value="${h(jid)}"><div class="grid2">${field('issuer_name','Legal business name','Crown Movers','text','required')}${field('issuer_address','Business billing address','','text','required')}${field('issuer_email','Business email','info@crownmovers.ca','email','required')}${field('issuer_phone','Business phone','514-606-4030')}${field('customer_name','Bill to',j.client.client_name,'text','required')}${field('customer_address','Client billing address',j.client.dropoff)}${field('customer_email','Client email',j.client.email,'email')}${field('hourly_rate','Agreed team rate',r.hourly_rate_cents?r.hourly_rate_cents/100:'','number','step="0.01" min="1" required')}${field('minimum_hours','Agreed service minimum',r.minimum_hours??'','number','step="0.25" min="0" required')}${field('transport_amount','Agreed transport amount',r.transport_cents!==undefined?r.transport_cents/100:'','number','step="0.01" min="0" required')}${field('fuel_amount','Agreed fuel amount',r.fuel_cents!==undefined?r.fuel_cents/100:'','number','step="0.01" min="0" required')}${field('discount_amount','Agreed discount',r.discount_cents?r.discount_cents/100:0,'number','step="0.01" min="0" required')}</div>${textArea('taxes_json','Reviewed tax lines JSON: name, rate_percent, registration. Enter [] only when explicitly not applicable.','',4)}${textArea('catalogue_json','Active material prices snapshot',JSON.stringify(materials.filter(x=>x.active&&x.sell_cents>0).map(x=>({code:x.code,name:x.name,unit_price:x.sell_cents/100})),null,2),7)}${textArea('approval_reference','Accepted terms reference and review note','',3)}<label class="checkbox-label"><input type="checkbox" name="confirmed" required>I reviewed the accepted prices, tax treatment, and material prices. I authorize the assigned team to enter actuals and finalize this job invoice.</label></form>`,formFooter('invoice-setup-form','Approve billing setup'),true);};

// ---------- Documents & approvals ----------
X.pageRenderers.documents=async()=>{
 const [templates,approvals]=await Promise.all([api('/document-templates'),api('/document-approvals')]);X.cache.docTemplates=templates;X.cache.docApprovals=approvals;
 const actions=`${v5btn('document-prepare','Prepare client approval','','btn-primary')}${may(['owner'])?v5btn('template-new','New template'):''}`;
 const templatesPanel=panel('Approved template library',xtable(['Template','Category','Version','State',''],templates.map(t=>[h(t.name)+'<br><span class="tiny muted">'+h(t.note||'')+'</span>',h(t.category),'v'+h(t.version),pill(t.active?'Active':'Inactive'),may(['owner'])?v5btn('template-edit','Edit',t.id,'btn-small'):''])));
 const approvalPanel=panel('Client approval requests',xtable(['Client / job','Document','Delivery','Status','Created',''],approvals.map(a=>[h(a.client_name)+'<br><span class="tiny muted">'+h(a.job_title||'No job')+'</span>',h(a.template_name)+' v'+h(a.template_version),h(a.delivery_method),pill(a.status),h(a.created_at.slice(0,16).replace('T',' ')),v5btn('document-view','Open',a.id,'btn-small')])));
 return head('Documents & approvals','Use locked templates for job acknowledgements and additional-work approvals.',actions)+noLiveSend+xnote('Movers can prepare an active non-manager-only template only for a job they are assigned to. They cannot rewrite the locked template wording. Managers control the template library.','teal')+templatesPanel+'<div class="spacer"></div>'+approvalPanel;
};
async function prepareDocument(){
 const [templates,jobs]=await Promise.all([api('/document-templates'),api('/jobs')]);
 const usableJobs=S.user.role==='crew'?jobs.filter(j=>j.crew_ids.includes(S.user.employee_id)):jobs;
 if(!usableJobs.length)return toast('No eligible job is available for this approval.');
 v5f('document-prepare','Prepare client approval',select('job_id','Job',usableJobs.map(j=>[j.id,j.title+' | '+localDay(j.start_at)]),usableJobs[0].id,'required')+select('template_id','Document template',templates.filter(t=>t.active&&!((S.user.role==='crew')&&t.manager_only)).map(t=>[t.id,t.name+' | v'+t.version]),templates[0]?.id||'','required')+select('delivery_method','Prepare for',[['Email','Email draft'],['Text','Text message'],['Copy link','Copy private link']],'Text')+field('recipient','Recipient (optional - Crown client contact used if blank)','')+textArea('details','Job-specific facts / extra work / condition','',6),'Prepare approval link');
}
function templateForm(t=null){
 const d=t||{name:'',category:'Acknowledgement',body:'',active:1,manager_only:0,note:'Legal review required before live use.',version:1};
 v5f('document-template',t?'Edit document template':'New document template',field('name','Template name',d.name,'text','required')+field('category','Category',d.category,'text','required')+textArea('body','Locked template wording',d.body,12)+checkedField('active','Active for staff use',Boolean(d.active))+checkedField('manager_only','Management-only template',Boolean(d.manager_only))+textArea('note','Internal note / legal-review status',d.note||'',3),t?'Save new version':'Create template',{id:t?.id||null,version:t?.version||null});
}
function documentView(a){showModal(h(a.template_name),`<div class="row">${pill(a.status)}<span class="tiny muted">Template v${h(a.template_version)} &middot; ${h(a.delivery_method)}</span></div><div class="spacer"></div><div class="message-draft">${h(a.rendered_body)}</div><div class="spacer"></div><div class="info-grid"><div><div class="info-label">Client</div>${h(a.client_name)}</div><div><div class="info-label">Job</div>${h(a.job_title||'Not linked')}</div><div><div class="info-label">Signed name</div>${h(a.signed_name||'Not yet approved')}</div><div><div class="info-label">Response</div>${h(a.response_note||'')}</div></div>`,btn('close','Close'));}
// Add quick access from a job.
const reviewBaseOpenJob=openJob;
openJob=async function(id){
 const jobs=await api('/jobs'),j=jobs.find(x=>x.id===id);if(!j)throw Error('Job not found.');
 const assigned=S.user.role!=='crew'||j.crew_ids.includes(S.user.employee_id);
 if(S.user.role==='crew'&&!assigned){
   S.modalContext={job:j};
   return showModal(h(j.title),`<div class="row">${pill(j.phase)}${pill(j.status)}<div class="grow"></div><span class="tiny muted">All Jobs &middot; read only</span></div><div class="spacer"></div><div class="info-grid"><div><div class="info-label">Crew block</div>${dateLabel(localDay(j.start_at))}<br>${timeLabel(j.start_at)} &ndash; ${timeLabel(j.end_at)}</div><div><div class="info-label">Assigned team</div>${h(j.crew_names.join(', '))}<div class="tiny muted">${h(j.truck_names.join(', '))}</div></div><div><div class="info-label">Pickup</div>${h(j.client.pickup)}<div class="tiny muted">${h(j.client.pickup_access)}</div></div><div><div class="info-label">Destination</div>${h(j.client.dropoff)}<div class="tiny muted">${h(j.client.dropoff_access)}</div></div><div><div class="info-label">Contact</div>${h(j.client.phone)}</div><div><div class="info-label">Additional stops</div>${j.stops.map(h).join('<br>')||'None recorded'}</div></div><div class="spacer"></div><h3>Inventory &amp; job notes</h3><div class="message-draft">${h([j.client.inventory,j.client.heavy_items,j.notes].filter(Boolean).join('\n\n')||'No notes recorded.')}</div><div class="spacer"></div><div class="callout"><strong>View only</strong>You are not assigned to this service. Start/finish, invoice, hours and approval actions are available only to the assigned team.</div>`,btn('close','Close'),true);
 }
 await reviewBaseOpenJob(id);
 const foot=document.querySelector('.modal-foot');if(foot)foot.insertAdjacentHTML('afterbegin',v5btn('document-prepare-job','Client approval',id));
};

// ---------- Review-build event handlers ----------
document.addEventListener('click',async e=>{
 const el=e.target.closest('[data-v5]');if(!el)return;e.preventDefault();const a=el.dataset.v5,id=el.dataset.id;
 try{
  if(a==='jobs-mine'){S.jobScope='mine';S.scheduleView='week';return navigate('schedule');}
  if(a==='jobs-all'){S.jobScope='all';S.scheduleView='week';return navigate('schedule');}
  if(a==='request-off')return requestOffForm();
  if(a==='timeoff-review')return reviewTimeOff(id);
  if(a==='timeoff-cancel'){if(!confirm('Remove this OFF request and make yourself available again?'))return;await api('/time-off/'+id+'/cancel',{method:'POST',data:{}});toast('Time off cancelled. Managers were notified.');return navigate('timeoff');}
  if(a==='open-timeoff'){return navigate('timeoff');}
  if(a==='notice-read'){await api('/manager-notifications/'+id+'/read',{method:'POST',data:{}});return navigate('dashboard');}
  if(a==='off-month'){X.timeOffView='month';return navigate('timeoff');}
  if(a==='off-week'){X.timeOffView='week';return navigate('timeoff');}
  if(a==='off-today'){X.timeOffMonth=monthStart();return navigate('timeoff');}
  if(a==='off-prev'){X.timeOffMonth=addDays(X.timeOffMonth,X.timeOffView==='month'?-28:-7);X.timeOffMonth=X.timeOffView==='month'?monthStart(ymd(X.timeOffMonth)):X.timeOffMonth;return navigate('timeoff');}
  if(a==='off-next'){X.timeOffMonth=addDays(X.timeOffMonth,X.timeOffView==='month'?35:7);X.timeOffMonth=X.timeOffView==='month'?monthStart(ymd(X.timeOffMonth)):X.timeOffMonth;return navigate('timeoff');}
  if(a==='team-hours-open')return addTime(id);
  if(a==='copy-invoice-team'){const d=X.cache.teamHours;if(d.invoice_service_hours!=null)document.querySelector('#team-hours-form [name=service_hours]').value=d.invoice_service_hours;if(d.approved_transport_hours!=null)document.querySelector('#team-hours-form [name=transport_hours]').value=d.approved_transport_hours;return toast('Final-invoice / approved travel hours copied. Review employee exceptions.');}
  if(a==='team-select-all'||a==='team-exceptions-clear'){document.querySelectorAll('#team-hours-form input[name^="common_"]').forEach(c=>{c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));});return;}
  if(a==='team-hours-approve'){if(!confirm('Approve all submitted employee hours for this completed service?'))return;await api('/jobs/'+id+'/team-hours/approve',{method:'POST',data:{reviewed:true}});toast('Team hours approved. Payroll has not been paid.');return addTime(id);}
  if(a==='rate-period-new')return ratePeriodForm();
  if(a==='rate-period-edit')return ratePeriodForm(X.cache.ratePeriods.find(r=>r.id===id));
  if(a==='material-new')return materialForm();
  if(a==='material-edit')return materialForm(X.cache.materials.find(m=>m.id===id));
  if(a==='document-prepare')return prepareDocument();
  if(a==='document-prepare-job'){const [templates,jobs]=await Promise.all([api('/document-templates'),api('/jobs')]);const j=jobs.find(x=>x.id===id);v5f('document-prepare','Prepare client approval',`<input type="hidden" name="job_id" value="${h(id)}">`+select('template_id','Document template',templates.filter(t=>t.active&&!((S.user.role==='crew')&&t.manager_only)).map(t=>[t.id,t.name+' | v'+t.version]),templates[0]?.id||'','required')+select('delivery_method','Prepare for',[['Email','Email draft'],['Text','Text message'],['Copy link','Copy private link']],'Text')+field('recipient','Recipient (optional)','')+textArea('details','Job-specific facts / extra work / condition','',6),'Prepare approval link',{job:j});return;}
  if(a==='template-new')return templateForm();
  if(a==='template-edit')return templateForm(X.cache.docTemplates.find(t=>t.id===id));
  if(a==='document-view')return documentView(X.cache.docApprovals.find(x=>x.id===id));
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}
});

document.addEventListener('change',e=>{
 if(e.target.id==='v5-rate-year'){X.rateYear=Number(e.target.value);navigate('rates');return;}
 if(e.target.matches('#team-hours-form input[name^="common_"]')){const row=e.target.closest('.team-hour-row');row.querySelectorAll('input[name^="ex"]').forEach(i=>{i.disabled=e.target.checked;if(e.target.checked)i.value='';});}
});

document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-v5-form]');if(!f)return;e.preventDefault();const kind=f.dataset.v5Form,d=readForm(f),ctx=X.formContext||{};const submit=document.querySelector('button[form="'+f.id+'"]');if(submit)submit.disabled=true;
 try{
  if(kind==='timeoff-request'){await api('/time-off',{method:'POST',data:d});modal.close();toast('Time-off request sent to management for approval.');return navigate('timeoff');}
  if(kind==='timeoff-review'){await api('/time-off/'+ctx.id+'/review',{method:'POST',data:d});modal.close();toast('Time-off decision saved.');return navigate('timeoff');}
  if(kind==='team-hours'){const team=X.cache.teamHours;const employee_ids=team.employees.map(x=>x.id),exceptions={};for(const emp of team.employees){const common=f.elements['common_'+emp.id].checked;if(!common)exceptions[emp.id]={service_hours:Number(d['exs_'+emp.id]),transport_hours:Number(d['ext_'+emp.id]||0),note:d['exn_'+emp.id]};}await api('/jobs/'+d.job_id+'/team-hours',{method:'POST',data:{employee_ids,service_hours:Number(d.service_hours),transport_hours:Number(d.transport_hours||0),note:d.note,exceptions}});toast('Team hours saved as individual employee records.');return addTime(d.job_id);}
  if(kind==='rate-period'){const payload={...d,active:f.active.checked};for(let n=2;n<=6;n++)payload['rate_'+n]=Number(d['rate_'+n]);if(ctx.id)await api('/annual-rates/'+ctx.id,{method:'PATCH',data:{...payload,version:ctx.version}});else await api('/annual-rates',{method:'POST',data:payload});modal.close();toast('Pricing calendar updated for new estimates only.');return navigate('rates');}
  if(kind==='material'){const payload={...d,taxable:f.taxable.checked,active:f.active.checked,sell_price:Number(d.sell_price),cost_price:Number(d.cost_price||0),quantity:Number(d.quantity||0),minimum:Number(d.minimum||0)};if(ctx.id)await api('/materials/'+ctx.id,{method:'PATCH',data:{...payload,version:ctx.version}});else await api('/materials',{method:'POST',data:payload});modal.close();toast('Material catalogue updated. Existing job invoice snapshots were not changed.');return navigate('inventory');}
  if(kind==='document-template'){const payload={...d,active:f.active.checked,manager_only:f.manager_only.checked};if(ctx.id)await api('/document-templates/'+ctx.id,{method:'PATCH',data:{...payload,version:ctx.version}});else await api('/document-templates',{method:'POST',data:payload});modal.close();toast('Document template saved. Existing approvals keep their original wording.');return navigate('documents');}
  if(kind==='document-prepare'){const jobs=await api('/jobs'),j=jobs.find(x=>x.id===d.job_id);const out=await api('/document-approvals',{method:'POST',data:{job_id:d.job_id,lead_id:j?.lead_id,template_id:d.template_id,delivery_method:d.delivery_method,recipient:d.recipient,details:d.details}});return showModal('Approval ready',`${noLiveSend}<div class="callout teal"><strong>Private approval link created</strong>${h(out.expires_at)} expiry. The client must actively approve; nothing is assumed.</div><div class="spacer"></div>${textArea('approval_link','Private client link',out.url,3)}${textArea('approval_text','Text message draft',out.text_message,4)}${out.email_draft_created?'<p class="small muted">An email draft was also added to Email outbox. It was not sent.</p>':''}`,`${btn('close','Done')}${v5btn('copy-approval-link','Copy link','','btn-primary')}`,true);}
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(submit)submit.disabled=false;}
});

document.addEventListener('click',e=>{const el=e.target.closest('[data-v5="copy-approval-link"]');if(!el)return;const ta=document.querySelector('#f-approval_link');if(ta)copyText(ta.value);});

// Crew always lands on My Jobs; office users keep Command Center.
const reviewOriginalBootstrap=bootstrap;
bootstrap=async function(){const out=await reviewOriginalBootstrap();if(S.user?.role==='crew'&&S.page!=='schedule'){S.jobScope='mine';await navigate('schedule');}return out;};

// Bootstrap is called by ux.js after the UX overrides are registered.
