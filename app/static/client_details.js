/* Client progress is saved privately; submitting sends a review request to the team. */
'use strict';
const DetailProgress={timer:null,chain:Promise.resolve(),pending:null,submitting:false,failed:false,observer:null,viewTimers:new Map(),viewed:new Set()};
const detailNames=['client_name','phone','move_date','home_size','pickup','dropoff','boxes','inventory','pickup_access','dropoff_access','pickup_walk','dropoff_walk','heavy_items'];
function detailSnapshot(form){return Object.fromEntries(detailNames.map(k=>[k,String(form.elements[k]?.value||'')]));}
function detailMessage(message,bad=false){const box=document.getElementById('detail-save-status');if(box){box.textContent=message;box.classList.toggle('portal-error',bad);}}
function detailValues(){
 const state=P.data.details_progress||{},pending=P.data.changes.filter(c=>c.status==='Pending');
 const values={...P.data.client,...Object.assign({},...pending.slice().reverse().map(c=>JSON.parse(c.payload_json)))};
 if(state.draft&&!state.draft.stale)Object.assign(values,state.draft.fields);
 if(DetailProgress.pending)Object.assign(values,DetailProgress.pending);
 return values;
}
function queueDetailSave(fields){
 DetailProgress.pending=fields;detailMessage(t('Saving progress…','Enregistrement…'));
 const operation=async()=>{
  const state=P.data.details_progress||{},draft=state.draft,version=P.data.client.version;
  const saved=await req('/details-progress',{method:'PUT',data:{fields,version,revision:draft?.revision||0}});
  state.draft={fields,base_version:version,revision:saved.revision,updated_at:saved.saved_at,stale:false};P.data.details_progress=state;
  if(DetailProgress.pending===fields)DetailProgress.pending=null;
  DetailProgress.failed=false;detailMessage(t('Progress saved privately. Submit when ready.','Progression enregistrée en privé. Transmettez-la lorsque vous êtes prêt.'));
 };
 DetailProgress.chain=DetailProgress.chain.catch(()=>{}).then(operation).catch(ex=>{DetailProgress.failed=true;detailMessage(ex.message,true);throw ex;});
 // Keep a rejected request visible without an unhandled rejection in the browser.
 DetailProgress.chain.catch(()=>{});return DetailProgress.chain;
}
function scheduleDetailSave(form){
 DetailProgress.pending=detailSnapshot(form);clearTimeout(DetailProgress.timer);
 detailMessage(t('Changes not yet saved…','Changements pas encore enregistrés…'));
 DetailProgress.timer=setTimeout(()=>{const fields=DetailProgress.pending;if(fields)queueDetailSave(fields);},900);
}
details=function(){
 const s=P.data.details_progress||{},r=s.request,l=detailValues(),labels=s.labels||{};
 const label=k=>labels[k]?.[P.fr?1:0]||k;
 const requested=r&&r.status==='Open'?r.fields:[];
 const first=[...new Set([...requested.filter(k=>detailNames.includes(k)),...detailNames.filter(k=>!String(l[k]||'').trim())])];
 const known=detailNames.filter(k=>!first.includes(k));
 const checklist=requested.length?panel(t('What we still need','Ce qu’il reste à vérifier'),'<p>'+t('Start with the details below. Everything already filled in is saved under “Details already provided”.','Commencez par les renseignements ci-dessous. Ceux qui sont déjà remplis se trouvent dans « Renseignements déjà fournis ».')+'</p><details><summary>'+t('See the requested checklist','Voir la liste demandée')+'</summary><ul>'+requested.map(k=>{
  const done=k==='photos'?(P.data.attachments.length||(P.data.media_links||[]).length):String(l[k]||'').trim();
  return '<li>'+esc(label(k))+' — '+t(done?'filled in; please check':'please add if known',done?'rempli; à vérifier':'à ajouter si connu')+'</li>';
 }).join('')+'</ul></details>'+(r.note?'<p>'+esc(r.note)+'</p>':'')):'';
 const pending=P.data.changes.some(c=>c.status==='Pending');
 const stale=s.draft?.stale?panel(t('Earlier saved progress','Progression enregistrée précédemment'),notice(t('The team updated your file. Compare your earlier progress below with the current details before copying anything back.','L’équipe a mis à jour votre dossier. Comparez votre ancienne progression ci-dessous avec les renseignements actuels avant de la recopier.'))+'<details><summary>'+t('Show earlier progress','Afficher la progression précédente')+'</summary>'+Object.entries(s.draft.fields).filter(([k,v])=>v!==String(l[k]||'')).map(([k,v])=>'<p><strong>'+esc(label(k))+'</strong><br>'+esc(v)+'</p>').join('')+'</details>'):'';
 const fields=names=>names.map(k=>{
  const multiline=['inventory','pickup_access','dropoff_access'].includes(k),title=label(k);
  return multiline?ta(k,title,l[k]||'',k==='inventory'?5:3):f(k,title,l[k]||'',k==='move_date'?'date':k==='phone'?'tel':'text',k==='client_name'?'required':'');
 }).join('');
 const form='<form data-details-form>'+(first.length?'<div class="grid2">'+fields(first)+'</div>':'<p>'+t('Your details are filled in. Check them below and submit when you are ready.','Vos renseignements sont remplis. Vérifiez-les ci-dessous, puis transmettez-les lorsque vous êtes prêt.')+'</p>')+(known.length?'<details data-known-details><summary>'+t('Details already provided','Renseignements déjà fournis')+' ('+known.length+')</summary><div class="spacer"></div><div class="grid2">'+fields(known)+'</div></details>':'')+'<p class="tiny muted">'+t('Not sure yet? Leave it blank and ask us for help. A requested date is not a confirmed booking.','Vous n’êtes pas certain? Laissez le champ vide et demandez-nous de l’aide. Une date souhaitée ne constitue pas une réservation confirmée.')+'</p><p id="detail-save-status" role="status">'+t(s.draft&&!s.draft.stale?'Your saved progress is filled in.':'Progress saves as you type.',s.draft&&!s.draft.stale?'Votre progression enregistrée est remplie.':'Votre progression s’enregistre pendant la saisie.')+'</p><div class="row wrap sticky-action"><button type="button" data-details-save>'+t('Save progress','Enregistrer la progression')+'</button><button type="submit" class="btn-primary">'+t('Submit my details','Transmettre mes renseignements')+'</button></div></form>';
 const status=pending||r?.status==='Submitted'?notice('<strong>'+t('Updates received — the team is reviewing them.','Renseignements reçus — l’équipe les examine.')+'</strong><p>'+t('Your requested service or date changes need confirmation from Crown. You can still add information or ask us for help.','Vos demandes de modification de service ou de date restent à confirmer par Crown. Vous pouvez ajouter des renseignements ou demander de l’aide.')+'</p><button data-client="tab" data-tab="help">'+t('Request a callback','Demander un rappel')+'</button>'):r?.status==='Reviewed'?notice(t('The team has reviewed your submitted information. You can send another update if anything changes.','L’équipe a examiné les renseignements transmis. Vous pouvez envoyer une nouvelle mise à jour au besoin.')):'';
 return status+checklist+stale+panel(t('Your move details','Votre déménagement'),form)+panel(t('Photos and shared folders','Photos et dossiers partagés'),'<p>'+t('Upload photos or videos, or paste the folder link you already have.','Téléversez des photos ou des vidéos, ou collez le lien du dossier que vous avez déjà.')+'</p><button data-client="tab" data-tab="files">'+t('Add photos / folder link','Ajouter des photos / un lien')+'</button>')+panel(t('Need a hand?','Besoin d’aide?'),'<button data-client="tab" data-tab="help">'+t('Request a callback','Demander un rappel')+'</button>');
};

const detailBaseUploads=uploads;
uploads=function(){
 const policy=P.data.upload_policy||{},photo=policy.max_photo_mb||30,video=policy.max_video_mb||0;
 if(policy.mode!=='photos')return detailBaseUploads();
 const guidance=policy.enabled?'<p>'+t('Photos: JPEG, PNG, WebP or iPhone HEIC, up to '+photo+' MB each.','Photos : JPEG, PNG, WebP ou HEIC d’iPhone, jusqu’à '+photo+' Mo chacune.')+(video?' '+t('Videos: MP4, MOV or WebM, up to '+video+' MB and 3 minutes.','Vidéos : MP4, MOV ou WebM, jusqu’à '+video+' Mo et 3 minutes.'):'')+'</p><p>'+t('Choose up to 10 files at a time. Wait for the saved-file count before leaving. If a file fails, it stays selected: click Upload files again to retry only those files.','Choisissez jusqu’à 10 fichiers à la fois. Attendez le nombre de fichiers enregistrés avant de quitter. Les fichiers en échec restent sélectionnés : cliquez de nouveau sur Téléverser les fichiers pour réessayer uniquement ceux-ci.')+'</p>':'';
 return '<details class="client-upload-help"><summary>'+t('Photo & video tips','Conseils pour les photos et vidéos')+'</summary>'+guidance+'<p>'+t('You can also share a folder link below. Check that Crown Movers has permission to open it.','Vous pouvez aussi partager un lien de dossier ci-dessous. Vérifiez que Crown Movers puisse l’ouvrir.')+'</p></details>'+detailBaseUploads();
};
document.addEventListener('input',e=>{const form=e.target.closest('form[data-details-form]');if(form)scheduleDetailSave(form);});
document.addEventListener('change',e=>{const form=e.target.closest('form[data-details-form]');if(form)scheduleDetailSave(form);});
document.addEventListener('click',e=>{const b=e.target.closest('[data-details-save]');if(!b)return;clearTimeout(DetailProgress.timer);queueDetailSave(detailSnapshot(b.closest('form')));});
document.addEventListener('submit',async e=>{
 const form=e.target.closest('form[data-details-form]');if(!form)return;e.preventDefault();if(DetailProgress.submitting)return;
 DetailProgress.submitting=true;const b=form.querySelector('[type=submit]');b.disabled=true;clearTimeout(DetailProgress.timer);
 try{
  await DetailProgress.chain.catch(()=>{});
  await req('/changes',{method:'POST',data:{version:P.data.client.version,fields:detailSnapshot(form)}});
  DetailProgress.pending=null;DetailProgress.failed=false;
  await load(t('Thank you! Your details are with the team. We will review them before confirming any changes.','Merci! Vos renseignements sont transmis à l’équipe. Nous les vérifierons avant de confirmer des changements.'));
 }catch(ex){detailMessage(ex.message,true);}finally{DetailProgress.submitting=false;if(b.isConnected)b.disabled=false;}
});
function observeEstimateViews(){
 DetailProgress.observer?.disconnect();for(const timer of DetailProgress.viewTimers.values())clearTimeout(timer);DetailProgress.viewTimers.clear();
 if(P.tab!=='estimates'||!P.data||!('IntersectionObserver' in window))return;
 DetailProgress.observer=new IntersectionObserver(entries=>{for(const entry of entries){
  const id=entry.target.dataset.estimateView;
  const key=id+':'+Math.floor(Date.now()/1800000);
  if(!entry.isIntersecting||entry.intersectionRatio<.5){clearTimeout(DetailProgress.viewTimers.get(id));DetailProgress.viewTimers.delete(id);continue;}
  if(DetailProgress.viewed.has(key)||DetailProgress.viewTimers.has(id))continue;
  DetailProgress.viewTimers.set(id,setTimeout(async()=>{
   DetailProgress.viewTimers.delete(id);if(document.visibilityState!=='visible'||P.tab!=='estimates'||!entry.target.isConnected)return;
   try{await req('/quotes/'+encodeURIComponent(id)+'/view',{method:'POST',data:{}});DetailProgress.viewed.add(key);}catch{/* Reading the estimate remains available if tracking is unavailable. */}
  },1000));
 }},{threshold:.5});
 el.querySelectorAll('[data-estimate-view]').forEach(node=>DetailProgress.observer.observe(node));
}
const detailBaseRender=render;
render=function(message=''){
 const form=el.querySelector('form[data-details-form]');
 if(form&&DetailProgress.pending&&!DetailProgress.submitting){clearTimeout(DetailProgress.timer);queueDetailSave(detailSnapshot(form));}
 detailBaseRender(message);observeEstimateViews();
};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')observeEstimateViews();});
window.addEventListener('beforeunload',e=>{if(DetailProgress.pending||DetailProgress.failed){e.preventDefault();e.returnValue='';}});
if(P.data)render();
