(function(){
 const previous=openInvoice;
 openInvoice=async function(iid){
  await previous(iid);const invoice=S.modalContext?.invoice,body=modal.querySelector('.modal-body');
  if(!body||invoice?.id!==iid||invoice.status!=='Issued')return;
  body.insertAdjacentHTML('afterbegin','<section class="panel panel-body"><h3>Send through QuickBooks</h3><p>Review the saved QuickBooks invoice, billing recipient and current balance before sending. Crew can send invoices for their assigned services after the office has synchronized them.</p><button class="btn-primary" data-invoice-qbo="preview" data-id="'+h(iid)+'">Review QuickBooks invoice</button></section><div class="spacer"></div>');
  const legacy=body.querySelector('[data-invoice-delivery]');if(legacy){legacy.textContent='Send with CrownOps email';legacy.className='btn-soft';}
 };
 async function review(iid){
  const r=await api('/invoices/'+encodeURIComponent(iid)+'/quickbooks-email');
  const back='<button data-invoice-qbo="back" data-id="'+h(iid)+'">Back to invoice</button>';
  if(r.state==='Not synchronized'){
   showModal('QuickBooks invoice — synchronization needed','<p>'+h(r.message)+'</p><p>Synchronization creates or updates the accounting invoice. Email is a separate reviewed action.</p>',
    back+(['owner','manager'].includes(S.user.role)?'<button class="btn-primary" data-invoice-qbo="sync" data-id="'+h(iid)+'">Synchronize issued invoice</button>':''));return;
  }
  showModal('QuickBooks invoice · '+h(r.state),'<p><strong>Send to:</strong> '+h(r.recipient)+'</p>'+
   '<p><strong>Invoice total:</strong> '+money(r.total_cents)+'<br><strong>Current balance:</strong> '+money(r.balance_cents)+'</p>'+
   (r.due_date?'<p><strong>Due date:</strong> '+h(r.due_date)+'</p>':'')+
   '<div class="message-draft" style="white-space:pre-wrap">'+h(r.memo)+'</div>'+
   (r.lines||[]).map(l=>'<div class="activity-row"><div style="overflow-wrap:anywhere">'+h(l.description)+'</div><strong>'+money(l.amount_cents)+'</strong></div>').join('')+
   '<p>'+h(r.message)+'</p><p class="small muted">QuickBooks controls the company template and payment options. Email sent is not proof of inbox delivery or payment. Never ask a client to pay again while an existing payment is awaiting reconciliation.</p>',
   back+'<button data-invoice-qbo="preview" data-id="'+h(iid)+'">Refresh delivery status</button>'+
   (r.can_send?'<button class="btn-primary" data-invoice-qbo="send" data-id="'+h(iid)+'" data-review="'+h(r.review_hash)+'" data-recipient="'+h(r.recipient)+'">Send through QuickBooks</button>':''));
 }
 window.openQuickBooksInvoice=review;
 document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-invoice-qbo]');if(!b)return;event.preventDefault();if(b.disabled)return;b.disabled=true;
  try{
   const iid=b.dataset.id;
   if(b.dataset.invoiceQbo==='back'){await openInvoice(iid);return;}
   if(b.dataset.invoiceQbo==='sync'){
    if(!confirm('Synchronize this finalized invoice with QuickBooks? No email or card charge will be requested.'))return;
    const r=await api('/invoices/'+encodeURIComponent(iid)+'/quickbooks',{method:'POST',data:{confirm:true}});
    if(r.amount_matches===false)throw Error('QuickBooks total differs. Management must reconcile it before sending.');
   }
   if(b.dataset.invoiceQbo==='send'){
    if(!confirm('Send this reviewed invoice through QuickBooks to '+b.dataset.recipient+'?'))return;
    const r=await api('/invoices/'+encodeURIComponent(iid)+'/quickbooks-email/send',{method:'POST',data:{confirm:true,recipient:b.dataset.recipient,review_hash:b.dataset.review}});
    toast(r.duplicate?'QuickBooks already reports this invoice sent. No duplicate email requested.':'QuickBooks confirmed sending the invoice.');
   }
   await review(iid);
  }catch(error){toast(error.message);}finally{if(b.isConnected)b.disabled=false;}
 });
})();
