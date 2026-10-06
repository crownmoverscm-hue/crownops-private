/* CrownOps v6 client estimate presentation. */
'use strict';
if(typeof estimates==='function'){
  estimates=function(){
    const terms=P.data.terms;
    const quoteFrench=q=>String(q.inputs?.language||q.result?.language||'English').toLowerCase().startsWith('fr');
    const qt=(q,en,fr)=>quoteFrench(q)?fr:en;
    const lineTable=(q,r)=>{
      const lines=r.line_items||[];
      if(!lines.length)return '';
      return '<div class="table-wrap"><table><thead><tr><th>'+qt(q,'Service / item','Service / article')+'</th><th>'+qt(q,'Qty','Qte')+'</th><th>'+qt(q,'Price','Prix')+'</th><th>'+qt(q,'Total','Total')+'</th></tr></thead><tbody>'+
        lines.map(x=>'<tr><td><strong>'+esc(x.name)+'</strong>'+(x.note?'<div class="muted tiny">'+esc(x.note)+'</div>':'')+'</td><td>'+esc(x.qty??1)+'</td><td>'+cash(x.unit_cents)+'</td><td><strong>'+cash(x.total_cents)+'</strong></td></tr>').join('')+
        '</tbody></table></div>';
    };
    const depositFor=q=>{
      if(q.status!=='Accepted'||q.stale)return '';
      const d=q.deposit||{};
      return '<div class="callout"><p>'+qt(q,'Deposit verified','Depot verifie')+': '+cash(d.verified_cents)+'. '+qt(q,'Remaining deposit','Depot restant')+': '+cash(d.due_cents)+'</p>'+
        (d.pending_cents?'<p>'+qt(q,'Your recorded deposit is being verified. Please do not pay again.','Votre depot enregistre est en cours de verification. Ne payez pas une deuxieme fois.')+'</p>':
        d.due_cents>0?'<button type="button" data-client="deposit" data-id="'+esc(q.id)+'" class="btn-primary">'+qt(q,'Pay deposit securely by card','Payer le depot par carte de facon securisee')+'</button><p class="muted tiny">'+qt(q,'Payment alone does not confirm the schedule. Crown confirms the booking after the required deposit or an approved payment arrangement.','Le paiement seul ne confirme pas l horaire. Crown confirme la reservation apres le depot requis ou une entente de paiement approuvee.')+'</p>':'')+
        '</div>';
    };
    const pageFrench=P.data.quotes.length?quoteFrench(P.data.quotes[0]):P.fr;
    const pageT=(en,fr)=>pageFrench?fr:en;
    return panel(pageT('Your estimates','Vos devis'),
      P.data.quotes.map(q=>{
        const r=q.result,template=q.inputs?.template_snapshot||{},service=template.title||q.inputs?.service_type||r.service_type||'Moving';
        const arrival=q.inputs?.start_window_label||q.inputs?.start_time||qt(q,'To be confirmed','A confirmer');
        const accept=q.stale?notice(qt(q,'Your details changed. Crown must review this estimate again.','Vos details ont change. Crown doit reviser ce devis.')):
          q.status!=='Accepted'&&terms.approved?
            '<details><summary>'+qt(q,'Read booking terms','Lire les conditions')+'</summary><div class="message-draft">'+esc(terms.text)+'</div></details>'+
            '<form data-client-form="accept" data-id="'+esc(q.id)+'"><div class="grid2">'+
            f('signed_name',qt(q,'Your full name','Votre nom complet'),'','text','required')+
            cb('agree',qt(q,'I have read and accept this estimate and the booking terms.','J ai lu et j accepte ce devis et les conditions de reservation.'))+
            '</div><div class="spacer"></div><button class="btn-primary" type="submit">'+qt(q,'Accept estimate','Accepter le devis')+'</button></form>':
          q.status==='Accepted'?notice(qt(q,'Accepted. Your requested date is held as tentative until the required deposit or an approved payment arrangement confirms the booking.','Accepte. La date demandee est retenue de facon provisoire jusqu au depot requis ou a une entente de paiement approuvee.')):
            empty(qt(q,'Crown is preparing the final booking terms.','Crown prepare les conditions finales.'));
        const templateCopy=(template.liability||template.fragile)?'<div class="callout teal"><strong>'+esc(template.title||qt(q,'Service details','Details du service'))+'</strong>'+
          (template.liability?'<p>'+esc(template.liability)+'</p>':'')+(template.fragile?'<p>'+esc(template.fragile)+'</p>':'')+'</div><div class="spacer"></div>':'';
        return '<div class="panel-body" style="border-bottom:1px solid var(--line)">'+
          '<div class="row"><div><div class="eyebrow">'+esc(service)+'</div><h3>'+qt(q,'Estimate','Devis')+' #'+esc(q.revision)+'</h3></div><span class="pill">'+esc(q.status)+'</span></div>'+
          '<div class="quote-price">'+cash(r.low_cents)+' - '+cash(r.high_cents)+'</div>'+
          '<p><strong>'+qt(q,'Service date','Date du service')+':</strong> '+esc(q.inputs?.move_date||q.move_date||'')+'<br><strong>'+qt(q,'Arrival / start window','Fenetre d arrivee / debut')+':</strong> '+esc(arrival)+'</p>'+
          '<p class="muted small">'+qt(q,'Before applicable taxes. Service is billed under the accepted terms and actual service conditions unless a line is explicitly fixed.','Avant taxes applicables. Le service est facture selon les conditions acceptees et les conditions reelles, sauf lorsqu une ligne est explicitement forfaitaire.')+'</p>'+
          lineTable(q,r)+'<div class="spacer"></div>'+templateCopy+
          '<p><strong>'+qt(q,'Deposit requirement','Depot requis')+':</strong> '+cash(q.deposit_required*100)+'</p>'+
          accept+depositFor(q)+'</div>';
      }).join('')||empty(pageT('Your estimate will appear after review by Crown.','Votre devis apparaitra apres sa revision par Crown.'))
    )+panel(pageT('Confirmed services','Services confirmes'),
      P.data.jobs.map(j=>'<div class="activity-row"><div><strong>'+esc(j.phase)+'</strong><p>'+esc(new Date(j.start_at).toLocaleString(pageFrench?'fr-CA':'en-CA',{timeZone:'America/Toronto'}))+' ET</p><span class="pill">'+esc(j.status)+'</span></div></div>').join('')||
      empty(pageT('No confirmed service is displayed yet.','Aucun service confirme n est encore affiche.')));
  };
}
