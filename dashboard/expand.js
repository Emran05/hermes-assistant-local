// expand.js — rich pop-out renderers (loaded after the main inline script;
// assignments here override the inline EXPAND_RENDER entries).

// ===== markets =====
EXPAND_RENDER.markets = function(el, d){
  if(d && d.error){el.innerHTML='<div class="hint">'+esc(d.error)+'</div>';return;}
  var idx = d.indices||[], wl = d.watchlist||[];
  var live = (d.state==='REGULAR' || d.state==='PRE' || d.state==='POST');
  var all = idx.concat(wl).filter(function(q){return !q.error;});
  var up = all.filter(function(q){return (q.pct||0)>=0;}).length, dn = all.length-up;
  var fresh='';
  if(d.asof){var dt=new Date(d.asof*1000);
    fresh = (d.state==='REGULAR') ? 'Live · market open'
          : (d.state==='PRE') ? 'Pre-market'
          : (d.state==='POST') ? 'After hours'
          : 'At close · '+dt.toLocaleDateString([], {month:'short', day:'numeric'});}

  function label(t){return '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:0 0 8px">'+esc(t)+'</div>';}
  function px(v){return v!=null ? v.toLocaleString(undefined,{maximumFractionDigits:2}) : '—';}
  function chip(q){var u=(q.pct||0)>=0;return '<span class="num delta '+(u?'up':'down')+'" style="font-weight:660;font-size:12px">'+(u?'+':'')+(q.pct!=null?q.pct:0)+'%</span>';}
  function spark(q,h){return miniSpark(q.spark,(q.pct||0)>=0).replace('class="qspark"','class="qspark" style="width:100%;height:'+h+'px;display:block"');}
  function rangeBar(lbl,lo,val,hi,u){
    if(lo==null||hi==null||val==null||!(hi>lo)) return '';
    var pct=Math.max(2,Math.min(98,(val-lo)/(hi-lo)*100));
    var mc=u?'var(--ok)':'var(--bad)';
    return '<div style="display:flex;align-items:center;gap:7px;font-size:10px;margin-top:6px" class="num">'+
      '<span style="width:26px;color:var(--muted);letter-spacing:.05em;font-weight:600">'+lbl+'</span>'+
      '<span style="width:56px;text-align:right;color:var(--faint)">'+fmtNum(lo)+'</span>'+
      '<div style="position:relative;flex:1;height:5px;border-radius:3px;background:var(--hairline)">'+
        '<i style="position:absolute;left:0;top:0;height:100%;width:'+pct+'%;border-radius:3px;background:linear-gradient(90deg,color-mix(in srgb,var(--iris) 35%,transparent),var(--iris))"></i>'+
        '<i style="position:absolute;left:'+pct+'%;top:50%;width:8px;height:8px;border-radius:50%;background:'+mc+';transform:translate(-50%,-50%);box-shadow:0 0 0 2px var(--glass-2)"></i>'+
      '</div>'+
      '<span style="width:56px;color:var(--faint)">'+fmtNum(hi)+'</span></div>';
  }

  var h='';
  h+='<div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:2px 0 12px">'+
     '<div style="font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:6px">'+
       (live?'<span class="livedot"></span>':'')+esc(fresh)+'</div>'+
     '<div class="w-sub" style="font-size:11px"><span class="delta up">'+up+' up</span> · <span class="delta down">'+dn+' down</span></div></div>';

  var tileCss='background:var(--glass-2);border:1px solid var(--hairline);border-radius:12px;padding:10px 11px';
  h+=label('Indices');
  h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">';
  h+=idx.map(function(q){
    if(q.error) return '<div style="'+tileCss+'"><div style="font-weight:660">'+esc(q.symbol)+'</div><div class="w-sub" style="color:var(--faint)">unavailable</div></div>';
    return '<div style="'+tileCss+'">'+
      '<div style="display:flex;align-items:baseline;gap:6px">'+
        '<span style="font-weight:720;font-size:12.5px">'+esc(q.symbol)+'</span>'+
        '<span class="w-sub" style="font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(q.friendly||q.name)+'</span></div>'+
      '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:6px;margin:4px 0 2px">'+
        '<span class="num" style="font-size:19px;font-weight:700;line-height:1">'+px(q.price)+'</span>'+chip(q)+'</div>'+
      spark(q,26)+'</div>';
  }).join('');
  h+='</div>';

  h+=label('Watchlist');
  h+=wl.map(function(q){
    if(q.error) return '<div class="quoterow"><span class="sym">'+esc(q.symbol)+'</span><span style="color:var(--faint)">unavailable</span></div>';
    var u=(q.pct||0)>=0;
    return '<div style="padding:11px 0;border-bottom:1px solid var(--hairline)">'+
      '<div style="display:flex;align-items:baseline;gap:8px">'+
        '<span style="font-weight:720;font-size:14px;min-width:54px">'+esc(q.symbol)+'</span>'+
        '<span class="w-sub" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(q.name)+'</span>'+
        '<span class="num" style="font-weight:680;font-size:14.5px">'+px(q.price)+'</span>'+
        '<span class="num delta '+(u?'up':'down')+'" style="width:66px;text-align:right;font-weight:640">'+(u?'+':'')+(q.pct!=null?q.pct:0)+'%</span></div>'+
      '<div style="margin:5px 0 1px">'+spark(q,28)+'</div>'+
      rangeBar('DAY',q.day_lo,q.price,q.day_hi,u)+
      rangeBar('52W',q.wk_lo,q.price,q.wk_hi,u)+
      '<div style="display:flex;gap:12px;margin-top:7px;font-size:10px;color:var(--muted)" class="num">'+
        '<span>Chg <b style="color:'+(u?'var(--ok)':'var(--bad)')+'">'+(u?'+':'')+fmtNum(q.chg)+'</b></span>'+
        '<span>Vol '+kfmt(q.vol)+'</span>'+
        (q.exch?'<span style="color:var(--faint)">'+esc(q.exch)+'</span>':'')+
        '<span style="margin-left:auto;color:var(--faint)">Prev close '+px(q.prev)+'</span></div>'+
    '</div>';
  }).join('') || '<div class="hint">No symbols.</div>';

  el.innerHTML=h;
};

// ===== battery =====
EXPAND_RENDER.battery=function(el,data){
  const d=data||{};
  if(d.available===false){el.innerHTML='<div class="hint">'+esc(d.reason||'Battery info unavailable.')+'</div>';return;}
  const pct=(d.pct!=null)?d.pct:0;
  const chg=!!d.charging, ac=!!d.ac;
  const cvar=chg?'--quick':(pct<=15?'--bad':pct<=35?'--warn':'--ok');
  const col='var('+cvar+')';
  // inline battery glyph, fill scales with charge
  const fw=Math.max(3,Math.round(94*pct/100));
  const bolt=chg?'<path d="M63 15 L52 30 L60 30 L57 41 L70 25 L62 25 Z" fill="#fff" stroke="'+col+'" stroke-width="1.2" stroke-linejoin="round"/>':'';
  const glyph='<svg width="122" height="54" viewBox="0 0 122 54" style="flex:0 0 auto">'+
    '<rect x="1.5" y="8" width="104" height="38" rx="9" fill="none" stroke="var(--hairline)" stroke-width="3"/>'+
    '<rect x="109" y="19" width="8" height="16" rx="3.5" fill="var(--hairline)"/>'+
    '<rect x="6" y="12.5" width="'+fw+'" height="29" rx="5" fill="'+col+'" opacity="'+(chg?'.55':'.9')+'"/>'+bolt+'</svg>';
  const bits=[esc(d.state||'')];
  if(d.time_label)bits.push('<b class="num">'+esc(d.time_label)+'</b>');
  else if(ac&&!chg&&pct>=95)bits.push('battery is holding at full');
  let h='<div style="display:flex;align-items:center;gap:16px;margin:4px 0 6px">'+glyph+
    '<div><div style="display:flex;align-items:baseline;gap:4px"><span class="num" style="font-size:52px;font-weight:720;line-height:1;color:'+col+'">'+pct+'</span>'+
    '<span style="font-size:22px;font-weight:600;color:var(--muted)">%</span></div>'+
    '<div class="w-sub" style="margin-top:3px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">'+bits.join('<span style="color:var(--faint)">&middot;</span>')+'</div></div></div>';
  const chips=[];
  if(d.low_power_mode)chips.push(['--warn','Low Power Mode']);
  if(d.warn)chips.push(['--bad','Low battery']);
  if(chips.length)h+='<div style="display:flex;gap:6px;margin:0 0 4px">'+chips.map(c=>
    '<span style="font-size:10.5px;font-weight:650;letter-spacing:.02em;padding:3px 9px;border-radius:20px;color:var('+c[0]+');background:color-mix(in srgb,var('+c[0]+') 15%,transparent)">'+esc(c[1])+'</span>').join('')+'</div>';
  const hh=d.health||{};
  h+=statGrid([
    ['Charge',pct+'%'],
    ['Capacity',(hh.max_capacity_pct!=null?hh.max_capacity_pct+'%':'—')],
    ['Cycles',(hh.cycles!=null?fmtNum(hh.cycles):'—')],
  ]);
  // battery health
  h+='<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:12px 0 6px">Battery health</div>';
  const cond=hh.condition||'—';
  const condOk=/normal|good/i.test(cond);
  h+='<div style="display:flex;align-items:center;justify-content:space-between;padding:2px 0 8px;border-bottom:1px solid var(--hairline)">'+
     '<span class="w-sub">Condition</span>'+
     '<span style="font-weight:640;font-size:13px;color:'+(condOk?'var(--ok)':'var(--warn)')+'">'+esc(cond)+'</span></div>';
  if(hh.max_capacity_pct!=null){
    const capc=hh.max_capacity_pct>=80?'--ok':(hh.max_capacity_pct>=60?'--warn':'--bad');
    h+='<div style="padding:9px 0 4px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px">'+
       '<span class="w-sub">Maximum capacity</span><span class="num" style="font-weight:640;color:var('+capc+')">'+hh.max_capacity_pct+'%</span></div>'+
       '<div style="height:7px;border-radius:4px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+hh.max_capacity_pct+'%;background:var('+capc+')"></i></div>'+
       '<div class="hint" style="margin-top:4px">of original design capacity</div></div>';
  }
  if(hh.cycles!=null){
    const rc=hh.rated_cycles||1000, cp=Math.min(100,Math.round(hh.cycles/rc*100));
    h+='<div style="padding:9px 0 4px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px">'+
       '<span class="w-sub">Cycle count</span><span class="num" style="font-weight:640">'+fmtNum(hh.cycles)+' <span style="color:var(--faint)">/ '+fmtNum(rc)+'</span></span></div>'+
       '<div style="height:7px;border-radius:4px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+Math.max(1.5,cp)+'%;background:linear-gradient(90deg,var(--iris),var(--quick))"></i></div>'+
       '<div class="hint" style="margin-top:4px">'+(rc-hh.cycles>0?fmtNum(rc-hh.cycles)+' cycles until Apple’s rated limit':'past rated cycle limit')+'</div></div>';
  }
  // power adapter
  const ad=d.adapter||{};
  if(ad.connected&&(ad.name||ad.watts)){
    h+='<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:14px 0 6px">Power adapter</div>'+
       '<div style="display:flex;align-items:center;gap:11px;background:var(--glass-2);border:1px solid var(--hairline);border-radius:11px;padding:11px 13px">'+
       '<span style="color:var(--quick);display:flex">'+icon('battery')+'</span>'+
       '<div style="flex:1;min-width:0"><div style="font-weight:620;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(ad.name||'USB-C adapter')+'</div>'+
       '<div class="hint" style="margin-top:1px">'+esc(ad.manufacturer||'')+(chg?' &middot; delivering charge':(ac?' &middot; connected':''))+'</div></div>'+
       (ad.watts!=null?'<div style="text-align:right"><div class="num" style="font-size:20px;font-weight:700;color:var(--quick)">'+ad.watts+'</div><div class="hint" style="margin-top:-2px">watts</div></div>':'')+'</div>';
  }
  // connected devices
  const devs=d.devices||[];
  if(devs.length){
    const ord=[['left','L'],['right','R'],['case','Case'],['main','Batt'],['single','']];
    const pill=(lab,v)=>{const pc=v<=15?'--bad':v<=35?'--warn':'--ok';
      return '<div style="display:flex;align-items:center;gap:7px;flex:1;min-width:96px">'+
        (lab?'<span style="width:30px;font-size:10.5px;color:var(--muted);font-weight:600">'+esc(lab)+'</span>':'')+
        '<div style="flex:1;height:6px;border-radius:3px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+Math.max(3,v)+'%;background:var('+pc+')"></i></div>'+
        '<span class="num" style="width:34px;text-align:right;font-size:11.5px;font-weight:600">'+v+'%</span></div>';};
    h+='<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:14px 0 6px">Connected devices</div>';
    h+=devs.map(dv=>{const lv=dv.levels||{};
      const rows=ord.filter(o=>lv[o[0]]!=null).map(o=>pill(o[0]==='single'?'':o[1],lv[o[0]]));
      return '<div style="padding:9px 0;border-bottom:1px solid var(--hairline)">'+
        '<div style="display:flex;align-items:baseline;gap:7px;margin-bottom:6px">'+
        '<span style="font-weight:620;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(dv.name)+'</span>'+
        (dv.type?'<span class="hint">'+esc(dv.type)+'</span>':'')+'</div>'+
        '<div style="display:flex;flex-wrap:wrap;gap:9px 16px">'+rows.join('')+'</div></div>';}).join('');
  }else{
    h+='<div class="hint" style="margin-top:14px;padding:9px 11px;background:var(--glass-2);border:1px solid var(--hairline);border-radius:10px">No Bluetooth devices reporting battery right now. AirPods, mice and keyboards appear here when connected.</div>';
  }
  el.innerHTML=h;
};

// ===== tasks =====
EXPAND_RENDER.tasks=function(el,data){
  function post(body){return fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json();});}
  function refresh(){fetch('/api/expand?id=tasks').then(function(r){return r.json();}).then(function(d){if(d&&(d.tasks||d.available!==undefined))render(d);}).catch(function(){});}
  function op(body){post(body).then(function(){refresh();}).catch(function(){});}
  function dur(s){s=+s||0;if(s<3600)return Math.max(1,Math.round(s/60))+'m';if(s<86400)return Math.round(s/3600)+'h';if(s<604800)return Math.round(s/86400)+'d';return Math.round(s/604800)+'w';}
  function ring(pct){var R=27,C=2*Math.PI*R,off=C*(1-(pct||0)/100),g='tkg'+Math.random().toString(36).slice(2,7);
    return '<svg width="66" height="66" viewBox="0 0 66 66" style="flex:0 0 auto"><defs><linearGradient id="'+g+'" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--iris)"/><stop offset="1" stop-color="var(--quick)"/></linearGradient></defs>'+
    '<circle cx="33" cy="33" r="'+R+'" fill="none" stroke="var(--hairline)" stroke-width="6"/>'+
    '<circle cx="33" cy="33" r="'+R+'" fill="none" stroke="url(#'+g+')" stroke-width="6" stroke-linecap="round" stroke-dasharray="'+C.toFixed(1)+'" stroke-dashoffset="'+off.toFixed(1)+'" transform="rotate(-90 33 33)" style="transition:stroke-dashoffset .5s"/>'+
    '<text x="33" y="34" text-anchor="middle" dominant-baseline="middle" class="num" style="font-size:16px;font-weight:700;fill:var(--ink)">'+(pct||0)+'%</text></svg>';}
  function chip(label,val,tone){var col=tone==='bad'?'var(--bad)':tone==='warn'?'var(--warn)':'var(--muted)';
    return '<span style="display:inline-flex;align-items:center;gap:5px;font-size:11px;color:'+col+';background:var(--chip);border:1px solid var(--hairline);border-radius:999px;padding:3px 10px"><b class="num" style="color:'+(tone?col:'var(--ink)')+';font-weight:680">'+val+'</b>'+esc(label)+'</span>';}
  var DELX='<svg class="ic" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var TICK='<svg viewBox="0 0 24 24" style="width:13px;height:13px" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  function taskRow(t,stale){
    var checked=t.done;
    var box='<button class="tk-chk" data-id="'+esc(t.id)+'" aria-label="Toggle" style="flex:0 0 auto;width:20px;height:20px;border-radius:6px;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;'+
      (checked?'background:linear-gradient(135deg,var(--iris),var(--quick));border:1px solid transparent':'background:transparent;border:1.5px solid var(--muted)')+'">'+(checked?TICK:'')+'</button>';
    var age=t.ts?relTime(t.ts):'';
    var ageCol=stale?'var(--bad)':'var(--faint)';
    var txt='<span style="flex:1;min-width:0;font-size:13px;line-height:1.4;'+(checked?'color:var(--faint);text-decoration:line-through':'color:var(--ink)')+'">'+esc(t.text)+'</span>';
    var ageEl=age?'<span class="num" style="flex:0 0 auto;font-size:10.5px;color:'+ageCol+'">'+esc(age)+'</span>':'';
    var del='<button class="tk-del" data-id="'+esc(t.id)+'" aria-label="Delete" style="flex:0 0 auto;background:none;border:none;cursor:pointer;color:var(--faint);opacity:.55;padding:2px;display:flex">'+DELX+'</button>';
    return '<div class="tk-row" style="display:flex;align-items:center;gap:11px;padding:9px 2px;border-bottom:1px solid var(--hairline)">'+box+txt+ageEl+del+'</div>';
  }
  function sectionLabel(txt,right){
    return '<div style="display:flex;align-items:center;gap:8px;margin:16px 0 4px"><span style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600">'+esc(txt)+'</span>'+(right||'')+'</div>';
  }
  function render(d){
    d=d||{};
    if(d.available===false){el.innerHTML='<div class="hint">'+esc(d.reason||'Tasks unavailable.')+'</div>';return;}
    var tasks=d.tasks||[];
    var openT=tasks.filter(function(t){return !t.done;});
    var doneT=tasks.filter(function(t){return t.done;});
    var total=d.total!=null?d.total:tasks.length;
    var pct=d.pct!=null?d.pct:(total?Math.round(doneT.length/total*100):0);
    var h='<div style="display:flex;align-items:center;gap:16px;margin:2px 0 4px">'+ring(pct)+
      '<div style="min-width:0"><div style="display:flex;align-items:baseline;gap:7px"><span class="num" style="font-size:30px;font-weight:700;line-height:1">'+openT.length+'</span>'+
      '<span class="w-sub" style="font-size:13px">open</span></div>'+
      '<div class="w-sub" style="margin-top:3px">'+doneT.length+' of '+total+' done'+(total===0?' &mdash; nothing yet':'')+'</div></div></div>';
    var chips=[];
    if(d.added_24h)chips.push(chip('added today',d.added_24h));
    if(d.added_7d)chips.push(chip('this week',d.added_7d));
    if(d.avg_open_age!=null&&openT.length)chips.push(chip('avg age',dur(d.avg_open_age)));
    if(d.stale)chips.push(chip('stale',d.stale,'bad'));
    if(chips.length)h+='<div style="display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 2px">'+chips.join('')+'</div>';
    h+='<div style="display:flex;gap:8px;margin:14px 0 2px"><input class="tk-new" placeholder="Add a task&hellip;" spellcheck="false" style="flex:1;background:var(--glass-2);border:1px solid var(--hairline);border-radius:10px;padding:10px 13px;color:var(--ink);font-size:13px;outline:none">'+
      '<button class="tk-add primary" style="flex:0 0 auto;display:flex;align-items:center;gap:5px;border-radius:10px;padding:0 15px;font-weight:600"><svg class="ic" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Add</button></div>';
    var stale=openT.filter(function(t){return t.age!=null&&t.age>604800;});
    var active=openT.filter(function(t){return !(t.age!=null&&t.age>604800);});
    active.sort(function(a,b){return (b.ts||0)-(a.ts||0);});
    stale.sort(function(a,b){return (a.ts||0)-(b.ts||0);});
    if(openT.length){
      h+=sectionLabel('Open · '+openT.length);
      h+='<div class="tk-open">'+active.map(function(t){return taskRow(t,false);}).join('');
      if(stale.length){
        h+='</div>'+sectionLabel('Needs attention · older than a week');
        h+='<div class="tk-open">'+stale.map(function(t){return taskRow(t,true);}).join('')+'</div>';
      } else { h+='</div>'; }
    } else {
      h+='<div class="hint" style="margin-top:14px">All clear &mdash; no open tasks. Your assistant reads these too.</div>';
    }
    if(doneT.length){
      var clr='<button class="tk-clear" style="margin-left:auto;background:none;border:none;cursor:pointer;font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:0">Clear completed</button>';
      h+=sectionLabel('Completed · '+doneT.length,clr);
      doneT.sort(function(a,b){return (b.ts||0)-(a.ts||0);});
      h+='<div class="tk-done" style="opacity:.75">'+doneT.map(function(t){return taskRow(t,false);}).join('')+'</div>';
    }
    el.innerHTML=h;
    var inp=el.querySelector('.tk-new');
    var add=function(){var v=inp.value.trim();if(!v)return;inp.value='';op({op:'add',text:v});};
    var addBtn=el.querySelector('.tk-add');if(addBtn)addBtn.onclick=add;
    if(inp)inp.addEventListener('keydown',function(e){if(e.key==='Enter')add();});
    el.querySelectorAll('.tk-chk').forEach(function(b){b.onclick=function(){op({op:'toggle',id:b.dataset.id});};});
    el.querySelectorAll('.tk-del').forEach(function(b){b.onclick=function(){op({op:'delete',id:b.dataset.id});};});
    var clrB=el.querySelector('.tk-clear');if(clrB)clrB.onclick=function(){op({op:'clear_done'});};
  }
  render(data);
};

// ===== reminders =====
EXPAND_RENDER.reminders=function(el,d){
  if(!d||!d.available){el.innerHTML='<div class="hint">'+esc((d&&d.reason)||'Reminders needs access — enable it under System Settings → Privacy & Security → Reminders (and Automation), then reopen the dashboard.')+'</div>';return;}
  if(!d.total){el.innerHTML='<div style="text-align:center;padding:30px 0;color:var(--muted)"><div style="display:inline-flex">'+icon('check')+'</div><div style="margin-top:8px;font-size:13px">No open reminders — you are all caught up.</div></div>';return;}
  var now=Date.now()/1000, pad=function(n){return (n<10?'0':'')+n;};
  var fmtTime=function(dt){var h=dt.getHours(),m=dt.getMinutes(),ap=h<12?'AM':'PM',hh=h%12;if(hh===0)hh=12;return hh+(m?':'+pad(m):'')+' '+ap;};
  var dueChip=function(it){
    if(it.due_ts==null)return '<span style="font-size:10.5px;color:var(--faint);white-space:nowrap;align-self:center">no date</span>';
    var dt=new Date(it.due_ts*1000),label,col='var(--muted)',bg='var(--chip)';
    if(it.due_state==='overdue'){var days=Math.floor((now-it.due_ts)/86400);label=days>=1?days+'d overdue':(it.all_day?'overdue':fmtTime(dt));col='var(--bad)';bg='color-mix(in srgb,var(--bad) 15%,transparent)';}
    else if(it.due_state==='today'){label='Today'+(it.all_day?'':' '+fmtTime(dt));col='var(--warn)';bg='color-mix(in srgb,var(--warn) 16%,transparent)';}
    else{var d2=Math.ceil((it.due_ts-now)/86400);if(d2<=6)label=dt.toLocaleDateString([],{weekday:'short'})+(it.all_day?'':' '+fmtTime(dt));else label=dt.toLocaleDateString([],{month:'short',day:'numeric'});}
    return '<span class="num" style="font-size:10.5px;font-weight:650;white-space:nowrap;align-self:center;color:'+col+';background:'+bg+';border-radius:7px;padding:2px 7px">'+esc(label)+'</span>';
  };
  var priMark=function(p){if(!p)return '';if(p<=1)return '<span title="High priority" style="color:var(--bad);font-weight:800;font-size:12px;letter-spacing:-1.5px;margin-right:3px">!!!</span>';if(p<=5)return '<span title="Medium priority" style="color:var(--warn);font-weight:800;font-size:12px;letter-spacing:-1.5px;margin-right:3px">!!</span>';return '<span title="Low priority" style="color:var(--muted);font-weight:800;font-size:12px;margin-right:3px">!</span>';};
  var flag='<svg viewBox="0 0 24 24" width="11" height="11" style="vertical-align:-1px;margin-left:4px;fill:none;stroke:var(--warn);stroke-width:2.2"><path d="M6 21V4h11l-2.2 3.5L17 11H6"/></svg>';
  var tile=function(label,val,color){return '<div style="background:var(--glass-2);border:1px solid var(--hairline);border-radius:10px;padding:9px 11px"><div class="num" style="font-size:19px;font-weight:700;line-height:1;color:'+(color||'var(--ink)')+'">'+val+'</div><div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px;font-weight:600">'+esc(label)+'</div></div>';};
  var h='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:2px 0 12px">'+tile('Open',d.total)+tile('Overdue',d.overdue,d.overdue?'var(--bad)':'var(--ink)')+tile('Today',d.today,d.today?'var(--warn)':'var(--ink)')+tile('No date',d.no_due)+'</div>';
  var up=d.scheduled-d.overdue-d.today; if(up<0)up=0;
  var seg=function(n,c){return n>0?'<i style="display:block;height:100%;width:'+(n/d.total*100)+'%;background:'+c+'"></i>':'';};
  h+='<div style="display:flex;height:8px;border-radius:5px;overflow:hidden;background:var(--hairline);margin-bottom:7px">'+seg(d.overdue,'var(--bad)')+seg(d.today,'var(--warn)')+seg(up,'var(--iris)')+seg(d.no_due,'var(--faint)')+'</div>';
  var dot=function(c,label,n){return '<span style="display:inline-flex;align-items:center;gap:4px"><i style="width:7px;height:7px;border-radius:2px;background:'+c+';display:inline-block"></i>'+label+' '+n+'</span>';};
  h+='<div class="num" style="display:flex;flex-wrap:wrap;gap:13px;font-size:10.5px;color:var(--muted);margin-bottom:12px">'+dot('var(--bad)','Overdue',d.overdue)+dot('var(--warn)','Today',d.today)+dot('var(--iris)','Upcoming',up)+dot('var(--faint)','No date',d.no_due)+'</div>';
  (d.lists||[]).forEach(function(g){
    h+='<div style="display:flex;align-items:center;gap:8px;margin:15px 0 3px"><span style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700">'+esc(g.name)+'</span>'+(g.overdue?'<span class="num" style="font-size:10px;font-weight:700;color:var(--bad);background:color-mix(in srgb,var(--bad) 14%,transparent);border-radius:7px;padding:1px 6px">'+g.overdue+' overdue</span>':'')+'<span class="num" style="margin-left:auto;font-size:11px;color:var(--faint)">'+g.count+'</span></div>';
    (g.items||[]).forEach(function(it){
      var ring=it.due_state==='overdue'?'var(--bad)':it.due_state==='today'?'var(--warn)':'var(--muted)';
      h+='<div style="display:flex;align-items:flex-start;gap:9px;padding:7px 1px;border-bottom:1px solid var(--hairline)"><span style="flex:0 0 auto;width:13px;height:13px;margin-top:2px;border-radius:50%;border:1.6px solid '+ring+';opacity:.85"></span><div style="flex:1;min-width:0"><div style="font-size:12.5px;line-height:1.35;color:var(--ink)">'+priMark(it.priority)+esc(it.title)+(it.flagged?flag:'')+'</div>'+(it.note?'<div style="font-size:11px;color:var(--muted);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(it.note)+'</div>':'')+'</div>'+dueChip(it)+'</div>';
    });
  });
  el.innerHTML=h;
};

// ===== notes =====
EXPAND_RENDER.notes=function(el,data){
  data=data||{};
  var A=data.apple||{};
  var initial=(data.text!=null?data.text:'');
  el.innerHTML=
    '<div style="display:flex;align-items:center;gap:8px;margin:2px 0 7px">'+
      '<span style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700">Scratchpad</span>'+
      '<span id="nt-save" class="w-sub" style="margin-left:auto;display:inline-flex;align-items:center;gap:5px;font-size:11px">'+
        '<i id="nt-dot" style="width:6px;height:6px;border-radius:50%;background:var(--faint);display:inline-block"></i>'+
        '<span id="nt-savetxt">Saved</span></span>'+
    '</div>'+
    '<textarea id="nt-ta" spellcheck="true" placeholder="Type freely — everything saves automatically." '+
      'style="width:100%;min-height:300px;resize:vertical;box-sizing:border-box;padding:14px 15px;border:1px solid var(--hairline);'+
      'border-radius:14px;background:var(--glass-2);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,\'SF Pro Text\',system-ui,sans-serif;'+
      'outline:none;letter-spacing:.005em;transition:border-color .18s"></textarea>'+
    '<div style="display:flex;align-items:center;gap:0;margin:9px 2px 2px;font-size:11.5px">'+
      '<span class="num" style="font-weight:640"><span id="nt-words">0</span></span>&nbsp;<span class="w-sub">words</span>'+
      '<span style="color:var(--faint);margin:0 9px">&middot;</span>'+
      '<span class="num" style="font-weight:640"><span id="nt-chars">0</span></span>&nbsp;<span class="w-sub">chars</span>'+
      '<span style="color:var(--faint);margin:0 9px">&middot;</span>'+
      '<span class="num" style="font-weight:640"><span id="nt-lines">0</span></span>&nbsp;<span class="w-sub">lines</span>'+
      '<span id="nt-read" class="w-sub" style="margin-left:auto"></span>'+
    '</div>'+
    '<div id="nt-apple"></div>';

  var ta=el.querySelector('#nt-ta');
  var dot=el.querySelector('#nt-dot');
  var stxt=el.querySelector('#nt-savetxt');
  var wEl=el.querySelector('#nt-words');
  var cEl=el.querySelector('#nt-chars');
  var lEl=el.querySelector('#nt-lines');
  var rEl=el.querySelector('#nt-read');
  ta.value=initial;

  function counts(){
    var t=ta.value;
    var w=(t.match(/\S+/g)||[]).length;
    wEl.textContent=w.toLocaleString();
    cEl.textContent=t.length.toLocaleString();
    lEl.textContent=(t?t.split('\n').length:0).toLocaleString();
    var mins=w/200;
    rEl.textContent=w?(mins<1?Math.max(1,Math.round(mins*60))+' sec read':(Math.round(mins*10)/10)+' min read'):'';
  }
  function setState(s){
    var map={saved:['var(--ok)','Saved'],dirty:['var(--warn)','Unsaved'],saving:['var(--muted)','Saving…'],error:['var(--bad)','Save failed']};
    var m=map[s]||map.saved;
    dot.style.background=m[0];
    stxt.textContent=m[1];
    ta.style.borderColor=(s==='dirty')?'color-mix(in srgb,var(--iris) 32%,var(--hairline))':'var(--hairline)';
  }
  counts();
  setState('saved');
  if(!initial) stxt.textContent='Empty';

  var timer=null,lastSaved=initial;
  function save(){
    var t=ta.value;
    if(t===lastSaved){setState(t?'saved':'saved');if(!t)stxt.textContent='Empty';return;}
    setState('saving');
    fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})})
      .then(function(r){return r.json();})
      .then(function(j){if(j&&j.ok){lastSaved=t;setState('saved');}else{setState('error');}})
      .catch(function(){setState('error');});
  }
  ta.addEventListener('input',function(){
    counts();
    if(ta.value!==lastSaved)setState('dirty');
    if(timer)clearTimeout(timer);
    timer=setTimeout(save,650);
  });
  ta.addEventListener('blur',function(){if(timer){clearTimeout(timer);timer=null;}save();});
  ta.addEventListener('keydown',function(e){
    if((e.metaKey||e.ctrlKey)&&(e.key==='s'||e.key==='S')){e.preventDefault();if(timer){clearTimeout(timer);timer=null;}save();}
  });

  var ap=el.querySelector('#nt-apple');
  var N=A.notes||[];
  if(A.available&&N.length){
    var head='<div style="display:flex;align-items:baseline;gap:8px;margin:18px 0 6px;border-top:1px solid var(--hairline);padding-top:13px">'+
      '<span style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700">Recent Apple Notes</span>'+
      '<span class="w-sub" style="margin-left:auto;font-size:11px">'+(A.total||N.length)+' total</span></div>';
    var rows=N.map(function(n){
      return '<div style="display:flex;align-items:baseline;gap:10px;padding:6px 0;border-bottom:1px solid var(--hairline)">'+
        '<span style="color:var(--faint);flex:0 0 auto;margin-top:1px;line-height:1">'+icon('note')+'</span>'+
        '<span style="flex:1;min-width:0;font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(n.title||'Untitled')+'</span>'+
        '<span class="num w-sub" style="flex:0 0 auto;font-size:10.5px">'+(n.ts?relTime(n.ts):'')+'</span></div>';
    }).join('');
    ap.innerHTML=head+rows+'<div class="hint" style="margin-top:8px;color:var(--faint)">Read-only preview from the macOS Notes app.</div>';
  }else if(A.available===false){
    ap.innerHTML='<div style="margin:18px 0 0;border-top:1px solid var(--hairline);padding-top:13px">'+
      '<div style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin-bottom:5px">Apple Notes</div>'+
      '<div class="hint" style="color:var(--faint);line-height:1.5">'+esc(A.reason||'Apple Notes preview unavailable.')+'</div></div>';
  }
};

// ===== briefing =====
EXPAND_RENDER.briefing=function(el,data){
  const RIC='<svg class="ic" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><polyline points="21 3 21 9 15 9"/></svg>';
  const SPIN='<span style="width:16px;height:16px;border-radius:50%;border:2px solid var(--hairline);border-top-color:var(--iris);animation:spin .9s linear infinite;flex:0 0 auto"></span>';
  function pmin(t){if(!t)return null;const m=(''+t).match(/(\d{1,2}):(\d{2})\s*([AaPp][Mm])?/);if(!m)return null;let h=+m[1];const mi=+m[2],ap=(m[3]||'').toLowerCase();if(ap==='pm'&&h<12)h+=12;if(ap==='am'&&h===12)h=0;return h*60+mi;}
  function readLbl(s){s=s||0;return s<60?Math.max(1,Math.round(s))+'s':Math.max(1,Math.round(s/60))+'m';}
  function cell(label,val,accent){return '<div style="background:var(--glass-2);border:1px solid var(--hairline);border-radius:12px;padding:9px 11px"><div class="num" style="font-size:19px;font-weight:680;letter-spacing:-.02em;color:'+(accent||'var(--ink)')+'">'+val+'</div><div style="font-size:9px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;margin-top:2px">'+label+'</div></div>';}
  function hero(d){
    const up=d.generated_at?('Updated '+relTime(d.generated_at)+(d.stale?' · stale':'')):'Not generated yet';
    return '<div data-brf style="display:flex;align-items:flex-start;gap:12px;margin:2px 0 2px">'+
      '<div style="flex:1;min-width:0">'+
        '<div style="font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--iris);font-weight:680">'+esc(d.greeting||'Daily briefing')+'</div>'+
        '<div style="font-family:ui-serif,Georgia,\'Times New Roman\',serif;font-size:20px;font-weight:560;letter-spacing:-.01em;color:var(--ink);margin-top:3px">'+esc(d.date_label||'Your brief')+'</div>'+
      '</div>'+
      '<div style="text-align:right;flex:0 0 auto;display:flex;flex-direction:column;align-items:flex-end;gap:6px">'+
        '<button id="brf-regen" class="ghost" style="display:inline-flex;align-items:center;gap:6px;padding:5px 11px;font-size:11.5px;font-weight:600;border-radius:9px"><span class="brf-ic" style="display:flex">'+RIC+'</span>Regenerate</button>'+
        '<div class="w-sub" style="font-size:10.5px'+(d.stale?';color:var(--warn)':'')+'">'+esc(up)+'</div>'+
      '</div></div>';
  }
  function banner(){return '<div data-brf style="display:flex;align-items:center;gap:10px;padding:9px 12px;margin:10px 0 2px;border:1px solid var(--hairline);border-radius:12px;background:var(--glass-2)">'+SPIN+'<div style="min-width:0"><div style="font-size:12.5px;font-weight:600;color:var(--ink)">Refreshing your brief…</div><div class="w-sub" style="font-size:10.5px">Hermes is checking your calendar, inbox and folders.</div></div></div>';}
  function stats(d){
    const st=d.stats||{};const sched=(d.sections||[]).find(function(s){return s.kind==='schedule';});
    const agenda=sched?sched.count:(st.sections||0);
    const tasks=(d.open_tasks==null?'—':d.open_tasks);
    return '<div data-brf style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0 4px">'+
      cell('On agenda',agenda,'var(--iris)')+cell('Brief items',st.items||0)+cell('Open tasks',tasks,d.open_tasks?'var(--warn)':'var(--ink)')+cell('Read',readLbl(st.read_sec))+'</div>';
  }
  function secHead(s){
    return '<div style="display:flex;align-items:center;gap:8px;margin:18px 0 7px">'+
      '<span style="color:var(--muted);display:flex">'+icon(s.icon||'note')+'</span>'+
      '<span style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700">'+esc(s.title)+'</span>'+
      (s.count?'<span class="num" style="font-size:10px;color:var(--faint);background:var(--chip);border-radius:9px;padding:1px 7px;font-weight:600">'+s.count+'</span>':'')+
      '<span style="flex:1;height:1px;background:var(--hairline)"></span></div>';
  }
  function emptyRow(){return '<div style="display:flex;align-items:center;gap:7px;padding:3px 0;color:var(--faint);font-size:12px"><span style="display:flex;opacity:.7">'+icon('check')+'</span>Nothing yet</div>';}
  function schedule(s){
    const now=new Date();const nowM=now.getHours()*60+now.getMinutes();let markDone=false,h='';
    s.items.forEach(function(it){
      const st=pmin(it.time),en=pmin(it.end);
      let state='plain';
      if(st!=null){if(en!=null?en<=nowM:st<nowM-1)state='past';else if(st<=nowM&&(en==null||nowM<en))state='now';else state='next';}
      if(!markDone&&(state==='now'||state==='next')){markDone=true;
        h+='<div style="display:flex;align-items:center;gap:8px;margin:4px 0 4px 60px"><span style="width:7px;height:7px;border-radius:50%;background:var(--iris);box-shadow:0 0 0 3px color-mix(in srgb,var(--iris) 22%,transparent)"></span><span style="font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--iris);font-weight:700">Now · '+now.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})+'</span><span style="flex:1;height:1px;background:var(--hairline)"></span></div>';
      }
      const tCol=state==='past'?'var(--faint)':state==='now'?'var(--iris)':'var(--ink)';
      const lCol=state==='past'?'var(--muted)':'var(--ink)';
      const dot=state==='now'?'<span style="width:9px;height:9px;border-radius:50%;background:var(--iris);box-shadow:0 0 0 3px color-mix(in srgb,var(--iris) 20%,transparent)"></span>'
        :state==='past'?'<span style="width:7px;height:7px;border-radius:50%;background:var(--faint);opacity:.6"></span>'
        :'<span style="width:8px;height:8px;border-radius:50%;border:1.5px solid var(--muted);box-sizing:border-box"></span>';
      h+='<div style="display:flex;align-items:stretch">'+
        '<div style="width:52px;flex:0 0 auto;text-align:right;padding:5px 8px 5px 0">'+
          '<div class="num" style="font-size:11.5px;font-weight:640;color:'+tCol+'">'+esc(it.time||'')+'</div>'+
          (it.end?'<div class="num" style="font-size:9.5px;color:var(--faint)">'+esc(it.end)+'</div>':'')+'</div>'+
        '<div style="width:16px;flex:0 0 auto;display:flex;justify-content:center;padding-top:7px;background:linear-gradient(var(--hairline),var(--hairline)) 50%/1.5px 100% no-repeat">'+dot+'</div>'+
        '<div style="flex:1;min-width:0;padding:5px 0 9px 9px"><div style="font-size:12.5px;line-height:1.4;font-weight:'+(state==='now'?'640':'500')+';color:'+lCol+'">'+esc(it.label||it.text)+(state==='now'?' <span style="font-size:9.5px;color:var(--iris);font-weight:700">&larr; now</span>':'')+'</div></div>'+
      '</div>';
    });
    return h;
  }
  function inboxHtml(t){
    let e=esc(t);
    e=e.replace(/(^|\s)([Ff]rom)\s+([A-Z][\wÀ-ÿ.'’\-]*(?:\s+[A-Z][\wÀ-ÿ.'’\-]*)?)/,function(_,p,f,n){return p+f+' <b style="color:var(--ink);font-weight:660">'+n+'</b>';});
    e=e.replace(/\s*\(([^)]*)\)\s*$/,' <span style="color:var(--faint);font-size:11px">· $1</span>');
    return e;
  }
  function itemRow(s,it,i){
    let lead,body;
    if(s.kind==='priority'){
      lead='<span class="num" style="flex:0 0 auto;width:20px;height:20px;border-radius:50%;background:var(--chip);color:var(--iris);font-weight:700;font-size:11px;display:flex;align-items:center;justify-content:center">'+(i+1)+'</span>';
      body=it.label?'<b style="font-weight:640">'+esc(it.label)+'</b>'+(it.rest?' — '+esc(it.rest):''):esc(it.text);
    }else if(s.kind==='inbox'){
      lead='<span style="flex:0 0 auto;width:6px;height:6px;border-radius:50%;background:var(--quick);margin-top:6px"></span>';
      body=inboxHtml(it.text);
    }else{
      lead='<span style="flex:0 0 auto;width:6px;height:6px;border-radius:50%;background:var(--iris);opacity:.75;margin-top:6px"></span>';
      body=it.label?'<b style="font-weight:640">'+esc(it.label)+'</b>'+(it.rest?' — '+esc(it.rest):''):esc(it.text);
    }
    return '<div style="display:flex;gap:9px;align-items:flex-start;padding:4px 0">'+lead+'<div style="flex:1;min-width:0;font-size:12.5px;line-height:1.45;color:var(--ink)">'+body+'</div></div>';
  }
  function section(s){
    let h='<div data-brf>'+secHead(s);
    if(!s.items||!s.items.length)h+=emptyRow();
    else if(s.kind==='schedule')h+=schedule(s);
    else h+=s.items.map(function(it,i){return itemRow(s,it,i);}).join('');
    return h+'</div>';
  }
  function footer(d){
    const st=d.stats||{};
    return '<div data-brf style="margin-top:16px;padding-top:10px;border-top:1px solid var(--hairline);display:flex;gap:8px;flex-wrap:wrap;font-size:10.5px;color:var(--faint)">'+
      (st.words?'<span>'+st.words+' words</span><span>·</span>':'')+
      '<span>Auto-refreshes every '+(d.refresh_min||30)+' min</span></div>';
  }
  function bindRegen(d){
    const btn=el.querySelector('#brf-regen');if(!btn)return;
    btn.onclick=function(){
      btn.disabled=true;const ic=btn.querySelector('.brf-ic svg');if(ic)ic.style.animation='spin .9s linear infinite';
      fetch('/api/briefing/refresh',{method:'POST'}).catch(function(){}).then(function(){
        draw(Object.assign({},d,{generating:true}));poll();
      });
    };
  }
  function poll(){
    clearInterval(el._brfPoll);
    el._brfPoll=setInterval(function(){
      fetch('/api/expand?id=briefing').then(function(r){return r.json();}).then(function(nd){
        if(!nd||!nd.generating){clearInterval(el._brfPoll);el._brfPoll=null;draw(nd);}
      }).catch(function(){});
    },3500);
  }
  function emptyHtml(d){
    return hero(d)+'<div data-brf style="text-align:center;padding:34px 16px;color:var(--muted)">'+
      '<div style="display:flex;justify-content:center;color:var(--iris);opacity:.85;margin-bottom:10px">'+icon('spark')+'</div>'+
      '<div style="font-size:14px;font-weight:600;color:var(--ink)">No briefing yet</div>'+
      '<div class="w-sub" style="margin-top:4px;max-width:280px;margin-left:auto;margin-right:auto">Hermes writes a personalized brief of your schedule, priorities, and inbox. Generate one to get started.</div>'+
      '<button id="brf-gen" class="primary" style="margin-top:14px;display:inline-flex;align-items:center;gap:7px">'+RIC+'Generate brief</button></div>';
  }
  function draw(d){
    if(el._brfPoll&&d&&!d.generating){clearInterval(el._brfPoll);el._brfPoll=null;}
    if(!d||d.available===false){el.innerHTML='<div class="hint">'+esc((d&&d.reason)||'Briefing unavailable.')+'</div>';return;}
    const hasSec=d.sections&&d.sections.length;
    if(!hasSec){
      if(d.generating){el.innerHTML=hero(d)+banner()+'<div class="skel" style="margin-top:14px"><i></i><i></i><i></i><i></i><i></i><i></i></div>';bindRegen(d);if(!el._brfPoll)poll();return;}
      if(d.reply){el.innerHTML=hero(d)+'<div class="md" data-brf style="margin-top:10px">'+renderMd(d.reply)+'</div>'+footer(d);bindRegen(d);return;}
      el.innerHTML=emptyHtml(d);
      const g=el.querySelector('#brf-gen');if(g)g.onclick=function(){g.disabled=true;fetch('/api/briefing/refresh',{method:'POST'}).catch(function(){}).then(function(){draw(Object.assign({},d,{generating:true}));poll();});};
      bindRegen(d);return;
    }
    let h=hero(d);if(d.generating)h+=banner();h+=stats(d);
    h+=d.sections.map(function(s){return section(s);}).join('');
    h+=footer(d);
    el.innerHTML=h;bindRegen(d);
    if(d.generating&&!el._brfPoll)poll();
    try{if(typeof revealStagger==='function')revealStagger(el.querySelectorAll('[data-brf]'),40);}catch(e){}
  }
  draw(data);
};

// ===== messages =====
EXPAND_RENDER.messages = function(el, data){
  var d = data || {};
  // ---- permission / unavailable states ----
  if(!d.available){
    if(d.grant){
      el.innerHTML =
        '<div style="padding:16px;border:1px solid var(--hairline);border-radius:14px;background:var(--glass-2)">'+
          '<div style="display:flex;align-items:center;gap:9px;margin-bottom:8px">'+
            '<span style="color:var(--warn);display:flex">'+icon('chat')+'</span>'+
            '<span style="font-weight:680;font-size:14px">Full Disk Access needed</span></div>'+
          '<div class="w-sub" style="line-height:1.5;color:var(--muted)">Hermes reads your Messages database locally &mdash; nothing leaves this Mac. Grant access so the dashboard process can open it:</div>'+
          '<ol style="margin:10px 0 2px;padding-left:20px;font-size:12px;color:var(--muted);line-height:1.65">'+
            '<li>Open <b style="color:var(--ink)">System Settings &rsaquo; Privacy &amp; Security &rsaquo; Full Disk Access</b></li>'+
            '<li>Click <b style="color:var(--ink)">+</b> and add the Python that runs the dashboard <span class="w-sub">(the launchd agent&rsquo;s interpreter)</span></li>'+
            '<li>Toggle it <b style="color:var(--ink)">on</b>, then restart the dashboard</li></ol>'+
        '</div>';
      return;
    }
    el.innerHTML = '<div class="hint">'+esc(d.reason || 'Messages unavailable.')+'</div>';
    return;
  }

  var C = d.conversations || [];
  if(!C.length){ el.innerHTML = '<div class="hint">No recent conversations.</div>'; return; }

  // ---- header stats ----
  var h = statGrid([
    ['Unread', (d.total_unread||0)],
    ['Chats',  (d.convo_count||C.length)],
    ['Today',  (d.today_count||0)]
  ]);

  // ---- avatar helpers ----
  var PAL = ['var(--iris)','var(--quick)','var(--ok)','var(--warn)','var(--wac)','var(--bad)'];
  function initials(name){
    var s = (name||'').replace(/^[^A-Za-z0-9]+/,'').trim();
    if(!s) return '#';
    var p = s.split(/\s+/);
    if(p.length>=2) return (p[0][0]+p[1][0]).toUpperCase();
    return s.slice(0,2).toUpperCase();
  }
  function hue(name){ var n=0,i; for(i=0;i<(name||'').length;i++) n=(n*31+name.charCodeAt(i))>>>0; return PAL[n%PAL.length]; }
  var GROUP_GLYPH = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M17 20v-2a4 4 0 0 0-3-3.87"/><path d="M9 20v-2a4 4 0 0 1 3-3.87"/><circle cx="9" cy="7" r="3"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';

  h += '<div style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:14px 0 6px">Conversations</div>';

  h += C.map(function(c){
    var unread = c.unread>0;
    var col = hue(c.name);
    var av;
    if(c.group){
      av = '<span style="width:38px;height:38px;flex:0 0 38px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, '+col+' 20%, transparent);color:'+col+'">'+GROUP_GLYPH+'</span>';
    } else {
      av = '<span style="width:38px;height:38px;flex:0 0 38px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;letter-spacing:.02em;background:color-mix(in srgb, '+col+' 20%, transparent);color:'+col+'">'+esc(initials(c.name))+'</span>';
    }
    // preview line: sender prefix for outgoing / group
    var pre = '';
    if(c.from_me) pre = '<span style="color:var(--faint)">You: </span>';
    else if(c.group) pre = '<span style="color:'+hue(c.sender)+'">'+esc(String(c.sender).split(/\s+/)[0])+': </span>';
    var pv;
    if(c.attachment && (!c.last || c.last==='Attachment')){
      pv = '<span style="display:inline-flex;align-items:center;gap:4px;color:var(--muted)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>Attachment</span>';
    } else if(c.reaction && (!c.last || c.last==='Reaction')){
      pv = '<span style="color:var(--muted)">Reacted to a message</span>';
    } else {
      pv = pre + esc(c.last || '');
    }
    var badge = unread ? '<span class="num" style="min-width:19px;height:19px;padding:0 6px;border-radius:10px;background:var(--iris);color:#fff;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;line-height:1">'+(c.unread>99?'99+':c.unread)+'</span>' : '';

    return '<div style="display:flex;align-items:center;gap:11px;padding:9px 2px;border-bottom:1px solid var(--hairline)">'+
      av+
      '<div style="min-width:0;flex:1">'+
        '<div style="display:flex;align-items:baseline;gap:8px">'+
          '<span style="font-weight:'+(unread?'700':'600')+';font-size:13.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(c.name)+'</span>'+
          (c.group?'<span class="w-sub" style="font-size:10px;flex:0 0 auto">'+c.participants+'</span>':'')+
          '<span class="w-sub num" style="margin-left:auto;flex:0 0 auto;font-size:11px;color:'+(unread?'var(--iris)':'var(--faint)')+'">'+(c.ts?relTime(c.ts):'')+'</span>'+
        '</div>'+
        '<div style="display:flex;align-items:center;gap:8px;margin-top:2px">'+
          '<span style="min-width:0;flex:1;font-size:12px;line-height:1.35;color:'+(unread?'var(--ink)':'var(--muted)')+';overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+pv+'</span>'+
          badge+
        '</div>'+
      '</div>'+
    '</div>';
  }).join('');

  h += '<div class="w-sub" style="margin-top:10px;font-size:10.5px;color:var(--faint);display:flex;align-items:center;gap:5px">'+
       '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>'+
       'Local-only &middot; read straight from chat.db, never sent anywhere</div>';

  el.innerHTML = h;
};

// ===== quicklinks =====
EXPAND_RENDER.quicklinks=function(el,data){
  if(data&&data.error){el.innerHTML='<div class="hint">'+esc(data.error)+'</div>';return;}
  let links=(data&&data.links||[]).map(l=>({label:l.label||'',url:l.url||'',domain:l.domain||'',mono:l.mono||'#',hue:(l.hue||0)}));
  const sugg=(data&&data.suggestions||[]).slice();
  const SEC='font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:16px 2px 8px';
  const INP='flex:1;min-width:0;background:var(--chip);border:1px solid var(--hairline);border-radius:9px;padding:7px 10px;font-size:12.5px;color:var(--ink);outline:none';
  const _dom=u=>{try{let n=new URL(/:\/\//.test(u)?u:'https://'+u).hostname.toLowerCase();return n.replace(/^www\./,'');}catch(e){return '';}};
  const _mono=(lb,dm)=>{const s=(lb||dm||'').trim();return s?s[0].toUpperCase():'#';};
  const _hue=k=>{let h=0;for(const c of (k||'?'))h=(h*31+c.charCodeAt(0))>>>0;return h%360;};
  const chip=(l,sz,fs)=>'<span style="flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:'+sz+'px;height:'+sz+'px;border-radius:'+Math.round(sz*0.28)+'px;font-weight:700;font-size:'+fs+'px;color:#fff;letter-spacing:-.02em;background:linear-gradient(135deg,hsl('+l.hue+' 72% 54%),hsl('+((l.hue+42)%360)+' 66% 44%));box-shadow:0 1px 3px hsl('+l.hue+' 60% 30% / .45)">'+esc(l.mono)+'</span>';
  async function persist(){try{await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quicklinks:links.map(l=>({label:l.label,url:l.url}))})});}catch(e){}}
  function render(){
    const nd=new Set(links.map(l=>l.domain).filter(Boolean)).size;
    let h='<div class="w-sub" style="margin:2px 2px 12px;font-weight:600"><b class="num">'+links.length+'</b> link'+(links.length===1?'':'s')+' &middot; <b class="num">'+nd+'</b> domain'+(nd===1?'':'s')+' &middot; <span style="color:var(--faint)">click a tile to open</span></div>';
    if(links.length){
      h+='<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px">'+links.map((l,i)=>
        '<div style="position:relative;background:var(--glass-2);border:1px solid var(--hairline);border-radius:12px;transition:border-color .15s">'+
          '<a href="#" data-url="'+esc(l.url)+'" title="'+esc(l.url)+'" style="display:flex;align-items:center;gap:10px;padding:11px 34px 11px 11px;text-decoration:none;color:var(--ink);min-width:0">'+
            chip(l,34,15)+
            '<span style="min-width:0;display:flex;flex-direction:column;gap:1px">'+
              '<span style="font-weight:620;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(l.label)+'</span>'+
              '<span style="font-size:10.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(l.domain||'—')+'</span>'+
            '</span></a>'+
          '<button data-del="'+i+'" title="Remove" style="position:absolute;top:6px;right:6px;width:20px;height:20px;display:flex;align-items:center;justify-content:center;border:none;border-radius:6px;background:transparent;color:var(--faint);cursor:pointer;padding:0">'+
            '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>'+
        '</div>').join('')+'</div>';
    }else{h+='<div class="hint">No links yet — add one below.</div>';}
    h+='<div style="'+SEC+'">Add link</div>';
    h+='<div style="display:flex;gap:7px;align-items:center">'+
        '<input id="ql-lb" placeholder="Label" style="'+INP+';max-width:34%">'+
        '<input id="ql-url" placeholder="example.com" style="'+INP+'">'+
        '<button id="ql-add" class="primary" style="flex:0 0 auto;padding:7px 14px">Add</button></div>';
    const have=new Set(links.map(l=>l.domain).filter(Boolean));
    const av=sugg.filter(s=>!have.has(s.domain));
    if(av.length){
      h+='<div style="'+SEC+'">Quick add</div>';
      h+='<div style="display:flex;flex-wrap:wrap;gap:7px">'+av.map((s,i)=>
        '<button data-sg="'+i+'" style="display:inline-flex;align-items:center;gap:7px;background:var(--chip);border:1px solid var(--hairline);border-radius:999px;padding:4px 11px 4px 5px;font-size:12px;color:var(--ink);cursor:pointer;font-weight:520">'+
          chip(s,20,10)+esc(s.label)+'</button>').join('')+'</div>';
    }
    el.innerHTML=h;
    wireLinks(el);
    el.querySelectorAll('button[data-del]').forEach(b=>{
      b.onmouseenter=()=>{b.style.background='var(--bad)';b.style.color='#fff';};
      b.onmouseleave=()=>{b.style.background='transparent';b.style.color='var(--faint)';};
      b.onclick=async e=>{e.preventDefault();links.splice(+b.dataset.del,1);await persist();render();};});
    const doAdd=async()=>{
      const lb=el.querySelector('#ql-lb'),ur=el.querySelector('#ql-url');
      let u=(ur.value||'').trim();if(!u)return;
      if(!/:\/\//.test(u))u='https://'+u;
      const dm=_dom(u),lab=(lb.value||'').trim()||dm||u;
      links.push({label:lab,url:u,domain:dm,mono:_mono(lab,dm),hue:_hue(dm||lab)});
      await persist();render();};
    el.querySelector('#ql-add').onclick=doAdd;
    el.querySelector('#ql-url').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();doAdd();}};
    el.querySelector('#ql-lb').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();doAdd();}};
    el.querySelectorAll('button[data-sg]').forEach(b=>{b.onclick=async e=>{
      e.preventDefault();const s=av[+b.dataset.sg];
      links.push({label:s.label,url:s.url,domain:s.domain,mono:s.mono,hue:s.hue});
      await persist();render();};});
  }
  render();
};

// ===== recent =====
EXPAND_RENDER.recent = function(el, d){
  if(!d || !d.available){ el.innerHTML='<div class="hint">'+esc((d&&d.reason)||'No recent file activity.')+'</div>'; return; }
  var SL=function(t){return '<div style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:16px 0 7px">'+esc(t)+'</div>';};
  var fmtSize=function(b){b=+b||0; if(b>=1073741824)return (b/1073741824).toFixed(1)+' GB'; if(b>=1048576)return (b/1048576).toFixed(1)+' MB'; if(b>=1024)return (b/1024).toFixed(0)+' KB'; return b+' B';};
  var CODE={py:1,js:1,ts:1,jsx:1,tsx:1,java:1,go:1,rs:1,c:1,cpp:1,cc:1,h:1,hpp:1,cs:1,rb:1,php:1,swift:1,kt:1,sh:1,bash:1,zsh:1,sql:1,json:1,yaml:1,yml:1,toml:1,xml:1,html:1,css:1,scss:1,vue:1};
  var NOTE={md:1,txt:1,rtf:1,doc:1,docx:1,pages:1,org:1,tex:1,pdf:1};
  var SHEET={csv:1,xls:1,xlsx:1,numbers:1,tsv:1};
  var extIc=function(e){return CODE[e]?'code':(NOTE[e]?'note':(SHEET[e]?'activity':'file'));};
  var fileURL=function(p){return 'file://'+String(p).split('/').map(encodeURIComponent).join('/');};
  var fileRow=function(f){
    var chip=(f.ext&&f.ext!=='—')?'<span style="font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);background:var(--chip);padding:1px 6px;border-radius:8px;flex:0 0 auto">'+esc(f.ext)+'</span>':'';
    return '<a href="#" data-url="'+esc(fileURL(f.path))+'" title="'+esc(f.path)+'" style="display:flex;align-items:center;gap:9px;padding:5px 2px 5px 4px;border-bottom:1px solid var(--hairline);text-decoration:none;color:var(--ink)">'+
      '<span style="color:var(--muted);display:flex;flex:0 0 auto">'+icon(extIc(f.ext))+'</span>'+
      '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px">'+esc(f.name)+'</span>'+
      chip+
      '<span class="num w-sub" style="width:60px;text-align:right;flex:0 0 auto">'+fmtSize(f.size)+'</span>'+
      '<span class="num" style="width:62px;text-align:right;color:var(--faint);font-size:11px;flex:0 0 auto">'+relTime(f.mtime)+'</span></a>';
  };
  var h='<div style="display:flex;align-items:flex-end;gap:14px;margin:2px 0 6px">'+
    '<div class="num" style="font-size:44px;font-weight:700;line-height:1">'+d.count+'</div>'+
    '<div style="padding-bottom:5px"><div style="font-size:14px;font-weight:600">file'+(d.count===1?'':'s')+' changed</div>'+
    '<div class="w-sub">last '+d.window_h+'h &middot; '+fmtSize(d.total_size)+' across '+d.folder_count+' folder'+(d.folder_count===1?'':'s')+'</div></div></div>';
  if(!d.count){ el.innerHTML=h+'<div class="hint">Nothing changed in the last '+d.window_h+' hours.</div>'; return; }
  h+=statGrid([['Last 24h', d.last_24h],['Last hour', d.last_1h],['Folders', d.folder_count]]);
  if(d.types&&d.types.length){
    h+=SL('Changed by file type');
    h+=barRows(d.types.map(function(t){return {label:(t.ext==='—'?'no ext':t.ext), val:t.count, sub:t.count+' &middot; '+fmtSize(t.size)};}), 74, 104);
  }
  h+=SL('Recent activity by folder');
  (d.groups||[]).forEach(function(g){
    h+='<div style="margin:12px 0 3px;display:flex;align-items:center;gap:8px">'+
      '<span style="color:var(--iris);display:flex;flex:0 0 auto">'+icon('folder')+'</span>'+
      '<span style="font-weight:640;font-size:13px;flex:0 0 auto;max-width:45%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(g.name)+'</span>'+
      '<span class="w-sub" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(g.rel)+'</span>'+
      '<span class="num" style="color:var(--faint);font-size:11px;flex:0 0 auto">'+g.count+' &middot; '+fmtSize(g.size)+'</span></div>';
    h+=(g.files||[]).map(fileRow).join('');
    var more=g.count-(g.files?g.files.length:0);
    if(more>0) h+='<div style="padding:3px 0 1px 27px;color:var(--faint);font-size:11px">+ '+more+' more file'+(more===1?'':'s')+'</div>';
  });
  h+='<div class="hint" style="margin-top:12px">Scanned '+d.scanned+' files &middot; click a row to open in Finder.</div>';
  el.innerHTML=h; wireLinks(el);
};

// ===== folders =====
EXPAND_RENDER.folders=function(el,data){
  const fb=b=>{b=+b||0;if(b>=1073741824)return (b/1073741824).toFixed(b>=10737418240?0:1)+' GB';if(b>=1048576)return (b/1048576).toFixed(b>=10485760?0:1)+' MB';if(b>=1024)return (b/1024).toFixed(0)+' KB';return b+' B';};
  const lbl=t=>'<div style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:12px 0 5px">'+t+'</div>';
  const shield='<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="flex:0 0 auto"><path d="M12 3l7 3v5c0 4.4-3 7.5-7 8.7C8 20.5 5 17.4 5 13V6z"/><path d="M9.2 12l1.9 1.9 3.7-3.9"/></svg>';
  if(!data||data.available===false){el.innerHTML='<div class="hint">'+esc((data&&data.reason)||'Folder access unavailable.')+'</div>';return;}
  const F=data.folders||[],total=data.total_bytes||0;
  const render=d=>EXPAND_RENDER.folders(el,d);
  const refresh=async()=>{try{const r=await fetch('/api/expand?id=folders').then(x=>x.json());if(r&&(r.available||r.rich))render(r);}catch(e){}};

  let h='<div style="display:flex;gap:9px;align-items:flex-start;padding:9px 11px;border:1px solid var(--hairline);border-radius:11px;background:var(--glass-2);margin-bottom:12px;color:var(--muted)">'+
    '<span style="color:var(--iris)">'+shield+'</span>'+
    '<div style="font-size:11.5px;line-height:1.45"><b style="color:var(--ink);font-weight:650">Read-only workspace.</b> The assistant may list, read and search inside these folders. It will not modify or delete anything unless you ask.</div></div>';

  h+=statGrid([['Folders',''+(data.count||0)],['Files',kfmt(data.total_files||0)],['Total size',fb(total)]]);

  h+=lbl('Grant a folder');
  h+='<div style="display:flex;gap:7px;margin-bottom:4px"><input id="fadd" placeholder="/Users/you/Projects  (paste or type a path)" '+
    'style="flex:1;min-width:0;background:var(--chip);border:1px solid var(--hairline);border-radius:9px;padding:7px 10px;font-size:12px;color:var(--ink);font-family:inherit"/>'+
    '<button id="faddb" style="border:1px solid var(--hairline);background:var(--iris);color:#fff;border-radius:9px;padding:0 14px;font-size:12px;font-weight:600;cursor:pointer">Grant</button></div>';
  h+='<div id="ferr" style="color:var(--bad);font-size:11px;min-height:0;margin-bottom:4px"></div>';
  const sug=(data.suggestions||[]);
  if(sug.length){h+='<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:2px">'+sug.map(p=>{const nm=p.split('/').filter(Boolean).pop();
    return '<button class="fsug" data-p="'+esc(p)+'" style="display:inline-flex;align-items:center;gap:4px;border:1px dashed var(--hairline);background:transparent;color:var(--muted);border-radius:20px;padding:3px 10px;font-size:11px;cursor:pointer">+ '+esc(nm)+'</button>';}).join('')+'</div>';}

  h+=lbl((data.count||0)+' granted '+((data.count===1)?'folder':'folders'));
  if(!F.length){h+='<div class="hint" style="padding:14px 0">No folders granted yet. Add one above so the assistant can help with your files.</div>';}
  h+=F.map(f=>{
    const share=total>0?Math.round((f.bytes||0)/total*100):0;
    let c='<div style="display:flex;align-items:flex-start;gap:9px;border:1px solid var(--hairline);border-radius:14px;padding:12px 13px;margin-bottom:10px;background:var(--glass-2)">';
    c+='<span style="color:var(--iris);margin-top:1px">'+icon('folder')+'</span>';
    c+='<div style="min-width:0;flex:1">';
    c+='<div style="display:flex;align-items:baseline;gap:8px"><span style="font-weight:650;font-size:13.5px">'+esc(f.name)+'</span>';
    c+='<button class="frm" data-p="'+esc(f.path)+'" title="Revoke access" style="margin-left:auto;flex:0 0 auto;border:1px solid var(--hairline);background:transparent;color:var(--muted);border-radius:8px;padding:2px 9px;font-size:11px;cursor:pointer">Revoke</button></div>';
    c+='<div class="num" style="font-size:10.5px;color:var(--faint);word-break:break-all;margin-top:2px">'+esc(f.path)+'</div>';
    if(f.exists===false){c+='<div style="color:var(--warn);font-size:11.5px;margin-top:7px">This folder no longer exists on disk.</div></div></div>';return c;}
    const meta=['<b class="num" style="color:var(--ink)">'+kfmt(f.files||0)+'</b> files',
      '<b class="num" style="color:var(--ink)">'+fb(f.bytes||0)+'</b>',
      kfmt(f.dirs||0)+' subfolders',
      (f.last_mtime?('updated '+relTime(f.last_mtime)):'no recent edits')];
    c+='<div class="w-sub" style="margin:7px 0 6px;font-size:11.5px">'+meta.join('&nbsp;·&nbsp;')+(f.truncated?' <span style="color:var(--faint)">(sampled)</span>':'')+'</div>';
    c+='<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted)"><span style="width:78px">of workspace</span>'+
      '<div style="flex:1;height:7px;border-radius:4px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+share+'%;background:linear-gradient(90deg,var(--iris),var(--quick))"></i></div>'+
      '<span class="num" style="width:34px;text-align:right;color:var(--ink)">'+share+'%</span></div>';
    const ty=(f.types||[]).filter(t=>t.bytes>0);
    if(ty.length){c+=lbl('By file type');c+=barRows(ty.map(t=>({label:(t.ext==='none'?'no ext':'.'+t.ext),val:t.bytes,sub:fb(t.bytes)})),70,58);}
    const lg=(f.largest||[]).filter(x=>x.bytes>0);
    if(lg.length){c+=lbl('Largest files');c+=lg.map(x=>'<div style="display:flex;align-items:baseline;gap:8px;padding:2px 0;font-size:12px">'+
      '<span style="color:var(--muted);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">'+esc(x.name)+'</span>'+
      '<span class="num" style="color:var(--ink);flex:0 0 auto">'+fb(x.bytes)+'</span></div>').join('');}
    if(f.newest&&f.newest.name){c+='<div class="w-sub" style="margin-top:7px;font-size:11px;color:var(--faint)">Newest: <span class="num" style="color:var(--muted)">'+esc(f.newest.name)+'</span> · '+relTime(f.newest.mtime)+'</div>';}
    c+='</div></div>';
    return c;
  }).join('');

  el.innerHTML=h;

  const errEl=el.querySelector('#ferr');
  const doAdd=async(path)=>{if(!path)return;errEl.textContent='';
    try{const r=await fetch('/api/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'add',path})}).then(x=>x.json());
      if(!r.ok){errEl.textContent=r.error||'Could not add that folder.';return;}refresh();}
    catch(e){errEl.textContent='Request failed.';}};
  const inp=el.querySelector('#fadd');
  el.querySelector('#faddb').onclick=()=>doAdd(inp.value.trim());
  inp.onkeydown=e=>{if(e.key==='Enter')doAdd(inp.value.trim());};
  el.querySelectorAll('.fsug').forEach(b=>b.onclick=()=>doAdd(b.dataset.p));
  el.querySelectorAll('.frm').forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent='…';
    try{await fetch('/api/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'remove',path:b.dataset.p})});refresh();}
    catch(e){b.disabled=false;b.textContent='Revoke';}});
};

// ===== clock =====
EXPAND_RENDER.clock = function(el, data){
  var sun = (data && data.sun) || {};
  var sr = sun.available ? sun.sunrise_min : null;
  var ss = sun.available ? sun.sunset_min : null;

  var DOW = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var MON = ['January','February','March','April','May','June','July','August','September','October','November','December'];

  function pad(n){return (n<10?'0':'')+n;}
  function fmtDur(mins){var m=Math.max(0,Math.round(mins));var h=Math.floor(m/60);m=m%60;return h>0?(h+'h '+pad(m)+'m'):(m+'m');}
  function pct(min){return Math.max(0,Math.min(100,(min/1440)*100));}

  var srPct = sr!=null?pct(sr):null, ssPct = ss!=null?pct(ss):null;
  var noonPct = sun.noon_min!=null?pct(sun.noon_min):null;

  var band = '';
  if(srPct!=null && ssPct!=null){
    var tw = pct(30); // ~30min civil twilight, in pct units
    band =
      '<i style="position:absolute;top:0;bottom:0;left:'+Math.max(0,srPct-tw)+'%;right:'+Math.max(0,100-ssPct-tw)+'%;'+
        'background:linear-gradient(90deg,color-mix(in srgb,#F5B94A 12%,transparent),color-mix(in srgb,#F5B94A 20%,transparent),color-mix(in srgb,#F5B94A 12%,transparent));"></i>'+
      '<i style="position:absolute;top:0;bottom:0;left:'+srPct+'%;right:'+(100-ssPct)+'%;'+
        'background:linear-gradient(90deg,color-mix(in srgb,#F5B94A 32%,transparent),color-mix(in srgb,#FFD27A 42%,transparent),color-mix(in srgb,#F5B94A 32%,transparent));"></i>';
  }
  var sunMk = '';
  if(srPct!=null) sunMk += '<i style="position:absolute;top:0;bottom:0;left:'+srPct+'%;width:1.5px;background:#F5B94A;opacity:.8"></i>';
  if(ssPct!=null) sunMk += '<i style="position:absolute;top:0;bottom:0;left:'+ssPct+'%;width:1.5px;background:#E9963B;opacity:.8"></i>';
  if(noonPct!=null) sunMk += '<i style="position:absolute;top:-3px;left:'+noonPct+'%;width:2px;height:6px;margin-left:-1px;border-radius:2px;background:var(--muted)"></i>';

  var deltaHtml = '';
  if(sun.available && sun.delta_min!=null){
    var dm = sun.delta_min, up = dm>=0;
    deltaHtml = '<span class="delta '+(up?'up':'down')+'" style="font-size:11px">'+(up?'+':'−')+fmtDur(Math.abs(dm))+' vs. yesterday</span>';
  }

  var now0 = new Date();
  var jan1 = new Date(now0.getFullYear(),0,1);
  var doy = Math.floor((now0 - jan1)/86400000)+1;
  var isLeap = (now0.getFullYear()%4===0 && now0.getFullYear()%100!==0)||now0.getFullYear()%400===0;
  var yearDays = isLeap?366:365;
  var d1=new Date(Date.UTC(now0.getFullYear(),now0.getMonth(),now0.getDate()));
  var dayNum=(d1.getUTCDay()+6)%7; d1.setUTCDate(d1.getUTCDate()-dayNum+3);
  var firstTh=new Date(Date.UTC(d1.getUTCFullYear(),0,4));
  var weekNo=1+Math.round(((d1-firstTh)/86400000-3+((firstTh.getUTCDay()+6)%7))/7);

  var mon = new Date(now0); var wd=(now0.getDay()+6)%7; mon.setDate(now0.getDate()-wd); mon.setHours(0,0,0,0);
  var WL=['M','T','W','T','F','S','S'];
  var weekCells='';
  for(var i=0;i<7;i++){
    var dd=new Date(mon); dd.setDate(mon.getDate()+i);
    var isToday = dd.toDateString()===now0.toDateString();
    weekCells +=
      '<div style="flex:1;text-align:center;padding:8px 0;border-radius:11px;'+
        (isToday?'background:linear-gradient(160deg,var(--iris),var(--quick));box-shadow:0 4px 14px color-mix(in srgb,var(--iris) 35%,transparent);':'background:var(--glass-2);border:1px solid var(--hairline);')+'">'+
        '<div style="font-size:10px;font-weight:700;letter-spacing:.06em;color:'+(isToday?'rgba(255,255,255,.85)':'var(--muted)')+'">'+WL[i]+'</div>'+
        '<div class="num" style="font-size:16px;font-weight:700;margin-top:2px;color:'+(isToday?'#fff':'var(--ink)')+'">'+dd.getDate()+'</div>'+
      '</div>';
  }

  var sunStats;
  if(sun.available){
    sunStats = statGrid([
      ['Sunrise', sun.sunrise||'—'],
      ['Sunset', sun.sunset||'—'],
      ['Daylight', sun.daylight||'—'],
      ['Solar noon', sun.solar_noon||'—'],
      ['Rise tmrw', sun.tomorrow_sunrise||'—'],
      ['Set tmrw', sun.tomorrow_sunset||'—']
    ]);
  } else {
    sunStats = '<div class="hint" style="margin:6px 0">'+esc(sun.reason||'Sun times unavailable.')+'</div>';
  }

  var LBL='font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:14px 0 7px';
  var UP='<svg viewBox="0 0 24 24" width="10" height="10" style="vertical-align:-1px" fill="none" stroke="#F5B94A" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 14 12 8 18 14"/></svg>';
  var DN='<svg viewBox="0 0 24 24" width="10" height="10" style="vertical-align:-1px" fill="none" stroke="#E9963B" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 10 12 16 18 10"/></svg>';

  el.innerHTML =
    '<div style="display:flex;align-items:baseline;gap:8px;margin:6px 0 2px">'+
      '<div id="ck-time" class="num" style="font-size:60px;font-weight:750;line-height:.95;letter-spacing:-.02em">——</div>'+
      '<div style="display:flex;flex-direction:column;justify-content:flex-end;padding-bottom:6px">'+
        '<span id="ck-ampm" style="font-size:18px;font-weight:700;color:var(--iris);line-height:1"></span>'+
        '<span id="ck-sec" class="num" style="font-size:13px;color:var(--muted);font-weight:600"></span>'+
      '</div>'+
    '</div>'+
    '<div id="ck-date" style="font-size:15px;font-weight:600;color:var(--ink)"></div>'+
    '<div class="w-sub" style="margin-top:1px">'+esc(sun.tz? (sun.tz.replace(/_/g,' ')+' · '+(sun.tzabbr||'')):'')+
      '<span> · Day '+doy+' of '+yearDays+' · Week '+weekNo+'</span></div>'+

    '<div style="'+LBL+'">Day progress</div>'+
    '<div style="position:relative;height:34px;border-radius:9px;overflow:hidden;'+
        'background:linear-gradient(180deg,#0f1530,#1a2140);border:1px solid var(--hairline)">'+
      band + sunMk +
      '<i id="ck-now" style="position:absolute;top:0;bottom:0;left:0%;width:2px;margin-left:-1px;background:var(--ink);box-shadow:0 0 8px var(--ink)"></i>'+
      '<i id="ck-nowdot" style="position:absolute;top:50%;left:0%;width:9px;height:9px;margin:-4.5px 0 0 -4.5px;border-radius:50%;background:var(--ink);box-shadow:0 0 0 3px color-mix(in srgb,var(--ink) 25%,transparent)"></i>'+
    '</div>'+
    '<div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--faint);margin-top:5px" class="num">'+
      '<span>12 AM</span>'+
      (sr!=null?'<span style="color:#F5B94A">'+UP+' '+esc(sun.sunrise)+'</span>':'')+
      (ss!=null?'<span style="color:#E9963B">'+DN+' '+esc(sun.sunset)+'</span>':'')+
      '<span>12 AM</span>'+
    '</div>'+
    '<div style="display:flex;align-items:center;gap:10px;margin-top:8px;font-size:12px">'+
      '<span id="ck-count" style="font-weight:640;color:var(--ink)"></span>'+
      '<span id="ck-elapsed" class="w-sub" style="margin-left:auto"></span>'+
    '</div>'+

    '<div style="'+LBL+'">Sun · daylight'+(deltaHtml?'  '+deltaHtml:'')+'</div>'+
    sunStats +

    '<div style="'+LBL+'">This week</div>'+
    '<div style="display:flex;gap:6px">'+weekCells+'</div>';

  var tEl=el.querySelector('#ck-time'), apEl=el.querySelector('#ck-ampm'),
      secEl=el.querySelector('#ck-sec'), dEl=el.querySelector('#ck-date'),
      nowEl=el.querySelector('#ck-now'), dotEl=el.querySelector('#ck-nowdot'),
      cntEl=el.querySelector('#ck-count'), elpEl=el.querySelector('#ck-elapsed');

  function tick(){
    var pop=document.getElementById('wpop');
    if(!tEl || !tEl.isConnected || (pop && pop.hidden)){
      if(window.__ckTimer){clearInterval(window.__ckTimer);window.__ckTimer=null;}
      return;
    }
    var d=new Date();
    var h=d.getHours(), m=d.getMinutes(), s=d.getSeconds();
    var h12=h%12||12;
    tEl.textContent=h12+':'+pad(m);
    apEl.textContent=h<12?'AM':'PM';
    secEl.textContent=':'+pad(s);
    dEl.textContent=DOW[d.getDay()]+', '+MON[d.getMonth()]+' '+d.getDate()+', '+d.getFullYear();
    var nowMin=h*60+m+s/60;
    var np=(nowMin/1440)*100;
    nowEl.style.left=np+'%'; dotEl.style.left=np+'%';
    elpEl.textContent=Math.round(nowMin/1440*100)+'% of day elapsed';
    if(sr!=null && ss!=null){
      var target,label;
      if(nowMin<sr){target=sr;label='Sunrise';}
      else if(nowMin<ss){target=ss;label='Sunset';}
      else {target=sr+1440;label='Sunrise';}
      cntEl.textContent=label+' in '+fmtDur(target-nowMin);
      if(nowMin>=sr && nowMin<ss){
        var frac=Math.round((nowMin-sr)/(ss-sr)*100);
        elpEl.textContent=frac+'% of daylight · '+Math.round(nowMin/1440*100)+'% of day';
      }
    } else {
      cntEl.textContent='';
    }
  }
  if(window.__ckTimer){clearInterval(window.__ckTimer);}
  tick();
  window.__ckTimer=setInterval(tick,1000);
};


// ============================================================
// WAVE 2 — revamp fleet
// ============================================================

// ===== day_drill =====
// ===== day_drill: clickable days in weather + calendar pop-outs =====

// --- shared plumbing (namespaced _dd*) ---
function _ddRM(){try{return window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;}catch(e){return false;}}
function _ddAnim(elm,kf,op){try{const a=animate(elm,kf,op);return (a&&a.finished)?a.finished:Promise.resolve();}catch(e){return Promise.resolve();}}
function _ddOpen(body){
  if(body.dataset.anim||!body.hidden)return;
  body.hidden=false;
  if(_ddRM())return;
  const h=body.scrollHeight;
  body.dataset.anim='1';body.style.overflow='hidden';
  _ddAnim(body,{height:['0px',h+'px'],opacity:[0,1]},{duration:.3,easing:[.32,.72,0,1]})
    .then(()=>{body.style.height='';body.style.opacity='';body.style.overflow='';delete body.dataset.anim;});
}
function _ddClose(body){
  if(body.dataset.anim||body.hidden)return;
  if(_ddRM()){body.hidden=true;return;}
  const h=body.scrollHeight;
  body.dataset.anim='1';body.style.overflow='hidden';
  _ddAnim(body,{height:[h+'px','0px'],opacity:[1,0]},{duration:.24,easing:[.32,.72,0,1]})
    .then(()=>{body.hidden=true;body.style.height='';body.style.opacity='';body.style.overflow='';delete body.dataset.anim;});
}
function _dd12(hm){ // "05:30" -> "5:30a"  (12-hour, design law)
  if(!hm||hm.indexOf(':')<0)return '—';
  const p=hm.split(':');let h=+p[0];const ap=h<12?'a':'p';h=h%12||12;
  return h+':'+p[1]+ap;
}
function _ddChev(){return '<svg class="ddw-chev" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 1.5L7 5l-3.5 3.5"/></svg>';}
function _ddIco(p,color){return '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="'+color+'" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:0 0 auto">'+p+'</svg>';}
function _ddChip(ico,label,val){return '<span class="ddw-chip">'+ico+'<span style="color:var(--muted)">'+esc(label)+'</span><b class="num">'+esc(val)+'</b></span>';}
function _ddCSS(){
  if(document.getElementById('dd-style'))return;
  const st=document.createElement('style');st.id='dd-style';
  st.textContent=
    '.ddw-head{display:flex;align-items:center;gap:9px;padding:6px 4px;margin:0 -4px;border-bottom:1px solid var(--hairline);font-size:12.5px;cursor:pointer;user-select:none;-webkit-user-select:none;border-radius:8px;transition:background .15s}'+
    '.ddw-head:hover{background:var(--glass-2)}'+
    '.ddw-chev{flex:0 0 auto;margin-left:2px;color:var(--faint);transition:transform .25s cubic-bezier(.32,.72,0,1)}'+
    '.dd-open .ddw-chev{transform:rotate(90deg);color:var(--iris)}'+
    '.ddw-body,.ddc-body{overflow:hidden}'+
    '.ddw-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}'+
    '.ddw-chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;padding:3px 9px;border:1px solid var(--hairline);border-radius:9px;background:var(--chip)}'+
    '.ddw-chip b{font-weight:640}'+
    '@keyframes ddw-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}'+
    '.ddw-line{stroke-dasharray:1;stroke-dashoffset:0;animation:ddw-draw .9s cubic-bezier(.4,0,.2,1) both}'+
    '@keyframes ddw-grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}'+
    '.ddw-bar{transform-box:fill-box;transform-origin:bottom;animation:ddw-grow .5s cubic-bezier(.34,1.3,.4,1) both}'+
    '@keyframes ddw-fade{from{opacity:0}to{opacity:1}}'+
    '.ddw-area{animation:ddw-fade .8s ease .15s both}'+
    '@keyframes dd-pulse{0%,100%{opacity:.35}50%{opacity:1}}'+
    '.ddw-hidot{animation:dd-pulse 2.6s ease-in-out infinite}'+
    '.ddc-strip{display:flex;gap:2px;align-items:flex-end;margin:6px 0 8px;padding:7px 4px 5px;border:1px solid var(--hairline);border-radius:12px;background:var(--glass-2)}'+
    '.ddc-cell{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer;border-radius:8px;padding:2px 0;min-width:0}'+
    '.ddc-cell:hover{background:var(--chip)}'+
    '.ddc-lb{font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-weight:600}'+
    '@keyframes ddc-pop{from{transform:scale(0)}to{transform:scale(1)}}'+
    '.ddc-dot{transform-box:fill-box;transform-origin:center;animation:ddc-pop .45s cubic-bezier(.34,1.4,.4,1) both}'+
    '@keyframes ddc-ring{0%{transform:scale(.55);opacity:.5}75%,100%{transform:scale(1.6);opacity:0}}'+
    '.ddc-ring{transform-box:fill-box;transform-origin:center;animation:ddc-ring 2.8s ease-out infinite}'+
    '.ddc-head{display:flex;align-items:baseline;gap:8px;margin:10px -4px 0;padding:5px 4px 4px;border-bottom:1px solid var(--hairline);cursor:pointer;user-select:none;-webkit-user-select:none;border-radius:8px;transition:background .15s}'+
    '.ddc-head:hover{background:var(--glass-2)}'+
    '.ddc-head .ddw-chev{align-self:center}'+
    '.ddc-badge{margin-left:auto;font-size:10px;color:var(--muted);background:var(--hairline);border-radius:8px;padding:0 7px;line-height:16px;min-width:16px;text-align:center}'+
    '@media (prefers-reduced-motion:reduce){.ddw-line,.ddw-bar,.ddw-area,.ddw-hidot,.ddc-dot,.ddc-ring,.ddw-chev{animation:none;transition:none}.ddc-ring{opacity:0}}';
  document.head.appendChild(st);
}

// --- bespoke per-day hourly chart: smooth temp curve + precip bars + sunrise/sunset ticks (hand-drawn SVG) ---
function _ddChart(hours,sr,ss){
  if(!hours||!hours.length)return '<div class="hint" style="padding:4px 0">Hourly detail unavailable — reopens after next refresh.</div>';
  const W=320,H=104,PL=13,PR=13,PT=16,PB=26,n=hours.length;
  const temps=hours.map(o=>o.temp),lo=Math.min.apply(null,temps),hi=Math.max.apply(null,temps),span=(hi-lo)||1;
  const X=i=>PL+i*(W-PL-PR)/((n-1)||1);
  const Y=t=>PT+(hi-t)*(H-PT-PB)/span;
  let s='<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto;display:block" aria-hidden="true">';
  s+='<line x1="'+PL+'" y1="'+(PT+(H-PT-PB)/3).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+(PT+(H-PT-PB)/3).toFixed(1)+'" stroke="var(--hairline)" stroke-width="1"/>';
  s+='<line x1="'+PL+'" y1="'+(PT+2*(H-PT-PB)/3).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+(PT+2*(H-PT-PB)/3).toFixed(1)+'" stroke="var(--hairline)" stroke-width="1"/>';
  s+='<line x1="'+PL+'" y1="'+(H-PB)+'" x2="'+(W-PR)+'" y2="'+(H-PB)+'" stroke="var(--hairline)" stroke-width="1"/>';
  const bw=(W-PL-PR)/n*0.55;
  hours.forEach((o,i)=>{const bh=(o.pop||0)/100*(H-PT-PB);
    if(bh>0.5)s+='<rect class="ddw-bar" style="fill:color-mix(in srgb,var(--quick) 40%,transparent);animation-delay:'+(i*16)+'ms" x="'+(X(i)-bw/2).toFixed(1)+'" y="'+(H-PB-bh).toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+bh.toFixed(1)+'" rx="1.5"/>';});
  [[sr],[ss]].forEach(pr=>{const hm=pr[0];
    if(hm&&hm.indexOf(':')>=0&&n>1){const fh=(+hm.split(':')[0])+(+hm.split(':')[1])/60;
      const tx=PL+fh/(n-1)*(W-PL-PR);
      if(tx>=PL&&tx<=W-PR)s+='<line x1="'+tx.toFixed(1)+'" y1="'+(H-PB)+'" x2="'+tx.toFixed(1)+'" y2="'+(H-PB-5)+'" stroke="var(--warn)" stroke-width="1.4" stroke-linecap="round"/><circle cx="'+tx.toFixed(1)+'" cy="'+(H-PB-7.5)+'" r="1.6" fill="var(--warn)"/>';}});
  let d='M'+X(0).toFixed(1)+' '+Y(temps[0]).toFixed(1);
  for(let i=1;i<n;i++){const mx=((X(i-1)+X(i))/2).toFixed(1),my=((Y(temps[i-1])+Y(temps[i]))/2).toFixed(1);
    d+=' Q'+X(i-1).toFixed(1)+' '+Y(temps[i-1]).toFixed(1)+' '+mx+' '+my;}
  d+=' L'+X(n-1).toFixed(1)+' '+Y(temps[n-1]).toFixed(1);
  s+='<path class="ddw-area" d="'+d+' L'+X(n-1).toFixed(1)+' '+(H-PB)+' L'+X(0).toFixed(1)+' '+(H-PB)+' Z" style="fill:color-mix(in srgb,var(--iris) 12%,transparent)"/>';
  s+='<path class="ddw-line" pathLength="1" d="'+d+'" fill="none" stroke="var(--iris)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>';
  const iH=temps.indexOf(hi),iL=temps.indexOf(lo);
  const cl=(v,a,b)=>Math.max(a,Math.min(b,v));
  s+='<circle class="ddw-hidot" cx="'+X(iH).toFixed(1)+'" cy="'+Y(hi).toFixed(1)+'" r="2.4" fill="var(--iris)"/>';
  s+='<text x="'+cl(X(iH),16,W-16).toFixed(1)+'" y="'+cl(Y(hi)-5,9,H).toFixed(1)+'" text-anchor="middle" font-size="9" font-weight="600" fill="var(--ink)" style="font-variant-numeric:tabular-nums">'+hi+'&#176;</text>';
  s+='<text x="'+cl(X(iL),16,W-16).toFixed(1)+'" y="'+cl(Y(lo)+11,0,H-PB-2).toFixed(1)+'" text-anchor="middle" font-size="9" fill="var(--muted)" style="font-variant-numeric:tabular-nums">'+lo+'&#176;</text>';
  hours.forEach((o,i)=>{if(i%4===0)s+='<text x="'+X(i).toFixed(1)+'" y="'+(H-4)+'" text-anchor="middle" font-size="8.5" fill="var(--faint)">'+esc(o.t)+'</text>';});
  s+='</svg>';
  return s;
}
function _ddDayDetail(x){
  return '<div style="padding:8px 2px 10px">'+
    _ddChart(x.hours||[],x.sunrise,x.sunset)+
    '<div class="ddw-chips">'+
    _ddChip(_ddIco('<path d="M1.5 9.5h9"/><path d="M3.8 9.5a2.2 2.2 0 0 1 4.4 0"/><path d="M6 4.2V1M4.6 2.4L6 1l1.4 1.4"/>','var(--warn)'),'Sunrise',_dd12(x.sunrise))+
    _ddChip(_ddIco('<path d="M1.5 9.5h9"/><path d="M3.8 9.5a2.2 2.2 0 0 1 4.4 0"/><path d="M6 1v3.2M4.6 2.8L6 4.2l1.4-1.4"/>','var(--warn)'),'Sunset',_dd12(x.sunset))+
    _ddChip(_ddIco('<circle cx="6" cy="6" r="2.2"/><path d="M6 .8v1.6M6 9.6v1.6M.8 6h1.6M9.6 6h1.6M2.3 2.3l1.1 1.1M8.6 8.6l1.1 1.1M9.7 2.3L8.6 3.4M3.4 8.6L2.3 9.7"/>','var(--iris)'),'UV max',(x.uv!=null?x.uv:'—'))+
    _ddChip(_ddIco('<path d="M6 1.6S2.8 5.2 2.8 7.4a3.2 3.2 0 0 0 6.4 0C9.2 5.2 6 1.6 6 1.6z"/>','var(--quick)'),'Precip',(x.pop!=null?x.pop+'%':'—'))+
    '</div></div>';
}

// --- WEATHER: same layout, 7-day rows drill into per-day hourly detail (exclusive accordion) ---
EXPAND_RENDER.weather=function(el,d){
  _ddCSS();
  const c=d.current||{};
  let h='<div style="display:flex;align-items:center;gap:12px;margin:4px 0 2px">'+weatherGlyph(c.code,c.is_day,50)+
    '<div class="num" style="font-size:52px;font-weight:700;line-height:1">'+c.temp+'&deg;</div>'+
    '<div style="padding-bottom:4px"><div style="font-size:15px;font-weight:600">'+esc(c.desc)+'</div><div class="w-sub">Feels like '+c.feels+'&deg; &middot; '+esc(d.city||'')+'</div></div></div>';
  h+=statGrid([['Humidity',c.humidity+'%'],['Wind',c.wind+' '+c.wind_dir],['UV index',c.uv],['Pressure',c.pressure+' hPa'],['Cloud',c.cloud+'%'],['Precip',c.precip+' mm']]);
  h+='<div class="w-sub" style="margin:6px 0 4px;font-weight:600">Next 24 hours</div>';
  h+='<div style="display:flex;gap:2px;overflow-x:auto;padding-bottom:6px">'+(d.hourly||[]).map(x=>
    '<div style="flex:0 0 auto;width:44px;text-align:center;font-size:10.5px"><div class="num" style="font-weight:600">'+x.temp+'&deg;</div>'+
    '<div style="height:'+(20+x.pop*0.4)+'px;width:8px;margin:4px auto 3px;border-radius:3px;background:linear-gradient(180deg,var(--quick),color-mix(in srgb,var(--quick) 20%,transparent))"></div>'+
    '<div style="color:var(--muted)">'+x.pop+'%</div><div style="color:var(--faint);margin-top:2px">'+esc(x.t)+'</div></div>').join('')+'</div>';
  h+='<div class="w-sub" style="margin:8px 0 4px;font-weight:600">7-day forecast <span style="font-weight:500;color:var(--faint)">&middot; tap a day for hourly</span></div>';
  const days=d.daily||[];
  h+=days.map((x,i)=>{const dt=new Date(x.date+'T00:00');const dn=i===0?'Today':dt.toLocaleDateString([],{weekday:'short'});
    return '<div class="ddw-day">'+
      '<div class="ddw-head" data-i="'+i+'" role="button" tabindex="0" aria-expanded="false">'+
      '<span style="width:38px;font-weight:600">'+dn+'</span>'+weatherGlyph(x.code,true,20)+
      '<span style="flex:1;color:var(--muted);margin-left:2px">'+esc(WMO_S(x.code))+'</span>'+
      '<span class="num" style="color:var(--quick);width:40px;text-align:right">'+(x.pop>10?x.pop+'%':'')+'</span>'+
      '<span class="num" style="width:64px;text-align:right"><b>'+x.hi+'&deg;</b> <span style="color:var(--muted)">'+x.lo+'&deg;</span></span>'+
      _ddChev()+'</div>'+
      '<div class="ddw-body" hidden></div></div>';}).join('');
  el.innerHTML=h;
  el.querySelectorAll('.ddw-head').forEach(head=>{
    const act=()=>{
      const i=+head.dataset.i,body=head.nextElementSibling;
      if(!body.hidden){_ddClose(body);head.classList.remove('dd-open');head.setAttribute('aria-expanded','false');return;}
      el.querySelectorAll('.ddw-body:not([hidden])').forEach(b=>{_ddClose(b);
        const hh=b.previousElementSibling;if(hh){hh.classList.remove('dd-open');hh.setAttribute('aria-expanded','false');}});
      if(!body.dataset.built){body.innerHTML=_ddDayDetail(days[i]||{});body.dataset.built='1';}
      _ddOpen(body);head.classList.add('dd-open');head.setAttribute('aria-expanded','true');};
    head.addEventListener('click',act);
    head.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();act();}});
  });
};

// --- CALENDAR: collapsible day sections + bespoke animated day-dot strip ---
EXPAND_RENDER.today=function(el,d){
  if(!d||!d.available){el.innerHTML='<div class="hint">'+esc((d&&d.reason)||'Calendar unavailable.')+'</div>';return;}
  _ddCSS();
  const map={};(d.days||[]).forEach(x=>map[x.date]=x);
  const pad=n=>(n<10?'0':'')+n;
  const rows=[];
  for(let i=0;i<8;i++){const dt=new Date();dt.setHours(0,0,0,0);dt.setDate(dt.getDate()+i);
    const key=dt.getFullYear()+'-'+pad(dt.getMonth()+1)+'-'+pad(dt.getDate());
    rows.push({i:i,key:key,dt:dt,
      rel:i===0?'Today':i===1?'Tomorrow':dt.toLocaleDateString([],{weekday:'long'}),
      lbl:dt.toLocaleDateString([],{month:'short',day:'numeric'}),
      day:map[key],n:map[key]?map[key].events.length:0});}
  let h='<div class="w-sub" style="margin:2px 0 4px"><b>'+(d.count||0)+'</b> event'+((d.count===1)?'':'s')+' &middot; next 7 days</div>';
  const week=rows.slice(0,7),mx=Math.max.apply(null,[1].concat(week.map(x=>x.n)));
  h+='<div class="ddc-strip">'+week.map(x=>{
    const r=x.n?(3.2+(x.n/mx)*4.6):2.2,today=x.i===0;
    const fill=x.n?(today?'var(--iris)':'color-mix(in srgb,var(--iris) '+Math.round(35+55*x.n/mx)+'%,var(--faint))'):'none';
    let svg='<svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true" style="display:block">';
    if(today)svg+='<circle class="ddc-ring" cx="13" cy="13" r="8" fill="none" stroke="var(--iris)" stroke-width="1.3"/>';
    svg+='<circle class="ddc-dot" style="animation-delay:'+(x.i*55)+'ms;'+(x.n?'fill:'+fill:'fill:none;stroke:var(--faint);stroke-width:1.2')+'" cx="13" cy="13" r="'+r.toFixed(1)+'"/></svg>';
    return '<div class="ddc-cell" data-key="'+x.key+'" role="button" tabindex="0" aria-label="'+esc(x.rel)+', '+x.n+' events">'+svg+
      '<span class="ddc-lb"'+(today?' style="color:var(--iris)"':'')+'>'+esc(x.dt.toLocaleDateString([],{weekday:'narrow'}))+'</span>'+
      '<span class="num" style="font-size:9px;color:var(--muted);line-height:1">'+(x.n||'&nbsp;')+'</span></div>';
  }).join('')+'</div>';
  h+=rows.map(x=>{const open=x.i<2;
    return '<div class="ddc-day" data-key="'+x.key+'">'+
      '<div class="ddc-head'+(open?' dd-open':'')+'" role="button" tabindex="0" aria-expanded="'+open+'">'+
      '<span style="font-weight:680;font-size:13px;color:var(--iris)">'+esc(x.rel)+'</span><span class="w-sub">'+esc(x.lbl)+'</span>'+
      '<span class="ddc-badge num">'+(x.n||'0')+'</span>'+_ddChev()+'</div>'+
      '<div class="ddc-body"'+(open?'':' hidden')+'>'+
      (x.day&&x.day.events.length?
        x.day.events.map(e=>'<div class="ev"><span class="t num">'+esc(e.all_day?'all-day':(e.time+(e.end?('&ndash;'+e.end):'')))+'</span><span class="n">'+esc(e.title)+'</span></div>').join(''):
        '<div style="padding:2px 0 4px;color:var(--faint);font-size:11.5px">No events</div>')+
      '</div></div>';}).join('');
  el.innerHTML=h;
  el.querySelectorAll('.ddc-day').forEach(sec=>{
    const head=sec.querySelector('.ddc-head'),body=sec.querySelector('.ddc-body');
    const act=()=>{
      if(!body.hidden){_ddClose(body);head.classList.remove('dd-open');head.setAttribute('aria-expanded','false');}
      else{_ddOpen(body);head.classList.add('dd-open');head.setAttribute('aria-expanded','true');}};
    head.addEventListener('click',act);
    head.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();act();}});
  });
  el.querySelectorAll('.ddc-cell').forEach(cell=>{
    const go=()=>{
      const sec=el.querySelector('.ddc-day[data-key="'+cell.dataset.key+'"]');if(!sec)return;
      const head=sec.querySelector('.ddc-head'),body=sec.querySelector('.ddc-body');
      if(body.hidden){_ddOpen(body);head.classList.add('dd-open');head.setAttribute('aria-expanded','true');}
      try{sec.scrollIntoView({behavior:_ddRM()?'auto':'smooth',block:'nearest'});}catch(e){}};
    cell.addEventListener('click',go);
    cell.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}});
  });
};

// ===== news_desk =====
// ===== news desk (rss pop-out override) =====
EXPAND_RENDER.rss = function(el, data){
  if(data && data.error){el.innerHTML='<div class="hint">'+esc(data.error)+'</div>';return;}

  // one-time style injection for tabs + bespoke wire glyph animation
  if(!document.getElementById('nd-style')){
    var st=document.createElement('style'); st.id='nd-style';
    st.textContent=
      '.nd-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 2px}'+
      '.nd-tab{font-size:11px;font-weight:620;letter-spacing:.02em;padding:4px 11px;border-radius:999px;border:1px solid var(--hairline);background:transparent;color:var(--muted);cursor:pointer;font-family:inherit;transition:color .15s,background .15s,border-color .15s}'+
      '.nd-tab:hover{color:var(--ink)}'+
      '.nd-tab.on{background:color-mix(in srgb,var(--iris) 15%,transparent);border-color:color-mix(in srgb,var(--iris) 45%,transparent);color:var(--ink)}'+
      '.nd-tab .n{font-size:9.5px;color:var(--faint);margin-left:5px;font-variant-numeric:tabular-nums}'+
      '.nd-lead a{font-size:13.5px;font-weight:650;line-height:1.35;color:var(--ink);text-decoration:none}'+
      '.nd-lead a:hover{color:var(--wac)}'+
      '@keyframes ndring{0%{transform:scale(.25);opacity:.9}70%{opacity:0}100%{transform:scale(1);opacity:0}}'+
      '@keyframes ndblip{0%,100%{opacity:.55}50%{opacity:1}}'+
      '.nd-glyph .ring{transform-box:fill-box;transform-origin:center;animation:ndring 2.6s cubic-bezier(.2,.6,.4,1) infinite}'+
      '.nd-glyph .blip{animation:ndblip 2.6s ease-in-out infinite}'+
      '@media (prefers-reduced-motion:reduce){.nd-glyph .ring{animation:none;opacity:.3;transform:scale(.75)}.nd-glyph .blip{animation:none}}';
    document.head.appendChild(st);
  }

  // bespoke broadcast-mast glyph: dot atop a mast, two expanding signal rings
  function glyph(){
    return '<svg class="nd-glyph" width="30" height="26" viewBox="0 0 30 26" fill="none" aria-hidden="true" style="flex:none">'+
      '<circle class="ring" cx="15" cy="8" r="10" stroke="var(--iris)" stroke-width="1.4"/>'+
      '<circle class="ring" cx="15" cy="8" r="10" stroke="var(--iris)" stroke-width="1.4" style="animation-delay:1.3s"/>'+
      '<path d="M15 10.6 L11.4 23 M15 10.6 L18.6 23 M12.7 19 L17.3 19 M13.9 14.4 L16.1 14.4" stroke="var(--muted)" stroke-width="1.4" stroke-linecap="round"/>'+
      '<circle class="blip" cx="15" cy="8" r="2.4" fill="var(--iris)"/></svg>';
  }
  function srcLine(x){
    return '<span style="color:var(--wac);font-weight:640">'+esc(x.source||'')+'</span>'+
           (x.ts?'<span style="color:var(--faint)"> · '+relTime(x.ts)+'</span>':'');
  }
  function row(x, lead){
    if(lead) return '<div class="nd-lead" style="padding:7px 0 10px;border-bottom:1px solid var(--hairline)">'+
      '<div style="font-size:10.5px;margin-bottom:3px">'+srcLine(x)+'</div>'+
      '<a href="#" data-url="'+esc(x.url)+'">'+esc(x.title)+'</a>'+
      (x.summary?'<div style="color:var(--muted);font-size:11.5px;line-height:1.45;margin-top:4px">'+esc(x.summary)+'</div>':'')+'</div>';
    return '<div class="news-item"><div style="min-width:0">'+
      '<a href="#" data-url="'+esc(x.url)+'">'+esc(x.title)+'</a>'+
      '<div class="sub">'+srcLine(x)+'</div>'+
      (x.summary?'<div class="ds" style="color:var(--muted);font-size:11px;margin-top:3px;line-height:1.4">'+esc(x.summary)+'</div>':'')+'</div></div>';
  }

  var S=(data&&data.sections)||[];
  if(!S.length){ // legacy flat-shape fallback
    var I=(data&&data.items)||[];
    if(!I.length){el.innerHTML='<div class="hint">No stories.</div>';return;}
    el.innerHTML=I.map(function(x,i){return row(x,i===0);}).join('');
    wireLinks(el);return;
  }

  var total=S.reduce(function(a,s){return a+(s.items||[]).length;},0);
  var cur=window.__ndTab;
  if(!S.some(function(s){return s.name===cur;})) cur=S[0].name;

  el.innerHTML=
    '<div style="display:flex;align-items:center;gap:10px;margin-top:2px">'+glyph()+
      '<div style="min-width:0">'+
        '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600">News desk</div>'+
        '<div class="w-sub" style="font-size:11px"><b class="num">'+total+'</b> stories · '+S.length+' desks</div>'+
      '</div>'+
      '<span class="livedot" style="margin-left:auto"></span>'+
      '<span style="font-size:10px;letter-spacing:.06em;color:var(--muted);font-weight:600">WIRE</span>'+
    '</div>'+
    '<div class="nd-tabs">'+S.map(function(s){
      return '<button class="nd-tab'+(s.name===cur?' on':'')+'" data-nd="'+esc(s.name)+'">'+esc(s.name)+
             '<span class="n">'+(s.items||[]).length+'</span></button>';}).join('')+'</div>'+
    '<div id="nd-list"></div>';

  var list=el.querySelector('#nd-list');
  function show(name, first){
    window.__ndTab=name;
    el.querySelectorAll('.nd-tab').forEach(function(b){b.classList.toggle('on',b.dataset.nd===name);});
    var sec=null;
    for(var i=0;i<S.length;i++) if(S[i].name===name) sec=S[i];
    sec=sec||S[0];
    list.innerHTML=(sec.items||[]).map(function(x,i){return row(x,i===0);}).join('')||'<div class="hint">No stories.</div>';
    wireLinks(list);
    if(!first && typeof animate==='function' && !(typeof REDUCE!=='undefined' && REDUCE)){
      try{animate(list,{opacity:[0,1],transform:['translateY(4px)','translateY(0)']},{duration:.18,easing:'ease-out'});}catch(e){}
    }
  }
  el.querySelectorAll('.nd-tab').forEach(function(b){
    b.addEventListener('click',function(){show(b.dataset.nd,false);});
  });
  show(cur,true);
};

// ===== markets_pro =====
// ===== markets_pro — rich MARKETS pop-out =====
// Overrides EXPAND_RENDER.markets (load after expand.js).
EXPAND_RENDER.markets = function(el, d){
  if(d && d.error){ el.innerHTML = '<div class="hint">'+esc(d.error)+'</div>'; return; }
  var idx = d.indices||[], wl = d.watchlist||[];
  var tickers = (d.tickers||[]).map(function(t){return String(t).toUpperCase();});
  var starred = (d.starred||[]).map(function(t){return String(t).toUpperCase();});
  var reduce = (typeof REDUCE!=='undefined') && REDUCE;
  var animGauge = !el._mktSeen && !reduce;
  el._mktSeen = true;

  // ---------- shared bits ----------
  function label(t){ return '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:0 0 8px">'+esc(t)+'</div>'; }
  function chgTxt(q){
    var u = (q.pct||0) >= 0, sgn = u?'+':'';
    return '<span class="num" style="font-size:11.5px;font-weight:640;color:'+(u?'var(--ok)':'var(--bad)')+'">'+sgn+fmtNum(q.chg)+' &middot; '+sgn+(q.pct!=null?q.pct:0)+'%</span>';
  }
  function spark(q,h,w){
    var s = miniSpark(q.spark,(q.pct||0)>=0);
    return s ? s.replace('class="qspark"','class="qspark" style="width:'+(w||'100%')+';height:'+h+'px;display:block"') : '';
  }
  function starIc(on){
    return '<svg viewBox="0 0 24 24" width="13" height="13" style="display:block"><path d="M12 2.8l2.8 5.8 6.4.86-4.68 4.44 1.18 6.32L12 17.2l-5.7 3.02 1.18-6.32L2.8 9.46l6.4-.86z" fill="'+(on?'var(--warn)':'none')+'" stroke="'+(on?'var(--warn)':'currentColor')+'" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  }
  var xIc = '<svg viewBox="0 0 24 24" width="10" height="10" style="display:block"><line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>';
  var plusIc = '<svg viewBox="0 0 24 24" width="12" height="12" style="display:block;flex:0 0 auto"><line x1="12" y1="5" x2="12" y2="19" stroke="var(--iris)" stroke-width="2.2" stroke-linecap="round"/><line x1="5" y1="12" x2="19" y2="12" stroke="var(--iris)" stroke-width="2.2" stroke-linecap="round"/></svg>';
  function rng(lbl, lo, v, hi, u){
    if(lo==null||hi==null||v==null||!(hi>lo)) return '<div style="flex:1"></div>';
    var p = Math.max(2, Math.min(98,(v-lo)/(hi-lo)*100));
    return '<div style="display:flex;align-items:center;gap:5px;flex:1;min-width:0" class="num">'+
      '<span style="font-size:9px;color:var(--muted);letter-spacing:.05em;font-weight:600;flex:0 0 auto">'+lbl+'</span>'+
      '<span style="font-size:9.5px;color:var(--faint);flex:0 0 auto">'+fmtNum(lo)+'</span>'+
      '<div style="position:relative;flex:1;height:4px;border-radius:2px;background:var(--hairline);min-width:24px">'+
        '<i style="position:absolute;left:0;top:0;height:100%;width:'+p.toFixed(1)+'%;border-radius:2px;background:color-mix(in srgb,var(--iris) 40%,transparent)"></i>'+
        '<i style="position:absolute;left:'+p.toFixed(1)+'%;top:50%;width:7px;height:7px;border-radius:50%;background:'+(u?'var(--ok)':'var(--bad)')+';transform:translate(-50%,-50%);box-shadow:0 0 0 1.5px var(--glass-2)"></i>'+
      '</div>'+
      '<span style="font-size:9.5px;color:var(--faint);flex:0 0 auto">'+fmtNum(hi)+'</span></div>';
  }

  // ---------- freshness (12-hour time always) ----------
  var live = (d.state==='REGULAR'||d.state==='PRE'||d.state==='POST');
  var fresh = '';
  if(d.asof){
    var dt = new Date(d.asof*1000);
    var tm = dt.toLocaleTimeString([], {hour:'numeric', minute:'2-digit', hour12:true});
    fresh = d.state==='REGULAR' ? 'Live &middot; market open'
          : d.state==='PRE' ? 'Pre-market &middot; '+tm
          : d.state==='POST' ? 'After hours &middot; '+tm
          : 'At close &middot; '+dt.toLocaleDateString([], {month:'short', day:'numeric'})+', '+tm;
  }

  // ---------- breadth gauge (bespoke animated SVG arc) ----------
  var br = d.breadth || (function(){
    var a=0,de=0,f=0;
    idx.concat(wl).forEach(function(q){ if(q.error)return; var p=q.pct||0; if(p>0.05)a++; else if(p<-0.05)de++; else f++; });
    return {adv:a, dec:de, flat:f};
  })();
  var tot = Math.max(1, br.adv+br.dec+br.flat);
  var L = Math.PI*44, arcD = 'M 11 58 A 44 44 0 0 1 99 58';
  function seg(color, startF, lenF){
    if(lenF<=0) return '';
    var gap = 2.2, lenPx = Math.max(.6, lenF*L-gap), startPx = startF*L+gap/2;
    var fin = lenPx.toFixed(1)+' '+(L*2).toFixed(0);
    return '<path class="mkt-seg" d="'+arcD+'" fill="none" stroke="'+color+'" stroke-width="7" stroke-linecap="round" data-dash="'+fin+'" style="stroke-dasharray:'+(animGauge?('0 '+(L*2).toFixed(0)):fin)+';stroke-dashoffset:'+(-startPx).toFixed(1)+'"/>';
  }
  var nf = (br.adv + br.flat/2)/tot, deg = (nf*180).toFixed(1);
  var gauge = '<svg viewBox="0 0 110 66" width="110" height="66" style="flex:0 0 auto;overflow:visible" aria-label="Market breadth: '+br.adv+' advancing, '+br.dec+' declining">'+
    '<path d="'+arcD+'" fill="none" stroke="var(--hairline)" stroke-width="7" stroke-linecap="round"/>'+
    seg('var(--ok)', 0, br.adv/tot) + seg('var(--muted)', br.adv/tot, br.flat/tot) + seg('var(--bad)', (br.adv+br.flat)/tot, br.dec/tot) +
    '<g class="mkt-needle" data-deg="'+deg+'" style="transform-box:view-box;transform-origin:55px 58px;transform:rotate('+(animGauge?'0':deg)+'deg)">'+
      '<line x1="49" y1="58" x2="24" y2="58" stroke="var(--ink)" stroke-width="1.6" stroke-linecap="round" opacity=".75"/>'+
      '<circle class="mkt-tip" cx="24" cy="58" r="2.6" fill="var(--ink)"/></g>'+
    '<circle cx="55" cy="58" r="3.2" fill="none" stroke="var(--ink)" stroke-width="1.4" opacity=".8"/></svg>';

  var h = '';
  h += '<div style="display:flex;align-items:center;gap:16px;margin:2px 0 14px">'+gauge+
    '<div style="flex:1;min-width:0">'+ label('Market breadth') +
      '<div class="num" style="display:flex;align-items:baseline;gap:6px;line-height:1;flex-wrap:wrap">'+
        '<span style="font-size:23px;font-weight:720;color:var(--ok)">'+br.adv+'</span><span style="font-size:9.5px;color:var(--muted);font-weight:600;letter-spacing:.05em">ADV</span>'+
        '<span style="font-size:23px;font-weight:720;color:var(--bad);margin-left:8px">'+br.dec+'</span><span style="font-size:9.5px;color:var(--muted);font-weight:600;letter-spacing:.05em">DEC</span>'+
        (br.flat?'<span style="font-size:23px;font-weight:720;color:var(--muted);margin-left:8px">'+br.flat+'</span><span style="font-size:9.5px;color:var(--muted);font-weight:600;letter-spacing:.05em">FLAT</span>':'')+
      '</div>'+
      '<div style="font-size:11px;color:var(--muted);margin-top:5px;display:flex;align-items:center;gap:6px">'+(live?'<span class="livedot"></span>':'')+fresh+
        '<span style="color:var(--faint)">&middot; '+(idx.length+wl.length)+' symbols</span></div>'+
    '</div></div>';

  // ---------- indices ----------
  var tileCss = 'background:var(--glass-2);border:1px solid var(--hairline);border-radius:12px;padding:9px 10px;min-width:0';
  h += label('Indices');
  h += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:14px">';
  h += idx.map(function(q){
    if(q.error) return '<div style="'+tileCss+'"><div style="font-weight:660">'+esc(q.symbol)+'</div><div class="w-sub" style="color:var(--faint)">unavailable</div></div>';
    return '<div style="'+tileCss+'">'+
      '<div style="display:flex;align-items:baseline;gap:6px">'+
        '<span style="font-weight:720;font-size:12px">'+esc(q.symbol)+'</span>'+
        '<span class="w-sub" style="font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(q.friendly||q.name)+'</span></div>'+
      '<div class="num" style="font-size:17px;font-weight:700;line-height:1.15;margin:3px 0 1px">'+fmtNum(q.price)+'</div>'+
      '<div style="margin-bottom:3px">'+chgTxt(q)+'</div>'+ spark(q,22) +'</div>';
  }).join('');
  h += '</div>';

  // ---------- watchlist header: label + status + search ----------
  h += '<div style="display:flex;align-items:center;gap:10px;margin:0 0 4px">'+
    '<span style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;flex:0 0 auto">Watchlist</span>'+
    '<span id="mkt-status" class="num" style="font-size:10.5px;color:var(--iris);font-weight:640"></span>'+
    '<div style="position:relative;margin-left:auto;flex:0 1 250px;min-width:150px">'+
      '<svg viewBox="0 0 24 24" width="12" height="12" style="position:absolute;left:9px;top:50%;transform:translateY(-50%);pointer-events:none"><circle cx="11" cy="11" r="7" fill="none" stroke="var(--muted)" stroke-width="2"/><line x1="16.5" y1="16.5" x2="21" y2="21" stroke="var(--muted)" stroke-width="2" stroke-linecap="round"/></svg>'+
      '<input id="mkt-q" placeholder="Add symbol&hellip;" autocomplete="off" spellcheck="false" style="width:100%;box-sizing:border-box;padding:5px 9px 5px 27px;font:inherit;font-size:12px;background:var(--glass-2);border:1px solid var(--hairline);border-radius:8px;color:var(--ink);outline:none">'+
      '<div id="mkt-dd" class="mkt-dd" hidden></div>'+
    '</div></div>';

  // ---------- watchlist rows ----------
  h += wl.map(function(q){
    var sym = esc(q.symbol);
    var xBtn = q.removable ? '<button class="mkt-x" data-del="'+sym+'" title="Remove '+sym+'" aria-label="Remove '+sym+'">'+xIc+'</button>' : '<span style="width:16px;flex:0 0 16px"></span>';
    if(q.error) return '<div class="mkt-row" style="display:flex;align-items:center;gap:8px">'+
      '<span style="width:18px;flex:0 0 18px"></span><span style="font-weight:700;font-size:13px;min-width:52px">'+sym+'</span>'+
      '<span class="w-sub" style="flex:1;color:var(--faint)">quote unavailable</span>'+xBtn+'</div>';
    var u = (q.pct||0) >= 0;
    var starBtn = '<button class="mkt-star" data-star="'+sym+'" title="'+(q.starred?'Unpin':'Pin to top')+'" aria-label="'+(q.starred?'Unstar':'Star')+' '+sym+'" '+(q.starred?'data-on="1"':'')+'>'+starIc(q.starred)+'</button>';
    return '<div class="mkt-row">'+
      '<div style="display:flex;align-items:center;gap:8px;min-width:0">'+ starBtn +
        '<span style="font-weight:720;font-size:13px;min-width:52px;flex:0 0 auto">'+sym+'</span>'+
        '<span class="w-sub" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px">'+esc(q.name)+'</span>'+
        '<span style="flex:0 0 66px">'+spark(q,20,'66px')+'</span>'+
        '<span class="num" style="font-weight:680;font-size:13.5px;flex:0 0 auto">'+fmtNum(q.price)+'</span>'+
        '<span style="flex:0 0 118px;text-align:right">'+chgTxt(q)+'</span>'+ xBtn +'</div>'+
      '<div style="display:flex;gap:14px;align-items:center;margin-top:4px;padding-left:26px">'+
        rng('D', q.day_lo, q.price, q.day_hi, u) + rng('52W', q.wk_lo, q.price, q.wk_hi, u) +
        '<span class="num" style="font-size:9.5px;color:var(--muted);flex:0 0 auto">VOL '+(q.vol!=null?kfmt(q.vol):'&mdash;')+'</span>'+
      '</div></div>';
  }).join('') || '<div class="hint">No symbols yet — search above to add one.</div>';

  el.innerHTML = h;

  // ---------- gauge sweep-in (once per pop-out; skipped under reduced motion) ----------
  if(animGauge){
    requestAnimationFrame(function(){ requestAnimationFrame(function(){
      el.querySelectorAll('.mkt-seg').forEach(function(p){ p.style.strokeDasharray = p.dataset.dash; });
      var n = el.querySelector('.mkt-needle'); if(n) n.style.transform = 'rotate('+n.dataset.deg+'deg)';
    }); });
  }

  // ---------- interactivity ----------
  var dd = el.querySelector('#mkt-dd');
  var deb = null, sel = -1, items = [];
  function closeDD(){ if(dd){ dd.hidden = true; dd.innerHTML=''; } sel=-1; items=[]; }
  function post(patch){
    return fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch)});
  }
  function busy(msg){
    var st = el.querySelector('#mkt-status'); if(st) st.textContent = msg||'';
    var inp = el.querySelector('#mkt-q'); if(inp) inp.disabled = !!msg;
  }
  function reload(){
    fetch('/api/expand?id=markets').then(function(r){return r.json();})
      .then(function(nd){ EXPAND_RENDER.markets(el, nd); })
      .catch(function(){ busy(''); });
  }
  function addTicker(sym){
    sym = String(sym||'').toUpperCase();
    if(!sym || tickers.indexOf(sym)>=0) { closeDD(); return; }
    tickers.unshift(sym); if(tickers.length>20) tickers.length = 20;
    el._mktQ=''; closeDD(); busy('Adding '+sym+'…');
    post({tickers:tickers}).then(reload).catch(function(){ busy(''); });
  }
  function removeTicker(sym){
    tickers = tickers.filter(function(x){return x!==sym;});
    starred = starred.filter(function(x){return x!==sym;});
    busy('Removing '+sym+'…');
    post({tickers:tickers, starred_tickers:starred}).then(reload).catch(function(){ busy(''); });
  }
  function toggleStar(sym){
    var i = starred.indexOf(sym);
    if(i>=0) starred.splice(i,1); else starred.unshift(sym);
    post({starred_tickers:starred});          // persist in background
    // optimistic local re-order: starred pinned first (in star order)
    var by = {};
    wl.forEach(function(q){ by[q.symbol]=q; q.starred = starred.indexOf(q.symbol)>=0; });
    var head = starred.filter(function(s){return by[s];}).map(function(s){return by[s];});
    var rest = wl.filter(function(q){return !q.starred;});
    d.watchlist = head.concat(rest); d.starred = starred; d.tickers = tickers;
    EXPAND_RENDER.markets(el, d);
  }
  el.querySelectorAll('[data-star]').forEach(function(b){ b.onclick = function(){ toggleStar(b.dataset.star); }; });
  el.querySelectorAll('[data-del]').forEach(function(b){ b.onclick = function(){ removeTicker(b.dataset.del); }; });

  // ---------- search (debounced, keyboard-navigable) ----------
  var input = el.querySelector('#mkt-q');
  if(input){
    input.value = el._mktQ || '';
    var have = {};
    wl.forEach(function(q){ have[q.symbol]=1; }); idx.forEach(function(q){ have[q.symbol]=1; }); tickers.forEach(function(t){ have[t]=1; });
    function markSel(){
      dd.querySelectorAll('.mkt-dd-item').forEach(function(n,i){ n.classList.toggle('sel', i===sel); });
    }
    function renderDD(r){
      items = r.results||[];
      if(r.error){ dd.innerHTML = '<div style="padding:8px 11px;font-size:11px;color:var(--muted)">'+esc(r.error)+'</div>'; dd.hidden=false; return; }
      if(!items.length){ dd.innerHTML = '<div style="padding:8px 11px;font-size:11px;color:var(--muted)">No equity or ETF matches for &ldquo;'+esc(r.q)+'&rdquo;</div>'; dd.hidden=false; return; }
      dd.innerHTML = items.map(function(it,i){
        var dup = have[it.symbol];
        return '<div class="mkt-dd-item'+(dup?' dim':'')+'" data-i="'+i+'">'+
          '<span class="num" style="font-weight:700;min-width:56px;flex:0 0 auto">'+esc(it.symbol)+'</span>'+
          '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted)">'+esc(it.name)+'</span>'+
          '<span style="font-size:9.5px;color:var(--faint);letter-spacing:.04em;flex:0 0 auto">'+esc(it.exch)+(it.type==='ETF'?' &middot; ETF':'')+'</span>'+
          (dup ? '<span style="font-size:9.5px;color:var(--ok);font-weight:650;flex:0 0 auto">ADDED</span>' : plusIc)+
        '</div>';
      }).join('');
      dd.hidden = false; sel = -1;
      dd.querySelectorAll('.mkt-dd-item').forEach(function(node){
        node.onmousedown = function(e){
          e.preventDefault();
          var it = items[+node.dataset.i];
          if(it && !have[it.symbol]) addTicker(it.symbol);
        };
      });
    }
    input.oninput = function(){
      el._mktQ = input.value;
      clearTimeout(deb);
      var q = input.value.trim();
      if(!q){ closeDD(); return; }
      deb = setTimeout(function(){
        fetch('/api/markets/search?q='+encodeURIComponent(q))
          .then(function(r){ return r.json(); })
          .then(function(r){ if(input.value.trim().toLowerCase() === String(r.q||'').toLowerCase()) renderDD(r); })
          .catch(function(){ closeDD(); });
      }, 260);
    };
    input.onkeydown = function(e){
      if(e.key==='Escape'){ closeDD(); input.blur(); }
      else if(e.key==='ArrowDown' && items.length){ sel = (sel+1)%items.length; markSel(); e.preventDefault(); }
      else if(e.key==='ArrowUp' && items.length){ sel = (sel-1+items.length)%items.length; markSel(); e.preventDefault(); }
      else if(e.key==='Enter' && items.length){
        var i2 = sel>=0 ? sel : 0;
        while(i2 < items.length && have[items[i2].symbol]) i2++;
        if(sel>=0 && have[items[sel].symbol]) i2 = items.length;   // explicit dup pick: no-op
        if(i2 < items.length) addTicker(items[i2].symbol);
        e.preventDefault();
      }
    };
    input.onblur = function(){ setTimeout(closeDD, 160); };
  }
};

// ===== system_pro =====
// system_pro — live-graphing SYSTEM pop-out (overrides EXPAND_RENDER.system)
EXPAND_RENDER.system = (function(){
  var RM = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  function cssv(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function hexA(c,a){
    c=(c||'').trim();
    if(c[0]==='#'&&c.length===7){
      return 'rgba('+parseInt(c.slice(1,3),16)+','+parseInt(c.slice(3,5),16)+','+parseInt(c.slice(5,7),16)+','+a+')';
    }
    return c;
  }
  var SEC_S='font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:650';
  function sec(t){ return '<div style="'+SEC_S+';margin:10px 0 4px">'+esc(t)+'</div>'; }
  function fmtRate(k){ k=+k||0; return k>=1024 ? (k/1024).toFixed(k>=10240?0:1)+' MB/s' : k.toFixed(k>=100?0:1)+' KB/s'; }
  function t12(ts){ var d=new Date(ts*1000),h=d.getHours(),m=d.getMinutes(),ap=h>=12?'PM':'AM'; h=h%12||12; return h+':'+(m<10?'0':'')+m+' '+ap; }
  function upTxt(hr){ if(hr==null)return '—'; if(hr<24)return hr.toFixed(1)+' h'; var dd=Math.floor(hr/24); return dd+'d '+Math.round(hr-dd*24)+'h'; }
  function niceMax(v){ if(v<=10)return 10; var p=Math.pow(10,Math.floor(Math.log10(v))),n=v/p; return (n<=1?1:n<=2?2:n<=5?5:10)*p; }
  var GC=2*Math.PI*17;

  function gaugeHTML(gid,label){
    return '<div style="text-align:center;padding:4px 2px 2px">'
      +'<div style="position:relative;width:62px;height:62px;margin:0 auto">'
      +'<svg viewBox="0 0 44 44" width="62" height="62" style="transform:rotate(-90deg);display:block">'
      +'<circle cx="22" cy="22" r="17" fill="none" stroke="var(--hairline)" stroke-width="4"/>'
      +'<circle class="sp-arc" id="'+gid+'" cx="22" cy="22" r="17" fill="none" stroke="var(--iris)" stroke-width="4" stroke-linecap="round" stroke-dasharray="0 '+GC.toFixed(1)+'"'
      +(RM?'':' style="transition:stroke-dasharray .9s cubic-bezier(.22,1,.36,1),stroke .4s linear"')+'/></svg>'
      +'<div id="'+gid+'-v" class="num" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;font-variant-numeric:tabular-nums">—</div></div>'
      +'<div style="'+SEC_S+';margin-top:3px">'+esc(label)+'</div>'
      +'<div id="'+gid+'-s" class="num" style="font-size:10px;color:var(--faint);margin-top:1px;white-space:nowrap">&nbsp;</div></div>';
  }
  function setGauge(root,gid,pct,sub){
    var c=root.querySelector('#'+gid); if(!c)return;
    var has=(pct!=null&&isFinite(pct)), p=has?Math.max(0,Math.min(100,+pct)):0;
    c.setAttribute('stroke-dasharray',(p/100*GC).toFixed(1)+' '+GC.toFixed(1));
    c.setAttribute('stroke', p>=85?'var(--bad)':p>=65?'var(--warn)':'var(--iris)');
    root.querySelector('#'+gid+'-v').textContent = has?Math.round(p)+'%':'—';
    var s=root.querySelector('#'+gid+'-s'); if(s)s.innerHTML=sub?esc(sub):'&nbsp;';
  }

  // canvas chart: fixed 30-min window anchored right, retina-scaled (dpr)
  function drawChart(cv,hist,series,ymaxOpt){
    var dpr=Math.max(1,window.devicePixelRatio||1);
    var w=cv.clientWidth,h=cv.clientHeight;
    if(!w||!h)return null;
    if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr);}
    var ctx=cv.getContext('2d');
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
    var ts=hist.ts||[]; if(!ts.length)return null;
    var now=ts[ts.length-1],SPAN=1800,pad=3;
    var X=function(t){ return w-1-((now-t)/SPAN)*(w-2); };
    var ymax=ymaxOpt;
    if(ymax==='auto'){var mx=0;series.forEach(function(s){(hist[s.key]||[]).forEach(function(v){if(v!=null&&v>mx)mx=v;});});ymax=niceMax(mx||1);}
    var Y=function(v){ return h-pad-(Math.max(0,Math.min(ymax,+v||0))/ymax)*(h-2*pad); };
    ctx.strokeStyle=cssv('--hairline')||'rgba(128,128,128,.14)';ctx.lineWidth=1;
    [0.25,0.5,0.75].forEach(function(f){var y=Math.round(pad+(h-2*pad)*f)+0.5;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();});
    ctx.font='9px -apple-system,system-ui,sans-serif';ctx.fillStyle=cssv('--faint')||'#888';
    ctx.fillText(ymaxOpt==='auto'?fmtRate(ymax):ymax+'%',4,10);
    var last=[];
    series.forEach(function(s){
      var vs=hist[s.key]||[],col=cssv(s.cvar)||'#5B63E6';
      function trace(){var st=false;ctx.beginPath();
        for(var i=0;i<ts.length;i++){var v=vs[i];if(v==null)continue;var x=X(ts[i]),y=Y(v);
          if(!st){ctx.moveTo(x,y);st=true;}else ctx.lineTo(x,y);}
        return st;}
      if(!trace())return;
      if(s.fill){
        var fi=0;while(fi<ts.length&&vs[fi]==null)fi++;
        var li=vs.length-1;while(li>=0&&vs[li]==null)li--;
        ctx.lineTo(X(ts[li]),h-pad);ctx.lineTo(X(ts[fi]),h-pad);ctx.closePath();
        var gp=ctx.createLinearGradient(0,0,0,h);
        gp.addColorStop(0,hexA(col,0.30));gp.addColorStop(1,hexA(col,0.02));
        ctx.fillStyle=gp;ctx.fill();
        trace();
      }
      ctx.strokeStyle=col;ctx.lineWidth=2;ctx.lineJoin='round';ctx.lineCap='round';ctx.stroke();
      var lj=vs.length-1;while(lj>=0&&vs[lj]==null)lj--;
      if(lj>=0){ctx.fillStyle=col;ctx.beginPath();ctx.arc(X(ts[lj]),Y(vs[lj]),2.4,0,7);ctx.fill();
        last.push({x:X(ts[lj]),y:Y(vs[lj]),col:col});}
    });
    return last;
  }
  function chartCard(id,title,h,legend){
    return '<div style="background:var(--glass-2);border:1px solid var(--hairline);border-radius:10px;padding:7px 10px 6px;min-width:0">'
      +'<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px"><span style="'+SEC_S+'">'+esc(title)+'</span>'
      +(legend||'')+'<span id="'+id+'-now" class="num" style="margin-left:auto;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums"></span></div>'
      +'<div style="position:relative"><canvas id="'+id+'" style="display:block;width:100%;height:'+h+'px"></canvas>'
      +'<div id="'+id+'-dots" style="position:absolute;inset:0;pointer-events:none"></div></div></div>';
  }
  function setDots(el,id,pts){
    var lay=el.querySelector('#'+id+'-dots'); if(!lay)return;
    lay.innerHTML=(pts||[]).map(function(p){
      return '<span style="position:absolute;left:'+(p.x-3)+'px;top:'+(p.y-3)+'px;width:6px;height:6px;border-radius:50%;'
        +'background:'+p.col+';box-shadow:0 0 0 3px '+hexA(p.col,0.20)+';'
        +(RM?'':'animation:lpulse 2s ease-in-out infinite')+'"></span>';}).join('');
  }
  function lg(cvar,txt,vid){
    return '<span style="display:inline-flex;align-items:center;gap:4px;font-size:10px;color:var(--muted)">'
      +'<i style="width:8px;height:8px;border-radius:2px;background:var(--'+cvar+')"></i>'+txt
      +' <b id="'+vid+'" class="num" style="color:var(--ink);font-variant-numeric:tabular-nums"></b></span>';
  }

  return function(el,d){
    if(window.__sysProTimer){clearInterval(window.__sysProTimer);window.__sysProTimer=null;}
    el.innerHTML=
      '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1.35fr;gap:6px;align-items:start;margin-top:2px">'
      + gaugeHTML('sp-g-cpu','CPU') + gaugeHTML('sp-g-ram','Memory') + gaugeHTML('sp-g-dsk','Disk')
      +'<div id="sp-info" style="display:flex;flex-direction:column;gap:5px;padding:6px 0 0 6px;border-left:1px solid var(--hairline)"></div></div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">'
      + chartCard('sp-cv-cpu','CPU · 30 min',56) + chartCard('sp-cv-ram','Memory · 30 min',56) +'</div>'
      +'<div style="margin-top:8px">'+chartCard('sp-cv-net','Network',72,
        lg('iris','IN','sp-in-now')+lg('quick','OUT','sp-out-now'))+'</div>'
      +'<div id="sp-x" class="num" style="display:flex;justify-content:space-between;font-size:9.5px;color:var(--faint);padding:2px 2px 0"></div>'
      +'<div id="sp-tops" style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px"></div>'
      +'<div id="sp-memsto" style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px"></div>'
      +'<div id="sp-nettot" class="w-sub" style="margin-top:8px"></div>';

    var tries=0;
    function paint(data){
      if(!el.isConnected)return;
      if(!el.clientWidth&&tries<40){tries++;requestAnimationFrame(function(){paint(data);});return;}
      tries=0;
      var s=data.sys||{},m=data.mem||{},hist=data.history||{},n=hist.cpu?hist.cpu.length:0;
      var cpuNow=n?hist.cpu[n-1]:s.cpu_pct, ramNow=n?hist.ram[n-1]:s.ram_pct;
      var inNow=n?hist.net_in_kbs[n-1]:null, outNow=n?hist.net_out_kbs[n-1]:null;
      setGauge(el,'sp-g-cpu',cpuNow,(data.load&&data.load.length?'load '+data.load[0]:''));
      setGauge(el,'sp-g-ram',ramNow,(m.total?Math.round(m.total*(ramNow||0)/100)+' / '+m.total+' GB':''));
      setGauge(el,'sp-g-dsk',s.disk_pct,(s.disk_free_gb!=null?s.disk_free_gb+' GB free':''));
      var inf=[['Load',data.load&&data.load.length?data.load.map(function(x){return x.toFixed(2);}).join(' / '):'—'],
               ['Uptime',upTxt(data.uptime_hr)],['Cores',data.cores!=null?data.cores:'—'],
               ['Samples',n+' · 5s']];
      el.querySelector('#sp-info').innerHTML=inf.map(function(p){
        return '<div style="display:flex;justify-content:space-between;gap:8px;font-size:11px">'
          +'<span style="'+SEC_S+'">'+p[0]+'</span><span class="num" style="font-weight:640;font-variant-numeric:tabular-nums">'+esc(p[1])+'</span></div>';}).join('');
      setDots(el,'sp-cv-cpu',drawChart(el.querySelector('#sp-cv-cpu'),hist,[{key:'cpu',cvar:'--iris',fill:1}],100));
      setDots(el,'sp-cv-ram',drawChart(el.querySelector('#sp-cv-ram'),hist,[{key:'ram',cvar:'--quick',fill:1}],100));
      setDots(el,'sp-cv-net',drawChart(el.querySelector('#sp-cv-net'),hist,
        [{key:'net_in_kbs',cvar:'--iris'},{key:'net_out_kbs',cvar:'--quick'}],'auto'));
      el.querySelector('#sp-cv-cpu-now').textContent=cpuNow!=null?cpuNow.toFixed(1)+'%':'—';
      el.querySelector('#sp-cv-ram-now').textContent=ramNow!=null?ramNow.toFixed(1)+'%':'—';
      el.querySelector('#sp-in-now').textContent=inNow!=null?fmtRate(inNow):'—';
      el.querySelector('#sp-out-now').textContent=outNow!=null?fmtRate(outNow):'—';
      var now=n?hist.ts[n-1]:Math.floor(Date.now()/1000);
      el.querySelector('#sp-x').innerHTML='<span>'+t12(now-1800)+'</span><span>'+t12(now-900)+'</span><span>'+t12(now)+'</span>';
      el.querySelector('#sp-tops').innerHTML=
        '<div>'+sec('Top by CPU')+barRows((data.cpu_top||[]).map(function(p){return {label:p.name,val:p.cpu,sub:p.cpu.toFixed(0)+'%',mono:1};}),118,40)+'</div>'
       +'<div>'+sec('Top by memory')+barRows((data.mem_top||[]).map(function(p){return {label:p.name,val:p.mem,sub:p.mem.toFixed(1)+'%',mono:1};}),118,40)+'</div>';
      var ms='';
      if(m.total)ms+='<div>'+sec('Memory · '+m.total+' GB')
        +barRows([{label:'Wired',val:m.wired,sub:m.wired+'G'},{label:'Active',val:m.active,sub:m.active+'G'},
                  {label:'Compressed',val:m.compressed,sub:m.compressed+'G'},{label:'Free',val:m.free,sub:m.free+'G'}],86,40)+'</div>';
      if(data.disks&&data.disks.length)ms+='<div>'+sec('Storage')
        +barRows(data.disks.map(function(v){return {label:v.name,val:v.pct,sub:v.used_gb+'/'+v.total_gb+'G'};}),86,64)+'</div>';
      el.querySelector('#sp-memsto').innerHTML=ms;
      el.querySelector('#sp-nettot').innerHTML=(data.net&&data.net.in_gb!=null)
        ?'Network since boot: <b class="num">'+data.net.in_gb+' GB</b> in · <b class="num">'+data.net.out_gb+' GB</b> out':'';
    }
    requestAnimationFrame(function(){paint(d);});
    window.__sysProTimer=setInterval(function(){
      if(!el.isConnected){clearInterval(window.__sysProTimer);window.__sysProTimer=null;return;}
      fetch('/api/expand?id=system').then(function(r){return r.json();}).then(function(nd){
        if(el.isConnected&&nd&&nd.history)paint(nd);}).catch(function(){});
    },5000);
  };
})();

// ===== console_pro =====
// ---------- console pro: stat chips + 24h activity chart + filterable linked timeline ----------
var CPRO = window.CPRO = window.CPRO || {src:'all', tool:'', data:null, sj:'', hj:''};
var CP_MONO = ['terminal','process','execute_code','read_file','write_file','patch','search_files'];
var CP_SRC_COLOR = {cli:'var(--iris)', hub:'var(--ok)', telegram:'var(--quick)'};

function cpHourLabel(i){var h=new Date(Date.now()-(23-i)*36e5).getHours();return (((h+11)%12)+1)+(h<12?'a':'p');}

function cpRenderStats(st){
  var el=$('cp-stats');if(!el)return;
  var bs=st.by_source||{};
  var h='<div class="cp-chip cp-hero"><span class="cp-big num">'+fmtNum(st.today_calls||0)+'</span>'+
    '<span class="cp-cap">calls<br>today</span></div>';
  var srcs=['cli','hub','telegram'];
  Object.keys(bs).forEach(function(s){if(srcs.indexOf(s)<0)srcs.push(s);});
  srcs.forEach(function(s){h+='<div class="cp-chip" title="'+esc(bs[s]||0)+' calls today via '+esc(s)+'">'+
    '<i class="cp-dot" style="background:'+(CP_SRC_COLOR[s]||'var(--muted)')+'"></i>'+
    '<span class="cp-cap">'+esc(s)+'</span><b class="num">'+fmtNum(bs[s]||0)+'</b></div>';});
  if(st.active_sessions!=null)h+='<div class="cp-chip" title="Sessions with activity in the last 15 minutes">'+
    (st.active_sessions?'<span class="livedot"></span>':'<i class="cp-dot" style="background:var(--faint)"></i>')+
    '<span class="cp-cap">active</span><b class="num">'+fmtNum(st.active_sessions)+'</b></div>';
  var bt=st.by_tool||[];
  if(bt.length){h+='<div class="cp-tools">'+bt.map(function(t){
    return '<button class="cp-toolchip" data-tool="'+esc(t.name)+'" title="'+esc(t.count)+
      ' calls in 24h — click to filter">'+toolIcon(t.name)+'<span class="cp-tn">'+esc(t.name)+
      '</span><b class="num">'+esc(t.count)+'</b></button>';}).join('')+'</div>';}
  el.innerHTML=h;
  el.querySelectorAll('.cp-toolchip').forEach(function(b){b.onclick=function(){
    var q=(CPRO.tool===b.dataset.tool)?'':b.dataset.tool;CPRO.tool=q;
    var inp=document.getElementById('cp-toolq');if(inp)inp.value=q;
    cpRows();};});
}

function cpRenderChart(st){
  var el=$('cp-chart');if(!el)return;
  var hist=(st.histogram||[]).slice(-24);while(hist.length<24)hist.unshift(0);
  var labels=(st.hist_labels&&st.hist_labels.length===24)?st.hist_labels
    :hist.map(function(_,i){return cpHourLabel(i);});
  var max=1;hist.forEach(function(v){if(v>max)max=v;});
  var W=480,H=74,base=58;
  var s='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Tool calls per hour, last 24 hours">';
  s+='<line class="cp-base" x1="2" y1="'+base+'" x2="'+(W-2)+'" y2="'+base+'"/>';
  for(var i=0;i<24;i++){
    var v=hist[i]||0,bh=v?Math.max(3,Math.round(v/max*50)):1.5,x=i*20+4,y=base-bh;
    s+='<rect class="cp-bar'+(i===23?' now':'')+(v?'':' z')+'" x="'+x+'" y="'+y+
      '" width="12" height="'+bh+'" rx="2.5" style="animation-delay:'+(i*16)+'ms" '+
      'title="'+v+' · '+esc(labels[i])+'"><title>'+v+(v===1?' call':' calls')+
      ' · '+esc(labels[i])+'</title></rect>';
    if((23-i)%6===0)s+='<text class="cp-x" x="'+(x+6)+'" y="'+(H-3)+'" text-anchor="middle">'+esc(labels[i])+'</text>';
  }
  el.innerHTML=s+'</svg>';
}

function cpBuildFilters(st){
  var bar=$('cp-filters');if(!bar||bar.dataset.built)return;bar.dataset.built='1';
  var srcs=['cli','hub','telegram'];
  Object.keys((st&&st.by_source)||{}).forEach(function(s){if(srcs.indexOf(s)<0)srcs.push(s);});
  var h='<button class="cp-f'+(CPRO.src==='all'?' on':'')+'" data-src="all">All</button>';
  srcs.forEach(function(s){h+='<button class="cp-f'+(CPRO.src===s?' on':'')+'" data-src="'+esc(s)+'">'+
    '<i class="cp-dot" style="background:'+(CP_SRC_COLOR[s]||'var(--muted)')+'"></i>'+esc(s)+
    '<span class="cp-fc num" data-fc="'+esc(s)+'"></span></button>';});
  h+='<input id="cp-toolq" type="search" placeholder="tool name…" spellcheck="false" '+
    'autocomplete="off" aria-label="Filter timeline by tool name" value="'+esc(CPRO.tool)+'">';
  bar.innerHTML=h;
  bar.querySelectorAll('.cp-f').forEach(function(b){b.onclick=function(){
    CPRO.src=b.dataset.src;
    bar.querySelectorAll('.cp-f').forEach(function(x){x.classList.toggle('on',x===b);});
    cpRows();};});
  var inp=bar.querySelector('#cp-toolq');
  inp.oninput=function(){CPRO.tool=inp.value;cpRows();};
}

function cpRow(e){
  var mono=CP_MONO.some(function(m){return (e.tool||'').indexOf(m)===0;});
  return '<div class="tl-row" data-kind="'+esc(e.kind)+'"><span class="tl-ic">'+toolIcon(e.tool)+'</span>'+
    '<div class="tl-body"><div class="tl-head"><span class="tl-tool">'+esc(e.tool)+'</span>'+
    '<span class="tl-src src-'+esc(e.source)+'">'+esc(e.source)+'</span>'+
    '<span class="tl-kind">'+(e.kind==='call'?'→ called':'✓ result')+'</span>'+
    '<span class="tl-when">'+(e.ts?relTime(e.ts):'')+'</span></div>'+
    (e.detail?'<div class="tl-detail'+(mono?' mono':'')+'">'+esc(e.detail)+'</div>':'')+
    '</div></div>';
}

function cpRows(){
  var el=$('console-timeline');if(!el||!CPRO.data)return;
  var d=CPRO.data,ev=d.events||[];
  if(CPRO.src!=='all')ev=ev.filter(function(e){return e.source===CPRO.src;});
  var q=(CPRO.tool||'').trim().toLowerCase();
  if(q)ev=ev.filter(function(e){return (e.tool||'').toLowerCase().indexOf(q)>=0;});
  if(!ev.length){
    el.innerHTML='<div class="hint">'+((d.events||[]).length
      ?'Nothing matches this filter.'
      :'No tool activity yet. When Hermes runs anything — a terminal command, a web search, computer use, reading a file — it streams here in real time, from every surface (this dashboard, Telegram, and the CLI).')+'</div>';
    return;
  }
  var h='';
  for(var i=0;i<ev.length;i++){
    var e=ev[i],nx=ev[i+1];
    // newest-first feed: a result directly above its own call = one linked pair
    if(e.kind==='result'&&nx&&nx.kind==='call'&&nx.tool===e.tool&&nx.source===e.source){
      h+='<div class="tl-pair">'+cpRow(e)+cpRow(nx)+'</div>';i++;
    }else h+=cpRow(e);
  }
  el.innerHTML=h;
}

async function loadConsole(){
  var d;try{d=await(await fetch('/api/console')).json();}catch(e){return;}
  CPRO.data=d;
  var st=d.stats||{};
  cpBuildFilters(st);                 // built once; input value + focus persist across polls
  var sj=JSON.stringify([st.today_calls,st.active_sessions,st.by_source,st.by_tool]);
  if(sj!==CPRO.sj){CPRO.sj=sj;cpRenderStats(st);}
  var hj=JSON.stringify(st.histogram||[]);
  if(hj!==CPRO.hj){CPRO.hj=hj;cpRenderChart(st);}   // rebuild (and re-animate) only when data moves
  var bs=st.by_source||{};
  document.querySelectorAll('#cp-filters [data-fc]').forEach(function(x){
    x.textContent=fmtNum(bs[x.dataset.fc]||0);});
  cpRows();                           // always re-render rows so relTime stays fresh; filters preserved
}

// ===== mind_pro =====
// ===== Mind view extras: skills-in-action, fuel, model mix =====
async function mindExtras(){
  let d;
  try{ d=await (await fetch('/api/mind_extra')).json(); }catch(e){ return; }
  const grid=document.getElementById('view-mind');
  if(!grid||!d) return;
  // duplicate-append guard on re-entry
  grid.querySelectorAll('[id^="mind-extra-"]').forEach(n=>n.remove());
  const RM=(typeof REDUCE!=='undefined')?REDUCE:matchMedia('(prefers-reduced-motion:reduce)').matches;
  const MN=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  function addCard(id,span2,iconPath,title,tiny,bodyHtml){
    const s=document.createElement('section');
    s.className='card glass'+(span2?' span2':'');
    s.id=id;
    s.innerHTML='<h2><svg class="ic" viewBox="0 0 24 24">'+iconPath+'</svg>'+title+
      ' <span class="tiny" style="margin-left:auto">'+tiny+'</span></h2><div class="body">'+bodyHtml+'</div>';
    grid.appendChild(s);
    return s;
  }

  // ---------- (a) Skills in action: leaderboard with bespoke rank medals ----------
  function medal(r){
    const M={1:['#F0C75E','#7A5B14'],2:['#C9D2E0','#4E5768'],3:['#D89E6A','#6B4420']}[r];
    if(M) return '<svg class="mx-medal" viewBox="0 0 20 20" aria-hidden="true">'+
      '<path d="M7 11.5 5.2 18l2.6-1.1 1.1 2.1 1.8-5.6z" fill="'+M[0]+'" opacity=".5"/>'+
      '<path d="M13 11.5l1.8 6.5-2.6-1.1-1.1 2.1-1.8-5.6z" fill="'+M[0]+'" opacity=".5"/>'+
      '<circle cx="10" cy="7.6" r="5.6" fill="'+M[0]+'"/>'+
      '<circle cx="10" cy="7.6" r="4.1" fill="none" stroke="'+M[1]+'" stroke-width=".7" opacity=".45"/>'+
      '<text x="10" y="10.2" text-anchor="middle" font-size="7" font-weight="700" fill="'+M[1]+'">'+r+'</text></svg>';
    return '<svg class="mx-medal" viewBox="0 0 20 20" aria-hidden="true">'+
      '<circle cx="10" cy="7.6" r="5.6" fill="none" stroke="currentColor" stroke-width="1" opacity=".4"/>'+
      '<text x="10" y="10.2" text-anchor="middle" font-size="7" font-weight="600" fill="currentColor" opacity=".85">'+r+'</text></svg>';
  }
  const su=d.skill_usage||[];
  const suMax=Math.max(1,...su.map(s=>s.count||0));
  let skHtml;
  if(su.length){
    skHtml=su.map((s,i)=>'<div class="mx-row" title="'+esc(s.name)+' · opened '+(s.count||0)+' time'+((s.count===1)?'':'s')+'">'+
      medal(i+1)+'<span class="mx-name">'+esc(s.name)+'</span>'+
      '<div class="mx-bar"><i style="--w:'+Math.max(4,Math.round((s.count||0)/suMax*100))+'%;animation-delay:'+(i*55)+'ms"></i></div>'+
      '<span class="mx-n num">'+(s.count||0)+'</span></div>').join('');
  }else{
    skHtml='<div class="hint">No skill invocations recorded yet. When Hermes opens a skill mid-conversation, its usage shows up here.</div>';
  }
  // memory files sub-list (density: rides along in the same card)
  const mf=d.memory_files||[];
  const fsz=b=>b<1024?b+' B':(b/1024).toFixed(1)+' KB';
  if(mf.length){
    skHtml+='<div class="mx-sub">Memory files</div>'+mf.map(f=>
      '<div class="mx-file"><svg class="ic" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'+
      '<span>'+esc(f.name)+'</span><span class="mx-meta num">'+fsz(f.size||0)+' · '+relTime(f.mtime)+'</span></div>').join('');
  }
  addCard('mind-extra-skills',false,
    '<path d="M13 2 4 14h6l-1 8 9-12h-6z"/>',
    'Skills in Action', su.length?(su.reduce((a,s)=>a+(s.count||0),0)+' invocations'):'',skHtml);

  // ---------- (c) Model mix: donut with sessions total in the hole ----------
  const COLS=['var(--iris)','var(--quick)','var(--ok)','var(--warn)','var(--bad)'];
  let mm=(d.model_mix||[]).slice();
  if(mm.length>5){
    const rest=mm.slice(4).reduce((a,m)=>a+(m.sessions||0),0);
    mm=mm.slice(0,4).concat([{name:'Other',sessions:rest}]);
  }
  const mTot=mm.reduce((a,m)=>a+(m.sessions||0),0);
  let mmHtml;
  if(mTot>0){
    const R=46,C=2*Math.PI*R,GAP=(mm.length>1?2.5:0);
    let acc=0,arcs='';
    mm.forEach((m,i)=>{
      const frac=(m.sessions||0)/mTot;
      const len=Math.max(0,frac*C-GAP);
      arcs+='<circle class="mx-arc" cx="63" cy="63" r="'+R+'" fill="none" stroke="'+COLS[i%COLS.length]+
        '" stroke-width="13" stroke-dasharray="'+(RM?(len+' '+(C-len)):('0 '+C))+'" data-dash="'+len+' '+(C-len)+
        '" stroke-dashoffset="'+(-acc*C+C*0.25)+'" style="transition-delay:'+(i*110)+'ms">'+
        '<title>'+esc(m.name)+' · '+(m.sessions||0)+' sessions ('+Math.round(frac*100)+'%)</title></circle>';
      acc+=frac;
    });
    mmHtml='<div class="mx-donutwrap">'+
      '<svg class="mx-donut" viewBox="0 0 126 126" aria-hidden="true">'+
        '<circle cx="63" cy="63" r="'+R+'" fill="none" stroke="var(--hairline)" stroke-width="13"/>'+arcs+
        '<text x="63" y="60" text-anchor="middle" class="mx-ctr num">'+mTot+'</text>'+
        '<text x="63" y="74" text-anchor="middle" class="mx-ctrsub">sessions</text></svg>'+
      '<div class="mx-mlegend">'+mm.map((m,i)=>
        '<div class="mx-mrow"><i class="mx-dot" style="background:'+COLS[i%COLS.length]+'"></i>'+
        '<span class="mx-mname" title="'+esc(m.name)+'">'+esc(m.name)+'</span>'+
        '<span class="mx-n num">'+(m.sessions||0)+'</span>'+
        '<span class="mx-pct num">'+Math.round((m.sessions||0)/mTot*100)+'%</span></div>').join('')+'</div></div>';
  }else{
    mmHtml='<div class="hint">No sessions in the last 14 days.</div>';
  }
  const mCard=addCard('mind-extra-models',false,
    '<circle cx="12" cy="12" r="9"/><path d="M12 3v9l6.4 6.3"/>',
    'Model Mix','last 14 days',mmHtml);
  if(mTot>0&&!RM){
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      mCard.querySelectorAll('.mx-arc').forEach(c=>{c.style.strokeDasharray=c.dataset.dash;});
    }));
  }

  // ---------- (b) Fuel: stacked in/out token bars, last 14 days ----------
  const days=d.tokens_by_day||[];
  const tin=days.reduce((a,x)=>a+(x.in_tok||0),0), tout=days.reduce((a,x)=>a+(x.out_tok||0),0);
  let fuelHtml;
  if(days.length&&(tin+tout)>0){
    const W=620,H=148,padL=38,padR=8,padT=10,padB=22;
    const plotW=W-padL-padR,plotH=H-padT-padB,y0=padT+plotH;
    const slot=plotW/days.length,barW=Math.min(24,Math.floor(slot-8));
    const rawMax=Math.max(1,...days.map(x=>(x.in_tok||0)+(x.out_tok||0)));
    const p=Math.pow(10,Math.floor(Math.log10(rawMax)));const mmul=rawMax/p;
    const niceMax=(mmul<=1?1:mmul<=1.5?1.5:mmul<=2?2:mmul<=2.5?2.5:mmul<=3?3:mmul<=4?4:mmul<=5?5:mmul<=7.5?7.5:10)*p;
    const roundTop=(x,y,w,h,r)=>{r=Math.min(r,h/2,w/2);
      return '<path d="M'+x+' '+(y+h)+'v'+(-(h-r))+'a'+r+' '+r+' 0 0 1 '+r+' '+(-r)+'h'+(w-2*r)+'a'+r+' '+r+' 0 0 1 '+r+' '+r+'v'+(h-r)+'z"';};
    let svg='<svg class="mx-chart" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet" width="100%" aria-hidden="true">';
    [0,0.5,1].forEach(f=>{
      const gy=y0-f*plotH;
      svg+='<line x1="'+padL+'" y1="'+gy+'" x2="'+(W-padR)+'" y2="'+gy+'" stroke="var(--hairline)" stroke-width="1"/>';
      if(f>0) svg+='<text x="'+(padL-5)+'" y="'+(gy+3)+'" text-anchor="end" class="mx-tick num">'+kfmt(niceMax*f)+'</text>';
    });
    days.forEach((x,i)=>{
      const cx=padL+slot*i+slot/2,bx=cx-barW/2;
      const hIn=(x.in_tok||0)/niceMax*plotH,hOut=(x.out_tok||0)/niceMax*plotH;
      const parts=x.d.split('-'),lab=(+parts[1])+'/'+(+parts[2]);
      const tip=MN[(+parts[1])-1]+' '+(+parts[2])+' · '+kfmt(x.in_tok||0)+' in · '+kfmt(x.out_tok||0)+' out';
      svg+='<g class="mx-grow" style="animation-delay:'+(i*40)+'ms"><title>'+esc(tip)+'</title>';
      if(hIn<0.5&&hOut<0.5){
        svg+='<rect x="'+bx+'" y="'+(y0-2)+'" width="'+barW+'" height="2" fill="var(--hairline)"/>';
      }else if(hOut<0.5){
        svg+=roundTop(bx,y0-hIn,barW,hIn,4)+' fill="var(--iris)"/>';
      }else{
        // in (bottom, square baseline) + 2px surface gap + out (top, 4px rounded data-end)
        if(hIn>=0.5) svg+='<rect x="'+bx+'" y="'+(y0-hIn)+'" width="'+barW+'" height="'+hIn+'" fill="var(--iris)"/>';
        svg+=roundTop(bx,y0-hIn-(hIn>=0.5?2:0)-hOut,barW,hOut,4)+' fill="var(--quick)"/>';
      }
      svg+='</g><text x="'+cx+'" y="'+(H-7)+'" text-anchor="middle" class="mx-tick num">'+lab+'</text>';
    });
    svg+='<line x1="'+padL+'" y1="'+y0+'" x2="'+(W-padR)+'" y2="'+y0+'" stroke="var(--hairline)" stroke-width="1"/></svg>';
    fuelHtml='<div class="mx-legend"><span><i style="background:var(--iris)"></i>Tokens in</span>'+
      '<span><i style="background:var(--quick)"></i>Tokens out</span></div>'+svg;
  }else{
    fuelHtml='<div class="hint">No token usage recorded in the last 14 days.</div>';
  }
  addCard('mind-extra-fuel',true,
    '<path d="M12 3s-6 6.6-6 11a6 6 0 0 0 12 0c0-4.4-6-11-6-11z"/><path d="M12 17a3 3 0 0 0 3-3"/>',
    'Fuel','14 days · '+kfmt(tin)+' in · '+kfmt(tout)+' out',fuelHtml);
}

// ===== visual_kit =====
// ===== visual_kit — bespoke hand-drawn pop-out visuals =====
// Overrides: battery, clock, crypto, tasks, quicklinks, agent_pulse
// Load AFTER expand.js (assignments override earlier entries).

var VK_REDUCE=(typeof REDUCE!=='undefined')?REDUCE:!!(typeof matchMedia!=='undefined'&&matchMedia('(prefers-reduced-motion:reduce)').matches);
function vkUid(p){return p+Math.random().toString(36).slice(2,8);}
function vkLbl(t,right){return '<div class="vk-lbl"><span>'+t+'</span>'+(right||'')+'<span class="vk-rule"></span></div>';}
function vkCap(n){n=+n||0;if(n>=1e12)return (n/1e12).toFixed(2)+'T';if(n>=1e9)return (n/1e9).toFixed(1)+'B';if(n>=1e6)return (n/1e6).toFixed(1)+'M';return kfmt(n);}
function vkPolar(cx,cy,r,a){var t=(a-90)*Math.PI/180;return (cx+r*Math.cos(t)).toFixed(2)+' '+(cy+r*Math.sin(t)).toFixed(2);}
function vkArcPath(cx,cy,r,a0,a1){var sw=((a1-a0)%360+360)%360;return 'M'+vkPolar(cx,cy,r,a0)+' A'+r+' '+r+' 0 '+(sw>180?1:0)+' 1 '+vkPolar(cx,cy,r,a1);}
// generic animated ring gauge; inner = extra SVG placed in the middle
function vkRing(pct,size,stroke,inner,c2){
  var c=size/2,r=c-stroke/2-2,C=2*Math.PI*r,off=C*(1-Math.max(0,Math.min(100,+pct||0))/100),g=vkUid('rg');
  return '<svg width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'" style="flex:0 0 auto">'+
    '<defs><linearGradient id="'+g+'" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--iris)"/><stop offset="1" stop-color="'+(c2||'var(--quick)')+'"/></linearGradient></defs>'+
    '<circle cx="'+c+'" cy="'+c+'" r="'+r+'" fill="none" stroke="var(--hairline)" stroke-width="'+stroke+'"/>'+
    '<circle class="vk-ring-arc" cx="'+c+'" cy="'+c+'" r="'+r+'" fill="none" stroke="url(#'+g+')" stroke-width="'+stroke+'" stroke-linecap="round" transform="rotate(-90 '+c+' '+c+')" stroke-dasharray="'+C.toFixed(1)+'" stroke-dashoffset="'+C.toFixed(1)+'" data-off="'+off.toFixed(1)+'"/>'+
    (inner||'')+'</svg>';
}
// kick the sweep animation after innerHTML insert
function vkSweep(el){el.querySelectorAll('.vk-ring-arc').forEach(function(a){var t=a.getAttribute('data-off');
  if(VK_REDUCE){a.style.transition='none';a.style.strokeDashoffset=t;return;}
  requestAnimationFrame(function(){requestAnimationFrame(function(){a.style.strokeDashoffset=t;});});});}
// tiny battery-bar glyph for connected devices
function vkBattGlyph(v){v=Math.max(0,Math.min(100,+v||0));var pc=v<=15?'var(--bad)':v<=35?'var(--warn)':'var(--ok)';
  var fw=Math.max(2,Math.round(20*v/100));
  return '<svg width="30" height="14" viewBox="0 0 30 14" style="flex:0 0 auto"><rect x="1" y="2.5" width="24" height="9" rx="2.8" fill="none" stroke="var(--muted)" stroke-width="1.3"/><rect x="26.6" y="5.4" width="2.4" height="3.2" rx="1" fill="var(--muted)"/><rect x="3" y="4.5" width="'+fw+'" height="5" rx="1.4" fill="'+pc+'"/></svg>';}

// ===== battery — animated ring gauge =====
EXPAND_RENDER.battery=function(el,d){
  d=d||{};
  if(d.available===false){el.innerHTML='<div class="hint">'+esc(d.reason||'Battery info unavailable.')+'</div>';return;}
  var pct=d.pct!=null?d.pct:0,chg=!!d.charging,ac=!!d.ac;
  var cv=chg?'--quick':(pct<=15?'--bad':pct<=35?'--warn':'--ok'),col='var('+cv+')';
  var c=62,ty=chg?c-4:c+3;
  var bolt='';
  if(chg){var bx=c,by=c+9;
    bolt='<path class="vk-bolt" d="M'+bx+' '+by+' L'+(bx-6)+' '+(by+12)+' L'+(bx-1)+' '+(by+12)+' L'+(bx-4)+' '+(by+21)+' L'+(bx+5)+' '+(by+9)+' L'+bx+' '+(by+9)+' Z" fill="'+col+'"/>';}
  var inner='<text x="'+c+'" y="'+ty+'" text-anchor="middle" class="num" style="font-size:29px;font-weight:750;fill:'+col+'">'+pct+'<tspan style="font-size:13px;font-weight:600;fill:var(--muted)">%</tspan></text>'+bolt+
    (chg?'':'<text x="'+c+'" y="'+(c+21)+'" text-anchor="middle" style="font-size:8.5px;letter-spacing:.09em;font-weight:700;fill:var(--muted)">'+(ac?'ON AC':'ON BATTERY')+'</text>');
  var ring=vkRing(pct,124,9,inner,col);
  var sub=[];
  if(d.time_label)sub.push('<b class="num">'+esc(d.time_label)+'</b>');
  else if(ac&&!chg&&pct>=95)sub.push('holding at full');
  else if(ac&&!chg)sub.push('on external power');
  var chips=[];
  if(chg)chips.push(['--quick','Charging']);
  if(d.low_power_mode)chips.push(['--warn','Low Power Mode']);
  if(d.warn)chips.push(['--bad','Low battery']);
  var ad=d.adapter||{};
  var adRow='';
  if(ad.connected&&(ad.name||ad.watts)){
    adRow='<div style="display:flex;align-items:center;gap:8px;margin-top:8px;font-size:11.5px;color:var(--muted)">'+
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--quick)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:0 0 auto"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>'+
      '<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(ad.name||'USB-C adapter')+'</span>'+
      (ad.watts!=null?'<b class="num" style="color:var(--quick);flex:0 0 auto">'+ad.watts+' W</b>':'')+'</div>';}
  var h='<div style="display:flex;align-items:center;gap:16px;margin:2px 0 2px">'+ring+
    '<div style="min-width:0;flex:1">'+
      '<div style="font-size:14.5px;font-weight:660;color:var(--ink)">'+esc(d.state||'Battery')+'</div>'+
      (sub.length?'<div class="w-sub" style="margin-top:2px">'+sub.join(' · ')+'</div>':'')+
      (chips.length?'<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:7px">'+chips.map(function(x){
        return '<span style="font-size:10px;font-weight:650;letter-spacing:.02em;padding:2.5px 8px;border-radius:20px;color:var('+x[0]+');background:color-mix(in srgb,var('+x[0]+') 15%,transparent)">'+esc(x[1])+'</span>';}).join('')+'</div>':'')+
      adRow+'</div></div>';
  var hh=d.health||{};
  h+=vkLbl('Battery health');
  h+=statGrid([
    ['Condition',hh.condition||'—'],
    ['Capacity',hh.max_capacity_pct!=null?hh.max_capacity_pct+'%':'—'],
    ['Cycles',hh.cycles!=null?fmtNum(hh.cycles):'—']]);
  if(hh.max_capacity_pct!=null){
    var capc=hh.max_capacity_pct>=80?'--ok':(hh.max_capacity_pct>=60?'--warn':'--bad');
    h+='<div style="display:flex;align-items:center;gap:8px;font-size:10.5px;color:var(--muted);margin:0 0 6px" class="num">'+
      '<span style="width:62px">Capacity</span>'+
      '<div style="flex:1;height:6px;border-radius:3px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+hh.max_capacity_pct+'%;background:var('+capc+')"></i></div>'+
      '<span style="width:34px;text-align:right;color:var(--ink)">'+hh.max_capacity_pct+'%</span></div>';}
  if(hh.cycles!=null){
    var rc=hh.rated_cycles||1000,cp=Math.min(100,Math.round(hh.cycles/rc*100));
    h+='<div style="display:flex;align-items:center;gap:8px;font-size:10.5px;color:var(--muted)" class="num">'+
      '<span style="width:62px">Cycles</span>'+
      '<div style="flex:1;height:6px;border-radius:3px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+Math.max(1.5,cp)+'%;background:linear-gradient(90deg,var(--iris),var(--quick))"></i></div>'+
      '<span style="width:60px;text-align:right;color:var(--ink)">'+fmtNum(hh.cycles)+' <span style="color:var(--faint)">/ '+fmtNum(rc)+'</span></span></div>'+
      '<div class="hint" style="margin-top:3px">'+(rc-hh.cycles>0?fmtNum(rc-hh.cycles)+' cycles until the rated limit':'past rated cycle limit')+'</div>';}
  var devs=d.devices||[];
  h+=vkLbl('Connected devices'+(devs.length?' · '+devs.length:''));
  if(devs.length){
    var ord=[['left','L'],['right','R'],['case','Case'],['main','Batt'],['single','']];
    h+=devs.map(function(dv){var lv=dv.levels||{};
      var rows=ord.filter(function(o){return lv[o[0]]!=null;}).map(function(o){var v=lv[o[0]];
        return '<span style="display:inline-flex;align-items:center;gap:5px">'+
          (o[1]?'<span style="font-size:10px;color:var(--muted);font-weight:600">'+o[1]+'</span>':'')+
          vkBattGlyph(v)+'<b class="num" style="font-size:11px">'+v+'%</b></span>';}).join('<span style="width:10px"></span>');
      return '<div style="display:flex;align-items:center;gap:9px;padding:7px 0;border-bottom:1px solid var(--hairline)">'+
        '<span style="min-width:0;flex:1;font-weight:620;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(dv.name)+
        (dv.type?' <span class="hint">'+esc(dv.type)+'</span>':'')+'</span>'+rows+'</div>';}).join('');
  }else{
    h+='<div class="hint" style="padding:8px 10px;background:var(--glass-2);border:1px solid var(--hairline);border-radius:9px">No Bluetooth devices reporting battery. AirPods, mice and keyboards appear here when connected.</div>';
  }
  el.innerHTML=h;
  vkSweep(el);
};

// ===== clock — analog face + 24h day ring with sun markers =====
EXPAND_RENDER.clock=function(el,data){
  var sun=(data&&data.sun)||{};
  var sr=sun.available?sun.sunrise_min:null,ss=sun.available?sun.sunset_min:null,nn=sun.available?sun.noon_min:null;
  var DOW=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var MON=['January','February','March','April','May','June','July','August','September','October','November','December'];
  function pad(n){return (n<10?'0':'')+n;}
  function fmtDur(mins){var m=Math.max(0,Math.round(mins)),hh=Math.floor(m/60);m=m%60;return hh>0?(hh+'h '+pad(m)+'m'):(m+'m');}
  var CX=84,CY=84;
  // face ticks (12) — majors at 12/3/6/9
  var ticks='';
  for(var i=0;i<12;i++){var maj=i%3===0,a=(i*30-90)*Math.PI/180;
    var r1=maj?49:52,r2=57;
    ticks+='<line x1="'+(CX+r1*Math.cos(a)).toFixed(1)+'" y1="'+(CY+r1*Math.sin(a)).toFixed(1)+'" x2="'+(CX+r2*Math.cos(a)).toFixed(1)+'" y2="'+(CY+r2*Math.sin(a)).toFixed(1)+'" stroke="'+(maj?'var(--muted)':'var(--hairline)')+'" stroke-width="'+(maj?2.2:1.3)+'" stroke-linecap="round"/>';}
  // 24h outer ring: hairline base + gold daylight arc + sun markers, midnight at top
  var mAng=function(min){return min/1440*360;};
  var ring24='<circle cx="'+CX+'" cy="'+CY+'" r="76" fill="none" stroke="var(--hairline)" stroke-width="5.5" opacity=".7"/>';
  if(sr!=null&&ss!=null){
    ring24+='<path d="'+vkArcPath(CX,CY,76,mAng(sr),mAng(ss))+'" fill="none" stroke="#F5B94A" stroke-width="5.5" stroke-linecap="round" opacity=".55"/>';
    ring24+='<circle cx="'+vkPolar(CX,CY,76,mAng(sr)).split(' ')[0]+'" cy="'+vkPolar(CX,CY,76,mAng(sr)).split(' ')[1]+'" r="3.4" fill="#F5B94A"/>';
    ring24+='<circle cx="'+vkPolar(CX,CY,76,mAng(ss)).split(' ')[0]+'" cy="'+vkPolar(CX,CY,76,mAng(ss)).split(' ')[1]+'" r="3.4" fill="#E9963B"/>';
  }
  if(nn!=null){var np=vkPolar(CX,CY,80.5,mAng(nn)).split(' ');
    ring24+='<circle cx="'+np[0]+'" cy="'+np[1]+'" r="1.6" fill="var(--muted)"/>';}
  var face='<svg width="168" height="168" viewBox="0 0 168 168" style="flex:0 0 auto">'+
    ring24+
    '<path id="ck-prog" d="" fill="none" stroke="var(--iris)" stroke-width="2" stroke-linecap="round" opacity=".9"/>'+
    '<circle id="ck-dot" cx="'+CX+'" cy="8" r="3.2" fill="var(--iris)" style="filter:drop-shadow(0 0 3px var(--iris))"/>'+
    '<circle cx="'+CX+'" cy="'+CY+'" r="58" fill="var(--glass-2)" stroke="var(--hairline)"/>'+ticks+
    '<line id="ck-hh" x1="'+CX+'" y1="'+(CY+9)+'" x2="'+CX+'" y2="'+(CY-30)+'" stroke="var(--ink)" stroke-width="4.4" stroke-linecap="round"/>'+
    '<line id="ck-mh" x1="'+CX+'" y1="'+(CY+11)+'" x2="'+CX+'" y2="'+(CY-45)+'" stroke="var(--ink)" stroke-width="2.8" stroke-linecap="round" opacity=".92"/>'+
    '<line id="ck-sh" class="vk-sechand" x1="'+CX+'" y1="'+(CY+14)+'" x2="'+CX+'" y2="'+(CY-50)+'" stroke="var(--iris)" stroke-width="1.5" stroke-linecap="round"/>'+
    '<circle cx="'+CX+'" cy="'+CY+'" r="4" fill="var(--iris)"/><circle cx="'+CX+'" cy="'+CY+'" r="1.6" fill="var(--glass-2)"/>'+
    '</svg>';
  // day-of-year / ISO week
  var now0=new Date();
  var doy=Math.floor((now0-new Date(now0.getFullYear(),0,1))/86400000)+1;
  var isLeap=(now0.getFullYear()%4===0&&now0.getFullYear()%100!==0)||now0.getFullYear()%400===0;
  var d1=new Date(Date.UTC(now0.getFullYear(),now0.getMonth(),now0.getDate()));
  var dn=(d1.getUTCDay()+6)%7;d1.setUTCDate(d1.getUTCDate()-dn+3);
  var firstTh=new Date(Date.UTC(d1.getUTCFullYear(),0,4));
  var weekNo=1+Math.round(((d1-firstTh)/86400000-3+((firstTh.getUTCDay()+6)%7))/7);
  // week strip
  var mon=new Date(now0),wd=(now0.getDay()+6)%7;mon.setDate(now0.getDate()-wd);mon.setHours(0,0,0,0);
  var WL=['M','T','W','T','F','S','S'],weekCells='';
  for(var k=0;k<7;k++){var dd=new Date(mon);dd.setDate(mon.getDate()+k);
    var isT=dd.toDateString()===now0.toDateString();
    weekCells+='<div style="flex:1;text-align:center;padding:5px 0;border-radius:9px;'+
      (isT?'background:linear-gradient(160deg,var(--iris),var(--quick));':'background:var(--glass-2);border:1px solid var(--hairline);')+'">'+
      '<div style="font-size:9px;font-weight:700;letter-spacing:.06em;color:'+(isT?'rgba(255,255,255,.85)':'var(--muted)')+'">'+WL[k]+'</div>'+
      '<div class="num" style="font-size:14px;font-weight:700;margin-top:1px;color:'+(isT?'#fff':'var(--ink)')+'">'+dd.getDate()+'</div></div>';}
  var deltaHtml='';
  if(sun.available&&sun.delta_min!=null){var dm=sun.delta_min,up=dm>=0;
    deltaHtml='<span class="delta '+(up?'up':'down')+'" style="font-size:10.5px;font-weight:640">'+(up?'+':'−')+fmtDur(Math.abs(dm))+' vs. yesterday</span>';}
  var sunStats=sun.available?statGrid([
      ['Sunrise',sun.sunrise||'—'],['Sunset',sun.sunset||'—'],['Daylight',sun.daylight||'—'],
      ['Solar noon',sun.solar_noon||'—'],['Rise tmrw',sun.tomorrow_sunrise||'—'],['Set tmrw',sun.tomorrow_sunset||'—']])
    :'<div class="hint" style="margin:4px 0">'+esc(sun.reason||'Sun times unavailable.')+'</div>';
  el.innerHTML=
    '<div style="display:flex;align-items:center;gap:16px;margin:2px 0 2px">'+face+
    '<div style="min-width:0;flex:1">'+
      '<div style="display:flex;align-items:baseline;gap:6px">'+
        '<span id="ck-time" class="num" style="font-size:42px;font-weight:750;line-height:1;letter-spacing:-.02em">—</span>'+
        '<span style="display:flex;flex-direction:column;padding-bottom:2px">'+
          '<span id="ck-ampm" style="font-size:14px;font-weight:700;color:var(--iris);line-height:1.1"></span>'+
          '<span id="ck-sec" class="num" style="font-size:11px;color:var(--muted);font-weight:600"></span></span></div>'+
      '<div id="ck-date" style="font-size:13px;font-weight:600;color:var(--ink);margin-top:4px"></div>'+
      '<div class="w-sub" style="font-size:11px;margin-top:1px">'+esc(sun.tz?(sun.tz.replace(/_/g,' ')+' · '+(sun.tzabbr||'')):'')+'</div>'+
      '<div class="w-sub" style="font-size:11px">Day '+doy+' of '+(isLeap?366:365)+' · Week '+weekNo+'</div>'+
      '<div style="margin-top:7px;font-size:11.5px"><span id="ck-count" style="font-weight:640;color:var(--ink)"></span></div>'+
      '<div id="ck-elapsed" class="w-sub" style="font-size:10.5px;margin-top:1px"></div>'+
    '</div></div>'+
    '<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--faint);margin:2px 2px 0" class="num">'+
      '<span>Ring · 24h from midnight (top)</span>'+
      (sr!=null?'<span style="color:#F5B94A">Rise '+esc(sun.sunrise)+'</span>':'')+
      (ss!=null?'<span style="color:#E9963B">Set '+esc(sun.sunset)+'</span>':'')+'</div>'+
    vkLbl('Sun · daylight',deltaHtml?'<span style="text-transform:none;letter-spacing:0">'+deltaHtml+'</span>':'')+
    sunStats+
    vkLbl('This week')+
    '<div style="display:flex;gap:5px">'+weekCells+'</div>';
  var tEl=el.querySelector('#ck-time'),apEl=el.querySelector('#ck-ampm'),secEl=el.querySelector('#ck-sec'),
      dEl=el.querySelector('#ck-date'),hhEl=el.querySelector('#ck-hh'),mhEl=el.querySelector('#ck-mh'),
      shEl=el.querySelector('#ck-sh'),prEl=el.querySelector('#ck-prog'),dotEl=el.querySelector('#ck-dot'),
      cntEl=el.querySelector('#ck-count'),elpEl=el.querySelector('#ck-elapsed');
  function tick(){
    var pop=document.getElementById('wpop');
    if(!tEl||!tEl.isConnected||(pop&&pop.hidden)){
      if(window.__ckTimer){clearInterval(window.__ckTimer);window.__ckTimer=null;}
      return;}
    var d=new Date(),hh=d.getHours(),m=d.getMinutes(),s=d.getSeconds();
    var h12=hh%12||12;
    tEl.textContent=h12+':'+pad(m);
    apEl.textContent=hh<12?'AM':'PM';
    secEl.textContent=':'+pad(s);
    dEl.textContent=DOW[d.getDay()]+', '+MON[d.getMonth()]+' '+d.getDate()+', '+d.getFullYear();
    hhEl.setAttribute('transform','rotate('+((hh%12+m/60)*30)+' '+CX+' '+CY+')');
    mhEl.setAttribute('transform','rotate('+((m+s/60)*6)+' '+CX+' '+CY+')');
    shEl.setAttribute('transform','rotate('+(s*6)+' '+CX+' '+CY+')');
    var nowMin=hh*60+m+s/60,ang=nowMin/1440*360;
    if(ang>0.5)prEl.setAttribute('d',vkArcPath(CX,CY,76,0,ang));
    var dp=vkPolar(CX,CY,76,ang).split(' ');
    dotEl.setAttribute('cx',dp[0]);dotEl.setAttribute('cy',dp[1]);
    elpEl.textContent=Math.round(nowMin/1440*100)+'% of day elapsed';
    if(sr!=null&&ss!=null){
      var target,label;
      if(nowMin<sr){target=sr;label='Sunrise';}
      else if(nowMin<ss){target=ss;label='Sunset';}
      else{target=sr+1440;label='Sunrise';}
      cntEl.textContent=label+' in '+fmtDur(target-nowMin);
      if(nowMin>=sr&&nowMin<ss)elpEl.textContent=Math.round((nowMin-sr)/(ss-sr)*100)+'% of daylight · '+Math.round(nowMin/1440*100)+'% of day';
    }else cntEl.textContent='';
  }
  if(window.__ckTimer){clearInterval(window.__ckTimer);window.__ckTimer=null;}
  tick();
  window.__ckTimer=setInterval(tick,1000);
};

// ===== crypto — coin medallions, dominance bar, deltas =====
EXPAND_RENDER.crypto=function(el,d){
  var coins=(d&&d.coins)||[];
  if(d&&d.error){el.innerHTML='<div class="hint">'+esc(d.error)+'</div>';return;}
  if(!coins.length){el.innerHTML='<div class="hint">No coins.</div>';return;}
  var COL={BTC:'#F7931A',ETH:'#627EEA',SOL:'#9945FF',BNB:'#F0B90B',XRP:'#00AAE4',ADA:'#0033AD',DOGE:'#C2A633',DOT:'#E6007A',LTC:'#345D9D',AVAX:'#E84142',MATIC:'#8247E5',LINK:'#2A5ADA',TRX:'#EB0029',UNI:'#FF007A',ATOM:'#6F7390',XLM:'#3E1BDB'};
  function ccol(sym){sym=(sym||'?').toUpperCase();if(COL[sym])return COL[sym];
    var n=0;for(var i=0;i<sym.length;i++)n=(n*31+sym.charCodeAt(i))>>>0;return 'hsl('+(n%360)+' 64% 50%)';}
  function medal(sym,sz){var c2=ccol(sym),g=vkUid('cg');
    return '<svg width="'+sz+'" height="'+sz+'" viewBox="0 0 46 46" style="flex:0 0 auto">'+
      '<defs><radialGradient id="'+g+'" cx=".32" cy=".26" r="1"><stop offset="0" stop-color="'+c2+'"/><stop offset="1" stop-color="'+c2+'" stop-opacity=".55"/></radialGradient></defs>'+
      '<circle class="vk-orbit" cx="23" cy="23" r="21.5" fill="none" stroke="'+c2+'" stroke-opacity=".55" stroke-width="1.1" stroke-dasharray="2.5 6.5"/>'+
      '<circle cx="23" cy="23" r="18" fill="url(#'+g+')"/>'+
      '<circle cx="23" cy="23" r="14.5" fill="none" stroke="#fff" stroke-opacity=".28" stroke-width="1"/>'+
      '<text x="23" y="24.5" text-anchor="middle" dominant-baseline="middle" style="font-size:16px;font-weight:800;fill:#fff;letter-spacing:-.02em">'+esc((sym||'?')[0].toUpperCase())+'</text>'+
      '<path d="M16 31 H30" stroke="#fff" stroke-opacity=".5" stroke-width="1.2" stroke-linecap="round"/></svg>';}
  function px(v){if(v==null)return '—';
    return '$'+v.toLocaleString(undefined,{maximumFractionDigits:v>=1000?0:(v>=1?2:4)});}
  function dchip(lbl,p){return '<span class="num" style="font-size:10.5px;color:var(--muted)">'+lbl+' '+deltaTxt(p!=null?p:0)+'</span>';}
  var tot=coins.reduce(function(a,c){return a+(+c.mcap||0);},0);
  var up24=coins.filter(function(c){return (c.pct24h||0)>=0;}).length;
  var h='<div style="display:flex;align-items:center;gap:12px;margin:2px 0 10px">'+
    '<div style="display:flex">'+coins.slice(0,4).map(function(c,i){
      return '<span style="display:inline-flex;'+(i?'margin-left:-9px;':'')+'position:relative;z-index:'+(9-i)+'">'+medal(c.symbol,30)+'</span>';}).join('')+'</div>'+
    '<div style="min-width:0"><div style="display:flex;align-items:baseline;gap:6px"><span class="num" style="font-size:21px;font-weight:720;line-height:1">$'+vkCap(tot)+'</span>'+
    '<span class="w-sub" style="font-size:10.5px">combined mcap</span></div>'+
    '<div class="w-sub" style="font-size:11px;margin-top:1px">'+coins.length+' coins · <span class="delta up">'+up24+' up</span> · <span class="delta down">'+(coins.length-up24)+' down</span> <span style="color:var(--faint)">24h</span></div></div></div>';
  // dominance — share of combined market cap
  if(tot>0){
    h+=vkLbl('Dominance · share of shown mcap');
    h+='<div style="display:flex;height:9px;border-radius:5px;overflow:hidden;background:var(--hairline);margin-bottom:6px">'+
      coins.map(function(c){var w=(+c.mcap||0)/tot*100;
        return '<i class="vk-domseg" style="display:block;height:100%;width:'+w.toFixed(2)+'%;min-width:'+(w>0?'3px':'0')+';background:'+ccol(c.symbol)+'"></i>';}).join('')+'</div>';
    h+='<div class="num" style="display:flex;flex-wrap:wrap;gap:5px 13px;font-size:10.5px;color:var(--muted);margin-bottom:4px">'+
      coins.map(function(c){var w=(+c.mcap||0)/tot*100;
        return '<span style="display:inline-flex;align-items:center;gap:4px"><i style="width:7px;height:7px;border-radius:2px;background:'+ccol(c.symbol)+';display:inline-block"></i>'+esc(c.symbol)+' <b>'+w.toFixed(1)+'%</b></span>';}).join('')+'</div>';
  }
  h+=vkLbl('Watchlist');
  h+=coins.map(function(c){
    var col=ccol(c.symbol),u7=(c.pct7d||0)>=0;
    var spark=(c.spark&&c.spark.length>1)?
      '<div style="margin:5px 0 0">'+miniSpark(c.spark,u7).replace('class="qspark"','class="qspark" style="width:100%;height:26px;display:block"')+'</div>':'';
    var rng='';
    if(c.lo24!=null&&c.hi24!=null&&c.price!=null&&c.hi24>c.lo24){
      var p=Math.max(2,Math.min(98,(c.price-c.lo24)/(c.hi24-c.lo24)*100));
      rng='<div style="display:flex;align-items:center;gap:7px;font-size:10px;margin-top:6px" class="num">'+
        '<span style="width:26px;color:var(--muted);letter-spacing:.05em;font-weight:600">24H</span>'+
        '<span style="color:var(--faint)">'+fmtNum(c.lo24)+'</span>'+
        '<div style="position:relative;flex:1;height:4px;border-radius:2px;background:var(--hairline)">'+
        '<i style="position:absolute;left:0;top:0;height:100%;width:'+p+'%;border-radius:2px;background:color-mix(in srgb,'+col+' 45%,transparent)"></i>'+
        '<i style="position:absolute;left:'+p+'%;top:50%;width:7px;height:7px;border-radius:50%;background:'+col+';transform:translate(-50%,-50%);box-shadow:0 0 0 2px var(--glass-2)"></i></div>'+
        '<span style="color:var(--faint)">'+fmtNum(c.hi24)+'</span></div>';}
    return '<div style="padding:9px 0;border-bottom:1px solid var(--hairline)">'+
      '<div style="display:flex;align-items:center;gap:10px">'+medal(c.symbol,34)+
      '<div style="min-width:0;flex:1"><div style="display:flex;align-items:baseline;gap:7px">'+
        '<span style="font-weight:720;font-size:13.5px">'+esc(c.symbol||c.id)+'</span>'+
        '<span class="w-sub" style="font-size:11px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(c.name||'')+'</span>'+
        '<span class="num" style="margin-left:auto;font-weight:700;font-size:14.5px">'+px(c.price)+'</span></div>'+
      '<div style="display:flex;gap:11px;margin-top:2px;align-items:baseline">'+dchip('1h',c.pct1h)+dchip('24h',c.pct24h)+dchip('7d',c.pct7d)+
        '<span class="num" style="margin-left:auto;font-size:10px;color:var(--faint)">MC $'+vkCap(c.mcap)+' · Vol $'+vkCap(c.vol)+'</span></div></div></div>'+
      spark+rng+'</div>';
  }).join('');
  el.innerHTML=h;
};

// ===== tasks — completion ring + interactive list =====
EXPAND_RENDER.tasks=function(el,data){
  function post(body){return fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json();});}
  function refresh(){fetch('/api/expand?id=tasks').then(function(r){return r.json();}).then(function(d){if(d&&(d.tasks||d.available!==undefined))render(d);}).catch(function(){});}
  function op(body){post(body).then(function(){refresh();}).catch(function(){});}
  function dur(s){s=+s||0;if(s<3600)return Math.max(1,Math.round(s/60))+'m';if(s<86400)return Math.round(s/3600)+'h';if(s<604800)return Math.round(s/86400)+'d';return Math.round(s/604800)+'w';}
  function chip(label,val,tone){var col=tone==='bad'?'var(--bad)':tone==='warn'?'var(--warn)':'var(--muted)';
    return '<span style="display:inline-flex;align-items:center;gap:5px;font-size:10.5px;color:'+col+';background:var(--chip);border:1px solid var(--hairline);border-radius:999px;padding:2.5px 9px"><b class="num" style="color:'+(tone?col:'var(--ink)')+';font-weight:680">'+val+'</b>'+esc(label)+'</span>';}
  var DELX='<svg class="ic" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var TICK='<svg viewBox="0 0 24 24" style="width:13px;height:13px" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  function taskRow(t,stale){
    var checked=t.done;
    var box='<button class="tk-chk" data-id="'+esc(t.id)+'" aria-label="Toggle" style="flex:0 0 auto;width:20px;height:20px;border-radius:6px;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;'+
      (checked?'background:linear-gradient(135deg,var(--iris),var(--quick));border:1px solid transparent':'background:transparent;border:1.5px solid var(--muted)')+'">'+(checked?TICK:'')+'</button>';
    var age=t.ts?relTime(t.ts):'';
    return '<div class="tk-row" style="display:flex;align-items:center;gap:10px;padding:8px 2px;border-bottom:1px solid var(--hairline)">'+box+
      '<span style="flex:1;min-width:0;font-size:13px;line-height:1.4;'+(checked?'color:var(--faint);text-decoration:line-through':'color:var(--ink)')+'">'+esc(t.text)+'</span>'+
      (age?'<span class="num" style="flex:0 0 auto;font-size:10.5px;color:'+(stale?'var(--bad)':'var(--faint)')+'">'+esc(age)+'</span>':'')+
      '<button class="tk-del" data-id="'+esc(t.id)+'" aria-label="Delete" style="flex:0 0 auto;background:none;border:none;cursor:pointer;color:var(--faint);opacity:.55;padding:2px;display:flex">'+DELX+'</button></div>';
  }
  function render(d){
    d=d||{};
    if(d.available===false){el.innerHTML='<div class="hint">'+esc(d.reason||'Tasks unavailable.')+'</div>';return;}
    var tasks=d.tasks||[];
    var openT=tasks.filter(function(t){return !t.done;});
    var doneT=tasks.filter(function(t){return t.done;});
    var total=d.total!=null?d.total:tasks.length;
    var pct=d.pct!=null?d.pct:(total?Math.round(doneT.length/total*100):0);
    var inner='<text x="41" y="38" text-anchor="middle" class="num" style="font-size:17px;font-weight:750;fill:var(--ink)">'+pct+'<tspan style="font-size:10px;fill:var(--muted)">%</tspan></text>'+
      '<text x="41" y="52" text-anchor="middle" class="num" style="font-size:9px;font-weight:600;fill:var(--muted)">'+doneT.length+' / '+total+'</text>';
    var h='<div style="display:flex;align-items:center;gap:14px;margin:2px 0 2px">'+vkRing(pct,82,7,inner)+
      '<div style="min-width:0;flex:1"><div style="display:flex;align-items:baseline;gap:7px"><span class="num" style="font-size:28px;font-weight:720;line-height:1">'+openT.length+'</span>'+
      '<span class="w-sub" style="font-size:12.5px">open</span></div>'+
      '<div class="w-sub" style="margin-top:2px;font-size:11.5px">'+doneT.length+' of '+total+' done'+(total===0?' — nothing yet':'')+'</div>';
    var chips=[];
    if(d.added_24h)chips.push(chip('added today',d.added_24h));
    if(d.added_7d)chips.push(chip('this week',d.added_7d));
    if(d.avg_open_age!=null&&openT.length)chips.push(chip('avg age',dur(d.avg_open_age)));
    if(d.stale)chips.push(chip('stale',d.stale,'bad'));
    if(chips.length)h+='<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:7px">'+chips.join('')+'</div>';
    h+='</div></div>';
    h+='<div style="display:flex;gap:7px;margin:11px 0 2px"><input class="tk-new" placeholder="Add a task&hellip;" spellcheck="false" style="flex:1;background:var(--glass-2);border:1px solid var(--hairline);border-radius:10px;padding:8px 12px;color:var(--ink);font-size:13px;outline:none">'+
      '<button class="tk-add primary" style="flex:0 0 auto;display:flex;align-items:center;gap:5px;border-radius:10px;padding:0 14px;font-weight:600"><svg class="ic" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Add</button></div>';
    var stale=openT.filter(function(t){return t.age!=null&&t.age>604800;});
    var active=openT.filter(function(t){return !(t.age!=null&&t.age>604800);});
    active.sort(function(a,b){return (b.ts||0)-(a.ts||0);});
    stale.sort(function(a,b){return (a.ts||0)-(b.ts||0);});
    if(openT.length){
      h+=vkLbl('Open · '+openT.length);
      h+=active.map(function(t){return taskRow(t,false);}).join('');
      if(stale.length){h+=vkLbl('Needs attention · older than a week');h+=stale.map(function(t){return taskRow(t,true);}).join('');}
    }else{
      h+='<div class="hint" style="margin-top:12px">All clear — no open tasks. Your assistant reads these too.</div>';
    }
    if(doneT.length){
      doneT.sort(function(a,b){return (b.ts||0)-(a.ts||0);});
      h+=vkLbl('Completed · '+doneT.length,'<button class="tk-clear" style="background:none;border:none;cursor:pointer;font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:0">Clear</button>');
      h+='<div style="opacity:.75">'+doneT.map(function(t){return taskRow(t,false);}).join('')+'</div>';
    }
    el.innerHTML=h;
    vkSweep(el);
    var inp=el.querySelector('.tk-new');
    var add=function(){if(!inp)return;var v=inp.value.trim();if(!v)return;inp.value='';op({op:'add',text:v});};
    var addBtn=el.querySelector('.tk-add');if(addBtn)addBtn.onclick=add;
    if(inp)inp.addEventListener('keydown',function(e){if(e.key==='Enter')add();});
    el.querySelectorAll('.tk-chk').forEach(function(b){b.onclick=function(){op({op:'toggle',id:b.dataset.id});};});
    el.querySelectorAll('.tk-del').forEach(function(b){b.onclick=function(){op({op:'delete',id:b.dataset.id});};});
    var clrB=el.querySelector('.tk-clear');if(clrB)clrB.onclick=function(){op({op:'clear_done'});};
  }
  render(data);
};

// ===== quicklinks — compact monogram grid =====
EXPAND_RENDER.quicklinks=function(el,data){
  if(data&&data.error){el.innerHTML='<div class="hint">'+esc(data.error)+'</div>';return;}
  var links=((data&&data.links)||[]).map(function(l){return {label:l.label||'',url:l.url||'',domain:l.domain||'',mono:l.mono||'#',hue:(l.hue||0)};});
  var sugg=((data&&data.suggestions)||[]).slice();
  var _dom=function(u){try{var n=new URL(/:\/\//.test(u)?u:'https://'+u).hostname.toLowerCase();return n.replace(/^www\./,'');}catch(e){return '';}};
  var _mono=function(lb,dm){var s=(lb||dm||'').trim();return s?s[0].toUpperCase():'#';};
  var _hue=function(k){var n=0,i;k=k||'?';for(i=0;i<k.length;i++)n=(n*31+k.charCodeAt(i))>>>0;return n%360;};
  function qchip(l,sz){var g=vkUid('qg');
    return '<svg width="'+sz+'" height="'+sz+'" viewBox="0 0 24 24" style="flex:0 0 auto;overflow:visible">'+
      '<defs><linearGradient id="'+g+'" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl('+l.hue+' 72% 55%)"/><stop offset="1" stop-color="hsl('+((l.hue+42)%360)+' 66% 42%)"/></linearGradient></defs>'+
      '<rect x="1" y="1" width="22" height="22" rx="6.5" fill="url(#'+g+')"/>'+
      '<path d="M3 6 Q3 3 6 3 L14 3" fill="none" stroke="#fff" stroke-opacity=".45" stroke-width="1.6" stroke-linecap="round"/>'+
      '<rect class="vk-sheen" x="2" y="1" width="7" height="22" rx="3.5" fill="#fff"/>'+
      '<text x="12" y="13" text-anchor="middle" dominant-baseline="middle" style="font-size:11.5px;font-weight:800;fill:#fff">'+esc(l.mono)+'</text></svg>';}
  function persist(){return fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quicklinks:links.map(function(l){return {label:l.label,url:l.url};})})}).catch(function(){});}
  var INP='min-width:0;background:var(--chip);border:1px solid var(--hairline);border-radius:8px;padding:6px 9px;font-size:12px;color:var(--ink);outline:none';
  function render(){
    var nd={},i;for(i=0;i<links.length;i++)if(links[i].domain)nd[links[i].domain]=1;
    var h='<div class="w-sub" style="margin:0 1px 8px;font-size:11px;font-weight:600"><b class="num">'+links.length+'</b> link'+(links.length===1?'':'s')+' · <b class="num">'+Object.keys(nd).length+'</b> domain'+(Object.keys(nd).length===1?'':'s')+'</div>';
    if(links.length){
      h+='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:6px">'+links.map(function(l,idx){
        return '<a href="#" class="vk-ql" data-url="'+esc(l.url)+'" title="'+esc(l.domain||l.url)+'" style="text-decoration:none;color:var(--ink)">'+
          qchip(l,20)+
          '<span style="min-width:0;flex:1;font-weight:600;font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(l.label)+'</span>'+
          '<button class="vk-qlx" data-del="'+idx+'" title="Remove" aria-label="Remove"><svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></a>';}).join('')+'</div>';
    }else h+='<div class="hint">No links yet — add one below.</div>';
    h+='<div style="display:flex;gap:6px;align-items:center;margin-top:9px">'+
      '<input id="ql-lb" placeholder="Label" style="'+INP+';flex:0 1 30%">'+
      '<input id="ql-url" placeholder="example.com" style="'+INP+';flex:1">'+
      '<button id="ql-add" class="primary" style="flex:0 0 auto;padding:6px 13px;border-radius:8px;font-size:12px">Add</button></div>';
    var av=sugg.filter(function(s){return !nd[s.domain];});
    if(av.length){
      h+=vkLbl('Quick add');
      h+='<div style="display:flex;flex-wrap:wrap;gap:5px">'+av.map(function(s,i2){
        return '<button data-sg="'+i2+'" style="display:inline-flex;align-items:center;gap:6px;background:var(--chip);border:1px solid var(--hairline);border-radius:999px;padding:2.5px 9px 2.5px 3.5px;font-size:11px;color:var(--ink);cursor:pointer;font-weight:540">'+qchip(s,16)+esc(s.label)+'</button>';}).join('')+'</div>';
    }
    el.innerHTML=h;
    wireLinks(el);
    el.querySelectorAll('button[data-del]').forEach(function(b){
      b.onclick=function(e){e.preventDefault();e.stopPropagation();links.splice(+b.dataset.del,1);persist();render();};});
    var doAdd=function(){
      var lb=el.querySelector('#ql-lb'),ur=el.querySelector('#ql-url');
      if(!ur)return;var u=(ur.value||'').trim();if(!u)return;
      if(!/:\/\//.test(u))u='https://'+u;
      var dm=_dom(u),lab=((lb&&lb.value)||'').trim()||dm||u;
      links.push({label:lab,url:u,domain:dm,mono:_mono(lab,dm),hue:_hue(dm||lab)});
      persist();render();};
    var ab=el.querySelector('#ql-add');if(ab)ab.onclick=doAdd;
    ['#ql-url','#ql-lb'].forEach(function(q){var n=el.querySelector(q);
      if(n)n.onkeydown=function(e){if(e.key==='Enter'){e.preventDefault();doAdd();}};});
    el.querySelectorAll('button[data-sg]').forEach(function(b){b.onclick=function(e){
      e.preventDefault();var s=av[+b.dataset.sg];if(!s)return;
      links.push({label:s.label,url:s.url,domain:s.domain,mono:s.mono,hue:s.hue});
      persist();render();};});
  }
  render();
};

// ===== agent_pulse — heartbeat waveform + activity =====
EXPAND_RENDER.agent_pulse=function(el,d){
  if(!d||!d.available){el.innerHTML='<div class="hint">'+esc((d&&d.reason)||'No agent activity yet.')+'</div>';return;}
  var t=d.today||{},g=d.totals||{};
  // waveform synthesized from per-session tool counts (oldest -> newest)
  var ss=(d.sessions||[]).slice(0,28).reverse();
  var vals=ss.map(function(s){return +s.tools||0;});
  if(!vals.length)vals=[0];
  var W=320,BASE=33,mx=Math.max(1,Math.max.apply(null,vals));
  var n=vals.length,step=W/(n+1);
  var p='M0 '+BASE;
  vals.forEach(function(v,i){var x=step*(i+1);
    var hgt=v>0?7+22*Math.sqrt(v/mx):2.2;
    var und=v>0?Math.min(5,hgt*0.28):0.8;
    p+=' L'+(x-step*0.38).toFixed(1)+' '+BASE+
       ' L'+(x-step*0.13).toFixed(1)+' '+(BASE-hgt).toFixed(1)+
       ' L'+(x+step*0.1).toFixed(1)+' '+(BASE+und).toFixed(1)+
       ' L'+(x+step*0.34).toFixed(1)+' '+BASE;});
  p+=' L'+W+' '+BASE;
  var wave='<svg viewBox="0 0 '+W+' 44" preserveAspectRatio="none" style="display:block;width:100%;height:46px">'+
    '<line x1="0" y1="'+BASE+'" x2="'+W+'" y2="'+BASE+'" stroke="var(--hairline)" stroke-width="1" vector-effect="non-scaling-stroke"/>'+
    '<path d="'+p+'" fill="none" stroke="var(--iris)" stroke-width="1.6" stroke-linejoin="round" opacity=".85" vector-effect="non-scaling-stroke"/>'+
    '<path class="vk-ekg-run" d="'+p+'" pathLength="100" fill="none" stroke="var(--quick)" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" stroke-dasharray="10 90" vector-effect="non-scaling-stroke"/>'+
    '</svg>';
  var h='<div style="margin:2px 0 0">'+
    '<div class="vk-lbl" style="margin:0 0 2px"><span style="display:inline-flex;align-items:center;gap:6px"><span class="livedot"></span>Heartbeat · last '+n+' session'+(n===1?'':'s')+'</span><span class="vk-rule"></span>'+
    '<span class="num" style="text-transform:none;letter-spacing:0;color:var(--faint)">spike = tool calls</span></div>'+wave+'</div>';
  h+=statGrid([['Sessions today',kfmt(t.sessions)||'0'],['Tool calls today',kfmt(t.tool_calls)||'0'],['Tokens today',kfmt(t.tokens)||'0']]);
  h+='<div class="w-sub" style="margin:-4px 0 2px;font-size:11px">All time: <b class="num">'+kfmt(g.sessions)+'</b> sessions · <b class="num">'+kfmt(g.tool_calls)+'</b> tool calls · <b class="num">'+kfmt(g.tokens)+'</b> tokens</div>';
  var plats=d.platforms||[];
  if(plats.length){h+=vkLbl('Platforms')+
    barRows(plats.map(function(pf){return {label:pf.name.charAt(0).toUpperCase()+pf.name.slice(1),val:pf.sessions,sub:(pf.today||0)+' / '+pf.sessions};}),96,66);}
  var tools=d.top_tools||[];
  if(tools.length){var tmax=Math.max.apply(null,[1].concat(tools.map(function(x){return x.count||0;})));
    h+=vkLbl('Top tools')+
    tools.map(function(x){return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12px">'+
      '<span style="width:18px;display:flex;justify-content:center;color:var(--muted)">'+toolIcon(x.name)+'</span>'+
      '<span style="width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(x.name)+'</span>'+
      '<div style="flex:1;height:7px;border-radius:4px;background:var(--hairline);overflow:hidden"><i style="display:block;height:100%;width:'+Math.round((x.count||0)/tmax*100)+'%;background:linear-gradient(90deg,var(--iris),var(--quick))"></i></div>'+
      '<span class="num" style="width:34px;text-align:right">'+(x.count||0)+'</span></div>';}).join('');}
  var recent=(d.sessions||[]).slice(0,14);
  h+=vkLbl('Recent sessions');
  h+=recent.map(function(s){
    var meta=[s.msgs+' msg',s.tools+' tool'+(s.tools===1?'':'s'),kfmt(s.tokens)+' tok'];
    if(s.dur&&typeof _pulseDur==='function')meta.push(_pulseDur(s.dur));
    var lead=s.title?esc(s.title):esc(s.model||'session');
    return '<div class="pulse"><span class="pf">'+esc(s.source)+'</span><span class="pm"><span style="color:var(--ink)">'+lead+'</span><span style="display:block;color:var(--faint);font-size:10.5px;margin-top:1px">'+meta.join(' · ')+'</span></span><span class="pw">'+(s.ts?relTime(s.ts):'')+'</span></div>';
  }).join('')||'<div class="hint">No recent sessions.</div>';
  el.innerHTML=h;
};
