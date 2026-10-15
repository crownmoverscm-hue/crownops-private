/* Staff sign-in, recovery and owner access controls. */
'use strict';
(function(){
 let resetToken='';
 ensureNav(['access','users','Team access']);
 const previousAllowed=allowedPages;
 allowedPages=function(){const pages=previousAllowed();return S.user.role==='owner'?[...new Set([...pages,'access'])]:pages.filter(p=>p!=='access');};
 const previousGroups=uxGroups;
 uxGroups=function(){const groups=previousGroups();const company=groups.find(g=>g[0]==='COMPANY');if(S.user.role==='owner'&&company&&!company[1].includes('access'))company[1].unshift('access');return groups;};

 const previousLogin=loginScreen;
 loginScreen=function(){previousLogin();const form=document.getElementById('login-form');if(!form)return;
  if(form.dataset.form==='login')form.insertAdjacentHTML('afterend','<button type="button" class="btn-text account-forgot" data-account="forgot">Forgot your password?</button>');
  if(['accept-invite','setup-owner'].includes(form.dataset.form))form.querySelector('button[type="submit"]').insertAdjacentHTML('beforebegin',field('confirm_password','Confirm password','','password','minlength="12" maxlength="200" autocomplete="new-password" required'));
 };
 const previousSetup=setupScreen;
 setupScreen=function(){previousSetup();const form=document.getElementById('login-form');if(form)form.querySelector('button[type="submit"]').insertAdjacentHTML('beforebegin',field('confirm_password','Confirm password','','password','minlength="12" maxlength="200" autocomplete="new-password" required'));};
 function signInPanel(title,body){previousLogin();document.querySelector('.login-form').innerHTML=`<div class="eyebrow">CROWN MOVERS WORKSPACE</div><div class="spacer"></div><h2>${title}</h2>${body}<button type="button" class="btn-text" data-account="back">Back to sign in</button><p class="device-copyright">&copy; 2026 Crown Movers. Private company workspace.</p>`;}
 async function forgotScreen(){
  signInPanel('Recover your account','<p class="small muted">Enter the email used for your staff invitation. You will choose your own new password through a private email link.</p><form data-account-form="forgot">'+field('email','Account email','','email','autocomplete="username" required')+'<button type="submit" class="btn-primary">Send reset link</button><div class="login-error" role="alert"></div><div class="account-result" role="status" aria-live="polite"></div></form>');
  try{const status=await api('/recovery-status');if(!status.available){const form=document.querySelector('[data-account-form="forgot"]');if(form){form.querySelector('button[type="submit"]').disabled=true;form.querySelector('.login-error').textContent='Email recovery is not enabled yet. Ask the owner for a private reset link.';}}}catch{const error=document.querySelector('[data-account-form="forgot"] .login-error');if(error)error.textContent='Recovery status could not be checked. Ask the owner if the email does not arrive.';}
 }
 function resetScreen(token){
  resetToken=token;signInPanel('Choose a new password','<p class="small muted">Use at least 12 characters. This link works once and expires after 30 minutes.</p><form data-account-form="reset">'+field('password','New password','','password','minlength="12" maxlength="200" autocomplete="new-password" required')+field('confirm_password','Confirm password','','password','minlength="12" maxlength="200" autocomplete="new-password" required')+'<p class="tiny muted">You will sign in again after the reset. Your authenticator is still required if enabled.</p><button type="submit" class="btn-primary">Save new password</button><div class="login-error" role="alert"></div></form>');
 }
 const previousBootstrap=bootstrap;
 bootstrap=async function(){if(location.hash.startsWith('#reset=')){const token=location.hash.slice(7);history.replaceState(null,'',location.pathname+location.search);return resetScreen(token);}return previousBootstrap();};

 function recoveryCard(status){
  const label=status.available?'Enabled for '+status.mode:'Setup needed';
  const instructions=status.available?'<p class="small muted">Welcome invitations and password recovery use this connection. Check a welcome email and a recovery email in the actual inbox before rolling this out to the team.</p>':'<p class="small muted">Connect the company Gmail account, then enable account emails in the hosting settings. You can provide a private reset link from this page while email recovery is being set up.</p>';
  return '<div id="account-email-setup">'+panel('Account emails',`<span class="pill ${status.available?'teal':'orange'}">${h(label)}</span>${instructions}<button class="btn-soft" data-page="connections">Open connections</button><details class="account-config"><summary>Hosting settings for account emails</summary><p class="small">Set <code>CROWNOPS_ALLOW_AUTH_EMAIL=1</code> after Gmail sending permission is approved. While the app is in staging, list the approved test addresses in <code>CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST</code>, separated by commas. Use the existing encryption key and Google credentials.</p></details>`)+'</div>';
 }
 X.pageRenderers.access=async function(){
  const d=await api('/account-access');S.user.is_master_owner=d.is_master_owner;
  const accountCards=d.accounts.map(person=>{
   const edit=person.can_manage?`<button class="btn-small ${person.active?'':'btn-primary'}" data-account="edit-access" data-id="${h(person.id)}" data-restore="${person.active?'false':'true'}">${person.active?'Change role':'Restore access'}</button>`:'';
   const actions=edit+(person.active&&person.can_manage?`<button class="btn-small" data-account="reset-link" data-id="${h(person.id)}">Private reset link</button><button class="btn-danger btn-small" data-account="remove" data-id="${h(person.id)}" data-name="${h(person.name)}" data-email="${h(person.email)}">Remove access</button>`:'');
   const access=person.master_owner?'Master owner':person.role==='owner'?'Owner':person.role==='dispatch'?'Dispatch':person.role;
   return `<div class="account-card"><div class="grow"><div class="strong">${h(person.name)}</div><div class="small muted account-email">${h(person.email)}</div><div class="tiny muted">${h(access)}${person.id===S.user.id?' &middot; Your account':''}</div></div><span class="pill ${person.active?'teal':'gray'}">${person.active?'Active':'Access removed'}</span><div class="account-actions">${actions}</div></div>`;
  }).join('');
  const pending=d.invitations.map(invite=>`<div class="account-card"><div class="grow"><div class="strong">${h(invite.name)}</div><div class="small muted account-email">${h(invite.email)}</div><div class="tiny muted">${h(invite.role)}${invite.email_status?' &middot; Email: '+h(invite.email_status==='Completed'?'Sent':invite.email_status):''} &middot; Expires ${h(uxTimestampLabel(invite.expires_at))}</div></div>${invite.role!=='owner'||d.is_master_owner?`<button class="btn-small" data-account="revoke-invite" data-id="${h(invite.id)}">Revoke link</button>`:''}</div>`).join('');
  return head('Team access','Individual passwords, private invitations and owner-controlled access.',btn('invite',icon('plus')+'Invite team member','btn-primary'))+
   `<div class="callout teal"><strong>${d.is_master_owner?'You are the master owner.':'The first owner account is protected.'}</strong>Owners can restore staff access and change staff roles. Only the master owner can assign Owner access or manage other owners. Your own access and the master owner are protected.</div><div class="spacer"></div>`+
   panel('Staff accounts',accountCards)+`<div class="spacer"></div>`+panel('Pending invitations',pending||'<p class="small muted">No active invitations. Create one private link per person; each link works once and expires after 48 hours.</p>')+`<div class="spacer"></div>`+recoveryCard(d.email_recovery)+
   '<p class="small muted" style="margin-top:16px">Removing access signs the person out and cancels unused account reset links. Restore access here to reuse the same account and email. Their password, authenticator, employee and work records are retained.</p>';
 };

 function accessRoleHelp(form){
  const role=form.elements.role.value;
  const notes={owner:'Owner has full administrative access, including sensitive financial information, company settings and ordinary staff access management. Only the master owner can assign this role.',sales:'Sales has broad operational access for clients, draft quotes, schedules and job follow-ups. Sensitive approvals remain restricted.',dispatch:'Dispatch is for scheduling and crew coordination. It is not full Owner access or sales quoting access.',crew:'Crew needs a linked active employee record and works on assigned jobs.',accounting:'Accounting is for accounting and payroll work. It does not grant owner account administration.'};
  form.querySelector('[data-access-role-help]').textContent=notes[role]||'';
  form.elements.employee_id.required=role==='crew';
 }
 async function editAccess(id,restore){
  const [access,employees]=await Promise.all([api('/account-access'),api('/employees')]);
  const person=access.accounts.find(p=>p.id===id);
  if(!person?.can_manage)throw Error('This account can only be managed by its authorized owner.');
  const roles=[['sales','Sales'],['dispatch','Dispatch'],['crew','Crew'],['accounting','Accounting'],...(access.is_master_owner?[['owner','Owner (full access)']]:[])];
  const options=[['','No linked employee'],...employees.map(p=>[p.id,p.name+(p.active===0?' (inactive)':'')])];
  const title=restore?'Restore account access':'Change account role';
  const body=`<p class="account-email"><strong>${h(person.name)}</strong><br>${h(person.email)}</p><form id="account-access-form" data-account-form="access" data-user-id="${h(id)}" data-revision="${h(person.access_revision)}" data-active="${restore||Boolean(person.active)}"><div class="grid2">${select('role','Access role',roles,person.role)}${select('employee_id','Link employee record',options,person.employee_id||'')}</div><div class="callout account-role-help" data-access-role-help></div><p class="small muted">${restore?'This restores the existing account. The person signs in with their existing password and authenticator, or uses Forgot your password? A new invitation is not needed.':'Changing the role or employee link ends existing sign-ins. The person signs in again with their existing password and authenticator.'} Their work records stay with the same account.</p><div class="login-error" role="alert"></div></form>`;
  showModal(title,body,formFooter('account-access-form',restore?'Restore access':'Save changes'));
  accessRoleHelp(document.getElementById('account-access-form'));
 }
 document.addEventListener('change',event=>{
  const form=event.target.closest('[data-account-form="access"]');
  if(form&&event.target.name==='role')accessRoleHelp(form);
 });
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-account]');if(!button)return;event.preventDefault();
  try{
   const action=button.dataset.account,id=button.dataset.id;
   if(action==='forgot')return forgotScreen();
   if(action==='back'){resetToken='';history.replaceState(null,'',location.pathname+location.search);return loginScreen();}
   if(action==='edit-access')return await editAccess(id,button.dataset.restore==='true');
   if(action==='reset-link'){const out=await api('/users/'+encodeURIComponent(id)+'/reset-link',{method:'POST',data:{}});return showModal('Private password reset link','<p class="small muted">Confirm the employee identity and share this privately with that person. It expires after 30 minutes and works once. No email was sent.</p>'+textArea('draft_body','Private reset link',out.url,3),btn('copy-draft','Copy private link','btn-primary'));}
   if(action==='remove')return showModal('Remove account access',`<p class="account-email"><strong>${h(button.dataset.name)}</strong><br>${h(button.dataset.email)}</p><p>This person will be signed out and cannot sign in or recover access by email. Their company work records will be retained.</p>`,btn('close','Cancel')+`<button class="btn-danger" data-account="remove-confirm" data-id="${h(id)}">Remove access</button>`);
   if(action==='remove-confirm'){button.disabled=true;await api('/users/'+encodeURIComponent(id)+'/disable',{method:'POST',data:{}});modal.close();toast('Access removed. Existing sign-ins were revoked.');return navigate('access');}
   if(action==='revoke-invite'){button.disabled=true;await api('/account-access/invitations/'+encodeURIComponent(id)+'/revoke',{method:'POST',data:{}});toast('Invitation link revoked.');return navigate('access');}
  }catch(error){button.disabled=false;modal.open?modalError(error):toast(error.message);}
 });
 document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('[data-account-form]'))return;event.preventDefault();
  const button=event.submitter||form.querySelector('button[type="submit"]'),error=form.querySelector('.login-error');if(button)button.disabled=true;error.textContent='';
  try{
   const data=Object.fromEntries(new FormData(form));
   if(form.dataset.accountForm==='access'){
    const out=await api('/users/'+encodeURIComponent(form.dataset.userId)+'/access',{method:'POST',data:{...data,employee_id:data.employee_id||null,active:form.dataset.active==='true',expected_revision:form.dataset.revision}});
    modal.close();toast(out.restored?'Access restored. They can sign in with their existing password.':out.changed?'Account access updated. Previous sign-ins ended.':'No changes to save.');return await navigate('access');
   }
   if(form.dataset.accountForm==='forgot'){const out=await api('/forgot-password',{method:'POST',data});form.querySelector('.account-result').textContent=out.message;return;}
   if(data.password!==data.confirm_password)throw Error('Passwords do not match.');
   await api('/reset-password',{method:'POST',data:{...data,token:resetToken}});resetToken='';loginScreen();toast('Password reset. Sign in with your new password.');
  }catch(problem){error.textContent=problem.message;}finally{if(button)button.disabled=false;}
 });
})();
