/* Current-service paste import. Source labels never become booking/payment state. */
'use strict';
(function(){
  let sourceText='', previewResult=null;
  const office=()=>['owner','manager'].includes(S.user.role);
  const items=()=> (S.importedSchedule||[]).filter(e=>e.source_type==='pasted_schedule'&&!e.promoted_job_id);
  const card=e=>`<button type="button" class="job-card" data-action="open-imported-job" data-id="${h(e.id)}"><span class="pill orange">Planning needed</span><strong>${h(e.client_name)}</strong><div>${h(e.service||'Service type needed')}</div><small>${h(e.time_label||'Time needed')} · ${h(e.crew_size||'?')} movers requested</small><small>${h(e.crew_raw||'Crew names needed')}</small></button>`;
  const baseSchedule=schedulePage;
  schedulePage=async function(){
    const html=await baseSchedule();
    S.importedSchedule=await api('/schedule-imports/events');
    const current=items();
    const undated=current.filter(e=>!e.date).length;
    const toolbar=undated?`<div class="row wrap" style="margin-top:20px"><button type="button" data-schedule-import="undated">${undated} service${undated===1?' needs':'s need'} a date</button></div>`:'';
    if(S.jobScope==='mine')return html;
    const template=document.createElement('template');template.innerHTML=html;
    const days=weekDays().map(dateIso);
    template.content.querySelectorAll('.weekly-day-column').forEach((column,i)=>{
      const matches=current.filter(e=>e.date===days[i]);
      if(!matches.length)return;
      column.querySelector('.weekly-day-jobs')?.insertAdjacentHTML('beforeend',matches.map(card).join(''));
      column.querySelectorAll('.weekly-open-slot').forEach(el=>el.remove());
    });
    return template.innerHTML+toolbar;
  };
  const baseSettings=settingsPage;
  settingsPage=async function(){
    const html=await baseSettings();
    return html+(office()?'<div class="spacer"></div>'+panel('Schedule setup','<p>Bring existing services into CrownOps and finish their planning details.</p><div class="row wrap"><button type="button" data-schedule-import="paste">Import current services</button><button type="button" data-schedule-import="list">Review setup services</button></div>'):'');
  };
  function pasteForm(){
    previewResult=null;
    showModal('Import current services',`<p>Copy the schedule with its column headings and paste it below. Only rows whose Status says <strong>In progress</strong> will be selected.</p><form id="schedule-paste-form"><div class="form-field"><label for="schedule-text-file">Or choose a pasted-text / TSV file</label><input id="schedule-text-file" type="file" accept=".txt,.tsv,text/plain,text/tab-separated-values"></div>${textArea('schedule_text','Pasted schedule',sourceText,10)}<p>Dates use MM/DD/YYYY. Missing information stays flagged. This saves source services for review without sending emails, recording payments or confirming bookings.</p></form>`,btn('close','Cancel')+'<button type="submit" form="schedule-paste-form" class="btn-primary">Preview import</button>',true);
  }
  function showPreview(p){
    previewResult=p;
    showModal('Review current-service import',`<p><strong>${p.summary.selected} selected · ${p.summary.new} new · ${p.summary.skipped} skipped</strong></p><p>${p.summary.undated} need a date. ${p.summary.missing_service} need a service type. Existing records will not be overwritten.</p><div class="table-wrap"><table><thead><tr><th>Client / date</th><th>Service</th><th>Result</th><th>Needs review</th></tr></thead><tbody>${p.events.map(e=>`<tr><td><strong>${h(e.client_name)}</strong><br>${h(e.date||'Date needed')}</td><td>${h(e.service||'Service needed')}</td><td>${h(e.action)}</td><td>${h(e.flags.join('; '))}</td></tr>`).join('')}</tbody></table></div><p>No emails, payment receipts, crew assignments or booking confirmations will be created.</p>`, '<button type="button" data-schedule-import="paste">Back</button>'+`<button type="button" data-schedule-import="commit" class="btn-primary" ${p.summary.new?'':'disabled'}>Import ${p.summary.new} services for review</button>`,true);
  }
  document.addEventListener('change',async e=>{
    if(e.target.id!=='schedule-text-file')return;
    const file=e.target.files[0];if(!file)return;
    try{
      if(file.size>1000000)throw Error('Choose a text file smaller than 1 MB.');
      const text=await file.text();const field=document.querySelector('#schedule-paste-form [name=schedule_text]');
      if(field){field.value=text;sourceText=text;}
    }catch(ex){modalError(ex);}
  });
  document.addEventListener('submit',async e=>{
    if(e.target.id!=='schedule-paste-form')return;
    e.preventDefault();const button=document.querySelector('[form="schedule-paste-form"]');button.disabled=true;
    try{sourceText=new FormData(e.target).get('schedule_text');showPreview(await api('/schedule-imports/paste-preview',{method:'POST',data:{text:sourceText}}));}
    catch(ex){modalError(ex);}finally{if(button.isConnected)button.disabled=false;}
  });
  document.addEventListener('click',async e=>{
    const button=e.target.closest('[data-schedule-import]');if(!button)return;
    e.preventDefault();button.disabled=true;
    try{
      if(button.dataset.scheduleImport==='paste')return pasteForm();
      if(['list','undated'].includes(button.dataset.scheduleImport)){
        S.importedSchedule=await api('/schedule-imports/events');
        const list=items().filter(e=>button.dataset.scheduleImport!=='undated'||!e.date);
        return showModal(button.dataset.scheduleImport==='undated'?'Services needing a date':'Service planning',`<p>Open a service to complete its date, crew, truck and booking details.</p><div class="grid2">${list.sort((a,b)=>(a.date||'9999').localeCompare(b.date||'9999')).map(e=>`<section><h3>${h(e.date||'Date needed')}</h3>${card(e)}</section>`).join('')||'<p>No services need setup.</p>'}</div>`,btn('close','Close'),true);
      }
      if(button.dataset.scheduleImport==='commit'&&previewResult){
        const result=await api('/schedule-imports/paste',{method:'POST',data:{text:sourceText,preview_token:previewResult.preview_token}});
        sourceText='';previewResult=null;modal.close();await navigate('schedule');
        showModal('Current services imported',`<h3>${result.imported} services saved for review</h3><p>${result.skipped} existing or repeated entries skipped. ${result.undated} saved without a date.</p><p>Find them in Schedule → All Jobs, or Settings & imports → Review setup services. Review a source record before creating its working job. No emails or payment receipts were created.</p>`,btn('close','Done'));
      }
    }catch(ex){modalError(ex);}finally{if(button.isConnected)button.disabled=false;}
  });
})();
