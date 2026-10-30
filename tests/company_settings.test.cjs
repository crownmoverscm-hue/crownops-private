'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../app/static/app.js','utf8');
const code=source.slice(source.indexOf('function seasonalRatesIssue('),source.indexOf('function bindPage('));
const row={'Season / Date Range':'Fictional full year','2 movers':100,'3 movers':150,'4 movers':200,'5 movers':250,'6 movers':300};
function harness(rates){
 const calls=[],context={S:{},may:()=>true,api:async path=>{calls.push(path);return path==='/pricing'?[{id:'TEST-PRICE',label:'Fictional only',approved:false,rates}]:[];},h:v=>String(v).replaceAll('<','&lt;').replaceAll('>','&gt;'),money:c=>'$'+(c/100).toFixed(2),head:(a,b)=>a+b,btn:(a,b)=>`<button data-action="${a}">${b}</button>`,icon:()=>'',pill:String,empty:(a,b)=>a+b,initials:()=>''};
 vm.createContext(context);vm.runInContext(code,context);return{context,calls};
}
for(const value of [{},null,'[]',42,[null],[{}],[{...row,'2 movers':'100'}],[{...row,'3 movers':Infinity}]])test('malformed rates remain visible without crashing technical settings: '+JSON.stringify(value),async()=>{
 const h=harness(value),html=await h.context.settingsPage();assert.match(html,/Settings & imports/);assert.match(html,/Team access/);assert.match(html,/Fleet verification/);assert.match(html,/Rate snapshot needs review/);assert.doesNotMatch(html,/data-action="approve-pricing"/);assert.deepEqual(h.calls,['/imports','/pricing','/vehicles','/users']);assert.equal(h.context.S.settingsData.prices[0].rates,value);
});
test('empty rate list is explicitly unconfigured and cannot be approved',async()=>{const h=harness([]),html=await h.context.settingsPage();assert.match(html,/No seasonal rates configured/);assert.doesNotMatch(html,/data-action="approve-pricing"/);});
test('valid rate table keeps values and review control',async()=>{const h=harness([row]),html=await h.context.settingsPage();assert.match(html,/Fictional full year/);assert.match(html,/\$100.00/);assert.match(html,/\$300.00/);assert.match(html,/data-action="approve-pricing"/);assert.doesNotMatch(html,/Rate snapshot needs review/);});
test('invalid rate labels are escaped',async()=>{const h=harness({});h.context.S={};const html=h.context.seasonalRatesTable({id:'<script>bad</script>',rates:{}});assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);});
