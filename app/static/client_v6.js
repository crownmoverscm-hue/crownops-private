/* CrownOps v6 client estimate presentation. */
'use strict';
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
        const r=q.result,service=q.inputs?.service_type||r.service_type||'Moving';
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
          '<div class="callout teal"><strong>'+t('Included limited liability','Responsabilite limitee incluse')+'</strong>'+
          t('Free limited liability coverage is $0.60/lb per article. TVs, large mirrors, glass, marble and other fragile items require suitable protection or boxing. Added protective packing alone does not increase liability coverage.','La responsabilite limitee gratuite est de 0,60 $/lb par article. Les televiseurs, grands miroirs, articles en verre, marbre et autres objets fragiles exigent une protection ou une boite appropriee. L emballage de protection additionnel n augmente pas a lui seul la couverture de responsabilite.')+
          '</div><div class="spacer"></div><p><strong>'+t('Deposit requirement','Depot requis')+':</strong> '+cash(q.deposit_required*100)+'</p>'+
          accept+depositNotice(q)+'</div>';
      }).join('')||empty(t('Your estimate will appear after review by Crown.','Votre devis apparaitra apres sa revision par Crown.'))
    )+panel(t('Confirmed services','Services confirmes'),
      P.data.jobs.map(j=>'<div class="activity-row"><div><strong>'+esc(j.phase)+'</strong><p>'+esc(new Date(j.start_at).toLocaleString(P.fr?'fr-CA':'en-CA',{timeZone:'America/Toronto'}))+' ET</p><span class="pill">'+esc(j.status)+'</span></div></div>').join('')||
      empty(t('No confirmed service is displayed yet.','Aucun service confirme n est encore affiche.')));
  };
}
