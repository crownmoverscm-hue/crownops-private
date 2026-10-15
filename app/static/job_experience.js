/* CrownOps v5.4 simplified job experience.
   Normal work should be one-click. History is preserved behind the scenes. */
(function(){
  const oldOpenJob = openJob;

  function crewLabel(j){
    const n=(j.crew_names||[]).length;
    return n ? `${n} Mover${n===1?'':'s'}` : 'Crew not assigned';
  }
  function qStatus(j){return j.quote_summary?.status||'';}
  function transportLabel(q){
    if(!q) return 'Not quoted';
    const hrs=Number(q.transport_hours||0);
    const hours=`${Number.isInteger(hrs)?hrs:hrs.toFixed(2).replace(/0+$/,'').replace(/\.$/,'')} hr${hrs===1?'':'s'}`;
    return `${hours} · ${money(q.transport_cents||0)}`;
  }
  function estimateLabel(q){
    if(!q || q.service_low==null || q.service_high==null) return 'Not quoted';
    return `${q.service_low}–${q.service_high} hours`;
  }
  function safeEmailLink(v){return v?`<a href="mailto:${h(v)}">${h(v)}</a>`:'Not supplied';}
  function safePhoneLink(v){return v?`<a href="tel:${h(v.replace(/[^+\d]/g,''))}">${h(v)}</a>`:'Not supplied';}
  function info(label,value){return `<div class="job-kv"><div class="info-label">${h(label)}</div><div class="job-value">${value}</div></div>`;}
  function roleCanEdit(){return may(['owner','sales','dispatch']);}
  function roleCanQuote(){return may(['owner','sales']);}

  openJob=async function(jid){
    const jobs=await api('/jobs'); const j=jobs.find(x=>x.id===jid); if(!j)throw Error('Job not found.');
    S.modalContext={job:j};
    const q=j.quote_summary||null;
    const assigned=S.user.role!=='crew'||j.crew_ids.includes(S.user.employee_id);
    const readOnlyCrew=S.user.role==='crew'&&!assigned;
    const quoteAccepted=qStatus(j)==='Accepted';
    const canInvoice=quoteAccepted && !readOnlyCrew;
    const attention=[];
    if(!(j.crew_names||[]).length)attention.push('crew');
    if(!(j.truck_names||[]).length)attention.push('truck');
    if(!q)attention.push('quote');
    const attentionTag=attention.length?`<span class="pill orange">Needs ${h(attention.join(' / '))}</span>`:`<span class="pill teal">Details ready</span>`;

    const clientBlock=`<section class="job-section job-client-section">
      <div class="job-section-head"><div><div class="eyebrow">CLIENT INFORMATION</div><h2>${h(j.client.client_name||j.title)}</h2></div>${roleCanEdit()?`<button class="btn-small" data-jx="edit-client" data-id="${h(jid)}">Edit</button>`:''}</div>
      <div class="job-info-grid">
        ${info('Phone',safePhoneLink(j.client.phone))}
        ${info('Email',safeEmailLink(j.client.email))}
      </div>
    </section>`;

    const movingBlock=`<section class="job-section">
      <div class="job-section-head"><div><div class="eyebrow">MOVING INFORMATION</div><h3>${h(j.phase)} · ${dateLabel(localDay(j.start_at))}</h3></div><div class="row wrap">${pill(j.status)}${attentionTag}</div></div>
      <div class="job-highlight-strip">
        <div><span>Team size</span><strong>${h(crewLabel(j))}</strong></div>
        <div><span>Estimated service</span><strong>${h(estimateLabel(q))}</strong></div>
        <div><span>Transportation</span><strong>${h(transportLabel(q))}</strong></div>
        <div><span>Truck</span><strong>${h((j.truck_names||[]).join(', ')||'Not assigned')}</strong></div>
      </div>
      <div class="job-info-grid job-grid-3">
        ${info('Start',`${timeLabel(j.start_at)}${j.end_at?` – ${timeLabel(j.end_at)}`:''}`)}
        ${info('Assigned team',h((j.crew_names||[]).join(', ')||'Not assigned'))}
        ${info('Home size',h(j.client.home_size||'Not supplied'))}
        ${info('Boxes',h(j.client.boxes||'Not supplied'))}
        ${info('Heavy / specialty',h(j.client.heavy_items||'None recorded'))}
        ${info('Additional stops',(j.stops||[]).length?(j.stops||[]).map(h).join('<br>'):'None recorded')}
      </div>
      <div class="job-route-grid">
        <div class="route-card"><div class="info-label">PICKUP</div><strong>${h(j.client.pickup||'Not supplied')}</strong><div>${h(j.client.pickup_access||'Access not supplied')}</div><div class="tiny muted">${h(j.client.pickup_walk||'Parking / walk not supplied')}</div></div>
        <div class="route-arrow">→</div>
        <div class="route-card"><div class="info-label">DESTINATION</div><strong>${h(j.client.dropoff||'Not supplied')}</strong><div>${h(j.client.dropoff_access||'Access not supplied')}</div><div class="tiny muted">${h(j.client.dropoff_walk||'Parking / walk not supplied')}</div></div>
      </div>
      <div class="job-notes"><div class="info-label">INVENTORY / NOTES</div><div>${h([j.client.inventory,j.client.notes,j.notes].filter(Boolean).join('\n\n')||'No inventory or notes recorded.')}</div></div>
    </section>`;

    let primary='';
    if(j.status==='Tentative'&&roleCanEdit()) primary+=`<button class="btn-primary" data-jx="confirm" data-id="${h(jid)}">Confirm job</button>`;
    if(j.status==='Confirmed'&&!readOnlyCrew) primary+=btn('job-status','Start job','btn-primary',`data-id="${h(jid)}" data-version="${j.version}" data-status="In progress"`);
    if(j.status==='In progress'&&!readOnlyCrew) primary+=btn('job-status','Mark completed','btn-primary',`data-id="${h(jid)}" data-version="${j.version}" data-status="Completed"`);

    const quick=`<div class="job-actions">
      <button class="job-action-card" data-jx="photos" data-id="${h(jid)}"><span class="job-action-icon">▧</span><span><strong>Photos</strong><small>View or upload client / estimate photos</small></span></button>
      ${q?`<button class="job-action-card" data-jx="quote" data-id="${h(jid)}"><span class="job-action-icon">$</span><span><strong>View Quote Details</strong><small>${h(estimateLabel(q))} · ${h(transportLabel(q))}</small></span></button>`:''}
      ${canInvoice?`<button class="job-action-card" data-jx="invoice" data-id="${h(jid)}"><span class="job-action-icon">▤</span><span><strong>Prepare invoice</strong><small>Uses the accepted quote</small></span></button>`:''}
      ${roleCanEdit()?`<button class="job-action-card" data-jx="edit-plan-simple" data-id="${h(jid)}"><span class="job-action-icon">✎</span><span><strong>Edit team / truck / timing</strong><small>Schedule details and assignments</small></span></button>`:''}
    </div>`;

    const readOnly=readOnlyCrew?`<div class="callout"><strong>Viewing another team's job</strong>You can review the client, moving details, accepted quote and photos. Editing stays with the assigned team or office.</div>`:'';
    showModal(h(j.client.client_name||j.title),`${clientBlock}${movingBlock}${readOnly}<div class="spacer"></div>${quick}<div class="spacer"></div><div class="row wrap">${primary}${may(['owner','dispatch'])&&!['Completed','Cancelled'].includes(j.status)?btn('cancel-job','Cancel job','btn-danger',`data-id="${h(jid)}" data-version="${j.version}"`):''}</div>`,btn('close','Close'),true);
  };

  async function openPhotos(jid){
    const jobs=await api('/jobs'); const j=jobs.find(x=>x.id===jid); if(!j)throw Error('Job not found.');
    const [files,policy]=await Promise.all([api('/leads/'+encodeURIComponent(j.lead_id)+'/uploads'),api('/upload-policy')]);
    const imgs=files.filter(f=>String(f.mime||'').startsWith('image/'));
    const others=files.filter(f=>!String(f.mime||'').startsWith('image/'));
    const assigned=S.user.role!=='crew'||j.crew_ids.includes(S.user.employee_id);
    const canUpload=(S.user.role!=='crew'||assigned)&&policy.enabled;
    const gallery=imgs.length?`<div class="photo-grid">${imgs.map(f=>CrownPhotos.ready(f)?`<a class="photo-tile" href="/api/uploads/${encodeURIComponent(f.id)}" target="_blank" rel="noopener"><img src="/api/uploads/${encodeURIComponent(f.id)}" alt="${h(f.category)}"><span>${h(f.category)}</span></a>`:`<div class="photo-tile pending"><div>Photo pending safety review</div><span>${h(f.category)}</span></div>`).join('')}</div>`:`<div class="empty-photo-state"><strong>No photos yet</strong><span>Upload inventory, stairs, elevator, access or in-person estimate photos here.</span></div>`;
    const extra=others.length?`<div class="spacer"></div><h3>Other files</h3>${others.map(f=>`<div class="activity-row"><div class="grow"><strong>${h(f.original_name)}</strong><div class="tiny muted">${h(f.category)}</div></div>${CrownPhotos.ready(f)?`<a href="/api/uploads/${encodeURIComponent(f.id)}" target="_blank">Open</a>`:pill(f.scan_status)}</div>`).join('')}`:'';
    const upload=canUpload?`<div class="spacer"></div><form id="job-photo-upload" data-jx-form="photo-upload" data-max-mb="${policy.max_file_mb}"><input type="hidden" name="job_id" value="${h(jid)}"><input type="hidden" name="lead_id" value="${h(j.lead_id)}"><div class="grid2"><div class="form-field"><label>Photo type</label><select name="category"><option value="Move photos">Furniture / inventory</option><option value="Access">Stairs / elevator / access</option><option value="Inventory">Inventory</option><option value="Incident">Condition / issue</option></select></div><div class="form-field"><label>Choose up to 10 photos · ${h(policy.max_file_mb)} MB each</label><input type="file" name="file" accept="${h(policy.accept)}" multiple required></div></div></form>`:'';
    showModal('Photos · '+h(j.client.client_name),`<p class="small muted">Client uploads and Crown in-person estimate photos live together on this service.</p><p class="small muted">${h(policy.message)}</p><p id="job-photo-progress" role="status"></p>${gallery}${extra}${upload}`,canUpload?`<button data-action="open-job" data-id="${h(jid)}">Back to move</button>${btn('close','Close')}<button class="btn-primary" type="submit" form="job-photo-upload">Upload</button>`:btn('close','Close'),true);
  }

  async function openQuoteDetails(jid){
    const jobs=await api('/jobs'); const j=jobs.find(x=>x.id===jid); if(!j)throw Error('Job not found.');
    const q=j.quote_summary; if(!q) return showModal('Quote details','<div class="callout orange"><strong>No linked quote</strong>Create or link an estimate first.</div>',btn('close','Close'));
    let full=null;
    if(roleCanQuote()){
      const qs=await api('/quotes'); full=qs.find(x=>x.id===q.id)||null;
    }
    const locked=q.status==='Accepted';
    const editAllowed=roleCanQuote()&&j.status==='Tentative';
    const note=locked?`<div class="callout teal"><strong>Accepted quote is preserved</strong>If details change, CrownOps creates a new draft revision. The accepted version is never overwritten, and the revised quote must be sent and accepted again.</div>`:`<div class="callout"><strong>Versioned quote</strong>Changing the estimate creates a new draft revision so the history stays intact.</div>`;
    const summary=`<div class="job-highlight-strip quote-strip"><div><span>Team</span><strong>${h(q.movers+' Movers')}</strong></div><div><span>Estimate</span><strong>${h(estimateLabel(q))}</strong></div><div><span>Transportation</span><strong>${h(transportLabel(q))}</strong></div><div><span>Hourly rate</span><strong>${money(q.hourly_rate_cents||0)}/h</strong></div></div><div class="job-info-grid"><div><div class="info-label">Quote revision</div>Revision ${h(q.revision)} · ${pill(q.status)}</div><div><div class="info-label">Minimum</div>${h(q.minimum_hours)} service hours</div><div><div class="info-label">Estimated total</div>${compact(q.low_cents||0)} – ${compact(q.high_cents||0)}</div><div><div class="info-label">Deposit</div>${money(Number(q.deposit_required||0)*100)}</div></div>`;
    let form='';
    if(editAllowed){
      const low=full?.result?.service_low??q.service_low, high=full?.result?.service_high??q.service_high, tr=full?.result?.transport_hours??q.transport_hours;
      form=`<div class="spacer"></div><h3>${locked?'Create revised quote':'Adjust estimate'}</h3><form id="job-quote-revise" data-jx-form="quote-revise"><input type="hidden" name="job_id" value="${h(jid)}"><div class="grid3">${field('hours_low','Estimate from',low,'number','step="0.25" min="0.25" required')}${field('hours_high','Estimate to',high,'number','step="0.25" min="0.25" required')}${field('transport_hours','Transportation fee (hours)',tr,'number','step="0.25" min="0" required')}</div><p class="tiny muted">Example: Estimate from 5 to 7 hours · Transportation fee 1 hour. CrownOps recalculates the dollar amount from the quote rate.</p></form>`;
    }
    const footer=`${btn('close','Close')}${roleCanQuote()?`<button class="btn-soft" data-jx="full-quote" data-id="${h(q.id)}">Open full quote</button>`:''}${editAllowed?`<button class="btn-primary" type="submit" form="job-quote-revise">${locked?'Create revised quote':'Save revised estimate'}</button>`:''}`;
    showModal('Quote details · '+h(j.client.client_name),summary+'<div class="spacer"></div>'+note+form,footer,true);
  }

  document.addEventListener('click',async e=>{
    const el=e.target.closest('[data-jx]'); if(!el)return; e.preventDefault();
    const a=el.dataset.jx,id=el.dataset.id;
    try{
      if(a==='confirm'){
        const jobs=await api('/jobs'); const j=jobs.find(x=>x.id===id); if(!j)throw Error('Job not found.');
        await api('/jobs/'+id+'/confirm',{method:'POST',data:{version:j.version}});toast('Job confirmed.');await navigate('schedule');return openJob(id);
      }
      if(a==='photos')return openPhotos(id);
      if(a==='quote')return openQuoteDetails(id);
      if(a==='invoice'){
        try{await api('/jobs/'+id+'/invoice-from-quote',{method:'POST',data:{}});}catch(ex){if(ex.status!==409)throw ex;}
        return openJobInvoice(id);
      }
      if(a==='edit-client'){const jobs=await api('/jobs');const j=jobs.find(x=>x.id===id);return editLead(j.lead_id);}
      if(a==='edit-plan-simple'){const b=document.createElement('button');b.dataset.v4='edit-plan';b.dataset.id=id;document.body.appendChild(b);b.click();b.remove();return;}
      if(a==='full-quote')return openQuote(id);
    }catch(ex){modal.open?modalError(ex):toast(ex.message);}
  });

  document.addEventListener('submit',async e=>{
    const f=e.target.closest('form[data-jx-form]');if(!f)return;e.preventDefault();
    const d=readForm(f);
    const button=document.querySelector(`button[form="${f.id}"]`);if(button)button.disabled=true;
    try{
      if(f.dataset.jxForm==='photo-upload'){
        const lid=f.elements.lead_id.value,jid=f.elements.job_id.value;
        const result=await CrownPhotos.batch(f,fd=>api('/leads/'+encodeURIComponent(lid)+'/uploads',{method:'POST',form:fd}),msg=>{const el=document.getElementById('job-photo-progress');if(el)el.textContent=msg;});
        if(result.saved){toast(result.saved+' photo(s) saved.');await openPhotos(jid);}
        if(result.error)modalError(new Error(result.saved+' of '+result.total+' saved. '+result.error));
        return;
      }
      if(f.dataset.jxForm==='quote-revise'){
        const jid=d.job_id;await api('/jobs/'+jid+'/quote-revision',{method:'POST',data:{hours_low:Number(d.hours_low),hours_high:Number(d.hours_high),transport_hours:Number(d.transport_hours)}});toast('New draft quote revision created. Resend it for approval.');return openQuoteDetails(jid);
      }
    }catch(ex){modal.open?modalError(ex):toast(ex.message);}finally{if(button)button.disabled=false;}
  });

  // expose for validation
  window.CrownJobExperience={openPhotos,openQuoteDetails};
})();
