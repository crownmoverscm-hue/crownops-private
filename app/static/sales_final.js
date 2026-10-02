/* CrownOps v5.7 launch sales workflow: client-first CRM + simple estimates. */
'use strict';

const SF={clients:[],clientAction:'quote',templates:null,defaults:{},materials:[],delivery:null,reviewMail:null,installPrompt:null};

window.addEventListener('beforeinstallprompt',e=>{
 e.preventDefault();SF.installPrompt=e;
 const b=document.getElementById('sf-install-app');if(b)b.hidden=false;
});
const sfOriginalLoginScreen=loginScreen;
loginScreen=function(){
 sfOriginalLoginScreen();
 const host=document.querySelector('.login-form');if(!host)return;
 const isApple=/iPhone|iPad|iPod/i.test(navigator.userAgent);
 const note=document.createElement('div');note.className='callout';note.style.marginTop='18px';
 note.innerHTML=isApple
  ?'<strong>Put CrownOps on your Home Screen</strong>On iPhone/iPad: open this page in Safari, tap Share, then Add to Home Screen. It opens like an app with its own CrownOps icon.'
  :'<strong>Install CrownOps on this device</strong><button id="sf-install-app" data-sf="install-app" class="btn-soft" style="margin-top:8px" '+(SF.installPrompt?'':'hidden')+'>Install CrownOps</button><div class="tiny muted" style="margin-top:7px">On Samsung/Android you can also use the browser menu → Add to Home screen / Install app.</div>';
 host.appendChild(note);
};

function sfMoneyNumber(v){const n=Number(v||0);return Number.isFinite(n)?n:0;}
function sfSuggestion(size){
 const s=String(size||'').toLowerCase();
 if(/studio|bachelor/.test(s))return [2.5,4];
 if(/1\s*bed|1\s*chambre|3\s*1\/2/.test(s))return [3,5];
 if(/2\s*bed|2\s*chambre|4\s*1\/2/.test(s))return [4,6];
 if(/3\s*bed|3\s*chambre|5\s*1\/2/.test(s))return [5,8];
 if(/4\s*bed|4\s*chambre|6\s*1\/2/.test(s))return [7,10];
 return [3,6];
}
function sfTemplate(id){return SF.templates?.find(x=>x.id===id)||SF.templates?.[0]||{};}
function sfCoreMaterials(){
 const find=(re)=>SF.materials.find(x=>x.active&&re.test(String(x.name||'').toLowerCase()));
 const plastic=find(/plastic|mattress|matelas/), bubble=find(/bubble|bulle/), mirror=find(/mirror|miroir/);
 return [
  {code:'plastic-protection',name:'Plastic protection / protecteur en plastique',price:(plastic?.sell_cents||0)/100},
  {code:'bubble-wrap',name:'Bubble wrap / papier bulle',price:(bubble?.sell_cents||0)/100},
  {code:'tv-protection',name:'TV protection package / protection téléviseur',price:35},
  {code:'mirror-protection',name:'Large mirror box/protection / boîte-protection grand miroir',price:(mirror?.sell_cents||0)/100},
 ];
}
function sfMaterialRows(){
 return sfCoreMaterials().map((m,i)=>`<div class="sf-material-row">
   <label class="checkbox-label"><input type="checkbox" class="sf-material-on" data-i="${i}"><span>${h(m.name)}</span></label>
   <input class="sf-material-qty" type="number" min="0" step="1" value="1" aria-label="Quantity">
   <input class="sf-material-price" type="number" min="0" step="0.01" value="${m.price.toFixed(2)}" aria-label="Unit price">
 </div>`).join('');
}
function sfClientCard(c,action){
 const latest=c.latest||{};
 return `<button class="sf-client-card" data-sf="select-client" data-id="${h(c.id)}" data-service="${h(latest.id||c.id)}" data-next="${h(action)}">
   <div class="avatar">${h(initials(c.client_name))}</div>
   <div class="grow"><div class="client-name">${h(c.client_name)}</div>
   <div class="tiny muted">${h(c.email||'No email')} &middot; ${h(c.phone||'No phone')}</div>
   <div class="tiny muted">${h(latest.move_date?dateLabel(latest.move_date):'No move date')} &middot; ${h(latest.pickup||'Pickup needed')}${latest.dropoff?' → '+h(latest.dropoff):''}</div></div>
   <span class="pill gray">${Number(c.service_count||1)} service${Number(c.service_count||1)===1?'':'s'}</span>${icon('right')}
 </button>`;
}
async function sfSearchClients(q='',action='quote'){
 SF.clients=await api('/ux/clients?q='+encodeURIComponent(q));
 const box=document.getElementById('sf-client-results');
 if(box)box.innerHTML=SF.clients.length?SF.clients.map(c=>sfClientCard(c,action)).join(''):empty('No matching client','Try the name, email, phone or address, or create a new client.');
}

const sfBaseSales=sales;
sales=async function(){
 const clients=await api('/ux/clients?q='+encodeURIComponent(S.search||''));
 const rows_=clients.map(c=>{
  const l=c.latest||{};
  return `<tr><td><div class="row"><div class="avatar">${h(initials(c.client_name))}</div><div><div class="client-name">${h(c.client_name)}</div><div class="client-sub">${h(c.email||'')} ${c.phone?'· '+h(c.phone):''}</div></div></div></td>
  <td><div>${h(l.move_date?dateLabel(l.move_date):'No active date')}</div><div class="client-sub">${h(l.pickup||'No pickup yet')}${l.dropoff?' → '+h(l.dropoff):''}</div></td>
  <td>${pill(l.stage||'Client')}</td><td><span class="pill gray">${Number(c.service_count||1)} service${Number(c.service_count||1)===1?'':'s'}</span></td>
  <td>${btn('open-lead',icon('right'),'btn-text',`data-id="${h(c.id)}" aria-label="Open client"`)}</td></tr>`;
 }).join('');
 return head('Sales & clients','Search one client file by name, email, phone or address. New services stay in the same client history.',btn('new-lead',icon('plus')+'New client','btn-primary'))+
 `<div class="filterbar"><div class="searchbox sf-sales-search">${icon('search')}<input id="sales-search" placeholder="Search name, email, phone or address" value="${h(S.search||'')}" aria-label="Search clients"></div><div class="grow"></div><span class="tiny muted">${clients.length.toLocaleString()} clients shown</span></div>
 <div class="panel"><div class="table-wrap"><table><thead><tr><th>Client</th><th>Latest service</th><th>Stage</th><th>History</th><th></th></tr></thead><tbody>${rows_}</tbody></table></div>${rows_?'':empty('No clients found','Create a new client or change the search.')}</div>`;
};

chooseLead=async function(action){
 SF.clientAction=action;
 showModal(action==='ai'?'Choose a client for AI assistance':'Choose a client for a new estimate',
 `<div class="sf-client-picker-head"><div class="searchbox sf-client-search">${icon('search')}<input id="sf-client-search" placeholder="Search name, email, phone or address" autocomplete="off"></div>
 <button class="btn-soft" data-action="new-lead">${icon('plus')}New client</button></div>
 <p class="tiny muted">Choose an existing service, or open the client file and create a new service if this is a returning move.</p>
 <div id="sf-client-results" class="sf-client-results"><div class="startup">Loading clients...</div></div>`,
 btn('close','Cancel'),true);
 await sfSearchClients('',action);
};

const sfBaseOpenLead=openLead;
openLead=async function(id){
 const d=await api('/ux/clients/'+encodeURIComponent(id)),c=d.client,services=d.services||[];
 S.modalContext={lead:{id:c.id,client_name:c.client_name,email:c.email,phone:c.phone}};
 const cards=services.map((s,idx)=>{
  const newest=idx===0;
  const quote=s.quotes?.[0];
  return `<div class="sf-service-card">
   <div class="sf-service-top"><div><div class="eyebrow">${newest?'LATEST SERVICE':'SERVICE HISTORY'}</div><h3>${h(s.move_date?dateLabel(s.move_date):'Date to confirm')}</h3></div>${pill(s.stage||'New Lead')}</div>
   <div class="small">${h(s.pickup||'Pickup needed')} <span class="muted">→</span> ${h(s.dropoff||'Destination needed')}</div>
   <div class="tiny muted" style="margin-top:5px">${h(s.home_size||'Move size needed')}${s.boxes?' · '+h(s.boxes)+' boxes':''}</div>
   <div class="row wrap" style="margin-top:12px">
    ${btn('edit-lead','Edit service','btn-small',`data-id="${h(s.id)}"`)}
    ${btn('new-quote',quote?'New estimate revision':'Build estimate','btn-primary btn-small',`data-id="${h(s.id)}"`)}
    ${quote?btn('open-quote','Open '+h(quote.status),'btn-soft btn-small',`data-id="${h(quote.id)}"`):''}
   </div>
  </div>`;
 }).join('');
 showModal(h(c.client_name),`<section class="sf-client-header">
   <div class="row wrap"><div class="avatar">${h(initials(c.client_name))}</div><div class="grow"><h3>${h(c.client_name)}</h3><div class="small muted">${h(c.email||'No email')} &middot; ${h(c.phone||'No phone')}</div></div>
   <button class="btn-primary" data-sf="new-service" data-id="${h(c.id)}">${icon('plus')}New service</button></div>
   <p class="tiny muted" style="margin-top:12px">Contact information stays together. Each new move, packing service or junk-removal request is saved below as its own service history.</p>
  </section><div class="spacer"></div><div class="sf-services">${cards||empty('No services yet','Create the client\'s first service.')}</div>`,
 btn('close','Close'),true);
};

function sfDefaultTemplateForLead(l){
 return String(l.language||'').toLowerCase().startsWith('fr')?'moving-fr':'moving-en';
}
async function sfLoadRate(){
 const f=document.getElementById('quote-form');if(!f)return;
 const date=f.elements.move_date.value,movers=f.elements.movers.value;
 if(!date||!movers)return;
 try{
  const r=await api('/ux/rate-preview?move_date='+encodeURIComponent(date)+'&movers='+encodeURIComponent(movers));
  f.elements.pricing_id.value=r.pricing_id;
  f.elements.minimum_key.value=r.minimum_key;
  f.elements.fuel_key.value=r.fuel_key;
  f.elements.hourly_rate_override.value=(r.hourly_rate_cents/100).toFixed(2);
  f.elements.trucks.value=String(r.default_trucks);
  const note=document.getElementById('sf-rate-note');if(note)note.textContent=`${r.season} · standard rate ${money(r.hourly_rate_cents)}/h · editable`;
 }catch(ex){const n=document.getElementById('sf-rate-note');if(n)n.textContent=ex.message;}
}
function sfApplyTemplate(){
 const f=document.getElementById('quote-form');if(!f)return;
 const t=sfTemplate(f.elements.template_id.value);
 f.elements.service_type.value=t.service_type||'Moving';
 f.elements.template_language.value=t.language||'English';
 f.elements.liability_text.value=t.liability_text||'';
 f.elements.fragile_text.value=t.fragile_text||'';
 f.elements.deposit_required.value=t.default_deposit??300;
 const long=Boolean(t.long_distance),junk=t.service_type==='Junk Removal';
 document.querySelectorAll('.sf-long-distance').forEach(x=>{x.hidden=!long;x.querySelectorAll('input,select,textarea').forEach(i=>i.disabled=!long);});
 document.querySelectorAll('.sf-hourly-service').forEach(x=>{x.hidden=junk;x.querySelectorAll('input,select,textarea').forEach(i=>i.disabled=junk);});
 const junkBox=document.getElementById('sf-junk-box');if(junkBox){junkBox.hidden=!junk;junkBox.querySelectorAll('input,select,textarea').forEach(i=>i.disabled=!junk);}
 if(long&&f.elements.long_distance_gas_per_km)f.elements.long_distance_gas_per_km.value=String(SF.defaults.long_distance_gas_per_km_per_truck??0.94);
}
function sfQuoteData(){
 const f=document.getElementById('quote-form');if(!f||!f.reportValidity())return null;
 const d=readForm(f);
 const numeric=['lead_version','movers','trucks','hours_low','hours_high','allowance_percent','transport_hours','hourly_rate_override','extra_truck_transport_fee','long_distance_gas_per_km','junk_base_amount','junk_extra_half_hours','other_amount','discount_amount','deposit_required'];
 numeric.forEach(k=>{if(d[k]!==''&&d[k]!==undefined)d[k]=Number(d[k]);});
 if(d.transport_fixed_amount!=='')d.transport_fixed_amount=Number(d.transport_fixed_amount);
 if(d.long_distance_km!=='')d.long_distance_km=Number(d.long_distance_km);
 const lines=[];
 document.querySelectorAll('.sf-material-row').forEach(row=>{
   if(!row.querySelector('.sf-material-on').checked)return;
   const name=row.querySelector('.checkbox-label span').textContent;
   const qty=Math.max(0,Number(row.querySelector('.sf-material-qty').value||0));
   const unit=Math.max(0,Number(row.querySelector('.sf-material-price').value||0));
   if(qty>0)lines.push({name,quantity:qty,unit_price:unit,line_total:Math.round(qty*unit*100)/100});
 });
 d.materials_lines=lines;
 d.materials_amount=Math.round(lines.reduce((n,x)=>n+x.line_total,0)*100)/100;
 if(d.service_type!=='Junk Removal'){delete d.junk_base_amount;delete d.junk_extra_half_hours;}
 return d;
}
quoteData=sfQuoteData;

function sfQuoteSummary(r){
 if(!r)return `<div class="quote-summary"><div class="eyebrow">CLIENT ESTIMATE PREVIEW</div><div class="quote-price">Ready when you are</div><p class="small muted">Choose the service, date and crew. CrownOps fills the standard rate; every commercial amount remains editable.</p></div>`;
 if(r.service_type==='Junk Removal')return `<div class="quote-summary"><div class="eyebrow">JUNK REMOVAL ESTIMATE · CAD</div><div class="quote-price">${money(r.low_cents)}</div><div class="tiny muted">Before applicable taxes</div>
  <div class="spacer"></div><div class="quote-line"><span>Selected volume / disposal</span><b>${money(r.junk_base_cents||0)}</b></div>
  <div class="quote-line"><span>Extra on-site labour</span><b>${money(r.other_cents||0)}</b></div>
  <div class="quote-line"><span>Materials / other</span><b>${money(r.materials_cents||0)}</b></div>
  <div class="quote-line"><span>Discount</span><b>-${money(r.discount_cents||0)}</b></div></div>`;
 const mats=(r.materials_lines||[]).map(x=>`<div class="quote-line"><span>${h(x.name)} × ${h(x.quantity)}</span><b>${money(Math.round(Number(x.line_total||0)*100))}</b></div>`).join('');
 return `<div class="quote-summary"><div class="eyebrow">ESTIMATED RANGE · CAD</div><div class="quote-price">${compact(r.low_cents)} – ${compact(r.high_cents)}</div><div class="tiny muted">Before applicable taxes · actual time billed under accepted terms</div>
 <div class="spacer"></div><div class="quote-line"><span>${h(r.service_type||'Moving')} · ${r.service_low}–${r.service_high} h</span><b>${compact(r.service_low_cents)}–${compact(r.service_high_cents)}</b></div>
 <div class="quote-line"><span>Transportation fee</span><b>${money(r.transport_cents)}</b></div>
 <div class="quote-line"><span>${r.long_distance_km?('Long-distance gas · '+r.long_distance_km+' km × $'+Number(r.long_distance_gas_per_km).toFixed(2)+' × '+r.trucks+' truck(s)'):('Gas · '+r.trucks+' truck(s)')}</span><b>${money(r.fuel_cents)}</b></div>
 ${mats||`<div class="quote-line"><span>Materials</span><b>${money(r.materials_cents||0)}</b></div>`}
 ${r.other_cents?`<div class="quote-line"><span>Other agreed charges</span><b>${money(r.other_cents)}</b></div>`:''}
 ${r.discount_cents?`<div class="quote-line"><span>Discount</span><b>-${money(r.discount_cents)}</b></div>`:''}
 <div class="spacer"></div><div class="tiny muted">${r.movers} movers · ${r.trucks} truck(s) · ${money(r.hourly_rate_cents)}/h<br>Minimum: ${r.minimum_hours} service hours<br>Free limited liability coverage: $0.60/lb per article</div></div>`;
}
quoteSummary=sfQuoteSummary;

newQuote=async function(lid){
 const [l,prices,tpl,mats]=await Promise.all([
  api('/leads/'+encodeURIComponent(lid)),api('/pricing'),api('/ux/quote-templates'),api('/materials').catch(()=>[])
 ]);
 if(!prices.length){toast('Import the Crown Movers rate table first.');return navigate('rates');}
 SF.templates=tpl.templates||[];SF.defaults=tpl.defaults||{};SF.materials=mats||[];
 const tId=sfDefaultTemplateForLead(l),suggested=sfSuggestion(l.home_size),p=prices[0];
 S.modalContext={lead:l,prices,lastResult:null};
 showModal('Build estimate',`<div class="row"><div class="avatar">${h(initials(l.client_name))}</div><div class="grow"><div class="strong">${h(l.client_name)}</div><div class="tiny muted">${h(l.email||'Email needed')} · ${h(l.phone||'Phone needed')}</div></div><span class="pill purple">Draft</span></div>
 <div class="spacer"></div><div class="quote-layout"><form id="quote-form" data-form="quote">
  <input type="hidden" name="lead_id" value="${h(lid)}"><input type="hidden" name="lead_version" value="${l.version}">
  <input type="hidden" name="pricing_id" value="${h(p.id)}"><input type="hidden" name="minimum_key" value=""><input type="hidden" name="fuel_key" value="">
  <input type="hidden" name="allowance_percent" value="0"><input type="hidden" name="service_type" value="Moving">
  <input type="hidden" name="template_language" value="English"><input type="hidden" name="liability_text" value=""><input type="hidden" name="fragile_text" value="">
  <input type="hidden" name="extra_truck_transport_fee" value="${Number(SF.defaults.extra_truck_transport_fee??25)}">
  <div class="grid2">
   ${select('template_id','Quote template',SF.templates.map(x=>[x.id,x.name]),tId,'required')}
   ${field('move_date','Service date',l.move_date,'date','required')}
   ${select('movers','Crew size',[2,3,4,5,6].map(x=>[x,x+' movers']),3,'required')}
   ${select('trucks','Truck count',[1,2,3,4].map(x=>[x,x+' truck'+(x===1?'':'s')]),1,'required')}
   <div class="form-field sf-hourly-service"><label>Hourly rate (editable)</label><div class="sf-money-input"><span>$</span><input name="hourly_rate_override" type="number" step="0.01" min="1" required></div><small id="sf-rate-note">Choose a date to load the standard rate.</small></div>
   <div class="form-field sf-hourly-service"><label>Estimated service hours</label><div class="sf-inline-pair"><input name="hours_low" type="number" step="0.25" min="0.25" value="${suggested[0]}" required><span>to</span><input name="hours_high" type="number" step="0.25" min="0.25" value="${suggested[1]}" required></div><small>Starting suggestion from move size. Edit after reviewing inventory/access.</small></div>
   <div class="form-field sf-hourly-service"><label>Transportation calculation</label><div class="sf-inline-pair"><input name="transport_hours" type="number" step="0.25" min="0" value="1"><span>hours</span></div><small>Local default: 1 × hourly rate. Each additional truck adds $25 unless you enter a fixed amount.</small></div>
   ${field('transport_fixed_amount','Fixed transportation fee (optional)','','number','step="0.01" min="0" placeholder="Leave blank to calculate from hours"')}
   <div class="form-field sf-long-distance" hidden><label>Long-distance route kilometres</label><input name="long_distance_km" type="number" step="1" min="0" placeholder="Base → pickup → destination → base"><small>Gas defaults to $0.94/km per truck and remains editable.</small></div>
   <div class="form-field sf-long-distance" hidden><label>Gas rate per km / truck</label><div class="sf-money-input"><span>$</span><input name="long_distance_gas_per_km" type="number" step="0.01" min="0" value="${Number(SF.defaults.long_distance_gas_per_km_per_truck??0.94).toFixed(2)}"></div></div>
   ${field('other_amount','Other agreed charges',0,'number','step="0.01" min="0"')}
   ${field('discount_amount','Discount',0,'number','step="0.01" min="0"')}
   ${field('deposit_required','Required deposit',300,'number','step="0.01" min="0"')}
  </div>
  <div id="sf-junk-box" class="panel panel-body" hidden><div class="eyebrow">JUNK REMOVAL GUIDE</div><div class="grid2">
    ${select('junk_base_amount','Truck space / disposal',[['175','Single item — $175'],['400','1/4 truck (~5 ft) — $400'],['800','1/2 truck (~10 ft) — $800'],['1200','3/4 truck (~15 ft) — $1,200'],['1600','Full 20 ft truck — $1,600']],'400')}
    ${field('junk_extra_half_hours','Extra 30-minute labour blocks',0,'number','min="0" step="1"')}
   </div><p class="tiny muted">Extra on-site labour is $80 per 30 minutes for a team of 2 crewmen.</p></div>
  <div class="spacer"></div><div class="panel panel-body"><div class="row"><div><div class="eyebrow">PROTECTION / MATERIALS</div><h3>Add only what applies</h3></div><div class="grow"></div><span class="tiny muted">Quantity · unit price</span></div>
   <div class="sf-materials">${sfMaterialRows()}</div>
   <p class="tiny muted" style="margin-top:10px">TVs, large mirrors and fragile glass require suitable protection/boxing. Protection charges do not change the standard $0.60/lb limited-liability coverage unless separate coverage is accepted.</p>
  </div>
  ${textArea('internal_note','Internal notes / assumptions','',3)}
 </form><div><div id="quote-result">${sfQuoteSummary(null)}</div><div class="spacer"></div>
  <div class="callout ${l.missing.length?'orange':'teal'}"><strong>${l.missing.length?'Details still missing':'Ready to price'}</strong>${h(l.missing.join(', ')||'Review inventory, access, route, parking/elevators and fragile items before finalizing.')}</div>
  <div class="spacer"></div><div class="callout"><strong>Simple rule</strong>The standard date/crew rate loads automatically. You can change the hourly rate, service hours, transportation, gas inputs, materials, discount and deposit before saving.</div>
 </div></div>`,
 `${btn('calculate-quote',icon('refresh')+'Calculate','btn-soft')}<button class="btn-primary" type="submit" form="quote-form">Save estimate draft</button>`,true);
 sfApplyTemplate();
 if(l.move_date)await sfLoadRate();
};

openQuote=async function(qid){
 const [qs,conn]=await Promise.all([api('/quotes'),qboState()]),q=qs.find(x=>x.id===qid);
 if(!q)throw Error('Estimate not found.');
 S.modalContext={quote:q,qbo:conn};
 const canFinalize=may(['owner','sales','dispatch']);
 const qboButton=qboReady(conn)&&['Approved','Accepted'].includes(q.status)?btn('qbo-estimate','Create QuickBooks draft','btn-soft',`data-id="${h(qid)}"`):'';
 let action='';
 if(q.status==='Draft'&&canFinalize)action=`<form id="approve-quote-form" data-form="approve-quote"><input type="hidden" name="id" value="${h(qid)}">${textArea('note','Final review note','Inventory, access, route, timing and pricing reviewed.',2)}<label class="checkbox-label"><input name="reviewed" type="checkbox" required>I reviewed the scope and pricing. Finalizing does not send anything yet.</label><div class="spacer"></div><button class="btn-primary" type="submit">Finalize estimate</button></form>`;
 if(q.status==='Approved')action=`<div class="callout teal"><strong>Ready for the client</strong>Prepare the branded email. The client will open the private CrownOps estimate, accept it, then see deposit options.</div><div class="spacer"></div><button class="btn-primary" data-sf="prepare-delivery" data-id="${h(qid)}">${icon('mail')}Prepare client email</button>`;
 if(q.status==='Accepted')action=`<div class="callout teal"><strong>Accepted by client</strong>A tentative schedule hold is created from the accepted move date. Once the deposit is verified, CrownOps marks the booking locked while crew/truck assignment can still be completed.</div><div class="spacer"></div><div class="row wrap"><button class="btn-primary" data-sf="deposit-reminder" data-id="${h(qid)}">${icon('mail')}Prepare deposit reminder</button><button class="btn-soft" data-sf="booking-exception" data-id="${h(qid)}">Cash / cheque booking arrangement</button></div>`;
 showModal('Estimate · '+h(q.client_name),`<div class="row"><div><div class="eyebrow">REVISION ${q.revision}</div><h3>${h(q.client_name)}</h3></div><div class="grow"></div>${pill(q.status)}</div><div class="spacer"></div>
 <div class="quote-layout"><div>${sfQuoteSummary(q.result)}<p class="source-warning">${h(q.result.tax_note||'Before applicable taxes.')}</p></div>
 <div class="stack"><div class="panel panel-body"><div class="info-grid"><div><div class="info-label">Service date</div><div class="strong">${dateLabel(q.inputs.move_date)}</div></div><div><div class="info-label">Deposit</div><div class="strong">${money(Number(q.inputs.deposit_required??300)*100)}</div></div><div><div class="info-label">Template</div><div>${h(q.inputs.template_id||'Standard')}</div></div><div><div class="info-label">Language</div><div>${h(q.inputs.template_language||'Client preference')}</div></div></div></div>
 ${action}${qboButton}<button data-action="quote-text" data-id="${h(qid)}">${icon('mail')}Preview plain-text copy</button></div></div>`,btn('close','Close'),true);
};

const sfBaseOpenJob=openJob;
openJob=async function(id){
 await sfBaseOpenJob(id);
 if(!modal.open||!may(['owner','sales','dispatch']))return;
 try{
  const jobs=await api('/jobs'),j=jobs.find(x=>x.id===id);
  if(j?.status==='Completed'){
   const foot=modal.querySelector('.modal-foot');
   if(foot&&!foot.querySelector('[data-sf="prepare-review"]')){
    const b=document.createElement('button');b.className='btn-soft';b.dataset.sf='prepare-review';b.dataset.id=id;b.textContent='Prepare feedback / review request';foot.prepend(b);
   }
  }
 }catch{}
};

document.addEventListener('input',e=>{
 if(e.target.id==='sf-client-search'){
  clearTimeout(SF.searchTimer);SF.searchTimer=setTimeout(()=>sfSearchClients(e.target.value,SF.clientAction).catch(ex=>toast(ex.message)),180);
 }
});
document.addEventListener('change',e=>{
 const f=e.target.closest('#quote-form');if(!f)return;
 if(e.target.name==='template_id')sfApplyTemplate();
 if(e.target.name==='move_date'||e.target.name==='movers')sfLoadRate();
 if(e.target.name==='movers'){
  const n=Number(e.target.value);if(n>=4)f.elements.trucks.value='2';
 }
});

document.addEventListener('click',async e=>{
 const el=e.target.closest('[data-sf]');if(!el)return;e.preventDefault();
 const a=el.dataset.sf,id=el.dataset.id;
 try{
  if(a==='install-app'){
   if(SF.installPrompt){SF.installPrompt.prompt();await SF.installPrompt.userChoice;SF.installPrompt=null;el.hidden=true;}
   return;
  }
  if(a==='select-client'){modal.close();const service=el.dataset.service||id;return el.dataset.next==='ai'?aiCompose(service):newQuote(service);}
  if(a==='new-service'){
   const r=await api('/ux/clients/'+encodeURIComponent(id)+'/new-service',{method:'POST',data:{}});
   toast('New service created. Add the new move details.');
   return editLead(r.id);
  }
  if(a==='prepare-delivery'){
   const r=await api('/ux/quotes/'+encodeURIComponent(id)+'/prepare-delivery',{method:'POST',data:{}});
   SF.delivery=r;
   return showModal('Client estimate email',`<div class="callout teal"><strong>Ready to send</strong>This branded email opens the client\'s private estimate page. Acceptance and deposit options happen there.</div><div class="spacer"></div>
   ${field('sf_subject','Subject',r.subject)}${textArea('sf_body','Message',r.body,12)}
   <div class="spacer"></div><label class="checkbox-label"><input id="sf-send-reviewed" type="checkbox">I reviewed the recipient, dates and message.</label>
   ${r.communication_suppressed?'<label class="checkbox-label"><input id="sf-contact-confirmed" type="checkbox">The client requested/authorized this operational estimate email.</label>':''}`,
   `${btn('close','Cancel')}<button class="btn-primary" data-sf="send-mail" data-id="${h(r.mail_id)}">Send estimate email</button>`,true);
  }
  if(a==='send-mail'){
   if(!document.getElementById('sf-send-reviewed')?.checked)throw Error('Check the review box before sending.');
   const out=await api('/ux/outbound/'+encodeURIComponent(id)+'/send-reviewed',{method:'POST',data:{reviewed:true,contact_confirmed:document.getElementById('sf-contact-confirmed')?.checked===true}});
   modal.close();toast(out.sent?'Email sent.':'Email was not confirmed as sent.');return navigate('quotes');
  }
  if(a==='deposit-reminder'){
   const r=await api('/ux/quotes/'+encodeURIComponent(id)+'/prepare-deposit-reminder',{method:'POST'});
   return showModal('Deposit reminder',`<div class="callout teal"><strong>Friendly reminder ready</strong>Review the amount and recipient before sending.</div><div class="spacer"></div>
    ${field('sf_subject','Subject',r.subject)}${textArea('sf_body','Message',r.body,10)}
    <label class="checkbox-label"><input id="sf-send-reviewed" type="checkbox">I reviewed this reminder and want to send it.</label>`,
    `${btn('close','Cancel')}<button class="btn-primary" data-sf="send-mail" data-id="${h(r.mail_id)}">Send reminder</button>`,true);
  }
  if(a==='booking-exception'){
   return showModal('Approve cash / cheque arrangement',`<form id="sf-booking-exception" data-sf-form="booking-exception"><input type="hidden" name="id" value="${h(id)}">
   ${select('method','Payment arrangement',['Cash on move day','Cheque on move day'],'Cash on move day','required')}${textArea('note','Internal note','Client will pay on service day; deposit exception approved by manager.',3)}</form>`,
   `${btn('close','Cancel')}<button class="btn-primary" type="submit" form="sf-booking-exception">Lock booking</button>`);
  }
  if(a==='prepare-review'){
   const r=await api('/ux/jobs/'+encodeURIComponent(id)+'/prepare-review-request',{method:'POST'});
   SF.reviewMail=r;
   return showModal('Feedback / review request',`<div class="callout"><strong>Neutral request</strong>Send this same request regardless of whether you expect a positive or negative review. No reward is tied to a public rating.</div><div class="spacer"></div>
    ${field('sf_subject','Subject',r.subject)}${textArea('sf_body','Message',r.body,11)}
    <label class="checkbox-label"><input id="sf-send-reviewed" type="checkbox">I reviewed this message and want to send it.</label>`,
    `${btn('close','Cancel')}<button class="btn-primary" data-sf="send-mail" data-id="${h(r.mail_id)}">Send request</button>`,true);
  }
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}
});

document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-sf-form]');if(!f)return;e.preventDefault();
 try{
  if(f.dataset.sfForm==='booking-exception'){
   const d=readForm(f),qid=d.id;delete d.id;
   await api('/ux/quotes/'+encodeURIComponent(qid)+'/booking-exception',{method:'POST',data:d});
   modal.close();toast('Booking locked with the payment arrangement recorded.');return navigate('schedule');
  }
 }catch(ex){modalError(ex);}
});
