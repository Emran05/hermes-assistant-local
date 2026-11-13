// _k14b_dom.js — a small, dependency-free DOM stand-in for the node unit tier.
//
// Not a suite (the runner skips files that start with "_"). The k14b tests
// eval REAL dashboard code (aux_desktop.js, aux_agent.js, aux_clip.js,
// aux_metrics.js, expand.js, functions extracted from index.html) against it,
// so it has to understand the markup those modules build with innerHTML:
// a forgiving HTML parser, a selector engine covering what the code queries
// (#id, .class, tag, [attr], [attr="v"], descendant and child combinators,
// comma lists), events, and the handful of element properties they touch.
"use strict";

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "source", "track", "wbr"]);
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
  middot: "·", rsaquo: "›", lsaquo: "‹", hellip: "…",
  mdash: "—", ndash: "–", times: "×" };
function decode(s) {
  return String(s).replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi, function (m, e) {
    if (e[0] === "#") {
      const n = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return isFinite(n) ? String.fromCodePoint(n) : m;
    }
    return Object.prototype.hasOwnProperty.call(ENT, e.toLowerCase()) ? ENT[e.toLowerCase()] : m;
  });
}
function escText(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function escAttr(s) { return escText(s).replace(/"/g, "&quot;"); }

class Node {
  constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; }
  get isConnected() {
    let n = this;
    while (n) { if (n === this.ownerDocument) return true; n = n.parentNode; }
    return false;
  }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const k = this.parentNode.childNodes; return k[k.indexOf(this) + 1] || null;
  }
  get children() { return this.childNodes.filter(function (c) { return c.nodeType === 1; }); }
  get firstElementChild() { return this.children[0] || null; }
  _detach(c) {
    if (c.parentNode) {
      const k = c.parentNode.childNodes, i = k.indexOf(c);
      if (i >= 0) k.splice(i, 1);
      if (c.ownerDocument && c.ownerDocument.activeElement &&
          (c === c.ownerDocument.activeElement || c.contains(c.ownerDocument.activeElement)))
        c.ownerDocument.activeElement = c.ownerDocument.body;   // removal blurs
    }
    c.parentNode = null;
  }
  appendChild(c) {
    if (c.nodeType === 11) { c.childNodes.slice().forEach((x) => this.appendChild(x)); return c; }
    this._detach(c); c.parentNode = this; this.childNodes.push(c); this.ownerDocument._reg(c); return c;
  }
  insertBefore(c, ref) {
    if (!ref) return this.appendChild(c);
    this._detach(c);
    const i = this.childNodes.indexOf(ref);
    c.parentNode = this;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    this.ownerDocument._reg(c); return c;
  }
  removeChild(c) { this._detach(c); return c; }
  replaceChild(n, old) { this.insertBefore(n, old); this._detach(old); return old; }
  replaceWith(n) { if (this.parentNode) this.parentNode.replaceChild(n, this); }
  remove() { if (this.parentNode) this._detach(this); }
  contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  get textContent() {
    if (this.nodeType === 3) return this.data;
    return this.childNodes.map(function (c) { return c.textContent; }).join("");
  }
  set textContent(v) {
    this.childNodes.slice().forEach((c) => this._detach(c));
    if (v !== "" && v != null) this.appendChild(this.ownerDocument.createTextNode(String(v)));
  }
}

class Text extends Node {
  constructor(doc, data) { super(doc); this.nodeType = 3; this.data = String(data); }
  get outerHTML() { return escText(this.data); }
}

class ClassList {
  constructor(el) { this.el = el; }
  _get() { return (this.el.getAttribute("class") || "").split(/\s+/).filter(Boolean); }
  _set(a) { this.el.setAttribute("class", a.join(" ")); }
  contains(c) { return this._get().indexOf(c) >= 0; }
  add() { const a = this._get(); for (const c of arguments) if (a.indexOf(c) < 0) a.push(c); this._set(a); }
  remove() { const r = Array.from(arguments); this._set(this._get().filter((c) => r.indexOf(c) < 0)); }
  toggle(c, force) {
    const on = force === undefined ? !this.contains(c) : !!force;
    if (on) this.add(c); else this.remove(c);
    return on;
  }
}

class Element extends Node {
  constructor(doc, tag) {
    super(doc); this.nodeType = 1; this.tagName = String(tag).toUpperCase();
    this.attrs = {}; this.style = {}; this.listeners = {}; this.classList = new ClassList(this);
    this._value = null; this.disabled = false;
    const self = this;
    this.dataset = new Proxy({}, {
      get(_, k) { const v = self.getAttribute("data-" + String(k).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase())); return v == null ? undefined : v; },
      set(_, k, v) { self.setAttribute("data-" + String(k).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()), v); return true; }
    });
  }
  get nodeName() { return this.tagName; }
  get id() { return this.attrs.id || ""; }
  set id(v) { this.setAttribute("id", v); }
  get className() { return this.attrs["class"] || ""; }
  set className(v) { this.setAttribute("class", v); }
  get hidden() { return "hidden" in this.attrs; }
  set hidden(v) { if (v) this.setAttribute("hidden", ""); else this.removeAttribute("hidden"); }
  get title() { return this.attrs.title || ""; }
  set title(v) { this.setAttribute("title", v); }
  get href() { return this.attrs.href || ""; }
  get src() { return this.attrs.src || ""; }
  set src(v) { this.setAttribute("src", v); }
  get value() {
    if (this._value !== null) return this._value;
    if (this.tagName === "TEXTAREA") return this.textContent;
    return this.attrs.value || "";
  }
  set value(v) { this._value = String(v); }
  setAttribute(k, v) {
    k = String(k).toLowerCase(); this.attrs[k] = String(v);
    if (k === "id") this.ownerDocument._reg(this);
    if (k === "disabled") this.disabled = true;
  }
  getAttribute(k) { k = String(k).toLowerCase(); return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return String(k).toLowerCase() in this.attrs; }
  removeAttribute(k) { delete this.attrs[String(k).toLowerCase()]; if (k === "disabled") this.disabled = false; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  removeEventListener(t, fn) { const l = this.listeners[t] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    let n = this;
    while (n && !ev._stop) {
      ev.currentTarget = n;
      if (n["on" + ev.type]) n["on" + ev.type].call(n, ev);
      (n.listeners && n.listeners[ev.type] || []).slice().forEach((fn) => fn.call(n, ev));
      if (!ev.bubbles) break;
      n = n.parentNode;
    }
    return !ev.defaultPrevented;
  }
  click() { return this.dispatchEvent(makeEvent("click", { bubbles: true })); }
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
  matches(sel) {
    if (sel === ":focus-visible") return this.ownerDocument.activeElement === this && !!this.ownerDocument._focusVisible;
    return matchesSelector(this, sel);
  }
  closest(sel) { let n = this; while (n && n.nodeType === 1) { if (n.matches(sel)) return n; n = n.parentNode; } return null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => { n.childNodes.forEach((c) => { if (c.nodeType === 1) { if (matchesSelector(c, sel, this)) out.push(c); walk(c); } }); };
    walk(this); return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByTagName(t) { return this.querySelectorAll(t); }
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  get offsetWidth() { return 0; }
  get outerHTML() {
    const a = Object.keys(this.attrs).map((k) => " " + k + (this.attrs[k] === "" && k === "hidden" ? "" : '="' + escAttr(this.attrs[k]) + '"')).join("");
    const t = this.tagName.toLowerCase();
    return "<" + t + a + ">" + (VOID.has(t) ? "" : this.innerHTML + "</" + t + ">");
  }
  get innerHTML() { return this.childNodes.map((c) => c.outerHTML).join(""); }
  set innerHTML(html) {
    this.childNodes.slice().forEach((c) => this._detach(c));
    parseInto(this, String(html == null ? "" : html));
  }
  insertAdjacentHTML(pos, html) {
    const tmp = this.ownerDocument.createElement("div"); tmp.innerHTML = html;
    const kids = tmp.childNodes.slice();
    if (pos === "beforeend") kids.forEach((k) => this.appendChild(k));
    else if (pos === "afterbegin") { const f = this.firstChild; kids.forEach((k) => this.insertBefore(k, f)); }
    else if (pos === "afterend" && this.parentNode) { const nx = this.nextSibling; kids.forEach((k) => this.parentNode.insertBefore(k, nx)); }
    else if (pos === "beforebegin" && this.parentNode) kids.forEach((k) => this.parentNode.insertBefore(k, this));
  }
}

function parseInto(root, html) {
  const doc = root.ownerDocument;
  const stack = [root];
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w:-]*)\s*>|<([a-zA-Z][\w:-]*)((?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)|(<)/g;
  let m;
  while ((m = re.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[0].startsWith("<!--")) continue;
    if (m[1]) {
      const t = m[1].toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) if (stack[i].tagName === t) { stack.length = i; break; }
      continue;
    }
    if (m[2]) {
      const el = doc.createElement(m[2]);
      const ar = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      let a;
      while ((a = ar.exec(m[3] || ""))) {
        const v = a[2] != null ? a[2] : a[3] != null ? a[3] : a[4] != null ? a[4] : "";
        el.setAttribute(a[1], decode(v));
      }
      top.appendChild(el);
      if (!VOID.has(m[2].toLowerCase()) && !m[4]) stack.push(el);
      continue;
    }
    const txt = m[5] != null ? m[5] : m[6];
    if (txt) top.appendChild(doc.createTextNode(decode(txt)));
  }
}

// ---- selector engine --------------------------------------------------------
function parseCompound(s) {
  const c = { tag: null, id: null, cls: [], attrs: [], not: [] };
  const re = /^([a-zA-Z*][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\]|:not\(([^)]*)\)|:([\w-]+)/g;
  let m, last = 0;
  while ((m = re.exec(s)) && m[0]) {
    if (m.index !== last) break;
    last = re.lastIndex;
    if (m[1]) c.tag = m[1] === "*" ? null : m[1].toUpperCase();
    else if (m[2]) c.id = m[2];
    else if (m[3]) c.cls.push(m[3]);
    else if (m[4]) c.attrs.push({ k: m[4].toLowerCase(), op: m[5] || null, v: m[6] != null ? m[6] : m[7] != null ? m[7] : m[8] });
    else if (m[9] != null) c.not.push(m[9]);
    else if (m[10]) c.pseudo = m[10];
  }
  return c;
}
function matchCompound(el, c) {
  if (c.tag && el.tagName !== c.tag) return false;
  if (c.id && el.id !== c.id) return false;
  for (const k of c.cls) if (!el.classList.contains(k)) return false;
  for (const a of c.attrs) {
    const v = el.getAttribute(a.k);
    if (v == null) return false;
    if (a.op === "=" && v !== a.v) return false;
    if (a.op === "^=" && !v.startsWith(a.v)) return false;
    if (a.op === "*=" && v.indexOf(a.v) < 0) return false;
    if (a.op === "~=" && v.split(/\s+/).indexOf(a.v) < 0) return false;
  }
  for (const n of c.not) if (matchesSelector(el, n)) return false;
  if (c.pseudo === "first-child" && el.parentNode && el.parentNode.children[0] !== el) return false;
  return true;
}
function splitTop(s, ch) {
  const out = []; let depth = 0, cur = "", q = null;
  for (const x of s) {
    if (q) { if (x === q) q = null; cur += x; continue; }
    if (x === '"' || x === "'") { q = x; cur += x; continue; }
    if (x === "[" || x === "(") depth++;
    if (x === "]" || x === ")") depth--;
    if (x === ch && depth === 0) { out.push(cur); cur = ""; continue; }
    cur += x;
  }
  out.push(cur); return out;
}
function matchesSelector(el, sel, scope) {
  return splitTop(sel, ",").some(function (one) {
    const toks = tokenize(one.trim());
    return matchChain(el, toks, toks.length - 1, scope);
  });
}
function tokenize(s) {
  const out = []; let cur = "", depth = 0, q = null;
  const push = () => { if (cur) out.push(cur); cur = ""; };
  for (const x of s) {
    if (q) { cur += x; if (x === q) q = null; continue; }
    if (x === '"' || x === "'") { q = x; cur += x; continue; }
    if (x === "[" || x === "(") depth++;
    if (x === "]" || x === ")") depth--;
    if (depth === 0 && /\s/.test(x)) { push(); continue; }
    if (depth === 0 && x === ">") { push(); out.push(">"); continue; }
    cur += x;
  }
  push(); return out;
}
function matchChain(el, toks, i, scope) {
  if (!matchCompound(el, parseCompound(toks[i]))) return false;
  if (i === 0) return true;
  if (toks[i - 1] === ">") {
    const p = el.parentNode;
    return !!(p && p.nodeType === 1 && matchChain(p, toks, i - 2, scope));
  }
  let p = el.parentNode;
  while (p && p.nodeType === 1) {
    if (matchChain(p, toks, i - 1, scope)) return true;
    p = p.parentNode;
  }
  return false;
}

function makeEvent(type, opts) {
  opts = opts || {};
  return Object.assign({
    type: type, bubbles: !!opts.bubbles, defaultPrevented: false, _stop: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this._stop = true; }
  }, opts);
}

class Document extends Element {
  constructor() {
    super(null, "#document");
    this.ownerDocument = this; this.nodeType = 9; this._ids = {};
    this.hidden = false; this.visibilityState = "visible"; this.readyState = "complete";
    this.documentElement = this.createElement("html"); this.appendChild(this.documentElement);
    this.head = this.createElement("head"); this.body = this.createElement("body");
    this.documentElement.appendChild(this.head); this.documentElement.appendChild(this.body);
    this.activeElement = this.body; this._focusVisible = false;
  }
  get hidden() { return !!this._hidden; }
  set hidden(v) { this._hidden = !!v; }
  get isConnected() { return true; }
  _reg() {}
  createElement(t) { return new Element(this, t); }
  createElementNS(_ns, t) { return new Element(this, t); }
  createTextNode(s) { return new Text(this, s); }
  createDocumentFragment() { const f = new Element(this, "#fragment"); f.nodeType = 11; return f; }
  getElementById(id) {
    const walk = (n) => { for (const c of n.childNodes) { if (c.nodeType === 1) { if (c.id === id) return c; const r = walk(c); if (r) return r; } } return null; };
    return walk(this);
  }
}

function makeEnv(opts) {
  opts = opts || {};
  const doc = new Document();
  const timers = [];
  let tid = 0;
  const env = {
    document: doc,
    timers: timers,
    setInterval(fn, ms) { const t = { id: ++tid, fn: fn, ms: ms, every: true, live: true }; timers.push(t); return t.id; },
    setTimeout(fn, ms) { const t = { id: ++tid, fn: fn, ms: ms || 0, every: false, live: true }; timers.push(t); return t.id; },
    clearInterval(id) { timers.forEach((t) => { if (t.id === id) t.live = false; }); },
    clearTimeout(id) { timers.forEach((t) => { if (t.id === id) t.live = false; }); },
    // run every live interval once (and any due one-shot timers)
    tick() {
      timers.slice().forEach((t) => {
        if (!t.live) return;
        if (!t.every) t.live = false;
        try { t.fn(); } catch (e) { env.errors.push(e); }
      });
    },
    liveIntervals() { return timers.filter((t) => t.live && t.every); },
    errors: [],
    makeEvent: makeEvent
  };
  return env;
}

// Install env globals for a module evaluated with indirect eval / new Function.
function install(env, extra) {
  const g = global;
  g.window = g; g.self = g;
  g.document = env.document;
  g.setInterval = env.setInterval; g.setTimeout = env.setTimeout;
  g.clearInterval = env.clearInterval; g.clearTimeout = env.clearTimeout;
  g.requestAnimationFrame = function (fn) { fn(); return 1; };
  g.matchMedia = function () { return { matches: false, addEventListener() {}, addListener() {} }; };
  g.getComputedStyle = function () { return { getPropertyValue() { return "x"; }, display: "block" }; };
  g.localStorage = (function () { const s = {}; return { getItem: (k) => (k in s ? s[k] : null), setItem: (k, v) => { s[k] = String(v); }, removeItem: (k) => { delete s[k]; } }; })();
  g.addEventListener = function () {}; g.removeEventListener = function () {};
  Object.keys(extra || {}).forEach(function (k) {
    Object.defineProperty(g, k, { value: extra[k], writable: true, configurable: true, enumerable: true });
  });
  return g;
}

// Pull one top-level `function name(...){...}` (or `async function`) out of a
// script body by brace matching that understands strings, regex-free.
function extractFunction(src, name) {
  const re = new RegExp("(?:^|\\n)((?:async\\s+)?function\\s+" + name + "\\s*\\()");
  const m = re.exec(src);
  if (!m) throw new Error("function " + name + " not found");
  let i = m.index + m[0].indexOf(m[1]);
  const start = i;
  i = src.indexOf("{", i);
  let depth = 0, q = null, tmpl = 0;
  for (; i < src.length; i++) {
    const c = src[i], p = src[i - 1];
    if (q) {
      if (c === "\\") { i++; continue; }
      if (c === q) q = null;
      continue;
    }
    if (c === "/" && src[i + 1] === "/" && p !== "\\") { i = src.indexOf("\n", i); continue; }
    if (c === "/" && src[i + 1] === "*" && p !== "\\") { i = src.indexOf("*/", i) + 1; continue; }
    if (c === "/") {
      // a regex literal where an operand is expected (after an operator or an
      // opening bracket): skip to its closing slash, honouring [...] classes
      let k = i - 1;
      while (k >= 0 && /\s/.test(src[k])) k--;
      if (k < 0 || "(,=:[!&|?{};+-*%<>~^".indexOf(src[k]) >= 0) {
        let cls = false;
        for (i++; i < src.length; i++) {
          const r = src[i];
          if (r === "\\") { i++; continue; }
          if (r === "[") cls = true;
          else if (r === "]") cls = false;
          else if (r === "/" && !cls) break;
          else if (r === "\n") break;
        }
        continue;
      }
    }
    if (c === '"' || c === "'" || c === "`") { q = c; continue; }
    if (c === "{") depth++;
    if (c === "}") { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error("unbalanced " + name);
}

module.exports = { makeEnv, install, extractFunction, makeEvent, decode };
