/* Everyone can submit a report. Active owner/dispatch accounts verify and close it. */
'use strict';
const INC={status:'',category:'',search:'',offset:0,options:null,busy:false};
const incidentKinds=['Client move','Property damage','Company truck','Other vehicle','Other incident'];
function incidentReviewLabel(r){
 if(r.status==='Completed')return r.completer_name?'Completed by '+r.completer_name+' · '+uxTimestampLabel(r.completed_at):'Historical completion · reviewer not recorded';
 if(r.status==='Verified')return 'Verified by '+(r.verifier_name||'Recorded account')+' · '+uxTimestampLabel(r.verified_at);
 return r.reviewer_name?'Reviewed by '+r.reviewer_name+' · '+uxTimestampLabel(r.reviewed_at):'Awaiting manager review';
}
X.pageRenderers.care=async()=>{
 const d=await api('/incidents?status='+encodeURIComponent(INC.status)+'&category='+encodeURIComponent(INC.category)+'&q='+encodeURIComponent(INC.search)+'&offset='+INC.offset);INC.list=d;
 const table=d.items.map(r=>[h(uxTimestampLabel(r.submitted_at)),h(r.category),'<strong>'+h(r.title)+'</strong><div class="tiny muted">'+h(r.client_name||r.vehicle_name||r.location||'General incident')+'</div>',h(r.reporter_name),pill(r.status)+'<div class="tiny muted">'+h(incidentReviewLabel(r))+'</div>','<button class="btn-small" data-incident="open" data-id="'+h(r.id)+'">Open report</button>']);
 return head('Incident reports',d.can_read_all?'Track incidents, review the details and record who verified or completed them.':'Submit a report and follow the status of reports you have submitted.','<button class="btn-primary" data-incident="new">New report</button>')+
 '<form data-incident-filter class="filterbar incident-filters"><input name="search" aria-label="Search incident reports" placeholder="Search reports" value="'+h(INC.search)+'"><select name="status" aria-label="Report status"><option value="">All statuses</option>'+['Submitted','In review','Verified','Completed'].map(x=>'<option '+(x===INC.status?'selected':'')+'>'+x+'</option>').join('')+'</select><select name="category" aria-label="Incident category"><option value="">All incident types</option>'+incidentKinds.map(x=>'<option '+(x===INC.category?'selected':'')+'>'+x+'</option>').join('')+'</select><button type="submit">Apply filters</button></form>'+
 panel('Reports · newest submitted first',table.length?xtable(['Submitted','Type','Report','Submitted by','Status / reviewing account',''],table):empty('No incident reports here','Use New report to record damage, a problem or another incident.'))+
 '<div class="row wrap incident-pages"><span class="tiny muted">'+d.total+' report'+(d.total===1?'':'s')+'</span><button data-incident="previous" '+(INC.offset?'':'disabled')+'>Previous</button><button data-incident="next" '+(INC.offset+d.items.length<d.total?'':'disabled')+'>Next</button>'+(may(['owner'])?'<div class="grow"></div><button data-incident="videos">Video permissions & credits</button>':'')+'</div>';
};
async function newIncident(jobId=''){
 const options=await api('/incidents/options');INC.options=options;
 const selected=options.jobs.some(j=>j.id===jobId)?jobId:'';
 showModal('New incident report','<form id="incident-report-form" data-incident-form="new" data-request-key="'+crypto.randomUUID()+'"><div class="grid2">'+
 select('category','Incident type',incidentKinds,selected?'Client move':'Other incident')+field('title','Short title','','text','maxlength="200" required')+
 select('job_id','Related move (optional)',[['','No related move'],...options.jobs.map(j=>[j.id,j.client_name+' · '+dateLabel(localDay(j.start_at))+' · '+j.title])],selected)+
 select('vehicle_id','Company truck (optional)',[['','No company truck'],...options.vehicles.map(v=>[v.id,v.name])])+field('other_vehicle','Other vehicle / plate (if relevant)','','text','maxlength="1000"')+
 field('occurred_at','When it happened · Montréal time (optional)','','datetime-local')+field('location','Location / address','','text','maxlength="1000"')+
 textArea('detail','What happened? Describe the damage or issue.','',6)+'</div><p class="tiny muted">Your account and submission time are recorded automatically. You can submit a general report without linking a client or truck.</p></form>',btn('close','Cancel')+'<button type="submit" form="incident-report-form" class="btn-primary">Submit report</button>',true);
}
async function openIncident(id){
 const d=await api('/incidents/'+encodeURIComponent(id)),r=d.report;INC.current=d;
 const information='<div class="row wrap">'+pill(r.status)+'<span class="pill gray">'+h(r.category)+'</span></div><p class="small">Submitted by <strong>'+h(r.reporter_name)+'</strong> · '+h(uxTimestampLabel(r.submitted_at))+'</p><div class="grid2"><div><div class="info-label">When it happened</div>'+h(r.occurred_at?uxTimestampLabel(r.occurred_at):'Not provided')+'</div><div><div class="info-label">Location</div>'+h(r.location||'Not provided')+'</div><div><div class="info-label">Move</div>'+h(r.job_title||'Not linked')+'</div><div><div class="info-label">Vehicle</div>'+h([r.vehicle_name,r.other_vehicle].filter(Boolean).join(' · ')||'Not linked')+'</div></div><div class="spacer"></div><div class="message-draft">'+h(r.detail)+'</div>';
 const review='<div class="callout '+(r.status==='Completed'||r.status==='Verified'?'teal':'orange')+'"><strong>'+h(incidentReviewLabel(r))+'</strong>'+(r.resolution?h(r.resolution):'')+'</div>';
 const history='<details class="incident-history" open><summary>Report history</summary>'+d.history.map(e=>'<div class="activity-row"><div><strong>'+h(e.action)+'</strong> · '+h(e.actor_name)+'<div class="tiny muted">'+h(uxTimestampLabel(e.created_at))+'</div>'+(e.note?'<p class="small">'+h(e.note)+'</p>':'')+'</div></div>').join('')+'</details>';
 const edit=d.can_review?'<form id="incident-review-form" data-incident-form="review"><input type="hidden" name="id" value="'+h(id)+'"><input type="hidden" name="version" value="'+r.version+'">'+textArea('note','Resolution or review note (optional)','',3)+'</form>':'';
 const footer=btn('close','Close')+(r.job_id?'<button data-incident="move" data-id="'+h(r.job_id)+'">Open related move</button>':'')+
 (d.can_review?(r.status!=='In review'?'<button type="submit" form="incident-review-form" value="In review">'+(r.status==='Completed'?'Reopen for review':'Mark in review')+'</button>':'')+
 (r.status!=='Verified'&&r.status!=='Completed'?'<button type="submit" form="incident-review-form" value="Verified" class="btn-soft">Mark verified</button>':'')+
 (r.status!=='Completed'?'<button type="submit" form="incident-review-form" value="Completed" class="btn-primary">Mark completed</button>':''):'');
 showModal(h(r.title),information+'<div class="spacer"></div>'+review+edit+history,footer,true);
}
document.addEventListener('click',async event=>{
 const el=event.target.closest('[data-incident]');if(!el)return;event.preventDefault();
 try{
  const action=el.dataset.incident,id=el.dataset.id;
  if(action==='new')return newIncident(id||'');
  if(action==='open')return openIncident(id);
  if(action==='move')return openJob(id);
  if(action==='next'){INC.offset+=50;return navigate('care');}
  if(action==='previous'){INC.offset=Math.max(0,INC.offset-50);return navigate('care');}
  if(action==='videos'){
   const releases=await api('/testimonials');X.cache.releases=releases;
   return showModal('Video permissions & credits',panel('Existing client permissions',xtable(['Client','Signed','Permission','Credit',''],releases.map(r=>[h(r.client_name),h(uxTimestampLabel(r.signed_at)),pill(r.status),r.applied_cents?money(r.applied_cents):'Not applied',xbtn('video-detail','Review',r.id,'btn-small')]))),btn('close','Close'),true);
  }
 }catch(error){modal.open?modalError(error):toast(error.message);}
});
document.addEventListener('submit',async event=>{
 const filter=event.target.closest('[data-incident-filter]');if(filter){event.preventDefault();const f=new FormData(filter);INC.search=f.get('search');INC.status=f.get('status');INC.category=f.get('category');INC.offset=0;return navigate('care');}
 const form=event.target.closest('[data-incident-form]');if(!form)return;event.preventDefault();if(INC.busy)return;
 INC.busy=true;const buttons=[...modal.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
 try{
  const data=Object.fromEntries(new FormData(form));
  if(form.dataset.incidentForm==='new'){data.request_key=form.dataset.requestKey;const r=await api('/incidents',{method:'POST',data});toast('Incident report submitted.');modal.close();INC.offset=0;await navigate('care');return await openIncident(r.id);}
  data.version=Number(data.version);data.status=event.submitter?.value;const id=data.id;delete data.id;
  await api('/incidents/'+encodeURIComponent(id)+'/review',{method:'POST',data});toast('Report marked '+data.status.toLowerCase()+'.');await navigate('care');await openIncident(id);
 }catch(error){modalError(error);}finally{INC.busy=false;buttons.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
});
