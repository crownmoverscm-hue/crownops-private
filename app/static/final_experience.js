/* CrownOps v5.4 final review experience.
   Priority: fastest path to the information/actions Crown uses every day. */
'use strict';
(function(){
  const legacySchedulePage = schedulePage;
  const legacyOpenLead = openLead;
  const legacyOpenJob = openJob;
  S.scheduleMode = S.scheduleMode || 'teams';

  const hrs=v=>{const n=Number(v);if(!Number.isFinite(n))return '—';return Number.isInteger(n)?String(n):String(n.toFixed(2)).replace(/0+$/,'').replace(/\.$/,'');};
  const estimate=q=>q&&q.service_low!=null&&q.service_high!=null?`${hrs(q.service_low)}–${hrs(q.service_high)} hrs`:'Estimate needed';
  const transport=q=>!q?'Not quoted':q.fixed_transport!==null&&q.fixed_transport!==undefined?`Local fee · ${money(q.transport_cents||0)}`:!Number(q.transport_hours||0)&&Number(q.transport_cents||0)>0?`Transportation fee · ${money(q.transport_cents)}`:`${hrs(q.transport_hours||0)} hr${Number(q.transport_hours)===1?'':'s'} · ${money(q.transport_cents||0)}`;
  const serviceType=p=>String(p||'Moving').replace(/demolition/ig,'Moving');
  const phoneHref=v=>`tel:${String(v||'').replace(/[^+\d]/g,'')}`;
  const textHref=v=>`sms:${String(v||'').replace(/[^+\d]/g,'')}`;
  const mapsHref=v=>`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(v||'')}`;
  const paymentPill=p=>{
    const s=p?.payment_status||'Not invoiced';
    if(s==='PAID')return '<span class="pill teal">PAID</span>';
    if(s==='BALANCE DUE')return '<span class="pill red">BALANCE DUE</span>';
    if(s==='PAYMENT TO VERIFY')return '<span class="pill orange">PAYMENT TO VERIFY</span>';
    return `<span class="pill gray">${h(s)}</span>`;
  };
  const depositPill=p=>{
    const s=p?.deposit_status||'Missing';
    const cl=/verified/i.test(s)?'teal':/recorded/i.test(s)?'orange':/not required/i.test(s)?'gray':'red';
    return `<span class="pill ${cl}">Deposit: ${h(s)}</span>`;
  };
  const readyPill=(r,id,insideCard=false)=>{
    if(r?.ready)return '<span class="pill teal">READY</span>';
    const tag=canPlanJobs()&&!insideCard?'button':'span';
    return `<${tag} class="pill orange" ${canPlanJobs()?`data-plan="review" data-id="${h(id)}"`:''}>NEEDS ATTENTION · ${r?.missing?.length||0}</${tag}>`;
  };
  const info=(label,value)=>`<div class="job-kv"><div class="info-label">${h(label)}</div><div class="job-value">${value}</div></div>`;

  // --------- Schedule: team-first dispatch board ---------
  function teamKey(j){
    const crew=(j.crew||[]).map(x=>x.id).sort().join('|');
    const trucks=(j.trucks||[]).map(x=>x.id).sort().join('|');
    return `${crew}::${trucks}`;
  }
  function scheduleCard(j){
    const q=j.quote;
    const crewCount=(j.crew||[]).length;
    const truck=(j.trucks||[]).map(x=>x.name).join(', ')||'Truck not assigned';
    return `<button class="dispatch-job-card" data-final="open-job" data-id="${h(j.id)}">
      <div class="dispatch-job-top"><strong>${h(dateLabel(localDay(j.start_at)))} · ${h(timeLabel(j.start_at))} · ${h(j.client?.client_name||j.title)}</strong>${pill(j.status)}</div>
      <div class="dispatch-route">${h(j.client?.pickup||'Pickup needed')} <span>→</span> ${h(j.client?.dropoff||'Destination needed')}</div>
      <div class="dispatch-job-stats">
        <span><b>${crewCount}</b> Mover${crewCount===1?'':'s'}</span>
        <span><b>${h(estimate(q))}</b></span>
        <span><b>${h(transport(q))}</b></span>
        <span><b>${h(truck)}</b></span>
        <span><b>${j.photo_count||0}</b> photo${j.photo_count===1?'':'s'}</span>
      </div>
      <div class="dispatch-job-foot">${readyPill(j.readiness,j.id,true)} ${q?`<span class="pill ${q.status==='Accepted'?'teal':'purple'}">Quote ${h(q.status)}</span>`:'<span class="pill red">Quote needed</span>'} ${depositPill(j.payments)} ${j.status==='Completed'?paymentPill(j.payments):''}</div>
    </button>`;
  }
  function teamBoard(jobs,off){
    const groups=new Map();
    for(const j of jobs){
      const k=teamKey(j);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(j);
    }
    const sorted=[...groups.values()].sort((a,b)=>new Date(a[0].start_at)-new Date(b[0].start_at));
    if(!sorted.length)return empty('No jobs this week','Create a job or move to another week.');
    return `<div class="dispatch-board">${sorted.map((list,i)=>{
      list.sort((a,b)=>new Date(a.start_at)-new Date(b.start_at));
      const first=list[0],crew=(first.crew||[]).map(x=>x.name).join(' + ')||'Crew not assigned',truck=(first.trucks||[]).map(x=>x.name).join(', ')||'Truck not assigned';
      const days=[...new Set(list.map(x=>localDay(x.start_at)))];
      return `<section class="dispatch-team"><div class="dispatch-team-head"><div><div class="eyebrow">TEAM ${i+1}</div><h2>${h(truck)}</h2><div class="small muted">${h(crew)}</div></div><div class="row wrap">${days.map(d=>`<span class="pill blue">${h(dateLabel(d))}</span>`).join('')}</div></div><div class="dispatch-team-jobs">${list.map(scheduleCard).join('')}</div></section>`;
    }).join('')}</div>${off?.length?`<div class="spacer"></div>${panel('Approved OFF this week',off.map(r=>`<span class="pill red" style="margin:4px">${h(r.employee_name)} · ${h(r.start_date===r.end_date?r.start_date:r.start_date+'–'+r.end_date)}</span>`).join(''))}`:''}`;
  }
  function weekBoard(jobs,off){
    const days=weekDays(), ids=days.map(dateIso);
    return `<div class="panel calendar-scroll"><div class="week-grid">${days.map(d=>{
      const day=dateIso(d),dayJobs=jobs.filter(j=>localDay(j.start_at)===day),dayOff=(off||[]).filter(r=>r.status==='Approved'&&r.start_date<=day&&r.end_date>=day);
      return `<div class="day-col"><div class="day-head"><small>${d.toLocaleDateString('en-CA',{weekday:'short'})}</small><b>${d.getDate()}</b></div><div class="day-jobs">${dayOff.map(r=>`<div class="off-card"><strong>${h(r.employee_name)}</strong><small>OFF</small></div>`).join('')}${dayJobs.map(j=>`<button class="job-card" data-final="open-job" data-id="${h(j.id)}"><div class="status">${h(timeLabel(j.start_at))} · ${h(j.status)}</div><strong>${h(j.client?.client_name||j.title)}</strong><small><b>${j.crew.length} Movers</b> · ${h(j.trucks.map(x=>x.name).join(', ')||'Truck needed')}</small><span class="tiny strong">${h(estimate(j.quote))}</span></button>`).join('')}</div></div>`;
    }).join('')}</div></div>`;
  }
  schedulePage=async function(){
    if(S.user.role==='crew')return legacySchedulePage();
    const days=weekDays(),start=dateIso(days[0]),end=dateIso(days[6]);
    const [jobs,off]=await Promise.all([api(`/ux/final/schedule?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),api('/time-off').catch(()=>[])]);
    const tabs=`<div class="segmented final-schedule-tabs"><button data-final="schedule-teams" class="${S.scheduleMode==='teams'?'selected':''}">Team view</button><button data-final="schedule-week" class="${S.scheduleMode==='week'?'selected':''}">Week calendar</button></div>`;
    const navTools=`<div class="calendar-tools"><div class="row">${btn('week-prev',icon('left'),'btn-icon')}${btn('week-today','Today')}${btn('week-next',icon('right'),'btn-icon')}<span class="date-range">${dateLabel(start)} – ${dateLabel(end)}</span><input type="date" id="calendar-jump" value="${h(start)}" aria-label="Jump to schedule date"></div>${tabs}</div>`;
    return head('Schedule','Dispatch by team first. Multiple jobs can be assigned to the same crew and truck when the time blocks do not overlap.',btn('new-job',icon('plus')+'New job','btn-primary'))+navTools+(S.scheduleMode==='teams'?teamBoard(jobs,off.filter(r=>r.status==='Approved'&&r.start_date<=end&&r.end_date>=start)):weekBoard(jobs,off))+`<p class="source-warning">CrownOps blocks actual crew/truck overlaps and approved OFF dates. A team can work multiple services on the same day when the scheduled times fit.</p>`;
  };

  // --------- Client page: client first, services second ---------
  openLead=async function(id){
    const d=await api('/ux/final/client/'+encodeURIComponent(id));
    const c=d.client;
    const services=d.services.length?d.services.map(s=>{
      const q=s.quote;
      return `<button class="client-service-card" data-final="open-job" data-id="${h(s.id)}"><div class="client-service-main"><div class="eyebrow">${h(serviceType(s.phase))}</div><h3>${h(dateLabel(localDay(s.start_at)))} · ${h(timeLabel(s.start_at))}</h3><div class="small muted">${h((s.crew_names||[]).join(' + ')||'Crew not assigned')} · ${h((s.truck_names||[]).join(', ')||'Truck not assigned')}</div></div><div class="client-service-stats"><strong>${h(q?`${q.movers||s.crew_names.length} Movers`:`${s.crew_names.length} Movers`)}</strong><span>${h(estimate(q))}</span><span>${h(transport(q))}</span><span>${s.photo_count||0} photos</span></div><div class="client-service-state">${pill(s.status)} ${readyPill(s.readiness,s.id,true)} ${q?pill('Quote '+q.status):'<span class="pill red">Quote needed</span>'}</div></button>`;
    }).join(''):empty('No services yet','Create a quote and service when the client is ready.');
    const buttons=`<div class="client-contact-actions"><a class="button-link" href="${phoneHref(c.phone)}">Call</a><a class="button-link" href="${textHref(c.phone)}">Text</a>${may(['owner','sales'])?`<button data-final="email-client" data-id="${h(id)}">Email</button>`:''}${may(['owner','sales'])?`<button data-final="legacy-client" data-id="${h(id)}">Edit client / sales details</button>`:''}</div>`;
    showModal(h(c.client_name),`<section class="job-section job-client-section"><div class="job-section-head"><div><div class="eyebrow">CLIENT INFORMATION</div><h2>${h(c.client_name)}</h2></div></div><div class="job-info-grid">${info('Phone',`<a href="${phoneHref(c.phone)}">${h(c.phone||'Not supplied')}</a>`)}${info('Email',h(c.email||'Not supplied'))}${info('Preferred language',h(c.language||'English'))}${info('Client status',pill(c.stage||'Client'))}</div>${buttons}</section><section class="job-section"><div class="job-section-head"><div><div class="eyebrow">SERVICES</div><h3>Moving / service history</h3></div></div><div class="client-services">${services}</div></section>`,btn('close','Close'),true);
  };

  // --------- Job page: the one screen field/office should understand immediately ---------
  openJob=async function(jid){
    const d=await api('/ux/final/job/'+encodeURIComponent(jid));
    const j=d.job,c=d.client,q=d.quote,p=d.payments;
    const route=d.route_stops||[],pickup=route[0]?.address||c.pickup,destination=route.at(-1)?.address||c.dropoff;
    const jobs=await api('/jobs'); const raw=jobs.find(x=>x.id===jid);
    if(!raw)throw Error('Job not found.');
    S.modalContext={job:raw};
    const assigned=canPlanJobs()||S.user.role!=='crew'||raw.crew_ids.includes(S.user.employee_id), readOnly=S.user.role==='crew'&&!assigned;
    const qAccepted=q?.status==='Accepted';
    const clientActions=`<div class="client-contact-actions"><a class="button-link" href="${phoneHref(c.phone)}">Call</a><a class="button-link" href="${textHref(c.phone)}">Text</a>${S.user.role!=='crew'&&may(['owner','sales'])?`<button data-final="email-client" data-id="${h(j.lead_id)}">Email</button>`:''}${pickup?`<button type="button" data-map-address="${h(pickup)}">Pickup map</button>`:''}${destination?`<button type="button" data-map-address="${h(destination)}">Destination map</button>`:''}</div>`;
    const client=`<section class="job-section job-client-section"><div class="job-section-head"><div><div class="eyebrow">CLIENT INFORMATION</div><h2>${h(c.client_name)}</h2></div>${S.user.role!=='crew'&&may(['owner','sales','dispatch'])?`<button data-final="open-client" data-id="${h(j.lead_id)}">Client page</button>`:''}</div><div class="job-info-grid">${info('Phone',`<a href="${phoneHref(c.phone)}">${h(c.phone||'Not supplied')}</a>`)}${info('Email',h(c.email||'Not supplied'))}</div>${clientActions}</section>`;
    const missing=d.readiness?.missing||[];
    const statusLine=`<div class="final-status-line">${readyPill(d.readiness,jid)} ${pill(j.status)} ${q?`<span class="pill ${qAccepted?'teal':'purple'}">Quote ${h(q.status)} · v${h(q.revision)}</span>`:'<span class="pill red">Quote needed</span>'} ${depositPill(p)} ${j.status==='Completed'||d.invoice?paymentPill(p):''}</div>`;
    const moving=`<section class="job-section"><div class="job-section-head"><div><div class="eyebrow">${h(serviceType(j.phase).toUpperCase())} INFORMATION</div><h3>${h(dateLabel(localDay(j.start_at)))} · ${h(timeLabel(j.start_at))}${j.end_at?`–${h(timeLabel(j.end_at))}`:''}</h3></div>${statusLine}</div><div class="job-highlight-strip final-five"><div><span>Team size</span><strong>${d.crew.length} Mover${d.crew.length===1?'':'s'}</strong></div><div><span>Estimated service</span><strong>${h(estimate(q))}</strong></div><div><span>Transportation</span><strong>${h(transport(q))}</strong></div><div><span>Truck</span><strong>${h(d.trucks.map(x=>x.name).join(', ')||'Not assigned')}</strong></div><div><span>Photos</span><strong>${d.photo_count} photo${d.photo_count===1?'':'s'}</strong></div></div><div class="job-info-grid job-grid-3">${info('Assigned team',h(d.crew.map(x=>x.name).join(', ')||'Not assigned'))}${info('Home size',h(c.home_size||'Not supplied'))}${info('Boxes',h(c.boxes||'Not supplied'))}${info('Heavy / specialty',h([c.heavy_items,...(inquiryMeta(c).heavy_items||[])].filter(Boolean).join(' · ')||'None recorded'))}${info('Minimum service',q?`${h(hrs(q.minimum_hours))} hrs`:'Not quoted')}${info('Hourly rate',q?money(q.hourly_rate_cents):'Not quoted')}</div>${renderMoveRoute(route.length?route:[{kind:'Pickup',address:c.pickup,access:[c.pickup_access,c.pickup_walk].filter(Boolean).join(' · ')},{kind:'Delivery',address:c.dropoff,access:[c.dropoff_access,c.dropoff_walk].filter(Boolean).join(' · ')}])}<div class="job-notes"><div class="info-label">INVENTORY / IMPORTANT NOTES</div><div>${h([c.inventory,c.notes,j.notes].filter(Boolean).join('\n\n')||'No inventory or notes recorded.')}</div></div>${missing.length?`<div class="spacer"></div><div class="callout orange"><strong>Needs attention</strong>${missing.map(x=>canPlanJobs()?`<button type="button" class="attention-chip" data-plan="review" data-id="${h(jid)}" data-target="${h(jpTarget(x))}">${h(x)}</button>`:`<span class="attention-chip">${h(x)}</span>`).join('')}</div>`:''}</section>`;
    let actions=`<div class="job-actions final-actions"><button class="job-action-card" data-jx="photos" data-id="${h(jid)}" ${readOnly?'disabled title="Photos are available to the assigned crew"':''}><span class="job-action-icon">▧</span><span><strong>Photos</strong><small>View ${d.photo_count} photo${d.photo_count===1?'':'s'} or upload more</small></span></button>${q?`<button class="job-action-card" data-jx="quote" data-id="${h(jid)}"><span class="job-action-icon">$</span><span><strong>View Quote Details</strong><small>${h(estimate(q))} · ${h(transport(q))}</small></span></button>`:`<button class="job-action-card muted-action" disabled><span class="job-action-icon">$</span><span><strong>Quote needed</strong><small>Create / link the estimate first</small></span></button>`}${qAccepted&&!readOnly?`<button class="job-action-card" data-jx="invoice" data-id="${h(jid)}"><span class="job-action-icon">▤</span><span><strong>${d.invoice?'Open invoice':'Prepare invoice'}</strong><small>Uses accepted Quote v${h(q.revision)}</small></span></button>`:`<button class="job-action-card muted-action" disabled><span class="job-action-icon">▤</span><span><strong>Invoice</strong><small>Available after quote acceptance</small></span></button>`}${!readOnly&&['In progress','Completed'].includes(j.status)?`<button class="job-action-card" data-final="team-hours" data-id="${h(jid)}"><span class="job-action-icon">◷</span><span><strong>Team hours</strong><small>Enter once, adjust exceptions only</small></span></button>`:''}${q?.service_type==='Long Distance'&&S.user.role!=='crew'?`<button class="job-action-card" data-final="hos-verify" data-id="${h(jid)}"><span class="job-action-icon">H</span><span><strong>Driver HOS verification</strong><small>Review DCE/log, rest, cycle and heavy-truck coverage</small></span></button>`:''}${canPlanJobs()?`<button class="job-action-card" data-jx="edit-plan-simple" data-id="${h(jid)}"><span class="job-action-icon">✎</span><span><strong>Edit team / truck / timing</strong><small>Change operational assignment</small></span></button>`:''}</div>`;
    let primary='';
    if(j.status==='Tentative'&&canPlanJobs())primary+=`<button class="btn-primary" data-jx="confirm" data-id="${h(jid)}">Review job setup</button>`;
    if(j.status==='Confirmed'&&!readOnly)primary+=btn('job-status','Start job','btn-primary',`data-id="${h(jid)}" data-version="${raw.version}" data-status="In progress"`);
    if(j.status==='In progress'&&!readOnly)primary+=btn('job-status','Mark completed','btn-primary',`data-id="${h(jid)}" data-version="${raw.version}" data-status="Completed"`);
    const readOnlyNotice=readOnly?`<div class="callout"><strong>Viewing another team's job</strong>You can review the client, moving details, quote and photos. Editing stays with the assigned team or office.</div><div class="spacer"></div>`:'';
    showModal(h(c.client_name),`${client}${moving}${readOnlyNotice}${actions}<div class="spacer"></div><div class="row wrap">${primary}${may(['owner','dispatch'])&&!['Completed','Cancelled'].includes(j.status)?btn('cancel-job','Cancel job','btn-danger',`data-id="${h(jid)}" data-version="${raw.version}"`):''}</div>`,btn('close','Close'),true);
  };

  // --------- Simple company-email draft from the client/job ---------
  function emailForm(lid){
    showModal('Email client',`<form id="final-email-form" data-final-form="email"><input type="hidden" name="lead_id" value="${h(lid)}"><div class="callout teal"><strong>Company email draft</strong>This is prepared inside CrownOps. Nothing is sent from a personal mailbox.</div><div class="spacer"></div>${field('subject','Subject','Crown Movers - your service','text','required')}${textArea('body','Message','',8)}<input type="hidden" name="category" value="Reply"></form>`,`${btn('close','Cancel')}<button class="btn-primary" type="submit" form="final-email-form">Save email draft</button>`,true);
  }

  async function hosVerification(jid){
    const s=await api('/jobs/'+encodeURIComponent(jid)+'/hos-verification');
    const blockerHtml=(s.blockers||[]).length?'<div class="callout orange"><strong>Not dispatch-ready</strong>'+(s.blockers||[]).map(x=>'<div class="tiny" style="margin-top:5px">'+h(x)+'</div>').join('')+'</div>':
      '<div class="callout teal"><strong>HOS dispatch status</strong>'+(s.required?'Current verification is complete for the assigned heavy truck(s).':'No heavy-vehicle HOS verification is required for the current assigned truck profile.')+'</div>';
    const vehicleRows=(s.vehicles||[]).map(v=>'<div class="quote-line"><span>'+h(v.name)+'</span><strong>'+(v.gvwr_kg==null?'GVWR missing':h(v.gvwr_kg)+' kg')+'</strong></div>').join('');
    const records=(s.records||[]).slice(0,8).map(r=>'<div class="activity-row"><div><strong>'+h(r.driver_name)+' · '+h(r.vehicle_name)+'</strong><p>Cycle '+h(r.cycle)+' · '+h(r.cycle_on_duty_hours)+' h accumulated · '+h(r.projected_on_duty_hours)+' h projected</p><span class="tiny muted">'+h(new Date(r.verified_at).toLocaleString('en-CA',{timeZone:'America/Toronto'}))+' · job version '+h(r.job_version)+'</span></div></div>').join('')||'<p class="small muted">No HOS verification has been recorded for this job yet.</p>';
    let form='';
    if(may(['owner','dispatch'])&&(s.heavy_vehicles||[]).length&&(s.drivers||[]).length){
      const drivers=(s.drivers||[]).map(x=>[x.id,x.name]);
      const trucks=(s.heavy_vehicles||[]).map(x=>[x.id,x.name+' · '+x.gvwr_kg+' kg']);
      form='<div class="spacer"></div><section class="job-section"><div class="eyebrow">DISPATCH VERIFICATION</div><form id="hos-verification-form" data-final-form="hos"><input type="hidden" name="job_id" value="'+h(jid)+'"><div class="grid2">'+
        select('driver_id','Assigned verified driver',drivers,drivers[0]?.[0]||'','required')+
        select('vehicle_id','Assigned heavy truck',trucks,trucks[0]?.[0]||'','required')+
        select('cycle','Driver cycle',[['1','Cycle 1 · 70 h / 7 days'],['2','Cycle 2 · 120 h / 14 days']],'1','required')+
        field('cycle_on_duty_hours','Current cycle on-duty hours','','number','step="0.25" min="0" max="120" required')+
        field('on_duty_since_last_24h_off','On-duty hours since last 24 h off','','number','step="0.25" min="0" max="120" required')+
        field('consecutive_off_duty_before_shift','Consecutive off-duty hours before shift',8,'number','step="0.25" min="0" max="48" required')+
        '</div>'+
        checkedField('had_24h_off_in_prev_14d','DCE/log confirms at least 24 consecutive off-duty hours in the preceding 14 days')+
        checkedField('dce_log_checked','I reviewed the driver DCE/ELD/activity log for current hours')+
        checkedField('daily_off_duty_plan_checked','The schedule preserves the daily 10-hour off-duty requirement')+
        checkedField('elapsed_window_checked','The 16-hour elapsed-time window has been reviewed for the planned work/rest periods')+
        textArea('note','Verification evidence / note','',3)+
        '<div class="callout"><strong>Internal safety rule</strong>Complete or refresh this verification within 24 hours of departure. CrownOps will not allow a long-distance heavy-truck job to start without a current record for every heavy truck.</div></form></section>';
    }
    const body=blockerHtml+'<div class="spacer"></div><section class="job-section"><div class="eyebrow">ASSIGNED TRUCKS</div>'+vehicleRows+'</section><div class="spacer"></div><section class="job-section"><div class="eyebrow">VERIFICATION HISTORY</div>'+records+'</section>'+form;
    const footer=btn('close','Close')+(form?'<button type="submit" form="hos-verification-form" class="btn-primary">Save HOS verification</button>':'');
    showModal('Driver HOS · '+h(s.job?.title||'Long-distance job'),body,footer,true);
  }

  document.addEventListener('click',async e=>{
    const el=e.target.closest('[data-final]');if(!el)return;e.preventDefault();const a=el.dataset.final,id=el.dataset.id;
    try{
      if(a==='schedule-teams'){S.scheduleMode='teams';return navigate('schedule');}
      if(a==='schedule-week'){S.scheduleMode='week';return navigate('schedule');}
      if(a==='open-job')return openJob(id);
      if(a==='open-client')return openLead(id);
      if(a==='legacy-client'){modal.close();return legacyOpenLead(id);}
      if(a==='email-client')return emailForm(id);
      if(a==='team-hours')return addTime(id);
      if(a==='hos-verify')return hosVerification(id);
    }catch(ex){modal.open?modalError(ex):toast(ex.message);}
  });

  document.addEventListener('submit',async e=>{
    const f=e.target.closest('form[data-final-form]');if(!f)return;e.preventDefault();
    try{
      if(f.dataset.finalForm==='email'){
        const d=readForm(f);await api('/outbound',{method:'POST',data:d});modal.close();toast('Company email draft saved. Nothing was sent.');return navigate('communications');
      }
      if(f.dataset.finalForm==='hos'){
        const jobId=f.elements.job_id.value;
        const d={
          driver_id:f.elements.driver_id.value,vehicle_id:f.elements.vehicle_id.value,cycle:Number(f.elements.cycle.value),
          cycle_on_duty_hours:Number(f.elements.cycle_on_duty_hours.value),
          on_duty_since_last_24h_off:Number(f.elements.on_duty_since_last_24h_off.value),
          consecutive_off_duty_before_shift:Number(f.elements.consecutive_off_duty_before_shift.value),
          had_24h_off_in_prev_14d:f.elements.had_24h_off_in_prev_14d.checked,
          dce_log_checked:f.elements.dce_log_checked.checked,
          daily_off_duty_plan_checked:f.elements.daily_off_duty_plan_checked.checked,
          elapsed_window_checked:f.elements.elapsed_window_checked.checked,
          note:f.elements.note.value
        };
        await api('/jobs/'+encodeURIComponent(jobId)+'/hos-verification',{method:'POST',data:d});
        toast('HOS verification saved for this driver and truck.');
        return hosVerification(jobId);
      }
    }catch(ex){modalError(ex);}
  });

  // Upgrade banner/version when the shell is drawn.
  const oldShellFinal=shell;
  shell=function(){oldShellFinal();const strip=document.getElementById('pilot-strip');if(strip)strip.innerHTML=`${icon('shield')}<span>CrownOps v6 launch candidate. Client email, QuickBooks and payments stay protected by their connection and production safety gates.</span>`;};
})();
