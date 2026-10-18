/* Shared private-photo upload controls for staff and the client portal. */
'use strict';
window.CrownPhotos={
 ready:file=>['Clean','Photo checked','Video checked'].includes(file.scan_status),
 async batch(form,send,progress){
  const files=[...form.elements.file.files];
  if(!files.length)throw Error('Choose at least one photo.');
  if(files.length>10)throw Error('Choose up to 10 photos at a time.');
  const limit=Number(form.dataset.maxMb||15)*1024*1024;
  let saved=0,error='';const failed=[];let index=0;
  for(const file of files){
   index++;
   const data=new FormData(form);data.delete('file');data.delete('lead_id');data.append('file',file);
   progress?.('Uploading '+index+' of '+files.length+' · '+file.name+'…');
   try{if(file.size>limit)throw Error('Maximum '+form.dataset.maxMb+' MB per file. You can paste a folder link instead.');await send(data);saved++;}catch(ex){failed.push(file);error+=(error?'\n':'')+file.name+': '+ex.message;}
  }
  if(failed.length){const selection=new DataTransfer();failed.forEach(file=>selection.items.add(file));form.elements.file.files=selection.files;}
  progress?.(saved+' of '+files.length+' saved.'+(failed.length?' '+failed.length+' file(s) remain selected for retry.':''));
  return {saved,total:files.length,error,failed:failed.length};
 },
 async showUpload(lid,jid=''){
  const policy=await api('/upload-policy');
  if(!policy.enabled){showModal('Add photos & videos','<p>'+h(policy.message)+'</p><div id="staff-media-links"></div>',btn('close','Close'));await CrownMedia.staffLinks(lid,jid,'staff-media-links');return;}
  const jobs=jid?[]:(await api('/jobs')).filter(j=>j.lead_id===lid);
  showModal('Add photos & videos','<p class="small muted">'+h(policy.message)+'</p><div id="staff-media-links"></div><form id="private-photo-upload" data-photo-upload data-max-mb="'+h(policy.max_file_mb)+'"><input type="hidden" name="lead_id" value="'+h(lid)+'">'+
   (jid?'<input type="hidden" name="job_id" value="'+h(jid)+'">':select('job_id','Attach to',[['','Entire moving file — shared with its assigned crews'],...jobs.map(j=>[j.id,j.title+' · '+j.phase])],''))+
   select('category','Photo type',['Move photos','Inventory','Access','Incident'],'Move photos')+
   '<div class="form-field media-drop"><label for="private-photo-input">Choose or drop up to 10 photos / short videos</label><input id="private-photo-input" type="file" name="file" accept="'+h(policy.accept)+'" multiple required></div><p id="private-photo-progress" role="status"></p></form>',
   btn('close','Cancel')+'<button type="submit" form="private-photo-upload" class="btn-primary">Upload files</button>');
  await CrownMedia.staffLinks(lid,jid,'staff-media-links');
 }
};
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-photo-upload]');if(!f)return;e.preventDefault();
 const button=modal.querySelector('button[form="'+f.id+'"]');if(button?.disabled)return;
 if(button)button.disabled=true;
 try{
  const lid=f.elements.lead_id.value;
  const result=await CrownPhotos.batch(f,data=>api('/leads/'+encodeURIComponent(lid)+'/uploads',{method:'POST',form:data}),msg=>{const el=document.getElementById('private-photo-progress');if(el)el.textContent=msg;});
  if(result.saved){toast(result.saved+' file(s) saved.');if(!result.error)await clientFiles(lid);}
  if(result.error)modalError(new Error(result.saved+' of '+result.total+' saved. '+result.error));
 }catch(ex){modalError(ex);}finally{if(button?.isConnected)button.disabled=false;}
});

document.addEventListener('dragover',e=>{const area=e.target.closest('.media-drop');if(area){e.preventDefault();area.classList.add('dragging');}});
document.addEventListener('dragleave',e=>e.target.closest('.media-drop')?.classList.remove('dragging'));
document.addEventListener('drop',e=>{const area=e.target.closest('.media-drop');if(!area)return;e.preventDefault();area.classList.remove('dragging');const input=area.querySelector('input[type=file]');if(input&&e.dataTransfer.files.length){input.files=e.dataTransfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));}});
