// A minimal fake DOM for running real index.html / expand.js renderers in
// plain node (no jsdom — the repo is stdlib-only). Not a suite: the runner
// skips files that start with "_".
//
// innerHTML is parsed shallowly: every start tag becomes a descendant element
// (id, class, hidden, aria-label are read), which is all querySelector('#id'),
// querySelector('.class') and querySelector('tag') need here. Elements made
// with createElement/appendChild nest properly.
"use strict";

function El(tag, attrs) {
  this.tagName = String(tag || "div").toUpperCase();
  attrs = attrs || {};
  this.id = attrs.id || "";
  this.className = attrs["class"] || "";
  this.hidden = !!attrs.hidden;
  this.attrs = attrs;
  this.style = {};
  this.dataset = {};
  this.value = "";
  this.type = "";
  this.onclick = null;
  this._kids = [];
  this._parsed = [];
  this._text = "";
  this._html = "";
  this._ls = {};
  this.parent = null;
  const self = this;
  this.classList = {
    add(c) { if (!self.classList.contains(c)) self.className = (self.className + " " + c).trim(); },
    remove(c) { self.className = self.className.split(/\s+/).filter(x => x && x !== c).join(" "); },
    contains(c) { return self.className.split(/\s+/).indexOf(c) >= 0; },
    toggle(c, on) { if (on === undefined) on = !self.classList.contains(c); on ? self.classList.add(c) : self.classList.remove(c); },
  };
}
Object.defineProperty(El.prototype, "textContent", {
  get() { return this._text + this._kids.map(k => k.textContent).join(""); },
  set(v) { this._text = String(v == null ? "" : v); this._kids = []; this._parsed = []; this._html = ""; },
});
Object.defineProperty(El.prototype, "innerHTML", {
  get() { return this._html; },
  set(v) {
    this._html = String(v == null ? "" : v);
    this._kids = [];
    this._text = this._html.replace(/<[^>]*>/g, "");
    this._parsed = [];
    const re = /<([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>/g;
    let m;
    while ((m = re.exec(this._html))) {
      const attrs = {};
      const ar = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
      let a;
      while ((a = ar.exec(m[2]))) attrs[a[1]] = a[2] != null ? a[2] : a[3] != null ? a[3] : a[4] != null ? a[4] : true;
      const el = new El(m[1], attrs);
      el.parent = this;
      this._parsed.push(el);
    }
  },
});
El.prototype.addEventListener = function (t, f) { (this._ls[t] = this._ls[t] || []).push(f); };
El.prototype.fire = function (t, e) { (this._ls[t] || []).slice().forEach(f => f(e || { target: this, preventDefault() {} })); };
El.prototype.appendChild = function (c) { if (c.parent) c.remove(); this._kids.push(c); c.parent = this; return c; };
El.prototype.append = function () { [].slice.call(arguments).forEach(c => this.appendChild(c)); };
El.prototype.prepend = function (c) { if (c.parent) c.remove(); this._kids.unshift(c); c.parent = this; return c; };
El.prototype.remove = function () {
  const p = this.parent;
  if (!p) return;
  p._kids = p._kids.filter(x => x !== this);
  p._parsed = p._parsed.filter(x => x !== this);
  this.parent = null;
};
El.prototype.insertAdjacentHTML = function () {};
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; if (k === "id") this.id = v; };
El.prototype.getAttribute = function (k) { return this.attrs[k]; };
El.prototype.focus = function () {};
El.prototype.contains = function (x) { return this.all().indexOf(x) >= 0 || x === this; };
El.prototype.all = function () {
  const out = [];
  const walk = el => { el._parsed.concat(el._kids).forEach(k => { out.push(k); walk(k); }); };
  walk(this);
  return out;
};
function matches(el, sel) {
  sel = sel.trim();
  if (sel[0] === "#") return el.id === sel.slice(1);
  if (sel[0] === ".") return sel.slice(1).split(".").every(c => el.classList.contains(c));
  return el.tagName === sel.toUpperCase();
}
El.prototype.querySelectorAll = function (sel) { return this.all().filter(el => matches(el, sel)); };
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
El.prototype.click = function () { if (this.onclick) return this.onclick({ preventDefault() {}, stopPropagation() {} }); };

// ---- window / document / fetch / timers -----------------------------------
function install(G) {
  const winLs = {};
  G.window = G;
  G.addEventListener = (t, f) => { (winLs[t] = winLs[t] || []).push(f); };
  G.dispatchEvent = ev => { (winLs[ev.type] || []).slice().forEach(f => f(ev)); return true; };
  G.CustomEvent = function (type, init) { this.type = type; this.detail = (init || {}).detail; };
  G.document = {
    createElement: t => new El(t),
    querySelectorAll: () => [],
    activeElement: null,
  };
  // timers run only when the test says so
  G.__timers = [];
  G.setTimeout = (fn, ms) => { G.__timers.push(fn); return G.__timers.length; };
  G.clearTimeout = id => { if (id) G.__timers[id - 1] = null; };
  G.runTimers = () => { const t = G.__timers; G.__timers = []; t.forEach(f => f && f()); };
  // fetch: tests push handlers; every call is recorded
  G.__fetches = [];
  G.__respond = () => ({ status: 200, body: {} });
  G.fetch = (url, opts) => {
    const call = { url: String(url), method: (opts && opts.method) || "GET",
                   body: opts && opts.body ? JSON.parse(opts.body) : null };
    G.__fetches.push(call);
    let r;
    try { r = G.__respond(call); } catch (e) { return Promise.reject(e); }
    if (r && r.reject) return Promise.reject(new Error("network down"));
    return Promise.resolve({ ok: r.status >= 200 && r.status < 300, status: r.status,
                             json: () => Promise.resolve(r.body) });
  };
  G.flush = () => new Promise(res => setImmediate(res)).then(() => new Promise(res => setImmediate(res)))
    .then(() => new Promise(res => setImmediate(res)));
}

// pull one `name(args){...}` / `function name(){...}` out of a source string by
// brace matching (strings/regex literals in these bodies never hold braces that
// unbalance them; the extraction is checked by node parsing it)
function extract(src, startRe) {
  const m = startRe.exec(src);
  if (!m) throw new Error("not found: " + startRe);
  let i = src.indexOf("{", m.index + m[0].length - 1);
  let depth = 0, q = null;
  for (let j = i; j < src.length; j++) {
    const c = src[j];
    if (q) { if (c === "\\") { j++; continue; } if (c === q) q = null; continue; }
    if (c === "/" && src[j + 1] === "/") { j = src.indexOf("\n", j); if (j < 0) break; continue; }
    if (c === "/" && src[j + 1] === "*") { j = src.indexOf("*/", j + 2) + 1; continue; }
    if (c === "'" || c === '"' || c === "`") { q = c; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return src.slice(m.index, j + 1); }
  }
  throw new Error("unbalanced: " + startRe);
}

module.exports = { El, install, extract };
