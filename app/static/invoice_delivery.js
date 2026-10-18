/* Invoice review, approved extras, billing recipient and verified payment status. */
'use strict';
(function(){
 const admin=()=>['owner','manager'].includes(S.user?.role), lastOpen=openInvoice;
 let state=null, extras=[];
 openInvoice=async function(id){
  await lastOpen(id);
  const [delivery,items]=await Promise.all([api('/invoices/'+encodeURIComponent(id)+'/delivery-state'),api('/invoices/'+encodeURIComponent(id)+'/extras')]);
  if(S.modalContext?.invoice?.id!==id)return;
  state=delivery;extras=items;const i=S.modalContext.invoice,body=modal.querySelector('.modal-body');if(!body)return;
  const labels={Draft:'Email not sent',Approved:'Approved email needs sending',Sending:'Delivery is being checked',Sent:'Invoice email sent',Unknown:'Delivery needs review',Failed:'Invoice email failed',Cancelled:'Invoice email cancelled','Not sent':'Email not sent'};
  const recipients=delivery.recipients||[delivery.recipient];
  body.insertAdjacentHTML('afterbegin',`<section class="invoice-delivery-state callout ${delivery.status==='Sent'?'teal':''}"><strong>${h(labels[delivery.status]||delivery.status)}</strong>${delivery.sent_at?`<p class="small">${h(uxTimestampLabel(delivery.sent_at))}</p>`:''}${delivery.can_send?`<p class="small">Check the billing recipient, then send the final invoice. The private link opens this invoice only.</p><div class="form-field"><label for="invoice-billing-recipient">Send invoice to</label><select id="invoice-billing-recipient">${recipients.map((r,n)=>`<option value="${h(r)}">${h(r)}${n===0?' · Billing recipient':' · Client'}</option>`).join('')}</select></div><p><button type="button" class="btn-primary" data-invoice-delivery="${h(id)}">Email final invoice</button></p>`:''}${['Unknown','Failed','Sending'].includes(delivery.status)?`<p class="small">Management must reconcile company Sent email before another attempt.</p>${admin()?`<button type="button" data-invoice-resolve="${h(id)}">Review delivery evidence</button>`:''}`:''}${delivery.status==='Approved'&&admin()?`<p><button data-invoice-send-approved="${h(delivery.message_id)}">Send reviewed approved email</button></p>`:''}</section><div class="spacer"></div>`);
  const review=i.deposit_review;
  if(review?.review_required)body.insertAdjacentHTML('beforeend',`<section class="callout orange"><strong>Verified deposit needs review · ${money(review.unallocated_cents)}</strong><p>${review.ambiguous?'This client has more than one service. Management must choose where the deposit belongs.':'A deposit remains above this invoice balance. Management must record how the credit will be handled.'}</p>${admin()?`<button type="button" data-invoice-deposit-review="${h(id)}">Review remaining deposit</button>`:''}</section>`);
  if(i.status==='Draft')body.insertAdjacentHTML('beforeend',`<section class="panel panel-body"><h3>Additional materials / services</h3><p class="small">Request a new item here. Management must approve its price and record the client’s agreement before it can be charged.</p>${extras.map(x=>`<div class="activity-row"><div><strong>${h(x.name)} × ${x.quantity}</strong><p class="small">${h(x.status)} · ${money(x.unit_cents)} each</p>${x.agreement?`<p class="tiny muted">${h(x.agreement)}</p>`:''}</div>${x.status==='Requested'&&admin()?`<button type="button" data-extra-review="${h(x.id)}">Review</button>`:''}</div>`).join('')}<button type="button" data-invoice-extra="${h(id)}">Request additional item</button></section>`);
  if(i.status==='Issued')body.insertAdjacentHTML('beforeend',`<section class="panel panel-body"><h3>Payments</h3><p class="small">Check QuickBooks before requesting another payment. A check reads the provider; it does not charge the client.</p><div class="row wrap"><button type="button" data-payment-state="${h(id)}">Refresh payment status</button><button type="button" data-quickbooks-payment="${h(id)}">Check QuickBooks payment link</button>${admin()?`<button type="button" data-invoice-reconcile="${h(id)}">Reconcile QuickBooks receipts</button><button type="button" data-invoice-match="${h(id)}">Match an existing receipt</button>`:''}</div><div id="invoice-payment-link" role="status"></div></section>`);
 };
 function financeForm(kind,title,body,submitLabel,extra=''){
  const i=S.modalContext.invoice;
  showModal(title,`<form id="invoice-finance-form" data-invoice-finance="${kind}" data-extra-id="${h(extra)}"><input type="hidden" name="invoice_id" value="${h(i.id)}"><input type="hidden" name="version" value="${i.version}"><input type="hidden" name="revision" value="${i.revision}">${body}</form>`,`<button type="button" data-invoice-return="${h(i.id)}">Back to invoice</button><button class="btn-primary" type="submit" form="invoice-finance-form">${h(submitLabel)}</button>`,true);
 }
 async function busy(button,work){if(button.disabled)return;button.disabled=true;try{await work();}catch(ex){modalError(ex);}finally{if(button.isConnected)button.disabled=false;}}
 document.addEventListener('click',async e=>{
  const back=e.target.closest('[data-invoice-return]');if(back){e.preventDefault();return openInvoice(back.dataset.invoiceReturn);}
  const b=e.target.closest('[data-invoice-delivery],[data-quickbooks-payment],[data-payment-state],[data-invoice-reconcile],[data-invoice-match],[data-invoice-resolve],[data-invoice-deposit-review],[data-invoice-extra],[data-extra-review],[data-invoice-send-approved]');if(!b)return;e.preventDefault();
  const i=S.modalContext?.invoice;if(!i)return;
  await busy(b,async()=>{
   const base='/invoices/'+encodeURIComponent(i.id);
   if(b.hasAttribute('data-invoice-delivery')){
    const recipient=document.getElementById('invoice-billing-recipient')?.value||state.recipient;
    if(!confirm('Send invoice '+i.number+' to '+recipient+'? The message includes this invoice and its current balance.'))return;
    const r=await api(base+'/deliver',{method:'POST',data:{confirm:true,recipient,version:i.version,revision:i.revision,include_quickbooks_payment:b.dataset.withPayment==='true'}});toast(r.sent?'Invoice email sent.':'Delivery needs review.');return openInvoice(i.id);
   }
   if(b.hasAttribute('data-invoice-send-approved')){await api('/outbound/'+b.dataset.invoiceSendApproved+'/send',{method:'POST',data:{}});return openInvoice(i.id);}
   if(b.hasAttribute('data-quickbooks-payment')){
    const r=await api(base+'/quickbooks-payment-link');if(i.version!==r.version)throw Error('The invoice changed. Reopen it before sharing a payment link.');
    const target=document.getElementById('invoice-payment-link');target.innerHTML='<p>'+h(r.environment==='sandbox'?'TEST ONLY · QuickBooks sandbox':'Secure QuickBooks payment')+' · '+money(r.amount_cents)+'</p>'+xlink(r.url,'Open QuickBooks payment page')+(state.can_send?`<p><button type="button" class="btn-primary" data-invoice-delivery="${h(i.id)}" data-with-payment="true">Email invoice with payment link</button></p>`:'');return;
   }
   if(b.hasAttribute('data-payment-state')){
    const p=await api(base+'/payment-state'),target=document.getElementById('invoice-payment-link');target.innerHTML=`<p><strong>${h(p.status)}</strong>${p.remote_balance_cents!==null?` · QuickBooks ${money(p.remote_balance_cents)}`:''} · CrownOps ${money(p.local_balance_cents)}</p><p class="small">${h(p.reason||(!p.mapped?'This invoice is not linked to QuickBooks. No remote payment has been checked.':'Current invoice and receipt allocations match.'))}</p>`;return;
   }
   if(b.hasAttribute('data-invoice-reconcile')){
    if(!confirm('Read linked QuickBooks payments and reconcile verified receipt allocations? This does not charge a client or change QuickBooks.'))return;
    const p=await api(base+'/reconcile-payments',{method:'POST',data:{confirmed:true,version:i.version,revision:i.revision}});toast(p.reason||'Payment status reconciled.');return openInvoice(i.id);
   }
   if(b.hasAttribute('data-invoice-match')){
    return financeForm('match','Match a verified receipt',`<p>Use this only when the same receipt is already recorded in CrownOps and QuickBooks. The invoice, client, amount and provider evidence will be checked before anything is matched.</p><div class="grid2">${field('payment_id','CrownOps payment ID','','text','required')}${field('quickbooks_payment_id','QuickBooks Payment ID','','text','required inputmode="numeric"')}</div>${textArea('note','Evidence that these are the same receipt','',3)}<p class="small muted">This reads QuickBooks and links existing records. It does not create a payment or charge the client.</p>`,'Verify and match');
   }
   if(b.hasAttribute('data-invoice-resolve')){
    return financeForm('resolve','Review invoice email delivery',`<p>Check company Sent email and the provider for this exact invoice and recipient. CrownOps will also search the original message reference.</p>${textArea('note','What did you check? Include the recipient, time and provider reference.','',4)}<label class="checkbox-label"><input type="checkbox" name="confirmed_not_sent">I verified this email was not delivered. If no sent copy is found, permit a reviewed retry after the waiting period.</label><p class="small muted">An existing sent copy will be marked Sent. This step never sends another email. An unconfirmed retry requires at least 30 minutes since the attempt.</p>`,'Check delivery evidence');
   }
   if(b.hasAttribute('data-invoice-deposit-review')){
    return financeForm('deposit','Review remaining deposit',`<p>${money(i.deposit_review.unallocated_cents)} remains unallocated. Use Apply verified deposit on the invoice when this service should receive it.</p>${textArea('reason','Where does the remaining deposit belong, or how will the client’s credit be handled?','',4)}<p class="small muted">This review records your decision. It does not refund money or allocate a payment.</p>`,'Save management review');
   }
   if(b.hasAttribute('data-invoice-extra')){
    return financeForm('extra','Request an additional item',`<p>The item is added to a management review queue. It will be charged only after its price and the client’s agreement are approved.</p>${field('name','Material / service name','','text','required maxlength="160"')}<div class="grid2">${field('quantity','Quantity used',1,'number','required min="1" max="10000" step="1"')}${field('unit_price','Proposed price per item',0,'number','required min="0" max="10000" step="0.01"')}</div>${textArea('reason','Why is it needed?','',3)}<input type="hidden" name="request_id" value="${crypto.randomUUID()}">`,'Request management approval');
   }
   if(b.hasAttribute('data-extra-review')){
    const extra=extras.find(x=>x.id===b.dataset.extraReview);if(!extra)return;
    return financeForm('review-extra','Review '+extra.name,`<p><strong>${h(extra.name)} × ${extra.quantity}</strong></p><p>${h(extra.reason)}</p><div class="grid2">${select('decision','Decision',[['','Choose a decision'],['Approve','Approve agreed charge'],['Decline','Decline request']],'','required')}${field('unit_price','Client-agreed price per item',extra.unit_cents/100,'number','required min="0" max="10000" step="0.01"')}</div>${textArea('agreement','Client agreement or reason for declining','',4)}<label class="checkbox-label"><input type="checkbox" name="client_agreed">The client agreed to this exact item, quantity and price. Required when approving.</label>`,'Save decision',extra.id);
   }
  });
 });
 document.addEventListener('submit',async e=>{
  const f=e.target.closest('form[data-invoice-finance]');if(!f)return;e.preventDefault();
  const button=e.submitter||document.querySelector('[form="invoice-finance-form"]');
  await busy(button,async()=>{
   const d=Object.fromEntries(new FormData(f)),base='/invoices/'+encodeURIComponent(d.invoice_id),kind=f.dataset.invoiceFinance;
   const common={version:Number(d.version),revision:Number(d.revision),confirmed:true};
   if(kind==='extra')await api(base+'/extras',{method:'POST',data:{version:common.version,request_id:d.request_id,name:d.name,quantity:d.quantity,unit_price:d.unit_price,reason:d.reason}});
   if(kind==='review-extra')await api(base+'/extras/'+f.dataset.extraId+'/review',{method:'POST',data:{version:common.version,decision:d.decision,unit_price:d.unit_price,agreement:d.agreement,client_agreed:f.elements.client_agreed.checked}});
   if(kind==='deposit')await api(base+'/deposit-review',{method:'POST',data:{version:common.version,reason:d.reason}});
   if(kind==='match')await api(base+'/match-quickbooks-receipt',{method:'POST',data:{...common,payment_id:d.payment_id,quickbooks_payment_id:d.quickbooks_payment_id,note:d.note}});
   if(kind==='resolve')await api(base+'/resolve-delivery',{method:'POST',data:{...common,message_id:state.message_id,note:d.note,confirmed_not_sent:f.elements.confirmed_not_sent.checked}});
   toast(kind==='extra'?'Item requested. Management will review it before charging.':'Review saved.');return openInvoice(d.invoice_id);
  });
 });
})();
