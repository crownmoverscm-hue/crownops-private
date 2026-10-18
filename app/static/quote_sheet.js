/* Guided and document-style editing share one draft and one material list. */
'use strict';
const QS={rows:[],catalogue:[],view:'guided',canManage:false};
const qsBaseBuilder=crmQuoteBuilder,qsBaseData=crmQuoteData,qsBaseOpen=crmOpenQuote;
const qsField=(name,label,value='',type='text',attrs='')=>field(name,label,value,type,attrs);
function qsRow(raw={}){return {code:raw.code||('CUSTOM_'+crypto.randomUUID()),name:raw.name||'',qty:raw.qty??1,unit_price:raw.unit_price??Number(raw.unit_cents||0)/100,note:raw.note||'',price_override:true};}
function qsReadRows(){
 document.querySelectorAll('[data-qs-row]').forEach(el=>{const row=QS.rows.find(r=>r.code===el.dataset.qsRow);if(!row)return;for(const k of ['name','qty','unit_price','note'])row[k]=el.querySelector('[data-mat="'+k+'"]').value;row.qty=Number(row.qty);row.unit_price=Number(row.unit_price);});
 return QS.rows.map(r=>({...r}));
}
function qsRenderRows(){
 const box=document.getElementById('qs-material-rows');if(!box)return;
 box.innerHTML=QS.rows.map((r,i)=>'<article class="qs-material-row" data-qs-row="'+h(r.code)+'"><div class="qs-material-description"><label for="qs-name-'+i+'">Material / extra</label><input id="qs-name-'+i+'" data-mat="name" value="'+h(r.name)+'" maxlength="160" required placeholder="e.g. Mattress bag"><textarea aria-label="Material description '+(i+1)+'" data-mat="note" rows="2" placeholder="Description / agreed details">'+h(r.note)+'</textarea></div><div><label for="qs-price-'+i+'">Unit price · CAD</label><input id="qs-price-'+i+'" data-mat="unit_price" type="number" min="0" max="10000" step="0.01" value="'+h(r.unit_price)+'" required></div><div><label for="qs-qty-'+i+'">Quantity</label><input id="qs-qty-'+i+'" data-mat="qty" type="number" min="0" max="10000" step="1" value="'+h(r.qty)+'" required></div><div><span class="info-label">Line total</span><strong data-mat-total>'+crmCash(Number(r.unit_price)*Number(r.qty)*100)+'</strong></div><div class="qs-row-actions"><button type="button" data-qs="remove" data-code="'+h(r.code)+'" aria-label="Remove material '+(i+1)+'">Remove</button>'+(QS.canManage&&!QS.catalogue.some(x=>x.code===r.code)?'<button type="button" data-qs="catalogue-save" data-code="'+h(r.code)+'">Save to material list</button>':'')+'</div></article>').join('')||'<p class="muted">No materials added yet. Choose an item from your list or add a custom material.</p>';
}
function qsTransport(){
 const f=document.getElementById('crm-quote-form');if(!f)return;
 const type=f.elements.service_type.value,local=type!=='Long Distance',mode=f.elements.transport_mode.value;
 f.elements.transport_mode.value=local?(mode==='long_distance'?'local_amount':mode):'long_distance';
 const flat=local&&f.elements.transport_mode.value==='local_amount';
 f.elements.fixed_transport.required=flat;
 f.elements.fixed_transport.placeholder=local?'35.00':'Optional agreed fixed amount';
 f.elements.fixed_transport.closest('.form-field').querySelector('label').textContent=local?'Local transportation fee · CAD':'OR fixed long-distance transportation fee · CAD';
 document.getElementById('qs-local-fuel').hidden=!local;
 ['distance_preset','distance_km','distance_rate'].forEach(k=>{f.elements[k].closest('.form-field').hidden=local;});
 f.elements.transport_hours.closest('.form-field').querySelector('label').textContent=local?'Travel hours reserved on the schedule':'True driving / transportation hours';
 document.getElementById('qs-transport-help').textContent=local?'Enter the agreed local transportation amount. Fuel is shown separately below; set it to 0 if included. Travel hours reserve enough time on the schedule.':'Long-distance driving, distance and truck rules remain in effect. A fixed amount is optional.';
 qsSyncDocument();
}
function qsSyncDocument(){
 const f=document.getElementById('crm-quote-form');if(!f)return;
 f.querySelectorAll('[data-qs-bind]').forEach(x=>{const source=f.elements.namedItem(x.dataset.qsBind);if(source&&document.activeElement!==x)x.value=source.value;x.placeholder=source?.placeholder||'';});
 const name=f.elements.service_line_name;if(name&&!name.dataset.edited)name.value=f.elements.movers.value+' movers + '+f.elements.trucks.value+' truck(s)';
}
function qsView(view){
 const f=document.getElementById('crm-quote-form');if(!f)return;QS.view=view;f.classList.toggle('qs-document-mode',view==='document');
 document.getElementById('qs-document').hidden=view!=='document';
 f.querySelectorAll('[data-qs-bind]').forEach(x=>{const source=f.elements.namedItem(x.dataset.qsBind);x.disabled=view!=='document'||Boolean(source?.disabled);if(!source)return;x.min=source.min||(['movers','trucks'].includes(x.dataset.qsBind)?(x.dataset.qsBind==='movers'?'2':'1'):'0');x.max=source.max||(source.options?String(Math.max(...[...source.options].map(o=>Number(o.value)))):'100000');x.step=source.step||(['movers','trucks'].includes(x.dataset.qsBind)?'1':'0.25');x.required=view==='document'&&!source.disabled&&(source.required||source.dataset.qsRequired==='1');});
 f.querySelectorAll('#crm-moving-fields input[required],#crm-moving-fields select[required]').forEach(x=>{x.dataset.qsRequired='1';});
 f.querySelectorAll('#crm-moving-fields [data-qs-required]').forEach(x=>x.required=view!=='document');
 f.querySelectorAll('[data-qs-view]').forEach(x=>{x.classList.toggle('selected',x.dataset.qsView===view);x.setAttribute('aria-pressed',String(x.dataset.qsView===view));});
 qsSyncDocument();
}
crmQuoteBuilder=async function(lid='',options={}){
 await qsBaseBuilder(lid,options);const f=document.getElementById('crm-quote-form');if(!f)return;
 const seed=options.seed||{},setup=CRM.quoteSetup||{},lead=CRM.currentLead||{},bill=seed.billing||{};
 // The intake wrapper may have loaded a prepared draft. Preserve its seeded values.
 QS.catalogue=setup.materials||[];QS.canManage=Boolean(setup.can_manage_materials);QS.lastType=f.elements.service_type.value;QS.rows=(seed.materials||[]).map(qsRow);
 f.insertAdjacentHTML('afterbegin','<div class="qs-view-switch segmented" aria-label="Quote editor view"><button type="button" data-qs-view="guided">Guided view</button><button type="button" data-qs-view="document">Line-item view</button></div><section class="panel panel-body qs-billing"><h3>Billing information</h3><div class="grid2">'+qsField('billing_name','Billing contact',bill.name||lead.client_name||'')+qsField('billing_company','Company (optional)',bill.company||'')+qsField('billing_email','Billing email (on documents)',bill.email||lead.email||'','email')+qsField('billing_phone','Billing phone',bill.phone||lead.phone||'','tel')+textArea('billing_address','Billing address',bill.address||'',3)+qsField('expiry_date','Quote expiration (optional)',seed.expiry_date||'','date')+qsField('payment_terms','Payment terms',seed.payment_terms||'')+'</div><p class="tiny muted">Quote delivery uses the saved client email. Billing details are copied onto the accepted service invoice.</p></section>');
 const transport=document.getElementById('crm-transport-fields');transport.querySelector('p').id='qs-transport-help';
 transport.querySelector('.grid2').insertAdjacentHTML('afterbegin',select('transport_mode','Transportation pricing',[['local_amount','Local — enter amount'],['long_distance','Long distance'],['hourly_base','Local — hourly base travel (existing pricing)']],seed.transport_mode||(f.elements.service_type.value==='Long Distance'?'long_distance':seed.transport_hours!==undefined&&seed.fixed_transport==null?'hourly_base':'local_amount'))+'<div id="qs-local-fuel">'+qsField('local_fuel_rate','Separate local fuel fee / truck · CAD',seed.local_fuel_rate??35,'number','min="0" max="10000" step="0.01"')+'</div>');
 if(!options.seed&&lid){const prepared=await api('/intake/drafts/'+encodeURIComponent(lid)+'/prepare',{method:'POST',data:{}});if(prepared.quote_id){const detail=await api('/crm/quotes/'+encodeURIComponent(prepared.quote_id));if(detail.quote.status==='Draft'&&detail.quote.lead_version===detail.lead.version){QS.rows=(detail.quote.inputs.materials||[]).map(qsRow);}}}
 const oldList=f.querySelector('.crm-material-list'),matPanel=oldList.closest('.panel');matPanel.id='qs-materials';
 matPanel.querySelector('p').textContent='Add items from your reusable list or enter custom materials. Prices and quantities are editable on this quote. Use quantity 0 for an optional item.';
 oldList.outerHTML='<div class="qs-add-material"><label for="qs-catalogue">Your material list</label><select id="qs-catalogue"><option value="">Choose a material…</option>'+QS.catalogue.map(x=>'<option value="'+h(x.code)+'">'+h(x.name)+' · '+crmCash(x.sell_cents)+'</option>').join('')+'</select><button type="button" data-qs="catalogue-add">Add selected item</button><button type="button" data-qs="custom-add" class="btn-soft">+ Add material / extra</button></div><div id="qs-material-rows"></div><p class="tiny muted">'+(QS.catalogue.length<=1?'Only '+QS.catalogue.length+' active item is currently in your list. Add custom materials now; Owners and Managers can save reusable items here.':'Your list has '+QS.catalogue.length+' active materials. Owners and Managers can maintain it in Materials.')+'</p>';
 const mirror=(key,label,type='number')=>'<label>'+label+'<input data-qs-bind="'+key+'" aria-label="Line item '+label+'" type="'+type+'" '+(type==='number'?'min="0" step="0.25"':'')+'></label>';
 const desc=seed.service_line_name||'';f.querySelector('.quote-layout').insertAdjacentHTML('beforebegin','<section id="qs-document" class="panel panel-body" hidden><h3>Quote details</h3><div class="qs-document-meta">'+mirror('movers','Movers')+mirror('trucks','Trucks')+mirror('minimum_hours','Minimum service hours')+'</div><div class="qs-sheet-row"><div>'+qsField('service_line_name','Product or service',desc)+textArea('service_line_note','Description / service note',seed.service_line_note||'',3)+'</div>'+mirror('hourly_rate','Hourly price · CAD')+'<div>'+mirror('hours_low','Estimated hours — low')+mirror('hours_high','Estimated hours — high')+'</div></div><div class="qs-sheet-row"><strong>Transportation fee</strong>'+mirror('fixed_transport','Fixed transport · CAD')+'<p class="small muted">Enter the agreed local amount, or use the route fields below for long distance.</p></div><p class="small muted">Totals remain an estimate range. Taxes follow the approved billing setup on the final invoice.</p></section>');
 if(desc&&desc!==seed.movers+' movers + '+seed.trucks+' truck(s)')f.elements.service_line_name.dataset.edited='true';
 f.querySelector('.qe-quote-info').insertAdjacentHTML('beforeend',qsField('media_link','Photo / video folder link (Drive, Dropbox, etc.)','','url','placeholder="https://drive.google.com/…"')+'<p class="tiny muted">A saved link is visible in this moving file to its assigned crew. Make sure the folder is shared with the team.</p>');
 document.getElementById('qs-document').before(f.elements.service_type.closest('.panel'));
 for(const key of ['name','company','email','phone','address'])if(seed.billing&&key in seed.billing)f.elements.namedItem('billing_'+key).dataset.edited='true';
 f.querySelector('.qe-client').after(f.querySelector('.qs-billing'));
 qeUpdateClientCard();qsRenderRows();qsView(options.editorView||seed.editor_view||'guided');qsTransport();
};
crmQuoteData=function(){
 const data=qsBaseData();if(!data)return null;
 data.materials=qsReadRows();data.editor_view=QS.view;data.billing={};
 for(const k of ['name','company','email','phone','address']){data.billing[k]=data['billing_'+k]||'';delete data['billing_'+k];}
 data.local_fuel_rate=Number(data.local_fuel_rate??35);
 if(data.transport_mode==='local_amount'&&data.fixed_transport===null)throw Error('Enter the local transportation fee. The $35.00 text is a placeholder.');
 return data;
};
crmOpenQuote=async function(id){
 await qsBaseOpen(id);const q=CRM.currentQuote?.quote,bill=q?.inputs?.billing;
 if(bill)modal.querySelector('.modal-body')?.insertAdjacentHTML('beforeend','<section class="panel panel-body"><h3>Bill to</h3><strong>'+h(bill.company||bill.name)+'</strong><p>'+h(bill.address)+'</p><p>'+h(bill.email)+'<br>'+h(bill.phone)+'</p>'+(q.inputs.payment_terms?'<p>Payment terms: '+h(q.inputs.payment_terms)+'</p>':'')+'</section>');
 if(q&&may(['owner','sales']))modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin','<button type="button" class="btn-soft" data-qs="edit-document" data-id="'+h(id)+'">'+(q.status==='Draft'?'Edit line items':'Create revised quote')+'</button>');
};
document.addEventListener('click',async e=>{
 const view=e.target.closest('[data-qs-view]');if(view){e.preventDefault();qsReadRows();qsView(view.dataset.qsView);return;}
 const b=e.target.closest('[data-qs]');if(!b)return;e.preventDefault();
 try{
  if(b.dataset.qs==='edit-document'){const d=await api('/crm/quotes/'+encodeURIComponent(b.dataset.id));return crmQuoteBuilder(d.lead.id,{seed:d.quote.inputs,result:d.quote.result,templateKey:d.quote.inputs.template_key,editorView:'document'});}
  qsReadRows();
  if(b.dataset.qs==='custom-add'){QS.rows.push(qsRow());qsRenderRows();document.querySelector('[data-qs-row]:last-child input').focus();}
  if(b.dataset.qs==='remove'){QS.rows=QS.rows.filter(r=>r.code!==b.dataset.code);qsRenderRows();}
  if(b.dataset.qs==='catalogue-add'){
   const item=QS.catalogue.find(x=>x.code===document.getElementById('qs-catalogue').value);if(!item)throw Error('Choose an item from your material list.');
   const existing=QS.rows.find(x=>x.code===item.code);if(existing)existing.qty=Number(existing.qty)+1;else QS.rows.push(qsRow({code:item.code,name:item.name,unit_price:item.sell_cents/100}));qsRenderRows();
  }
  if(b.dataset.qs==='catalogue-save'){
   const row=QS.rows.find(x=>x.code===b.dataset.code);if(!row?.name.trim()||!Number.isFinite(row.unit_price)||row.unit_price<0)throw Error('Enter the material name and a valid price first.');
   b.disabled=true;await api('/materials',{method:'POST',data:{code:row.code,name:row.name,sell_price:row.unit_price,category:'Moving material',taxable:true}});
   QS.catalogue.push({code:row.code,name:row.name,sell_cents:Math.round(row.unit_price*100)});document.getElementById('qs-catalogue').add(new Option(row.name+' · '+crmCash(row.unit_price*100),row.code));qsRenderRows();toast('Saved to the reusable material list.');
  }
 }catch(ex){modalError(ex);if(b.isConnected)b.disabled=false;}
});
document.addEventListener('input',e=>{
 const f=e.target.closest('#crm-quote-form');if(!f)return;
 if(e.target.dataset.qsBind){const x=f.elements.namedItem(e.target.dataset.qsBind);x.value=e.target.value;}
 if(e.target.name==='service_line_name')e.target.dataset.edited='true';
 const row=e.target.closest('[data-qs-row]');if(row){qsReadRows();const r=QS.rows.find(x=>x.code===row.dataset.qsRow);row.querySelector('[data-mat-total]').textContent=crmCash(r.qty*r.unit_price*100);}
 if(e.target.name?.startsWith('client_')){for(const [source,target] of [['client_name','billing_name'],['client_email','billing_email'],['client_phone','billing_phone']])if(e.target.name===source&&!f.elements[target].dataset.edited)f.elements[target].value=e.target.value;}
 if(e.target.name?.startsWith('billing_'))e.target.dataset.edited='true';
 qsSyncDocument();
});
document.addEventListener('change',e=>{
 if(!e.target.closest('#crm-quote-form'))return;
 const f=document.getElementById('crm-quote-form');
 if(e.target.name==='transport_mode'){
  if(e.target.value==='long_distance'&&f.elements.service_type.value!=='Long Distance'){f.elements.service_type.value='Long Distance';f.elements.service_type.dispatchEvent(new Event('change',{bubbles:true}));}
  else if(e.target.value!=='long_distance'&&f.elements.service_type.value==='Long Distance'){f.elements.service_type.value='Moving';f.elements.service_type.dispatchEvent(new Event('change',{bubbles:true}));}
 }
 if(e.target.name==='service_type'&&QS.lastType!==f.elements.service_type.value){f.elements.fixed_transport.value='';QS.lastType=f.elements.service_type.value;}
 if(e.target.dataset.qsBind)f.elements.namedItem(e.target.dataset.qsBind).dispatchEvent(new Event('change',{bubbles:true}));
 if(['transport_mode','service_type'].includes(e.target.name)){qsView(QS.view);qsTransport();}
 qsSyncDocument();
});

let qsTimer,qsCalculation=0;
document.addEventListener('input',e=>{const f=e.target.closest('#crm-quote-form');if(!f)return;const sequence=++qsCalculation;clearTimeout(qsTimer);qsTimer=setTimeout(async()=>{if(document.getElementById('crm-quote-form')!==f||!f.checkValidity())return;try{const d=crmQuoteData();if(!d)return;const r=await api('/crm/quotes/calculate',{method:'POST',data:d});if(sequence===qsCalculation&&document.getElementById('crm-quote-form')===f)crmRenderCalc(r);}catch{}},350);});

const qsOldRefresh=crmRefreshRate;
crmRefreshRate=async function(){await qsOldRefresh();qsSyncDocument();};
const qsOldClientCard=qeUpdateClientCard;
qeUpdateClientCard=function(){qsOldClientCard();const f=document.getElementById('crm-quote-form');if(!f)return;const client=QE.client||{client_name:f.elements.client_name?.value||'',email:f.elements.client_email?.value||'',phone:f.elements.client_phone?.value||''};for(const [key,value] of [['billing_name',client.client_name],['billing_email',client.email],['billing_phone',client.phone]]){const input=f.elements.namedItem(key);if(input&&!input.dataset.edited&&document.activeElement!==input&&value)input.value=value;}};
