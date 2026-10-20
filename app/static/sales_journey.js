'use strict';
function crownInquiryTable(rows){
 const labels=['Client','Latest inquiry','Move / service','Stage / follow-up','Sales owner / next action',''];
 return '<div class="table-wrap"><table><thead><tr>'+labels.map(label=>'<th>'+h(label)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map((cell,i)=>'<td data-label="'+h(labels[i])+'">'+cell+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}
function crownSalesPriorities(data){
 return '<div data-sales-priorities="'+h(data.suggestion_count||data.suggestions.length)+'">'+panel('Suggested next actions · '+h(data.date),'<p class="small muted">'+h(data.method)+'</p>'+(data.suggestions.length?data.suggestions.map(s=>'<div class="activity-row"><div class="grow"><strong>'+h(s.client_name)+'</strong><div>'+h(s.action)+'</div><p class="tiny muted">'+h(s.reason)+' · '+h(s.owner)+'</p></div><button class="btn-small" data-sales-open="'+h(s.lead_id)+'">Open client</button></div>').join(''):'<p class="small muted">No priority sales actions found.</p>')+(data.suggestion_count>12?'<p class="tiny muted">Showing 12 of '+h(data.suggestion_count)+' services needing attention. Use Follow-ups to review the full task list.</p>':''))+'</div><div class="spacer"></div>';
}
async function crownRequestDetails(lid){
 const state=await api('/leads/'+encodeURIComponent(lid)+'/detail-requests');const active=state.request;
 const summary=active?'<div class="callout teal"><strong>Last request: '+h(active.status)+'</strong><p>'+h(active.created_at)+'</p></div><div class="spacer"></div>':'';
 const fields=Object.entries(state.labels).map(([k,label])=>'<label class="checkbox-label"><input type="checkbox" name="detail_field" value="'+h(k)+'" '+(state.missing.includes(k)||k==='photos'?'checked':'')+'>'+h(label[0])+'</label>').join('');
 showModal('Request client information',summary+(active?.status==='Open'?'<p>An information request is already open. Review its prepared message in Communications before sending. You can close the request after collecting the details another way.</p>':'<p>The client’s private moving form starts with the details already on file. Select what you want them to check. This prepares a friendly email for your review.</p><form id="detail-request-form" data-sales-details="'+h(lid)+'"><input type="hidden" name="request_key" value="'+crypto.randomUUID()+'"><div class="grid2">'+fields+textArea('note','Optional note to the client','',3)+'</div></form>'),
  (active?.status==='Open'?'<button data-page="communications" class="btn-primary">Review email in Communications</button>':'<button type="submit" form="detail-request-form" class="btn-primary">Prepare request email</button>')+(active&&active.status!=='Reviewed'?'<button data-sales-complete-details="'+h(active.id)+'" data-lead="'+h(lid)+'">'+(active.status==='Open'?'Close request':'Mark information reviewed')+'</button>':'')+btn('close','Close'),true);
}
async function crownDetailsButton(lid){
 if(!may(['owner','manager','sales']))return;
 const foot=modal.querySelector('.modal-foot');if(foot&&!foot.querySelector('[data-sales-details-open]'))foot.insertAdjacentHTML('afterbegin','<button class="btn-soft" data-sales-details-open="'+h(lid)+'">Request missing details</button>');
}
const salesJourneyLead=openLead;
openLead=async function(lid){await salesJourneyLead(lid);await crownDetailsButton(lid);};
const salesJourneyFiles=clientFiles;
clientFiles=async function(lid){await salesJourneyFiles(lid);await crownDetailsButton(lid);};
const salesJourneyQuote=crmOpenQuote;
crmOpenQuote=async function(qid){
 await salesJourneyQuote(qid);const d=CRM.currentQuote;if(!d)return;await crownDetailsButton(d.lead.id);
 const delivery=d.delivery||{};modal.querySelector('.modal-body')?.insertAdjacentHTML('afterbegin','<div class="callout teal"><strong>Estimate: '+h(delivery.state||d.quote.status)+'</strong><p class="small">'+h(delivery.sent_at?'Email sent: '+uxTimestampLabel(delivery.sent_at):'Email delivery has not been confirmed.')+'<br>'+h(delivery.opens||0)+' visible estimate open(s)'+(delivery.last_viewed_at?' · Last open: '+h(uxTimestampLabel(delivery.last_viewed_at)):'')+'</p><p class="tiny muted">Opens count when the estimate is visible in the private client portal, once per browser session every 30 minutes. This does not prove the client read it. Booking confirmation appears separately.</p></div><div class="spacer"></div>');
};
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-sales-details-open],[data-sales-open],[data-sales-history],[data-sales-complete-details]');if(!b)return;e.preventDefault();b.disabled=true;
 try{
  if(b.hasAttribute('data-sales-history')){CRM.showHistory=!CRM.showHistory;return await navigate('quotes');}
  if(b.dataset.salesOpen)return await openLead(b.dataset.salesOpen);
  if(b.dataset.salesDetailsOpen)return await crownRequestDetails(b.dataset.salesDetailsOpen);
  if(b.dataset.salesCompleteDetails){await api('/leads/'+encodeURIComponent(b.dataset.lead)+'/detail-requests/'+encodeURIComponent(b.dataset.salesCompleteDetails)+'/complete',{method:'POST',data:{}});toast('Information request closed.');return await crownRequestDetails(b.dataset.lead);}
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-sales-details]');if(!f)return;e.preventDefault();const b=modal.querySelector('[form="'+f.id+'"]');if(b.disabled)return;b.disabled=true;
 try{
  const d=Object.fromEntries(new FormData(f));d.fields=new FormData(f).getAll('detail_field');
  const r=await api('/leads/'+encodeURIComponent(f.dataset.salesDetails)+'/detail-requests',{method:'POST',data:d});
  if(typeof inboxMail==='function')return await inboxMail(r.mail_id);
  showModal('Client information request prepared','<div class="callout teal">'+(r.duplicate?'There is already an open request.':'Your request email is ready.')+' Review it in Communications and send when ready.</div><p>Client information stays attached to this service. Submitted changes enter the review queue.</p>','<button data-page="communications" class="btn-primary">Open Communications</button>'+btn('close','Close'));
 }catch(ex){modalError(ex);}finally{if(b.isConnected)b.disabled=false;}
});
