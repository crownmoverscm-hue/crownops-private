/* The private portal exposes only the service authorized by its session. */
'use strict';
const CT={draft:'',offset:0,request:null,sequence:0};
const conversationHelp=help;
help=function(){
 const holder=document.createElement('div');holder.innerHTML=conversationHelp();
 // The unified message form replaces the separate general enquiry and sent-email lists.
 for(const p of [...holder.querySelectorAll('.panel')]){
  if(p.querySelector('[data-client-form="concern"]')||['Messages from Crown','Messages de Crown'].includes(p.querySelector('h2')?.textContent))p.remove();
 }
 return panel(t('Messages & updates','Messages et nouvelles'),`<p>${t('Your messages, photos and service updates stay together here. For urgent help, call','Vos messages, photos et nouvelles du service sont regroupés ici. Pour une demande urgente, appelez le')} <a href="tel:+15146064030">514-606-4030</a>.</p><form id="portal-thread-form" class="portal-thread-form"><label>${t('Message the team','Écrire à l’équipe')}<textarea name="body" rows="4" maxlength="10000" required></textarea></label><button type="submit" class="btn-primary">${t('Send message','Envoyer le message')}</button><p class="small muted">${t('This sends a message to your Crown Movers team.','Ce message est transmis à votre équipe Crown Movers.')}</p></form><div id="portal-thread" aria-live="polite" class="portal-thread-loading">${t('Loading your updates…','Chargement des nouvelles…')}</div>`)+holder.innerHTML;
};
async function loadClientConversation(){
 const seq=++CT.sequence;
 try{
  const r=await req('/conversation?offset='+CT.offset),target=document.getElementById('portal-thread');if(!target||seq!==CT.sequence)return;
  const labels={'Service request received':['Service request received','Demande de service reçue'],'Client message':['Your message','Votre message'],'Message from Crown Movers':['Message from Crown Movers','Message de Crown Movers'],'Phone conversation with Crown Movers':['Phone conversation with Crown Movers','Conversation téléphonique avec Crown Movers'],'Moving details submitted':['Moving details submitted','Détails du déménagement transmis'],'Moving details reviewed':['Moving details reviewed','Détails examinés'],'File received':['File received','Fichier reçu'],'Photo / video folder added':['Photo / video folder added','Dossier photo / vidéo ajouté'],'Callback requested':['Callback requested','Rappel demandé'],'Estimate accepted':['Estimate accepted','Devis accepté'],'Service schedule':['Service schedule','Horaire du service'],'Invoice issued':['Invoice issued','Facture émise'],'Verified payment record':['Verified payment record','Paiement vérifié'],'Payment reversal recorded':['Payment reversal recorded','Annulation du paiement enregistrée']};
  target.innerHTML=r.items.map(x=>`<article class="inbox-event ${x.direction==='client'?'from-client':''}"><strong>${esc(labels[x.title]?t(...labels[x.title]):x.title)}</strong><p class="small muted">${esc(new Date(x.at).toLocaleString(P.fr?'fr-CA':'en-CA',{timeZone:(window.CROWNOPS_TZ||'America/Toronto')}))}${x.actor?' · '+esc(x.direction==='client'?t('You','Vous'):x.actor):''}${x.status?' · '+esc(x.status):''}</p>${x.body?'<p class="inbox-body">'+esc(x.body)+'</p>':''}</article>`).join('')+`<div class="inbox-pagination"><button data-thread-offset="${Math.max(0,CT.offset-40)}" ${!CT.offset?'disabled':''}>${t('Previous','Précédent')}</button><span>${r.total} ${t('updates','nouvelles')}</span><button data-thread-offset="${CT.offset+40}" ${CT.offset+40>=r.total?'disabled':''}>${t('Next','Suivant')}</button></div>`;if(r.total<=40)target.querySelector('.inbox-pagination')?.remove();
 }catch(e){const target=document.getElementById('portal-thread');if(target&&seq===CT.sequence){target.textContent=e.message;target.insertAdjacentHTML('beforeend',' <button data-thread-offset="'+CT.offset+'">'+t('Try again','Réessayer')+'</button>');}}
}
const conversationRender=render;
render=function(msg=''){conversationRender(msg);const tab=el.querySelector('.portal-tabs [data-tab="help"]');if(tab)tab.textContent=t('Messages & updates','Messages et nouvelles');if(P.tab==='help'){const f=document.getElementById('portal-thread-form');if(f)f.elements.body.value=CT.draft;loadClientConversation();}};
document.addEventListener('click',e=>{const b=e.target.closest('[data-thread-offset]');if(!b||b.disabled)return;CT.offset=Number(b.dataset.threadOffset);loadClientConversation();});
document.addEventListener('submit',async e=>{
 const f=e.target.closest('#portal-thread-form');if(!f)return;e.preventDefault();const b=e.submitter;if(b.disabled)return;b.disabled=true;
 try{if(!CT.request)CT.request=crypto.randomUUID();await req('/conversation',{method:'POST',data:{body:f.elements.body.value,request_key:CT.request}});CT.request=null;CT.draft='';CT.offset=0;f.reset();render(t('Thank you! Your message is with our team.','Merci! Votre message a été transmis à notre équipe.'));}
 catch(ex){document.getElementById('client-error').textContent=ex.message;}finally{b.disabled=false;}
});

document.addEventListener('input',e=>{if(e.target.closest('#portal-thread-form'))CT.draft=e.target.value;});
window.addEventListener('beforeunload',e=>{if(CT.draft.trim()){e.preventDefault();e.returnValue='';}});
