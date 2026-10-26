/* Template-first estimates. Nothing is saved or sent until the user saves a draft. */
'use strict';
const QE={client:null,mode:'',locked:false,searchVersion:0,serviceVersion:0,busy:false,key:''};
const qeBaseBuilder=crmQuoteBuilder;
const qeServiceFields=['pickup','dropoff','home_size','boxes','pickup_access','dropoff_access','pickup_walk','dropoff_walk','inventory','heavy_items'];

crmTemplates=async function(){
 const templates=await api('/crm/templates');CRM.templates=templates;
 showModal('Choose a quote template',
  '<p class="muted">Choose a starting point. Edit the quote and select or add the client on the same sheet.</p>'+
  '<div class="grid2 qe-templates">'+templates.map(t=>
   '<button type="button" class="qe-template" data-editor="template" data-id="'+h(t.template_key)+'">'+
   '<span class="eyebrow">'+h(t.service_type)+' · '+h(t.language)+'</span><strong>'+h(t.name)+'</strong>'+
   '<span class="small muted">'+h(t.body?.liability||'')+'</span>'+
   '<span class="small">'+h(t.body?.blank_notes||t.body?.fragile||'')+'</span><span class="qe-template-action">Use template '+icon('right')+'</span></button>'
  ).join('')+'</div>',btn('close','Cancel'),true);
};
crmChooseClient=async function(){return crmTemplates();};

function qeDetails(lead={},readonly=false){
 return '<div class="panel panel-body qe-details"><h3>Moving / service details</h3>'+
  (readonly?'<p class="tiny muted">Using the selected inquiry. Its existing details stay with this moving file.</p>':'<p class="tiny muted">These details will be saved with this client’s new service.</p>')+
  '<div class="grid2">'+field('pickup','Pickup / service address',lead.pickup||'')+field('dropoff','Destination address',lead.dropoff||'')+
  field('home_size','Move size / description',lead.home_size||'')+field('boxes','Approx. boxes',lead.boxes||'')+
  field('pickup_access','Pickup access',lead.pickup_access||'')+field('dropoff_access','Destination access',lead.dropoff_access||'')+
  field('pickup_walk','Pickup parking / walk',lead.pickup_walk||'')+field('dropoff_walk','Destination parking / walk',lead.dropoff_walk||'')+
  textArea('inventory','Furniture / inventory',lead.inventory||'',3)+textArea('heavy_items','Heavy / special items',lead.heavy_items||'',3)+'</div></div>';
}
function qeUpdateClientCard(){
 const f=document.getElementById('crm-quote-form'),el=document.getElementById('crm-builder-client-card');if(!f||!el)return;
 const name=QE.client?.client_name||(QE.mode==='new'?f.elements.client_name.value:'Select a client above');
 const email=QE.client?.email||(QE.mode==='new'?f.elements.client_email.value:'');
 el.innerHTML='<div class="eyebrow">CLIENT</div><h3>'+h(name||'New client')+'</h3><p class="small">'+h(email||'Contact needed')+'</p>'+
  '<p class="tiny muted">'+h(f.elements.pickup?.value||'Pickup needed')+' → '+h(f.elements.dropoff?.value||'Destination needed')+'</p>';
}
function qeSetDetails(lead={},readonly=false){
 const f=document.getElementById('crm-quote-form');document.getElementById('qe-service-details').innerHTML=qeDetails(lead,readonly);
 qeServiceFields.forEach(k=>{f.elements[k].readOnly=readonly;});qeUpdateClientCard();
}
function qeSetClientMode(mode){
 const f=document.getElementById('crm-quote-form');QE.mode=mode;
 const contact=document.getElementById('qe-new-contact');contact.hidden=mode!=='new';
 contact.querySelectorAll('input').forEach(el=>{el.disabled=mode!=='new';el.required=mode==='new'&&el.name==='client_name';});
 qeUpdateClientCard();
}
function qeClientResults(clients){
 return clients.length?clients.map(c=>'<button type="button" class="search-result qe-client-result" data-editor="client" data-id="'+h(c.id)+'">'+
  '<span class="avatar">'+h(initials(c.client_name))+'</span><span class="grow"><strong>'+h(c.client_name)+'</strong><span class="tiny muted">'+
  h([c.email,uxPhoneLabel(c.phone)].filter(Boolean).join(' · '))+'</span></span>'+icon('right')+'</button>').join(''):
  '<p class="small muted">No matching client. Check the spelling or choose Add new client.</p>';
}
async function qeSelectClient(id){
 const version=++QE.serviceVersion;const client=await api('/crm/clients/'+encodeURIComponent(id));
 const f=document.getElementById('crm-quote-form');if(!f||version!==QE.serviceVersion)return;
 QE.client=client;qeSetClientMode('existing');
 f.elements.lead_id.value='';f.elements.lead_version.value='';
 document.getElementById('qe-client-selected').innerHTML='<div class="row wrap"><div class="grow"><strong>'+h(client.client_name)+'</strong><div class="tiny muted">'+h([client.email,uxPhoneLabel(client.phone)].filter(Boolean).join(' · '))+'</div></div><button type="button" data-editor="change-client">Change client</button></div>';
 document.getElementById('qe-client-search-box').hidden=true;document.getElementById('qe-client-results').innerHTML='';
 const services=(client.services||[]).filter(s=>!['Lost','Closed'].includes(s.stage));
 document.getElementById('qe-service-choice').innerHTML=select('selected_service','Moving file / inquiry',[['new','New service for this client'],...services.map(s=>[s.id,[uxDateLabel(s.move_date,'Date needed'),s.lead_type||'Moving',s.pickup||'Address needed',s.stage].filter(Boolean).join(' · ')])],'new');
 if(CRM.currentLead?.id){CRM.currentLead=null;qeSetDetails({},false);}qeUpdateClientCard();
}

crmQuoteBuilder=async function(leadId='',options={}){
 await qeBaseBuilder(leadId,options);
 const f=document.getElementById('crm-quote-form');if(!f)return;
 const lead=CRM.currentLead||{},seed=options.seed||{};
 QE.client=leadId?{client_name:lead.client_name,email:lead.email,phone:lead.phone}:null;
 QE.mode=leadId?'existing':'';QE.locked=Boolean(leadId);QE.busy=false;QE.key=crypto.randomUUID();++QE.searchVersion;++QE.serviceVersion;
 f.insertAdjacentHTML('afterbegin','<input type="hidden" name="lead_version" value="'+h(lead.version??'')+'">'+
  '<section class="panel panel-body qe-client"><h3>Client</h3>'+
  (leadId?'<div class="callout teal"><strong>'+h(lead.client_name)+'</strong>'+h([lead.email,uxPhoneLabel(lead.phone)].filter(Boolean).join(' · '))+'</div><p class="tiny muted">This quote is attached to this inquiry. Saving edits creates a new draft revision.</p>':
   '<div id="qe-client-selected"></div><div id="qe-client-search-box"><label for="qe-client-search">Search an existing client</label><div class="row wrap"><input type="search" id="qe-client-search" placeholder="Name, email, phone or address" autocomplete="off"><button type="button" data-editor="new-client">Add new client</button></div><div id="qe-client-results" aria-live="polite"></div></div>'+
   '<div id="qe-new-contact" hidden><div class="grid2">'+field('client_name','Client name','','text','maxlength="200"')+field('client_email','Email','','email','maxlength="254"')+field('client_phone','Phone')+'</div><p class="tiny muted">Enter an email or phone number. We check for existing contacts when you save.</p></div><div id="qe-service-choice"></div>')+
  '</section><section class="panel panel-body qe-quote-info"><div class="grid2">'+field('quote_title','Quote title',seed.quote_title||CRM.templates.find(t=>t.template_key===f.elements.template_key.value)?.body?.title||'Moving Service','text','maxlength="160"')+
  textArea('client_note','Client note / agreed scope',seed.client_note||'',3)+'</div><p class="tiny muted">This note is visible to the client. Booking terms and protection wording are included separately.</p></section><div id="qe-service-details"></div>');
 if(!leadId)qeSetClientMode('');
 qeSetDetails(lead,Boolean(leadId));
 for(const [key,value] of Object.entries(seed)){
  const input=f.elements.namedItem(key);if(!input||['lead_id','lead_version','quote_title','client_note'].includes(key)||typeof value==='object')continue;
  if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value??'';
 }
 for(const material of seed.materials||[]){
  const checkbox=[...f.querySelectorAll('.crm-mat-check')].find(x=>x.dataset.code===material.code);if(!checkbox)continue;
  checkbox.checked=true;const qty=f.querySelector('.crm-mat-qty[data-code="'+CSS.escape(material.code)+'"]');qty.disabled=false;qty.value=material.qty;
 }
 const deposit=f.elements.deposit_required.closest('.form-field');document.querySelector('.qe-quote-info .grid2').append(deposit);f.elements.deposit_required.disabled=false;
 crmToggleServiceFields();crmToggleStartTime();qeUpdateClientCard();
 if(options.result)crmRenderCalc(options.result);
 if(CRM.quoteSetup?.pricing_error)modalError(new Error(CRM.quoteSetup.pricing_error));
};

document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-editor]');if(!b)return;e.preventDefault();const action=b.dataset.editor;
 try{
  if(action==='template')return crmQuoteBuilder('',{templateKey:b.dataset.id});
  if(action==='edit'){
   const d=await api('/crm/quotes/'+encodeURIComponent(b.dataset.id));
   if(d.quote.status!=='Draft')throw Error('Open the latest draft before editing.');
   return crmQuoteBuilder(d.lead.id,{templateKey:d.quote.inputs.template_key,seed:d.quote.inputs,result:d.quote.result});
  }
  if(action==='client')return qeSelectClient(b.dataset.id);
  const f=document.getElementById('crm-quote-form');if(!f||QE.locked)return;
  if(action==='new-client'){
   ++QE.searchVersion;++QE.serviceVersion;QE.client=null;qeSetClientMode('new');
   if(f.elements.lead_id.value){CRM.currentLead=null;qeSetDetails({},false);}
   f.elements.lead_id.value='';f.elements.lead_version.value='';
   document.getElementById('qe-client-selected').innerHTML='<div class="row wrap"><strong class="grow">New client</strong><button type="button" data-editor="change-client">Search existing clients</button></div>';
   document.getElementById('qe-client-search-box').hidden=true;document.getElementById('qe-service-choice').innerHTML='';f.elements.client_name.focus();
  }
  if(action==='change-client'){
   ++QE.searchVersion;++QE.serviceVersion;QE.client=null;qeSetClientMode('');
   if(f.elements.lead_id.value){CRM.currentLead=null;qeSetDetails({},false);}
   f.elements.lead_id.value='';f.elements.lead_version.value='';document.getElementById('qe-client-selected').innerHTML='';
   document.getElementById('qe-service-choice').innerHTML='';document.getElementById('qe-client-search-box').hidden=false;
   document.getElementById('qe-client-search').focus();
  }
 }catch(ex){modalError(ex);}
});
let qeSearchTimer;
document.addEventListener('input',e=>{
 if(e.target.closest('#crm-quote-form'))qeUpdateClientCard();
 if(e.target.id!=='qe-client-search')return;
 clearTimeout(qeSearchTimer);const query=e.target.value.trim(),version=++QE.searchVersion;
 const box=document.getElementById('qe-client-results');if(query.length<2){box.innerHTML='<p class="tiny muted">Type at least two characters.</p>';return;}
 box.innerHTML='<p class="tiny muted">Searching…</p>';
 qeSearchTimer=setTimeout(async()=>{
  try{const clients=await api('/crm/clients?q='+encodeURIComponent(query));if(version===QE.searchVersion&&document.getElementById('qe-client-results')===box)box.innerHTML=qeClientResults(clients);}
  catch(ex){if(version===QE.searchVersion)box.textContent=ex.message;}
 },200);
});
document.addEventListener('change',async e=>{
 if(e.target.name!=='selected_service'||!e.target.closest('#crm-quote-form'))return;
 const f=document.getElementById('crm-quote-form'),id=e.target.value,version=++QE.serviceVersion;
 f.elements.lead_id.value='';f.elements.lead_version.value='';CRM.currentLead=null;qeSetDetails({},false);
 try{
  if(id==='new'){f.elements.lead_id.value='';f.elements.lead_version.value='';CRM.currentLead=null;qeSetDetails({},false);return;}
  const setup=await api('/crm/quote-setup?lead_id='+encodeURIComponent(id));if(version!==QE.serviceVersion||document.getElementById('crm-quote-form')!==f)return;
  if(setup.lead.lead_type!==f.elements.service_type.value){e.target.value='new';throw Error('Choose a matching service type/template before selecting this inquiry.');}
  CRM.currentLead=setup.lead;f.elements.lead_id.value=id;f.elements.lead_version.value=setup.lead.version;
  qeSetDetails(setup.lead,true);f.elements.move_date.value=setup.lead.move_date||'';
  if(setup.lead.move_date&&f.elements.service_type.value!=='Junk Removal')await crmRefreshRate();
 }catch(ex){modalError(ex);}
});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('#crm-quote-form[data-editor-form]');if(!f)return;e.preventDefault();if(QE.busy)return;
 try{
  if(!QE.mode)throw Error('Select a client or choose Add new client before saving.');
  if(f.elements.selected_service&&f.elements.selected_service.value!=='new'&&f.elements.lead_id.value!==f.elements.selected_service.value)throw Error('Wait for the selected inquiry to load, or choose New service.');
  const data=crmQuoteData();if(!data)return;
  data.draft_key=QE.key;data.lead_version=data.lead_version===''?undefined:Number(data.lead_version);
  if(QE.client?.id)data.client_id=QE.client.id;
  if(QE.mode==='new')data.new_client={client_name:f.elements.client_name.value,email:f.elements.client_email.value,phone:f.elements.client_phone.value};
  if(!data.lead_id)data.service_details=Object.fromEntries(qeServiceFields.map(k=>[k,f.elements[k].value]));
  for(const key of ['client_name','client_email','client_phone','selected_service',...qeServiceFields])delete data[key];
  QE.busy=true;const editorKey=QE.key;f.setAttribute('aria-busy','true');const buttons=[...modal.querySelectorAll('button:not([data-action=close])')];buttons.forEach(b=>b.disabled=true);
  try{const quote=await api('/crm/quotes',{method:'POST',data});toast('Draft estimate saved.');if(modal.open&&document.getElementById('crm-quote-form')===f)await crmOpenQuote(quote.id);}
  finally{if(QE.key===editorKey)QE.busy=false;f.removeAttribute('aria-busy');buttons.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
 }catch(ex){modalError(ex);}
});

// Native validation may target a field hidden by the document-style view.
document.addEventListener('invalid',e=>{
 if(!e.target.closest('#crm-quote-form'))return;
 modalError(new Error('Draft was not saved. Complete '+(e.target.labels?.[0]?.textContent||e.target.name||'the required fields')+'. If no hourly rate is available, ask an owner to configure pricing.'));
},true);
