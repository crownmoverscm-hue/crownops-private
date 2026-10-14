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
  const instructions=status.available?'<p class="small muted">Staff can use Forgot your password? on the sign-in screen. Check the first recovery email in the actual inbox before rolling this out to the team.</p>':'<p class="small muted">Connect the company Gmail account, then enable account recovery emails in the hosting settings. You can provide a private reset link from this page while email recovery is being set up.</p>';
  return panel('Password recovery',`<span class="pill ${status.available?'teal':'orange'}">${h(label)}</span>${instructions}<button class="btn-soft" data-page="connections">Open connections</button><details class="account-config"><summary>Hosting settings for account emails</summary><p class="small">Set <code>CROWNOPS_ALLOW_AUTH_EMAIL=1</code> after Gmail sending permission is approved. While the app is in staging, list the approved test addresses in <code>CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST</code>, separated by commas. Use the existing encryption key and Google credentials.</p></details>`);
 }
 X.pageRenderers.access=async function(){
  const d=await api('/account-access');S.user.is_master_owner=d.is_master_owner;
  const accountCards=d.accounts.map(person=>{
   const actions=person.active&&person.can_manage?`<button class="btn-small" data-account="reset-link" data-id="${h(person.id)}">Private reset link</button><button class="btn-danger btn-small" data-account="remove" data-id="${h(person.id)}" data-name="${h(person.name)}" data-email="${h(person.email)}">Remove access</button>`:'';
   const access=person.master_owner?'Master owner':person.role==='owner'?'Owner':person.role==='dispatch'?'Dispatch / manager':person.role;
   return `<div class="account-card"><div class="grow"><div class="strong">${h(person.name)}</div><div class="small muted account-email">${h(person.email)}</div><div class="tiny muted">${h(access)}${person.id===S.user.id?' &middot; Your account':''}</div></div><span class="pill ${person.active?'teal':'gray'}">${person.active?'Active':'Access removed'}</span><div class="account-actions">${actions}</div></div>`;
  }).join('');
  const pending=d.invitations.map(invite=>`<div class="account-card"><div class="grow"><div class="strong">${h(invite.name)}</div><div class="small muted account-email">${h(invite.email)}</div><div class="tiny muted">${h(invite.role)} &middot; Expires ${h(uxTimestampLabel(invite.expires_at))}</div></div>${invite.role!=='owner'||d.is_master_owner?`<button class="btn-small" data-account="revoke-invite" data-id="${h(invite.id)}">Revoke link</button>`:''}</div>`).join('');
  return head('Team access','Individual passwords, private invitations and owner-controlled access.',btn('invite',icon('plus')+'Invite team member','btn-primary'))+
   `<div class="callout teal"><strong>${d.is_master_owner?'You are the master owner.':'The first owner account is protected.'}</strong>The master owner controls owner invitations and other owner accounts. Operational managers should use Dispatch access. Nobody can remove their own access or disable the master owner.</div><div class="spacer"></div>`+
   panel('Staff accounts',accountCards)+`<div class="spacer"></div>`+panel('Pending invitations',pending||'<p class="small muted">No active invitations. Create one private link per person; each link works once and expires after 48 hours.</p>')+`<div class="spacer"></div>`+recoveryCard(d.email_recovery)+
   '<p class="small muted" style="margin-top:16px">Removing access signs the person out and cancels unused account reset links. Their employee and work records remain available for company records.</p>';
 };

 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-account]');if(!button)return;event.preventDefault();
  try{
   const action=button.dataset.account,id=button.dataset.id;
   if(action==='forgot')return forgotScreen();
   if(action==='back'){resetToken='';history.replaceState(null,'',location.pathname+location.search);return loginScreen();}
   if(action==='reset-link'){const out=await api('/users/'+encodeURIComponent(id)+'/reset-link',{method:'POST',data:{}});return showModal('Private password reset link','<p class="small muted">Confirm the employee identity and share this privately with that person. It expires after 30 minutes and works once. No email was sent.</p>'+textArea('draft_body','Private reset link',out.url,3),btn('copy-draft','Copy private link','btn-primary'));}
   if(action==='remove')return showModal('Remove account access',`<p class="account-email"><strong>${h(button.dataset.name)}</strong><br>${h(button.dataset.email)}</p><p>This person will be signed out and cannot sign in or recover access by email. Their company work records will be retained.</p>`,btn('close','Cancel')+`<button class="btn-danger" data-account="remove-confirm" data-id="${h(id)}">Remove access</button>`);
   if(action==='remove-confirm'){button.disabled=true;await api('/users/'+encodeURIComponent(id)+'/disable',{method:'POST',data:{}});modal.close();toast('Access removed. Existing sign-ins were revoked.');return navigate('access');}
   if(action==='revoke-invite'){button.disabled=true;await api('/account-access/invitations/'+encodeURIComponent(id)+'/revoke',{method:'POST',data:{}});toast('Invitation link revoked.');return navigate('access');}
  }catch(error){button.disabled=false;modal.open?modalError(error):toast(error.message);}
 });
 document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('[data-account-form]'))return;event.preventDefault();
  const button=form.querySelector('button[type="submit"]'),error=form.querySelector('.login-error');button.disabled=true;error.textContent='';
  try{
   const data=Object.fromEntries(new FormData(form));
   if(form.dataset.accountForm==='forgot'){const out=await api('/forgot-password',{method:'POST',data});form.querySelector('.account-result').textContent=out.message;return;}
   if(data.password!==data.confirm_password)throw Error('Passwords do not match.');
   await api('/reset-password',{method:'POST',data:{...data,token:resetToken}});resetToken='';loginScreen();toast('Password reset. Sign in with your new password.');
  }catch(problem){error.textContent=problem.message;}finally{button.disabled=false;}
 });
})();
