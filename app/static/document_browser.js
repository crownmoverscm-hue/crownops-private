/* Readable financial lists. Every shortcut uses the existing reviewed workflow. */
'use strict';
(function(){
 const state={quotes:{period:'all',status:'all',search:'',history:false,page:0,sort:'newest'},invoices:{period:'all',status:'all',search:'',page:0,sort:'newest'}};
 let records={quotes:[],invoices:[]},visible=[],menu=null,trigger=null,timer;
 const office=()=>['owner','manager','sales'].includes(S.user?.role),admin=()=>['owner','manager'].includes(S.user?.role);
 const day=value=>value?(/^\d{4}-\d{2}-\d{2}$/.test(value)?value:new Intl.DateTimeFormat('en-CA',{timeZone:'America/Toronto',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value))):'';
 const showDay=value=>value?h(uxDateLabel(day(value),'Not set')):'<span class="muted">Not set</span>';
 function inPeriod(doc,kind){
  const p=state[kind].period;if(p==='all')return true;
  const value=day(kind==='quotes'?doc.created_at:doc.issued_at||doc.created_at),today=day(new Date());
  const start=p==='month'?today.slice(0,8)+'01':new Date(Date.parse(today+'T12:00:00Z')-(Number(p)-1)*86400000).toISOString().slice(0,10);
  return value>=start&&value<=today;
 }
 function options(values,current){return values.map(([v,label])=>`<option value="${h(v)}" ${v===current?'selected':''}>${h(label)}</option>`).join('');}
 function selectBox(key,label,values,current){return `<label>${label}<select data-doc-control="${key}" aria-label="${label}">${options(values,current)}</select></label>`;}
 function metric(label,value,detail){return `<div class="doc-metric"><strong>${value}</strong><span>${h(label)}</span><small>${h(detail||'')}</small></div>`;}
 function menuButton(kind,d){return `<button type="button" class="doc-more" data-doc-menu="${kind}" data-id="${h(d.id)}" aria-label="Actions for ${h(kind==='quotes'?d.client_name+' revision '+d.revision:d.number)}" aria-haspopup="menu" aria-expanded="false">⋯</button>`;}
 function rowActions(kind,d){return `<div class="doc-row-actions"><button class="btn-small" data-doc-action="open" data-kind="${kind}" data-id="${h(d.id)}">Open</button>${menuButton(kind,d)}</div>`;}
 function delivery(d){return `<div class="doc-delivery">${h(d.email_status==='Sent'?'Email sent':d.email_status==='Not sent'?'Email not sent':'Email: '+d.email_status)}${d.sent_at?'<span>'+showDay(d.sent_at)+'</span>':''}</div>`;}
 async function render(kind){
  closeMenu(false);
  records[kind]=await api(kind==='quotes'?'/documents/quotes':'/invoices');
  let all=records[kind],s=state[kind];
  if(kind==='quotes'&&!s.history){const latest=new Map();for(const q of all){if(!latest.has(q.lead_id)||latest.get(q.lead_id).revision<q.revision)latest.set(q.lead_id,q);}all=[...latest.values()];}
  const period=all.filter(d=>inPeriod(d,kind)),count=key=>period.filter(d=>d.display_state===key).length;
  const q=kind==='quotes';
  const statuses=q?['Draft','Ready','Sent','Accepted','Invoiced','Expired']:['Draft','Correction draft','Balance due','Partial','Overdue','Paid'];
  const search=s.search.trim().toLowerCase();
  visible=period.filter(d=>(s.status==='all'||(s.status==='Viewed'?d.opens>0:s.status==='Overdue'?d.overdue:d.display_state===s.status))&&(!search||[d.id,d.number,d.client_name,d.email,d.inputs?.quote_title,d.inputs?.move_date,d.inputs?.pickup,d.inputs?.dropoff,d.service_date,d.due_date,d.display_state].join(' ').toLowerCase().includes(search)));
  visible.sort((a,b)=>s.sort==='customer'?String(a.client_name).localeCompare(String(b.client_name)):s.sort==='amount'?Number(q?b.result?.high_cents:b.billing_total_cents)-Number(q?a.result?.high_cents:a.billing_total_cents):String((s.sort==='service'?a.inputs?.move_date||a.service_date:a.created_at)||'').localeCompare(String((s.sort==='service'?b.inputs?.move_date||b.service_date:b.created_at)||''))*(s.sort==='newest'?-1:1));
  const maxPage=Math.max(0,Math.ceil(visible.length/25)-1);s.page=Math.min(s.page,maxPage);const subset=visible.slice(s.page*25,s.page*25+25);
  const outstanding=period.reduce((v,d)=>v+(d.outstanding_cents||0),0),paid=period.reduce((v,d)=>v+(d.verified_paid_cents||0),0),overdue=period.filter(d=>d.overdue).reduce((v,d)=>v+d.outstanding_cents,0);
  const noDue=period.filter(d=>d.has_issued_revision&&d.outstanding_cents>0&&!d.due_date).length;
  const summary=q?
   metric('Sent',count('Sent'),'Awaiting acceptance')+metric('Viewed',period.filter(d=>d.opens>0).length,'Estimates opened in the client portal')+metric('Accepted',count('Accepted'),'Not yet invoiced')+metric('Invoiced',count('Invoiced'),'Linked issued invoice')+metric('Expired',count('Expired'),'Needs a revised estimate'):
   metric('Outstanding',money(outstanding),'Includes overdue balances')+metric('Overdue',money(overdue),'Unpaid after the saved due date')+metric('Verified paid',money(paid),'Payments applied to these invoices')+metric('Drafts',count('Draft')+count('Correction draft'),'Awaiting final review');
  const overview=`<section class="panel doc-overview"><div class="panel-head"><h2>Overview</h2>${selectBox('period','Date range',[['all','All time'],['30','Last 30 days'],['90','Last 90 days'],['month','This month']],s.period)}</div><div class="doc-metrics">${summary}</div><p class="doc-footnote">${q?'Based on estimate creation date. Viewed counts portal opens, not proof of reading.':`Based on issue date; unissued drafts use creation date. Verified paid includes allocated deposits, net of reversals. ${noDue?noDue+' unpaid invoice(s) have no due date and are not counted as overdue.':''}`}</p></section>`;
  const filters=`<div class="doc-toolbar">${selectBox('status','Status',[['all','All statuses'],...statuses.map(v=>[v,v]),...(q?[['Viewed','Viewed']]:[])],s.status)}${selectBox('sort','Sort by',[['newest','Newest first'],['customer','Customer'],['service','Service date'],['amount','Highest amount']],s.sort)}<label class="doc-search">Search<input data-doc-search value="${h(s.search)}" placeholder="${q?'Client, quote, date or address':'Invoice, client or date'}" aria-label="Search ${q?'estimates':'invoices'}"></label><button data-doc-export>Export view (CSV)</button>${q?`<label class="doc-history"><input type="checkbox" data-doc-history ${s.history?'checked':''}>Include earlier revisions</label>`:''}</div>`;
  const rows=subset.map(d=>q?[
   `<div class="client-name">${h(d.client_name)}</div><div class="tiny muted">${h(d.id)} · r${d.revision}</div><div class="doc-title">${h(d.inputs?.quote_title||d.inputs?.service_type||'Moving estimate')}</div>`,
   showDay(d.inputs?.move_date),showDay(d.created_at),
   `${h(d.result?.movers||'—')} movers<div class="tiny muted">${h(d.result?.trucks||'—')} truck(s)</div>`,
   `<strong>${money(d.result?.low_cents||0)} – ${money(d.result?.high_cents||0)}</strong><div class="tiny muted">Before tax</div>`,
   pill(d.display_state)+delivery(d)+(d.inputs?.expiry_date?'<div class="tiny muted">Expires '+showDay(d.inputs.expiry_date)+'</div>':''),
   d.opens?`<strong>${d.opens} open${d.opens===1?'':'s'}</strong><div class="tiny muted">Last: ${h(uxTimestampLabel(d.last_viewed_at))}</div>`:'<span class="muted">No tracked opens</span>',rowActions(kind,d)
  ]:[
   `<strong>${h(d.number)}</strong><div class="tiny muted">Revision ${d.revision}</div>`,h(d.client_name),showDay(d.issued_at),showDay(d.due_date),
   `<strong>${money(d.has_issued_revision?d.billing_total_cents:d.calculation.total_cents)}</strong>${d.display_state==='Correction draft'?'<div class="tiny muted">Last issued total</div>':''}`,
   `<strong>${money(d.has_issued_revision?d.outstanding_cents:d.calculation.balance_cents)}</strong><div class="tiny muted">${d.has_issued_revision?money(d.verified_paid_cents)+' verified paid':'Draft amount'}</div>`,
   pill(d.display_state)+delivery(d),rowActions(kind,d)
  ]);
  return head(q?'Estimates':'Invoices',q?'Track each estimate from draft to client acceptance and invoice.':S.user.role==='crew'?'Prepare and follow invoices for your assigned services.':'Track balances, due dates and client delivery in one place.',q&&office()?'<button class="btn-primary" data-crm="quote-new">'+icon('plus')+'New quote</button>':btn('go-schedule','Open schedule','btn-soft'))+
   overview+'<div class="spacer"></div><section class="panel doc-browser">'+filters+(rows.length?xtable(q?['Customer / quote','Move date','Created','Crew','Estimate','Status','Client activity','Actions']:['Invoice','Customer','Issued','Due','Total','Balance','Status','Actions'],rows):empty('No '+(q?'estimates':'invoices')+' in this view','Change the date range, search or status filter.'))+
   `<div class="doc-pagination"><span>${visible.length?s.page*25+1:0}–${Math.min((s.page+1)*25,visible.length)} of ${visible.length}</span><button data-doc-page="-1" ${s.page===0?'disabled':''}>Previous</button><button data-doc-page="1" ${s.page===maxPage?'disabled':''}>Next</button></div></section>`;
 }
 X.pageRenderers.quotes=()=>render('quotes');X.pageRenderers.invoices=()=>render('invoices');
 function closeMenu(focus=true){if(menu){menu.remove();menu=null;}if(trigger){trigger.setAttribute('aria-expanded','false');if(focus&&trigger.isConnected)trigger.focus();trigger=null;}}
 function openMenu(button){
  if(trigger===button){closeMenu();return;}closeMenu(false);trigger=button;
  const kind=button.dataset.docMenu,d=records[kind].find(x=>x.id===button.dataset.id);if(!d)return;
  const q=kind==='quotes',actions=[['open','Preview / details']];
  if(q){
   if(office())actions.push(['edit',d.status==='Draft'?'Edit draft':'Create revised quote'],['duplicate','Duplicate as new revision']);
   if(office()&&['Draft','Approved'].includes(d.status)&&d.email_status!=='Sent')actions.push(['send','Review & send estimate']);
   if(office()&&['Approved','Accepted'].includes(d.status)&&d.display_state!=='Expired')actions.push(['share','Share private client link']);
   if(d.invoices?.length)actions.push(['invoice','Open linked invoice']);
   else if(office()&&d.status==='Accepted')actions.push(['prepare','Prepare job invoice']);
   actions.push(['print','Print / save PDF']);
  }else{
   actions.push(['edit',d.status==='Draft'?'Edit draft':'Review correction']);
   if(admin()&&d.status==='Draft')actions.push(['due','Set due date']);
   if(d.status==='Issued')actions.push(['send','Email / delivery details'],['share','Share private invoice link'],['collect','Record / review payment'],['payment','Check QuickBooks payment link'],['print','Print / save PDF']);
   actions.push(['job','Open service']);
  }
  menu=document.createElement('div');menu.className='doc-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label',q?'Estimate actions':'Invoice actions');
  menu.innerHTML=actions.map(([a,label])=>`<button role="menuitem" data-doc-action="${a}" data-kind="${kind}" data-id="${h(d.id)}">${h(label)}</button>`).join('');
  document.body.append(menu);button.setAttribute('aria-expanded','true');
  if(innerWidth>600){const r=button.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(r.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(r.bottom+5,innerHeight-menu.offsetHeight-8))+'px';}
  menu.querySelector('button').focus();
 }
 async function share(kind,d){
  const label=kind==='quotes'?'This private link opens the client’s moving file, including their estimates.':'This private link opens this invoice only.';
  showModal('Share with this client',`<p>${label} Share it only with the intended client.</p><p>No email is sent by preparing a link.</p>`,`<button data-action="close">Cancel</button><button class="btn-primary" data-doc-create-link data-kind="${kind}" data-id="${h(d.id)}">Prepare private link</button>`);
 }
 async function action(button){
  const {kind,id}=button.dataset,d=records[kind].find(x=>x.id===id),a=button.dataset.docAction;if(!d)return;closeMenu(false);
  if(a==='print'){window.open('/api/documents/'+kind+'/'+encodeURIComponent(id)+'/print','_blank','noopener');return;}
  if(a==='share')return share(kind,d);
  if(a==='job')return openJob(d.job_id);
  if(a==='collect')return collectionPanel(id);
  if(kind==='quotes'){
   if(a==='edit'||a==='duplicate'){const detail=await api('/crm/quotes/'+encodeURIComponent(id));await crmQuoteBuilder(detail.lead.id,{seed:detail.quote.inputs,result:detail.quote.result,templateKey:detail.quote.inputs.template_key,editorView:'document'});if(a==='duplicate')toast('Review the copied details. Saving creates a new draft revision for this client.');return;}
   if(a==='invoice')return openInvoice(d.invoices[0].id);
   if(a==='prepare'){
    const detail=await api('/crm/quotes/'+encodeURIComponent(id));
    if(!detail.job)return toast('Plan and confirm the service before preparing its invoice.');
    if(!['Confirmed','In progress','Completed'].includes(detail.job.status))return openJob(detail.job.id);
    if(!confirm('Prepare a draft invoice from this job’s accepted quote? Actual hours and final review are still required.'))return;
    const result=await api('/jobs/'+encodeURIComponent(detail.job.id)+'/invoice-from-quote',{method:'POST',data:{}});return openInvoice(result.id);
   }
   await crmOpenQuote(id);if(a==='send')modal.querySelector('[data-crm="quote-finalize"]')?.focus();return;
  }
  if(a==='due'){
   const current=await api('/invoices/'+encodeURIComponent(id));
   return showModal('Invoice due date',`<form id="doc-due-form" data-id="${h(id)}" data-version="${current.version}">${field('due_date','Agreed payment due date',current.terms?.due_date||'','date')}<p>Use the date agreed with the client. Leave blank if it has not been agreed. It will appear on the finalized invoice.</p></form>`,btn('close','Cancel')+'<button class="btn-primary" type="submit" form="doc-due-form">Save due date</button>');
  }
  await openInvoice(id);
  if(a==='send')modal.querySelector('.invoice-delivery-state')?.scrollIntoView({block:'start'});
  if(a==='payment')modal.querySelector('[data-quickbooks-payment]')?.click();
  if(a==='edit'&&d.status==='Issued')modal.querySelector('[data-action="correct-invoice"]')?.focus();
 }
 document.addEventListener('click',async e=>{
  const toggle=e.target.closest('[data-doc-menu]');if(toggle){e.preventDefault();return openMenu(toggle);}
  if(menu&&!menu.contains(e.target))closeMenu(false);
  const b=e.target.closest('[data-doc-action],[data-doc-create-link],[data-doc-copy],[data-doc-page],[data-doc-export]');if(!b)return;e.preventDefault();if(b.disabled)return;
  b.disabled=true;
  try{
   if(b.hasAttribute('data-doc-action'))await action(b);
   if(b.hasAttribute('data-doc-create-link')){
    const kind=b.dataset.kind,d=records[kind].find(x=>x.id===b.dataset.id);
    const r=await api('/documents/'+kind+'/'+encodeURIComponent(d.id)+'/share',{method:'POST',data:{confirmed:true,version:d.version,revision:d.revision}});
    showModal('Private client link',`<p>For <strong>${h(r.recipient)}</strong> only. Keep this link private. No email has been sent.</p><label>Private link<input id="doc-private-link" readonly value="${h(r.url)}"></label><p class="small muted">If the link expires, prepare a fresh link.</p>`,btn('close','Close')+'<button class="btn-primary" data-doc-copy>Copy link</button>');
   }
   if(b.hasAttribute('data-doc-copy')){const input=document.getElementById('doc-private-link');try{await navigator.clipboard.writeText(input.value);toast('Private link copied.');}catch{input.focus();input.select();toast('Select Copy to copy this private link.');}}
   if(b.hasAttribute('data-doc-page')){state[S.page].page+=Number(b.dataset.docPage);await navigate(S.page);}
   if(b.hasAttribute('data-doc-export')){
    const q=S.page==='quotes',csv=v=>{let s=String(v??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
    const data=[q?['Quote','Client','Revision','Created','Move date','Low CAD before tax','High CAD before tax','Status','Opens']:['Invoice','Client','Issued','Due','Total CAD','Outstanding CAD','Verified paid CAD','Status'],...visible.map(d=>q?[d.id,d.client_name,d.revision,day(d.created_at),d.inputs?.move_date,(d.result?.low_cents||0)/100,(d.result?.high_cents||0)/100,d.display_state,d.opens]:[d.number,d.client_name,day(d.issued_at),d.due_date,d.billing_total_cents/100,d.outstanding_cents/100,d.verified_paid_cents/100,d.display_state])];
    const url=URL.createObjectURL(new Blob(['\ufeff'+data.map(row=>row.map(csv).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='crownops-'+S.page+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   }
  }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
 });
 document.addEventListener('keydown',e=>{
  if(!menu)return;const buttons=[...menu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
  if(e.key==='Escape'){e.preventDefault();closeMenu();}
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();}
  if(e.key==='Tab')closeMenu(false);
 });
 document.addEventListener('change',e=>{
  if(!state[S.page])return;const control=e.target.closest('[data-doc-control]');
  if(control){state[S.page][control.dataset.docControl]=control.value;state[S.page].page=0;navigate(S.page);}
  if(e.target.matches('[data-doc-history]')){state.quotes.history=e.target.checked;state.quotes.page=0;navigate('quotes');}
 });
 document.addEventListener('input',e=>{
  if(!e.target.matches('[data-doc-search]'))return;const kind=S.page,value=e.target.value,pos=e.target.selectionStart;state[kind].search=value;state[kind].page=0;clearTimeout(timer);
  timer=setTimeout(async()=>{if(S.page!==kind)return;await navigate(kind);const input=document.querySelector('[data-doc-search]');if(input){input.focus();input.setSelectionRange(pos,pos);}},300);
 });
 document.addEventListener('submit',async e=>{
  if(e.target.id!=='doc-due-form')return;e.preventDefault();const f=e.target,b=e.submitter;b.disabled=true;
  try{await api('/documents/invoices/'+encodeURIComponent(f.dataset.id)+'/due-date',{method:'PATCH',data:{due_date:f.elements.due_date.value,version:Number(f.dataset.version)}});modal.close();toast('Due date saved.');await navigate('invoices');}catch(ex){modalError(ex);}finally{b.disabled=false;}
 });
 window.addEventListener('resize',()=>closeMenu(false));
})();
