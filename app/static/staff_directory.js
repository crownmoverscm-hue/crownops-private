/* Staff contacts and assignments are shared; individual wages are owner/self only. */
'use strict';
function staffRate(e){return e.pay_rate_pending?'Pay rate pending owner setup':money(e.hourly_cents)+'/h';}
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
 return head('Employee directory','Contacts and assigned services. Pay rates are visible only to owners and the employee concerned.',actions)+
  (owner?'<div class="filterbar"><label class="small">Hours from <input id="ux-dir-start" type="date" value="'+h(d.start)+'"></label><label class="small">to <input id="ux-dir-end" type="date" value="'+h(d.end)+'"></label><button data-ux="directory-load">Load</button></div>':'')+
  '<div class="staff-directory-table">'+panel('Team',xtable(['Employee','Phone','Emergency contact','Services',...(owner?['Pay rate','Hours']:[]),''],records))+'</div>'+  '<div class="staff-directory-cards">'+d.employees.map(e=>'<article class="panel panel-body"><div class="row wrap"><strong class="grow">'+h(e.full_name)+'</strong>'+pill(e.active?'Active':'Inactive')+'</div><p class="small muted">'+h(e.role)+'</p><p class="small">Phone: '+h(e.phone||'Not provided')+'</p><p class="small">Emergency: '+h(e.emergency_contact?.name||'Not provided')+'<br>'+h(e.emergency_contact?.phone||'')+'</p>'+(e.can_view_pay?'<p class="staff-own-pay">'+(owner?'Pay rate':'Your pay')+' · '+h(staffRate(e))+'</p>':'')+(Array.isArray(e.assigned_services)?'<p class="small">'+e.assigned_services.length+' assigned services</p>':'')+'<button type="button" data-ux="employee-open" data-id="'+h(e.id)+'">Open employee</button></article>').join('')+'</div>';
};
X.pageRenderers.payroll=async()=>{
 if(!UX.payStart)[UX.payStart,UX.payEnd]=uxDefaultRange();
 const d=await api('/ux/payroll-summary?start='+encodeURIComponent(UX.payStart)+'&end='+encodeURIComponent(UX.payEnd));UX.payroll=d;
 const owner=may(['owner']),own=d.employees.find(e=>e.employee_id===S.user.employee_id);
 const ownPay=!owner&&own&&Object.hasOwn(own,'hourly_cents')?'<div class="callout teal"><strong>Your pay rate · '+h(staffRate(own))+'</strong>'+(own.pay_rate_pending?'':'Gross estimate for this range: '+money(own.gross_cents))+'</div>':'';
 const records=d.employees.filter(e=>e.hours||e.pending_entries).map(e=>[h(e.employee_name),h(e.jobs),'<strong>'+Number(e.hours).toFixed(2)+' h</strong>',pill(e.status),...(owner?[h(staffRate(e)),e.pay_rate_pending?'Pending':money(e.gross_cents)]:[]),'<button class="btn-small" data-ux="payroll-detail" data-id="'+h(e.employee_id)+'">Details</button>']);
 return head('Payroll & hours','Choose the dates to total worked hours. Individual pay stays private.',d.can_manage_hours?'<button class="btn-primary" data-ux="payroll-approve">Approve current range</button>':'')+
  '<div class="filterbar payroll-range"><label class="small">From <input id="ux-pay-start" type="date" value="'+h(d.start)+'"></label><label class="small">To <input id="ux-pay-end" type="date" value="'+h(d.end)+'"></label><button data-ux="payroll-load">Load payroll</button><div class="grow"></div><span class="pill blue">'+Number(d.total_hours).toFixed(2)+' total hours</span></div>'+ownPay+
  panel('Hours summary',records.length?xtable(['Employee','Jobs','Hours','State',...(owner?['Pay rate','Gross estimate']:[]),''],records):empty('No hours in this range','Choose another date range or enter the hours after a service.'));
};
