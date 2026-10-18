/* CrownOps v6 client estimate presentation. */
'use strict';
if(typeof estimates==='function'){
  estimates=function(){
    const terms=P.data.terms;
    const quoteFrench=q=>P.fr;
    const qt=(q,en,fr)=>quoteFrench(q)?fr:en;
    const quoteStatus=q=>q.expired?qt(q,'Expired — request an update','Expiré — demandez une mise à jour'):q.stale?qt(q,'Needs review','À réviser'):q.status==='Approved'?qt(q,'Ready for your acceptance','Prêt à accepter'):q.status==='Accepted'?qt(q,'Accepted','Accepté'):q.status;
    const lineNote=x=>x.note==='Client-facing transportation amount.'?'Transportation charge for the crew and truck.':x.note==='Montant de transport presente au client.'?'Frais de transport pour l’équipe et le camion.':x.note;
    const lineTable=(q,r)=>{
      const lines=r.line_items||[];
      if(!lines.length)return '';
      return '<div class="table-wrap client-lines"><table><thead><tr><th>'+qt(q,'Service / item','Service / article')+'</th><th>'+qt(q,'Qty','Qte')+'</th><th>'+qt(q,'Price','Prix')+'</th><th>'+qt(q,'Total','Total')+'</th></tr></thead><tbody>'+
        lines.map(x=>'<tr><td><strong>'+esc(x.name)+'</strong>'+(x.note?'<div class="muted tiny">'+esc(lineNote(x))+'</div>':'')+'</td><td data-label="'+qt(q,'Qty','Qté')+'">'+esc(x.qty??1)+'</td><td data-label="'+qt(q,'Price','Prix')+'">'+cash(x.unit_cents)+'</td><td data-label="Total"><strong>'+cash(x.total_cents)+'</strong></td></tr>').join('')+
        '</tbody></table></div>';
    };
    const depositFor=q=>{
      if(q.status!=='Accepted'||q.stale||(q.availability?.status==='Fully booked'&&!q.booking_confirmed))return '';
      const d=q.deposit||{},policy=P.data.deposit_policy||{enabled:false};
      const interac=policy.enabled&&d.due_cents>0?'<div class="panel panel-body" style="margin-top:12px"><strong>'+qt(q,'Interac e-Transfer','Virement Interac')+'</strong><p>'+qt(q,'Send the deposit to','Envoyez le depot a')+': <strong>'+esc(policy.recipient_email)+'</strong>'+(policy.recipient_name?' · '+esc(policy.recipient_name):'')+'</p>'+(policy.auto_deposit?'<p class="tiny muted">'+qt(q,'Auto-deposit is enabled. No password is required.','Le depot automatique est active. Aucun mot de passe n est requis.')+'</p>':'')+(policy.reference_note?'<p class="tiny muted">'+esc(policy.reference_note)+'</p>':'')+'<p class="tiny muted">'+qt(q,'Your booking is confirmed only after Crown verifies receipt of the required deposit.','Votre reservation est confirmee seulement apres verification du depot requis par Crown.')+'</p></div>':'';
      return '<div class="callout"><strong>'+qt(q,d.pending_cents?'Deposit verification pending':d.due_cents>0?'Deposit pending':'Deposit requirement satisfied',d.pending_cents?'Dépôt en attente de vérification':d.due_cents>0?'Dépôt à payer':'Exigence de dépôt satisfaite')+'</strong><p>'+qt(q,'Deposit verified','Depot verifie')+': '+cash(d.verified_cents)+'. '+qt(q,'Remaining deposit','Depot restant')+': '+cash(d.due_cents)+'</p>'+
        (d.pending_cents?'<p>'+qt(q,'Your recorded deposit is being verified. Please do not pay again.','Votre depot enregistre est en cours de verification. Ne payez pas une deuxieme fois.')+'</p>':
        d.due_cents>0?(P.data.card_deposit_available?'<div class="row wrap"><button type="button" data-client="deposit" data-id="'+esc(q.id)+'" class="btn-primary">'+qt(q,'Pay securely by card','Payer par carte de facon securisee')+'</button></div>':'')+interac+(!P.data.card_deposit_available&&!policy.enabled?'<p class="muted tiny">'+qt(q,'Contact Crown Movers for the approved deposit method.','Communiquez avec Crown Movers pour le mode de depot approuve.')+'</p>':'')+'<p class="muted tiny">'+qt(q,'Payment alone does not confirm the schedule. Crown confirms the booking after the required deposit or an approved payment arrangement.','Le paiement seul ne confirme pas l horaire. Crown confirme la reservation apres le depot requis ou une entente de paiement approuvee.')+'</p>':'')+
        '</div>';
    };
    const pageFrench=P.data.quotes.length?quoteFrench(P.data.quotes[0]):P.fr;
    const pageT=(en,fr)=>pageFrench?fr:en;
    return panel(pageT('Your estimates','Vos devis'),
      P.data.quotes.map(q=>{
        const r=q.result,template=q.inputs?.template_snapshot||{},service=q.inputs?.quote_title||template.title||q.inputs?.service_type||r.service_type||'Moving';
        const arrival=q.inputs?.start_window_label||q.inputs?.start_time||qt(q,'To be confirmed','A confirmer');
        const route=(q.pickup?'<p><strong>'+qt(q,'Pickup','Départ')+':</strong><br>'+esc(q.pickup)+'</p>':'')+(q.dropoff?'<p><strong>'+qt(q,'Destination','Destination')+':</strong><br>'+esc(q.dropoff)+'</p>':'');
        const accept=q.expired?notice(qt(q,'This estimate has expired. Request a callback and we will help you update it.','Ce devis a expiré. Demandez un rappel pour le mettre à jour.')):(q.availability?.status==='Fully booked'&&!q.booking_confirmed)?notice(qt(q,'This requested date is fully booked. Please request a callback to choose another date. Existing confirmed services remain scheduled.','Cette date souhaitée est complète. Demandez un rappel pour choisir une autre date. Les services déjà confirmés restent à l’horaire.')):q.stale?notice(qt(q,'Your details changed. Crown must review this estimate again.','Vos details ont change. Crown doit reviser ce devis.')):
          q.status!=='Accepted'&&terms.approved?
            '<details><summary>'+qt(q,'Read booking terms','Lire les conditions')+'</summary><div class="message-draft">'+esc(terms.text)+'</div></details>'+
            '<form data-client-form="accept" data-id="'+esc(q.id)+'"><div class="grid2">'+
            f('signed_name',qt(q,'Your full name','Votre nom complet'),'','text','required')+
            cb('agree',qt(q,'I have read and accept this estimate and the booking terms.','J ai lu et j accepte ce devis et les conditions de reservation.'))+
            '</div><div class="spacer"></div><button class="btn-primary" type="submit">'+qt(q,'Accept estimate','Accepter le devis')+'</button></form>':
          q.status==='Accepted'?notice(qt(q,'Estimate accepted. Acceptance alone does not confirm your booking. Crown confirms the date after checking the required deposit or approved payment arrangement and availability. See Confirmed services below for your booking status.','Devis accepté. L’acceptation seule ne confirme pas la réservation. Crown confirme la date après vérification du dépôt requis ou de l’entente de paiement approuvée et des disponibilités. Consultez les Services confirmés ci-dessous pour connaître l’état de votre réservation.')):
            empty(qt(q,'Crown is preparing the final booking terms.','Crown prepare les conditions finales.'));
        const templateCopy=(template.liability||template.fragile)?'<div class="callout teal"><strong>'+esc(template.title||qt(q,'Service details','Details du service'))+'</strong>'+
          (template.liability?'<p>'+esc(template.liability)+'</p>':'')+(template.fragile?'<p>'+esc(template.fragile)+'</p>':'')+'</div><div class="spacer"></div>':'';
        return '<div class="panel-body" style="border-bottom:1px solid var(--line)">'+
          '<div class="row" data-estimate-view="'+esc(q.id)+'"><div><div class="eyebrow">'+esc(service)+'</div><h3>'+qt(q,'Estimate','Devis')+' #'+esc(q.revision)+'</h3></div><span class="pill">'+esc(quoteStatus(q))+'</span></div>'+
          '<div class="row wrap"><button data-client="print">'+qt(q,'Print / save estimate','Imprimer / enregistrer le devis')+'</button></div><div class="quote-price">'+cash(r.low_cents)+' - '+cash(r.high_cents)+'</div>'+
          '<p><strong>'+qt(q,'Requested service date','Date souhaitée du service')+':</strong> '+esc(q.inputs?.move_date||q.move_date||'')+'<br><strong>'+qt(q,'Arrival / start window','Fenetre d arrivee / debut')+':</strong> '+esc(arrival)+'</p>'+route+
          '<p class="muted small">'+qt(q,'Before applicable taxes. Service is billed under the accepted terms and actual service conditions unless a line is explicitly fixed.','Avant taxes applicables. Le service est facture selon les conditions acceptees et les conditions reelles, sauf lorsqu une ligne est explicitement forfaitaire.')+'</p>'+
          (q.inputs?.expiry_date?'<p>'+qt(q,'Valid through: ','Valide jusqu’au : ')+esc(q.inputs.expiry_date)+'</p>':'')+lineTable(q,r)+'<div class="spacer"></div>'+(q.inputs?.client_note?'<div class="message-draft">'+esc(q.inputs.client_note)+'</div><div class="spacer"></div>':'')+templateCopy+
          (q.inputs?.billing?'<div class="callout"><strong>'+qt(q,'Bill to','Facturer à')+'</strong><p>'+esc(q.inputs.billing.company||q.inputs.billing.name)+'<br>'+esc(q.inputs.billing.address||'')+'</p>'+(q.inputs.payment_terms?'<p>'+qt(q,'Payment terms: ','Conditions de paiement : ')+esc(q.inputs.payment_terms)+'</p>':'')+'</div>':'')+'<p><strong>'+qt(q,'Deposit requirement','Depot requis')+':</strong> '+cash(q.deposit_required*100)+'</p>'+
          accept+depositFor(q)+'</div>';
      }).join('')||empty(pageT('Your estimate will appear after review by Crown.','Votre devis apparaitra apres sa revision par Crown.'))
    )+panel(pageT('Confirmed services','Services confirmes'),
      P.data.jobs.map(j=>'<div class="activity-row"><div><strong>'+esc(j.phase)+'</strong><p>'+esc(new Date(j.start_at).toLocaleString(pageFrench?'fr-CA':'en-CA',{timeZone:'America/Toronto'}))+' ET</p><span class="pill">'+esc(j.status)+'</span></div></div>').join('')||
      empty(pageT('No confirmed service is displayed yet.','Aucun service confirme n est encore affiche.')));
  };
}

// The Files page reflects the server's supported upload mode.
const clientLegacyFiles=uploads;
uploads=function(){
 const policy=P.data.upload_policy;
 if(!policy||(policy.enabled&&policy.mode!=='photos'))return clientLegacyFiles();
 const ready=CrownPhotos.ready;
 const form=policy.enabled?'<form data-photo-client data-max-mb="'+esc(policy.max_file_mb)+'"><div class="grid2"><div class="form-field"><label>'+t('Photo / video type','Type de photo / vidéo')+'</label><select name="category"><option value="Move photos">'+t('Furniture / inventory','Meubles / inventaire')+'</option><option value="Access">'+t('Stairs / elevator / parking','Escaliers / ascenseur / stationnement')+'</option><option value="Incident">'+t('Condition / issue','État / problème')+'</option></select></div><div class="form-field media-drop"><label>'+t('Choose up to 10 photos / short videos','Choisissez jusqu’à 10 photos / courtes vidéos')+' · '+esc(policy.max_file_mb)+' MB</label><input type="file" name="file" accept="'+esc(policy.accept)+'" multiple required></div></div><p id="client-photo-progress" role="status"></p><button class="btn-primary" type="submit">'+t('Upload files','Téléverser les fichiers')+'</button></form>':'';
 const message=policy.enabled?t(policy.message,'Ajoutez vos photos (JPEG, PNG, WebP ou HEIC) et courtes vidéos (MP4, MOV ou WebM), ou partagez un lien vers votre dossier de photos et vidéos.'):t('Photo uploads are temporarily unavailable. Contact Crown Movers to add photos to your moving file.','Le téléversement est temporairement indisponible. Communiquez avec Crown Movers pour ajouter des photos à votre dossier.');
 const gallery=P.data.attachments.map(a=>ready(a)&&a.mime.startsWith('image/')?'<a class="photo-tile" href="/api/portal/uploads/'+encodeURIComponent(a.id)+'" target="_blank" rel="noopener"><img src="/api/portal/uploads/'+encodeURIComponent(a.id)+'" alt="'+esc(a.category)+'" loading="lazy"><span>'+esc(a.category)+'</span></a>':'<div class="activity-row"><strong>'+esc(a.original_name)+'</strong> '+(ready(a)?'<a href="/api/portal/uploads/'+encodeURIComponent(a.id)+'" target="_blank" rel="noopener">'+t('Open','Ouvrir')+'</a>':esc(a.scan_status))+'</div>').join('');
 const folders='<section class="media-link-box"><h3>'+t('Share a photo / video folder','Partager un dossier de photos / vidéos')+'</h3><p>'+t('Paste the link you already have. Make sure Crown Movers can open it.','Collez le lien que vous avez déjà. Assurez-vous que Crown Movers puisse l’ouvrir.')+'</p>'+(P.data.media_links||[]).map(l=>'<p><a class="button-link" href="'+esc(l.url)+'" target="_blank" rel="noopener noreferrer">'+esc(l.label)+' ↗</a></p>').join('')+'<form data-client-form="media-link"><div class="grid2">'+f('label',t('Folder name (optional)','Nom du dossier (facultatif)'))+f('url',t('Photo / video folder link','Lien du dossier photo / vidéo'),'','text','required placeholder="https://drive.google.com/…"')+'</div><button type="submit">'+t('Save folder link','Enregistrer le lien')+'</button></form></section>';
 return panel(t('Photos & videos','Photos et vidéos'),folders+notice(message)+form)+panel(t('Your files','Vos fichiers'),gallery?'<div class="photo-grid">'+gallery+'</div>':empty(t('No files uploaded.','Aucun fichier téléversé.')));
};
document.addEventListener('submit',async e=>{
 const form=e.target.closest('form[data-photo-client]');if(!form)return;e.preventDefault();
 const button=form.querySelector('button[type="submit"]');if(button.disabled)return;button.disabled=true;
 try{
  const result=await CrownPhotos.batch(form,data=>req('/uploads',{method:'POST',form:data}),msg=>{const el=document.getElementById('client-photo-progress');if(el)el.textContent=msg;});
  if(result.saved&&!result.error)await load(t(result.saved+' file(s) saved.',result.saved+' fichier(s) enregistré(s).'));
  if(result.error)error(new Error(result.saved+' / '+result.total+' · '+result.error));
 }catch(ex){error(ex);}finally{if(button.isConnected)button.disabled=false;}
});
