/* CrownOps v6 launch CRM: client-first sales and fast quote workflow. */
'use strict';
const CRM={clientSearch:'',clients:[],templates:[],currentClient:null,currentLead:null,currentQuote:null,lastCalc:null};

function crmCash(c){return 'CA$'+(Number(c||0)/100).toLocaleString('en-CA',{minimumFractionDigits:2,maximumFractionDigits:2});}
function crmServiceLabel(s){return s||'Moving';}
function crmLineTable(result){
 const rows=(result?.line_items||[]).map(x=>[
   '<strong>'+h(x.name)+'</strong>'+(x.note?'<div class="tiny muted">'+h(x.note)+'</div>':''),
   h(x.qty??1),crmCash(x.unit_cents),'<strong>'+crmCash(x.total_cents)+'</strong>'
 ]);
 return rows.length?xtable(['Item','Qty','Price','Total'],rows):'<p class="small muted">This older estimate does not contain itemized v6 lines.</p>';
}
function crmClientCard(c){
 const latest=(c.services||[])[0];
 return '<button class="search-result crm-client-result" data-crm="client-open" data-id="'+h(c.id)+'">'+
   '<div class="avatar">'+h(initials(c.client_name))+'</div><div class="grow"><div class="strong">'+h(c.client_name)+'</div>'+
   '<div class="tiny muted">'+h(c.email||'No email')+' · '+h(c.phone||'No phone')+'</div>'+
   '<div class="tiny">'+(latest?(h(latest.move_date||'Date needed')+' · '+h(latest.pickup||'Address needed')+' · '+h(latest.stage||'')):'No service history yet')+'</div></div>'+
   '<span class="pill gray">'+Number((c.services||[]).length)+' service'+((c.services||[]).length===1?'':'s')+'</span>'+icon('right')+'</button>';
}
async function crmSearchClients(q=''){
 CRM.clientSearch=q;CRM.clients=await api('/crm/clients?q='+encodeURIComponent(q));
 return CRM.clients;
}
function crmActivityTime(v){
 if(!v)return 'No inquiry activity';
 const d=new Date(v);if(Number.isNaN(d.getTime()))return h(v);
 const day=d.toLocaleDateString('en-CA',{timeZone:'America/Toronto',month:'short',day:'numeric'});
 const tm=d.toLocaleTimeString('en-CA',{timeZone:'America/Toronto',hour:'numeric',minute:'2-digit'});
 return day+' · '+tm;
}
function crmActivityBadge(a){
 if(!a)return '<span class="pill gray">Client record</span>';
 const label=String(a.label||'Inquiry');
 const cl=/voicemail/i.test(label)?'purple':/missed call/i.test(label)?'red':/quote request|form/i.test(label)?'blue':/email/i.test(label)?'teal':'gray';
 return '<span class="pill '+cl+'">'+h(label)+'</span>';
}
async function crmClientBrowser(){
 const clients=await crmSearchClients(CRM.clientSearch);
 const rows=clients.map(c=>{
   const a=c.latest_activity||{};
   const latest=(c.services||[]).find(s=>s.id===a.lead_id)||(c.services||[])[0]||{};
   const activity=crmActivityBadge(a)+
     '<div class="tiny strong" style="margin-top:5px">'+h(crmActivityTime(a.at||c.activity_at))+'</div>'+
     '<div class="tiny muted">'+h(a.source||'CrownOps')+(a.summary?' · '+h(a.summary):'')+'</div>';
   const follow=Number(c.open_followups||0);
   return [
    '<div class="client-name">'+h(c.client_name)+'</div><div class="tiny muted">'+h(c.email||'')+'<br>'+h(c.phone||'')+'</div>',
    activity,
    latest.id?(h(latest.move_date||'Date needed')+'<div class="tiny muted">'+h(latest.pickup||'Address needed')+'</div>'):'No service yet',
    pill(latest.stage||'Client')+(follow?'<div class="tiny" style="margin-top:5px"><strong>'+h(follow)+'</strong> open follow-up'+(follow===1?'':'s')+'</div>':''),
    h((c.services||[]).length),
    '<button class="btn-small" data-crm="client-open" data-id="'+h(c.id)+'">Open</button>'
   ];
 });
 return head('Sales & clients','Your newest quote requests, missed calls, voicemails and client messages appear first. Search any client without losing their service history.',
   '<button class="btn-primary" data-crm="client-new">'+icon('plus')+'New client</button>'+
   '<button class="btn-soft" data-action="new-lead">'+icon('plus')+'New inquiry</button>')+
   '<div class="filterbar"><div class="searchbox" style="min-width:360px">'+icon('search')+
   '<input id="crm-client-search" value="'+h(CRM.clientSearch)+'" placeholder="Search name, email, phone or address..." aria-label="Search clients"></div>'+
   '<span class="tiny muted">'+clients.length+' client matches · newest inquiry first</span></div>'+
   '<div class="panel"><div class="panel-head"><h2>Inquiry inbox</h2><span class="tiny muted">Forms, calls, voicemails and emails are ranked by the client's actual inquiry time</span></div>'+
   '<div class="panel-body flush">'+(rows.length?xtable(['Client','Latest inquiry','Move / service','Stage / follow-up','History',''],rows):empty('No clients found','Search another email, phone or name, or create a new client.'))+'</div></div>';
}
X.pageRenderers.sales=crmClientBrowser;

const crmBaseQuotes=X.pageRenderers.quotes;
X.pageRenderers.quotes=async()=>{
 const all=await api('/quotes');
 const search=String(UX.estimateSearch||'').trim().toLowerCase();
 const filtered=all.filter(q=>(UX.estimateStatus==='all'||q.status===UX.estimateStatus)&&(!search||[q.client_name,q.status,q.inputs?.move_date,q.inputs?.pickup,q.inputs?.dropoff,q.inputs?.service_type].join(' ').toLowerCase().includes(search)));
 const counts={all:all.length,Draft:0,Approved:0,Accepted:0};all.forEach(q=>{if(counts[q.status]!==undefined)counts[q.status]++;});
 const rows=filtered.map(q=>[
   '<div class="client-name">'+h(q.client_name)+'</div><div class="tiny muted">Revision '+h(q.revision)+' · '+h(q.inputs?.service_type||'Moving')+'</div>',
   h(q.inputs?.move_date||'Date needed'),
   q.result?.movers?('<strong>'+h(q.result.movers)+' movers</strong><div class="tiny muted">'+h(q.result.trucks||1)+' truck(s)</div>'):'—',
   '<strong>'+crmCash(q.result?.low_cents)+'</strong> – <strong>'+crmCash(q.result?.high_cents)+'</strong>',
   pill(q.status),
   '<button class="btn-small" data-crm="quote-open" data-id="'+h(q.id)+'">Open</button>'
 ]);
 const overview='<div class="ux-overview-grid">'+[
  uxOverviewMetric('Total estimates',counts.all),uxOverviewMetric('Draft',counts.Draft),
  uxOverviewMetric('Ready / sent',counts.Approved),uxOverviewMetric('Accepted',counts.Accepted)
 ].join('')+'</div>';
 const filters='<div class="ux-browser-toolbar"><div class="ux-filter-chips">'+[
  uxStatusButton('estimate-filter','all','All',counts.all,UX.estimateStatus),
  uxStatusButton('estimate-filter','Draft','Draft',counts.Draft,UX.estimateStatus),
  uxStatusButton('estimate-filter','Approved','Sent / ready',counts.Approved,UX.estimateStatus),
  uxStatusButton('estimate-filter','Accepted','Accepted',counts.Accepted,UX.estimateStatus)
 ].join('')+'</div><div class="searchbox ux-browser-search">'+icon('search')+
 '<input id="ux-estimate-search" value="'+h(UX.estimateSearch)+'" placeholder="Search client, email, date or address" aria-label="Search estimates"></div></div>';
 return head('Estimates','Client-first quoting with automatic date rates, editable pricing, English/French templates and deposit workflow.',
   '<button class="btn-primary" data-crm="quote-new">'+icon('plus')+'New Quote</button>'+
   '<button class="btn-soft" data-crm="templates">'+icon('file')+'Templates</button>')+
   panel('Overview',overview)+'<div class="spacer"></div>'+
   '<div class="panel"><div class="panel-head"><h2>Estimates</h2><span class="tiny muted">'+filtered.length+' shown</span></div>'+
   filters+'<div class="panel-body flush">'+(rows.length?xtable(['Client','Service date','Crew','Estimate','Status',''],rows):empty('No estimates in this view','Create a new estimate or change the filters.'))+'</div></div>';
};

async function crmChooseClient(){
 const clients=await crmSearchClients('');
 showModal('Choose a client for a quote',
   '<div class="callout teal"><strong>Find the client once.</strong>Search name, email, phone or address. You can use an existing service, add a new service, or create a brand-new client.</div>'+
   '<div class="spacer"></div><div class="searchbox" style="width:100%">'+icon('search')+
   '<input id="crm-quote-client-search" placeholder="Search name, email, phone or address..." autocomplete="off"></div>'+
   '<div id="crm-client-results" style="margin-top:14px">'+clients.map(crmClientCard).join('')+'</div>',
   '<button data-crm="client-new" class="btn-soft">'+icon('plus')+'New client</button>'+btn('close','Cancel'),true);
}
async function crmNewClient(){
 showModal('Add new client','<form id="crm-new-client" data-crm-form="client"><div class="grid2">'+
   field('client_name','Client name','','text','required')+field('email','Email','','email')+field('phone','Phone')+
   select('language','Preferred language',[['English','English'],['French','Français']],'English')+
   '</div></form>',btn('close','Cancel')+'<button type="submit" form="crm-new-client" class="btn-primary">Save & add service</button>');
}
async function crmOpenClient(cid){
 const c=await api('/crm/clients/'+encodeURIComponent(cid));CRM.currentClient=c;
 const duplicates=(c.possible_duplicates||[]);
 const duplicateWarning=duplicates.length?'<div class="callout orange"><strong>Possible duplicate client record</strong>'+duplicates.map(d=>'<div class="task-row"><div class="grow"><div class="small strong">'+h(d.client_name)+'</div><div class="tiny muted">'+h([d.email,d.phone].filter(Boolean).join(' · '))+'</div><div class="tiny muted">'+h(d.reason)+'</div></div><button class="btn-small" data-crm="duplicate-open" data-id="'+h(d.candidate_id)+'">Open</button><button class="btn-small" data-crm="duplicate-dismiss" data-id="'+h(d.id)+'" data-client="'+h(c.id)+'">Not same client</button></div>').join('')+'</div><div class="spacer"></div>':'';
 const services=(c.services||[]).map(s=>[
   '<strong>'+h(crmServiceLabel(s.lead_type))+'</strong><div class="tiny muted">'+h(s.home_size||'Size not entered')+'</div>',
   h(s.move_date||'Date needed'),
   h(s.pickup||'Pickup needed')+'<div class="tiny muted">'+h(s.dropoff||'Destination needed')+'</div>',
   pill(s.stage),
   '<button class="btn-small" data-crm="quote-from-service" data-id="'+h(s.id)+'">Quote</button>'
 ]);
 showModal(h(c.client_name),
   duplicateWarning+'<section class="job-section"><div class="eyebrow">CLIENT INFORMATION</div><div class="spacer"></div><div class="info-grid">'+
   '<div><div class="info-label">Email</div><div>'+h(c.email||'Not provided')+'</div></div>'+
   '<div><div class="info-label">Phone</div><div>'+h(c.phone||'Not provided')+'</div></div>'+
   '<div><div class="info-label">Language</div><div>'+h(c.language||'English')+'</div></div>'+
   '<div><div class="info-label">Service history</div><div>'+services.length+' record(s)</div></div></div></section>'+
   '<div class="spacer"></div><div class="panel"><div class="panel-head"><h3>Services</h3><button class="btn-primary btn-small" data-crm="service-new" data-id="'+h(c.id)+'">'+icon('plus')+'New service</button></div>'+
   '<div class="panel-body flush">'+(services.length?xtable(['Service','Date','Route','Stage',''],services):empty('No services yet','Add the first service for this client.'))+'</div></div>',
   '<button class="btn-soft" data-crm="client-edit" data-id="'+h(c.id)+'">Edit client</button>'+btn('close','Close'),true);
}
async function crmEditClient(cid){
 const c=CRM.currentClient?.id===cid?CRM.currentClient:await api('/crm/clients/'+encodeURIComponent(cid));CRM.currentClient=c;
 showModal('Edit client · '+h(c.client_name),
   '<form id="crm-edit-client" data-crm-form="client-edit"><input type="hidden" name="client_id" value="'+h(cid)+'"><div class="grid2">'+
   field('client_name','Client name',c.client_name||'','text','required')+field('email','Email',c.email||'','email')+
   field('phone','Phone',c.phone||'')+select('language','Preferred language',[['English','English'],['French','Français']],c.language||'English')+
   '</div><p class="small muted">This is the client contact profile used for new services. Older service records keep their historical details.</p></form>',
   btn('close','Cancel')+'<button type="submit" form="crm-edit-client" class="btn-primary">Save client</button>');
}
async function crmNewService(cid){
 const c=CRM.currentClient?.id===cid?CRM.currentClient:await api('/crm/clients/'+encodeURIComponent(cid));CRM.currentClient=c;
 showModal('New service · '+h(c.client_name),
   '<form id="crm-new-service" data-crm-form="service"><input type="hidden" name="client_id" value="'+h(cid)+'"><div class="grid2">'+
   select('service_type','Service type',[['Moving','Moving'],['Packing','Packing'],['Junk Removal','Junk removal'],['Long Distance','Long distance']],'Moving','required')+
   field('move_date','Service date','','date')+field('pickup','Pickup / service address')+field('dropoff','Destination address')+
   field('home_size','Move size / description')+field('boxes','Approx. boxes')+
   field('pickup_access','Pickup access')+field('dropoff_access','Destination access')+
   field('pickup_walk','Pickup walk / parking')+field('dropoff_walk','Destination walk / parking')+
   textArea('inventory','Furniture / inventory','',4)+textArea('heavy_items','Heavy / special items','',3)+
   '</div></form>',btn('close','Cancel')+'<button type="submit" form="crm-new-service" class="btn-primary">Create service & quote</button>',true);
}
async function crmQuoteBuilder(leadId){
 const templates=await api('/crm/templates');CRM.templates=templates;
 const firstSetup=await api('/crm/quote-setup?lead_id='+encodeURIComponent(leadId)+'&movers=2');CRM.currentLead=firstSetup.lead;CRM.longDistancePresets=firstSetup.long_distance_presets||[];
 const lead=firstSetup.lead;const lang=String(lead.language||'English').toLowerCase().startsWith('fr')?'French':'English';
 const type=['Moving','Packing','Junk Removal','Long Distance'].includes(lead.lead_type)?lead.lead_type:'Moving';
 const template=templates.find(t=>t.service_type===type&&t.language===lang)||templates[0];
 const materialRows=(firstSetup.materials||[]).map(m=>
   '<div class="crm-material-row"><label class="checkbox-label"><input type="checkbox" class="crm-mat-check" data-code="'+h(m.code)+'" data-name="'+h(m.name)+'" data-price="'+h(m.sell_cents)+'">'+
   '<span><strong>'+h(m.name)+'</strong><br><span class="tiny muted">'+crmCash(m.sell_cents)+' · '+h(m.category)+'</span></span></label>'+
   '<input class="crm-mat-qty" data-code="'+h(m.code)+'" type="number" min="0" step="1" value="1" style="width:85px" disabled></div>').join('');
 showModal('Build a quote · '+h(lead.client_name),
   '<form id="crm-quote-form" data-crm-form="quote"><input type="hidden" name="lead_id" value="'+h(lead.id)+'"><div class="quote-layout"><div class="stack">'+
   '<div class="panel panel-body"><div class="eyebrow">1 · SERVICE</div><div class="grid2">'+
   select('service_type','Service type',[['Moving','Moving'],['Packing','Packing'],['Junk Removal','Junk removal'],['Long Distance','Long distance']],type,'required')+
   select('language','Quote language',[['English','English'],['French','Français']],lang,'required')+
   field('move_date','Service date',lead.move_date||'','date','required')+
   select('start_window','Arrival / start window',[['morning','Morning · 9:00-9:30 AM'],['afternoon','Afternoon · 1:00-4:00 PM'],['custom','Custom exact time']],'morning','required')+
   '<div id="crm-custom-start" style="display:none">'+field('start_time','Custom exact start time','','time')+'</div>'+
   select('template_key','Template',templates.filter(t=>t.service_type===type&&t.language===lang).map(t=>[t.template_key,t.name]),template?.template_key||'')+
   '</div></div>'+
   '<div class="panel panel-body" id="crm-moving-fields"><div class="eyebrow">2 · CREW & RATE</div><p class="small muted">Operational rule: 4-5 movers require at least 2 trucks; 6 movers require at least 3 trucks.</p><div class="grid2">'+
   select('movers','Movers',[2,3,4,5,6],2,'required')+select('trucks','Truck(s)',[1,2,3],1,'required')+
   field('hourly_rate','Hourly rate',firstSetup.suggested_hourly_rate??'','number','step="0.01" min="1" required')+
   '<div class="form-field"><label>Rate source</label><input id="crm-rate-source" value="'+h(firstSetup.rate_source||'Select date')+'" disabled></div>'+
   field('hours_low','Estimated hours - low',3,'number','step="0.25" min="0.25" required')+
   field('hours_high','Estimated hours - high',4,'number','step="0.25" min="0.25" required')+
   field('minimum_hours','Minimum service hours',3,'number','step="0.25" min="0" required')+
   '<label id="crm-small-afternoon-wrap" class="checkbox-label full"><input type="checkbox" name="small_afternoon_job"><span><strong>Small afternoon job</strong><br><span class="tiny muted">Use only for a small local Moving/Packing job. Sets Afternoon 1:00-4:00 PM and a 1.5-hour service minimum.</span></span></label>'+
   field('deposit_required','Deposit required',300,'number','step="0.01" min="0"')+
   '</div></div>'+
   '<div class="panel panel-body" id="crm-transport-fields"><div class="eyebrow">3 · TRANSPORTATION</div><p class="small muted">Local: transportation hours × team rate, plus $25 per extra truck. Long distance: enter true total driving time from 700 Rue Hodge through the route and back to base. Driving time of 1 hour+ is billed at 1.15×. Gas/distance uses actual round-trip road KM + 5%, then $0.94/km per truck.</p><div class="grid2">'+
   select('distance_preset','Long-distance city preset',[['','Manual / exact route']].concat((firstSetup.long_distance_presets||[]).map(p=>[p.key,p.city+', '+p.province+' · '+p.round_trip_km+' km round trip'])),'')+
   field('transport_hours','True driving / transportation hours',1,'number','step="0.25" min="0"')+
   field('fixed_transport','OR fixed transportation fee','','number','step="0.01" min="0"')+
   field('distance_km','Actual round-trip road km',0,'number','step="1" min="0"')+
   field('distance_rate','Long-distance rate / km',0.94,'number','step="0.01" min="0"')+
   '</div></div>'+
   '<div class="panel panel-body" id="crm-junk-fields" style="display:none"><div class="eyebrow">JUNK REMOVAL</div><div class="grid2">'+
   select('junk_tier','Truck space',[['single','$175 · Single item'],['quarter','$400 · 1/4 truck · approx. 5 ft'],['half','$800 · 1/2 truck · approx. 10 ft'],['three_quarter','$1,200 · 3/4 truck · approx. 15 ft'],['full','$1,600 · Full 20 ft truck']],'quarter')+
   field('junk_extra_half_hours','Extra labour · $80 per 30 min / 2 crew',0,'number','step="1" min="0"')+'</div></div>'+
   '<div class="panel panel-body"><div class="eyebrow">4 · MATERIALS / PROTECTION</div><p class="small muted">Select only what applies. TV protection is preset at $35. Other prices come from Materials and can be maintained there.</p>'+
   '<div class="crm-material-list">'+materialRows+'</div><div class="grid2" style="margin-top:14px">'+
   field('other_amount','Other agreed charges',0,'number','step="0.01" min="0"')+field('discount_amount','Approved discount',0,'number','step="0.01" min="0"')+
   '</div></div>'+
   '<div class="callout teal"><strong>Protection & liability</strong>Free limited liability coverage is $0.60/lb per article. TVs, large mirrors, glass, marble and other fragile items require suitable protection/boxing. Added packing protection does not by itself increase liability coverage.</div>'+
   '</div><div class="stack"><div id="crm-quote-result" class="quote-summary"><div class="eyebrow">ESTIMATE PREVIEW</div><div class="quote-price">Choose the service details</div><p class="muted">Rates remain editable before saving.</p></div>'+
   '<div class="panel panel-body"><div class="eyebrow">CLIENT</div><h3>'+h(lead.client_name)+'</h3><p class="small">'+h(lead.email)+'<br>'+h(lead.phone)+'</p><p class="tiny muted">'+h(lead.pickup||'Pickup needed')+' → '+h(lead.dropoff||'Destination needed')+'</p></div>'+
   '</div></div></form>',
   btn('close','Cancel')+'<button type="button" class="btn-soft" data-crm="quote-calc">Calculate</button><button type="submit" form="crm-quote-form" class="btn-primary">Save draft</button>',true);
 crmToggleServiceFields();crmToggleStartTime();
}
function crmQuoteData(){
 const f=document.getElementById('crm-quote-form');if(!f||!f.reportValidity())return null;
 const d=Object.fromEntries(new FormData(f));d.materials=[];
 d.small_afternoon_job=Boolean(f.elements.small_afternoon_job?.checked);
 document.querySelectorAll('.crm-mat-check:checked').forEach(ch=>{const qty=document.querySelector('.crm-mat-qty[data-code="'+CSS.escape(ch.dataset.code)+'"]');d.materials.push({code:ch.dataset.code,name:ch.dataset.name,qty:Number(qty?.value||1),unit_price:Number(ch.dataset.price||0)/100});});
 for(const k of ['movers','trucks','hourly_rate','hours_low','hours_high','minimum_hours','deposit_required','transport_hours','fixed_transport','distance_km','distance_rate','junk_extra_half_hours','other_amount','discount_amount']){
   if(d[k]===undefined)continue;
   if(d[k]==='')d[k]=null;else d[k]=Number(d[k]);
 }
 return d;
}
function crmRenderCalc(r){
 CRM.lastCalc=r;const el=document.getElementById('crm-quote-result');if(!el)return;
 el.innerHTML='<div class="eyebrow">ESTIMATED RANGE · CAD</div><div class="quote-price">'+crmCash(r.low_cents)+' – '+crmCash(r.high_cents)+'</div>'+
 '<p class="muted">Before applicable taxes</p><div class="quote-line"><span>Service</span><strong>'+crmCash(r.service_low_cents)+' – '+crmCash(r.service_high_cents)+'</strong></div>'+
 '<div class="quote-line"><span>'+(r.service_type==='Long Distance'?'Long Distance Transportation Fee':'Transportation Fee')+'</span><strong>'+crmCash(r.transport_cents)+'</strong></div>'+
 (r.service_type==='Long Distance'&&r.transport_hours?'<div class="quote-line"><span>True / billable driving hours</span><strong>'+h(r.transport_hours)+' h → '+h(r.billable_transport_hours)+' h</strong></div>':'')+
 '<div class="quote-line"><span>Fuel / distance</span><strong>'+crmCash(r.fuel_cents)+'</strong></div>'+
 (r.service_type==='Long Distance'&&r.distance_km?'<div class="quote-line"><span>Actual / adjusted distance</span><strong>'+h(r.distance_km)+' km → '+h(r.adjusted_distance_km)+' km</strong></div>':'')+
 '<div class="quote-line"><span>Materials</span><strong>'+crmCash(r.materials_cents)+'</strong></div>'+
 '<div class="quote-line"><span>Other / discount</span><strong>'+crmCash((r.other_cents||0)-(r.discount_cents||0))+'</strong></div>'+
 '<div class="tiny" style="margin-top:12px">'+h(r.movers)+' movers · '+h(r.trucks)+' truck(s)'+(r.hourly_rate_cents?' · '+crmCash(r.hourly_rate_cents)+'/h':'')+'</div>'+
 (r.hos_plan?'<div class="spacer"></div><div class="callout '+(r.hos_plan.one_day_possible?'teal':'orange')+'"><strong>Quebec heavy-vehicle HOS planning</strong><div class="small">'+h(r.hos_plan.status)+'</div><div class="tiny" style="margin-top:6px">'+h(r.hos_plan.true_driving_hours)+' h driving · '+h(r.hos_plan.estimated_on_duty_hours)+' h estimated on-duty · '+h(r.hos_plan.minimum_schedule_days)+' day(s) minimum. Final dispatch must verify the assigned driver\'s rest, 16-hour elapsed window and cycle availability.</div></div>':'');
}
function crmToggleStartTime(){
 const f=document.getElementById('crm-quote-form');if(!f)return;const wrap=document.getElementById('crm-custom-start');if(!wrap)return;
 const custom=f.elements.start_window.value==='custom';wrap.style.display=custom?'block':'none';
 const input=f.elements.start_time;if(input){input.required=custom;if(!custom)input.value='';}
}
function crmToggleServiceFields(){
 const f=document.getElementById('crm-quote-form');if(!f)return;const type=f.elements.service_type.value;
 const toggle=(id,disabled)=>{const el=document.getElementById(id);if(!el)return;el.style.display=disabled?'none':'block';el.querySelectorAll('input,select,textarea').forEach(x=>x.disabled=disabled);};
 toggle('crm-junk-fields',type!=='Junk Removal');
 toggle('crm-moving-fields',type==='Junk Removal');
 toggle('crm-transport-fields',type==='Junk Removal');
 const smallWrap=document.getElementById('crm-small-afternoon-wrap'),small=f.elements.small_afternoon_job;
 const smallAllowed=['Moving','Packing'].includes(type);
 if(smallWrap)smallWrap.style.display=smallAllowed?'flex':'none';
 if(small&&!smallAllowed&&small.checked){small.checked=false;if(f.elements.minimum_hours)f.elements.minimum_hours.value='3';}
}
async function crmRefreshRate(){
 const f=document.getElementById('crm-quote-form');if(!f||!f.elements.move_date.value)return;
 const r=await api('/crm/quote-setup?lead_id='+encodeURIComponent(f.elements.lead_id.value)+'&move_date='+encodeURIComponent(f.elements.move_date.value)+'&movers='+encodeURIComponent(f.elements.movers.value));
 f.elements.hourly_rate.value=r.suggested_hourly_rate??'';document.getElementById('crm-rate-source').value=r.rate_source||'';
}
async function crmOpenQuote(qid){
 const d=await api('/crm/quotes/'+encodeURIComponent(qid));CRM.currentQuote=d;const q=d.quote,r=q.result,l=d.lead;
 const depositRequired=Math.round(Number(q.inputs?.deposit_required||0)*100);const due=Math.max(0,depositRequired-Number(d.verified_deposit_cents||0));
 const status='<div class="row wrap">'+pill(q.status)+(d.job?pill(d.job.status):'')+(depositRequired?'<span class="pill '+(due<=0?'teal':'orange')+'">Deposit '+(due<=0?'received':crmCash(due)+' due')+'</span>':'')+'</div>';
 const actions=[];
 if(q.status==='Draft'||q.status==='Approved')actions.push('<button class="btn-primary" data-crm="quote-finalize" data-id="'+h(q.id)+'">Finalize & send</button>');
 if(['Approved','Accepted'].includes(q.status))actions.push('<button class="btn-soft" data-v4="qbo-estimate" data-id="'+h(q.id)+'">QuickBooks draft</button>');
 if(q.status==='Accepted'&&due>0){actions.push('<button class="btn-soft" data-crm="deposit-remind" data-id="'+h(q.id)+'">Deposit reminder</button>');actions.push('<button class="btn-primary" data-crm="deposit-record" data-id="'+h(q.id)+'">Record / approve deposit</button>');}
 showModal('Estimate · '+h(l.client_name),
   status+'<div class="spacer"></div><div class="quote-layout"><div>'+
   '<div class="quote-summary"><div class="eyebrow">'+h(r.service_type||'Moving').toUpperCase()+'</div><div class="quote-price">'+crmCash(r.low_cents)+' – '+crmCash(r.high_cents)+'</div><p class="muted">Before applicable taxes · Revision '+h(q.revision)+'</p>'+
   '<div class="quote-line"><span>Service date</span><strong>'+h(q.inputs?.move_date||l.move_date||'Not set')+'</strong></div>'+
   '<div class="quote-line"><span>Arrival / start</span><strong>'+h(q.inputs?.start_window_label||q.inputs?.start_time||'Not set')+'</strong></div>'+
   '<div class="quote-line"><span>Client template</span><strong>'+h(q.inputs?.template_snapshot?.name||q.inputs?.template_key||'—')+'</strong></div>'+
   '<div class="quote-line"><span>Crew</span><strong>'+h(r.movers||'—')+' movers · '+h(r.trucks||'—')+' truck(s)</strong></div>'+
   (r.hourly_rate_cents?'<div class="quote-line"><span>Hourly rate</span><strong>'+crmCash(r.hourly_rate_cents)+'/h</strong></div>':'')+
   '<div class="quote-line"><span>'+(r.service_type==='Long Distance'?'Long Distance Transportation Fee':'Transportation Fee')+'</span><strong>'+crmCash(r.transport_cents)+'</strong></div>'+
   (r.service_type==='Long Distance'&&r.transport_hours?'<div class="quote-line"><span>True / billable driving hours</span><strong>'+h(r.transport_hours)+' h → '+h(r.billable_transport_hours)+' h</strong></div>':'')+
   '<div class="quote-line"><span>Fuel / distance</span><strong>'+crmCash(r.fuel_cents)+'</strong></div></div>'+
   (r.hos_plan?'<div class="spacer"></div><div class="callout '+(r.hos_plan.one_day_possible?'teal':'orange')+'"><strong>Quebec HOS plan</strong>'+h(r.hos_plan.status)+'<br><span class="tiny">'+h(r.hos_plan.true_driving_hours)+' h driving · '+h(r.hos_plan.estimated_on_duty_hours)+' h estimated on-duty · final driver log/cycle verification required.</span></div>':'')+
   '<div class="spacer"></div><div class="panel panel-body">'+crmLineTable(r)+'</div></div>'+
   '<div class="stack"><div class="panel panel-body"><div class="eyebrow">CLIENT</div><h3>'+h(l.client_name)+'</h3><p>'+h(l.email)+'<br>'+h(l.phone)+'</p><p class="small muted">'+h(l.pickup||'Pickup needed')+' → '+h(l.dropoff||'Destination needed')+'</p></div>'+
   '<div class="callout teal"><strong>'+h(q.inputs?.template_snapshot?.title||'Included')+'</strong>'+h(q.inputs?.template_snapshot?.liability||'Free limited liability coverage: $0.60/lb per article.')+(q.inputs?.template_snapshot?.fragile?'<br><br>'+h(q.inputs.template_snapshot.fragile):'')+(q.inputs?.template_snapshot?.blank_notes?'<br><br>'+h(q.inputs.template_snapshot.blank_notes):'')+'</div>'+
   (d.job?'<div class="panel panel-body"><div class="eyebrow">SCHEDULE</div><strong>'+h(d.job.status)+'</strong><p class="small muted">'+h(localDay(d.job.start_at))+' · crew/truck assignment follows confirmation.</p></div>':'')+
   '</div></div>',actions.join(' ')+btn('close','Close'),true);
}
async function crmFinalize(qid){
 if(!confirm('Finalize this estimate and send the client their private View Estimate link through the connected Gmail account?'))return;
 const r=await api('/crm/quotes/'+encodeURIComponent(qid)+'/finalize',{method:'POST',data:{send_now:true,sync_quickbooks:true}});
 if(r.sent&&!r.quickbooks_error){toast('Estimate finalized, sent and synchronized to QuickBooks.');modal.close();return navigate('quotes');}
 const emailState=r.sent?'<div class="callout teal"><strong>Client email sent.</strong>The private View Estimate link was delivered.</div>':
   '<div class="callout orange"><strong>The estimate is ready, but Gmail did not confirm delivery.</strong>'+h(r.send_error||'Open Communications to review and send the prepared message.')+'</div>';
 const qboState=r.quickbooks?'<div class="callout teal"><strong>QuickBooks draft created.</strong>Accounting received the estimate draft; CrownOps remains the client acceptance system.</div>':
   '<div class="callout orange"><strong>QuickBooks draft was not confirmed.</strong>'+h(r.quickbooks_error||'QuickBooks writes may still be locked in this environment. You can retry from the estimate after reviewing Connections.')+'</div>';
 showModal('Estimate finalized',emailState+'<div class="spacer"></div>'+qboState+'<div class="spacer"></div>'+
   '<p class="small muted">The private client link was created. Do not post it publicly.</p>'+textArea('link','Private View Estimate link',r.portal_url,3),
   '<button data-page="communications" class="btn-primary">Open Communications</button>'+btn('close','Close'));
}
async function crmDepositDialog(qid){
 showModal('Deposit / booking','<form id="crm-deposit-form" data-crm-form="deposit"><input type="hidden" name="quote_id" value="'+h(qid)+'"><div class="grid2">'+
   select('method','Method',[['Interac e-Transfer','Interac e-Transfer'],['Cash','Cash'],['Cheque','Cheque']],'Interac e-Transfer')+
   select('mode','Status',[['received','Payment received and verified'],['due_on_service','Approve booking · payment due on service day']],'received')+
   field('amount','Amount received',300,'number','step="0.01" min="0.01"')+field('reference','Transfer / receipt / cheque reference')+
   '</div><p class="small muted">Use “due on service day” only for an approved cash/cheque arrangement. CrownOps will confirm the service but will not pretend money was received.</p></form>',
   btn('close','Cancel')+'<button type="submit" form="crm-deposit-form" class="btn-primary">Save deposit status</button>');
}
async function crmTemplates(){
 const t=await api('/crm/templates');showModal('Quote templates',
   '<div class="grid2">'+t.map(x=>'<div class="panel panel-body"><div class="eyebrow">'+h(x.service_type)+' · '+h(x.language)+'</div><h3>'+h(x.name)+'</h3><p class="small muted">'+h(x.body?.liability||'')+'</p><p class="tiny">'+h(x.body?.blank_notes||x.body?.fragile||'')+'</p></div>').join('')+'</div>',
   btn('close','Close'),true);
}

document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-crm]');if(!b)return;e.preventDefault();const a=b.dataset.crm,id=b.dataset.id;
 try{
  if(a==='quote-new')return crmChooseClient();
  if(a==='client-new')return crmNewClient();
  if(a==='client-open'){if(modal.open)modal.close();return crmOpenClient(id);}
  if(a==='client-edit')return crmEditClient(id);
  if(a==='duplicate-open'){if(modal.open)modal.close();return crmOpenClient(id);}
  if(a==='duplicate-dismiss'){await api('/crm/duplicates/'+encodeURIComponent(id)+'/dismiss',{method:'POST'});toast('Duplicate warning dismissed.');return crmOpenClient(b.dataset.client);}
  if(a==='service-new')return crmNewService(id);
  if(a==='quote-from-service')return crmQuoteBuilder(id);
  if(a==='quote-open')return crmOpenQuote(id);
  if(a==='templates')return crmTemplates();
  if(a==='quote-calc'){const d=crmQuoteData();if(!d)return;return crmRenderCalc(await api('/crm/quotes/calculate',{method:'POST',data:d}));}
  if(a==='quote-finalize')return crmFinalize(id);
  if(a==='deposit-record')return crmDepositDialog(id);
  if(a==='deposit-remind'){const r=await api('/crm/quotes/'+encodeURIComponent(id)+'/deposit-reminder',{method:'POST'});toast('Friendly deposit reminder drafted in Communications.');return;}
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}
});

document.addEventListener('input',async e=>{
 if(e.target.id==='crm-quote-client-search'){
   try{const r=await crmSearchClients(e.target.value);const box=document.getElementById('crm-client-results');if(box)box.innerHTML=r.length?r.map(crmClientCard).join(''):empty('No client found','Try another name, email, phone or address, or create a new client.');}catch(ex){}
 }
 if(e.target.id==='crm-client-search'){
   CRM.clientSearch=e.target.value;
 }
});

document.addEventListener('change',async e=>{
 if(e.target.classList.contains('crm-mat-check')){const q=document.querySelector('.crm-mat-qty[data-code="'+CSS.escape(e.target.dataset.code)+'"]');if(q)q.disabled=!e.target.checked;}
 if(e.target.closest('#crm-quote-form')){
   const f=document.getElementById('crm-quote-form');
   if(e.target.name==='service_type'){crmToggleServiceFields();const type=e.target.value,lang=f.elements.language.value;const t=CRM.templates.find(x=>x.service_type===type&&x.language===lang);if(t){f.elements.template_key.innerHTML='<option value="'+h(t.template_key)+'">'+h(t.name)+'</option>';}}
   if(e.target.name==='language'){const type=f.elements.service_type.value,lang=e.target.value;const t=CRM.templates.find(x=>x.service_type===type&&x.language===lang);if(t){f.elements.template_key.innerHTML='<option value="'+h(t.template_key)+'">'+h(t.name)+'</option>';}}
   if(e.target.name==='distance_preset'){
     const p=(CRM.longDistancePresets||[]).find(x=>x.key===e.target.value);
     if(p){f.elements.distance_km.value=String(p.round_trip_km);toast(p.city+' preset loaded: '+p.round_trip_km+' km actual round trip; CrownOps adds the 5% distance buffer automatically.');}
   }
   if(e.target.name==='start_window'){
     crmToggleStartTime();
     if(e.target.value!=='afternoon'&&f.elements.small_afternoon_job?.checked){f.elements.small_afternoon_job.checked=false;f.elements.minimum_hours.value='3';toast('Small afternoon job was turned off because the arrival window changed.');}
   }
   if(e.target.name==='small_afternoon_job'){
     if(e.target.checked){f.elements.start_window.value='afternoon';f.elements.minimum_hours.value='1.5';crmToggleStartTime();toast('Afternoon window selected with a 1.5-hour service minimum.');}
     else{f.elements.minimum_hours.value='3';}
   }
   if(e.target.name==='movers'){
     const movers=Number(e.target.value),minimumTrucks=movers>=6?3:(movers>=4?2:1);
     if(Number(f.elements.trucks.value)<minimumTrucks){
       f.elements.trucks.value=String(minimumTrucks);
       toast(movers+' movers requires at least '+minimumTrucks+' truck'+(minimumTrucks===1?'':'s')+'. Truck count set to '+minimumTrucks+'.');
     }
   }
   if((e.target.name==='move_date'||e.target.name==='movers')&&f.elements.service_type.value!=='Junk Removal'){try{await crmRefreshRate();}catch(ex){toast(ex.message);}}
 }
});

document.addEventListener('keydown',e=>{
 if(e.key==='Enter'&&e.target.id==='crm-client-search'){e.preventDefault();navigate('sales');}
});

document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-crm-form]');if(!f)return;e.preventDefault();const kind=f.dataset.crmForm,d=Object.fromEntries(new FormData(f));
 try{
   if(kind==='client'){
     const created=await api('/crm/clients',{method:'POST',data:d});
     if(created.duplicate){
       const rows=(created.candidates||[]).map(x=>'<button type="button" class="search-result crm-client-result" data-crm="client-open" data-id="'+h(x.id)+'"><div class="avatar">'+h(initials(x.client_name))+'</div><div class="grow"><div class="strong">'+h(x.client_name)+'</div><div class="tiny muted">'+h([x.email,x.phone].filter(Boolean).join(' · '))+'</div><div class="tiny muted">'+h((x.reasons||[]).join(', '))+'</div></div>'+icon('right')+'</button>').join('');
       return showModal('Possible existing client','<div class="callout orange"><strong>We found matching contact information.</strong>Open the existing client instead of creating a duplicate. If this truly is a different person, change the email/phone so CrownOps can keep the records separate.</div><div class="spacer"></div>'+rows,btn('close','Cancel'),true);
     }
     modal.close();CRM.currentClient=created;return crmNewService(created.id);
   }
   if(kind==='client-edit'){const cid=d.client_id;delete d.client_id;await api('/crm/clients/'+encodeURIComponent(cid),{method:'PATCH',data:d});toast('Client contact saved.');modal.close();return crmOpenClient(cid);}
   if(kind==='service'){const cid=d.client_id;delete d.client_id;const r=await api('/crm/clients/'+encodeURIComponent(cid)+'/services',{method:'POST',data:d});modal.close();return crmQuoteBuilder(r.lead_id);}
   if(kind==='quote'){const qd=crmQuoteData();if(!qd)return;await api('/crm/quotes/calculate',{method:'POST',data:qd});const q=await api('/crm/quotes',{method:'POST',data:qd});toast('Draft estimate saved.');modal.close();return crmOpenQuote(q.id);}
   if(kind==='deposit'){const qid=d.quote_id;delete d.quote_id;d.amount=Number(d.amount||0);const r=await api('/crm/quotes/'+encodeURIComponent(qid)+'/manual-deposit',{method:'POST',data:d});toast(r.confirmed?'Booking confirmed. Confirmation email is prepared.':'Partial deposit saved.');modal.close();return crmOpenQuote(qid);}
 }catch(ex){modalError(ex);}
});

// Legacy quote-entry bridge: every estimate action now enters the v6 CRM workflow.
if(typeof chooseLead==='function')chooseLead=function(action){return action==='ai'?crmChooseClient():crmChooseClient();};
if(typeof newQuote==='function')newQuote=function(leadId){return crmQuoteBuilder(leadId);};
if(typeof openQuote==='function')openQuote=function(qid){return crmOpenQuote(qid);};
