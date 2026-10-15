/* Owner hosting and recovery controls. Private backup files never enter browser storage. */
'use strict';
(function(){
 const previousLaunch=X.pageRenderers.launch;
 const download=name=>{
  const link=document.createElement('a');link.href='/api/owner-backups/'+encodeURIComponent(name);
  link.download=name;document.body.appendChild(link);link.click();link.remove();
 };
 X.pageRenderers.launch=async function(){
  const [company,d]=await Promise.all([previousLaunch(),api('/hosting-status')]);
  const checks=d.checks.map(c=>`<div class="company-launch-row"><span class="pill ${c.ready?'teal':'orange'}">${c.ready?'Checked':'Needs action'}</span><div class="grow"><div class="strong">${h(c.label)}</div><div class="small muted">${h(c.detail)}</div></div>${c.page?`<button class="btn-small" data-page="${h(c.page)}">Open</button>`:''}</div>`).join('');
  const backup=d.backup||{};
  const latest=backup.available?`<p class="small muted">Last restore verification: ${h(uxTimestampLabel(backup.verified_at))}. Includes ${h(backup.upload_files)} private files.</p><button class="btn-soft" data-recovery="download" data-file="${h(backup.filename)}">Download latest verified backup</button>`:'<p class="small muted">No verified portable recovery copy is available yet.</p>';
  const recovery=panel('Hosting & recovery',`<div class="company-launch-list">${checks}</div><div class="spacer"></div><div class="row wrap"><button class="btn-primary" data-recovery="create">Create, verify & download backup</button></div><div class="spacer"></div>${latest}<p class="small muted" style="margin-top:14px">The download contains private company data. Keep an encrypted copy outside this service and keep the existing server encryption key separately in the company password vault. This button creates a manual recovery copy.</p><details class="account-config"><summary>Hosting checks to verify in Railway</summary><ul class="company-steps">${d.hosting_verification_required.map(x=>`<li>${h(x)}</li>`).join('')}</ul><p class="small muted">${h(d.notice)}</p></details>`);
  const container=document.createElement('div');container.innerHTML=company;
  if(container.firstElementChild?.classList.contains('page-head'))container.firstElementChild.remove();
  return head('Launch checklist','Set up permanent team access, hosting and recovery, then verify the workflows your team uses.')+recovery+'<div class="spacer"></div>'+container.innerHTML;
 };
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-recovery]');if(!button)return;event.preventDefault();
  if(button.dataset.recovery==='download')return download(button.dataset.file);
  const label=button.textContent;button.disabled=true;button.textContent='Creating and verifying recovery copy…';
  try{
   const out=await api('/owner-backups',{method:'POST',data:{}});
   download(out.filename);toast('Backup restore verified. Download started; store the copy privately outside Railway.');
   await navigate('launch');
  }catch(error){toast(error.message);}finally{if(button.isConnected){button.disabled=false;button.textContent=label;}}
 });
})();
