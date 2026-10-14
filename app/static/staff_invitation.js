/* Staff invitation email flow. The owner reviews the recipient in the form. */
'use strict';
(function(){
 let emailReady=false;
 const requestId=()=>crypto.randomUUID();
 inviteForm=async function(){
  const [employees,access]=await Promise.all([api('/employees'),api('/account-access')]);
  const status=access.email_recovery||{};emailReady=Boolean(status.available);
  const note=emailReady?`<div class="callout teal"><strong>Invitation emails are enabled.</strong>The email goes to the address above. It includes a private signup link, password steps, device setup and guidance for the selected role.${status.mode==='approved test email addresses'?' This workspace currently sends only to approved test addresses.':''}</div>`:'<div class="callout orange"><strong>Invitation email setup is needed.</strong>Connect the company Gmail account and enable account emails before sending. You can create a private link now. <button type="button" class="btn-text" data-invite="setup">Open email setup</button></div>';
  const body=`<form id="invite-form" data-form="invite" data-request-id="${requestId()}"><div class="grid2">${field('name','Team member name','','text','autocomplete="name" maxlength="150" required')}${field('email','Their email address','','email','autocomplete="email" maxlength="254" required')}${select('role','Access role',[['sales','Sales'],['dispatch','Dispatch'],['crew','Crew'],['accounting','Accounting'],...(S.user.is_master_owner?[['owner','Owner (full access)']]:[])],'crew')}${select('employee_id','Link employee record',[['','No linked employee'],...employees.map(person=>[person.id,person.name])])}</div><p class="small muted">Crew accounts need a linked employee record. Check the name, email and access role before sending.</p>${note}<div class="spacer"></div><button type="button" class="btn-soft" data-invite="preview">Preview welcome email</button><div id="staff-invite-preview" class="staff-invite-preview" aria-live="polite"></div></form>`;
  const footer=btn('close','Cancel')+`<button class="btn-primary" type="submit" form="invite-form" value="email" ${emailReady?'':'disabled'}>Send invitation email</button><button class="btn-soft" type="submit" form="invite-form" value="link">Create link only</button>`;
  showModal('Invite a team member',body,footer);
 };

 function showResult(out){
  const status=out.delivery_status,recipient=h(out.recipient||'the invited email address');
  let title='Invitation created',notice='The private link works once and expires after 48 hours. No email was sent.',color='';
  if(out.sent){title='Invitation email sent';notice=`Gmail accepted the welcome email for <strong>${h(out.name||'Team member')}</strong> at <strong>${recipient}</strong>. Ask them to check their inbox and spam folder. The private signup link works once and expires after 48 hours.`;color='teal';}
  else if(status==='Unknown'){title='Email sending not confirmed';notice=`Sending to <strong>${recipient}</strong> was not confirmed. Check company Gmail Sent and the recipient inbox before creating another invitation. There is no automatic resend.`;color='orange';}
  else if(['Pending','Running'].includes(status)){title='Invitation email pending';notice=`The email request for <strong>${recipient}</strong> is pending. Sending has not been confirmed. Check Team access and Gmail Sent before creating another invitation.`;color='orange';}
  const link=out.url?'<div class="spacer"></div>'+textArea('draft_body','Private invitation link',out.url,3).replace('<textarea ','<textarea readonly '):'';
  const preview=out.email_preview?`<details class="staff-email-details"><summary>View welcome email</summary><div class="tiny strong">${h(out.email_preview.subject)}</div><div class="staff-email-copy">${h(out.email_preview.body)}</div></details>`:'';
  const send=out.url&&out.invitation_id&&!out.email_requested?`<button class="btn-primary" data-invite="email-current" ${emailReady?'':'disabled'}>Send this invitation by email</button>`:'';
  showModal(title,`<div class="callout ${color} staff-email-notice">${notice}</div>${link}${preview}`,btn('close','Done')+(out.url?btn('copy-draft','Copy private link','btn-soft'):'')+send);
  S.modalContext.staffInvitation=out;
 }

 window.submitStaffInvitation=async function(form,data,submit){
  const sendEmail=submit?.value==='email';
  const controls=[...modal.querySelectorAll('button[type="submit"][form="invite-form"]')];
  controls.forEach(button=>button.disabled=true);
  const label=submit?.textContent;if(submit)submit.textContent=sendEmail?'Sending invitation…':'Creating link…';
  try{
   const out=await api('/invitations',{method:'POST',data:{...data,send_email:sendEmail,request_id:form.dataset.requestId}});
   showResult({...out,request_id:form.dataset.requestId});
  }finally{
   if(form.isConnected){controls.forEach(button=>button.disabled=button.value==='email'&&!emailReady);if(submit)submit.textContent=label;}
  }
 };

 document.addEventListener('input',event=>{
  const form=event.target.closest('#invite-form');if(!form)return;
  const preview=form.querySelector('#staff-invite-preview');if(preview?.textContent)preview.textContent='Details changed. Preview the email again to review the latest information.';
 });
 document.addEventListener('change',event=>{
  if(event.target.closest('#invite-form')){const preview=document.getElementById('staff-invite-preview');if(preview?.textContent)preview.textContent='Details changed. Preview the email again to review the latest information.';}
 });
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-invite]');if(!button)return;event.preventDefault();
  try{
   if(button.dataset.invite==='setup'){modal.close();await navigate('access');document.getElementById('account-email-setup')?.scrollIntoView({block:'start'});return;}
   if(button.dataset.invite==='preview'){
    const form=document.getElementById('invite-form'),data=readForm(form),snapshot=JSON.stringify(data);button.disabled=true;
    const content=await api('/account-access/invitation-preview',{method:'POST',data});
    if(!form.isConnected)return;
    if(snapshot!==JSON.stringify(readForm(form)))return toast('Details changed. Preview the email again.');
    form.querySelector('#staff-invite-preview').innerHTML=`<p class="small strong">${h(content.subject)}</p><div class="staff-email-copy">${h(content.body)}</div>`;
   }
   if(button.dataset.invite==='email-current'){
    const out=S.modalContext.staffInvitation;if(!out?.url)return;
    const url=new URL(out.url);if(!url.hash.startsWith('#invite='))throw Error('The private invitation link is missing.');
    button.disabled=true;button.textContent='Sending invitation…';out.request_id=out.request_id||requestId();
    const result=await api('/account-access/invitations/'+encodeURIComponent(out.invitation_id)+'/send-email',{method:'POST',data:{token:url.hash.slice(8),request_id:out.request_id}});
    showResult({...out,...result,url:out.url,email_preview:out.email_preview});
   }
  }catch(error){modal.open?modalError(error):toast(error.message);}
  finally{if(button.isConnected){button.disabled=false;if(button.dataset.invite==='email-current')button.textContent='Send this invitation by email';}}
 });
})();
