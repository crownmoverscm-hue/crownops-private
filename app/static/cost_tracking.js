/* Keep costs, QuickBooks review and recorded job margin in one private section. */
'use strict';
(function(){
 const CS={tab:'ledger',start:today().slice(0,8)+'01',end:today(),offset:0,data:null,current:null};
 const tabs=()=>'<div class="filterbar" aria-label="Expense views">'+[['ledger','Expense ledger'],['margins','Job margins'],['quickbooks','QuickBooks review']].map(([id,label])=>'<button data-cost-tab="'+id+'" aria-pressed="'+(CS.tab===id)+'" class="'+(CS.tab===id?'btn-primary':'')+'">'+label+'</button>').join('')+'</div>';
 const dates=()=>'<form data-cost-dates class="filterbar cost-date-fields">'+field('start','From',CS.start,'date','required')+field('end','To',CS.end,'date','required')+'<button type="submit">'+(CS.tab==='quickbooks'?'Fetch from QuickBooks':'Show margins')+'</button></form>';
 const previous=X.pageRenderers.expenses;
 X.pageRenderers.expenses=async()=>{
  if(!(S.user?.role==='owner'))throw Error('Expenses are private to Owners.');
  if(CS.tab==='ledger')return tabs()+await previous();
  if(CS.tab==='margins'){
   const d=await api('/costs/margins?'+new URLSearchParams({start:CS.start,end:CS.end}));
   const values=d.items.map(j=>[h(dateLabel(j.date)),'<strong>'+h(j.title)+'</strong><div class="tiny muted">'+h(j.invoice_number||'No issued invoice')+'</div>',j.revenue_cents==null?'Pending':money(j.revenue_cents),money(j.approved_labour_cents),money(j.expense_cents),j.recorded_margin_cents==null?'Needs review':'<strong>'+money(j.recorded_margin_cents)+'</strong>',j.warnings.map(w=>'<div class="small">'+h(w)+'</div>').join('')||'Recorded',j.invoice_id?'<button data-cost-invoice="'+h(j.invoice_id)+'">Open invoice</button>':'']);
   return head('Job margins','See what each service earned and the costs recorded against it.')+tabs()+dates()+
    '<div class="callout teal">'+h(d.note)+'</div><div class="spacer"></div>'+panel('Services in this date range',values.length?xtable(['Service date','Job / invoice','Revenue before tax','Approved labour','Linked costs','Recorded margin','Review',''],values):empty('No services in this range','Choose the dates of the services you want to review.'))+
    '<div class="spacer"></div>'+panel('Company costs and unmatched transactions','<p>'+money(d.company_costs.cents)+' in company expenses before tax ('+d.company_costs.count+' records) are not allocated to a job.</p><p>'+d.quickbooks_review_count+' QuickBooks records await review. Refunds, supplier credits and bank-feed items awaiting posting are not included. Refresh and reconcile QuickBooks before relying on the totals.</p>');
  }
  const d=await api('/costs/quickbooks?offset='+CS.offset);CS.data=d;
  const records=d.items.map(s=>'<article class="panel panel-body"><div class="row wrap"><strong class="grow">'+h(s.data.vendor||'Supplier missing')+'</strong>'+pill(s.state)+'</div><p class="small">'+h(s.data.date||'Date missing')+' · '+h(s.kind==='Bill'?'Supplier bill':'Paid expense')+' · '+h(s.data.reference)+'</p><p><strong>'+ (s.data.total_cents==null?'Amount needs review':money(s.data.total_cents))+'</strong> '+h(s.data.currency||'Currency missing')+'</p>'+(s.data.balance_cents?'<p class="small">Outstanding in QuickBooks: '+money(s.data.balance_cents)+'</p>':'')+'<p class="small muted">'+h(s.data.description)+'</p>'+s.data.problems.map(p=>'<p class="small">'+h(p)+'</p>').join('')+'<p class="tiny muted">'+(s.environment==='sandbox'?'Test record · ':'')+'Checked '+h(uxTimestampLabel(s.checked_at))+'</p><button data-cost-review="'+h(s.id)+'">'+(s.expense_id?'Review linked expense':'Review / match')+'</button></article>').join('');
  return head('QuickBooks expense review','Owner approval required: fetch, match and approve each cost.')+tabs()+
   (d.connected&&d.environment==='sandbox'?'<div class="callout orange"><strong>QuickBooks is in test mode.</strong> You can preview transactions, but test records cannot enter your real expense ledger.</div>':'')+
   (!d.connected?'<div class="callout orange">Connect QuickBooks to bring in recorded expenses. <button data-cost-connect>Open Connections</button></div>':'')+dates()+
   '<details class="panel panel-body"><summary>How expense importing works</summary><p class="small muted">This reads posted expenses and supplier bills. Connect and review bank transactions inside QuickBooks first. Choose operating costs here; payroll payments, transfers, asset purchases and tax remittances need accounting review. Refresh after changes in QuickBooks; this is not a live bank feed.</p></details><div class="spacer"></div>'+
   (records?'<div class="grid2">'+records+'</div>':panel('Review queue',empty('No QuickBooks expenses fetched','Choose a date range and select Fetch from QuickBooks.')))+
   '<div class="row wrap"><span class="grow small">'+d.total+' records</span><button data-cost-page="previous" '+(CS.offset?'':'disabled')+'>Previous</button><button data-cost-page="next" '+(CS.offset+d.items.length<d.total?'':'disabled')+'>Next</button></div>';
 };
 async function review(id){
  const s=CS.data.items.find(x=>x.id===id);if(!s)throw Error('Refresh the expense list.');CS.current=s;
  const jobs=await api('/jobs'),locked=s.environment!=='production'||s.data.problems.length;
  const canIgnore=s.environment==='production'&&!s.expense_id;
  const modes=locked?(canIgnore?[['ignore','Ignore · resolve separately in QuickBooks']]:[]):s.expense_id?[['update','Update the linked expense from this snapshot']]:[['create','Add a new expense'],...(s.matches.length?[['match','Match an expense already recorded']]:[]),['ignore','Ignore · not an operating expense']];
  let body='<p><strong>'+h(s.data.vendor)+'</strong> · '+h(s.data.date)+' · '+(s.data.total_cents==null?'Amount needs review':money(s.data.total_cents))+'</p>';
  if(s.expense)body+='<p>Linked expense: '+h(s.expense.vendor)+' · '+money(s.expense.total_cents)+' · '+h(s.expense.status)+'</p><button data-expense="edit" data-id="'+h(s.expense.id)+'">Open existing expense</button>';
  if(locked)body+='<div class="callout orange">'+h(s.environment!=='production'?'Test records cannot be imported.':s.data.problems.join(' '))+'</div>';
  body+='<form id="cost-review-form" data-cost-review-form>'+select('action','What should happen?',modes,locked?'ignore':s.expense_id?'update':s.matches.length?'match':'create')+
   (s.matches.length?select('expense_id','Possible match',s.matches.map(e=>[e.id,e.vendor+' · '+dateLabel(e.expense_date)+' · '+money(e.total_cents)]),s.matches[0].id):'')+
   select('category','Category for a new / updated expense',CS.data.categories.map(c=>[c,c]),s.expense?.category||'Other')+
   select('job_id','Related service for a new / updated expense',[['','Company expense'],...jobs.map(j=>[j.id,j.title+' · '+localDay(j.start_at)])],s.expense?.job_id||'')+
   '<p class="small muted">Matching keeps the existing expense and its job assignment. Updating replaces its amount and payment status with the fetched snapshot. Bill payments are not imported as a second expense.</p>'+
   (!s.expense_id?'<label class="checkbox-label"><input type="checkbox" name="confirmed_distinct">If I add a new expense, I confirm it is a separate purchase from any same-date, same-amount record.</label>':'')+'</form>';
  showModal('Review QuickBooks expense',body,btn('close','Cancel')+(!locked||canIgnore?'<button type="submit" form="cost-review-form" class="btn-primary">Approve expense review</button>':''),true);
 }
 document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-cost-tab],[data-cost-page],[data-cost-review],[data-cost-invoice],[data-cost-connect]');if(!b)return;e.preventDefault();
  try{
   if(b.hasAttribute('data-cost-connect'))return navigate('connections');
   if(b.dataset.costReview)return await review(b.dataset.costReview);
   if(b.dataset.costInvoice){const id=b.dataset.costInvoice;modal.close();return openInvoice(id);}
   if(b.dataset.costTab)CS.tab=b.dataset.costTab;
   if(b.dataset.costPage)CS.offset=Math.max(0,CS.offset+(b.dataset.costPage==='next'?50:-50));
   await navigate('expenses');
  }catch(error){modal.open?modalError(error):toast(error.message);}
 });
 document.addEventListener('submit',async e=>{
  const f=e.target.closest('[data-cost-dates],[data-cost-review-form]');if(!f)return;e.preventDefault();if(f.dataset.busy)return;
  f.dataset.busy='1';const b=e.submitter,label=b?.textContent;if(b){b.disabled=true;b.textContent='Working…';}
  try{
   const data=Object.fromEntries(new FormData(f));
   if(f.matches('[data-cost-dates]')){CS.start=data.start;CS.end=data.end;if(CS.tab==='quickbooks'){const r=await api('/costs/quickbooks/fetch',{method:'POST',data});CS.offset=0;toast(r.records+' transactions fetched for review.');}}
   else{const s=CS.current,match=s.matches.find(x=>x.id===data.expense_id);await api('/costs/quickbooks/'+encodeURIComponent(s.id)+'/review',{method:'POST',data:{...data,version:s.version,expense_version:s.expense?.version??match?.version,confirmed_distinct:f.elements.confirmed_distinct?.checked===true}});modal.close();toast('Expense review saved.');}
   await navigate('expenses');
  }catch(error){modal.open?modalError(error):toast(error.message);}finally{delete f.dataset.busy;if(b?.isConnected){b.disabled=false;b.textContent=label;}}
 });
})();
