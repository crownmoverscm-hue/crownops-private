/* CrownOps v5.7 launch sales workflow. Loaded last so it simplifies earlier review-build screens. */
'use strict';
const SL={templates:null,clients:[],client:null,calc:null,quote:null};
const slOldChooseLead=chooseLead;
const slOldOpenQuote=openQuote;

function slCash(c){return 'CA$'+(Number(c||0)/100).toLocaleString('en-CA',{minimumFractionDigits:2,maximumFractionDigits:2});}
function slNum(name,def){const el=document.querySelector('#sl-quote-form [name="'+name+'"]');return el?Number(el.value||def||0):Number(def||0);}
function slVal(name,def){const el=document.querySelector('#sl-quote-form [name="'+name+'"]');return el?el.value:(def||'');}
function slOptions(items,value){return items.map(function(x){const a=Array.isArray(x)?x:[x,x];return '<option value="'+h(a[0])+'" '+(String(a[0])===String(value)?'selected':'')+'>'+h(a[1])+'</option>';}).join('');}
function slField(name,label,value,type,attrs){return '<div class="form-field"><label>'+label+'</label><input name="'+name+'" type="'+(type||'text')+'" value="'+h(value==null?'':value)+'" '+(attrs||'')+'></div>';}
function slSelect(name,label,items,value,attrs){return '<div class="form-field"><label>'+label+'</label><select name="'+name+'" '+(attrs||'')+'>'+slOptions(items,value)+'</select></div>';}
function slArea(name,label,value,rows){return '<div class="form-field full"><label>'+label+'</label><textarea name="'+name+'" rows="'+(rows||3)+'">'+h(value||'')+'</textarea></div>';}

async function slLoadTemplates(){if(!SL.templates)SL.templates=await api('/launch/templates');return SL.templates;}

function slClientRow(c){
 const s=c.latest_service;
 return '<div class="sl-client-result"><div class="grow"><div class="client-name">'+h(c.client_name)+'</div><div class="small">'+h(c.email||'No email')+' · '+h(c.phone||'No phone')+'</div><div class="tiny muted">'+(c.service_count||0)+' service'+((c.service_count||0)===1?'':'s')+(s?' · Latest: '+h(s.move_date||'date needed')+' · '+h(s.stage):'')+'</div></div>'+
 (s?'<button class="btn-soft" data-sl="use-service" data-lead="'+h(s.id)+'">Use current service</button>':'')+
 '<button class="btn-primary" data-sl="new-service" data-client="'+h(c.id)+'">New service</button></div>';
}
async function slChooseClient(q){
 const term=q==null?'':q;SL.clients=await api('/launch/clients?q='+encodeURIComponent(term));
 const body='<div class="callout teal"><strong>Find the client once.</strong>Search by name, email, phone, pickup or destination. Returning clients keep their older moves underneath the new one.</div><div class="spacer"></div>'+
 '<div class="sl-client-search"><div class="searchbox">'+icon('search')+'<input id="sl-client-search" value="'+h(term)+'" placeholder="Search name, email, phone or address..." autofocus></div><button class="btn-soft" data-sl="client-search">Search</button><button class="btn-primary" data-sl="add-client">'+icon('plus')+' New client</button></div>'+
 '<div class="spacer"></div><div class="sl-client-results">'+(SL.clients.length?SL.clients.map(slClientRow).join(''):empty('No matching client','Add a new client and start their first service.',''))+'</div>';
 showModal('Choose a client for an estimate',body,btn('close','Cancel'),true);
}
chooseLead=async function(action){if(action==='quote')return slChooseClient('');return slOldChooseLead(action);};

async function slNewClient(){
 showModal('Add a new client','<form id="sl-client-form" data-sl-form="client"><div class="grid2">'+
 slField('client_name','Client name','','text','required')+slField('email','Email','','email','')+slField('phone','Phone','','tel','')+
 slSelect('language','Preferred language',['English','French'],'English')+
 '</div></form>',btn('close','Cancel')+'<button type="submit" form="sl-client-form" class="btn-primary">Save & create service</button>');
}
async function slNewService(cid){
 const c=SL.clients.find(function(x){return x.id===cid;})||((await api('/launch/clients/'+encodeURIComponent(cid))).client);
 SL.client=c;
 showModal('New service for '+h(c.client_name),'<form id="sl-service-form" data-sl-form="service"><input type="hidden" name="client_id" value="'+h(cid)+'"><div class="grid2">'+
 slSelect('service_kind','Service type',['Moving - Local','Moving - Long Distance','Packing','Junk Removal'],'Moving - Local')+
 slField('move_date','Service date','','date','')+
 slField('pickup','Pickup address','','text','')+slField('dropoff','Destination address','','text','')+
 slField('home_size','Move size / home size','','text','')+slField('boxes','Boxes / range','','text','')+
 slArea('inventory','Furniture / inventory','',5)+slArea('notes','Internal notes','',3)+
 '</div></form>',btn('close','Cancel')+'<button type="submit" form="sl-service-form" class="btn-primary">Continue to estimate</button>',true);
}

function slTemplateFor(kind,language){
 const fr=String(language||'').toLowerCase().indexOf('fr')===0;
 if(kind==='Moving - Long Distance')return fr?'long-fr':'long-en';
 if(kind==='Packing')return fr?'packing-fr':'packing-en';
 if(kind==='Junk Removal')return fr?'junk-fr':'junk-en';
 return fr?'local-fr':'local-en';
}
function slResult(r){
 if(!r)return '<div class="quote-summary"><div class="eyebrow">ESTIMATE PREVIEW</div><div class="quote-price">Choose the service details</div><p class="small muted">CrownOps will calculate from the saved date/crew rate. Every important amount remains editable.</p></div>';
 let lines=(r.line_items||[]).map(function(x){return '<div class="quote-line"><span>'+h(x.name)+(Number(x.qty)!==1?' · '+h(x.qty):'')+'</span><b>'+slCash(x.total_cents)+'</b></div>';}).join('');
 return '<div class="quote-summary"><div class="eyebrow">'+h(r.service_kind||'ESTIMATE')+' · CAD</div><div class="quote-price">'+slCash(r.low_cents)+(r.high_cents!==r.low_cents?' – '+slCash(r.high_cents):'')+'</div><div class="tiny muted">Before applicable taxes</div><div class="spacer"></div>'+lines+
 '<div class="spacer"></div><div class="tiny muted">'+(r.hourly_rate_cents?String(r.movers)+' movers at '+slCash(r.hourly_rate_cents)+'/h<br>':'')+h(r.liability_note||'')+'</div></div>';
}
async function slQuoteBuilder(lid){
 const data=await Promise.all([api('/leads/'+encodeURIComponent(lid)),slLoadTemplates(),api('/pricing')]);
 const l=data[0],t=data[1],prices=data[2];SL.calc=null;SL.quote={lead:l};
 const kind=(l.lead_type&&['Moving - Local','Moving - Long Distance','Packing','Junk Removal'].includes(l.lead_type))?l.lead_type:'Moving - Local';
 const template=slTemplateFor(kind,l.language);
 const plastic=(t.defaults.plastic_unit_cents||0)/100,bubble=(t.defaults.bubble_unit_cents||0)/100;
 const p=prices[0]||{};
 const body='<div class="row"><div class="avatar">'+h(initials(l.client_name))+'</div><div><div class="strong">'+h(l.client_name)+'</div><div class="tiny muted">'+h(l.email||'Email needed')+' · '+h(l.phone||'Phone needed')+'</div></div><div class="grow"></div><button class="btn-small" data-action="edit-lead" data-id="'+h(lid)+'">Edit client details</button></div><div class="spacer"></div>'+
 '<form id="sl-quote-form" data-sl-form="quote"><input type="hidden" name="lead_id" value="'+h(lid)+'"><input type="hidden" name="lead_version" value="'+h(l.version)+'"><input type="hidden" name="pricing_id" value="'+h(p.id||'')+'">'+
 '<div class="quote-layout"><div class="stack">'+
 '<div class="panel panel-body"><div class="eyebrow">1 · SERVICE</div><div class="grid2">'+
 slSelect('template_key','Quote template',t.templates.map(function(x){return [x.key,x.name];}),template)+
 slSelect('service_kind','Service type',['Moving - Local','Moving - Long Distance','Packing','Junk Removal'],kind)+
 slSelect('language','Client language',['English','French'],l.language||'English')+
 slField('move_date','Service date',l.move_date,'date','required')+
 slField('start_time','Start time / planning time','09:00','time','required')+
 slSelect('movers','Movers',[2,3,4,5,6],2)+slSelect('trucks','Trucks',[[0,'0 trucks'],[1,'1 truck'],[2,'2 trucks'],[3,'3 trucks'],[4,'4 trucks']],1)+
 '</div></div>'+
 '<div class="panel panel-body"><div class="eyebrow">2 · LABOUR & HOURS</div><p class="small muted">The saved date and mover count determine the standard hourly rate. You can override it for this estimate.</p><div class="grid2">'+
 slField('hourly_rate_override','Hourly rate',0,'number','min="0" step="0.01"')+
 slField('minimum_hours','Service minimum',3,'number','min="0" step="0.25"')+
 slField('hours_low','Estimated service hours · low','','number','min="0" step="0.25"')+
 slField('hours_high','Estimated service hours · high','','number','min="0" step="0.25"')+
 '</div><div class="tiny muted" id="sl-rate-help">The date and mover count automatically load the saved hourly rate and a suggested hour range. You can edit them.</div></div>'+
 '<div class="panel panel-body"><div class="eyebrow">3 · TRANSPORT & FUEL</div><div class="grid2">'+
 slSelect('transport_mode','Transportation charge',[['hours','Hourly calculation'],['fixed','Fixed client-facing amount']],'hours')+
 slField('transport_hours','Transportation hours',1,'number','min="0" step="0.25"')+
 slField('transport_amount','Fixed transportation amount',0,'number','min="0" step="0.01"')+
 slField('distance_km','Long-distance round-trip km',0,'number','min="0" step="1"')+
 slField('km_rate','Long-distance rate per km',0.94,'number','min="0" step="0.01"')+
 slField('fuel_amount','Fixed fuel override (0 = automatic)',0,'number','min="0" step="0.01"')+
 '</div><p class="tiny muted">Local default: $35 gas per truck. A second moving truck also adds $25 to transportation. Long distance defaults to $0.94/km per moving truck; the final amount remains editable.</p></div>'+
 '<div class="panel panel-body"><div class="eyebrow">4 · PROTECTION & MATERIALS</div><div class="grid2">'+
 slField('plastic_qty','Plastic protection qty',0,'number','min="0" step="1"')+slField('plastic_unit','Plastic price each',plastic,'number','min="0" step="0.01"')+
 slField('bubble_qty','Bubble wrap qty',0,'number','min="0" step="1"')+slField('bubble_unit','Bubble price each',bubble,'number','min="0" step="0.01"')+
 slField('tv_qty','TV protection packages',0,'number','min="0" step="1"')+slField('tv_unit','TV package price',35,'number','min="0" step="0.01"')+
 slField('mirror_qty','Large mirror packages',0,'number','min="0" step="1"')+slField('mirror_unit','Mirror package price',35,'number','min="0" step="0.01"')+
 slField('fragile_qty','Other fragile glass packages',0,'number','min="0" step="1"')+slField('fragile_unit','Fragile package price',35,'number','min="0" step="0.01"')+
 slField('materials_other','Other material charges',0,'number','min="0" step="0.01"')+
 '</div><div class="callout orange"><strong>Fragile-item rule</strong>TVs, large mirrors, glass tables, mantels and similar fragile items need appropriate protection/boxing. Standard limited liability remains $0.60/lb per article unless separate insurance terms apply.</div></div>'+
 '<div class="panel panel-body"><div class="eyebrow">5 · OTHER</div><div class="grid2">'+
 slField('other_amount','Other agreed charges',0,'number','min="0" step="0.01"')+slField('discount_amount','Discount',0,'number','min="0" step="0.01"')+
 slField('deposit_required','Deposit required',300,'number','min="0" step="0.01"')+
 '</div></div>'+
 '<details class="panel panel-body"><summary class="strong">Junk-removal pricing</summary><div class="spacer"></div><div class="grid2">'+
 slSelect('junk_package','Junk package',Object.keys(t.junk_pricing).map(function(k){return [k,t.junk_pricing[k].name+' · '+slCash(t.junk_pricing[k].price_cents)];}),'quarter')+
 slField('junk_labor_blocks','Extra 30-minute 2-person labour blocks',0,'number','min="0" step="1"')+
 '</div><p class="tiny muted">$80 per additional 30 minutes for 2 crew members.</p></details>'+
 '</div><div><div id="sl-quote-result">'+slResult(null)+'</div><div class="spacer"></div><div class="callout teal"><strong>Simple rule</strong>Change any number you need. CrownOps keeps the saved company rate as the default but never hides the rate you are actually offering the client.</div></div></div></form>';
 showModal('New estimate · '+h(l.client_name),body,'<button class="btn-soft" data-sl="calculate">'+icon('refresh')+' Calculate</button><button class="btn-primary" type="submit" form="sl-quote-form">Save draft</button>',true);\n setTimeout(slRefreshRate,0);\n}
newQuote=slQuoteBuilder;

function slQuoteData(){
 const f=document.getElementById('sl-quote-form');if(!f||!f.reportValidity())return null;
 const d=Object.fromEntries(new FormData(f));
 ['lead_version','movers','trucks','hourly_rate_override','minimum_hours','hours_low','hours_high','transport_hours','transport_amount','distance_km','km_rate','fuel_amount','plastic_qty','plastic_unit','bubble_qty','bubble_unit','tv_qty','tv_unit','mirror_qty','mirror_unit','fragile_qty','fragile_unit','materials_other','other_amount','discount_amount','deposit_required','junk_labor_blocks'].forEach(function(k){if(d[k]!==''&&d[k]!=null)d[k]=Number(d[k]);});
 return d;
}
async function slRefreshRate(){
 const f=document.getElementById('sl-quote-form');if(!f)return;
 const date=slVal('move_date'),movers=slNum('movers',2),lead=slVal('lead_id'),kind=slVal('service_kind','Moving - Local');
 if(!date||kind==='Junk Removal')return;
 try{
  const r=await api('/launch/rate?move_date='+encodeURIComponent(date)+'&movers='+encodeURIComponent(movers)+'&lead_id='+encodeURIComponent(lead)+'&service_kind='+encodeURIComponent(kind));
  const rate=f.querySelector('[name="hourly_rate_override"]'),low=f.querySelector('[name="hours_low"]'),high=f.querySelector('[name="hours_high"]');
  if(rate&&!rate.dataset.userEdited){rate.value=(r.hourly_rate_cents/100).toFixed(2);}
  if(low&&!low.value)low.value=r.suggested_hours_low||'';
  if(high&&!high.value)high.value=r.suggested_hours_high||'';
  const help=document.getElementById('sl-rate-help');if(help)help.innerHTML='Saved rate: <strong>'+slCash(r.hourly_rate_cents)+'/h</strong> · '+h(r.label||'rate calendar')+'. Edit the hourly rate if this client has a special offer.';
 }catch(ex){const help=document.getElementById('sl-rate-help');if(help)help.textContent=ex.message;}
}

async function slCalculate(){
 const d=slQuoteData();if(!d)return;
 const out=await api('/launch/quotes/calculate',{method:'POST',data:d});SL.calc=out;
 if(!d.hours_low){document.querySelector('#sl-quote-form [name=hours_low]').value=out.suggestions.hours_low||'';}
 if(!d.hours_high){document.querySelector('#sl-quote-form [name=hours_high]').value=out.suggestions.hours_high||'';}
 const help=document.getElementById('sl-rate-help');if(help)help.innerHTML='Saved rate for this date/crew: <strong>'+slCash(out.suggestions.standard_rate_cents)+'/h</strong>'+(out.suggestions.rate_label?' · '+h(out.suggestions.rate_label):'')+'. Enter an override only when you are intentionally changing it.';
 document.getElementById('sl-quote-result').innerHTML=slResult(out.result);
 return out;
}

async function slOpenClient(cid){
 const d=await api('/launch/clients/'+encodeURIComponent(cid));SL.client=d.client;
 const services=d.services.map(function(s){
  return '<div class="client-service-card"><div class="client-service-main"><div class="eyebrow">'+h(s.service_kind||'Service')+'</div><h3>'+h(s.move_date||'Date to confirm')+'</h3><div class="small muted">'+h(s.pickup||'Pickup needed')+' → '+h(s.dropoff||'Destination needed')+'</div></div><div class="client-service-stats"><span>'+h(s.home_size||'Size needed')+'</span>'+(s.quote?'<span>'+slCash(s.quote.low_cents)+(s.quote.high_cents!==s.quote.low_cents?'–'+slCash(s.quote.high_cents):'')+'</span>':'<span>No estimate</span>')+'</div><div class="client-service-state">'+pill(s.stage)+(s.quote?pill(s.quote.status):'')+'<div class="row">'+
  '<button class="btn-small" data-sl="use-service" data-lead="'+h(s.id)+'">New estimate</button><button class="btn-small" data-action="open-lead" data-id="'+h(s.id)+'">Service details</button>'+(s.quote?'<button class="btn-small" data-sl="open-launch-quote" data-id="'+h(s.quote.id)+'">Open quote</button>':'')+'</div></div></div>';
 }).join('');
 showModal(h(d.client.client_name),'<section class="job-section job-client-section"><div class="job-section-head"><div><div class="eyebrow">CLIENT</div><h2>'+h(d.client.client_name)+'</h2></div><button class="btn-primary" data-sl="new-service" data-client="'+h(cid)+'">+ New service</button></div><div class="job-info-grid"><div><div class="info-label">Email</div>'+h(d.client.email||'—')+'</div><div><div class="info-label">Phone</div>'+h(d.client.phone||'—')+'</div><div><div class="info-label">Language</div>'+h(d.client.language||'English')+'</div></div></section><section class="job-section"><div class="job-section-head"><div><div class="eyebrow">HISTORY</div><h3>Newest service first</h3></div></div><div class="client-services">'+(services||empty('No services yet','Create the first service for this client.'))+'</div></section>',btn('close','Close'),true);
}

X.pageRenderers.sales=async function(){
 const q=String(S.search||'');const cs=await api('/launch/clients?q='+encodeURIComponent(q));SL.clients=cs;
 const rowsHtml=cs.map(function(c){const s=c.latest_service;return [ '<div class="client-name">'+h(c.client_name)+'</div><div class="tiny muted">'+h(c.email||'')+' · '+h(c.phone||'')+'</div>', String(c.service_count||0), s?h(s.move_date||'Date needed'):'—', s?pill(s.stage):pill('Client'), '<button class="btn-small" data-sl="open-client" data-client="'+h(c.id)+'">Open client</button>' ];});
 return head('Sales & clients','A real client database: search once, keep every move underneath the same customer.', '<button class="btn-soft" data-sl="add-client">'+icon('plus')+' New client</button><button class="btn-primary" data-action="choose-quote">'+icon('spark')+' New estimate</button>')+
 '<div class="filterbar"><div class="searchbox"><span>'+icon('search')+'</span><input id="sl-sales-search" value="'+h(q)+'" placeholder="Search name, email, phone or address"></div><span class="tiny muted">'+cs.length+' clients shown</span></div>'+
 '<div class="panel"><div class="panel-head"><h2>Client database</h2><span class="tiny muted">Newest service first inside each client file</span></div><div class="panel-body flush">'+(rowsHtml.length?xtable(['Client','Services','Latest date','Stage',''],rowsHtml):empty('No clients found','Try another search or add a new client.'))+'</div></div>';
};

function slQuoteStatusActions(q){
 if(q.status==='Draft')return '<button class="btn-primary" data-sl="finalize" data-id="'+h(q.id)+'">Finalize & send estimate</button>';
 if(q.status==='Approved')return '<button class="btn-primary" data-page="outbox">Open email outbox</button>';
 if(q.status==='Accepted')return '<div class="stack"><div class="callout teal"><strong>Accepted by client</strong>The service is on the schedule as tentative until the deposit is verified or you approve an alternate arrangement.</div><button class="btn-soft" data-sl="deposit-reminder" data-id="'+h(q.id)+'">Prepare deposit reminder</button><button class="btn-soft" data-sl="deposit-transfer" data-id="'+h(q.id)+'">Record verified e-transfer deposit</button><div class="row"><button class="btn-small" data-sl="deposit-cash" data-id="'+h(q.id)+'">Approve cash on move day</button><button class="btn-small" data-sl="deposit-cheque" data-id="'+h(q.id)+'">Approve cheque on move day</button></div></div>';
 return '';
}
openQuote=async function(qid){
 const qs=await api('/quotes');const q=qs.find(function(x){return x.id===qid;});if(!q)return slOldOpenQuote(qid);
 SL.quote=q;
 showModal('Estimate revision '+q.revision,'<div class="row"><h3>'+h(q.client_name)+'</h3><div class="grow"></div>'+pill(q.status)+'</div><div class="spacer"></div><div class="quote-layout"><div>'+slResult(q.result)+'</div><div class="stack"><div class="callout"><strong>Saved version</strong>This revision keeps the exact rates and amounts you offered. New edits create a new revision.</div><div class="info-grid"><div><div class="info-label">Date</div>'+dateLabel(q.inputs.move_date)+'</div><div><div class="info-label">Deposit</div>'+slCash(Number(q.inputs.deposit_required||300)*100)+'</div><div><div class="info-label">Template</div>'+h(q.inputs.template_key||'Local')+'</div><div><div class="info-label">Service</div>'+h(q.inputs.service_kind||'Moving')+'</div></div>'+slQuoteStatusActions(q)+'</div></div>',btn('close','Close'),true);
};

document.addEventListener('click',async function(e){
 const b=e.target.closest('[data-sl]');if(!b)return;e.preventDefault();const a=b.dataset.sl;
 try{
  if(a==='client-search')return slChooseClient(document.getElementById('sl-client-search').value);
  if(a==='add-client')return slNewClient();
  if(a==='new-service')return slNewService(b.dataset.client);
  if(a==='use-service'){modal.close();return slQuoteBuilder(b.dataset.lead);}
  if(a==='calculate')return slCalculate();
  if(a==='open-client')return slOpenClient(b.dataset.client);
  if(a==='open-launch-quote')return openQuote(b.dataset.id);
  if(a==='finalize'){
   const out=await api('/launch/quotes/'+encodeURIComponent(b.dataset.id)+'/finalize',{method:'POST',data:{confirmed:true}});
   try{
    await api('/outbound/'+encodeURIComponent(out.message_id)+'/approve',{method:'POST',data:{reviewed:true}});
    const sent=await api('/outbound/'+encodeURIComponent(out.message_id)+'/send',{method:'POST',data:{confirm:true}});
    toast(sent.sent?'Estimate finalized and emailed to the client.':'Estimate finalized; email remains in Outbox.');
   }catch(ex){toast('Estimate finalized. Email is approved in Outbox and will send once live Gmail actions are enabled.');}
   return openQuote(b.dataset.id);
  }
  if(a==='deposit-reminder'){const r=await api('/launch/quotes/'+encodeURIComponent(b.dataset.id)+'/deposit-action',{method:'POST',data:{action:'reminder'}});try{await api('/outbound/'+encodeURIComponent(r.message_id)+'/approve',{method:'POST',data:{reviewed:true}});}catch(_){}toast('Deposit reminder prepared in Outbox.');return openQuote(b.dataset.id);}
  if(a==='deposit-transfer')return showModal('Record verified e-transfer deposit','<form id="sl-deposit-form" data-sl-form="deposit"><input type="hidden" name="quote_id" value="'+h(b.dataset.id)+'">'+slField('amount','Amount received',300,'number','min="0.01" step="0.01" required')+slField('reference','Transfer / bank reference','','text','required')+'</form>',btn('close','Cancel')+'<button type="submit" form="sl-deposit-form" class="btn-primary">Confirm received deposit</button>');
  if(a==='deposit-cash'){if(confirm('Confirm this booking with cash due on the service day? No payment will be recorded as received yet.')){await api('/launch/quotes/'+encodeURIComponent(b.dataset.id)+'/deposit-action',{method:'POST',data:{action:'cash_on_day'}});toast('Booking confirmed with cash-due exception.');return openQuote(b.dataset.id);}}
  if(a==='deposit-cheque'){if(confirm('Confirm this booking with cheque due on the service day? No payment will be recorded as received yet.')){await api('/launch/quotes/'+encodeURIComponent(b.dataset.id)+'/deposit-action',{method:'POST',data:{action:'cheque_on_day'}});toast('Booking confirmed with cheque-due exception.');return openQuote(b.dataset.id);}}
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}
});

document.addEventListener('change',function(e){
 if(!e.target.closest('#sl-quote-form'))return;
 if(e.target.name==='hourly_rate_override')e.target.dataset.userEdited='1';
 if(['move_date','movers','service_kind'].includes(e.target.name))slRefreshRate();
});

document.addEventListener('keydown',function(e){
 if(e.key==='Enter'&&e.target.id==='sl-client-search'){e.preventDefault();slChooseClient(e.target.value);}
 if(e.key==='Enter'&&e.target.id==='sl-sales-search'){e.preventDefault();S.search=e.target.value;navigate('sales');}
});

document.addEventListener('submit',async function(e){
 const f=e.target.closest('form[data-sl-form]');if(!f)return;e.preventDefault();
 const kind=f.dataset.slForm,d=Object.fromEntries(new FormData(f));
 try{
  if(kind==='client'){const c=await api('/launch/clients',{method:'POST',data:d});const list=await api('/launch/clients?q='+encodeURIComponent(d.email||d.phone||d.client_name));SL.clients=list;return slNewService(c.id);}
  if(kind==='service'){const cid=d.client_id;delete d.client_id;const r=await api('/launch/clients/'+encodeURIComponent(cid)+'/services',{method:'POST',data:d});modal.close();return slQuoteBuilder(r.lead_id);}
  if(kind==='quote'){const data=slQuoteData();if(!data)return;const q=await api('/launch/quotes',{method:'POST',data:data});toast('Draft estimate saved.');modal.close();await navigate('quotes');return openQuote(q.id);}
  if(kind==='deposit'){const qid=d.quote_id;delete d.quote_id;await api('/launch/quotes/'+encodeURIComponent(qid)+'/deposit-action',{method:'POST',data:{action:'etransfer_received',amount:Number(d.amount),reference:d.reference}});toast('Deposit recorded and booking confirmed.');return openQuote(qid);}
 }catch(ex){modalError(ex);}
});
