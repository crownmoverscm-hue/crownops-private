'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ctx=vm.createContext({});
vm.runInContext(fs.readFileSync('app/static/schedule_calendar.js','utf8'),ctx);
const c=ctx.CrownSchedule;
test('day and week ranges keep Sunday in its Monday-Sunday week',()=>{
 assert.equal(c.dates('day','2026-11-15').length,1);
 const ds=c.dates('week','2026-11-15');
 assert.equal(c.iso(ds[0]),'2026-11-09');assert.equal(c.iso(ds[6]),'2026-11-15');
});
test('month ranges include complete Monday-Sunday rows across years and leap years',()=>{
 for(const [anchor,start,end,length] of [['2026-11-15','2026-10-26','2026-12-06',42],['2027-02-14','2027-02-01','2027-02-28',28],['2028-02-29','2028-01-31','2028-03-05',35],['2027-01-01','2026-12-28','2027-01-31',35]]){
  const ds=c.dates('month',anchor);assert.equal(c.iso(ds[0]),start);assert.equal(c.iso(ds.at(-1)),end);assert.equal(ds.length,length);
  assert.equal(ds[0].getDay(),1);assert.equal(ds.at(-1).getDay(),0);
 }
});
test('navigation shifts the selected period and clamps short months',()=>{
 assert.equal(c.shift('2026-12-31','day',1),'2027-01-01');
 assert.equal(c.shift('2026-12-31','week',1),'2027-01-07');
 assert.equal(c.shift('2027-01-31','month',1),'2027-02-28');
 assert.equal(c.shift('2028-01-31','month',1),'2028-02-29');
 assert.equal(c.shift('2026-11-01','day',-1),'2026-10-31');
});
test('date ranges remain consecutive through Montreal daylight saving boundaries',()=>{
 const ds=c.dates('month','2026-11-01');const dates=Array.from(ds,c.iso);
 assert.equal(new Set(dates).size,dates.length);
 assert(dates.includes('2026-10-31'));assert(dates.includes('2026-11-01'));assert(dates.includes('2026-11-02'));
});
test('truck names sort alphabetically without case, with natural number order',()=>{
 const trucks=[{id:'z',name:'Zulu'},{id:'10',name:'Truck 10'},{id:'a',name:'alpha'},{id:'2',name:'Truck 2'},{id:'b',name:'Bravo'}];
 assert.deepEqual(trucks.sort(c.compareTrucks).map(t=>t.name),['alpha','Bravo','Truck 2','Truck 10','Zulu']);
});
