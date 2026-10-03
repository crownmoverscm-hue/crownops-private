/* CrownOps v6 aftercare: consistent, neutral review outreach to every completed client. */
'use strict';
const crmOldCare=X.pageRenderers.care;
X.pageRenderers.care=async()=>{
 const base=await crmOldCare();
 const [jobs,cfg]=await Promise.all([api('/jobs'),api('/crm/review-settings')]);
 const completed=jobs.filter(j=>j.status==='Completed').slice().sort((a,b)=>String(b.start_at).localeCompare(String(a.start_at))).slice(0,60);
 const rows=completed.map(j=>[
   '<div class="client-name">'+h(j.client?.client_name||j.title)+'</div><div class="tiny muted">'+h(j.client?.email||'')+'</div>',
   h(localDay(j.start_at)),h(j.phase||'Moving'),
   '<button class="btn-small" data-crm-review="prepare" data-id="'+h(j.id)+'">Prepare honest review request</button>'
 ]);
 const setup=may(['owner'])?'<form id="crm-review-settings-form" data-crm-review-form="settings"><div class="row wrap">'+
   '<div class="form-field grow"><label>Public review link</label><input name="review_url" type="url" value="'+h(cfg.review_url||'')+'" placeholder="https://..." required></div>'+
   '<label class="checkbox-label"><input type="checkbox" name="enabled" '+(cfg.enabled?'checked':'')+'>Enable automation checks</label>'+
   '<label class="checkbox-label"><input type="checkbox" name="review_request" '+(cfg.review_request?'checked':'')+'>Prepare review requests after completed jobs</label>'+
   '<button class="btn-primary" type="submit">Save review settings</button></div></form>':
   '<p class="small muted">Management controls the public review link and automation policy.</p>';
 return base+'<div class="spacer"></div>'+panel('Public review follow-up',
   '<div class="callout teal"><strong>Ask every completed client for an honest review.</strong>No rating is requested, no unhappy client is filtered out, and no discount or benefit is tied to the public review. This keeps the review program trustworthy while making follow-up consistent.</div>'+
   '<div class="spacer"></div>'+setup+'<div class="spacer"></div>'+
   (rows.length?xtable(['Client','Completed','Service',''],rows):empty('No completed services yet','Completed jobs will appear here for review follow-up.'))
 );
};
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-crm-review]');if(!b)return;e.preventDefault();
 try{
   if(b.dataset.crmReview==='prepare'){
     const r=await api('/crm/jobs/'+encodeURIComponent(b.dataset.id)+'/review-request',{method:'POST'});
     toast('Honest review request drafted in Communications.');
     return navigate('communications');
   }
 }catch(ex){toast(ex.message);}
});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-crm-review-form]');if(!f)return;e.preventDefault();
 try{
   const d=Object.fromEntries(new FormData(f));d.enabled=f.enabled.checked;d.review_request=f.review_request.checked;
   await api('/crm/review-settings',{method:'POST',data:d});toast('Review follow-up settings saved.');return navigate('care');
 }catch(ex){toast(ex.message);}
});
