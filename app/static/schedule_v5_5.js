/* CrownOps v5.5 weekly dispatch calendar.
   General schedule stays week-first, compact, truck-colour coded, and supports many jobs per day. */
'use strict';
(function(){
  const previousFleet=X.pageRenderers.fleet;
  let availability={items:[],can_manage:false};

  const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null;};
  const compact=v=>{const n=num(v);if(n===null)return '—';return Number.isInteger(n)?String(n):String(Number(n.toFixed(2)));};
  const scheduledHours=j=>Math.max(0,(new Date(j.end_at)-new Date(j.start_at))/3600000);
  const serviceHigh=j=>num(j.quote?.service_high) ?? scheduledHours(j);
  const serviceLow=j=>num(j.quote?.service_low) ?? scheduledHours(j);
  const transportHours=j=>num(j.quote?.transport_hours) ?? 0;
  const estimateCode=j=>{
    const lo=serviceLow(j),hi=serviceHigh(j),tr=transportHours(j);
    const service=Math.abs(lo-hi)<0.001?compact(lo):`${compact(lo)}–${compact(hi)}`;
    return `${service} + ${compact(tr)}`;
  };
  const truckPrimary=j=>(j.trucks||[]).slice().sort((a,b)=>(Number(a.display_order||999)-Number(b.display_order||999))||String(a.name).localeCompare(String(b.name)))[0]||null;
  const safeColor=c=>/^#[0-9a-f]{6}$/i.test(String(c||''))?String(c).toUpperCase():'#64748B';
  const rgba=(hex,a)=>{const x=safeColor(hex).slice(1);const r=parseInt(x.slice(0,2),16),g=parseInt(x.slice(2,4),16),b=parseInt(x.slice(4,6),16);return `rgba(${r},${g},${b},${a})`;};
  const truckDots=j=>(j.trucks||[]).map(t=>`<span class="truck-dot" style="background:${safeColor(t.color_hex)}"></span>`).join('');
  const phaseName=p=>String(p||'Moving').replace(/demolition/ig,'Moving');

  function sortDayJobs(dayJobs){
    const groups=new Map();
    for(const j of dayJobs){
      const t=truckPrimary(j);const key=t?.id||'__unassigned__';
      if(!groups.has(key))groups.set(key,{truck:t,jobs:[]});groups.get(key).jobs.push(j);
    }
    const ordered=[...groups.values()].sort((a,b)=>{
      const aSize=Math.max(...a.jobs.map(serviceHigh),0),bSize=Math.max(...b.jobs.map(serviceHigh),0);
      if(bSize!==aSize)return bSize-aSize; // large primary services first
      const ao=Number(a.truck?.display_order||999),bo=Number(b.truck?.display_order||999);
      if(ao!==bo)return ao-bo;
      return String(a.truck?.name||'zzz').localeCompare(String(b.truck?.name||'zzz'));
    });
    return ordered.flatMap(g=>g.jobs.sort((a,b)=>new Date(a.start_at)-new Date(b.start_at)));
  }

  function weeklyJobCard(j,personal=false){
    const t=truckPrimary(j),color=safeColor(t?.color_hex),crew=(j.crew||[]).length,required=Number(j.quote?.movers||0);
    const crewLabel=required>crew?`${crew}/${required} movers assigned`:crew?`${crew} mover${crew===1?'':'s'}`:'Crew needed';
    const truckName=t?.name||'Truck needed';
    const statusClass=j.status==='Confirmed'?'confirmed':j.status==='In progress'?'progress':j.status==='Completed'?'completed':'tentative';
    return `<button class="weekly-dispatch-card ${statusClass}" data-final="open-job" data-id="${h(j.id)}" style="--truck:${color};--truck-soft:${rgba(color,.085)};--truck-border:${rgba(color,.32)}">
      <div class="weekly-card-top"><span>${h(timeLabel(j.start_at))}${personal?' – '+h(timeLabel(j.end_at)):''}</span><strong>${h(estimateCode(j))}</strong></div>
      <div class="weekly-client">${h(j.client?.client_name||j.title)}</div>
      <div class="weekly-service">${h(phaseName(j.phase))} · ${h(crewLabel)}</div>
      <div class="weekly-crew">${h((j.crew||[]).map(x=>x.name).join(' · ')||'Crew not assigned')}</div>
      <div class="weekly-truck">${truckDots(j)}<span>${h((j.trucks||[]).map(x=>x.name).join(' + ')||truckName)}</span></div>
      ${j.assigned_to_me?'<span class="pill blue weekly-personal">You are assigned</span>':''}
      ${j.readiness?.missing?.length?`<span class="pill orange" ${canPlanJobs()?`data-plan="review" data-id="${h(j.id)}"`:""}>Needs attention · ${j.readiness.missing.length}</span>`:""}
    </button>`;
  }

  function legend(vehicles){
    const trucks=(vehicles||[]).filter(v=>v.active).slice().sort((a,b)=>(Number(a.display_order||999)-Number(b.display_order||999))||a.name.localeCompare(b.name));
    return `<div class="truck-legend-bar"><div class="truck-legend-title"><strong>Truck colours</strong><span>Every service keeps the colour of its assigned truck.</span></div><div class="truck-legend-items">${trucks.map(t=>`<span class="truck-legend-chip"><i style="background:${safeColor(t.color_hex)}"></i>${h(t.name)}</span>`).join('')||'<span class="muted small">Add your trucks to start colour-coding the schedule.</span>'}</div>${may(['owner','sales','dispatch'])?'<button data-v55="manage-trucks" class="btn-small">Manage trucks</button>':''}</div>`;
  }

  function compactWeekLabel(start,end){
    const a=new Date(start+'T12:00:00'),b=new Date(end+'T12:00:00');
    if(Number.isNaN(a.getTime())||Number.isNaN(b.getTime()))return dateLabel(start)+' – '+dateLabel(end);
    const sameYear=a.getFullYear()===b.getFullYear(),sameMonth=sameYear&&a.getMonth()===b.getMonth();
    const left=a.toLocaleDateString('en-CA',{month:'short',day:'numeric'});
    const right=b.toLocaleDateString('en-CA',{month:sameMonth?undefined:'short',day:'numeric',year:sameYear?undefined:'numeric'});
    if(sameYear)return left+' – '+right+', '+b.getFullYear();
    return a.toLocaleDateString('en-CA',{month:'short',day:'numeric',year:'numeric'})+' – '+b.toLocaleDateString('en-CA',{month:'short',day:'numeric',year:'numeric'});
  }

  function weekCalendar(jobs,off,vehicles){
    const days=weekDays();
    const columns=days.map(d=>{
      const day=dateIso(d),dayJobs=sortDayJobs(jobs.filter(j=>localDay(j.start_at)===day));
      const approved=(off||[]).filter(r=>r.status==='Approved'&&r.start_date<=day&&r.end_date>=day);
      const fillers=Math.max(0,14-dayJobs.length);
      return `<section class="weekly-day-column ${availability.items.some(x=>x.day===day&&x.status==='Fully booked')?'day-closed':''}">
        <header class="weekly-day-head"><div><small>${h(d.toLocaleDateString('en-CA',{weekday:'short'}))}</small><strong>${d.getDate()}</strong></div><span>${dayJobs.length} job${dayJobs.length===1?'':'s'}</span></header>
        ${availability.items.some(x=>x.day===day&&x.status==='Fully booked')?'<div class="day-closed-label">Fully booked · no new bookings</div>':''}
        ${approved.length?`<div class="weekly-off-row">${approved.map(r=>`<span>${h(r.employee_name)} OFF</span>`).join('')}</div>`:''}
        <div class="weekly-day-jobs">${dayJobs.map(j=>weeklyJobCard(j)).join('')}${Array.from({length:fillers},()=>'<div class="weekly-open-slot" aria-hidden="true"></div>').join('')}</div>
      </section>`;
    }).join('');
    return `<div class="weekly-dispatch-scroll"><div class="weekly-dispatch-grid">${columns}</div></div>`;
  }

  function personalAgenda(jobs,off){
    return '<div class="personal-agenda">'+weekDays().map(d=>{
      const day=dateIso(d),dayJobs=jobs.filter(j=>localDay(j.start_at)===day).sort((a,b)=>new Date(a.start_at)-new Date(b.start_at));
      const approved=(off||[]).filter(r=>r.status==='Approved'&&r.start_date<=day&&r.end_date>=day);
      if(!dayJobs.length&&!approved.length)return '';
      return `<section class="personal-agenda-day" data-date="${h(day)}"><h2>${h(d.toLocaleDateString('en-CA',{weekday:'long',month:'short',day:'numeric'}))}</h2>${approved.length?'<p class="pill orange">Approved time off</p>':''}<div class="personal-agenda-jobs">${dayJobs.map(j=>weeklyJobCard(j,true)).join('')}</div></section>`;
    }).join('')+'</div>';
  }

  schedulePage=async function(){
    if(S.scheduleUser!==S.user.id){S.scheduleUser=S.user.id;S.jobScope=S.user.role==='crew'?'mine':'all';}
    if(!['mine','all'].includes(S.jobScope))S.jobScope=S.user.role==='crew'?'mine':'all';
    const days=weekDays(),start=dateIso(days[0]),end=dateIso(days[6]);
    const [jobs,allOff,vehicles,identity,daysAvailable]=await Promise.all([
      api(`/ux/final/schedule?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&scope=${S.jobScope}`),
      api('/time-off').catch(()=>[]),
      api('/vehicles').catch(()=>[]),
      api('/schedule-identity'),api('/schedule/availability?start='+start+'&end='+end)
    ]);
    availability=daysAvailable;
    const closed=availability.items.filter(x=>x.status==='Fully booked');
    const availabilityBar='<div class="schedule-availability"><div><strong>Date availability</strong><p class="small muted">'+(closed.length?closed.map(x=>h(uxDateLabel(x.day))+': Fully booked').join(' · '):'Dates this week are open for scheduling.')+'</p></div>'+(availability.can_manage?'<button data-availability="open" class="btn-soft">Set fully booked / reopen</button>':'')+'</div>';
    const off=S.jobScope==='mine'?allOff.filter(r=>identity.id&&r.employee_id===identity.id):allOff;
    const scope=`<div class="segmented job-scope" aria-label="Schedule scope"><button type="button" data-v5="jobs-mine" class="${S.jobScope==='mine'?'selected':''}" aria-pressed="${S.jobScope==='mine'}">My Jobs</button><button type="button" data-v5="jobs-all" class="${S.jobScope==='all'?'selected':''}" aria-pressed="${S.jobScope==='all'}">All Jobs</button></div>`;
    const missingIdentity=S.jobScope==='mine'&&!identity.matched?`<div class="callout orange schedule-identity-notice"><strong>Your login is not matched to an employee yet.</strong><p class="small">An owner can add your sign-in email (${h(S.user.email)}) to the correct employee profile. My Jobs will then show that employee’s assignments. Use All Jobs in the meantime.</p><button type="button" data-page="team">Open employee directory</button></div>`:'';
    const noJobs=!jobs.length?`<div class="callout schedule-empty"><strong>${S.jobScope==='mine'?'No jobs assigned to you this week.':'No jobs scheduled this week.'}</strong><span class="small">${S.jobScope==='mine'?'Switch to All Jobs or choose another week.':'Choose another week to see more services.'}</span></div>`:'';
    const navTools=`<div class="calendar-tools weekly-tools"><div class="weekly-nav-main"><div class="weekly-nav-buttons">${btn('week-prev',icon('left'),'btn-icon')}${btn('week-today','Today')}${btn('week-next',icon('right'),'btn-icon')}</div><div class="weekly-range-label">${h(compactWeekLabel(start,end))}</div></div><div class="weekly-nav-side"><label class="weekly-jump"><span>Jump to</span><input type="date" id="calendar-jump" value="${h(start)}" aria-label="Jump to schedule date"></label><div class="weekly-capacity">${vehicles.filter(v=>v.active).length} active trucks</div></div></div>`;
    return head('Schedule',S.jobScope==='mine'?'Your assigned services, crew and trucks.':'Company services. Select a move to see its details, team and truck.',canPlanJobs()?btn('new-job',icon('plus')+'New job','btn-primary'):'')+
      scope+missingIdentity+navTools+availabilityBar+(S.jobScope==='mine'?noJobs+personalAgenda(jobs,off):legend(vehicles)+noJobs+weekCalendar(jobs,off,vehicles))+
      `<p class="source-warning">Example: 5–7 + 1 means 5–7 estimated service hours plus 1 transportation hour. CrownOps allows the same crew/truck on another service later that day when the time blocks do not overlap.</p>`;
  };

  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-availability]');if(!b)return;e.preventDefault();
    if(b.dataset.availability!=='open')return;
    showModal('Date availability','<p>Closing a date prevents new bookings and rescheduling onto that day. Existing confirmed jobs stay on the schedule. Client inquiries can still be received.</p><form id="date-availability-form"><div class="grid2">'+field('day','Service date',document.getElementById('calendar-jump')?.value||dateIso(new Date()),'date','required')+select('status','Availability',[['Fully booked','Fully booked — block new bookings'],['Open','Open — allow scheduling']],'Fully booked')+textArea('reason','Reason for the team (optional)','',3)+'</div></form>',btn('close','Cancel')+'<button form="date-availability-form" type="submit" class="btn-primary">Save availability</button>');
  });
  document.addEventListener('submit',async e=>{
    const f=e.target.closest('#date-availability-form');if(!f)return;e.preventDefault();
    const b=document.querySelector('[form="date-availability-form"]');if(b.disabled)return;b.disabled=true;
    try{
      const d=Object.fromEntries(new FormData(f)),result=await api('/schedule/availability?start='+d.day+'&end='+d.day),old=result.items.find(x=>x.day===d.day);
      // Compare with the state displayed when opening the control where possible.
      const seen=availability.items.find(x=>x.day===d.day);
      if(d.day>=availability.start&&d.day<=availability.end&&(old?.version||0)!==(seen?.version||0))throw Error('Availability changed since this schedule opened. Reload before saving.');
      await api('/schedule/availability/'+d.day,{method:'PUT',data:{status:d.status,version:old?.version||0,reason:d.reason}});
      modal.close();toast(d.status==='Fully booked'?'Date marked fully booked.':'Date reopened.');await navigate('schedule');
    }catch(ex){toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
  });

  // Fleet page: make truck name + schedule colour easy to manage.
  X.pageRenderers.fleet=async()=>{
    const fleet=await api('/fleet');X.cache.fleet=fleet;
    return head('Trucks & fleet','Add trucks as Crown grows, give each one a name and schedule colour, then manage checks and maintenance.',may(['owner','sales','dispatch'])?btn('new-vehicle','Add truck','btn-primary'):'')+
      `<div class="truck-management-grid">${fleet.map(v=>`<section class="truck-management-card" style="--truck:${safeColor(v.color_hex)};--truck-soft:${rgba(v.color_hex,.08)}"><div class="truck-card-title"><span class="truck-big-dot" style="background:${safeColor(v.color_hex)}"></span><div><h2>${h(v.name)}</h2><div class="tiny muted">Schedule colour ${h(safeColor(v.color_hex))}</div></div>${pill(v.active?'Available':'Unavailable')}</div><div class="info-grid"><div><span class="info-label">Plate</span><div>${h(v.profile.plate||'Not entered')}</div></div><div><span class="info-label">Odometer</span><div>${h(v.profile.odometer_km||0)} km</div></div><div><span class="info-label">Next maintenance</span><div>${h(v.profile.next_service_date||'Not scheduled')}</div></div><div><span class="info-label">Inspection due</span><div>${h(v.profile.inspection_due||'Not configured')}</div></div></div><div class="spacer"></div><div class="row wrap">${may(['owner','sales','dispatch'])?`<button data-v55="truck-style" data-id="${h(v.id)}">Name & colour</button>`:''}${xbtn('truck-check','Complete check',v.id,'btn-primary')}${may(['owner','sales','dispatch'])?xbtn('fleet-profile','Details',v.id)+xbtn('maintenance','Maintenance',v.id):''}</div>${v.warnings?.length?`<div class="spacer"></div><div class="row wrap">${v.warnings.map(w=>pill(w)).join('')}</div>`:''}</section>`).join('')}</div>`;
  };

  document.addEventListener('click',async e=>{
    const el=e.target.closest('[data-v55]');if(!el)return;e.preventDefault();
    try{
      if(el.dataset.v55==='manage-trucks')return navigate('fleet');
      if(el.dataset.v55==='truck-style'){
        const v=(X.cache.fleet||await api('/fleet')).find(x=>x.id===el.dataset.id);if(!v)throw Error('Truck not found.');
        X.formContext={id:v.id};
        return showModal('Truck name & schedule colour',`<form id="truck-style-form" data-v55-form="truck-style"><div class="grid2">${field('name','Truck name',v.name,'text','required')}${field('color_hex','Schedule colour',safeColor(v.color_hex),'color','required')}</div><p class="full small muted">Every job assigned to this truck uses this colour on the weekly schedule.</p></form>`,`${btn('close','Cancel')}<button class="btn-primary" type="submit" form="truck-style-form">Save truck</button>`,true);
      }
    }catch(ex){modal.open?modalError(ex):toast(ex.message);}
  });

  document.addEventListener('submit',async e=>{
    const f=e.target.closest('form[data-v55-form]');if(!f)return;e.preventDefault();
    try{
      const d=readForm(f),ctx=X.formContext||{};
      if(f.dataset.v55Form==='truck-style'){
        await api('/vehicles/'+encodeURIComponent(ctx.id),{method:'PUT',data:d});modal.close();toast('Truck name and schedule colour updated.');return navigate('fleet');
      }
    }catch(ex){modalError(ex);}
  });

  const oldShell=shell;
  shell=function(){oldShell();const strip=document.getElementById('pilot-strip');if(strip){strip.hidden=S.mode==='production'&&!demo();strip.innerHTML=`${icon('shield')}<span>${demo()?'Interactive preview — fictional records. Nothing is sent.':'Test workspace — use fictional records and reviewed test recipients.'}</span>`;}};
})();
