'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {suggest}=require('../app/static/local_estimator.js');
const lead=(extra={},intake={})=>({lead_type:'Moving',home_size:'1 bedroom apartment',boxes:'10-20',pickup_access:'Ground floor / direct access',dropoff_access:'Ground floor / direct access',pickup_walk:'0-10 ft',dropoff_walk:'0-10 ft',...extra,intake:{local_area:'Yes',between_stops_minutes:'20',...intake}});
test('unknown travel never becomes an invented driving allowance',()=>{
 const s=suggest(lead({}, {between_stops_minutes:''}));assert.equal(s.available,true);assert.equal(s.can_apply,false);assert.equal(s.transport_hours,null);assert(s.warnings.some(x=>x.includes('labour only')));
});
test('actual between-stop driving adds service time without adding movers',()=>{
 const a=suggest(lead({}, {between_stops_minutes:'0'})),b=suggest(lead({}, {between_stops_minutes:'60'}));assert.equal(b.movers,a.movers);assert.equal(b.hours_low-a.hours_low,1);assert.equal(b.hours_high-a.hours_high,1);
});
test('walking ranges remain distinct, including 30–50 versus 50–100 feet',()=>{
 const a=suggest(lead({pickup_walk:'30-50 ft'})),b=suggest(lead({pickup_walk:'50-100 ft'}));assert(a.can_apply);assert(b.hours_high>a.hours_high);assert(!a.warnings.some(x=>x.includes('50–100')));
});
test('large crews preserve Crown truck minimums and room-size crew caps',()=>{
 for(const [home,max] of [['Studio apartment',3],['1 bedroom apartment',3],['2 bedroom home',4],['3 bedroom home',5],['More than 4 bedrooms / full house',6]]){
  const s=suggest(lead({home_size:home,boxes:'100-120',pickup_access:'3 flights of stairs',dropoff_access:'3 flights of stairs'}));assert(s.available);assert(s.movers<=max);if(s.movers>=6)assert(s.trucks>=3);else if(s.movers>=4)assert(s.trucks>=2);
 }
});
test('small afternoon furniture jobs use 1.5h while morning uses 3h',()=>{
 const m=lead({home_size:'Furniture delivery / micro-move',boxes:'No boxes'});assert.equal(suggest(m).minimum_hours,3);m.intake.start_window='afternoon';const s=suggest(m);assert.equal(s.minimum_hours,1.5);assert(s.small_afternoon_job);
});
test('nonlocal, missing and unsupported custom access need manual review',()=>{
 assert.equal(suggest(lead({}, {local_area:'No'})).available,false);assert.equal(suggest(lead({boxes:''})).available,false);assert.equal(suggest(lead({pickup_access:'Unmeasured loading access'})).available,false);
});
test('special handling adds guidance without importing specialty prices or dated rates',()=>{
 const s=suggest(lead({}, {heavy_items:['Large treadmill']}));assert(s.warnings.some(x=>/specialty charges/.test(x)));for(const k of ['fee','hourly_rate','fuel','low_cents','high_cents'])assert.equal(k in s,false);
});
test('separate base travel uses the supplied 45 minute thresholds',()=>{
 assert.equal(suggest(lead({}, {pickup_base_minutes:'45',return_base_minutes:'45'})).transport_hours,1);assert.equal(suggest(lead({}, {pickup_base_minutes:'46',return_base_minutes:'45'})).transport_hours,1.25);assert.equal(suggest(lead({}, {pickup_base_minutes:'46',return_base_minutes:'46'})).transport_hours,1.5);
});
