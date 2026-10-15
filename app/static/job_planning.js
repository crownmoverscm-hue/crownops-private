/* Direct planning actions with account-based history; no routine reason prompts. */
'use strict';
const JP={data:null,busy:false,preview:0,drafts:{}};
function canPlanJobs(){return Boolean(S.user?.can_plan_jobs||['owner','sales','dispatch','accounting'].includes(S.user?.role));}
function jpTarget(issue){
 if(/crew|driver|employee|pay rate|\bOFF\b/i.test(issue))return 'crew';
 if(/truck|vehicle|inspection|pre-use|repair/i.test(issue))return 'trucks';
 if(/deposit|payment/i.test(issue))return 'payments';
 if(/quote|estimate|scope/i.test(issue))return 'quote';
 if(/phone|address|client/i.test(issue))return 'client';
 return 'timing';
}
function jpResourceList(kind,selected){
 const resources=JP.data.resources[kind],name=kind==='crew'?'crew_ids':'truck_ids';
 return resources.length?resources.map(r=>{
  const checked=selected.includes(r.id),disabled=!r.available&&!checked;
  return '<label class="jp-resource '+(!r.available?'jp-unavailable':'')+'" data-jp-resource="'+h(kind)+'"><input type="checkbox" name="'+name+'" value="'+h(r.id)+'" '+(checked?'checked ':'')+(disabled?'disabled ':'')+'><span class="grow"><strong>'+h(r.name)+'</strong><span class="tiny muted">'+h(r.reasons.join(' · ')||(r.driver_verified?'Available · verified driver':'Available'))+'</span></span>'+(checked?'<span class="pill blue">Selected</span>':'')+'</label>';
 }).join(''):'<p class="small muted">No '+(kind==='crew'?'employees':'trucks')+' have been added yet. Use the setup button below.</p>';
}
function jpHistory(){
 const labels={crew_ids:'Crew',truck_ids:'Trucks',start_at:'Start time',end_at:'End time',notes:'Instructions',stops:'Stops',title:'Title',status:'Status',quote_id:'Accepted estimate'};
 return '<details class="jp-history"><summary>Recent changes · account and time recorded automatically</summary>'+
 (JP.data.history.length?JP.data.history.map(item=>{
  let details={};try{details=JSON.parse(item.details_json);}catch{}
  return '<div class="activity-row"><div><strong>'+h(item.actor_name||'System')+'</strong> · '+h(uxTimestampLabel(item.created_at))+'<div class="small">'+h(item.action)+'</div>'+
   (details.changed_fields?.length?'<div class="tiny muted">'+h(details.changed_fields.map(k=>labels[k]||k).join(', '))+'</div>':'')+'</div></div>';
 }).join(''):'<p class="small muted">No changes recorded yet.</p>')+'</details>';
}
async function openJobPlan(id,focus=''){
 if(!canPlanJobs())throw Error('Crew cannot change job plans.');
 const data=await api('/jobs/'+encodeURIComponent(id)+'/planning');JP.data=data;JP.busy=false;++JP.preview;
 const j=data.job;if(['Completed','Cancelled'].includes(j.status))throw Error('This job is closed. Its plan and history are preserved.');
 const local=value=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Toronto',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)).replace(' ','T');
 const issues=data.readiness.missing||[];
 const attention=issues.length?'<div class="callout orange jp-attention"><strong>Finish the job setup</strong><div class="row wrap">'+issues.map(issue=>'<button type="button" data-plan="fix" data-target="'+h(jpTarget(issue))+'">'+h(issue)+'</button>').join('')+'</div></div>':'<div class="callout teal"><strong>Job setup is ready</strong>Review or change the assignments below.</div>';
 const section=(kind,title,selected)=>'<section id="jp-'+kind+'" class="jp-section"><div class="row wrap"><h3 class="grow">'+title+'</h3><span id="jp-count-'+kind+'" class="pill blue">'+selected.length+' selected</span><button type="button" data-plan="focus" data-target="'+kind+'">Choose '+title.toLowerCase()+'</button></div><label class="jp-search-label" for="jp-search-'+kind+'">Search '+title.toLowerCase()+'</label><input type="search" id="jp-search-'+kind+'" data-plan-search="'+kind+'" placeholder="Search by name or availability"><div id="jp-options-'+kind+'" class="jp-options">'+jpResourceList(kind,selected)+'</div>'+(S.user.role!=='crew'?'<button type="button" data-plan="setup" data-target="'+kind+'">'+(kind==='crew'?'Employee setup & availability':'Truck setup & availability')+'</button>':'<p class="tiny muted">An owner can complete setup for unavailable resources.</p>')+'</section>';
 const notes=j.status==='Confirmed'?j.notes.replace('Accepted estimate - awaiting deposit / booking confirmation. ',''):j.notes;
 showModal('Assign crew, trucks & confirm',attention+'<form id="job-plan-form" data-job-plan><input type="hidden" name="job_id" value="'+h(id)+'">'+
  section('crew','Crew',j.crew_ids)+section('trucks','Trucks',j.truck_ids)+
  '<section id="jp-timing" class="jp-section"><h3>Timing & service</h3><div class="grid2">'+field('title','Job title',j.title,'text','required')+
  field('start_at','Start · Montréal time',local(j.start_at),'datetime-local','required')+field('end_at','End · Montréal time',local(j.end_at),'datetime-local','required')+
  select('quote_id','Accepted estimate',[['','No accepted estimate linked'],...data.accepted_quotes.map(q=>[q.id,'Revision '+q.revision])],j.quote_id||'')+'</div><p id="jp-availability" class="tiny muted" role="status"></p></section>'+
  '<details class="jp-section"><summary>Stops & crew instructions</summary><div class="grid2">'+textArea('stops','Ordered stops · one per line',j.stops.join('\n'),3)+textArea('notes','Crew instructions',notes,4)+'</div></details>'+jpHistory()+'</form><p class="tiny muted">Changes are recorded under your account. Availability and booking checks run when you save.</p>',
  '<button type="button" data-plan="back" data-id="'+h(id)+'">Back to move</button><button type="submit" form="job-plan-form" class="btn-soft" value="save">Save plan</button>'+
  (j.status!=='In progress'?'<button type="submit" form="job-plan-form" class="btn-primary" value="confirm">Save & confirm</button>':''),true);
 const draft=JP.drafts[id],form=document.getElementById('job-plan-form');
 if(draft&&draft.version===j.version){
  for(const [name,value] of Object.entries(draft.fields))if(form.elements[name]&&!['crew_ids','truck_ids','job_id'].includes(name))form.elements[name].value=value;
  for(const kind of ['crew','trucks'])document.getElementById('jp-options-'+kind).innerHTML=jpResourceList(kind,draft[kind]);
  form.dispatchEvent(new Event('change',{bubbles:true}));
  jpRefreshAvailability(form);
 }
 if(focus)jpFocus(focus);
}
function jpRemember(){
 const form=document.getElementById('job-plan-form');if(!form||!JP.data)return;
 const checked=name=>[...form.querySelectorAll('[name="'+name+'"]:checked')].map(x=>x.value);
 JP.drafts[JP.data.job.id]={version:JP.data.job.version,fields:Object.fromEntries(new FormData(form)),crew:checked('crew_ids'),trucks:checked('truck_ids')};
}
function jpFocus(target){
 const el=document.getElementById('jp-'+target);if(!el)return;
 el.scrollIntoView({block:'start',behavior:'smooth'});const input=el.querySelector('input[type=search],input');input?.focus({preventScroll:true});
}
async function jpRefreshAvailability(form){
 const version=++JP.preview,start=form.elements.start_at.value,end=form.elements.end_at.value;if(!start||!end)return;
 const message=document.getElementById('jp-availability');message.textContent='Checking crew and truck availability…';
 try{
  const data=await api('/jobs/'+encodeURIComponent(JP.data.job.id)+'/planning?start_at='+encodeURIComponent(start)+'&end_at='+encodeURIComponent(end));
  if(version!==JP.preview||!form.isConnected)return;
  const selected=kind=>[...form.querySelectorAll('input[name="'+(kind==='crew'?'crew_ids':'truck_ids')+'"]:checked')].map(x=>x.value);
  const crew=selected('crew'),trucks=selected('trucks');JP.data.resources=data.resources;
  document.getElementById('jp-options-crew').innerHTML=jpResourceList('crew',crew);document.getElementById('jp-options-trucks').innerHTML=jpResourceList('trucks',trucks);
  message.textContent=data.job.version===JP.data.job.version?'Availability updated.':'This job changed in another account. Reopen the plan before saving.';
 }catch(ex){if(form.isConnected)message.textContent=ex.message;}
}
window.CrownPlanning={open:openJobPlan,canEdit:canPlanJobs};
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-plan]');if(!b)return;e.preventDefault();e.stopImmediatePropagation();
 try{
  const action=b.dataset.plan,id=b.dataset.id||JP.data?.job.id,target=b.dataset.target;
  if(action==='review')return openJobPlan(id,['crew','trucks','timing'].includes(target)?target:'');
  if(action==='focus')return jpFocus(target);
  if(action==='back')return openJob(id);
  if(action==='fix'){
   if(['crew','trucks','timing'].includes(target))return jpFocus(target);
   if(target==='quote'){jpFocus('timing');return;}
   if(target==='payments'){jpRemember();modal.close();return navigate('payments');}
   if(target==='client'){jpRemember();modal.close();return openLead(JP.data.job.lead_id);}
  }
  if(action==='setup'){
   jpRemember();modal.close();await navigate(target==='crew'?'team':'fleet');
   document.getElementById('content').insertAdjacentHTML('afterbegin','<div class="callout teal jp-return"><strong>Finish resource setup, then return to this job.</strong><button data-plan="review" data-id="'+h(id)+'">Return to job assignments</button></div>');
  }
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}
},true);
document.addEventListener('input',e=>{
 const kind=e.target.dataset.planSearch;if(!kind)return;const query=e.target.value.toLowerCase();
 document.querySelectorAll('[data-jp-resource="'+kind+'"]').forEach(el=>el.hidden=!el.textContent.toLowerCase().includes(query));
});
document.addEventListener('change',e=>{
 const form=e.target.closest('#job-plan-form');if(!form)return;
 if(['start_at','end_at'].includes(e.target.name))jpRefreshAvailability(form);
 for(const [kind,name] of [['crew','crew_ids'],['trucks','truck_ids']]){
  const el=document.getElementById('jp-count-'+kind);if(el)el.textContent=form.querySelectorAll('[name="'+name+'"]:checked').length+' selected';
 }
});
document.addEventListener('submit',async e=>{
 const form=e.target.closest('form[data-job-plan]');if(!form)return;e.preventDefault();if(JP.busy)return;
 const confirmPlan=e.submitter?.value==='confirm',data=Object.fromEntries(new FormData(form)),id=data.job_id;delete data.job_id;
 data.version=JP.data.job.version;data.confirm=confirmPlan;
 data.crew_ids=[...form.querySelectorAll('[name=crew_ids]:checked')].map(x=>x.value);
 data.truck_ids=[...form.querySelectorAll('[name=truck_ids]:checked')].map(x=>x.value);
 data.stops=data.stops.split('\n').map(x=>x.trim()).filter(Boolean);
 JP.busy=true;const buttons=[...modal.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
 try{
  await api('/jobs/'+encodeURIComponent(id)+'/plan',{method:'PUT',data});delete JP.drafts[id];toast(confirmPlan?'Job plan confirmed.':'Job plan saved.');
  await navigate('schedule');await openJob(id);
 }catch(ex){modalError(ex);}finally{JP.busy=false;buttons.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
});
