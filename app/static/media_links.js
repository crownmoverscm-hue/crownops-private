/* Folder links remain available alongside private uploads, including when upload service is unavailable. */
'use strict';
window.CrownMedia={
 normalize(url){url=url.trim();return url&&!/^https?:\/\//i.test(url)?'https://'+url:url;},
 async staffLinks(lid,jid,target){
  const box=document.getElementById(target);if(!box)return;
  const links=await api('/leads/'+encodeURIComponent(lid)+'/media-links');if(!box.isConnected)return;
  box.innerHTML='<section class="media-link-box"><h3>Photo / video folder links</h3><p class="small muted">Paste the link the client sent. Assigned crew can open it from this moving file. Check that the folder’s sharing settings let the team view it.</p>'+links.map(l=>'<p><a class="button-link" href="'+h(l.url)+'" target="_blank" rel="noopener noreferrer">'+h(l.label)+' ↗</a></p>').join('')+(S.user.role==='crew'&&!jid?'<p class="tiny muted">Open your assigned service to add another folder link.</p>':'<form data-media-link data-lead="'+h(lid)+'" data-job="'+h(jid)+'" data-target="'+h(target)+'"><div class="grid2">'+field('label','Folder name (optional)')+field('url','Drive / Dropbox / video-folder link','','text','required placeholder="https://drive.google.com/…"')+'</div><button type="submit" class="btn-soft">Save folder link</button></form>')+'</section>';
 }
};
const mediaBaseClientFiles=clientFiles;
clientFiles=async function(lid){await mediaBaseClientFiles(lid);const body=modal.querySelector('.modal-body');if(!body)return;body.insertAdjacentHTML('afterbegin','<div id="client-hub-media-links"></div>');await CrownMedia.staffLinks(lid,'','client-hub-media-links');};
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-media-link]');if(!f)return;e.preventDefault();const b=f.querySelector('[type=submit]');if(b.disabled)return;b.disabled=true;
 try{const d=Object.fromEntries(new FormData(f));d.url=CrownMedia.normalize(d.url);d.job_id=f.dataset.job||'';await api('/leads/'+encodeURIComponent(f.dataset.lead)+'/media-links',{method:'POST',data:d});toast('Folder link saved for this moving file.');await CrownMedia.staffLinks(f.dataset.lead,f.dataset.job,f.dataset.target);}catch(ex){modalError(ex);}finally{if(b.isConnected)b.disabled=false;}
});
