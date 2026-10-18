/* The assigned team can prepare its service and check its actual truck from the job. */
'use strict';
(function(){
 const CH={brief:null};
 const css=document.createElement('style');css.textContent='.crew-brief-summary{margin:16px 0;padding:16px;border:1px solid var(--border,#dce3ee);border-radius:12px;background:var(--panel,#fff)}.crew-brief-summary p{margin:8px 0}.crew-brief-check{display:flex;align-items:flex-start;gap:12px;padding:13px 0;border-bottom:1px solid var(--border,#e5e9f0)}.crew-brief-check input{width:22px;height:22px;flex:none;margin:0}.crew-brief-check span{min-width:0;overflow-wrap:anywhere}.crew-guide-text{white-space:pre-wrap;overflow-wrap:anywhere}.crew-brief-list{padding-left:22px}.crew-brief-list li{margin:8px 0}.crew-check-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 20px}.crew-check-grid .crew-brief-check{height:100%}@media(max-width:600px){.crew-check-grid{grid-template-columns:1fr}.crew-brief-summary .row>button{flex:1 1 100%;white-space:normal}.crew-brief-summary{padding:13px}}';document.head.appendChild(css);
 const canManage=()=>['owner','manager'].includes(S.user?.role);
 const stamp=value=>value?uxTimestampLabel(value):'Not recorded';
 const safeLink=value=>{try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:'';}catch{return '';}};
 const backButton=id=>'<button type="button" data-crew-handoff="job" data-id="'+h(id)+'">Back to move</button>';
 const protocolText=p=>['preparation','service','completion'].map((key,i)=>'<section class="panel panel-body"><h3>'+['Before service','During service','After service'][i]+'</h3><div class="crew-guide-text">'+h(p[key])+'</div></section>').join('<div class="spacer"></div>');
 const truckButtons=d=>d.trucks.length?d.trucks.map(t=>'<button type="button" data-crew-handoff="truck" data-id="'+h(d.job.id)+'" data-truck="'+h(t.id)+'" '+(!d.can_check_truck?'disabled':'')+'>Check '+h(t.name)+'</button>').join(''):'<span class="small muted">No truck assigned. Ask the office to finish the plan.</span>';

 const previousJob=openJob;
 openJob=async function(id){
  await previousJob(id);
  const job=S.modalContext?.job;
  if(!job||job.id!==id||!['owner','manager','crew'].includes(S.user?.role))return;
  if(S.user.role==='crew'&&!(job.crew_ids||[]).includes(S.user.employee_id))return;
  const d=await api('/jobs/'+encodeURIComponent(id)+'/handoff');
  if(!modal.open||S.modalContext?.job?.id!==id)return;
  const body=modal.querySelector('.modal-body');if(!body)return;
  const summary='<section class="crew-brief-summary" data-crew-brief-entry><h3>Crew preparation</h3><p class="small">Lead: <strong>'+h(d.leads.join(' · ')||'Ask the office to confirm the lead')+'</strong> · '+d.materials.length+' agreed material item'+(d.materials.length===1?'':'s')+'</p><div class="row wrap"><button type="button" class="btn-primary" data-crew-handoff="brief" data-id="'+h(id)+'">Crew brief & checklist</button>'+truckButtons(d)+'</div><p class="tiny muted">'+(d.checklist.review_required?'The plan changed. Review the checklist again.':'Check the service details, materials and approved instructions before departure.')+'</p></section>';
  const actions=body.querySelector('.job-actions');if(actions)actions.insertAdjacentHTML('beforebegin',summary);else body.insertAdjacentHTML('beforeend',summary);
 };

 async function openBrief(id){
  const d=await api('/jobs/'+encodeURIComponent(id)+'/handoff');CH.brief=d;
  const c=d.client,k=d.checklist;
  const materials=d.materials.length?'<ul class="crew-brief-list">'+d.materials.map(m=>'<li><strong>'+h(m.quantity)+' × '+h(m.name)+'</strong>'+(m.note?'<br><span class="small">'+h(m.note)+'</span>':'')+'</li>').join('')+'</ul>':'<p class="small muted">No materials listed on the accepted estimate. Confirm what equipment or protection is needed with the lead.</p>';
  const trucks=d.trucks.map(t=>'<div class="activity-row"><div class="grow"><strong>'+h(t.name)+'</strong> · '+h(t.plate||'Plate not entered')+'<div class="tiny muted">'+(t.active?'Active':'Inactive — ask the office to assign a safe truck')+' · Last check: '+stamp(t.latest_check?.checked_at)+(t.latest_check?' · '+h(t.latest_check.severity):'')+'</div></div></div>').join('')||'<p class="small muted">Truck not assigned.</p>';
  const contact='<p class="small">'+h(dateLabel(localDay(d.job.start_at)))+' · '+h(timeLabel(d.job.start_at))+'–'+h(timeLabel(d.job.end_at))+'</p>'+(c.phone?'<p><a class="button-link" href="tel:'+h(String(c.phone).replace(/[^+\d]/g,''))+'">Call '+h(c.client_name)+'</a></p>':'<p class="small muted">Client phone not recorded. Ask the office before departure.</p>');
  const files='<button type="button" data-crew-handoff="photos" data-id="'+h(id)+'">View / add photos and videos ('+d.photo_count+' photos · '+d.video_count+' videos)</button>'+d.media_links.map(l=>{const url=safeLink(l.url);return url?'<p><a class="button-link" href="'+h(url)+'" target="_blank" rel="noopener noreferrer">'+h(l.label)+' ↗</a></p>':'';}).join('');
  const protocols='<details class="panel panel-body"><summary><strong>'+h(d.protocols.title)+'</strong> · '+(d.protocols.approved?'Approved guide':'Manager review needed')+'</summary><div class="spacer"></div>'+(!d.protocols.approved?'<p class="small muted">These are general reminders. Follow the approved company procedures and ask your manager about any missing instructions.</p>':'<p class="tiny muted">Reviewed by '+h(d.protocols.updated_by_name)+' · '+stamp(d.protocols.updated_at)+'</p>')+protocolText(d.protocols)+(canManage()?'<p><button type="button" data-crew-handoff="protocols" data-id="'+h(id)+'">Edit crew guide</button></p>':'')+'</details>';
  const checks=['Before service','After service'].map(group=>'<fieldset class="panel panel-body"><legend><strong>'+group+'</strong></legend>'+k.items.filter(i=>i.group===group).map(i=>'<label class="crew-brief-check"><input type="checkbox" name="'+h(i.key)+'" '+(k.checks[i.key]?'checked ':'')+(!d.can_update?'disabled ':'')+'><span>'+h(i.label)+'</span></label>').join('')+'</fieldset>').join('<div class="spacer"></div>');
  const history=k.updated_at?'<p class="tiny muted">Last saved by '+h(k.updated_by_name)+' · '+stamp(k.updated_at)+'</p>':'';
  showModal('Crew brief · '+h(c.client_name),
   '<section class="panel panel-body"><h3>'+h(d.job.title)+'</h3>'+contact+'<p><strong>Lead:</strong> '+h(d.leads.join(' · ')||'Not selected — ask the office')+'</p><p><strong>Assigned crew:</strong> '+h(d.crew.map(e=>e.name+(e.driver_verified?' (verified driver)':'')).join(' · ')||'Not assigned')+'</p>'+trucks+'<div class="row wrap">'+truckButtons(d)+'</div></section><div class="spacer"></div>'+renderMoveRoute(d.route)+'<div class="spacer"></div><section class="panel panel-body"><h3>Inventory & instructions</h3><p class="small">'+h([c.home_size,c.boxes?'Boxes: '+c.boxes:'',c.heavy_items].filter(Boolean).join(' · ')||'Review the client details with the lead.')+'</p><div class="crew-guide-text">'+h([c.inventory,c.service_details,(c.additional_services||[]).join(' · '),d.job.notes].filter(Boolean).join('\n\n')||'No additional instructions recorded.')+'</div></section><div class="spacer"></div><section class="panel panel-body"><h3>Materials & equipment</h3>'+materials+'<p class="small muted">Check the required tools, dollies, straps, blankets and protection with the lead. Ask management to approve any extra charge before proceeding.</p></section><div class="spacer"></div><section class="panel panel-body"><h3>Photos & shared folders</h3>'+files+'</section><div class="spacer"></div>'+protocols+'<div class="spacer"></div>'+(k.review_required?'<div class="callout orange"><strong>The service plan or crew guide changed.</strong>Review the checked items before saving them again.</div><div class="spacer"></div>':'')+'<form id="crew-handoff-form" data-crew-handoff-form="checklist" data-job="'+h(id)+'">'+checks+'<div class="spacer"></div>'+textArea('notes','Notes for the team',k.notes,3)+history+'</form><p class="small muted">This checklist keeps the team informed. Record truck checks, actual hours, invoices and payments in their own service actions.</p>',backButton(id)+(d.can_update?'<button type="submit" form="crew-handoff-form" class="btn-primary">Save checklist</button>':''),true);
 }

 async function openTruck(id,truckId){
  const d=await api('/jobs/'+encodeURIComponent(id)+'/handoff'),truck=d.trucks.find(t=>t.id===truckId);CH.brief=d;
  if(!truck||!d.can_check_truck)throw Error('Open an active service with this assigned truck to record a check.');
  const labels={tires:'Tires',lights:'Lights',brakes:'Brakes',fluids:'Fluids / leaks',body:'Body condition',dollies_3:'3 dedicated dollies',straps_2:'2 sets of straps',blankets:'Blankets and tools',paperwork:'Required paperwork',locked:'Security / lockup checked'};
  showModal('Check assigned truck · '+h(truck.name),'<p class="small">'+h(d.client.client_name)+' · '+h(truck.plate||'Verify the actual truck')+'</p><p class="small muted">Complete the actual check using the company’s approved procedure. Describe defects or missing equipment and notify the manager.</p><form id="crew-truck-check" data-crew-handoff-form="truck" data-job="'+h(id)+'" data-truck="'+h(truckId)+'">'+field('odometer_km','Current odometer · km',truck.odometer_km||0,'number','min="0" required')+'<div class="crew-check-grid">'+Object.entries(labels).map(([key,label])=>'<label class="crew-brief-check"><input type="checkbox" name="'+h(key)+'"><span>'+h(label)+'</span></label>').join('')+'</div><div class="spacer"></div>'+select('severity','Defects found',[['','Choose result'],['None','No defects'],['Minor','Minor defect'],['Major','Major defect — truck unavailable']],'','required')+textArea('notes','Defects, missing equipment or check notes','',4)+'</form><p class="small muted">A major defect makes the truck unavailable. Only management can record repair clearance and reactivate it.</p>',backButton(id)+'<button type="submit" form="crew-truck-check" class="btn-primary">Save truck check</button>',true);
 }

 async function openProtocols(id=''){
  const p=await api('/crew-protocols');if(!p.can_edit)throw Error('An owner or manager can edit the crew guide.');
  showModal('Approved crew guide','<form id="crew-guide-form" data-crew-handoff-form="protocols" data-job="'+h(id)+'"><input type="hidden" name="version" value="'+h(p.version)+'">'+field('title','Guide title',p.title,'text','required')+textArea('preparation','Before service',p.preparation,5)+textArea('service','During service',p.service,5)+textArea('completion','After service',p.completion,5)+'<div class="spacer"></div><label class="crew-brief-check"><input type="checkbox" name="reviewed" required><span>I reviewed these instructions and approve them for the team.</span></label></form>',(id?backButton(id):btn('close','Close'))+'<button type="submit" form="crew-guide-form" class="btn-primary">Publish crew guide</button>',true);
 }

 const priorHelp=X.pageRenderers.help;
 if(priorHelp)X.pageRenderers.help=async function(){const result=await priorHelp();return result+(canManage()?'<div class="spacer"></div>'+panel('Crew service guide','<p class="small">Keep the instructions shown with every assigned service current.</p><button data-crew-handoff="protocols" class="btn-primary">Review crew guide</button>'):'');};
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-crew-handoff]');if(!button)return;event.preventDefault();
  try{const action=button.dataset.crewHandoff,id=button.dataset.id||'';
   if(action==='brief')return await openBrief(id);
   if(action==='job')return await openJob(id);
   if(action==='truck')return await openTruck(id,button.dataset.truck);
   if(action==='protocols')return await openProtocols(id);
   if(action==='photos')return await CrownJobExperience.openPhotos(id);
  }catch(error){modal.open?modalError(error):toast(error.message);}
 });
 document.addEventListener('submit',async event=>{
  const form=event.target.closest('form[data-crew-handoff-form]');if(!form)return;event.preventDefault();
  if(form.dataset.busy)return;form.dataset.busy='1';const buttons=[...modal.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
  try{
   const kind=form.dataset.crewHandoffForm,id=form.dataset.job;
   if(kind==='checklist'){
    const k=CH.brief.checklist,checks=Object.fromEntries(k.items.map(i=>[i.key,form.elements[i.key].checked]));
    await api('/jobs/'+encodeURIComponent(id)+'/handoff',{method:'PUT',data:{version:k.version,job_version:k.job_version,protocol_version:k.protocol_version,checks,notes:form.elements.notes.value}});
    toast('Crew checklist saved.');return await openBrief(id);
   }
   if(kind==='truck'){
    const keys=['tires','lights','brakes','fluids','body','dollies_3','straps_2','blankets','paperwork','locked'];
    const checklist=Object.fromEntries(keys.map(key=>[key,form.elements[key].checked]));
    if(form.elements.severity.value==='None'&&Object.values(checklist).some(value=>!value))throw Error('Check every item before choosing No defects. Describe any defect or missing equipment and choose the appropriate defect classification.');
    const result=await api('/fleet/'+encodeURIComponent(form.dataset.truck)+'/inspection',{method:'POST',data:{job_id:id,odometer_km:Number(form.elements.odometer_km.value),severity:form.elements.severity.value,notes:form.elements.notes.value,checklist}});
    toast(result.truck_blocked?'Check saved. Truck unavailable — contact the manager.':'Truck check saved.');return await openJob(id);
   }
   if(kind==='protocols'){
    const data=Object.fromEntries(new FormData(form));data.version=Number(data.version);data.reviewed=form.elements.reviewed.checked;
    await api('/crew-protocols',{method:'PUT',data});toast('Crew guide published.');if(id)return await openBrief(id);modal.close();return await navigate('help');
   }
  }catch(error){modalError(error);}finally{delete form.dataset.busy;buttons.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
 });
 window.CrownHandoff={openBrief,openTruck,openProtocols};
})();
