/* Local calendar dates and truck ordering shared by all schedule views. */
'use strict';
var CrownSchedule=(function(){
  const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
  function date(value){
    const d=value instanceof Date?new Date(value):new Date(String(value)+'T12:00:00');
    if(Number.isNaN(d.getTime()))throw Error('Choose a valid schedule date.');
    d.setHours(12,0,0,0);return d;
  }
  function monday(value){const d=date(value);d.setDate(d.getDate()-((d.getDay()+6)%7));return d;}
  function dates(view,value){
    const anchor=date(value);let first=date(anchor),last=date(anchor);
    if(view==='week'){first=monday(anchor);last=date(first);last.setDate(last.getDate()+6);}
    if(view==='month'){
      first=monday(new Date(anchor.getFullYear(),anchor.getMonth(),1,12));
      last=monday(new Date(anchor.getFullYear(),anchor.getMonth()+1,0,12));last.setDate(last.getDate()+6);
    }
    const result=[];for(let d=date(first);d<=last;d.setDate(d.getDate()+1))result.push(date(d));
    return result;
  }
  function shift(value,view,direction){
    const d=date(value),step=direction<0?-1:1;
    if(view==='month'){
      const day=d.getDate();d.setDate(1);d.setMonth(d.getMonth()+step);
      d.setDate(Math.min(day,new Date(d.getFullYear(),d.getMonth()+1,0,12).getDate()));
    }else d.setDate(d.getDate()+step*(view==='day'?1:7));
    return iso(d);
  }
  function compareTrucks(a,b){
    return String(a?.name||'').trim().localeCompare(String(b?.name||'').trim(),'en-CA',{numeric:true,sensitivity:'base'})||
      String(a?.id||'').localeCompare(String(b?.id||''));
  }
  return {iso,date,monday,dates,shift,compareTrucks};
})();
