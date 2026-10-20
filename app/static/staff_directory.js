/* Staff contacts and assignments are shared; individual wages are Owner/Manager/self only. */
'use strict';
function staffRate(e){return e.pay_rate_pending?'Pay rate pending management setup':money(e.hourly_cents)+'/h';}
function staffServices(e){
 const jobs=e.assigned_services||[];
 return '<section class="employee-services"><h3>Assigned services</h3>'+(jobs.length?'<div class="stack">'+jobs.map(j=>'<button type="button" class="task-row" data-action="open-job" data-id="'+h(j.id)+'"><span><strong>'+h(j.title)+'</strong><br><span class="tiny muted">'+h(dateLabel(localDay(j.start_at)))+' · '+h(timeLabel(j.start_at))+' · '+h(j.status)+'</span></span></button>').join('')+'</div>':'<p class="small muted">No upcoming services assigned.</p>')+'</section>';
}
const staffOwnerForm=uxEmployeeForm;
uxEmployeeForm=function(employee){
 if(!employee)return;
 if(may(['owner'])){staffOwnerForm(employee);modal.querySelector('.modal-body')?.insertAdjacentHTML('beforeend',staffServices(employee));return;}
 const ownPay=employee.can_view_pay?'<div class="callout teal"><strong>Your pay rate</strong>'+h(staffRate(employee))+'</div>':'';
 showModal(h(employee.full_name),ownPay+'<div class="job-info-grid"><div><div class="info-label">Role</div>'+h(employee.role)+'</div><div><div class="info-label">Phone</div>'+h(employee.phone||'Not provided')+'</div><div><div class="info-label">Emergency contact</div>'+h(employee.emergency_contact?.name||'Not provided')+' · '+h(employee.emergency_contact?.phone||'')+'</div></div>'+staffServices(employee),btn('close','Close'),true);
};
teamPage=async function(){
 if(!UX.dirStart)[UX.dirStart,UX.dirEnd]=uxDefaultRange();
 const d=await api('/ux/employee-directory?start='+encodeURIComponent(UX.dirStart)+'&end='+encodeURIComponent(UX.dirEnd));UX.directory=d;
 const owner=may(['owner']);
 const actions=owner?btn('invite',icon('plus')+'Invite team member','btn-primary')+'<button class="btn-soft" data-ux="employee-add">Add employee</button><button class="btn-soft" data-ux="employee-contact-import">Import contacts</button>':'';
 const records=d.employees.map(e=>[
  '<div class="client-name">'+h(e.full_name)+'</div><div class="tiny muted">'+h(e.role)+(e.active?'':' · Inactive')+'</div>'+(!owner&&e.can_view_pay?'<div class="staff-own-pay">Your pay · '+h(staffRate(e))+'</div>':''),
  h(e.phone||'—'),h(e.emergency_contact?.name||'—')+'<br><span class="tiny muted">'+h(e.emergency_contact?.phone||'')+'</span>',
  Array.isArray(e.assigned_services)?e.assigned_services.length+' assigned':'—',
  ...(owner?[h(staffRate(e)),Number(e.current_hours||0).toFixed(2)+' h']:[]),
  '<button class="btn-small" data-ux="employee-open" data-id="'+h(e.id)+'">Open</button>'
 ]);
 return head('Employee directory','Contacts and assigned services. Pay rates are visible only to Owners, Managers and the employee concerned.',actions)+
  (owner?'<div class="filterbar"><label class="small">Hours from <input id="ux-dir-start" type="date" value="'+h(d.start)+'"></label><label class="small">to <input id="ux-dir-end" type="date" value="'+h(d.end)+'"></label><button data-ux="directory-load">Load</button></div>':'')+
  '<div class="staff-directory-table">'+panel('Team',xtable(['Employee','Phone','Emergency contact','Services',...(owner?['Pay rate','Hours']:[]),''],records))+'</div>'+  '<div class="staff-directory-cards">'+d.employees.map(e=>'<article class="panel panel-body"><div class="row wrap"><strong class="grow">'+h(e.full_name)+'</strong>'+pill(e.active?'Active':'Inactive')+'</div><p class="small muted">'+h(e.role)+'</p><p class="small">Phone: '+h(e.phone||'Not provided')+'</p><p class="small">Emergency: '+h(e.emergency_contact?.name||'Not provided')+'<br>'+h(e.emergency_contact?.phone||'')+'</p>'+(e.can_view_pay?'<p class="staff-own-pay">'+(owner?'Pay rate':'Your pay')+' · '+h(staffRate(e))+'</p>':'')+(Array.isArray(e.assigned_services)?'<p class="small">'+e.assigned_services.length+' assigned services</p>':'')+'<button type="button" data-ux="employee-open" data-id="'+h(e.id)+'">Open employee</button></article>').join('')+'</div>';
};
X.pageRenderers.payroll=async()=>{
 const ref=UX.payPeriodReference||(UX.payPeriodCustom?UX.payStart:'')||'';
 const calendar=await api('/payroll-periods?on='+encodeURIComponent(ref));UX.payCalendar=calendar;
 if(calendar.configured&&!UX.payPeriodCustom){UX.payStart=calendar.selected.start;UX.payEnd=calendar.selected.end;}
 else if(!UX.payStart)[UX.payStart,UX.payEnd]=uxDefaultRange();
 const d=await api('/ux/payroll-summary?start='+encodeURIComponent(UX.payStart)+'&end='+encodeURIComponent(UX.payEnd));UX.payroll=d;
 const owner=may(['owner','manager']),exportUrl='/api/ux/payroll-hours/export.csv?'+new URLSearchParams({start:d.start,end:d.end}),own=d.employees.find(e=>e.employee_id===S.user.employee_id);
 const ownPay=!owner&&own&&Object.hasOwn(own,'hourly_cents')?'<div class="callout teal"><strong>Your pay rate · '+h(staffRate(own))+'</strong>'+(own.pay_rate_pending?'':'Gross estimate for this range: '+money(own.gross_cents))+'</div>':'';
 const records=d.employees.filter(e=>e.hours||e.pending_entries||e.missing_entries).map(e=>[h(e.employee_name),h(e.jobs),'<strong>'+Number(e.hours).toFixed(2)+' h</strong>',pill(e.status),...(owner?[h(staffRate(e)),e.pay_rate_pending?'Pending':money(e.gross_cents)]:[]),'<button class="btn-small" data-ux="payroll-detail" data-id="'+h(e.employee_id)+'">Details</button>']);
 return head('Payroll & hours','1. Record hours  ·  2. Review completed services  ·  3. Export for payroll.',(d.can_manage_hours?'<button class="btn-primary" data-ux="payroll-approve">Approve completed hours</button>':'')+(owner?'<button data-hours-export data-url="'+h(exportUrl)+'">Export approved hours</button>':''))+
  payrollCalendarBar(calendar)+
  '<div class="filterbar payroll-range"><label class="small">From <input id="ux-pay-start" type="date" value="'+h(d.start)+'"></label><label class="small">To <input id="ux-pay-end" type="date" value="'+h(d.end)+'"></label><button data-ux="payroll-load">Load payroll</button><div class="grow"></div><span class="pill blue">'+Number(d.total_hours).toFixed(2)+' total hours</span></div>'+ownPay+
  '<div class="payroll-metrics"><div><span>Recorded</span><strong>'+Number(d.total_hours).toFixed(2)+' h</strong></div><div><span>Approved</span><strong>'+Number(d.approved_hours).toFixed(2)+' h</strong></div><div><span>To review</span><strong>'+(d.missing_entries+d.pending_entries)+'</strong><small>'+d.missing_entries+' missing · '+d.pending_entries+' submitted</small></div></div>'+
  panel('Hours summary',records.length?xtable(['Employee','Jobs','Hours','State',...(owner?['Pay rate','Gross estimate']:[]),''],records):empty('No hours in this range','Choose another date range or enter the hours after a service.'))+'<p class="small muted">Amounts are straight-time estimates. Your payroll provider handles overtime, vacation pay, deductions and payment. Exporting does not pay staff.</p>';
};

document.addEventListener('click',async e=>{
 const button=e.target.closest('[data-hours-export]');if(!button)return;
 button.disabled=true;
 try{const response=await fetch(button.dataset.url,{credentials:'same-origin'});if(!response.ok){const error=await response.json();throw Error(error.detail||'Export could not finish.');}const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download='crownops-approved-hours-'+UX.payStart+'-'+UX.payEnd+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){toast(error.message);}finally{button.disabled=false;}
});

function payrollCalendarBar(c){
 const setup=c.can_configure?'<button data-pay-calendar="settings" class="btn-soft">'+(c.configured?'Payroll period settings':'Set up payroll period')+'</button>':'';
 if(!c.configured)return '<section class="callout"><strong>How do you run payroll?</strong><p>'+(c.can_configure?'Choose weekly or every two weeks and the first day of a period. CrownOps will remember it for the team.':'An Owner or Manager can set the company payroll schedule. You can still review hours by date.')+'</p>'+setup+'</section>';
 return '<section class="panel panel-body payroll-calendar"><div><strong>'+h(c.period_days===7?'Weekly payroll':'Payroll every two weeks')+'</strong><p>'+h(UX.payPeriodCustom?'Custom date range':uxDateLabel(c.selected.start)+' through '+uxDateLabel(c.selected.end)+' · both dates included')+'</p></div><div class="row wrap"><button data-pay-calendar="previous">Previous period</button><button data-pay-calendar="current">Current period</button><button data-pay-calendar="next">Next period</button>'+setup+'</div></section>';
}
function payrollPeriodPreview(){
 const f=document.getElementById('payroll-period-form');if(!f)return;
 const days=Number(f.elements.period_days.value),anchor=f.elements.period_anchor.value,out=document.getElementById('payroll-period-preview');
 if(![7,14].includes(days)||!/^\d{4}-\d{2}-\d{2}$/.test(anchor)){out.textContent='Choose a frequency and the first day of one payroll period.';return;}
 const start=new Date(anchor+'T12:00:00Z'),end=new Date(start);end.setUTCDate(end.getUTCDate()+days-1);
 const weekday=new Intl.DateTimeFormat('en-CA',{timeZone:'UTC',weekday:'long'});
 out.textContent=weekday.format(start)+' through '+weekday.format(end)+' · '+days+' days. Example: '+uxDateLabel(anchor)+' through '+uxDateLabel(end.toISOString().slice(0,10))+'. Both dates are included.';
}
async function payrollPeriodSettings(){
 const c=await api('/payroll-periods');UX.payCalendar=c;
 showModal('Payroll period settings','<form id="payroll-period-form"><label for="payroll-frequency">How often do you run payroll?</label><select id="payroll-frequency" name="period_days" required><option value="">Choose a frequency</option><option value="7" '+(c.configured&&c.period_days===7?'selected':'')+'>Weekly · every 7 days</option><option value="14" '+(c.configured&&c.period_days===14?'selected':'')+'>Every two weeks · every 14 days</option></select><label for="payroll-anchor">First day of one payroll period</label><input id="payroll-anchor" type="date" name="period_anchor" required value="'+h(c.period_anchor||'')+'"><p class="callout" id="payroll-period-preview" aria-live="polite"></p><p class="small muted">This schedule is shared by the team. Changing it does not edit recorded hours, approved hours or saved pay rates. For an older payroll run, use its original From and To dates.</p></form>',btn('close','Cancel')+'<button type="submit" form="payroll-period-form" class="btn-primary">Save payroll schedule</button>');payrollPeriodPreview();
}
document.addEventListener('change',e=>{if(e.target.closest('#payroll-period-form'))payrollPeriodPreview();});
document.addEventListener('click',e=>{if(e.target.closest('[data-ux="payroll-load"]')){UX.payPeriodCustom=true;UX.payPeriodReference=null;}},true);
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-pay-calendar]');if(!b||b.disabled)return;e.preventDefault();b.disabled=true;
 try{
  if(b.dataset.payCalendar==='settings')return await payrollPeriodSettings();
  const c=UX.payCalendar;UX.payPeriodCustom=false;UX.payPeriodReference=b.dataset.payCalendar==='current'?null:c[b.dataset.payCalendar].start;
  return await navigate('payroll');
 }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('submit',async e=>{
 if(e.target.id!=='payroll-period-form')return;e.preventDefault();const b=e.submitter;if(b?.disabled)return;if(b)b.disabled=true;
 try{
  const f=e.target;await api('/payroll-periods',{method:'POST',data:{period_days:Number(f.elements.period_days.value),period_anchor:f.elements.period_anchor.value,review_token:UX.payCalendar.review_token}});
  UX.payPeriodCustom=false;UX.payPeriodReference=null;UX.payStart=null;UX.payEnd=null;modal.close();await navigate('payroll');toast('Payroll schedule saved for the team.');
 }catch(ex){modalError(ex);}finally{if(b?.isConnected)b.disabled=false;}
});
