/* One call-intake flow, private photos and optional reviewable calculator guidance. */
'use strict';
const INQUIRY_CHOICES={
 home_size:['Furniture delivery / micro-move','Studio apartment','1 bedroom apartment','2 bedroom apartment','2 bedroom home','3 bedroom apartment','3 bedroom home','4 bedroom home','More than 4 bedrooms / full house','Storage facility'],
 boxes:['No boxes','1-10','10-20','20-30','30-40','40-50','50-60','60-80','80-100','100-120','120+','Not sure'],
 access:['Ground floor / direct access','A few steps','1 flight of stairs','2 flights of stairs','3 flights of stairs','4+ flights of stairs','Elevator','Freight elevator','Not sure'],
 walk:['0-10 ft','10-20 ft','20-30 ft','30-50 ft','50-100 ft','100+ ft','Not sure'],
 heavy:['Upright piano','Safe','Large appliance / commercial fridge','Pool table','Oversized couch','Large glass item','Marble / granite item','Large treadmill','Gym equipment','Other / Not sure'],
 extras:['Packing','Unpacking','Furniture disassembly / reassembly','Storage','Junk removal','Packing materials']
};
function inquiryMeta(lead){try{return JSON.parse(lead.intake_json||'{}')||{};}catch{return {};}}
function inquiryPhotoLink(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';}catch{return '';}}
function inquiryChoice(name,label,value,kind){
 const id='inquiry-options-'+name;
 return field(name,label,value||'','text','list="'+id+'"')+'<datalist id="'+id+'">'+INQUIRY_CHOICES[kind].map(v=>'<option value="'+h(v)+'"></option>').join('')+'</datalist>';
}
function inquiryChecks(name,label,choices,selected=[]){return '<fieldset class="inquiry-checks"><legend>'+h(label)+'</legend>'+choices.map(v=>'<label class="checkbox-label"><input type="checkbox" name="'+name+'" value="'+h(v)+'" '+(selected.includes(v)?'checked':'')+'>'+h(v)+'</label>').join('')+'</fieldset>';}
editLead=async function(id=''){
 const [l,policy]=await Promise.all([id?api('/leads/'+encodeURIComponent(id)):Promise.resolve({}),api('/upload-policy')]);
 const m=inquiryMeta(l);S.modalContext={lead:l};
 const photos=policy.enabled?'<label class="form-field full" for="inquiry-photo-files">Upload photos now · up to 10, '+h(policy.max_file_mb)+' MB each<input type="file" id="inquiry-photo-files" name="file" accept="'+h(policy.accept)+'" multiple></label>':'<p class="small muted">'+h(policy.message)+'</p>';
 showModal(id?'Edit inquiry':'New client inquiry','<form id="lead-form" data-inquiry data-max-mb="'+h(policy.max_file_mb||15)+'"><input type="hidden" name="id" value="'+h(id)+'"><input type="hidden" name="version" value="'+h(l.version||'')+'">'+
  '<section class="panel panel-body"><h3>1 · Client & service</h3><div class="grid2">'+field('client_name','Client name',l.client_name||'','text','required')+field('move_date','Requested move date',l.move_date||'','date')+field('email','Email',l.email||'','email')+field('phone','Phone',l.phone||'','tel')+
  select('lead_type','Service type',[['Moving','Local moving'],['Packing','Packing'],['Long Distance','Long distance'],['Junk Removal','Junk removal'],...(!['Moving','Packing','Long Distance','Junk Removal','Form',undefined].includes(l.lead_type)?[[l.lead_type,l.lead_type]]:[])],l.lead_type==='Form'?'Moving':l.lead_type||'Moving')+
  select('intake_local_area','Both addresses in Greater Montreal?',[['','Choose / ask client'],['Yes','Yes'],['No','No'],['Not sure','Not sure']],m.local_area||'')+
  select('language','Client language',['English','French','Unknown'],l.language||'English')+select('intake_start_window','Preferred arrival',[['unknown','Not decided'],['morning','Morning'],['afternoon','Afternoon']],m.start_window||'unknown')+'</div></section>'+
  '<section class="panel panel-body"><h3>2 · Move details</h3><p class="tiny muted">Choose a suggested answer or type a more specific one. Leave unknown details blank or select Not sure.</p><div class="grid2">'+field('pickup','Pickup address',l.pickup||'')+field('dropoff','Destination address',l.dropoff||'')+
  inquiryChoice('home_size','Move size',l.home_size,'home_size')+inquiryChoice('boxes','Approximate box count',l.boxes,'boxes')+
  inquiryChoice('pickup_access','Pickup access',l.pickup_access,'access')+inquiryChoice('dropoff_access','Destination access',l.dropoff_access,'access')+
  inquiryChoice('pickup_walk','Pickup walk from parking · feet',l.pickup_walk,'walk')+inquiryChoice('dropoff_walk','Destination walk from parking · feet',l.dropoff_walk,'walk')+
  textArea('inventory','Furniture, appliances, rooms and inventory',l.inventory||'',4)+textArea('heavy_items','Other heavy-item / handling details',l.heavy_items||'',3)+
  '</div>'+inquiryChecks('intake_heavy_items','Heavy / specialty items',INQUIRY_CHOICES.heavy,m.heavy_items)+inquiryChecks('intake_additional_services','Additional services',INQUIRY_CHOICES.extras,m.additional_services)+textArea('intake_service_details','Assembly, deadlines and other service details',m.service_details||'',3)+
  '<details><summary>Driving time · if already known</summary><p class="tiny muted">Enter driving minutes from a route check. The calculator will not guess travel from the addresses.</p><div class="grid2">'+field('intake_between_stops_minutes','Pickup to destination · minutes',m.between_stops_minutes||'','number','min="0" max="1440"')+field('intake_pickup_base_minutes','700 Rue Hodge to pickup · minutes',m.pickup_base_minutes||'','number','min="0" max="1440"')+field('intake_return_base_minutes','Destination back to base · minutes',m.return_base_minutes||'','number','min="0" max="1440"')+'</div></details></section>'+
  '<section class="panel panel-body"><h3>3 · Photos</h3>'+field('photo_link','Drive / photo folder link · HTTPS',l.photo_link||'','url')+photos+'<p class="tiny muted">Uploads stay private with this moving file. External folders keep their existing sharing permissions.</p></section>'+
  '<details class="panel panel-body"><summary>Internal follow-up</summary><div class="grid2">'+select('stage','Sales stage',[...new Set(['New Lead','Contacted','Waiting for details','Quote Sent','Accepted','Booked','Closed','Lost','Commercial Review',l.stage].filter(Boolean))],l.stage||'New Lead')+select('priority','Priority',['Normal','High'],l.priority||'Normal')+field('source','Source',l.source||'Phone call')+textArea('notes','Internal notes',l.notes||'',3)+'</div></details><p id="inquiry-progress" role="status"></p></form>',btn('close','Cancel')+'<button type="submit" form="lead-form" class="btn-primary">Save inquiry</button>',true);
};
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-inquiry]');if(!f)return;e.preventDefault();if(f.dataset.busy)return;
 const buttons=[...modal.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);f.dataset.busy='1';let savedId='';
 try{
  const d=Object.fromEntries(new FormData(f)),id=d.id;delete d.id;delete d.file;d.version=Number(d.version);d.intake={};
  for(const key of ['local_area','service_details','start_window','between_stops_minutes','pickup_base_minutes','return_base_minutes']){d.intake[key]=d['intake_'+key]||'';delete d['intake_'+key];}
  for(const key of ['heavy_items','additional_services']){d.intake[key]=[...f.querySelectorAll('[name="intake_'+key+'"]:checked')].map(x=>x.value);delete d['intake_'+key];}
  const files=[...(f.elements.file?.files||[])];
  if(files.length>10)throw Error('Choose up to 10 photos at a time.');
  if(files.some(x=>x.size>Number(f.dataset.maxMb)*1024*1024))throw Error('One of the photos exceeds '+f.dataset.maxMb+' MB.');
  const result=await api(id?'/leads/'+encodeURIComponent(id):'/leads',{method:id?'PATCH':'POST',data:d});savedId=id||result.id;
  let saved=0,error='';
  for(const file of files){const data=new FormData();data.append('file',file);data.append('category','Move photos');document.getElementById('inquiry-progress').textContent='Inquiry saved. Uploading '+(saved+1)+' of '+files.length+' photos…';try{await api('/leads/'+encodeURIComponent(savedId)+'/uploads',{method:'POST',form:data});saved++;}catch(ex){error=ex.message;break;}}
  await navigate('sales');await openLead(savedId);toast('Inquiry saved'+(saved?' with '+saved+' photo(s).':'.'));
  if(error)modalError(new Error('The inquiry is saved. '+saved+' of '+files.length+' photos uploaded. '+error+' Use Add photos to upload the remaining files.'));
 }catch(ex){
  if(savedId){await openLead(savedId);modalError(new Error('The inquiry was saved. '+ex.message));}else modalError(ex);
 }finally{delete f.dataset.busy;buttons.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
});
const inquiryPreviousOpenLead=openLead;
openLead=async function(id){
 await inquiryPreviousOpenLead(id);
 if(!may(['owner','sales']))return;
 const l=await api('/leads/'+encodeURIComponent(id));const m=inquiryMeta(l);
 const body=modal.querySelector('.modal-body');if(!body)return;
 body.insertAdjacentHTML('afterbegin','<section class="callout teal inquiry-saved"><strong>Moving file · ready for an estimate</strong><div class="row wrap"><button class="btn-primary" data-inquiry-action="estimate" data-id="'+h(id)+'">Create estimate</button><button data-inquiry-action="photos" data-id="'+h(id)+'">View / add photos</button>'+(inquiryPhotoLink(l.photo_link)?'<a class="button-link" href="'+h(inquiryPhotoLink(l.photo_link))+'" target="_blank" rel="noopener noreferrer">Open photo folder</a>':'')+'</div>'+((m.additional_services||[]).length?'<p class="small">Additional services: '+h(m.additional_services.join(', '))+'</p>':'')+((m.heavy_items||[]).length?'<p class="small">Specialty items: '+h(m.heavy_items.join(', '))+'</p>':'')+'</section>');
};
const inquiryQuoteBuilder=crmQuoteBuilder;
crmQuoteBuilder=async function(id='',options={}){
 inquirySuggestion=null;
 await inquiryQuoteBuilder(id,options);
 const f=document.getElementById('crm-quote-form');if(!f)return;
 const details=document.getElementById('qe-service-details');
 details.insertAdjacentHTML('afterend','<section class="panel panel-body" id="inquiry-quote-summary"></section>');inquiryQuoteSummary();
};
const inquirySetDetails=qeSetDetails;
qeSetDetails=function(lead={},readonly=false){inquirySetDetails(lead,readonly);inquiryQuoteSummary(lead);};
function inquiryQuoteSummary(lead=CRM.currentLead){
 const box=document.getElementById('inquiry-quote-summary');if(!box)return;
 const photoFields=field('photo_link','Client photo / file link · Google Drive, Dropbox or another HTTPS link',lead?.photo_link||'','url','maxlength="3000" placeholder="https://drive.google.com/..."')+'<div data-photo-link-preview>'+inquiryFolderButton(lead?.photo_link)+'</div><p class="tiny muted">Saved with this moving file when you save the draft. Assigned crew can open the link. The folder’s sharing permissions still apply.</p>';
 if(!lead?.id){box.innerHTML='<h3>Client photos & files</h3>'+photoFields+'<p class="small muted">Save the draft to upload photos to its moving file.</p>';return;}
 const m=inquiryMeta(lead);
 box.innerHTML='<h3>Inquiry photos & calculator</h3>'+photoFields+'<div class="row wrap"><button type="button" data-inquiry-action="photos" data-id="'+h(lead.id)+'">View inquiry photos</button></div><p class="small">'+h([...(m.additional_services||[]),...(m.heavy_items||[]),m.service_details].filter(Boolean).join(' · ')||'Review the recorded inventory and access above.')+'</p>'+field('calculator_drive_minutes','Driving time between stops · minutes',m.between_stops_minutes||'','number','min="0" max="1440"')+'<button type="button" data-inquiry-action="suggest">Preview calculator suggestion</button><div id="inquiry-suggestion" aria-live="polite"></div><p class="tiny muted">Uses your residential calculator’s labour rules. Current CrownOps rates remain editable. Photos and folder links require staff review.</p>';
}
function inquiryFolderButton(value){const link=inquiryPhotoLink(value);return link?'<a class="button-link" href="'+h(link)+'" target="_blank" rel="noopener noreferrer">Open client photo / file link</a>':'';}
let inquirySuggestion=null;
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-inquiry-action]');if(!b)return;e.preventDefault();
 try{
  const a=b.dataset.inquiryAction,id=b.dataset.id;
  if(a==='estimate')return crmQuoteBuilder(id);
  if(a==='photos'){inquiryPhotoReturn={state:modalSnapshot(),label:document.getElementById('crm-quote-form')?'Back to estimate':'Back to inquiry'};return clientFiles(id);}
  if(a==='return'){const saved=inquiryPhotoReturn;inquiryPhotoReturn=null;if(saved){const state=saved.state;modal.innerHTML=state.html;modal.style.width=state.width;S.modalContext=state.context;if(state.root)modal.dataset.moveRoot=state.root;else delete modal.dataset.moveRoot;
   const form=document.getElementById('crm-quote-form');if(saved.updatedLink!==undefined&&form){if(CRM.currentLead)CRM.currentLead.photo_link=saved.updatedLink;form.elements.photo_link.value=saved.updatedLink;form.elements.photo_link.dispatchEvent(new Event('input',{bubbles:true}));}
   requestAnimationFrame(()=>{const body=modal.querySelector('.modal-body');if(body)body.scrollTop=state.scroll.body;modal.scrollTop=state.scroll.dialog;});}return;}
  const f=document.getElementById('crm-quote-form');if(!f)return;
  if(a==='suggest'){
   const lead=CRM.currentLead||{},meta={...inquiryMeta(lead),between_stops_minutes:f.elements.calculator_drive_minutes.value};
   inquirySuggestion=CrownLocalEstimator.suggest({...lead,intake_json:JSON.stringify(meta)});const s=inquirySuggestion;
   document.getElementById('inquiry-suggestion').innerHTML=s.available?'<div class="callout '+(s.can_apply?'teal':'orange')+'"><strong>'+h(s.movers)+' movers · '+h(s.trucks)+' truck(s) · '+h(s.hours_low)+'–'+h(s.hours_high)+' service hours</strong><p>'+h(s.profile)+'</p>'+s.warnings.map(w=>'<p class="tiny">'+h(w)+'</p>').join('')+'<button type="button" data-inquiry-action="apply" '+(!s.can_apply?'disabled':'')+'>Use suggestion in draft</button></div>':'<p class="small">'+h(s.reason)+'</p>';return;
  }
  if(a==='apply'){
   const s=inquirySuggestion;if(!s?.can_apply)throw Error('Preview a complete suggestion first.');
   if(f.elements.service_type.value!=='Moving')throw Error('Use local moving suggestions only for a Moving quote.');
   const controls=[...modal.querySelectorAll('button,input,select,textarea')].map(el=>[el,el.disabled]);QE.busy=true;
   try{
    for(const k of ['movers','trucks','hours_low','hours_high','minimum_hours','start_window'])f.elements[k].value=s[k];
    if(s.transport_hours!==null)f.elements.transport_hours.value=s.transport_hours;
    f.elements.small_afternoon_job.checked=s.small_afternoon_job;
    f.dataset.calculatorBasis=JSON.stringify({...s,between_stops_minutes:f.elements.calculator_drive_minutes.value});
    crmToggleServiceFields();crmToggleStartTime();
    const states=controls.map(([el])=>[el,el.disabled]);states.forEach(([el])=>el.disabled=true);
    f.elements.hourly_rate.value='';
    try{await crmRefreshRate();}finally{states.filter(([el])=>el.isConnected).forEach(([el,disabled])=>el.disabled=disabled);}
    qeUpdateClientCard();toast('Suggestion copied. Review rates, travel and any specialty charges before saving.');
   }finally{QE.busy=false;}
  }
 }catch(ex){modalError(ex);}
});
document.addEventListener('input',e=>{if(e.target.name==='calculator_drive_minutes'){inquirySuggestion=null;const box=document.getElementById('inquiry-suggestion');if(box)box.innerHTML='';}});
document.addEventListener('input',e=>{if(e.target.name==='photo_link'){const box=e.target.closest('form')?.querySelector('[data-photo-link-preview]');if(box)box.innerHTML=inquiryFolderButton(e.target.value);}});
const inquiryQuoteData=crmQuoteData;
crmQuoteData=function(){const d=inquiryQuoteData();if(!d)return d;if(d.photo_link?.trim()&&!inquiryPhotoLink(d.photo_link))throw Error('Use a complete HTTPS photo or file link.');const f=document.getElementById('crm-quote-form');delete d.calculator_drive_minutes;if(f?.dataset.calculatorBasis)d.calculator_basis=JSON.parse(f.dataset.calculatorBasis);return d;};

let inquiryPhotoReturn=null;
document.addEventListener('click',e=>{if(e.target.closest('[data-action="close"],[data-action="logout"],[data-page]'))inquiryPhotoReturn=null;},true);
const inquiryClientFiles=clientFiles;
clientFiles=async function(id){
 await inquiryClientFiles(id);
 if(may(['owner','sales'])){
  const lead=await api('/leads/'+encodeURIComponent(id));
  modal.querySelector('.modal-body')?.insertAdjacentHTML('afterbegin','<section class="panel panel-body"><h3>Client photo / file link</h3><form data-inquiry-photo-link data-lead-id="'+h(id)+'" data-previous-link="'+h(lead.photo_link||'')+'">'+field('photo_link','Paste a Google Drive, Dropbox or other HTTPS link',lead.photo_link||'','url','maxlength="3000" placeholder="https://drive.google.com/..."')+'<div class="row wrap"><button type="submit">Save link</button><span data-photo-link-preview>'+inquiryFolderButton(lead.photo_link)+'</span></div><p class="tiny muted">Available to this moving file’s assigned crew. The folder’s sharing permissions still apply. Clear the field and save to remove the link.</p></form></section>');
 }else{const link=inquiryFolderButton(X.formContext?.l?.photo_link);if(link)modal.querySelector('.modal-body')?.insertAdjacentHTML('afterbegin','<p>'+link+'</p>');}
 if(inquiryPhotoReturn)modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin','<button type="button" data-inquiry-action="return">'+h(inquiryPhotoReturn.label)+'</button>');
};
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-inquiry-photo-link]');if(!f)return;e.preventDefault();
 const button=f.querySelector('button[type=submit]');if(button.disabled)return;
 try{
  const value=f.elements.photo_link.value.trim();if(value&&!inquiryPhotoLink(value))throw Error('Use a complete HTTPS photo or file link.');
  button.disabled=true;
  await api('/crm/services/'+encodeURIComponent(f.dataset.leadId)+'/photo-link',{method:'PATCH',data:{expected_link:f.dataset.previousLink,photo_link:value}});
  f.dataset.previousLink=value;
  if(inquiryPhotoReturn)inquiryPhotoReturn.updatedLink=value;
  if(CRM.currentLead?.id===f.dataset.leadId)CRM.currentLead.photo_link=value;
  toast(value?'Photo link saved for the assigned crew.':'Photo link removed.');
 }catch(ex){modalError(ex);}finally{if(button.isConnected)button.disabled=false;}
});
