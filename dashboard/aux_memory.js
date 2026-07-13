// aux_memory.js — Editable Memory panel (P1.1). Served at /aux_memory.js,
// loaded after expand.js so it sees every inline helper (esc, animate, REDUCE,
// relTime, revealStagger). Takes over the Mind view "What it remembers" card.
// Zero emoji; bespoke two-tone SVG only; 12-hour times; Motion One entrances.
(function(){
  "use strict";

  var MEMP = {list:null, trash:null, limits:null, sel:null, file:null,
              etag:null, dirty:false, saving:false, timer:null, pending:null,
              retried:false, editing:-1};

  function el(id){ return document.getElementById(id); }
  function E(s){ return (typeof esc==='function') ? esc(s) : String(s==null?'':s); }
  function A(node, kf, opt){                 // safe animate (skipped by REDUCE)
    if(typeof REDUCE!=='undefined' && REDUCE) return;
    try{ if(typeof animate==='function') animate(node, kf, opt); }catch(e){}
  }

  // ---- bespoke two-tone SVG glyphs (accent fill @ .16 + currentColor stroke) ----
  var GLY = {
    plus:'<path d="M12 5.5v13M5.5 12h13" stroke-linecap="round"/>',
    pencil:'<path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1z" fill="currentColor" opacity=".16"/><path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1z" stroke-linejoin="round"/><path d="M14.5 6.5l3 3" stroke-linecap="round"/>',
    x:'<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" stroke-linecap="round"/>',
    trash:'<path d="M5 7h14l-1 12.5a2 2 0 0 1-2 1.9H8a2 2 0 0 1-2-1.9z" fill="currentColor" opacity=".16"/><path d="M5 7h14l-1 12.5a2 2 0 0 1-2 1.9H8a2 2 0 0 1-2-1.9z" stroke-linejoin="round"/><path d="M3.5 7h17M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7M10 11v6M14 11v6" stroke-linecap="round"/>',
    restore:'<path d="M4.2 9.4a8 8 0 1 1-1 5.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M3.6 4.4v5h5" stroke-linecap="round" stroke-linejoin="round"/>',
    lock:'<rect x="5" y="10.5" width="14" height="9.2" rx="2.1" fill="currentColor" opacity=".16"/><rect x="5" y="10.5" width="14" height="9.2" rx="2.1"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" stroke-linecap="round"/>',
    check:'<path d="M20 6.5L9.2 17.3 4.5 12.6" stroke-linecap="round" stroke-linejoin="round"/>',
    dot:'<circle cx="12" cy="12" r="5" fill="currentColor"/>',
    file:'<path d="M6.5 3.5h7l5 5v11a1.4 1.4 0 0 1-1.4 1.4H6.5A1.4 1.4 0 0 1 5.1 19.5V4.9A1.4 1.4 0 0 1 6.5 3.5z" fill="currentColor" opacity=".14"/><path d="M13 3.5v5h5" stroke-linejoin="round"/><path d="M6.5 3.5h7l5 5v11a1.4 1.4 0 0 1-1.4 1.4H6.5A1.4 1.4 0 0 1 5.1 19.5V4.9A1.4 1.4 0 0 1 6.5 3.5z" stroke-linejoin="round"/>'
  };
  function memIcon(kind, cls){
    return '<svg class="mic '+(cls||'')+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" '+
           'stroke-width="1.7" aria-hidden="true">'+(GLY[kind]||'')+'</svg>';
  }

  function mem12(ts){
    if(!ts) return '';
    try{ return new Date(ts*1000).toLocaleString('en-US',
      {month:'short',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true}); }
    catch(e){ return ''; }
  }
  function fbytes(n){
    n = n||0;
    if(n<1024) return n+' B';
    if(n<1048576) return (n/1024).toFixed(n<10240?1:0)+' KB';
    return (n/1048576).toFixed(1)+' MB';
  }
  function chipLabel(name){
    if(name==='USER.md') return 'Core facts';
    if(name==='MEMORY.md') return 'Agent notes';
    return name.replace(/\.md$/i,'');
  }

  // ---- one-time CSS ------------------------------------------------------
  function injectCss(){
    if(el('memcss')) return;
    var s = document.createElement('style');
    s.id = 'memcss';
    s.textContent = [
      '.mem-wrap{display:flex;flex-direction:column;gap:12px}',
      '.mem-banner{border-radius:12px;padding:9px 12px;font-size:12px;line-height:1.45;',
      'display:flex;align-items:center;gap:10px;flex-wrap:wrap}',
      '.mem-banner .bx{display:flex;gap:8px;margin-left:auto}',
      '.mem-banner.warn{background:color-mix(in srgb,var(--warn) 16%,transparent);',
      'border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);color:var(--ink)}',
      '.mem-banner.bad{background:color-mix(in srgb,var(--bad) 14%,transparent);',
      'border:1px solid color-mix(in srgb,var(--bad) 40%,transparent);color:var(--ink)}',
      '.mem-banner.info{background:var(--glass-2);border:1px solid var(--hairline);color:var(--muted)}',
      '.mem-strip{display:flex;gap:8px;flex-wrap:wrap;align-items:center}',
      '.mem-chip{display:inline-flex;align-items:center;gap:7px;padding:6px 11px;border-radius:999px;',
      'border:1px solid var(--hairline);background:var(--glass-2);color:var(--muted);font-size:12px;',
      'font-weight:600;cursor:pointer;white-space:nowrap;transition:background .15s,color .15s,border-color .15s}',
      '.mem-chip:hover{color:var(--ink)}',
      '.mem-chip.on{background:color-mix(in srgb,var(--iris) 20%,transparent);',
      'border-color:color-mix(in srgb,var(--iris) 55%,transparent);color:var(--ink)}',
      '.mem-chip.ghost{color:var(--faint);border-style:dashed}',
      '.mem-chip .cdot{width:6px;height:6px;border-radius:50%;flex:0 0 auto}',
      '.mem-chip .cdot.you{background:var(--iris)}',
      '.mem-chip .cdot.agent{background:var(--faint)}',
      '.mem-chip .mic{width:14px;height:14px}',
      '.mem-detail{display:flex;flex-direction:column;gap:10px}',
      '.mem-metaline{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--muted)}',
      '.mem-prov{display:inline-flex;align-items:center;gap:6px;font-weight:600}',
      '.mem-prov .cdot{width:6px;height:6px;border-radius:50%}',
      '.mem-prov .cdot.you{background:var(--iris)}.mem-prov .cdot.agent{background:var(--faint)}',
      '.mem-meter{flex:1 1 120px;min-width:90px;max-width:260px;height:5px;border-radius:3px;',
      'background:var(--hairline);overflow:hidden;position:relative}',
      '.mem-meter i{position:absolute;left:0;top:0;height:100%;border-radius:3px;',
      'background:var(--iris);transition:width .3s ease}',
      '.mem-meter.warn i{background:var(--warn)}.mem-meter.bad i{background:var(--bad)}',
      '.mem-meter-n{font-size:10.5px;color:var(--faint);white-space:nowrap}',
      '.mem-rows{display:flex;flex-direction:column;gap:6px}',
      '.mem-row{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-radius:10px;',
      'background:var(--glass-2);border:1px solid var(--hairline);transition:opacity .2s}',
      '.mem-row .t{flex:1;min-width:0;font-size:13px;line-height:1.45;word-break:break-word;white-space:pre-wrap}',
      '.mem-row.saving{opacity:.5}',
      '.mem-row input,.mem-composer input{flex:1;min-width:0;font:inherit;font-size:13px;',
      'padding:6px 9px;border-radius:8px;border:1px solid color-mix(in srgb,var(--iris) 45%,transparent);',
      'background:var(--glass);color:var(--ink);outline:none}',
      '.mem-iconbtn{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;',
      'width:26px;height:26px;border-radius:7px;border:1px solid transparent;background:transparent;',
      'color:var(--faint);cursor:pointer;transition:background .15s,color .15s}',
      '.mem-iconbtn:hover{background:var(--glass);color:var(--ink)}',
      '.mem-iconbtn.ok:hover{color:var(--ok)}.mem-iconbtn.del:hover{color:var(--bad)}',
      '.mem-iconbtn .mic{width:16px;height:16px}',
      '.mem-composer{display:flex;align-items:center;gap:8px;margin-top:2px}',
      '.mem-btn{font:inherit;font-size:12px;font-weight:600;padding:6px 13px;border-radius:8px;',
      'border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);cursor:pointer;',
      'white-space:nowrap;transition:background .15s,border-color .15s}',
      '.mem-btn:hover{background:var(--glass)}',
      '.mem-btn.pri{background:color-mix(in srgb,var(--iris) 22%,transparent);',
      'border-color:color-mix(in srgb,var(--iris) 55%,transparent)}',
      '.mem-btn.pri:hover{background:color-mix(in srgb,var(--iris) 32%,transparent)}',
      '.mem-btn.danger{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 35%,transparent)}',
      '.mem-btn[disabled]{opacity:.45;cursor:not-allowed}',
      '.mem-ta{width:100%;box-sizing:border-box;min-height:180px;resize:vertical;font-size:12.5px;',
      'line-height:1.55;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:11px 12px;',
      'border-radius:11px;border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);outline:none}',
      '.mem-ta:focus{border-color:color-mix(in srgb,var(--iris) 45%,transparent)}',
      '.mem-danger{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:2px;',
      'padding-top:9px;border-top:1px solid var(--hairline);font-size:11.5px;color:var(--faint)}',
      '.mem-danger .bx{margin-left:auto}',
      '.mem-inlinehint{font-size:11.5px;color:var(--bad)}',
      '.mem-secret{font-size:11px;color:var(--faint);line-height:1.4}',
      '.mem-trash{border-top:1px solid var(--hairline);padding-top:8px}',
      '.mem-trash-h{display:inline-flex;align-items:center;gap:7px;font-size:11.5px;font-weight:600;',
      'color:var(--muted);cursor:pointer;background:none;border:none;padding:2px 0}',
      '.mem-trash-h .chev{transition:transform .2s}.mem-trash.open .mem-trash-h .chev{transform:rotate(90deg)}',
      '.mem-trash-body{display:none;flex-direction:column;gap:6px;margin-top:8px}',
      '.mem-trash.open .mem-trash-body{display:flex}',
      '.mem-trashrow{display:flex;align-items:center;gap:9px;font-size:12px;color:var(--muted)}',
      '.mem-trashrow .n{color:var(--ink);font-weight:600}',
      '.mem-trashrow .w{color:var(--faint);font-size:11px}.mem-trashrow .bx{margin-left:auto}',
      '@media (prefers-reduced-motion:reduce){.mem-meter i,.mem-chip{transition:none}}'
    ].join('');
    document.head.appendChild(s);
  }

  // ---- banners -----------------------------------------------------------
  function bannerClear(){ var b=el('mem-banner'); if(b) b.innerHTML=''; }
  function bannerShow(cls, html){
    var b = el('mem-banner'); if(!b) return;
    b.innerHTML = '<div class="mem-banner '+cls+'">'+html+'</div>';
    A(b.firstChild, {opacity:[0,1], transform:['translateY(-6px)','translateY(0)']},
      {duration:0.18, easing:'ease-out'});
    return b.firstChild;
  }

  // ---- top-level render --------------------------------------------------
  function renderMemoryPanel(mem){
    var ml = el('mem-list');
    if(!ml) return;
    injectCss();
    var card = ml.closest ? ml.closest('.card') : null;
    if(card) card.classList.add('span2');
    ml.innerHTML =
      '<div class="mem-wrap">'+
        '<div id="mem-banner"></div>'+
        '<div id="mem-strip" class="mem-strip"></div>'+
        '<div id="mem-detail" class="mem-detail"></div>'+
        '<div id="mem-trash" class="mem-trash"></div>'+
      '</div>';
    memFetchList(true);
    startPoll();
  }

  function startPoll(){
    if(MEMP.timer){ clearInterval(MEMP.timer); MEMP.timer=null; }
    MEMP.timer = setInterval(pollTick, 15000);
  }

  async function memFetchList(preferCore){
    var r;
    try{ r = await fetch('/api/memory/list',{cache:'no-store'}); }
    catch(e){ return listError(String(e && e.message || e)); }
    var d;
    try{ d = await r.json(); }catch(e){ return listError('bad response'); }
    if(!d || !d.ok) return listError((d && d.error) || ('HTTP '+r.status));
    bannerClear();
    MEMP.list = d.files || [];
    MEMP.trash = d.trash || [];
    MEMP.limits = d.limits || {};
    // choose a selection
    var names = MEMP.list.map(function(f){ return f.name; });
    if(!MEMP.sel || names.indexOf(MEMP.sel)<0){
      MEMP.sel = null;
      if(preferCore && names.indexOf('USER.md')>=0) MEMP.sel='USER.md';
      else if(names.length) MEMP.sel=names[0];
    }
    renderStrip();
    renderTrash();
    if(MEMP.sel) memOpen(MEMP.sel, true);
    else renderDetailHint();
  }

  function listError(msg){
    var d = el('mem-detail');
    bannerShow('bad','Memory panel unavailable — '+E(msg)+
      '<span class="bx"><button class="mem-btn" id="mem-retry">Retry</button></span>');
    var rb = el('mem-retry'); if(rb) rb.addEventListener('click', function(){ memFetchList(true); });
    if(d && !d.innerHTML) d.innerHTML='';
  }

  function renderDetailHint(){
    var d = el('mem-detail'); if(!d) return;
    d.innerHTML = '<div class="hint">Nothing stored yet. As you chat, Hermes writes durable '+
      'facts about you here (its built-in USER.md memory) and recalls them in future '+
      'conversations — on every platform. Add one now, or create a topic file.</div>';
  }

  // ---- file strip --------------------------------------------------------
  function renderStrip(){
    var strip = el('mem-strip'); if(!strip) return;
    var names = MEMP.list.map(function(f){ return f.name; });
    var h = '';
    MEMP.list.forEach(function(f){
      var who = f.last_writer==='user' ? 'you' : 'agent';
      h += '<button class="mem-chip'+(f.name===MEMP.sel?' on':'')+'" data-open="'+E(f.name)+'">'+
             '<span class="cdot '+(who==='you'?'you':'agent')+'"></span>'+
             E(chipLabel(f.name))+'</button>';
    });
    if(names.indexOf('MEMORY.md')<0){
      h += '<button class="mem-chip ghost" data-create="MEMORY.md">'+
             '<span class="cdot agent"></span>Agent notes — not created</button>';
    }
    h += '<button class="mem-chip ghost" id="mem-new">'+memIcon('plus')+'New file</button>';
    strip.innerHTML = h;
    [].forEach.call(strip.querySelectorAll('[data-open]'), function(b){
      b.addEventListener('click', function(){ memOpen(b.getAttribute('data-open')); });
    });
    [].forEach.call(strip.querySelectorAll('[data-create]'), function(b){
      b.addEventListener('click', function(){ memCreate(b.getAttribute('data-create'), true); });
    });
    var nb = el('mem-new');
    if(nb) nb.addEventListener('click', newFilePrompt);
  }

  function newFilePrompt(){
    var raw = window.prompt('Name the new memory file (letters, numbers, dashes):','');
    if(raw==null) return;
    var name = raw.trim();
    if(!name) return;
    if(!/\.md$/i.test(name)) name += '.md';
    if(!/^[A-Za-z0-9][A-Za-z0-9._ -]{0,62}\.md$/.test(name)){
      bannerShow('bad','That name isn’t allowed — use letters, numbers, dots, spaces or dashes, ending in .md.');
      return;
    }
    memCreate(name);
  }

  // ---- open a file -------------------------------------------------------
  async function memOpen(name, silent){
    MEMP.sel = name; MEMP.dirty=false; MEMP.editing=-1; MEMP.pending=null; MEMP.retried=false;
    if(!silent) bannerClear();
    renderStrip();
    var d = el('mem-detail'); if(!d) return;
    var r, body;
    try{ r = await fetch('/api/memory/file?name='+encodeURIComponent(name),{cache:'no-store'}); }
    catch(e){ d.innerHTML='<div class="hint">Couldn’t load '+E(name)+' — '+E(String(e))+'</div>'; return; }
    try{ body = await r.json(); }catch(e){ body={ok:false,error:'bad response'}; }
    if(!body.ok){
      if(body.error==='too_big_to_edit')
        d.innerHTML='<div class="hint">'+E(body.hint||'This file is too large to edit here.')+'</div>';
      else if(body.error==='not_utf8')
        d.innerHTML='<div class="hint">'+E(body.hint||'This file isn’t valid UTF-8 and can’t be edited here.')+'</div>';
      else if(body.error==='missing')
        d.innerHTML='<div class="hint">'+E(name)+' is no longer on disk.</div>';
      else d.innerHTML='<div class="hint">Couldn’t open '+E(name)+' — '+E(body.error||'')+'</div>';
      return;
    }
    MEMP.file = body; MEMP.etag = body.etag;
    if(body.kind==='entries') memRenderEntries(body);
    else memRenderFreeform(body);
    A(d,{opacity:[0,1],transform:['translateY(6px)','translateY(0)']},{duration:0.22,easing:'ease-out'});
  }

  function provChip(f){
    var you = f.last_writer==='user';
    return '<span class="mem-prov"><span class="cdot '+(you?'you':'agent')+'"></span>'+
           (you?'You edited':'Hermes wrote this')+
           (f.last_writer_at?(' · '+E(mem12(f.last_writer_at))):'')+'</span>';
  }

  function memMeter(used, limit){
    var pct = limit>0 ? Math.min(100, Math.round(used/limit*100)) : 0;
    var cls = used>=limit ? 'bad' : (pct>85 ? 'warn' : '');
    return '<span class="mem-meter '+cls+'"><i style="width:'+pct+'%"></i></span>'+
           '<span class="mem-meter-n">'+used+' / '+limit+'</span>';
  }

  // ---- entries editor (core files) ---------------------------------------
  function memRenderEntries(f){
    var d = el('mem-detail'); if(!d) return;
    var ents = f.entries || [];
    var full = ents.length >= 1 && f.char_used >= (f.char_limit||1);
    var h = '<div class="mem-metaline">'+provChip(f)+
            '<span>· '+fbytes(f.size)+'</span>'+
            memMeter(f.char_used||0, f.char_limit||0)+'</div>';
    h += '<div class="mem-rows" id="mem-rows">';
    if(!ents.length) h += '<div class="hint">No facts yet — add the first one below.</div>';
    ents.forEach(function(t,i){
      h += '<div class="mem-row" data-i="'+i+'"><span class="t">'+E(t)+'</span>'+
           '<button class="mem-iconbtn" data-edit="'+i+'" title="Edit">'+memIcon('pencil')+'</button>'+
           '<button class="mem-iconbtn del" data-rm="'+i+'" title="Remove">'+memIcon('x')+'</button></div>';
    });
    h += '</div>';
    h += '<div class="mem-composer"><input id="mem-add" type="text" placeholder="Add a fact…" '+
         'maxlength="1000" autocomplete="off"><button class="mem-btn pri" id="mem-addb"'+
         (full?' disabled':'')+'>Add</button></div>';
    h += '<div class="mem-inlinehint" id="mem-limithint"'+(full?'':' style="display:none"')+'>'+
         'Core memory is full ('+(f.char_limit||0)+' chars). Remove or shorten a fact to add more.</div>';
    h += '<div class="mem-danger"><span>Core memory can be emptied, never deleted.</span></div>';
    h += '<div class="mem-secret">Memory is injected into the model’s context — don’t store passwords or keys here.</div>';
    d.innerHTML = h;

    var rows = d.querySelectorAll('.mem-row');
    if(typeof revealStagger==='function') revealStagger(rows, 45);
    [].forEach.call(d.querySelectorAll('[data-edit]'), function(b){
      b.addEventListener('click', function(){ startEditFact(parseInt(b.getAttribute('data-edit'),10)); });
    });
    [].forEach.call(d.querySelectorAll('[data-rm]'), function(b){
      b.addEventListener('click', function(){ memDeleteFact(parseInt(b.getAttribute('data-rm'),10)); });
    });
    var add = el('mem-add'), addb = el('mem-addb');
    function doAdd(){
      var v = (add.value||'').trim();
      if(!v) return;
      memAddFact(v);
    }
    if(addb) addb.addEventListener('click', doAdd);
    if(add){
      add.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); doAdd(); } });
      add.addEventListener('input', function(){ MEMP.dirty = !!add.value; });
    }
  }

  function startEditFact(i){
    var d = el('mem-detail'); if(!d) return;
    var row = d.querySelector('.mem-row[data-i="'+i+'"]'); if(!row) return;
    var cur = (MEMP.file.entries||[])[i] || '';
    MEMP.editing = i; MEMP.dirty = true;
    row.innerHTML = '<input type="text" value="'+E(cur)+'" maxlength="1000">'+
      '<button class="mem-iconbtn ok" data-save="1" title="Save">'+memIcon('check')+'</button>'+
      '<button class="mem-iconbtn" data-cancel="1" title="Cancel">'+memIcon('x')+'</button>';
    var inp = row.querySelector('input'); inp.focus(); inp.select();
    function save(){
      var v = (inp.value||'').trim();
      if(!v){ memDeleteFact(i); return; }
      memEditFact(i, v);
    }
    inp.addEventListener('keydown', function(e){
      if(e.key==='Enter'){ e.preventDefault(); save(); }
      if(e.key==='Escape'){ e.preventDefault(); MEMP.editing=-1; MEMP.dirty=false; memRenderEntries(MEMP.file); }
    });
    row.querySelector('[data-save]').addEventListener('click', save);
    row.querySelector('[data-cancel]').addEventListener('click', function(){
      MEMP.editing=-1; MEMP.dirty=false; memRenderEntries(MEMP.file);
    });
  }

  function curEntries(){ return (MEMP.file.entries||[]).slice(); }

  function memAddFact(text){
    var next = curEntries(); next.push(text);
    memSave(MEMP.sel, function(body){ body.entries = next; }, next);
  }
  function memEditFact(i, text){
    var next = curEntries(); next[i] = text;
    memSave(MEMP.sel, function(body){ body.entries = next; }, next);
  }
  function memDeleteFact(i){
    var next = curEntries(); next.splice(i,1);
    memSave(MEMP.sel, function(body){ body.entries = next; }, next);
  }

  // ---- freeform editor ---------------------------------------------------
  function memRenderFreeform(f){
    var d = el('mem-detail'); if(!d) return;
    var max = (MEMP.limits && MEMP.limits.max_file_bytes) || 131072;
    var h = '<div class="mem-metaline">'+provChip(f)+'<span>· '+fbytes(f.size)+'</span>'+
            '<span class="mem-meter-n" id="mem-cc" style="margin-left:auto"></span></div>';
    h += '<textarea class="mem-ta" id="mem-ta" spellcheck="false"></textarea>';
    h += '<div class="mem-inlinehint" id="mem-limithint" style="display:none">'+
         'This file is over the '+Math.round(max/1024)+' KB limit — trim it before saving.</div>';
    h += '<div class="mem-composer" id="mem-ff-actions" style="display:none">'+
         '<button class="mem-btn pri" id="mem-save">Save</button>'+
         '<button class="mem-btn" id="mem-revert">Revert</button></div>';
    h += '<div class="mem-danger"><span>Deleting moves this file to the dashboard trash — you can restore it.</span>'+
         '<span class="bx"><button class="mem-btn danger" id="mem-del">Delete file</button></span></div>';
    h += '<div class="mem-secret">Memory is injected into the model’s context — don’t store passwords or keys here.</div>';
    d.innerHTML = h;
    var ta = el('mem-ta'); ta.value = f.content || '';
    autogrow(ta);
    var orig = f.content || '';
    function cc(){
      var by = (new TextEncoder().encode(ta.value)).length;
      el('mem-cc').textContent = by+' / '+max+' bytes';
      var over = by>max, dirty = ta.value!==orig;
      MEMP.dirty = dirty;
      el('mem-ff-actions').style.display = dirty ? 'flex' : 'none';
      el('mem-limithint').style.display = over ? 'block' : 'none';
      var sb = el('mem-save'); if(sb) sb.disabled = over;
    }
    cc();
    ta.addEventListener('input', function(){ autogrow(ta); cc(); });
    el('mem-save').addEventListener('click', function(){
      memSave(MEMP.sel, function(body){ body.content = ta.value; }, null);
    });
    el('mem-revert').addEventListener('click', function(){ ta.value=orig; autogrow(ta); cc(); });
    el('mem-del').addEventListener('click', function(){ memDelete(MEMP.sel); });
    A(ta,{opacity:[0,1]},{duration:0.2});
  }
  function autogrow(ta){ ta.style.height='auto'; ta.style.height=Math.max(180, ta.scrollHeight+2)+'px'; }

  // ---- save pipeline (shared) --------------------------------------------
  async function memSave(name, patchFn, optimisticEntries){
    if(MEMP.saving) return;
    MEMP.saving = true;
    var body = {name:name, base_etag: MEMP.etag};
    patchFn(body);
    MEMP.pending = {name:name, body:body};
    // optimistic UI for entries
    if(optimisticEntries){
      MEMP.file.entries = optimisticEntries;
      MEMP.editing = -1;
      memRenderEntries(MEMP.file);
      var rows = document.querySelectorAll('#mem-rows .mem-row');
      [].forEach.call(rows, function(r){ r.classList.add('saving'); });
    }
    var r, resp;
    try{
      r = await fetch('/api/memory/save',{method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
      resp = await r.json();
    }catch(e){
      MEMP.saving=false;
      bannerShow('bad','Save failed — '+E(String(e))+'. Your change was not stored.');
      return reloadFile();
    }
    MEMP.saving = false;
    if(resp.ok){
      MEMP.pending = null; MEMP.retried=false;
      MEMP.etag = resp.etag;
      MEMP.file.etag = resp.etag; MEMP.file.mtime = resp.mtime; MEMP.file.size = resp.size;
      MEMP.file.last_writer='user'; MEMP.file.last_writer_at = Math.floor(Date.now()/1000);
      if(resp.char_used!=null) MEMP.file.char_used = resp.char_used;
      bannerClear();
      MEMP.dirty=false;
      if(MEMP.file.kind==='entries'){ memRenderEntries(MEMP.file); }
      else { if(body.content!=null) MEMP.file.content=body.content; memRenderFreeform(MEMP.file); }
      // refresh the strip chip provenance dot + trash counts in the background
      quietRelist();
      return;
    }
    // error paths
    if(r.status===423 || resp.error==='locked'){
      bannerShow('warn','Hermes is writing to this file — retrying in a moment…');
      if(!MEMP.retried){
        MEMP.retried = true;
        setTimeout(function(){ retryPending(); }, 1500);
      } else {
        bannerShow('warn','Hermes is still writing — try again shortly.'+
          '<span class="bx"><button class="mem-btn" id="mem-retry2">Retry</button></span>');
        var rb=el('mem-retry2'); if(rb) rb.addEventListener('click', retryPending);
        reloadFile();
      }
      return;
    }
    if(r.status===409 || resp.error==='conflict'){ return memConflictBanner(resp.current); }
    if(resp.error==='over_limit'){
      var lh = el('mem-limithint');
      if(lh){ lh.textContent='Over the '+resp.char_limit+'-char limit ('+resp.char_used+'). Remove or shorten a fact.'; lh.style.display='block'; }
      else bannerShow('bad','Over the character limit ('+resp.char_used+'/'+resp.char_limit+').');
      return reloadFile();
    }
    if(resp.error==='missing'){
      bannerShow('bad',E(name)+' is gone on disk.'+
        '<span class="bx"><button class="mem-btn pri" id="mem-recreate">Re-create with your content</button></span>');
      var cb=el('mem-recreate');
      if(cb) cb.addEventListener('click', function(){ recreatePending(); });
      return;
    }
    bannerShow('bad','Couldn’t save — '+E(resp.error||('HTTP '+r.status))+'.');
    reloadFile();
  }

  function retryPending(){
    if(!MEMP.pending) return;
    var p = MEMP.pending;
    memSave(p.name, function(b){ for(var k in p.body) b[k]=p.body[k]; },
      p.body.entries || null);
  }

  async function recreatePending(){
    if(!MEMP.pending) return;
    var p = MEMP.pending, r, resp;
    var cbody = {name:p.name};
    if(p.body.entries){ /* create expects content; core file recreated with entries via join is not supported by create's content path */ }
    // For freeform re-create with the pending content; for entries, join to send as content is wrong,
    // so recreate empty then save. Simplest robust path: create empty, then re-save.
    try{
      r = await fetch('/api/memory/create',{method:'POST',headers:{'Content-Type':'application/json'},
        body: JSON.stringify(p.body.entries!=null ? {name:p.name, content:''} : {name:p.name, content:p.body.content||''})});
      resp = await r.json();
    }catch(e){ bannerShow('bad','Re-create failed — '+E(String(e))); return; }
    if(!resp.ok){ bannerShow('bad','Re-create failed — '+E(resp.error||('HTTP '+r.status))); return; }
    MEMP.etag = resp.file && resp.file.etag;
    await memFetchList(false);
    memOpen(p.name);
    // if it was an entries edit, replay the entries save over the fresh empty file
    if(p.body.entries!=null){
      setTimeout(function(){ memSave(p.name, function(b){ b.entries=p.body.entries; }, p.body.entries); }, 150);
    }
  }

  function reloadFile(){ if(MEMP.sel) memOpen(MEMP.sel, true); }

  // ---- conflict banner ---------------------------------------------------
  function memConflictBanner(cur){
    MEMP.saving=false;
    var b = bannerShow('warn','Hermes updated this file while you were editing.'+
      '<span class="bx"><button class="mem-btn" id="mem-loadtheirs">Load theirs</button>'+
      '<button class="mem-btn pri" id="mem-keepmine">Keep mine</button></span>');
    var lt=el('mem-loadtheirs'), km=el('mem-keepmine');
    if(lt) lt.addEventListener('click', function(){
      // adopt the server's current version
      if(cur){
        MEMP.etag = cur.etag; MEMP.file.etag=cur.etag; MEMP.file.mtime=cur.mtime;
        MEMP.file.content = cur.content; MEMP.file.last_writer = cur.last_writer||'agent';
        if(cur.entries) MEMP.file.entries = cur.entries;
        if(MEMP.file.kind==='entries') MEMP.file.char_used = (cur.content||'').length;
      }
      MEMP.dirty=false; MEMP.pending=null; bannerClear();
      if(MEMP.file.kind==='entries') memRenderEntries(MEMP.file); else memRenderFreeform(MEMP.file);
    });
    if(km) km.addEventListener('click', async function(){
      // re-read fresh etag, then save our pending payload over it (their bytes are
      // already snapshotted server-side, so nothing is lost)
      if(!MEMP.pending){ bannerClear(); return; }
      var p = MEMP.pending;
      try{
        var fr = await fetch('/api/memory/file?name='+encodeURIComponent(p.name),{cache:'no-store'});
        var fj = await fr.json();
        if(fj.ok) MEMP.etag = fj.etag;
      }catch(e){}
      bannerClear();
      memSave(p.name, function(bd){ for(var k in p.body) if(k!=='base_etag') bd[k]=p.body[k]; },
        p.body.entries || null);
    });
  }

  // ---- create / delete / restore ----------------------------------------
  async function memCreate(name, ghost){
    var r, resp;
    try{
      r = await fetch('/api/memory/create',{method:'POST',headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:name, content:''})});
      resp = await r.json();
    }catch(e){ bannerShow('bad','Couldn’t create '+E(name)+' — '+E(String(e))); return; }
    if(!resp.ok){
      if(resp.error==='exists') bannerShow('bad','A file named '+E(name)+' already exists.');
      else bannerShow('bad','Couldn’t create '+E(name)+' — '+E(resp.error||('HTTP '+r.status)));
      return;
    }
    bannerClear();
    MEMP.sel = name;
    await memFetchList(false);
    memOpen(name);
  }

  async function memDelete(name){
    if(!window.confirm('Move '+name+' to the dashboard trash? You can restore it.')) return;
    var d = el('mem-detail');
    var chip = document.querySelector('#mem-strip [data-open="'+name+'"]');
    if(chip) A(chip,{opacity:[1,0],transform:['scale(1)','scale(.85)']},{duration:0.2});
    var r, resp;
    try{
      r = await fetch('/api/memory/delete',{method:'POST',headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:name})});
      resp = await r.json();
    }catch(e){ bannerShow('bad','Delete failed — '+E(String(e))); return; }
    if(!resp.ok){
      if(r.status===423) bannerShow('warn','Hermes is writing to this file — try again in a moment.');
      else if(resp.error==='core_file') bannerShow('bad', E(resp.hint||'Core memory can’t be deleted.'));
      else bannerShow('bad','Couldn’t delete '+E(name)+' — '+E(resp.error||('HTTP '+r.status)));
      return;
    }
    bannerClear();
    MEMP.sel = null;
    await memFetchList(true);
  }

  async function memRestore(trashName){
    var r, resp;
    try{
      r = await fetch('/api/memory/restore',{method:'POST',headers:{'Content-Type':'application/json'},
        body: JSON.stringify({trash_name:trashName})});
      resp = await r.json();
    }catch(e){ bannerShow('bad','Restore failed — '+E(String(e))); return; }
    if(!resp.ok){ bannerShow('bad','Couldn’t restore — '+E(resp.error||('HTTP '+r.status))); return; }
    bannerClear();
    MEMP.sel = resp.name;
    await memFetchList(false);
    if(resp.name) memOpen(resp.name);
  }

  // ---- trash disclosure --------------------------------------------------
  function renderTrash(){
    var t = el('mem-trash'); if(!t) return;
    var items = MEMP.trash || [];
    if(!items.length){ t.innerHTML=''; return; }
    var open = t.classList.contains('open');
    var h = '<button class="mem-trash-h" id="mem-trash-h">'+
      '<svg class="mic chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '+
      'style="width:13px;height:13px"><path d="M9 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>'+
      'Recently deleted ('+items.length+')</button>';
    h += '<div class="mem-trash-body">';
    items.forEach(function(it){
      h += '<div class="mem-trashrow"><span class="n">'+E(it.orig)+'</span>'+
           '<span class="w">deleted '+E(mem12(it.deleted_at))+' · '+fbytes(it.size)+'</span>'+
           '<span class="bx"><button class="mem-iconbtn ok" data-restore="'+E(it.trash_name)+'" '+
           'title="Restore">'+memIcon('restore')+'</button></span></div>';
    });
    h += '</div>';
    t.innerHTML = h;
    if(open) t.classList.add('open');
    el('mem-trash-h').addEventListener('click', function(){ t.classList.toggle('open'); });
    [].forEach.call(t.querySelectorAll('[data-restore]'), function(b){
      b.addEventListener('click', function(){ memRestore(b.getAttribute('data-restore')); });
    });
  }

  // ---- background relist (chips/trash) without disturbing the editor -----
  async function quietRelist(){
    try{
      var r = await fetch('/api/memory/list',{cache:'no-store'});
      var d = await r.json();
      if(d && d.ok){ MEMP.list=d.files||[]; MEMP.trash=d.trash||[]; renderStrip(); renderTrash(); }
    }catch(e){}
  }

  // ---- 15s etag poll -----------------------------------------------------
  async function pollTick(){
    if(typeof curView!=='undefined' && curView!=='mind') return;
    if(!MEMP.sel || MEMP.saving || !MEMP.file) return;
    var r, body;
    try{ r = await fetch('/api/memory/file?name='+encodeURIComponent(MEMP.sel),{cache:'no-store'}); }
    catch(e){ return; }
    try{ body = await r.json(); }catch(e){ return; }
    if(!body.ok) return;
    if(body.etag === MEMP.etag) return;
    if(MEMP.dirty || MEMP.editing>=0){
      // surface as a conflict-style banner (their bytes are snapshotted on save)
      bannerShow('warn','Hermes updated this file while you were editing.'+
        '<span class="bx"><button class="mem-btn" id="mem-poll-load">Load theirs</button>'+
        '<button class="mem-btn pri" id="mem-poll-keep">Keep mine</button></span>');
      var lb=el('mem-poll-load'), kb=el('mem-poll-keep');
      if(lb) lb.addEventListener('click', function(){
        MEMP.dirty=false; MEMP.editing=-1; MEMP.file=body; MEMP.etag=body.etag; bannerClear();
        if(body.kind==='entries') memRenderEntries(body); else memRenderFreeform(body);
      });
      if(kb) kb.addEventListener('click', function(){
        MEMP.etag = body.etag;         // adopt fresh etag; save over it
        bannerClear();
        if(MEMP.file.kind==='entries') memSave(MEMP.sel, function(b){ b.entries = curEntries(); }, curEntries());
        else { var ta=el('mem-ta'); if(ta) memSave(MEMP.sel, function(b){ b.content=ta.value; }, null); }
      });
    } else {
      // silent refresh
      MEMP.file = body; MEMP.etag = body.etag;
      if(body.kind==='entries') memRenderEntries(body); else memRenderFreeform(body);
      bannerShow('info','Hermes just updated this.');
      setTimeout(bannerClear, 4000);
      renderStrip();
    }
  }

  window.renderMemoryPanel = renderMemoryPanel;
})();
