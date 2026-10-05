/* CrownOps v6 client estimate presentation. */
'use strict';

// v6.1 deposit choices: show Interac instructions beside the secure card option.
if(typeof depositNotice==='function'){
  depositNotice=function(q){
    if(q.status!=='Accepted'||q.stale)return '';
    const d=q.deposit||{},opt=P.data.deposit_options||{},due=Number(d.due_cents||0);
    if(d.pending_cents)return '<div class="callout"><strong>'+t('Deposit pending verification','Dépôt en vérification')+'</strong><p>'+t('We recorded your deposit and are verifying it. Please do not pay a second time.','Nous avons enregistré votre dépôt et le vérifions. Ne payez pas une deuxième fois.')+'</p></div>';
    if(due<=0)return '<div class="callout teal"><strong>'+t('Deposit verified','Dépôt vérifié')+'</strong><p>'+t('Your booking is being confirmed by Crown Movers.','Votre réservation est en cours de confirmation par Crown Movers.')+'</p></div>';
    const et=opt.etransfer_email?'<div class="panel panel-body"><strong>'+t('Interac e-Transfer','Virement Interac')+'</strong><p>'+t('Send the required deposit to','Envoyez le dépôt requis à')+' <strong>'+esc(opt.etransfer_email)+'</strong>.</p>'+(opt.etransfer_note?'<p class="muted small">'+esc(opt.etransfer_note)+'</p>':'')+'<p class="tiny muted">'+t('Crown verifies the transfer before the booking is marked confirmed.','Crown vérifie le virement avant de confirmer la réservation.')+'</p></div>':'';
    const card='<div class="panel panel-body"><strong>'+t('Credit card','Carte de crédit')+'</strong><p>'+t('Use the secure payment page for the remaining deposit.','Utilisez la page de paiement sécurisée pour le dépôt restant.')+'</p><button type="button" data-client="deposit" data-id="'+esc(q.id)+'" class="btn-primary">'+t('Pay deposit securely','Payer le dépôt de façon sécurisée')+'</button></div>';
    return '<div class="callout"><p><strong>'+t('Remaining deposit','Dépôt restant')+':</strong> '+cash(due)+'</p><p class="muted small">'+t('Your requested date is held tentatively until the deposit is verified or Crown approves another payment arrangement.','Votre date demandée est retenue provisoirement jusqu’à la vérification du dépôt ou l’approbation d’une autre entente de paiement par Crown.')+'</p></div><div class="grid2">'+et+card+'</div>';
  };
}

if(typeof estimates==='function'){
  estimates=function(){
    const terms=P.data.terms;
    const lineTable=r=>{
      const lines=r.line_items||[];
      if(!lines.length)return '';
      return '<div class="table-wrap"><table><thead><tr><th>'+t('Service / item','Service / article')+'</th><th>'+t('Qty','Qte')+'</th><th>'+t('Price','Prix')+'</th><th>'+t('Total','Total')+'</th></tr></thead><tbody>'+
        lines.map(x=>'<tr><td><strong>'+esc(x.name)+'</strong>'+(x.note?'<div class="muted tiny">'+esc(x.note)+'</div>':'')+'</td><td>'+esc(x.qty??1)+'</td><td>'+cash(x.unit_cents)+'</td><td><strong>'+cash(x.total_cents)+'</strong></td></tr>').join('')+
        '</tbody></table></div>';
    };
    return panel(t('Your estimates','Vos devis'),
      P.data.quotes.map(q=>{
        const r=q.result,service=q.inputs?.service_type||r.service_type||'Moving',tpl=q.inputs?.template_body||{};
        const accept=q.stale?notice(t('Your details changed. Crown must review this estimate again.','Vos details ont change. Crown doit reviser ce devis.')):
          q.status!=='Accepted'&&terms.approved?
            '<details><summary>'+t('Read booking terms','Lire les conditions')+'</summary><div class="message-draft">'+esc(terms.text)+'</div></details>'+
            '<form data-client-form="accept" data-id="'+esc(q.id)+'"><div class="grid2">'+
            f('signed_name',t('Your full name','Votre nom complet'),'','text','required')+
            cb('agree',t('I have read and accept this estimate and the booking terms.','J ai lu et j accepte ce devis et les conditions de reservation.'))+
            '</div><div class="spacer"></div><button class="btn-primary" type="submit">'+t('Accept estimate','Accepter le devis')+'</button></form>':
          q.status==='Accepted'?notice(t('Accepted. Your requested date is held as tentative until the required deposit or an approved payment arrangement confirms the booking.','Accepte. La date demandee est retenue de facon provisoire jusqu au depot requis ou a une entente de paiement approuvee.')):
            empty(t('Crown is preparing the final booking terms.','Crown prepare les conditions finales.'));
        return '<div class="panel-body" style="border-bottom:1px solid var(--line)">'+
          '<div class="row"><div><div class="eyebrow">'+esc(service)+'</div><h3>'+t('Estimate','Devis')+' #'+esc(q.revision)+'</h3></div><span class="pill">'+esc(q.status)+'</span></div>'+
          '<div class="quote-price">'+cash(r.low_cents)+' - '+cash(r.high_cents)+'</div>'+
          '<p class="muted small">'+t('Before applicable taxes. Service is billed under the accepted terms and actual service conditions unless a line is explicitly fixed.','Avant taxes applicables. Le service est facture selon les conditions acceptees et les conditions reelles, sauf lorsqu une ligne est explicitement forfaitaire.')+'</p>'+
          lineTable(r)+'<div class="spacer"></div>'+
          (tpl.included?'<div class="callout teal"><strong>'+t('Included / service notes','Inclus / notes de service')+'</strong>'+esc(tpl.included)+'</div><div class="spacer"></div>':'')+
          '<div class="callout teal"><strong>'+t('Included limited liability','Responsabilité limitée incluse')+'</strong>'+
          esc(tpl.liability||t('Free limited liability coverage is $0.60/lb per article.','La responsabilité limitée gratuite est de 0,60 $/lb par article.'))+
          (tpl.fragile?'<p class="small" style="margin-top:8px">'+esc(tpl.fragile)+'</p>':'')+
          '</div><div class="spacer"></div>'+
          (tpl.date_change_terms?'<div class="message-draft"><strong>'+t('Date-change terms','Conditions de changement de date')+'</strong><br>'+esc(tpl.date_change_terms)+'</div><div class="spacer"></div>':'')+
          '<p><strong>'+t('Deposit requirement','Dépôt requis')+':</strong> '+cash(q.deposit_required*100)+'</p>'+
          accept+depositNotice(q)+'</div>';
      }).join('')||empty(t('Your estimate will appear after review by Crown.','Votre devis apparaitra apres sa revision par Crown.'))
    )+panel(t('Confirmed services','Services confirmes'),
      P.data.jobs.map(j=>'<div class="activity-row"><div><strong>'+esc(j.phase)+'</strong><p>'+esc(new Date(j.start_at).toLocaleString(P.fr?'fr-CA':'en-CA',{timeZone:'America/Toronto'}))+' ET</p><span class="pill">'+esc(j.status)+'</span></div></div>').join('')||
      empty(t('No confirmed service is displayed yet.','Aucun service confirme n est encore affiche.')));
  };
}
