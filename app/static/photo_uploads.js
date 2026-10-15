/* Shared private-photo upload controls for staff and the client portal. */
'use strict';
window.CrownPhotos={
 ready:file=>['Clean','Photo checked'].includes(file.scan_status),
 async batch(form,send,progress){
  const files=[...form.elements.file.files];
  if(!files.length)throw Error('Choose at least one photo.');
  if(files.length>10)throw Error('Choose up to 10 photos at a time.');
  const limit=Number(form.dataset.maxMb||15)*1024*1024;
  if(files.some(f=>f.size>limit))throw Error('One of the selected files is too large. Maximum '+form.dataset.maxMb+' MB per file.');
  let saved=0,error='';
  for(const file of files){
   const data=new FormData(form);data.delete('file');data.delete('lead_id');data.append('file',file);
   progress?.('Uploading '+(saved+1)+' of '+files.length+'…');
   try{await send(data);saved++;}catch(ex){error=file.name+': '+ex.message;break;}
  }
  return {saved,total:files.length,error};
 },
 async showUpload(lid,jid=''){
  const policy=await api('/upload-policy');
  if(!policy.enabled)return showModal('Add move photos','<p>'+h(policy.message)+'</p>',btn('close','Close'));
  const jobs=jid?[]:(await api('/jobs')).filter(j=>j.lead_id===lid);
  showModal('Add move photos','<p class="small muted">'+h(policy.message)+'</p><form id="private-photo-upload" data-photo-upload data-max-mb="'+h(policy.max_file_mb)+'"><input type="hidden" name="lead_id" value="'+h(lid)+'">'+
   (jid?'<input type="hidden" name="job_id" value="'+h(jid)+'">':select('job_id','Attach to',[['','Entire moving file — shared with its assigned crews'],...jobs.map(j=>[j.id,j.title+' · '+j.phase])],''))+
   select('category','Photo type',['Move photos','Inventory','Access','Incident'],'Move photos')+
   '<div class="form-field"><label for="private-photo-input">Choose up to 10 photos · '+h(policy.max_file_mb)+' MB each</label><input id="private-photo-input" type="file" name="file" accept="'+h(policy.accept)+'" multiple required></div><p id="private-photo-progress" role="status"></p></form>',
   btn('close','Cancel')+'<button type="submit" form="private-photo-upload" class="btn-primary">Upload photos</button>');
 }
};
document.addEventListener('submit',async e=>{
 const f=e.target.closest('form[data-photo-upload]');if(!f)return;e.preventDefault();
 const button=modal.querySelector('button[form="'+f.id+'"]');if(button?.disabled)return;
 if(button)button.disabled=true;
 try{
  const lid=f.elements.lead_id.value;
  const result=await CrownPhotos.batch(f,data=>api('/leads/'+encodeURIComponent(lid)+'/uploads',{method:'POST',form:data}),msg=>{const el=document.getElementById('private-photo-progress');if(el)el.textContent=msg;});
  if(result.saved){toast(result.saved+' photo(s) saved.');await clientFiles(lid);}
  if(result.error)modalError(new Error(result.saved+' of '+result.total+' saved. '+result.error));
 }catch(ex){modalError(ex);}finally{if(button?.isConnected)button.disabled=false;}
});
