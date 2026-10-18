'use strict';
/* A source inquiry belongs to a service only after an unambiguous match. */
const gmailReviewConnections=X.pageRenderers.connections;
const gmailReviewFollowups=X.pageRenderers.followups;
X.pageRenderers.followups=async function(){
 let markup=await gmailReviewFollowups();
 const tasks=await api('/tasks?scope='+encodeURIComponent(UX.followupScope)).catch(()=>[]);
 for(const task of tasks){
  if(task.status==='Done'||!String(task.legacy_id||'').startsWith('gmail-match:'))continue;
  const old='<button class="btn-small" data-ux="followup-complete" data-id="'+h(task.id)+'" data-lead="">Done</button>';
  markup=markup.replace(old,'<button class="btn-small" data-page="connections">Choose client service</button>');
 }
 return markup;
};
const gmailReviewAttentionButton=uxAttentionButton;
uxAttentionButton=function(action){
 if(action.type==='task'&&/^Match incoming (?:quote form|voicemail \/ missed call)/.test(action.title||'')){
  return '<button class="btn-small" data-page="connections">Choose client service</button>';
 }
 return gmailReviewAttentionButton(action);
};
X.pageRenderers.connections=async function(){
 let markup=await gmailReviewConnections();
 if(!may(['owner','manager','sales']))return markup;
 const items=await api('/gmail/review').catch(()=>[]);
 for(const item of items){
  if(!['gmail_quote','gmail_missed_call','gmail_voicemail','gmail_email'].includes(item.event_type))continue;
  const old='<button class="btn-small" data-ux="gmail-reviewed" data-id="'+h(item.id)+'">Reviewed</button>';
  markup=markup.replace(old,'<button class="btn-small" data-gmail-match="'+h(item.id)+'">Choose client service</button>');
 }
 return markup;
};
async function gmailMatchDialog(id){
 const item=await api('/gmail/review/'+encodeURIComponent(id)+'/match-options');
 const p=item.payload||{},parsed=p.parsed||{},match=item.matching||{},candidates=match.candidates||[];
 const summary='<div class="callout orange"><strong>'+h(p.origin_label||'Incoming client inquiry')+'</strong><p>'+h(match.reason||'Review the original inquiry and choose its client service.')+'</p></div>'+
  '<p class="small"><strong>'+h(p.subject||'Client inquiry')+'</strong><br>'+h([parsed.client_name||parsed.caller,parsed.email,parsed.phone,parsed.move_date].filter(Boolean).join(' · '))+'</p>'+
  '<details><summary>Read original inquiry</summary><div class="message-draft">'+h(p.body||p.snippet||JSON.stringify(parsed,null,2))+'</div></details><div class="spacer"></div>';
 if(item.status!=='Needs review')return showModal('Inquiry already resolved',summary+'<p>'+h(item.status)+'</p>',btn('close','Close'),true);
 const decisions=[['','Choose an action'],...(candidates.length?[['match','Attach to an existing service']]:[]),['new','Create a separate new inquiry'],['dismiss','Dismiss this item with a reason']];
 const choices=[['','Choose the correct service'],...candidates.map(c=>[c.id,[c.client_name,uxDateLabel(c.move_date,'Date needed'),c.pickup||'Address needed',c.stage].join(' · ')])];
 showModal('Choose the correct client service',summary+'<form id="gmail-match-form" data-gmail-event="'+h(id)+'"><input type="hidden" name="fingerprint" value="'+h(match.fingerprint)+'"><div class="grid2">'+
  select('decision','What should happen?',decisions,decisions[0][0])+
  (candidates.length?select('lead_id','Existing service',choices,choices[0][0]):'')+
  textArea('note','Short reason for this decision','',3)+checkedField('reviewed','I compared the original inquiry with this service or new inquiry',false)+
  '</div><p class="tiny muted">Attaching an inquiry keeps the existing service details and signed estimate intact. Sales reviews the newly supplied information before changing the service.</p></form>',
  '<button type="submit" form="gmail-match-form" class="btn-primary">Save reviewed decision</button>'+btn('close','Cancel'),true);
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-gmail-match]');if(!b)return;e.preventDefault();b.disabled=true;
 try{await gmailMatchDialog(b.dataset.gmailMatch);}catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('#gmail-match-form');if(!f)return;e.preventDefault();const b=modal.querySelector('[form="gmail-match-form"]');if(b.disabled)return;b.disabled=true;
 try{
  const d=Object.fromEntries(new FormData(f));d.reviewed=f.elements.reviewed.checked;
  const result=await api('/gmail/review/'+encodeURIComponent(f.dataset.gmailEvent)+'/resolve',{method:'POST',data:d});
  modal.close();toast(result.status==='Ignored'?'Inquiry dismissed with its reason.':'Inquiry attached. Review the client’s newly supplied information.');await navigate('connections');
 }catch(ex){modalError(ex);}finally{if(b.isConnected)b.disabled=false;}
});
