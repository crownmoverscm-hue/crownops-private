'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/app.js','utf8');
const helpers=source.slice(source.indexOf('function mondayOfWeek('),source.indexOf('function dateIso('));
const context=vm.createContext({S:{week:null},today:()=> '2026-10-18'});
vm.runInContext(helpers,context);
const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
test('schedule weeks contain Monday through Sunday across boundaries',()=>{
 for(const [day,monday,sunday] of [['2026-10-18','2026-10-12','2026-10-18'],['2026-10-19','2026-10-19','2026-10-25'],['2027-01-01','2026-12-28','2027-01-03'],['2027-01-04','2027-01-04','2027-01-10'],['2026-11-01','2026-10-26','2026-11-01']]){
  context.S.week=new Date(day+'T12:00:00');const days=vm.runInContext('weekDays()',context);
  assert.equal(iso(days[0]),monday);assert.equal(iso(days[6]),sunday);assert.equal(days[0].getDay(),1);assert.equal(days[6].getDay(),0);
 }
});
test('Today and restored Sunday state normalize to the containing Monday',()=>{
 context.S.week=null;assert.equal(iso(vm.runInContext('weekDays()[0]',context)),'2026-10-12');
 context.S.week=new Date('2026-10-18T12:00:00');assert.equal(iso(vm.runInContext('weekDays()[0]',context)),'2026-10-12');
 vm.runInContext('S.week.setDate(S.week.getDate()+7)',context);assert.equal(iso(vm.runInContext('weekDays()[0]',context)),'2026-10-19');
});
