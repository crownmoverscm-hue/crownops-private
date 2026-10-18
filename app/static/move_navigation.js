/* Keep move context while visiting related screens; maps always offer a choice. */
'use strict';
const MoveNav={id:null,origin:null,scroll:{},opening:false};
function moveScroll(){return {body:modal.querySelector('.modal-body')?.scrollTop||0,dialog:modal.scrollTop};}
function rememberMovePosition(){if(modal.dataset.moveRoot)MoveNav.scroll[modal.dataset.moveRoot]=moveScroll();}
const moveShowModal=showModal;
showModal=function(...args){
 rememberMovePosition();delete modal.dataset.moveRoot;const result=moveShowModal(...args);
 if(MoveNav.id&&!MoveNav.opening&&![...modal.querySelectorAll('button')].some(b=>b.textContent.trim()==='Back to move')){
  let foot=modal.querySelector('.modal-foot');if(!foot){foot=document.createElement('div');foot.className='modal-foot';modal.appendChild(foot);}
  foot.insertAdjacentHTML('afterbegin','<button type="button" data-move-back>Back to move</button>');
 }
 return result;
};
const moveOpenJob=openJob;
openJob=async function(id){
 if(MoveNav.id!==id){
  MoveNav.id=id;MoveNav.origin={page:S.page,week:S.week?new Date(S.week):null,scope:S.jobScope,x:scrollX,y:scrollY,calendar:document.querySelector('.weekly-dispatch-scroll')?.scrollLeft||0};
 }
 MoveNav.opening=true;
 try{
  await moveOpenJob(id);modal.dataset.moveRoot=id;
  modal.querySelector('.job-actions')?.insertAdjacentHTML('beforeend','<button class="job-action-card" data-incident="new" data-id="'+h(id)+'"><span class="job-action-icon">!</span><span><strong>Report an incident</strong><small>Damage, property, truck or another issue</small></span></button>');
  const label=MoveNav.origin?.page==='schedule'?'Back to schedule':'Back to previous page';
  modal.querySelector('.modal-foot')?.insertAdjacentHTML('afterbegin','<button type="button" data-move-return>'+h(label)+'</button>');
  const pos=MoveNav.scroll[id];if(pos)requestAnimationFrame(()=>{const body=modal.querySelector('.modal-body');if(body)body.scrollTop=pos.body;modal.scrollTop=pos.dialog;});
 }finally{MoveNav.opening=false;}
};
const moveNavigate=navigate;
navigate=async function(page){
 rememberMovePosition();const result=await moveNavigate(page);
 if(MoveNav.id&&!MoveNav.opening){
  const content=document.getElementById('content');content?.querySelector('.move-return-banner')?.remove();
  content?.insertAdjacentHTML('afterbegin','<div class="callout teal move-return-banner"><span>Your move is still open.</span><button type="button" data-move-back>Back to move</button></div>');
 }
 return result;
};
function restoreMovePageScroll(origin){
 if(!origin)return;requestAnimationFrame(()=>{scrollTo(origin.x,origin.y);const calendar=document.querySelector('.weekly-dispatch-scroll');if(calendar)calendar.scrollLeft=origin.calendar;});
}
async function restoreMoveOrigin(origin){
 if(!origin)return;
 const changed=S.page!==origin.page||(origin.page==='schedule'&&(
  (S.week?.getTime()??null)!==(origin.week?.getTime()??null)||S.jobScope!==origin.scope));
 S.week=origin.week?new Date(origin.week):null;S.jobScope=origin.scope;
 if(changed)await moveNavigate(origin.page);
 restoreMovePageScroll(origin);
}
async function backToMove(){
 if(window.CrownPlanning&&document.getElementById('job-plan-form'))jpRemember();
 mapReturn=null;
 const id=MoveNav.id,origin=MoveNav.origin;if(!id)return;
 await restoreMoveOrigin(origin);
 return openJob(id);
}
function clearMoveContext(){mapReturn=null;MoveNav.id=null;MoveNav.origin=null;document.querySelectorAll('.move-return-banner').forEach(el=>el.remove());delete modal.dataset.moveRoot;}

function renderMoveRoute(route){
 return '<section class="move-route"><div class="row wrap"><h3>Service route</h3><span class="tiny muted">Follow the locations in this order</span></div><ol class="move-stop-list">'+route.map((stop,i)=>
  '<li class="move-stop-card"><span class="move-stop-number">'+(i+1)+'</span><div class="grow"><div class="eyebrow">'+h(stop.kind||'Stop')+'</div><strong>'+h(stop.address||'Address needed')+'</strong>'+
  (stop.access?'<p class="small">'+h(stop.access)+'</p>':'')+(stop.notes?'<p class="small muted">'+h(stop.notes)+'</p>':'')+'</div>'+
  (stop.address?'<button type="button" data-map-address="'+h(stop.address)+'" aria-label="Open map for stop '+(i+1)+'">Open map</button>':'')+'</li>').join('')+'</ol></section>';
}
function routeEditor(route){
 const items=route.length?route:[{kind:'Pickup',address:'',access:'',notes:''},{kind:'Delivery',address:'',access:'',notes:''}];
 return '<div data-route-editor><p class="small muted">Add every pickup, storage location and delivery in the order the team should visit.</p><div data-route-rows>'+items.map((stop,i)=>
 '<section class="route-edit-row"><div class="row wrap"><strong class="grow">Location '+(i+1)+'</strong><button type="button" data-route-action="up" aria-label="Move location '+(i+1)+' up" '+(i===0?'disabled':'')+'>↑</button><button type="button" data-route-action="down" aria-label="Move location '+(i+1)+' down" '+(i===items.length-1?'disabled':'')+'>↓</button><button type="button" data-route-action="remove" aria-label="Remove location '+(i+1)+'">Remove</button></div><div class="grid2">'+
 '<label>Type<select data-route-field="kind">'+['Pickup','Stop','Delivery','Storage','Return'].map(k=>'<option '+(stop.kind===k?'selected':'')+'>'+k+'</option>').join('')+'</select></label>'+
 '<label>Address<input data-route-field="address" value="'+h(stop.address||'')+'" maxlength="1000" autocomplete="street-address"></label>'+
 '<label>Access / parking<input data-route-field="access" value="'+h(stop.access||'')+'" maxlength="1000"></label><label>Instructions<input data-route-field="notes" value="'+h(stop.notes||'')+'" maxlength="2000"></label></div></section>').join('')+'</div><button type="button" data-route-action="add">Add stop</button></div>';
}
function readRouteEditor(container,includeEmpty=false){
 return [...container.querySelectorAll('.route-edit-row')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-route-field]')].map(el=>[el.dataset.routeField,el.value.trim()]))).filter(row=>includeEmpty||row.address||row.access||row.notes);
}
function modalSnapshot(){
 const clone=modal.cloneNode(true),from=[...modal.querySelectorAll('input,select,textarea')],to=[...clone.querySelectorAll('input,select,textarea')];
 from.forEach((el,i)=>{const c=to[i];if(el.type==='file')return;if(el.tagName==='TEXTAREA')c.textContent=el.value;else if(el.tagName==='SELECT')[...c.options].forEach((o,j)=>o.toggleAttribute('selected',el.options[j].selected));else{c.setAttribute('value',el.value);c.toggleAttribute('checked',el.checked);}});
 return {html:clone.innerHTML,width:modal.style.width,scroll:moveScroll(),root:modal.dataset.moveRoot,context:S.modalContext};
}
let mapReturn=null;
function restoreMapScreen(){
 if(!mapReturn)return;const s=mapReturn;mapReturn=null;modal.innerHTML=s.html;modal.style.width=s.width;S.modalContext=s.context;
 if(s.root)modal.dataset.moveRoot=s.root;else delete modal.dataset.moveRoot;
 requestAnimationFrame(()=>{const body=modal.querySelector('.modal-body');if(body)body.scrollTop=s.scroll.body;modal.scrollTop=s.scroll.dialog;});
}
function chooseMoveMap(address){
 mapReturn=modal.open?modalSnapshot():null;
 const google='https://www.google.com/maps/dir/?api=1&destination='+encodeURIComponent(address)+'&travelmode=driving';
 const apple='https://maps.apple.com/directions?destination='+encodeURIComponent(address)+'&mode=driving';
 showModal('Open location in…','<p class="map-address">'+h(address)+'</p><div class="map-choices"><a class="button-link btn-primary" href="'+h(google)+'" data-map-provider="google" target="_blank" rel="noopener">Google Maps</a><a class="button-link btn-soft" href="'+h(apple)+'" data-map-provider="apple" target="_blank" rel="noopener">Apple Maps</a></div><p class="tiny muted">Your device opens the selected app when supported, or its web map. Check the route for truck restrictions.</p>', '<button type="button" data-map-cancel>Back</button>');
}
document.addEventListener('click',async event=>{
 const el=event.target.closest('[data-move-back],[data-move-return],[data-route-action],[data-map-address],[data-map-provider],[data-map-cancel],[data-action="close"],a[href]');if(!el)return;
 try{
  if(el.matches('[data-move-back]')){event.preventDefault();event.stopImmediatePropagation();return await backToMove();}
  if(el.matches('[data-move-return]')){event.preventDefault();event.stopImmediatePropagation();const origin=MoveNav.origin;modal.close();clearMoveContext();await restoreMoveOrigin(origin);return;}
  if(el.matches('[data-map-cancel]')){event.preventDefault();event.stopImmediatePropagation();if(mapReturn)restoreMapScreen();else modal.close();return;}
  if(el.matches('[data-map-provider]')){event.preventDefault();event.stopImmediatePropagation();window.open(el.href,'_blank','noopener');if(mapReturn)restoreMapScreen();else modal.close();return;}
  if(el.matches('[data-action="close"]')&&MoveNav.id){
   if(mapReturn){event.preventDefault();event.stopImmediatePropagation();return restoreMapScreen();}
   if(!modal.dataset.moveRoot){event.preventDefault();event.stopImmediatePropagation();return await backToMove();}
   const origin=MoveNav.origin;clearMoveContext();restoreMovePageScroll(origin);return;
  }
  if(el.dataset.routeAction){
   event.preventDefault();event.stopImmediatePropagation();const editor=el.closest('[data-route-editor]'),items=readRouteEditor(editor,true),index=[...editor.querySelectorAll('.route-edit-row')].indexOf(el.closest('.route-edit-row'));
   const action=el.dataset.routeAction;
   if(action==='add'){if(items.length>=30)return toast('Use up to 30 locations.');items.splice(items.length>1&&['Delivery','Return'].includes(items.at(-1).kind)?items.length-1:items.length,0,{kind:'Stop',address:'',access:'',notes:''});}
   if(action==='remove')items.splice(index,1);
   if(action==='up'&&index>0)[items[index-1],items[index]]=[items[index],items[index-1]];
   if(action==='down'&&index<items.length-1)[items[index+1],items[index]]=[items[index],items[index+1]];
   editor.outerHTML=routeEditor(items);return;
  }
  let address=el.dataset.mapAddress;
  if(!address&&el.tagName==='A'){
   const url=new URL(el.href,location.href);
   if((/(^|\.)google\.com$/.test(url.hostname)&&url.pathname.startsWith('/maps'))||url.hostname==='maps.apple.com')address=url.searchParams.get('query')||url.searchParams.get('q')||url.searchParams.get('destination')||url.searchParams.get('daddr');
  }
  if(address){event.preventDefault();event.stopImmediatePropagation();chooseMoveMap(address);}
 }catch(error){modal.open?modalError(error):toast(error.message);}
},true);
modal.addEventListener('cancel',event=>{if(mapReturn){event.preventDefault();restoreMapScreen();}else if(MoveNav.id&&!modal.dataset.moveRoot){event.preventDefault();backToMove();}else clearMoveContext();});

const moveLoginScreen=loginScreen;
loginScreen=function(...args){clearMoveContext();MoveNav.scroll={};if(window.CrownPlanning)JP.drafts={};return moveLoginScreen(...args);};
