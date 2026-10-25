/* Explicit launch walkthroughs. Rendering never sends, imports or calls providers. */
(function(){
 const oldConnections=X.pageRenderers.connections;
 X.pageRenderers.connections=async function(){
  const body=await oldConnections();if(S.user.role!=='owner')return body;
  return body+panel('Staging and launch checks','<p>Preview setup files without importing. Sender verification sends only after explicit approval to an approved test address.</p><div class="row wrap"><button data-launch="sender">Verify company sender</button><button data-launch="staff">Preview staff invitations</button><button data-launch="materials">Preview materials import</button><button data-launch="recovery">Preview stored form recovery</button></div>');
 };
 async function sender(){
  const proof=await api('/company-sender/proof');
  showModal('Company sender verification','<p><strong>'+h(proof.state)+'</strong></p><ol><li>In Gmail, open Settings → See all settings → Accounts and Import → Send mail as.</li><li>Add and verify info@crownmovers.ca using the mailbox verification email.</li><li>Review CrownOps Email rules, then send one approved test below.</li><li>Check actual headers, approve the matching sender, and separately confirm inbox delivery.</li></ol>'+
   (proof.actual_from?'<p>Actual From: '+h(proof.actual_from)+'<br>Actual Reply-To: '+h(proof.actual_reply_to||'Missing')+'</p>':'')+
   '<label>Approved test recipient<input id="sender-proof-recipient" type="email" value="jonathanprobonas15@gmail.com"></label><p class="small">Provider send confirmation does not prove inbox delivery.</p>',
   '<button data-action="close">Close</button><button data-launch="sender-check">Check sent headers</button>'+
   (proof.state==='Verified'?'<button data-launch="sender-approve" class="btn-primary">Approve verified sender</button>':'')+
   (proof.state!=='Pending'?'<button data-launch="sender-send">Send verification test</button>':''));
 }
 function preview(kind){
  const recovery=kind==='recovery';
  showModal('Dry-run preview — '+kind,'<p>This preview writes no records and sends nothing. '+(recovery?'Paste the documented event_ids JSON object.':'Choose a CSV file or paste the documented rows JSON object.')+'</p>'+
   (recovery?'':'<label>CSV file<input id="launch-preview-file" type="file" accept=".csv,text/csv"></label>')+
   '<label>Preview data<textarea id="launch-preview-input" rows="10" style="width:100%;box-sizing:border-box">'+h(recovery?'{}':'{"rows": []}')+'</textarea></label><pre id="launch-preview-result" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>',
   '<button data-action="close">Close</button><button data-launch="preview" data-kind="'+kind+'" class="btn-primary">Validate preview</button>');
 }
 const oldEstimate=window.openQuickBooksEstimate;
 window.openQuickBooksEstimate=async function(qid){
  const state=await api('/quotes/'+encodeURIComponent(qid)+'/booking-workflow');
  if(!state.available&&!state.workflow_enabled)return oldEstimate(qid);
  const base='/quotes/'+encodeURIComponent(qid)+'/booking-workflow';
  let preview=null;
  if(state.quickbooks_estimate.id)preview=await api(base+'/preview');
  const totals=preview||state.quickbooks_estimate.reviewed_totals;
  const actions='<button data-action="close">Close</button>'+
   (!state.quickbooks_estimate.id?'<button data-launch="quote-prepare" data-id="'+h(qid)+'">Prepare sandbox estimate</button>':'')+
   (preview?'<button data-launch="quote-review" data-id="'+h(qid)+'" data-review="'+h(preview.review_hash)+'">Approve totals and prepare email draft</button>':'')+
   (preview&&state.email.status==='Draft'?'<button data-launch="quote-send" data-id="'+h(qid)+'" data-review="'+h(preview.review_hash)+'" class="btn-primary">Send approved test email</button>':'')+
   (state.acceptance.client_status==='Accepted'&&state.acceptance.quickbooks_sync!=='Synced'?'<button data-launch="quote-sync" data-id="'+h(qid)+'">Sync accepted status to sandbox</button>':'');
  showModal('CrownOps email + QuickBooks sandbox','<p>QuickBooks stores the official estimate. CrownOps sends the private quote link. QuickBooks email stays off. Acceptance creates a tentative job only.</p>'+
   '<p>QuickBooks estimate ID: <strong>'+h(state.quickbooks_estimate.id||'Not prepared')+'</strong><br>Email: '+h(state.email.status)+'<br>Client: '+h(state.acceptance.client_status)+'<br>Acceptance sync: '+h(state.acceptance.quickbooks_sync)+'</p>'+
   (state.acceptance.error?'<div class="callout orange">'+h(state.acceptance.error)+'</div>':'')+
   (totals?'<p>Before tax: '+money(totals.subtotal_cents)+'<br>Tax: '+money(totals.tax_cents)+'<br><strong>Total: '+money(totals.total_cents)+'</strong></p>':'')+
   (preview?(preview.lines||[]).map(line=>'<p style="white-space:pre-wrap;overflow-wrap:anywhere">'+h(line.description)+' '+money(line.amount_cents||0)+'</p>').join(''):'<label>Amount displayed in QuickBooks<select id="launch-quote-basis"><option value="high">High end of range</option><option value="low">Low end of range</option></select></label>')+
   '<p class="small">No payment or final booking confirmation is triggered here.</p>',actions);
 };
 document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-launch]');if(!b)return;event.preventDefault();if(b.disabled)return;b.disabled=true;
  const action=b.dataset.launch;
  try{
   if(action==='sender')return await sender();
   if(['staff','materials','recovery'].includes(action))return preview(action);
   if(action==='preview'){
    const kind=b.dataset.kind,route=kind==='recovery'?'/intake/recovery/preview':'/launch/'+(kind==='staff'?'staff-invitations':'materials')+'/preview';
    const file=document.getElementById('launch-preview-file')?.files?.[0];
    if(file&&file.size>100000)throw Error('Use a CSV under 100 KB.');
    const data=file?{csv:await file.text()}:JSON.parse(document.getElementById('launch-preview-input').value);
    const result=await api(route,{method:'POST',data});
    document.getElementById('launch-preview-result').textContent=JSON.stringify(result,null,2);return;
   }
   if(action.startsWith('sender-')){
    if(action==='sender-send'){
     const recipient=document.getElementById('sender-proof-recipient').value;
     if(!confirm('Send ONE company sender verification email to '+recipient+'?'))return;
     await api('/company-sender/proof/send-test',{method:'POST',data:{confirm:true,recipient,request_id:crypto.randomUUID()}});
    }else if(action==='sender-approve'){
     if(!confirm('Approve the displayed actual From and Reply-To as the company sender?'))return;
     await api('/company-sender/proof/approve',{method:'POST',data:{confirm:true}});
    }else await api('/company-sender/proof/check',{method:'POST',data:{}});
    return await sender();
   }
   const qid=b.dataset.id,base='/quotes/'+encodeURIComponent(qid)+'/booking-workflow';
   if(!confirm(action==='quote-send'?'Send ONE reviewed quote email to the approved test client?':'Proceed with this sandbox-only step? No payment is requested.'))return;
   const endpoint={'quote-prepare':'prepare','quote-review':'review','quote-send':'send-email','quote-sync':'sync-acceptance'}[action];
   const basis=document.getElementById('launch-quote-basis')?.value;
   const result=await api(base+'/'+endpoint,{method:'POST',data:{confirm:true,review_hash:b.dataset.review,...(basis?{basis}:{})}});
   if(result.send_error)toast(result.send_error);
   await window.openQuickBooksEstimate(qid);
  }catch(error){toast(error.message);}finally{if(b.isConnected)b.disabled=false;}
 });
})();
