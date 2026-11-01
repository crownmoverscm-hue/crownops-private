/* Staff-only appearance. No API requests, credentials, database fields or business actions. */
(function(global){
 'use strict';
 const names=Object.freeze({black:'Black View',light:'Light View',ocean:'Ocean Blue View'});
 const prefix='crownops.staff-theme.v1:';
 let staff=null,current='black',languageOverride='',queued=false;
 const memory=new Map();
 const doc=global.document;
 const valid=value=>Object.prototype.hasOwnProperty.call(names,value)?value:'black';
 const key=id=>prefix+encodeURIComponent(String(id));
 function language(){return (languageOverride||doc?.documentElement.getAttribute('data-ui-language')||((doc?.documentElement.lang||'').startsWith('fr')?'fr':global.navigator?.language)||'en').toLowerCase().startsWith('fr')?'fr':'en';}
 function read(id){try{return valid(global.localStorage.getItem(key(id))||memory.get(String(id)));}catch(_){return valid(memory.get(String(id)));}}
 function apply(value,announce=false){
  current=valid(value);
  if(doc){doc.documentElement.dataset.staffTheme=current;doc.querySelectorAll('[data-staff-theme-select]').forEach(s=>s.value=current);}
  if(announce&&doc){let live=doc.getElementById('staff-theme-status');if(!live){live=doc.createElement('div');live.id='staff-theme-status';live.className='staff-theme-sr';live.setAttribute('role','status');live.setAttribute('aria-live','polite');doc.body.append(live);}live.textContent=language()==='fr'?names[current]+' sélectionné.':names[current]+' selected.';}
 }
 function select(value){
  if(staff===null)return;
  const chosen=valid(value);memory.set(staff,chosen);
  try{global.localStorage.setItem(key(staff),chosen);}catch(_){}
  apply(chosen,true);
 }
 function setStaff(id){
  const next=id===null||id===undefined||id===''?null:String(id);
  if(next!==staff){staff=next;apply(staff===null?'black':read(staff));}
  schedule();
 }
 function setLanguage(value){languageOverride=String(value||'');schedule();}
 function createControl(where){
  const el=doc.createElement('div');el.className='staff-theme-control staff-theme-'+where;
  const label=doc.createElement('label');label.htmlFor='staff-theme-'+where;label.dataset.themeLabel='';label.id=label.htmlFor+'-label';
  const s=doc.createElement('select');s.id=label.htmlFor;s.dataset.staffThemeSelect='';s.setAttribute('aria-describedby',s.id+'-help');
  for(const [value,text] of Object.entries(names)){const option=doc.createElement('option');option.value=value;option.textContent=text;s.append(option);}
  const help=doc.createElement('small');help.id=s.id+'-help';help.dataset.themeHelp='';
  el.append(label,s,help);return el;
 }
 function enhance(){
  queued=false;if(!doc||staff===null)return;
  const sidebar=doc.querySelector('.app .sidebar');
  if(sidebar&&!sidebar.querySelector('.staff-theme-nav'))sidebar.querySelector('.brand')?.insertAdjacentElement('afterend',createControl('nav'));
  const content=doc.getElementById('content');const title=content?.querySelector('h1')?.textContent||'';
  if(content&&/settings|company setup|configuration|paramètres|réglages/i.test(title)&&!content.querySelector('.staff-theme-settings')){
   const panel=doc.createElement('section');panel.className='panel staff-theme-settings-panel';panel.setAttribute('aria-labelledby','staff-theme-settings-label');panel.append(createControl('settings'));
   const head=content.querySelector('.page-head');if(head)head.insertAdjacentElement('afterend',panel);else content.prepend(panel);
  }
  const mobile=doc.querySelector('.finish-mobile-brand');
  if(mobile&&!mobile.querySelector('.staff-theme-mobile-subtitle')){const sub=doc.createElement('span');sub.className='staff-theme-mobile-subtitle';sub.textContent='CROWN MOVERS';mobile.querySelector('strong')?.insertAdjacentElement('afterend',sub);}
  const lang=language();
  for(const control of doc.querySelectorAll('.staff-theme-control')){
   if(control.lang!==lang)control.lang=lang;
   const label=control.querySelector('[data-theme-label]'),help=control.querySelector('[data-theme-help]');
   const text=lang==='fr'?'Thème':'Theme',hint=lang==='fr'?'Enregistré pour ce compte dans ce navigateur.':'Saved for this account in this browser.';
   if(label.textContent!==text)label.textContent=text;
   if(help.textContent!==hint)help.textContent=hint;
   control.querySelector('select').value=current;
  }
 }
 function schedule(){if(doc&&!queued){queued=true;global.queueMicrotask(enhance);}}
 const api=Object.freeze({names,select,setStaff,setLanguage,getTheme:()=>current,getStaff:()=>staff,refresh:schedule});
 global.CrownOpsThemes=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 if(!doc)return;
 apply('black');
 doc.addEventListener('change',e=>{if(e.target.matches?.('[data-staff-theme-select]'))select(e.target.value);});
 global.addEventListener('storage',e=>{if(staff!==null&&e.key===key(staff))apply(valid(e.newValue));});
 new global.MutationObserver(schedule).observe(doc.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['lang','data-ui-language']});
})(typeof window!=='undefined'?window:globalThis);
