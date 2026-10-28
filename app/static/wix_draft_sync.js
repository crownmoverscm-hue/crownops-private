(async()=>{
'use strict';
const el=id=>document.getElementById(id);let csrf='',preflight=null;
async function call(url,data){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(data?{method:'POST',body:JSON.stringify(data)}:{})});const b=await r.json();if(!r.ok)throw Error(typeof b.detail==='string'?b.detail:'Request blocked');return b;}
function fail(e){el('error').textContent=e.message;}
try{
 const me=await call('/api/me');csrf=me.csrf;el('identity').textContent=`Signed in: ${me.user.name} · ${me.user.role}. Owner identity comes from the server session.`;
 const state=await call('/api/wix-drafts/status');el('access-panel').hidden=!state.can_review;el('fixture').onclick=async()=>{el('fixture').disabled=true;try{await call('/api/wix-drafts/fixture',{confirm:true});location.reload();}catch(e){fail(e);el('fixture').disabled=false;}};el('access').onclick=async()=>{el('access').disabled=true;try{const result=await call('/api/wix-drafts/test-access',{confirm:true});el('access-result').textContent=JSON.stringify(result,null,2);}catch(e){fail(e);el('access').disabled=false;}};el('status').textContent=JSON.stringify(state,null,2);
 for(const q of state.quotes){const option=document.createElement('option');option.value=q.id;option.textContent=`${q.client_name} · ${q.id} · r${q.revision}`;el('quote').append(option);}
 if(!state.quotes.length){el('load').disabled=true;el('error').textContent='No eligible TEST quote is available. Use the existing quote editor to save a fictional draft; do not alter Estimate 185.';}
 el('load').onclick=async()=>{preflight=null;el('review').style.display='none';el('error').textContent='';el('confirm').checked=false;try{const q=encodeURIComponent(el('quote').value);preflight=await call(`/api/wix-drafts/preflight/${q}?basis=${el('basis').value}`);el('snapshot').textContent=JSON.stringify(preflight,null,2);el('review').style.display=state.can_review?'block':'none';}catch(e){fail(e);}};
 for(const id of ['quote','basis'])el(id).onchange=()=>{preflight=null;el('review').style.display='none';el('snapshot').textContent='Selection changed. Read the current preflight again.';};
 el('record').onclick=async()=>{if(!preflight)return;el('record').disabled=true;try{const q=encodeURIComponent(preflight.snapshot.quote_id);const result=await call(`/api/wix-drafts/review/${q}`,{basis:preflight.snapshot.basis,snapshot_hash:preflight.snapshot_hash,reason:el('reason').value,confirm:el('confirm').checked});el('error').textContent=JSON.stringify(result); }catch(e){fail(e);}finally{el('record').disabled=false;}};
 const release=await call('/api/app-release');el('release').textContent=`CrownOps ${release.version} · ${release.revision}`;
}catch(e){fail(e);el('identity').textContent='Use your authorized staging account to sign in through CrownOps. No separate login or credentials are embedded here.';el('load').disabled=true;}
})();
