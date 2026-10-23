/* Company account verification and capability switches are distinct checks. */
(function(){
 const previous=X.pageRenderers.connections;
 X.pageRenderers.connections=async function(){
  const original=await previous(),data=X.cache.connections||await api('/connections'),q=data.quickbooks||{};
  const available=flag=>flag?'Enabled · account walkthrough required':'Not enabled';
  const actions='<button class="btn-primary" data-qbo-check>Check QuickBooks connection</button>'+
   (S.user.role==='owner'&&!q.authorized_at?'<button data-v4="connect-quickbooks" data-id="quickbooks">Connect QuickBooks</button>':'')+
   (S.user.role==='owner'&&q.authorized_at&&data.connection_health?.quickbooks?.state==='reconnect_required'?'<button data-v4="connect-quickbooks" data-id="quickbooks">Restore QuickBooks permission</button>':'')+
   (['owner','manager'].includes(S.user.role)?'<button data-ux="qbo-setup">Review tax / item setup</button>':'');
  const card=panel('QuickBooks — company connection',
   '<div class="row wrap">'+pill(q.state||'Not checked')+pill(q.environment==='production'?'LIVE ACCOUNT':'TEST ACCOUNT')+'</div>'+
   '<div class="spacer"></div><div class="info-grid"><div><div class="info-label">Company</div><div class="strong">'+h(q.company_name||'Run the account check to confirm')+'</div></div>'+
   '<div><div class="info-label">Last account check</div><div class="strong">'+h(q.checked_at?uxTimestampLabel(q.checked_at):'Not checked')+'</div></div>'+
   '<div><div class="info-label">Accounting / tax setup</div><div class="strong">'+(q.mappings_complete?'Mappings reviewed':'Review needed')+'</div></div>'+
   '<div><div class="info-label">Quote email and acceptance</div><div class="strong">'+available(q.estimate_email_enabled&&q.writes_enabled)+'</div></div>'+
   '<div><div class="info-label">Invoice export</div><div class="strong">'+available(q.writes_enabled&&q.mappings_complete)+'</div></div>'+
   '<div><div class="info-label">Invoice email through QuickBooks</div><div class="strong">'+available(q.invoice_email_enabled&&q.writes_enabled)+'</div></div>'+
   '<div><div class="info-label">Automatic invoice payment updates</div><div class="strong">'+(q.receipt_sync_enabled?'Enabled · periodic checks':'Not enabled')+'</div>'+(q.receipt_sync_checked_at?'<p class="small">Last check: '+h(uxTimestampLabel(q.receipt_sync_checked_at))+'</p>':'')+'</div></div>'+
   (q.check_error?'<div class="callout orange">'+h(q.check_error)+'</div>':'')+
   '<p>The account check reads company details. It sends no email and creates no invoice or payment. Each invoice’s payment link and balance are checked separately before collection.</p>'+
   '<p class="small muted">Saved authorization and automatic renewal do not certify the full quote → acceptance → invoice → payment journey. Only an owner can connect or disconnect the company account.</p>'+
   '<div class="row wrap">'+actions+'</div>');
  const container=document.createElement('div');container.innerHTML=original;
  for(const title of container.querySelectorAll('h2')){
   if(title.textContent.trim()!=='QuickBooks')continue;
   const old=title.closest('.panel');if(!old)continue;
   const advanced=document.createElement('details');advanced.innerHTML='<summary>Advanced QuickBooks connection controls</summary>';
   old.replaceWith(advanced);advanced.appendChild(old);
  }
  const heading=container.querySelector('.page-head');
  if(heading)heading.insertAdjacentHTML('afterend',card+'<div class="spacer"></div>');else container.insertAdjacentHTML('afterbegin',card);
  return container.innerHTML;
 };
 document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-qbo-check]');if(!b)return;if(b.disabled)return;b.disabled=true;
  try{const r=await api('/quickbooks/test-connection');toast(r.ok?'Account verified: '+r.company_name:r.message);await navigate('connections');}
  catch(error){toast(error.message);}finally{if(b.isConnected)b.disabled=false;}
 });
})();
