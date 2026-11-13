"""Shared helper for the chat-UI suites (k03): pull REAL functions out of
index.html's inline <script> and run them in plain node against a tiny fake
DOM, a scripted fetch and an immediate setTimeout.

Not a suite itself (the runner skips files starting with `_`).

A top-level function in index.html's inline script starts at column 0
(`function name(` / `async function name(`) and ends at the first later line
that is exactly `}` at column 0 — the file's own layout, which is what lets
this stay a dozen lines instead of a JS parser. A one-line function is taken
as its single line.
"""
import json
import os
import re
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
INDEX = os.path.join(REPO, "dashboard", "index.html")


def index_src():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def extract(name, src=None):
    """Source of top-level function `name` from index.html, or None."""
    src = index_src() if src is None else src
    lines = src.split("\n")
    pat = re.compile(r"^(async\s+)?function\s+%s\s*\(" % re.escape(name))
    for i, line in enumerate(lines):
        if pat.match(line):
            if line.rstrip().endswith("}") and line.count("{") == line.count("}"):
                return line
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("}"):
                    return "\n".join(lines[i:j + 1])
    return None


def extract_line(prefix, src=None):
    """The first line of index.html that starts with `prefix`, or None."""
    src = index_src() if src is None else src
    for line in src.split("\n"):
        if line.startswith(prefix):
            return line
    return None


# A DOM just big enough for the chat column: elements with children, classes,
# dataset, attributes, textContent/innerHTML, querySelector(All) on a handful
# of selector shapes (".cls", "tag", "#id"), remove() and isConnected.
FAKE_DOM = r"""
class El {
  constructor(tag){ this.tagName=String(tag).toUpperCase(); this.children=[]; this.parent=null;
    this.dataset={}; this.attrs={}; this.style={}; this.className=''; this._text=''; this._html='';
    this.disabled=false; this.hidden=false; this.value='';
    const self=this; this.classList={
      add:(...c)=>{const s=new Set(self.className.split(/\s+/).filter(Boolean));c.forEach(x=>s.add(x));self.className=[...s].join(' ');},
      remove:(...c)=>{self.className=self.className.split(/\s+/).filter(x=>x&&!c.includes(x)).join(' ');},
      contains:(c)=>self.className.split(/\s+/).includes(c),
      toggle:(c,on)=>{const has=self.classList.contains(c);const want=on===undefined?!has:!!on;want?self.classList.add(c):self.classList.remove(c);return want;}};
  }
  setAttribute(k,v){ this.attrs[k]=String(v); if(k==='id')this.id=String(v); }
  getAttribute(k){ return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c){ if(c.parent)c.remove(); c.parent=this; this.children.push(c); return c; }
  insertAdjacentHTML(pos,h){ this._html+=h; }
  remove(){ if(this.parent){ const p=this.parent; p.children=p.children.filter(x=>x!==this); this.parent=null; } }
  get isConnected(){ let n=this; while(n){ if(n===DOC_ROOT)return true; n=n.parent; } return false; }
  get textContent(){ return this._text + this.children.map(c=>c.textContent).join(''); }
  set textContent(v){ this.children=[]; this._text=String(v); this._html=''; }
  get innerText(){ return this.textContent; }
  get innerHTML(){ return this._html; }
  set innerHTML(v){
    this.children.forEach(c=>{c.parent=null;}); this.children=[]; this._text=''; this._html=String(v);
    // materialise the few structural children the chat code queries for
    const re=/<(div|span|code|button|section)([^>]*)>/g; let m;
    while((m=re.exec(this._html))){ const el=new El(m[1]); const cm=/class="([^"]*)"/.exec(m[2]); if(cm)el.className=cm[1];
      const im=/id="([^"]*)"/.exec(m[2]); if(im){el.id=im[1];} this.appendChild(el); }
  }
  _all(){ const out=[]; const walk=n=>{ n.children.forEach(c=>{out.push(c);walk(c);}); }; walk(this); return out; }
  _match(el,sel){ sel=sel.trim();
    if(sel.startsWith('.')) return el.className.split(/\s+/).includes(sel.slice(1));
    if(sel.startsWith('#')) return el.id===sel.slice(1);
    return el.tagName===sel.toUpperCase(); }
  querySelectorAll(sel){ return this._all().filter(e=>this._match(e,sel)); }
  querySelector(sel){ return this.querySelectorAll(sel)[0]||null; }
  get scrollHeight(){ return 100; }
}
const DOC_ROOT = new El('body');
const BY_ID = {};
function $(id){ if(!BY_ID[id]){ const e=new El('div'); e.id=id; DOC_ROOT.appendChild(e); BY_ID[id]=e; } return BY_ID[id]; }
globalThis.$=$;
// the deck: its chat mode, and the attributes the page sets on it (aria-busy)
const DECK={dataset:{chat:''},attrs:{},setAttribute(k,v){this.attrs[k]=String(v);},
  removeAttribute(k){delete this.attrs[k];},hasAttribute(k){return k in this.attrs;},getAttribute(k){return k in this.attrs?this.attrs[k]:null;}};
globalThis.document={ createElement:(t)=>new El(t), body:DOC_ROOT,
  querySelector:(s)=>s==='.deck'?DECK:DOC_ROOT.querySelector(s),
  getElementById:(id)=>DOC_ROOT._all().find(e=>e.id===id)||null };
globalThis.window=globalThis;
globalThis.performance={now:()=>Date.now()};
globalThis.localStorage={_s:{},getItem(k){return this._s[k]||null},setItem(k,v){this._s[k]=String(v)}};
const TOASTS=[]; globalThis.toast=(m)=>{TOASTS.push(String(m));};
const STATES=[]; globalThis.setAgentState=(s)=>{STATES.push(s);};
// esc() is the REAL one, cut out of index.html's inline script (E138)
globalThis.esc=require(__INDEX_HELPERS__).indexFunction("esc");
globalThis.renderMd=(s)=>esc(s);
globalThis.deepForward=()=>{}; globalThis.pollDeep=()=>{}; globalThis.renderAIInfo=()=>{};
globalThis.setTimeout=(fn)=>{ setImmediate(fn); return 0; };
globalThis.animate=()=>{};
"""


def anim_null(src=None):
    """index.html's no-runtime motion API, verbatim: the `const ANIM_NULL=…`
    block through `const HA=()=>window.HermesAnim||ANIM_NULL;`
    (docs/plans/motion-system-2026-10.md §6.7). Extracted functions call the
    motion runtime through HA(); with no aux_anim.js here HA() is ANIM_NULL,
    which lands every call at its end state at once (exit's done and flip's
    mutate run before returning; hold() is a setTimeout, the immediate one
    above). Empty when index.html does not carry it, which then fails loudly
    (HA is not defined) instead of passing on a stand-in."""
    src = index_src() if src is None else src
    a = src.find("\nconst ANIM_NULL=")
    if a < 0:
        return ""
    tail = "\nconst HA=()=>window.HermesAnim||ANIM_NULL;"
    b = src.find(tail, a)
    if b < 0:
        return ""
    return src[a + 1:b + len(tail)] + "\n"


# the page's own ANIM_NULL/HA() ride along with the fake DOM
FAKE_DOM = FAKE_DOM.replace("__INDEX_HELPERS__", json.dumps(os.path.join(
    REPO, "tools", "tests", "lib", "index_helpers.js")))
FAKE_DOM = FAKE_DOM + anim_null()


def run_node(js, timeout=60):
    """Run `js` in node; return (rc, parsed JSON from the last stdout line or
    None, full output)."""
    d = tempfile.mkdtemp(prefix="hermes-chatui-")
    p = os.path.join(d, "t.js")
    with open(p, "w", encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run(["node", p], capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    last = [l for l in (r.stdout or "").splitlines() if l.strip()]
    data = None
    if last:
        try:
            data = json.loads(last[-1])
        except ValueError:
            data = None
    return r.returncode, data, out
