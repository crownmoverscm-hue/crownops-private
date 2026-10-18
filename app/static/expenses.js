/* Company expenses are private to Owners and Managers. */
'use strict';
(function(){
 const EX={start:'',end:'',category:'',status:'',offset:0,data:null,current:null};
 const admin=()=>['owner','manager'].includes(S.user?.role);
 ensureNav(['expenses','wallet','Expenses']);
 const previousAllowed=allowedPages;
 allowedPages=function(){const pages=previousAllowed();return admin()?[...new Set([...pages,'expenses'])]:pages.filter(p=>p!=='expenses');};
 const previousGroups=uxGroups;
 uxGroups=function(){const groups=previousGroups(),company=groups.find(g=>g[0]==='COMPANY');if(admin()&&company&&!company[1].includes('expenses'))company[1].unshift('expenses');return groups;};
 const query=()=>new URLSearchParams({start:EX.start,end:EX.end,category:EX.category,status:EX.status}).toString();
 X.pageRenderers.expenses=async()=>{
  if(!admin())throw Error('Expenses are private to Owners and Managers.');
  const d=await api('/expenses?'+query()+'&offset='+EX.offset);EX.data=d;
  const items=d.items.map(e=>[
   h(dateLabel(e.expense_date)),
   '<strong>'+h(e.vendor)+'</strong><div class="tiny muted">'+h(e.description)+'</div>',
   h(e.category),money(e.subtotal_cents),money(e.tax_cents),'<strong>'+money(e.total_cents)+'</strong>',
   pill(e.status),h([e.job_title,e.vehicle_name].filter(Boolean).join(' · ')||'Company expense'),
   '<button class="btn-small" data-expense="edit" data-id="'+h(e.id)+'">Open</button>'
  ]);
  const cards=d.items.map(e=>'<article class="panel panel-body"><div class="expense-card-title"><strong>'+h(e.vendor)+'</strong>'+pill(e.status)+'</div><p class="small muted">'+h(dateLabel(e.expense_date))+' · '+h(e.category)+'</p><p>'+h(e.description)+'</p><div class="expense-card-title"><strong>'+money(e.total_cents)+'</strong><button data-expense="edit" data-id="'+h(e.id)+'">Open expense</button></div></article>').join('');
  return head('Expenses','Private company costs, receipts and payment status.',
   '<button class="btn-primary" data-expense="new">Add expense</button><a class="button-link" href="/api/expenses/export.csv?'+h(query())+'" download>Export CSV</a>')+
   '<div class="grid3 expense-metrics">'+panel('Expenses in this view',money(d.total_cents))+panel('Unpaid',money(d.unpaid_cents))+panel('Records',h(d.total))+'</div><div class="spacer"></div>'+
   '<form data-expense-filter class="expense-filters">'+field('start','From',EX.start,'date')+field('end','To',EX.end,'date')+
   select('category','Category',[['','All categories'],...d.categories.map(c=>[c,c])],EX.category)+
   select('status','Status',[['','All statuses'],['Paid','Paid'],['Unpaid','Unpaid'],['Voided','Voided']],EX.status)+
   '<button type="submit">Apply filters</button></form>'+(items.length?'<div class="expense-ledger-table">'+panel('Expense ledger',xtable(['Date','Vendor / description','Category','Before tax','Tax','Total CAD','Status','Move / truck',''],items))+'</div><div class="expense-ledger-cards">'+cards+'</div>':panel('Expense ledger',empty('No expenses in this view','Add an expense or adjust the filters.')))+
   '<div class="expense-pages"><span class="small muted">'+d.total+' matching records. Voided amounts are excluded from totals.</span><button data-expense="previous" '+(EX.offset?'':'disabled')+'>Previous</button><button data-expense="next" '+(EX.offset+d.items.length<d.total?'':'disabled')+'>Next</button></div>'+
   '<p class="small muted">Assign costs to a related move to include them in Job margins. Use QuickBooks review to fetch and match posted expenses. Saving here does not make a payment or change QuickBooks.</p>';
 };
 async function expenseForm(id=''){
  if(!EX.data)EX.data=await api('/expenses?limit=1');
  const [e,jobs,trucks]=await Promise.all([id?api('/expenses/'+encodeURIComponent(id)):Promise.resolve({expense_date:today(),status:'Paid',category:'Other'}),api('/jobs'),api('/vehicles')]);
  EX.current=e;const closed=e.status==='Voided';
  const body='<form id="expense-form" data-expense-form data-request-key="'+crypto.randomUUID()+'"><div class="grid2">'+
   field('date','Expense date',e.expense_date,'date','required')+field('vendor','Vendor',e.vendor||'','text','required maxlength="200"')+
   select('category','Category',(EX.data?.categories||['Other']).map(c=>[c,c]),e.category)+
   select('status','Payment recorded',[['Paid','Paid'],['Unpaid','Unpaid']],e.status)+
   field('subtotal','Amount before tax · CAD',id?(e.subtotal_cents/100).toFixed(2):'','number','min="0" step="0.01" required')+
   field('tax','Total tax · CAD',id?(e.tax_cents/100).toFixed(2):'0.00','number','min="0" step="0.01" required')+
   field('description','What was purchased?',e.description||'','text','maxlength="1000"')+field('reference','Receipt / invoice number',e.reference||'','text','maxlength="200"')+
   select('job_id','Related move (optional)',[['','Company expense'],...jobs.map(j=>[j.id,j.title+' · '+localDay(j.start_at)])],e.job_id||'')+
   select('vehicle_id','Related truck (optional)',[['','No truck'],...trucks.map(t=>[t.id,t.name])],e.vehicle_id||'')+
   field('receipt_url','Private receipt link · HTTPS',e.receipt_url||'','url','maxlength="3000"')+textArea('notes','Notes',e.notes||'',3)+
   '</div><p class="strong" id="expense-total"></p><p class="tiny muted">Keep receipt folders restricted to Owners and Managers. A link retains the external folder’s existing sharing permissions.</p></form>'+
   (id?'<p class="tiny muted">Entered '+h(uxTimestampLabel(e.created_at))+' · last updated '+h(uxTimestampLabel(e.updated_at))+'</p>':'')+
   (closed?'<div class="callout orange">Voided: '+h(e.void_reason)+'</div>':'');
  showModal(id?'Company expense':'Add company expense',body,btn('close','Close')+(closed?'':(id?'<button data-expense="void-form" data-id="'+h(id)+'" class="btn-danger">Void expense</button>':'')+'<button type="submit" form="expense-form" class="btn-primary">Save expense</button>'),true);
  const form=document.getElementById('expense-form');if(closed)[...form.elements].forEach(el=>el.disabled=true);updateTotal(form);
 }
 function updateTotal(form){const n=Number(form.elements.subtotal.value)+Number(form.elements.tax.value);document.getElementById('expense-total').textContent='Total: '+money(Number.isFinite(n)?Math.round(n*100):0);}
 document.addEventListener('input',e=>{const f=e.target.closest('#expense-form');if(f&&['subtotal','tax'].includes(e.target.name))updateTotal(f);});
 document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-expense]');if(!b)return;e.preventDefault();
  try{
   const a=b.dataset.expense,id=b.dataset.id;
   if(a==='new'||a==='edit')return expenseForm(id||'');
   if(a==='previous'||a==='next'){EX.offset=Math.max(0,EX.offset+(a==='next'?50:-50));return navigate('expenses');}
   if(a==='void-form')return showModal('Void expense','<form id="expense-void" data-expense-void data-id="'+h(id)+'">'+textArea('reason','Why is this expense being voided?','',3)+'</form><p class="small muted">The record and history are retained. Voiding does not refund or cancel a payment.</p>',btn('close','Cancel')+'<button type="submit" form="expense-void" class="btn-danger">Void expense</button>');
  }catch(ex){modal.open?modalError(ex):toast(ex.message);}
 });
 document.addEventListener('submit',async e=>{
  const f=e.target.closest('[data-expense-form],[data-expense-filter],[data-expense-void]');if(!f)return;e.preventDefault();
  if(f.dataset.busy)return;
  const data=Object.fromEntries(new FormData(f));
  if(f.matches('[data-expense-filter]')){Object.assign(EX,data);EX.offset=0;return navigate('expenses');}
  f.dataset.busy='1';const button=e.submitter;if(button)button.disabled=true;
  try{
   const current=EX.current||{};
   if(f.matches('[data-expense-void]'))await api('/expenses/'+encodeURIComponent(f.dataset.id)+'/void',{method:'POST',data:{...data,version:current.version}});
   else await api(current.id?'/expenses/'+encodeURIComponent(current.id):'/expenses',{method:current.id?'PUT':'POST',data:{...data,...(current.id?{version:current.version}:{request_key:f.dataset.requestKey})}});
   modal.close();toast('Expense saved.');await navigate('expenses');
  }catch(ex){modalError(ex);}finally{delete f.dataset.busy;if(button?.isConnected)button.disabled=false;}
 });
})();
