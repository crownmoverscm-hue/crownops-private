/* One provider sends the estimate; acceptance is separate from booking confirmation. */
window.openQuickBooksEstimate = async function(qid){
 const base='/quotes/'+encodeURIComponent(qid)+'/quickbooks-delivery';
 const r=await api(base);
 const close='<button data-action="close">Close</button>';
 if(r.send_only_preflight){
  const detail=(name,value)=>'<div class="quote-line" style="gap:12px;overflow-wrap:anywhere"><span>'+h(name)+'</span><strong>'+h(value)+'</strong></div>';
  showModal('TEST ONLY · Estimate 185 send preflight',
   '<div class="callout orange"><strong>Email sending is disabled.</strong> No acceptance, booking, invoice, deposit or payment is authorized.</div>'+
   detail('Company',r.company_name+' · '+r.country+' · '+r.environment)+detail('Realm ID',r.realm_id)+
   detail('QuickBooks Estimate ID',r.external_id)+detail('CrownOps quote',r.quote_id+' · r'+r.revision)+
   detail('Logical service parent',r.service_id)+detail('QuickBooks Customer ID',r.customer_id)+
   '<p style="overflow-wrap:anywhere">Revision history: '+r.revisions.map(q=>h(q.id)+' (r'+h(q.revision)+')').join(' → ')+'</p>'+
   '<div class="panel panel-body"><strong>Immutable synced financial snapshot</strong>'+detail('Subtotal',money(r.subtotal_cents))+
   detail('GST / TPS',money(r.gst_cents))+detail('QST / TVQ',money(r.qst_cents))+detail('Total CAD',money(r.total_cents))+
   '<p class="small" style="overflow-wrap:anywhere">Snapshot: '+h(r.snapshot_hash)+'</p></div>'+
   detail('Fictional customer email',r.customer_email)+detail('Current Estimate BillEmail',r.bill_email)+detail('Proposed sendTo recipient',r.recipient)+
   '<p class="callout orange">'+h(r.send_to_warning)+'</p>'+
   detail('CC / BCC requested','None')+detail('Provider-default CC / BCC',r.cc_bcc.provider_defaults_verified_absent?'Verified absent':'Unknown — sending blocked')+
   detail('Sender',r.sender)+detail('Reply-To',r.reply_to)+
   '<p>'+h(r.message_preview_note)+'</p><div class="message-draft" style="white-space:pre-wrap;overflow-wrap:anywhere">'+h(r.memo)+'</div>'+
   '<details><summary>Saved estimate content</summary>'+(r.lines||[]).map(x=>'<p style="white-space:pre-wrap;overflow-wrap:anywhere">'+h(x.description)+(x.amount_cents?' · '+money(x.amount_cents):'')+'</p>').join('')+'</details>'+
   '<p style="overflow-wrap:anywhere"><strong>Intended external action:</strong> '+h(r.operation)+'</p>'+
   '<p>'+h(r.duplicate_protection)+'</p>'+detail('Existing send attempt',r.send_attempt.state||'None recorded')+
   '<p><strong>Effective gates:</strong> Customer CREATE OFF · Estimate CREATE OFF · Acceptance OFF · Booking OFF · Invoice OFF · Deposit OFF · Payment OFF · Gmail OFF</p>'+
   '<p>Provider email status: '+h(r.provider_email_status||'NotSet')+' · estimate status: '+h(r.provider_status)+'. Neither sent nor Accepted status proves inbox receipt or a customer click.</p>'+
   '<ul>'+r.blockers.map(x=>'<li>'+h(x)+'</li>').join('')+'</ul>',close+'<button disabled>Send disabled — owner approval required</button>');
  return;
 }

 if(r.test_only&&r.state==='Not prepared'){
  showModal('TEST ONLY · create one unsent sandbox estimate',
   '<p><strong>'+h(r.company_name)+' · '+h(r.realm_id)+' · '+h(r.country)+'</strong></p>'+
   '<p>Service parent: '+h(r.service_id)+' · quote '+h(r.quote_id)+' · revision '+h(r.revision)+'</p>'+
   '<p>Revision history: '+r.revisions.map(q=>h(q.id)+' (r'+h(q.revision)+')').join(' → ')+'</p>'+
   '<p>Exact fictional amount: 3 × $100 labour + $150 transportation + $35.99 fuel.</p>'+
   '<p>Subtotal $485.99 · TPS $24.30 · TVQ $48.48 · <strong>CAD $558.77</strong></p>'+
   '<div class="message-draft" style="white-space:pre-wrap">'+h(r.terms.text)+'</div>'+
   '<h3>Proposed QuickBooks writes</h3><ul>'+r.proposed_writes.map(x=>'<li>'+h(x)+'</li>').join('')+'</ul>'+
   (r.requires_customer_create_confirmation?'<label><input type="checkbox" id="test-customer-confirm"> I explicitly confirm the required creation of one fictional sandbox customer shown above.</label>':'<p>Existing QuickBooks customer: '+h(r.customer_id)+'</p>')+
   '<p>No email, acceptance, booking, invoice or payment is enabled. A timeout consumes the attempt until reconciled.</p>',
   close+'<button class="btn-primary" data-qbo-delivery="test-prepare" data-id="'+h(qid)+'" data-review="'+h(r.review_hash)+'" data-customer-required="'+h(r.requires_customer_create_confirmation)+'">Create one unsent sandbox estimate</button>');return;
 }
 if(r.state==='Not prepared'){
  showModal('Send estimate through QuickBooks',
   '<p>QuickBooks will email the client an estimate they can accept. Acceptance returns to CrownOps as a tentative service, ready for your team to schedule and confirm.</p>'+
   '<p>First, prepare the itemized estimate. You will review the recipient, taxes, total and booking terms before any email is sent.</p>'+
   (r.enabled?select('qbo_basis','Amount displayed by QuickBooks',[['high','High end of the estimated range'],['low','Low end of the estimated range']],'high'):
    '<div class="callout orange"><strong>Awaiting connection testing</strong><p>The sandbox email and acceptance walkthrough must be approved before an owner enables this feature.</p></div>')+
   '<p class="small muted">The full range stays visible. Add an expiration date in the quote editor first. An existing accounting-only draft requires review before using this flow.</p>',
   close+(r.enabled?'<button class="btn-primary" data-qbo-delivery="prepare" data-id="'+h(qid)+'">Prepare for review</button>':''));return;
 }
 const lineHtml=(r.lines||[]).map(x=>'<div class="panel panel-body" style="margin:8px 0;overflow-wrap:anywhere"><div style="white-space:pre-wrap">'+h(x.description)+'</div>'+
   (x.quantity!==undefined?'<p class="small">'+h(x.quantity)+' × '+money(Math.round(Number(x.unit_price||0)*100))+'</p>':'')+
   (x.amount_cents?'<strong>'+money(x.amount_cents)+'</strong>':'')+'</div>').join('');
 showModal('QuickBooks estimate · '+h(r.state),
  '<p><strong>To:</strong> '+h(r.recipient||'Not yet confirmed')+'</p>'+
  (r.expiry?'<p><strong>Expires:</strong> '+h(r.expiry)+'</p>':'')+
  (r.error?'<div class="callout orange">'+h(r.error)+'</div>':'')+
  '<p class="small muted">QuickBooks controls the email layout and branding. This is the saved estimate content. Provider “sent” status does not prove inbox delivery.</p>'+
  (r.memo?'<div class="message-draft" style="white-space:pre-wrap">'+h(r.memo)+'</div>':'')+lineHtml+
  (r.total_cents!==undefined?'<div class="panel panel-body"><p>Before tax: <strong>'+money(r.subtotal_cents)+'</strong></p>'+
   (r.taxes||[]).map(t=>'<p>'+h(t.name)+': <strong>'+money(t.amount_cents)+'</strong></p>').join('')+
   '<p>Total tax: '+money(r.tax_cents)+'</p><p style="font-size:1.25rem">Total: <strong>'+money(r.total_cents)+'</strong></p></div>':'')+
  (r.test_only?'<p><strong>TEST ONLY: sending, acceptance and booking remain disabled.</strong> QuickBooks Estimate ID: '+h(r.external_id)+'</p>':'<p>Client acceptance creates a tentative service. Your team still checks the date, deposit, crew and truck before confirming.</p>')+
  (r.state==='Accepted'?'<button data-page="schedule" class="btn-primary">Open schedule</button>':''),
  close+(r.test_only?'':'<button data-qbo-delivery="sync" data-id="'+h(qid)+'">Check acceptance</button>')+
  (!r.test_only&&r.state==='Ready'&&r.provider_status==='Pending'&&r.provider_email_status!=='EmailSent'?'<button class="btn-primary" data-qbo-delivery="send" data-id="'+h(qid)+'" data-review="'+h(r.review_hash)+'" data-recipient="'+h(r.recipient)+'">Send through QuickBooks</button>':''));
};
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-qbo-delivery]');if(!b)return;event.preventDefault();
 if(b.disabled)return;b.disabled=true;
 const qid=b.dataset.id,base='/quotes/'+encodeURIComponent(qid)+'/quickbooks-delivery';
 try{
  if(b.dataset.qboDelivery==='open'){await openQuickBooksEstimate(qid);return;}
  if(b.dataset.qboDelivery==='test-prepare'){
   const customer=document.getElementById('test-customer-confirm')?.checked===true;
   if(b.dataset.customerRequired==='true'&&!customer)throw Error('Review and explicitly confirm the required fictional customer creation first.');
   await api(base+'/prepare',{method:'POST',timeoutMs:95000,data:{confirm:true,review_hash:b.dataset.review,confirm_customer_create:customer}});
   toast('One sandbox estimate created and read back. Not sent.');
  }else if(b.dataset.qboDelivery==='prepare'){
   await api(base+'/prepare',{method:'POST',data:{basis:document.querySelector('[name="qbo_basis"]').value,confirm:true}});
   toast('QuickBooks draft prepared. No email sent.');
  }else if(b.dataset.qboDelivery==='send'){
   if(!confirm('Send this reviewed estimate through QuickBooks to '+b.dataset.recipient+'?'))return;
   const r=await api(base+'/send',{method:'POST',data:{confirm:true,recipient:b.dataset.recipient,review_hash:b.dataset.review}});
   toast(r.duplicate?'This estimate was already sent; no duplicate email requested.':'QuickBooks confirmed sending. Client acceptance will sync back here.');
  }else{
   const r=await api(base+'/sync',{method:'POST',data:{}});toast(r.message);
  }
  await openQuickBooksEstimate(qid);
 }catch(error){toast(error.message);}finally{if(b.isConnected)b.disabled=false;}
});
