/* Today shares Inbox's received-time activity and independently assigned tasks. */
'use strict';
const TODAY={order:'newest',busy:false};
const todayPreviousDashboard=dashboard;
const todayClientsNav=nav.find(n=>n[0]==='sales');if(todayClientsNav)todayClientsNav[2]='Clients';
function todayActivityCard(t){
 return `<article class="today-activity inbox-accent-${inboxType(t)[1]}" data-activity-id="${h(t.id)}"><div class="grow"><div class="row wrap inbox-badges">${inboxTaskBadges(t)}</div><h3>${h(t.client_name||(t.channel==='forms'?'Unmatched form':'Unmatched activity'))}</h3><p>${h(t.title)}${t.matching_required?' · Client match needs review':''}</p><p class="small muted">Received ${h(uxTimestampLabel(t.received_at||t.created_at))}${t.move_date?' · Move / service: '+h(uxDateLabel(t.move_date)):''}</p><p class="small muted">${h(t.owner_name||'Unclaimed')}</p></div><div class="row wrap">${!t.owner_id&&t.status!=='Done'?`<button data-inbox-task="${h(t.id)}" data-task-action="claim">Claim</button>`:''}<button data-inbox-task="${h(t.id)}" data-task-action="open">Open</button></div></article>`;
}
function todayOpenInbox(scope='team',status='all'){
 IB.tab='tasks';IB.view='followups';IB.scope=scope;IB.status=status;IB.order=TODAY.order;IB.offset=0;IB.q='';IB.lid=null;
 return navigate('communications');
}
dashboard=async function(){
 if(!['owner','manager','sales','dispatch'].includes(S.user.role))return todayPreviousDashboard();
 // Latest arrivals are always Team, so Mine cannot hide a new unclaimed inquiry.
 if(!UX.todayView)UX.todayView='mine';
 const scope=UX.todayView,order=TODAY.order;
 const [d,activity,tasks]=await Promise.all([
  api('/ux/today?view='+scope),
  api('/inbox-tasks?channel=tasks&activity_only=true&status=all&scope=team&order='+order),
  api('/inbox-tasks?channel=tasks&status=open&scope='+scope+'&order='+order)
 ]);
 const date=new Intl.DateTimeFormat('en-CA',{timeZone:'UTC',weekday:'long',month:'long',day:'numeric',year:'numeric'}).format(new Date(d.date+'T12:00:00Z'));
 const jobs=d.jobs.map(j=>`<button class="today-job" data-ux="open-job" data-id="${h(j.id)}"><div><h3>${h(j.title)}</h3><p>${h(j.phase)} · ${timeLabel(j.start_at)} – ${timeLabel(j.end_at)}</p><p class="small muted">${h(j.crew_names.join(', ')||'Crew to assign')} · ${h((j.truck_names||[]).join(', ')||'Truck to assign')}</p></div>${pill(j.status)}</button>`).join('')||empty('No jobs scheduled today','Open Schedule to review upcoming services.');
 const filters=`<div class="ux-browser-toolbar"><div class="ux-filter-chips">${[['mine','Mine'],['unclaimed','Unclaimed'],['team','Team']].map(([key,label])=>uxStatusButton('today-view',key,label,tasks.scope_counts?.[key]||0,scope)).join('')}</div><p class="small muted">All unfinished tasks, including older and upcoming follow-ups.</p></div>`;
 const operational=d.attention.filter(a=>!['task','conversation','email'].includes(a.type));
 const operations=operational.length?`<div class="today-office-tasks"><h3>Service & office tasks</h3>${operational.map(a=>`<div class="attention-row"><div class="grow"><strong>${h(a.title)}</strong><p class="small muted">${h(a.detail||'')}</p></div>${uxAttentionButton(a)}</div>`).join('')}</div>`:'';
 return `<div class="today-workspace">`+head(date,"Today · Montréal time",'<button data-today-refresh class="btn-soft">'+icon('refresh')+'Refresh</button><button data-page="communications" class="btn-primary">Open Inbox</button>')+
 `<section class="panel today-jobs" data-today-section="jobs"><div class="panel-head"><h2>Jobs scheduled today · ${d.jobs.length}</h2><button data-page="schedule">Open Schedule</button></div><div class="panel-body">${jobs}</div></section>`+
 `<section class="panel" data-today-section="activity"><div class="panel-head"><div><h2>Latest calls, forms & emails</h2><p class="small muted">All team activity · received time · ${order==='newest'?'newest':'oldest'} first</p></div><label class="today-order">Sort by<select id="today-order"><option value="newest" ${order==='newest'?'selected':''}>Newest first</option><option value="oldest" ${order==='oldest'?'selected':''}>Oldest first</option></select></label></div><div class="today-activity-list">${activity.items.slice(0,8).map(todayActivityCard).join('')||empty('No calls, forms or emails yet','Incoming items will appear here after they reach Inbox.')}</div><div class="panel-body"><button data-today-inbox="team" data-status="all">View all activity in Inbox · ${activity.total}</button><p class="tiny muted">Refreshes every 30 seconds while this page is visible. Website and email imports may take longer to arrive.</p></div></section>`+
 `<section class="panel" data-today-section="tasks"><div class="panel-head"><h2>Unfinished tasks</h2><button data-today-inbox="${h(scope)}" data-status="open">View all · ${tasks.total}</button></div><div class="panel-body">${filters}<div class="today-task-list">${tasks.items.slice(0,8).map(inboxTaskCard).join('')||empty('No unfinished tasks in this view','Use Unclaimed or Team to review the wider queue.')}</div>${operations}</div></section></div>`;
};
async function todayRefreshPanels(){
 if(TODAY.busy||S.page!=='dashboard'||document.hidden||modal.open||!document.querySelector('.today-workspace'))return;
 TODAY.busy=true;const scope=UX.todayView,order=TODAY.order;
 try{
  const html=await dashboard();
  if(S.page!=='dashboard'||modal.open||scope!==UX.todayView||order!==TODAY.order)return;
  const template=document.createElement('template');template.innerHTML=html;
  for(const key of ['jobs','activity','tasks']){
   const old=document.querySelector(`[data-today-section="${key}"]`),fresh=template.content.querySelector(`[data-today-section="${key}"]`);
   if(old&&fresh&&!old.contains(document.activeElement))old.replaceWith(fresh);
  }
  const heading=document.querySelector('.today-workspace .page-head h1'),freshHeading=template.content.querySelector('.page-head h1');
  if(heading&&freshHeading)heading.textContent=freshHeading.textContent;
  document.querySelector('.today-refresh-error')?.remove();
 }catch(ex){
  const page=document.querySelector('.today-workspace');
  if(S.page==='dashboard'&&page&&!page.querySelector('.today-refresh-error'))page.insertAdjacentHTML('afterbegin','<p class="callout today-refresh-error" role="status">Could not refresh Today. The displayed information may be older. Use Refresh to try again.</p>');
 }finally{TODAY.busy=false;}
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-today-refresh],[data-today-inbox]');if(!b||b.disabled)return;
 e.preventDefault();b.disabled=true;
 try{if(b.hasAttribute('data-today-inbox'))await todayOpenInbox(b.dataset.todayInbox,b.dataset.status);else await navigate('dashboard');}
 catch(ex){toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('change',e=>{if(e.target.id==='today-order'){TODAY.order=e.target.value;navigate('dashboard');}});
setInterval(todayRefreshPanels,30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)todayRefreshPanels();});
