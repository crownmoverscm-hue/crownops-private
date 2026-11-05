/* COM-8: owner-facing company profile, branding and pricing setup. Every save is server-validated, role-checked and audited. */
'use strict';
(function(){
 ensureNav(['company','file','Company profile']);
 const previousAllowed=allowedPages;
 allowedPages=function(){const pages=previousAllowed();return ['owner','manager','sales','accounting','dispatch'].includes(S.user.role)?[...new Set([...pages,'company'])]:pages.filter(p=>p!=='company');};
 const previousGroups=uxGroups;
 uxGroups=function(){const groups=previousGroups();const company=groups.find(g=>g[0]==='COMPANY');if(company&&allowedPages().includes('company')&&!company[1].includes('company'))company[1].unshift('company');return groups;};
 const ZONES=['America/St_Johns','America/Halifax','America/Moncton','America/Toronto','America/Winnipeg','America/Regina','America/Edmonton','America/Vancouver','America/Whitehorse'];
 const STATE={approved:['teal','Approved'],legacy:['teal','Existing settings'],'pending approval':['orange','Pending approval'],incomplete:['red','Incomplete'],optional:['','Optional']};
 const MONEY=[['local_fuel_per_truck','Local fuel per truck ($)'],['extra_truck_fee','Extra truck fee ($)'],['long_distance_km_rate','Long-distance rate per km ($)'],['junk_extra_labour','Junk removal extra labour per 30 min ($)'],['default_deposit','Default deposit ($)'],['testimonial_credit','Testimonial credit ($, optional)']];
 const HOURS=[['default_minimum_hours','Default minimum service hours'],['moving_packing_minimum_hours','Moving and packing minimum hours'],['small_afternoon_minimum_hours','Small afternoon job minimum hours']];
 const FACTORS=[['long_distance_km_buffer','Long-distance distance buffer (e.g. 1.05 = +5%)'],['long_distance_transport_multiplier','Long-distance transport multiplier']];
 const TIERS=[['single','Single item'],['quarter','Quarter load'],['half','Half load'],['three_quarter','Three-quarter load'],['full','Full load']];
 const C={};
 const manager=()=>['owner','manager'].includes(S.user.role);
 const pill=state=>{const [cls,label]=STATE[state]||['',state];return `<span class="pill ${cls}">${h(label)}</span>`;};
 const input=(name,label,value='',extra='')=>`<div class="form-field"><label for="cp-${name}">${label}</label><input id="cp-${name}" name="${name}" value="${h(value??'')}" ${extra} ${manager()?'':'disabled'}></div>`;
 X.pageRenderers.company=async function(){
  const [status,profile,pricing]=await Promise.all([api('/company/setup-status'),api('/company/profile'),api('/company/pricing-policy')]);
  Object.assign(C,{status,profile,pricing});
  const readOnly=manager()?'':xnote('Only an Owner or Manager can change these settings.','teal');
  const checklist=`<div class="table-wrap"><table><tbody>${status.items.map(i=>`<tr><td>${h(i.label)}</td><td>${pill(i.state)}</td></tr>`).join('')}</tbody></table></div>`+
   (status.ready?xnote('Setup is complete. Estimates, client messages and invoices can use these settings.','teal'):xnote('Finish the incomplete items before preparing estimates, client messages or invoices.'));
  const p=profile;
  const identity=`<form id="company-profile-form" data-company-form="profile"><div class="grid2">${input('name','Company name',p.configured?p.name:'','required maxlength="120"')}${input('short_name','Short name used in messages',p.configured?p.short_name:'','maxlength="60"')}
   ${input('phone','Client phone',p.configured?p.phone:'','required maxlength="40" inputmode="tel"')}${input('email','Client email',p.configured?p.email:'','required type="email" maxlength="240"')}
   ${input('timezone','Business time zone',p.configured?p.timezone:'','required list="cp-zones" maxlength="64"')}<datalist id="cp-zones">${ZONES.map(z=>`<option value="${z}">`).join('')}</datalist>
   ${input('brand_color','Brand colour (#RRGGBB, optional)',p.brand_color||'','maxlength="7" pattern="#[0-9a-fA-F]{6}"')}
   ${input('local_time_label','Local time label (English, optional)',p.configured?p.local_time_label:'','maxlength="60" placeholder="e.g. Vancouver time"')}${input('local_time_label_fr','Local time label (French, optional)',p.configured?p.local_time_label_fr:'','maxlength="60"')}</div>
   <p class="small muted">The time zone cannot change after jobs or schedules exist, because booked arrival times would shift.</p>
   ${manager()?'<button type="submit" class="btn-primary">Save company profile</button>':''}</form>`;
  const logo=`${(status.items.find(i=>i.key==='logo')||{}).state==='approved'?`<div class="row"><img id="company-logo-preview" alt="Current company logo" src="/api/company/logo?v=${Date.now()}" style="max-height:72px;max-width:220px"></div>`:'<p class="small muted">No logo uploaded. Client pages show your company name instead.</p>'}
   ${manager()?`<form id="company-logo-form" data-company-form="logo"><div class="form-field"><label for="cp-logo">Logo file (PNG, JPEG or WebP, under 2 MB)</label><input id="cp-logo" name="logo" type="file" accept="image/png,image/jpeg,image/webp" required></div><button type="submit" class="btn-soft">Upload logo</button> <button type="button" class="btn-small" data-company="remove-logo">Remove logo</button></form>`:''}`;
  const pol=pricing.policy||pricing.draft||{};
  const tiers=pol.junk_tiers||{};
  const truck=(pol.truck_minimums||[]).map(r=>r[0]+':'+r[1]).join(', ');
  const policyNote=pricing.configured?(pricing.source==='legacy-crown'?xnote('These are the existing pricing rules for this installation.','teal'):xnote('Approved pricing is in use (version '+h(pricing.version)+').','teal')):(pricing.draft?xnote('A draft is saved but not approved. Estimates stay blocked until you approve it.'):xnote('No pricing is set up. Enter your own values; CrownOps does not supply default prices.'));
  const pricingForm=`${policyNote}<form id="company-pricing-form" data-company-form="pricing"><div class="grid2">
   ${MONEY.map(([k,l])=>input(k,l,pol[k]??'','inputmode="decimal"')).join('')}${HOURS.map(([k,l])=>input(k,l,pol[k]??'','inputmode="decimal"')).join('')}${FACTORS.map(([k,l])=>input(k,l,pol[k]??'','inputmode="decimal"')).join('')}
   ${input('truck_minimums','Truck minimums (crew size:trucks, e.g. 6:3, 4:2)',truck,'')}
   ${input('limited_liability_text','Limited liability wording',pol.limited_liability_text||'','maxlength="200"')}</div>
   <h3>Junk removal tiers</h3><div class="grid2">${TIERS.map(([k,l])=>input('junk_name_'+k,l+' name',(tiers[k]||[])[0]||'','maxlength="80"')+input('junk_price_'+k,l+' price ($)',(tiers[k]||[])[1]||'','inputmode="decimal"')).join('')}</div>
   ${manager()?`<label class="checkbox-label full"><input type="checkbox" name="approved"> I have reviewed these prices and approve them for estimates</label><button type="submit" class="btn-primary">Save pricing</button>`:''}</form>`;
  const related=`<div class="grid3">${panel('Hourly crew rates',`${pill((status.items.find(i=>i.key==='rates')||{}).state)}<p class="small muted">Crew rates by date and crew size live in the rate calendar.</p><button class="btn-soft" data-page="rates">Open rate calendar</button>`)}
   ${panel('Tax and invoice settings',`${pill((status.items.find(i=>i.key==='billing')||{}).state)}<p class="small muted">Invoice issuer, address and tax registrations.</p><button class="btn-soft" data-page="policies">Open invoice setup</button>`)}
   ${panel('Company email',`<p class="small muted">Connect and approve the mailbox that sends client email.</p><button class="btn-soft" data-page="connections">Open connections</button>`)}</div>`;
  return head('Company profile','Identity, branding and pricing used across estimates, client messages and invoices.')+readOnly+panel('Setup status',checklist)+panel('Company identity',identity)+panel('Logo',logo)+panel('Pricing policy',pricingForm)+related;
 };
 function num(v){return String(v||'').trim();}
 document.addEventListener('submit',async e=>{
  const f=e.target.closest('[data-company-form]');if(!f)return;e.preventDefault();
  const kind=f.dataset.companyForm,d=Object.fromEntries(new FormData(f).entries());
  try{
   if(kind==='profile'){await api('/company/profile',{method:'PUT',data:{...d,version:C.profile.version}});toast('Company profile saved.');}
   if(kind==='logo'){const fd=new FormData();fd.append('file',f.logo.files[0]);await api('/company/logo',{method:'POST',form:fd});toast('Logo saved.');}
   if(kind==='pricing'){
    const truck=num(d.truck_minimums)?num(d.truck_minimums).split(',').map(x=>x.split(':').map(y=>parseInt(y.trim(),10))):[];
    const data={version:C.pricing.version,approved:f.approved.checked,truck_minimums:truck,limited_liability_text:d.limited_liability_text,junk_tiers:{}};
    [...MONEY,...HOURS,...FACTORS].forEach(([k])=>{data[k]=num(d[k]);});if(!data.testimonial_credit)delete data.testimonial_credit;
    TIERS.forEach(([k])=>{data.junk_tiers[k]=[num(d['junk_name_'+k]),num(d['junk_price_'+k])];});
    await api('/company/pricing-policy',{method:'PUT',data});toast(data.approved?'Pricing approved.':'Pricing draft saved.');
   }
   return navigate('company');
  }catch(ex){toast(ex.message);}
 });
 document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-company]');if(!b)return;
  if(b.dataset.company==='remove-logo'){if(!confirm('Remove the company logo?'))return;try{await api('/company/logo',{method:'DELETE'});toast('Logo removed.');navigate('company');}catch(ex){toast(ex.message);}}
 });
})();
