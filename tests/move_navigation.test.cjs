'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function screen(){
  const state={plan:null,navigations:[],calendar:{scrollLeft:120},scroll:[],listeners:{}};
  const context={
    console,Date,Map,URL,Intl,FormData:class{constructor(form){return Object.entries(form.fields)[Symbol.iterator]();}},
    S:{page:'schedule',week:new Date('2026-11-15T12:00:00'),jobScope:'mine'},
    JP:undefined,scrollX:0,scrollY:260,window:{},
    h:String,requestAnimationFrame:fn=>fn(),scrollTo:(...v)=>state.scroll.push(v),
    modal:{dataset:{},scrollTop:0,querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{},close:()=>{}},
    document:{getElementById:id=>id==='job-plan-form'?state.plan:null,
      querySelector:selector=>selector==='.weekly-dispatch-scroll'?state.calendar:null,
      querySelectorAll:()=>[],addEventListener:(type,fn)=>{if(type==='click'&&!state.planningClick)state.planningClick=fn;state.listeners[type]=fn;}},
    showModal:()=>{state.plan=null;},
    navigate:async page=>{state.navigations.push(page);context.S.page=page;},
    openJob:async()=>{context.showModal('Move');},loginScreen:()=>{},
    readRouteEditor:form=>form.route
  };
  vm.createContext(context);
  for(const file of ['job_planning.js','move_navigation.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static',file),'utf8'),context);
  return {context,state,run:code=>vm.runInContext(code,context)};
}

test('returning from a related page restores a cloned week, personal scope and scroll',async()=>{
  const {context:c,state:s,run}=screen();
  await c.openJob('J1');
  c.S.week.setDate(c.S.week.getDate()+7);c.S.jobScope='all';
  c.S.page='fleet';s.calendar.scrollLeft=0;
  await c.backToMove();
  assert.equal(c.S.week.getDate(),15);assert.equal(c.S.jobScope,'mine');
  assert.deepEqual(s.navigations,['schedule']);assert.equal(s.calendar.scrollLeft,120);
  assert.deepEqual(s.scroll.at(-1),[0,260]);
  c.S.week.setDate(22);
  assert.equal(run('MoveNav.origin.week.getDate()'),15);
});

test('returning on the schedule itself restores a changed week or scope',async()=>{
  const {context:c,state:s}=screen();await c.openJob('J1');
  c.S.week=new Date('2026-12-06T12:00:00');c.S.jobScope='all';
  await c.backToMove();assert.equal(c.S.week.getDate(),15);
  assert.equal(c.S.jobScope,'mine');assert.deepEqual(s.navigations,['schedule']);
});

test('Back to schedule uses the same origin restoration',async()=>{
  const {context:c,state:s,run}=screen();await c.openJob('J1');
  c.S.week.setDate(22);c.S.jobScope='all';
  const button={matches:selector=>selector==='[data-move-return]'};
  await s.listeners.click({target:{closest:()=>button},preventDefault(){},stopImmediatePropagation(){}});
  assert.equal(c.S.week.getDate(),15);assert.equal(c.S.jobScope,'mine');
  assert.equal(run('MoveNav.id'),null);assert.deepEqual(s.navigations,['schedule']);
});

for(const via of ['editor button','dialog back'])test(`leaving the assignment editor via ${via} remembers unsaved crew, trucks, fields and route`,async()=>{
  const {context:c,state:s,run}=screen();
  await c.openJob('J1');
  run("JP.data={job:{id:'J1',version:3}}");
  s.plan={fields:{job_id:'J1',title:'Unsaved title',start_at:'2026-11-15T09:00'},
    route:[{kind:'Pickup',address:'Unsaved address',access:'Side door',notes:'Call first'}],
    querySelectorAll:selector=>selector==='.route-edit-row'?[{querySelectorAll:()=>Object.entries(s.plan.route[0]).map(([key,value])=>({dataset:{routeField:key},value}))}]:selector.includes('crew_ids')?[{value:'E1'},{value:'E2'}]:[{value:'V1'}]};
  if(via==='dialog back')await c.backToMove();
  else await s.planningClick({target:{closest:()=>({dataset:{plan:'back',id:'J1'}})},preventDefault(){},stopImmediatePropagation(){}});
  const draft=JSON.parse(run('JSON.stringify(JP.drafts.J1)'));
  assert.equal(draft.version,3);assert.deepEqual(draft.crew,['E1','E2']);
  assert.deepEqual(draft.trucks,['V1']);assert.equal(draft.fields.title,'Unsaved title');
  assert.equal(draft.route_stops[0].address,'Unsaved address');
});
