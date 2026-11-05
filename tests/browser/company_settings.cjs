'use strict';
// COM-8 owner setup on a NEW (unconfigured) company installation. Fictional data only.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),zlib=require('node:zlib');
function png(){ // 8x8 solid PNG built in memory
 const crc=(b)=>{let c,crcTable=[];for(let n=0;n<256;n++){c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;crcTable[n]=c>>>0;}let x=0xffffffff;for(const v of b)x=crcTable[(x^v)&255]^(x>>>8);return (x^0xffffffff)>>>0;};
 const chunk=(t,d)=>{const l=Buffer.alloc(4);l.writeUInt32BE(d.length);const td=Buffer.concat([Buffer.from(t),d]);const c=Buffer.alloc(4);c.writeUInt32BE(crc(td));return Buffer.concat([l,td,c]);};
 const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(8,0);ihdr.writeUInt32BE(8,4);ihdr[8]=8;ihdr[9]=2;
 const raw=Buffer.alloc(8*(1+8*3));for(let y=0;y<8;y++){raw[y*25]=0;for(let x=0;x<8;x++){raw[y*25+1+x*3]=30;raw[y*25+2+x*3]=90;raw[y*25+3+x*3]=160;}}
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',zlib.deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const base='http://127.0.0.1:8095',errors=[];
 async function login(role,width){const ctx=await browser.newContext({viewport:{width,height:900}});await ctx.addCookies([{name:'crownops_session',value:role+'-token',url:base}]);
  const p=await ctx.newPage();p.on('pageerror',e=>errors.push(role+': '+e.message));await p.goto(base);await p.locator('#content h1').waitFor();
  await p.evaluate(()=>navigate('company'));await p.getByRole('heading',{name:'Company profile',exact:true}).waitFor();return p;}
 async function noOverflow(p,label){for(const width of [390,1440]){await p.setViewportSize({width,height:900});const wide=await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert.equal(wide,false,label+' overflows at '+width);}}
 async function pills(p){return p.locator('table tbody tr').evaluateAll(rows=>rows.map(r=>r.innerText.replace(/\s+/g,' ').trim()));}
 async function row(p,label,state){await p.locator('table tbody tr').filter({hasText:label}).filter({hasText:state}).first().waitFor({timeout:15000});}
 const owner=await login('owner',1440);
 let status=await pills(owner);assert.deepEqual(status.filter(s=>/Incomplete/.test(s)).length,3,'identity, pricing and billing start incomplete: '+status);
 assert.equal(await owner.getByLabel('Company name',{exact:true}).inputValue(),'','no Crown defaults are prefilled');
 assert.equal(await owner.getByLabel('Local fuel per truck ($)',{exact:true}).inputValue(),'','no default prices are prefilled');
 await owner.getByLabel('Company name',{exact:true}).fill('Fictional Prairie Movers');
 await owner.getByLabel('Client phone',{exact:true}).fill('306-555-0155');
 await owner.getByLabel('Client email',{exact:true}).fill('office@prairie.example.invalid');
 await owner.getByLabel('Business time zone',{exact:true}).fill('America/Regina');
 await owner.getByLabel('Brand colour (#RRGGBB, optional)',{exact:true}).fill('#2a6f4b');
 await owner.getByRole('button',{name:'Save company profile',exact:true}).click();
 await owner.getByText('Company profile saved.').waitFor();await owner.getByRole('heading',{name:'Company profile',exact:true}).waitFor();
 await owner.getByLabel('Logo file (PNG, JPEG or WebP, under 2 MB)',{exact:true}).setInputFiles({name:'logo.png',mimeType:'image/png',buffer:png()});
 await owner.getByRole('button',{name:'Upload logo',exact:true}).click();await owner.locator('#company-logo-preview').waitFor();
 const values={'Local fuel per truck ($)':'45','Extra truck fee ($)':'30','Long-distance rate per km ($)':'1.05','Junk removal extra labour per 30 min ($)':'65','Default deposit ($)':'250',
  'Default minimum service hours':'2','Moving and packing minimum hours':'2','Small afternoon job minimum hours':'1.5','Long-distance distance buffer (e.g. 1.05 = +5%)':'1.05',
  'Long-distance transport multiplier':'1.1','Truck minimums (crew size:trucks, e.g. 6:3, 4:2)':'6:3, 5:2','Limited liability wording':'Released value per signed terms'};
 for(const [label,v] of Object.entries(values))await owner.getByLabel(label,{exact:true}).fill(v);
 for(const [k,l] of [['Single item','80'],['Quarter load','200'],['Half load','380'],['Three-quarter load','560'],['Full load','740']]){await owner.getByLabel(k+' name',{exact:true}).fill(k);await owner.getByLabel(k+' price ($)',{exact:true}).fill(l);}
 await owner.getByRole('button',{name:'Save pricing',exact:true}).click();await owner.getByText('Pricing draft saved.').waitFor();
 await row(owner,'Pricing policy','Pending approval');
 await owner.getByRole('checkbox',{name:'I have reviewed these prices and approve them for estimates'}).check();
 await owner.getByRole('button',{name:'Save pricing',exact:true}).click();await owner.getByText('Pricing approved.').waitFor();
 for(const [label,state] of [['Company identity and contact','Approved'],['Logo (optional)','Approved'],['Pricing policy','Approved'],['Hourly crew rates','Approved'],['Tax and invoice settings','Incomplete']])await row(owner,label,state);
 await noOverflow(owner,'owner settings');
 const portal=await owner.context().newPage();await portal.goto(base+'/portal');const html=await portal.content();
 assert.ok(!/Crown Movers|514-606-4030|\bCrown\b(?!Ops)/.test(html),'portal page shows no Crown identity');
 const sales=await login('sales',390);
 assert.equal(await sales.getByLabel('Company name',{exact:true}).isDisabled(),true,'sales sees read-only settings');
 assert.equal(await sales.getByRole('button',{name:'Save pricing',exact:true}).count(),0);
 await sales.getByText('Only an Owner or Manager can change these settings.').waitFor();
 await noOverflow(sales,'sales settings');
 assert.deepEqual(errors,[]);
 console.log('PASS: new-company owner setup (profile, logo, pricing draft then approval, status checklist), read-only sales view, portal free of Crown identity, 390/1440 widths; no page errors.');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
