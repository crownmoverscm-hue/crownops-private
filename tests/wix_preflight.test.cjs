'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/wix_draft_sync.js'),'utf8');
const tick=()=>new Promise(setImmediate);
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
function harness({statusGate,preflightGate,preflightStatus=200}={}){
 const elements={},requests=[],timers=new Map();let n=0;
 const el=id=>elements[id]??={textContent:'',value:id==='basis'?'high':'WIX-TEST-READINESS-01',disabled:false,checked:false,hidden:false,style:{},append(){}};
 const snapshot={snapshot:{quote_id:'WIX-TEST-READINESS-01',basis:'high',full_legal_terms:'FICTIONAL TERMS'},snapshot_hash:'fixture-hash',review:null,writes_enabled:false};
 const fetch=async(url,options={})=>{
  requests.push({url,method:options.method||'GET'});
  const gate=url==='/api/wix-drafts/status'?statusGate:url.includes('/preflight/')?preflightGate:null;
  if(gate)await Promise.race([gate.promise,new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true}))]);
  const status=url.includes('/preflight/')?preflightStatus:200;
  const body=url==='/api/me'?{csrf:'disposable',user:{name:'TEST owner',role:'owner'}}:url==='/api/wix-drafts/status'?{can_review:true,quotes:[{id:'WIX-TEST-READINESS-01',client_name:'TEST fictional',revision:1}]}:url==='/api/app-release'?{revision:'fixture-build',version:'test'}:status===200?snapshot:{detail:'Protected quote excluded.'};
  return{ok:status===200,json:async()=>body};
 };
 const done=vm.runInNewContext(source,{AbortController,fetch,document:{getElementById:el,createElement:()=>({})},location:{reload(){throw Error('Unexpected reload');}},setTimeout:fn=>{timers.set(++n,fn);return n;},clearTimeout:id=>timers.delete(id)});
 return{el,done,requests,fireTimeouts(){for(const fn of [...timers.values()])fn();}};
}
test('read stays disabled in HTML and while access initialization is pending',async()=>{
 const html=fs.readFileSync(path.join(__dirname,'../app/static/wix_draft_sync.html'),'utf8');assert.match(html,/<button id="load" disabled>/);
 const gate=deferred(),h=harness({statusGate:gate});await tick();assert.equal(h.el('load').disabled,true);assert.equal(h.el('load').onclick,undefined);gate.resolve();await h.done;assert.equal(h.el('load').disabled,false);assert.equal(typeof h.el('load').onclick,'function');
});
test('successful snapshot read renders hash and terms with GET only',async()=>{const h=harness();await h.done;await h.el('load').onclick();assert.match(h.el('snapshot').textContent,/fixture-hash/);assert.match(h.el('snapshot').textContent,/FICTIONAL TERMS/);assert.equal(h.el('load').disabled,false);assert.match(h.el('loading').textContent,/No review/);assert(h.requests.every(r=>r.method==='GET'));});
test('a stalled preflight times out visibly, clears review and permits another read',async()=>{const gate=deferred(),h=harness({preflightGate:gate});await h.done;const read=h.el('load').onclick();await tick();assert.equal(h.el('load').disabled,true);h.fireTimeouts();await read;assert.match(h.el('error').textContent,/timed out/);assert.equal(h.el('load').disabled,false);assert.equal(h.el('review').style.display,'none');assert.equal(h.el('snapshot').textContent,'');assert(h.requests.every(r=>r.method==='GET'));});
test('initialization timeout explains failure and leaves read disabled',async()=>{const h=harness({statusGate:deferred()});await tick();h.fireTimeouts();await h.done;assert.equal(h.el('load').disabled,true);assert.match(h.el('error').textContent,/timed out/);assert.match(h.el('loading').textContent,/could not be loaded/);});
test('backend rejection is visible and does not leave a stale review',async()=>{const h=harness({preflightStatus:403});await h.done;await h.el('load').onclick();assert.match(h.el('error').textContent,/Protected quote excluded/);assert.equal(h.el('load').disabled,false);assert.equal(h.el('review').style.display,'none');});
test('changing selection discards a late snapshot response',async()=>{const gate=deferred(),h=harness({preflightGate:gate});await h.done;const read=h.el('load').onclick();h.el('quote').onchange();gate.resolve();await read;assert.match(h.el('snapshot').textContent,/Selection changed/);assert.equal(h.el('review').style.display,'none');});
test('a failed read cannot submit a local review and never retries automatically',async()=>{const h=harness({preflightStatus:403});await h.done;await h.el('load').onclick();await h.el('record').onclick();assert.equal(h.requests.filter(r=>r.url.includes('/preflight/')).length,1);assert(h.requests.every(r=>r.method==='GET'));});
