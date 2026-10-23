/* One provider sends the estimate; acceptance is separate from booking confirmation. */
window.openQuickBooksEstimate = async function(qid){
 const base='/quotes/'+encodeURIComponent(qid)+'/quickbooks-delivery';
 const r=await api(base);
 const close='<button data-action="close">Close</button>';
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
  '<p>Client acceptance creates a tentative service. Your team still checks the date, deposit, crew and truck before confirming.</p>'+
  (r.state==='Accepted'?'<button data-page="schedule" class="btn-primary">Open schedule</button>':''),
  close+'<button data-qbo-delivery="sync" data-id="'+h(qid)+'">Check acceptance</button>'+
  (r.state==='Ready'&&r.provider_status==='Pending'&&r.provider_email_status!=='EmailSent'?'<button class="btn-primary" data-qbo-delivery="send" data-id="'+h(qid)+'" data-review="'+h(r.review_hash)+'" data-recipient="'+h(r.recipient)+'">Send through QuickBooks</button>':''));
};
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-qbo-delivery]');if(!b)return;event.preventDefault();
 if(b.disabled)return;b.disabled=true;
 const qid=b.dataset.id,base='/quotes/'+encodeURIComponent(qid)+'/quickbooks-delivery';
 try{
  if(b.dataset.qboDelivery==='open'){await openQuickBooksEstimate(qid);return;}
  if(b.dataset.qboDelivery==='prepare'){
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
