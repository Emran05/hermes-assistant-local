// _a11y_dom.js — a small, honest DOM for the keyboard/focus suites (k14a).
//
// Not a suite (run.sh skips files that start with "_"). The keyboard and
// focus bugs these suites pin down live in the interaction between real
// handlers and real tree state — which element holds focus after a menu is
// rebuilt, whether Esc reaches a listener, what an ARIA attribute says after
// a toggle — so a hand-rolled stub per test would be a re-implementation.
// This file gives the real dashboard code (index.html's inline script, the
// aux_*.js modules) a document to run against instead:
//
//   * an HTML parser good enough for index.html and every innerHTML string
//     the dashboard builds (raw-text <script>/<style>, void and self-closed
//     SVG elements, quoted/unquoted attributes, the entities the UI uses);
//   * a selector engine (tag, #id, .class, [attr], [attr=v], [attr^=v],
//     [attr*=v], :not(), :first-child, :last-child, :focus, :focus-within,
//     descendant/child combinators, selector lists) that THROWS on anything
//     it does not understand, so an unsupported query fails loudly instead of
//     silently matching nothing;
//   * events with capture/target/bubble phases, on<type> properties, once,
//     stopPropagation/stopImmediatePropagation, preventDefault;
//   * focus that behaves like a browser's: focusable = native control or
//     [tabindex], not disabled, rendered; focusout/blur/focusin/focus with
//     relatedTarget; activeElement falls back to <body> once the focused
//     element is removed or hidden ("focus fixup", no events);
//   * MutationObserver (attributes + childList, subtree, attributeFilter),
//     delivered as a microtask the way browsers do;
//   * fake timers (setTimeout/setInterval queue the test drains) and a fetch
//     the test answers from a route table. Nothing touches the network.
//
// Layout does not exist here. Elements report whatever box a test assigns
// with el.__rect = {left, top, width, height}; everything else is 0.
"use strict";
const vm = require("vm");

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "source", "track", "wbr"]);
const RAW = new Set(["script", "style", "textarea", "title"]);
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
  hellip: "…", mdash: "—", ndash: "–", middot: "·", times: "×",
  rarr: "→", larr: "←", uarr: "↑", darr: "↓", copy: "©",
  rsquo: "’", lsquo: "‘", ldquo: "“", rdquo: "”", bull: "•",
  deg: "°", minus: "−", check: "✓", thinsp: " ", ensp: " " };
function decode(s) {
  return String(s).replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);/g, function (m, n) {
    if (n[0] === "#") {
      const cp = n[1] === "x" || n[1] === "X" ? parseInt(n.slice(2), 16) : parseInt(n.slice(1), 10);
      try { return String.fromCodePoint(cp); } catch (e) { return m; }
    }
    return Object.prototype.hasOwnProperty.call(ENT, n) ? ENT[n] : m;
  });
}
function escText(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function escAttr(s) { return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;"); }
function kebab(p) { return p.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); }); }
function camel(p) { return p.replace(/-([a-z])/g, function (m, c) { return c.toUpperCase(); }); }

// --------------------------------------------------------------------------
// selector engine
// --------------------------------------------------------------------------
function splitTop(s, sep) {
  const out = []; let depth = 0, q = null, cur = "";
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) { cur += c; if (c === q) q = null; continue; }
    if (c === '"' || c === "'") { q = c; cur += c; continue; }
    if (c === "(" || c === "[") depth++;
    if (c === ")" || c === "]") depth--;
    if (c === sep && depth === 0) { out.push(cur); cur = ""; continue; }
    cur += c;
  }
  out.push(cur);
  return out.map(function (x) { return x.trim(); }).filter(function (x) { return x.length; });
}
const selCache = new Map();
function parseSelectorList(sel) {
  if (selCache.has(sel)) return selCache.get(sel);
  const list = splitTop(sel, ",").map(parseComplex);
  selCache.set(sel, list);
  return list;
}
function parseComplex(s) {
  // tokens: compound, combinator, compound, ...
  const parts = []; let i = 0, cur = "", depth = 0, q = null;
  function flush() { if (cur.trim()) parts.push({ compound: parseCompound(cur.trim()) }); cur = ""; }
  while (i < s.length) {
    const c = s[i];
    if (q) { cur += c; if (c === q) q = null; i++; continue; }
    if (c === '"' || c === "'") { q = c; cur += c; i++; continue; }
    if (c === "(" || c === "[") depth++;
    if (c === ")" || c === "]") depth--;
    if (depth === 0 && (c === ">" || c === "+" || c === "~" || /\s/.test(c))) {
      flush();
      let j = i; while (j < s.length && /\s/.test(s[j])) j++;
      let comb = " ";
      if (s[j] === ">" || s[j] === "+" || s[j] === "~") { comb = s[j]; j++; while (j < s.length && /\s/.test(s[j])) j++; }
      if (parts.length) parts.push({ comb: comb });
      i = j; continue;
    }
    cur += c; i++;
  }
  flush();
  return parts;
}
function parseCompound(s) {
  const c = { tag: null, ids: [], classes: [], attrs: [], pseudos: [] };
  let i = 0;
  const ident = function () {
    let j = i; while (j < s.length && /[A-Za-z0-9_\- -￿\\]/.test(s[j])) j++;
    const v = s.slice(i, j); i = j; return v;
  };
  if (s[i] === "*") { i++; }
  else if (/[A-Za-z]/.test(s[i])) c.tag = ident().toLowerCase();
  while (i < s.length) {
    const ch = s[i];
    if (ch === "#") { i++; c.ids.push(ident()); }
    else if (ch === ".") { i++; c.classes.push(ident()); }
    else if (ch === "[") {
      let j = i + 1, q = null;
      while (j < s.length) { const d = s[j]; if (q) { if (d === q) q = null; } else if (d === '"' || d === "'") q = d; else if (d === "]") break; j++; }
      const body = s.slice(i + 1, j).trim(); i = j + 1;
      const m = body.match(/^([A-Za-z_:][-A-Za-z0-9_:.]*)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]+)))?\s*(i)?$/);
      if (!m) throw new Error("mini-dom: unsupported attribute selector [" + body + "]");
      c.attrs.push({ name: m[1].toLowerCase(), op: m[2] || null,
        val: m[3] != null ? m[3] : (m[4] != null ? m[4] : m[5]) });
    } else if (ch === ":") {
      i++; if (s[i] === ":") throw new Error("mini-dom: pseudo-elements are not supported in " + s);
      const name = ident().toLowerCase(); let arg = null;
      if (s[i] === "(") {
        let depth = 1, j = i + 1;
        while (j < s.length && depth) { if (s[j] === "(") depth++; else if (s[j] === ")") depth--; j++; }
        arg = s.slice(i + 1, j - 1); i = j;
      }
      const known = ["not", "first-child", "last-child", "focus", "focus-visible", "focus-within",
        "hover", "active", "checked", "disabled", "scope", "is", "where", "root", "has"];
      if (known.indexOf(name) < 0) throw new Error("mini-dom: unsupported pseudo-class :" + name);
      c.pseudos.push({ name: name, arg: arg, list: (arg != null && name !== "scope") ? parseSelectorList(arg) : null });
      if (name === "has" && arg != null && /^\s*[>+~]/.test(arg)) throw new Error("mini-dom: relative :has(" + arg + ") is not supported");
    } else throw new Error("mini-dom: cannot parse selector '" + s + "' at " + i);
  }
  return c;
}
function attrMatch(el, a) {
  const v = el.getAttribute(a.name);
  if (v == null) return false;
  if (!a.op) return true;
  const want = a.val;
  switch (a.op) {
    case "=": return v === want;
    case "^=": return want !== "" && v.indexOf(want) === 0;
    case "$=": return want !== "" && v.slice(-want.length) === want;
    case "*=": return want !== "" && v.indexOf(want) >= 0;
    case "~=": return v.split(/\s+/).indexOf(want) >= 0;
    case "|=": return v === want || v.indexOf(want + "-") === 0;
  }
  return false;
}
function compoundMatch(el, c, scope) {
  if (!el || el.nodeType !== 1) return false;
  if (c.tag && el.localName !== c.tag) return false;
  for (const id of c.ids) if (el.getAttribute("id") !== id) return false;
  if (c.classes.length) {
    const cls = (el.getAttribute("class") || "").split(/\s+/);
    for (const k of c.classes) if (cls.indexOf(k) < 0) return false;
  }
  for (const a of c.attrs) if (!attrMatch(el, a)) return false;
  for (const p of c.pseudos) {
    const doc = el.ownerDocument;
    switch (p.name) {
      case "not": if (matchesList(el, p.list, scope)) return false; break;
      case "is": case "where": if (!matchesList(el, p.list, scope)) return false; break;
      case "first-child": if (!el.parentNode || el.parentNode.firstElementChild !== el) return false; break;
      case "last-child": if (!el.parentNode || el.parentNode.lastElementChild !== el) return false; break;
      case "focus": case "focus-visible": if (doc.activeElement !== el) return false; break;
      case "focus-within": if (!el.contains(doc.activeElement)) return false; break;
      // pointer state is whatever the test says it is (doc.__hover / doc.__press)
      case "hover": if (!doc.__hover || !el.contains(doc.__hover)) return false; break;
      case "active": if (!doc.__press || !el.contains(doc.__press)) return false; break;
      case "root": if (el !== doc.documentElement) return false; break;
      case "has": if (!el.querySelector(p.arg)) return false; break;
      case "checked": if (!el.checked) return false; break;
      case "disabled": if (!el.hasAttribute("disabled")) return false; break;
      case "scope": if (el !== scope) return false; break;
    }
  }
  return true;
}
function complexMatch(el, parts, idx, scope) {
  if (!compoundMatch(el, parts[idx].compound, scope)) return false;
  if (idx === 0) return true;
  const comb = parts[idx - 1].comb;
  if (comb === ">") return complexMatch(el.parentElement, parts, idx - 2, scope);
  if (comb === " ") {
    let p = el.parentElement;
    while (p) { if (complexMatch(p, parts, idx - 2, scope)) return true; p = p.parentElement; }
    return false;
  }
  if (comb === "+") { const s = el.previousElementSibling; return !!s && complexMatch(s, parts, idx - 2, scope); }
  if (comb === "~") {
    let s = el.previousElementSibling;
    while (s) { if (complexMatch(s, parts, idx - 2, scope)) return true; s = s.previousElementSibling; }
    return false;
  }
  return false;
}
function matchesList(el, list, scope) {
  for (const parts of list) if (complexMatch(el, parts, parts.length - 1, scope)) return true;
  return false;
}

// --------------------------------------------------------------------------
// nodes
// --------------------------------------------------------------------------
class Node {
  constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; this.__l = null; }
  get parentElement() { const p = this.parentNode; return p && p.nodeType === 1 ? p : null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const p = this.parentNode; if (!p) return null; const i = p.childNodes.indexOf(this); return p.childNodes[i + 1] || null; }
  get previousSibling() { const p = this.parentNode; if (!p) return null; const i = p.childNodes.indexOf(this); return i > 0 ? p.childNodes[i - 1] : null; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === this.ownerDocument || n.nodeType === 9; }
  contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  _detach(n) {
    if (n.parentNode) {
      const old = n.parentNode, i = old.childNodes.indexOf(n);
      if (i >= 0) old.childNodes.splice(i, 1);
      n.parentNode = null;
      old._mutated("childList", null, null, [], [n]);
    }
  }
  appendChild(n) { return this.insertBefore(n, null); }
  insertBefore(n, ref) {
    if (n.nodeType === 11) { const kids = n.childNodes.slice(); for (const k of kids) this.insertBefore(k, ref); return n; }
    this._detach(n);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i >= 0) this.childNodes.splice(i, 0, n); else this.childNodes.push(n);
    n.parentNode = this;
    this._mutated("childList", null, null, [n], []);
    return n;
  }
  removeChild(n) { this._detach(n); return n; }
  replaceChild(n, old) { this.insertBefore(n, old); this._detach(old); return old; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  append() { for (const a of arguments) this.appendChild(typeof a === "string" ? this.ownerDocument.createTextNode(a) : a); }
  prepend() { const f = this.firstChild; for (const a of arguments) this.insertBefore(typeof a === "string" ? this.ownerDocument.createTextNode(a) : a, f); }
  get textContent() {
    if (this.nodeType === 3) return this.data;
    let s = ""; for (const c of this.childNodes) if (c.nodeType === 1 || c.nodeType === 3) s += c.textContent; return s;
  }
  set textContent(v) {
    if (this.nodeType === 3) { this.data = String(v); return; }
    const old = this.childNodes.slice();
    for (const c of old) c.parentNode = null;
    this.childNodes = [];
    if (v !== "" && v != null) { const t = this.ownerDocument.createTextNode(String(v)); t.parentNode = this; this.childNodes.push(t); }
    this._mutated("childList", null, null, this.childNodes.slice(), old);
  }
  _mutated(type, name, oldValue, added, removed) {
    const doc = this.ownerDocument || this;
    if (doc && doc.__mo) doc.__mo(this, type, name, oldValue, added, removed);
  }
  // ---- events ----
  addEventListener(type, fn, opt) {
    if (!fn) return;
    const capture = typeof opt === "boolean" ? opt : !!(opt && opt.capture);
    const once = !!(opt && typeof opt === "object" && opt.once);
    this.__l = this.__l || {};
    const arr = this.__l[type] = this.__l[type] || [];
    if (arr.some(function (l) { return l.fn === fn && l.capture === capture; })) return;
    arr.push({ fn: fn, capture: capture, once: once });
  }
  removeEventListener(type, fn, opt) {
    const capture = typeof opt === "boolean" ? opt : !!(opt && opt.capture);
    if (!this.__l || !this.__l[type]) return;
    this.__l[type] = this.__l[type].filter(function (l) { return !(l.fn === fn && l.capture === capture); });
  }
  dispatchEvent(ev) { return dispatch(this, ev); }
}
function fire(node, ev, phase) {
  ev.currentTarget = node; ev.eventPhase = phase;
  const ls = (node.__l && node.__l[ev.type]) ? node.__l[ev.type].slice() : [];
  for (const l of ls) {
    if (phase === 1 && !l.capture) continue;
    if (phase === 3 && l.capture) continue;
    if (l.once) node.removeEventListener(ev.type, l.fn, l.capture);
    try { typeof l.fn === "function" ? l.fn.call(node, ev) : l.fn.handleEvent(ev); }
    catch (e) { reportError(node, e); }
    if (ev.__stopNow) return;
  }
  if (phase !== 1) {
    const h = node["on" + ev.type];
    if (typeof h === "function") {
      let r; try { r = h.call(node, ev); } catch (e) { reportError(node, e); }
      if (r === false) ev.preventDefault();
    }
  }
}
function reportError(node, e) {
  const doc = node.ownerDocument || node.document || node;
  const w = doc && doc.defaultView ? doc.defaultView : (node.window || null);
  if (w && w.__errors) w.__errors.push(e); else throw e;
}
function dispatch(target, ev) {
  ev.target = target; ev.__stop = false; ev.__stopNow = false;
  const path = [];
  let n = target.parentNode;
  while (n) { path.unshift(n); n = n.parentNode; }
  const doc = target.ownerDocument || (target.nodeType === 9 ? target : null);
  const win = doc && doc.defaultView;
  if (win && target !== win && (path[0] === doc || target === doc)) path.unshift(win);
  for (const p of path) { fire(p, ev, 1); if (ev.__stop) return !ev.defaultPrevented; }
  fire(target, ev, 2);
  if (ev.bubbles && !ev.__stop) {
    for (let i = path.length - 1; i >= 0; i--) { fire(path[i], ev, 3); if (ev.__stop) break; }
  }
  ev.currentTarget = null; ev.eventPhase = 0;
  return !ev.defaultPrevented;
}
class Event {
  constructor(type, init) {
    init = init || {};
    this.type = type; this.bubbles = !!init.bubbles; this.cancelable = init.cancelable !== false;
    this.defaultPrevented = false; this.target = null; this.currentTarget = null; this.eventPhase = 0;
    this.isTrusted = false; this.timeStamp = Date.now();
    for (const k in init) if (!(k in this)) this[k] = init[k];
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this.__stop = true; }
  stopImmediatePropagation() { this.__stop = true; this.__stopNow = true; }
  composedPath() { const p = []; let n = this.target; while (n) { p.push(n); n = n.parentNode; } return p; }
}
class CustomEvent extends Event { constructor(t, i) { super(t, i); this.detail = i && i.detail !== undefined ? i.detail : null; } }
class UIEvent extends Event { constructor(t, i) { super(t, i); if (this.detail === undefined) this.detail = 0; } }
class KeyboardEvent extends UIEvent {
  constructor(t, i) { super(t, i); for (const k of ["key", "code"]) if (this[k] === undefined) this[k] = "";
    for (const k of ["shiftKey", "metaKey", "ctrlKey", "altKey", "repeat", "isComposing"]) if (this[k] === undefined) this[k] = false; }
}
class MouseEvent extends UIEvent {
  constructor(t, i) { super(t, i); for (const k of ["clientX", "clientY", "pageX", "pageY", "button"]) if (this[k] === undefined) this[k] = 0;
    if (this.relatedTarget === undefined) this.relatedTarget = null; }
}
class PointerEvent extends MouseEvent { constructor(t, i) { super(t, i); if (this.pointerType === undefined) this.pointerType = "mouse"; } }
class FocusEvent extends UIEvent { constructor(t, i) { super(t, i); if (this.relatedTarget === undefined) this.relatedTarget = null; } }

// Browser collections are NOT arrays (no map/filter/find): a page that calls
// them on a NodeList breaks in WebKit, so it must break here too.
class NodeList {
  constructor(arr) { for (let i = 0; i < arr.length; i++) this[i] = arr[i]; Object.defineProperty(this, "length", { value: arr.length }); }
  item(i) { return this[i] || null; }
  forEach(fn, t) { for (let i = 0; i < this.length; i++) fn.call(t, this[i], i, this); }
  entries() { return Array.prototype.slice.call(this).entries(); }
  keys() { return Array.prototype.slice.call(this).keys(); }
  values() { return Array.prototype.slice.call(this).values(); }
  [Symbol.iterator]() { return Array.prototype.slice.call(this)[Symbol.iterator](); }
}
class HTMLCollection {
  constructor(arr) { for (let i = 0; i < arr.length; i++) this[i] = arr[i]; Object.defineProperty(this, "length", { value: arr.length }); }
  item(i) { return this[i] || null; }
  [Symbol.iterator]() { return Array.prototype.slice.call(this)[Symbol.iterator](); }
}
class Text extends Node {
  constructor(doc, data) { super(doc); this.nodeType = 3; this.nodeName = "#text"; this.data = String(data); }
  get nodeValue() { return this.data; } set nodeValue(v) { this.data = String(v); }
  get wholeText() { return this.data; }
}
class Comment extends Node { constructor(doc, d) { super(doc); this.nodeType = 8; this.nodeName = "#comment"; this.data = d; } get textContent() { return ""; } }
class Fragment extends Node { constructor(doc) { super(doc); this.nodeType = 11; this.nodeName = "#document-fragment"; }
  get children() { return new HTMLCollection(this.childNodes.filter(function (c) { return c.nodeType === 1; })); }
  querySelector(s) { return Element.prototype.querySelector.call(this, s); }
  _qsa(s) { return Element.prototype._qsa.call(this, s); }
  querySelectorAll(s) { return Element.prototype.querySelectorAll.call(this, s); } }

function makeStyle(el) {
  const store = new Map();
  const api = {
    setProperty(n, v) { if (v == null || v === "") store.delete(n); else store.set(n, String(v)); },
    getPropertyValue(n) { return store.has(n) ? store.get(n) : ""; },
    removeProperty(n) { const v = store.get(n) || ""; store.delete(n); return v; },
    get cssText() { return Array.from(store.entries()).map(function (e) { return e[0] + ": " + e[1]; }).join("; ") + (store.size ? ";" : ""); },
    set cssText(s) {
      store.clear();
      String(s || "").split(";").forEach(function (d) { const i = d.indexOf(":"); if (i > 0) store.set(d.slice(0, i).trim(), d.slice(i + 1).trim()); });
    },
    get length() { return store.size; }
  };
  return new Proxy(api, {
    get(t, p) {
      if (p in t) { const v = t[p]; return typeof v === "function" ? v.bind(t) : v; }
      if (typeof p !== "string") return undefined;
      const k = kebab(p); return store.has(k) ? store.get(k) : "";
    },
    set(t, p, v) {
      if (p === "cssText") { t.cssText = v; return true; }
      const k = kebab(String(p));
      if (v == null || v === "") store.delete(k); else store.set(k, String(v));
      return true;
    }
  });
}
function makeClassList(el) {
  const get = function () { return (el.getAttribute("class") || "").split(/\s+/).filter(Boolean); };
  const put = function (a) { el.setAttribute("class", a.join(" ")); };
  return {
    add() { const a = get(); for (const c of arguments) if (a.indexOf(c) < 0) a.push(c); put(a); },
    remove() { const rm = Array.from(arguments); put(get().filter(function (c) { return rm.indexOf(c) < 0; })); },
    contains(c) { return get().indexOf(c) >= 0; },
    toggle(c, force) {
      const has = get().indexOf(c) >= 0, want = force === undefined ? !has : !!force;
      if (want && !has) this.add(c); else if (!want && has) this.remove(c);
      return want;
    },
    item(i) { return get()[i] || null; },
    get length() { return get().length; },
    forEach(fn) { get().forEach(fn); },
    toString() { return get().join(" "); }
  };
}
const NATIVE_FOCUS = new Set(["button", "input", "select", "textarea"]);
class Element extends Node {
  constructor(doc, tag, ns) {
    super(doc);
    this.nodeType = 1;
    this.localName = String(tag).toLowerCase();
    this.namespaceURI = ns || null;
    this.__a = new Map();
    this.style = makeStyle(this);
    this.classList = makeClassList(this);
    this.scrollTop = 0; this.scrollLeft = 0;
    this.__value = null;
  }
  get tagName() { return this.localName.toUpperCase(); }
  get nodeName() { return this.tagName; }
  get __kids() { return this.childNodes.filter(function (c) { return c.nodeType === 1; }); }
  get children() { return new HTMLCollection(this.__kids); }
  get childElementCount() { return this.__kids.length; }
  get firstElementChild() { return this.__kids[0] || null; }
  get lastElementChild() { const c = this.__kids; return c[c.length - 1] || null; }
  get nextElementSibling() { let n = this.nextSibling; while (n && n.nodeType !== 1) n = n.nextSibling; return n; }
  get previousElementSibling() { let n = this.previousSibling; while (n && n.nodeType !== 1) n = n.previousSibling; return n; }
  // ---- attributes ----
  getAttribute(n) { n = String(n).toLowerCase(); return this.__a.has(n) ? this.__a.get(n) : null; }
  setAttribute(n, v) {
    n = String(n).toLowerCase(); const old = this.getAttribute(n);
    this.__a.set(n, String(v));
    this._mutated("attributes", n, old, [], []);
  }
  removeAttribute(n) {
    n = String(n).toLowerCase(); if (!this.__a.has(n)) return;
    const old = this.__a.get(n); this.__a.delete(n);
    this._mutated("attributes", n, old, [], []);
  }
  hasAttribute(n) { return this.__a.has(String(n).toLowerCase()); }
  toggleAttribute(n, f) { const has = this.hasAttribute(n), want = f === undefined ? !has : !!f; if (want && !has) this.setAttribute(n, ""); if (!want && has) this.removeAttribute(n); return want; }
  getAttributeNames() { return Array.from(this.__a.keys()); }
  get attributes() { return Array.from(this.__a.entries()).map(function (e) { return { name: e[0], value: e[1] }; }); }
  get id() { return this.getAttribute("id") || ""; } set id(v) { this.setAttribute("id", v); }
  get className() { return this.getAttribute("class") || ""; } set className(v) { this.setAttribute("class", v); }
  get hidden() { return this.hasAttribute("hidden"); } set hidden(v) { if (v) this.setAttribute("hidden", ""); else this.removeAttribute("hidden"); }
  get title() { return this.getAttribute("title") || ""; } set title(v) { this.setAttribute("title", v); }
  get type() { return this.getAttribute("type") || (this.localName === "button" ? "submit" : this.localName === "input" ? "text" : ""); }
  set type(v) { this.setAttribute("type", v); }
  get disabled() { return this.hasAttribute("disabled"); } set disabled(v) { if (v) this.setAttribute("disabled", ""); else this.removeAttribute("disabled"); }
  get draggable() { return this.getAttribute("draggable") === "true"; } set draggable(v) { this.setAttribute("draggable", v ? "true" : "false"); }
  get placeholder() { return this.getAttribute("placeholder") || ""; } set placeholder(v) { this.setAttribute("placeholder", v); }
  get href() { return this.getAttribute("href") || ""; } set href(v) { this.setAttribute("href", v); }
  get checked() { return this.hasAttribute("checked"); } set checked(v) { if (v) this.setAttribute("checked", ""); else this.removeAttribute("checked"); }
  get value() {
    if (this.__value != null) return this.__value;
    if (this.localName === "textarea") return this.textContent;
    if (this.localName === "select") { const o = this.querySelector("option[selected]") || this.querySelector("option"); return o ? (o.getAttribute("value") != null ? o.getAttribute("value") : o.textContent) : ""; }
    return this.getAttribute("value") || "";
  }
  set value(v) { this.__value = String(v == null ? "" : v); }
  get selectionStart() { return this.value.length; } setSelectionRange() {} select() {}
  get dataset() {
    const el = this;
    return new Proxy({}, {
      get(t, p) { return typeof p === "string" ? (el.getAttribute("data-" + kebab(p)) == null ? undefined : el.getAttribute("data-" + kebab(p))) : undefined; },
      set(t, p, v) { el.setAttribute("data-" + kebab(String(p)), v); return true; },
      deleteProperty(t, p) { el.removeAttribute("data-" + kebab(String(p))); return true; },
      has(t, p) { return el.hasAttribute("data-" + kebab(String(p))); },
      ownKeys() { return el.getAttributeNames().filter(function (n) { return n.indexOf("data-") === 0; }).map(function (n) { return camel(n.slice(5)); }); },
      getOwnPropertyDescriptor(t, p) { return el.hasAttribute("data-" + kebab(String(p))) ? { enumerable: true, configurable: true, value: el.getAttribute("data-" + kebab(String(p))) } : undefined; }
    });
  }
  get tabIndex() {
    const a = this.getAttribute("tabindex");
    if (a != null && /^-?\d+$/.test(a.trim())) return parseInt(a, 10);
    if (NATIVE_FOCUS.has(this.localName) || (this.localName === "a" && this.hasAttribute("href"))) return 0;
    return -1;
  }
  set tabIndex(v) { this.setAttribute("tabindex", String(v | 0)); }
  // ---- tree queries ----
  matches(sel) { return matchesList(this, parseSelectorList(sel), this); }
  closest(sel) { const list = parseSelectorList(sel); let n = this; while (n && n.nodeType === 1) { if (matchesList(n, list, this)) return n; n = n.parentNode; } return null; }
  _qsa(sel) {
    const list = parseSelectorList(sel), out = [], scope = this;
    (function walk(n) { for (const c of n.childNodes) if (c.nodeType === 1) { if (matchesList(c, list, scope)) out.push(c); walk(c); } })(this);
    return out;
  }
  querySelectorAll(sel) { return new NodeList(this._qsa(sel)); }
  querySelector(sel) { return this._qsa(sel)[0] || null; }
  getElementsByTagName(t) { return new HTMLCollection(this._qsa(t === "*" ? "*" : t)); }
  getElementsByClassName(c) { return new HTMLCollection(this._qsa(c.trim().split(/\s+/).map(function (x) { return "." + x; }).join(""))); }
  // ---- markup ----
  get innerHTML() { return this.childNodes.map(serialize).join(""); }
  set innerHTML(html) {
    const old = this.childNodes.slice();
    for (const c of old) c.parentNode = null;
    this.childNodes = [];
    const frag = parseHTML(this.ownerDocument, String(html == null ? "" : html), RAW.has(this.localName) ? this.localName : null);
    for (const c of frag) { c.parentNode = this; this.childNodes.push(c); }
    this._mutated("childList", null, null, this.childNodes.slice(), old);
  }
  get outerHTML() { return serialize(this); }
  get innerText() { return this.textContent; } set innerText(v) { this.textContent = v; }
  insertAdjacentHTML(pos, html) {
    const nodes = parseHTML(this.ownerDocument, String(html), null);
    const p = pos.toLowerCase();
    for (const n of nodes) {
      if (p === "beforeend") this.appendChild(n);
      else if (p === "afterbegin") this.insertBefore(n, this.firstChild);
      else if (p === "beforebegin") this.parentNode.insertBefore(n, this);
      else if (p === "afterend") this.parentNode.insertBefore(n, this.nextSibling);
    }
  }
  insertAdjacentElement(pos, el) { const p = pos.toLowerCase();
    if (p === "beforeend") this.appendChild(el); else if (p === "afterbegin") this.insertBefore(el, this.firstChild);
    else if (p === "beforebegin") this.parentNode.insertBefore(el, this); else this.parentNode.insertBefore(el, this.nextSibling); return el; }
  before(n) { this.parentNode.insertBefore(n, this); } after(n) { this.parentNode.insertBefore(n, this.nextSibling); }
  replaceWith(n) { this.parentNode.replaceChild(n, this); }
  cloneNode(deep) { const c = this.ownerDocument.createElement(this.localName); for (const [k, v] of this.__a) c.__a.set(k, v); if (deep) c.innerHTML = this.innerHTML; return c; }
  // ---- rendering / layout (assigned by tests) ----
  get __rendered() {
    if (!this.isConnected) return false;
    let n = this;
    while (n && n.nodeType === 1) {
      if (n.hasAttribute("hidden") || n.style.display === "none" || n.__cssHidden) return false;
      n = n.parentNode;
    }
    return true;
  }
  getBoundingClientRect() {
    const r = this.__rect || { left: 0, top: 0, width: 0, height: 0 };
    return { left: r.left, top: r.top, width: r.width, height: r.height, x: r.left, y: r.top,
      right: r.left + r.width, bottom: r.top + r.height };
  }
  getClientRects() { return this.__rendered ? [this.getBoundingClientRect()] : []; }
  get offsetWidth() { return this.__rendered && this.__rect ? (this.__rect.ow != null ? this.__rect.ow : this.__rect.width) : 0; }
  get offsetHeight() { return this.__rendered && this.__rect ? (this.__rect.oh != null ? this.__rect.oh : this.__rect.height) : 0; }
  get offsetLeft() { return this.__rect ? this.__rect.left : 0; }
  get offsetTop() { return this.__rect ? this.__rect.top : 0; }
  get offsetParent() { return this.__rendered ? (this.parentElement || null) : null; }
  get clientWidth() { return this.offsetWidth; } get clientHeight() { return this.offsetHeight; }
  get scrollHeight() { return this.__scrollHeight || this.offsetHeight; } get scrollWidth() { return this.offsetWidth; }
  scrollIntoView() {} scrollTo() {} scrollBy() {}
  getAnimations() { return (this.__anims || []).filter(function (a) { return a.playState !== "finished" && a.playState !== "idle"; }); }
  animate(kf, opt) { return makeAnimation(this, kf, opt); }
  // ---- focus ----
  get __focusable() {
    if (!this.__rendered) return false;
    if (this.hasAttribute("disabled") && NATIVE_FOCUS.has(this.localName)) return false;
    if (this.hasAttribute("tabindex")) return true;
    return NATIVE_FOCUS.has(this.localName) || (this.localName === "a" && this.hasAttribute("href"));
  }
  focus() { this.ownerDocument.__setFocus(this.__focusable ? this : null, this); }
  blur() { const d = this.ownerDocument; if (d.activeElement === this) d.__setFocus(d.body, null, true); }
  click() {
    if (this.hasAttribute("disabled") && NATIVE_FOCUS.has(this.localName)) return;
    dispatch(this, new MouseEvent("click", { bubbles: true, cancelable: true, detail: 0 }));
  }
}
function makeAnimation(el, kf, opt) {
  const timing = typeof opt === "number" ? { duration: opt } : Object.assign({}, opt || {});
  let res; const finished = new Promise(function (r) { res = r; });
  const a = {
    effect: { getComputedTiming() { return { iterations: timing.iterations == null ? 1 : timing.iterations, duration: timing.duration || 0 }; },
      getTiming() { return timing; } },
    timing: timing, keyframes: kf, playState: "running", onfinish: null, finished: finished,
    cancel() { this.playState = "idle"; }, finish() { this.playState = "finished"; res(this); if (this.onfinish) this.onfinish(); },
    pause() {}, play() {}, reverse() {}, commitStyles() {}
  };
  el.__anims = el.__anims || []; el.__anims.push(a);
  const doc = el.ownerDocument; if (doc) (doc.__allAnims = doc.__allAnims || []).push(a);
  if (!(timing.duration > 0) && !(timing.delay > 0)) a.finish();
  return a;
}
function serialize(n) {
  if (n.nodeType === 3) return n.parentNode && RAW.has(n.parentNode.localName) ? n.data : escText(n.data);
  if (n.nodeType === 8) return "<!--" + n.data + "-->";
  if (n.nodeType !== 1) return "";
  let s = "<" + n.localName;
  for (const [k, v] of n.__a) s += v === "" ? " " + k : " " + k + '="' + escAttr(v) + '"';
  s += ">";
  if (VOID.has(n.localName)) return s;
  return s + n.childNodes.map(serialize).join("") + "</" + n.localName + ">";
}

// --------------------------------------------------------------------------
// HTML parser (tolerant, for the dashboard's own markup)
// --------------------------------------------------------------------------
function parseHTML(doc, html, rawParent) {
  const root = new Fragment(doc);
  if (rawParent) { if (html) root.appendChild(doc.createTextNode(html)); return root.childNodes.slice(); }
  const stack = [root];
  let i = 0, inSvg = 0;
  const top = function () { return stack[stack.length - 1]; };
  const add = function (n) { const p = top(); p.childNodes.push(n); n.parentNode = p; };
  while (i < html.length) {
    const lt = html.indexOf("<", i);
    if (lt < 0) { add(doc.createTextNode(decode(html.slice(i)))); break; }
    if (lt > i) add(doc.createTextNode(decode(html.slice(i, lt))));
    if (html.startsWith("<!--", lt)) {
      const e = html.indexOf("-->", lt + 4); const end = e < 0 ? html.length : e;
      add(new Comment(doc, html.slice(lt + 4, end))); i = end + 3; continue;
    }
    if (html[lt + 1] === "!" || html[lt + 1] === "?") { const e = html.indexOf(">", lt); i = e < 0 ? html.length : e + 1; continue; }
    if (html[lt + 1] === "/") {
      const e = html.indexOf(">", lt); const name = html.slice(lt + 2, e).trim().toLowerCase();
      for (let k = stack.length - 1; k > 0; k--) {
        if (stack[k].localName === name) { if (name === "svg") inSvg--; stack.length = k; break; }
      }
      i = e + 1; continue;
    }
    if (!/[A-Za-z]/.test(html[lt + 1] || "")) { add(doc.createTextNode("<")); i = lt + 1; continue; }
    // start tag
    let j = lt + 1;
    while (j < html.length && /[A-Za-z0-9\-:]/.test(html[j])) j++;
    const name = html.slice(lt + 1, j).toLowerCase();
    const el = doc.createElement(name);
    let selfClose = false;
    while (j < html.length) {
      while (j < html.length && /\s/.test(html[j])) j++;
      if (html[j] === ">") { j++; break; }
      if (html[j] === "/" && html[j + 1] === ">") { selfClose = true; j += 2; break; }
      if (html[j] === "/") { j++; continue; }
      let k = j; while (k < html.length && !/[\s=>]/.test(html[k]) && !(html[k] === "/" && html[k + 1] === ">")) k++;
      const an = html.slice(j, k).toLowerCase(); j = k;
      while (j < html.length && /\s/.test(html[j])) j++;
      let av = "";
      if (html[j] === "=") {
        j++; while (j < html.length && /\s/.test(html[j])) j++;
        if (html[j] === '"' || html[j] === "'") { const q = html[j]; const e = html.indexOf(q, j + 1); av = html.slice(j + 1, e); j = e + 1; }
        else { let e = j; while (e < html.length && !/[\s>]/.test(html[e])) e++; av = html.slice(j, e); j = e; }
      }
      if (an) el.__a.set(an, decode(av));
    }
    add(el);
    if (name === "svg") inSvg++;
    if (VOID.has(name) || selfClose) { if (name === "svg" && selfClose) inSvg--; i = j; continue; }
    if (RAW.has(name) && !inSvg) {
      const close = html.toLowerCase().indexOf("</" + name, j);
      const end = close < 0 ? html.length : close;
      const txt = html.slice(j, end);
      if (txt) { const t = doc.createTextNode(name === "textarea" || name === "title" ? decode(txt) : txt); t.parentNode = el; el.childNodes.push(t); }
      const gt = close < 0 ? html.length : html.indexOf(">", close);
      i = gt + 1; continue;
    }
    stack.push(el); i = j;
  }
  const out = root.childNodes.slice();
  for (const n of out) n.parentNode = null;
  return out;
}

// --------------------------------------------------------------------------
// document + window
// --------------------------------------------------------------------------
class Document extends Node {
  constructor() {
    super(null);
    this.ownerDocument = null; this.nodeType = 9; this.nodeName = "#document";
    this.readyState = "complete"; this.hidden = false; this.visibilityState = "visible";
    this.__active = null; this.__observers = [];
    this.title = "";
    const self = this;
    this.__mo = function (target, type, name, oldValue, added, removed) { queueRecords(self, target, type, name, oldValue, added, removed); };
  }
  get documentElement() { return this.childNodes.find(function (c) { return c.nodeType === 1; }) || null; }
  get head() { return this.documentElement ? this.documentElement.querySelector("head") : null; }
  get body() { return this.documentElement ? this.documentElement.querySelector("body") : null; }
  get scrollingElement() { return this.documentElement; }
  get children() { return new HTMLCollection(this.childNodes.filter(function (c) { return c.nodeType === 1; })); }
  get firstElementChild() { return this.childNodes.find(function (c) { return c.nodeType === 1; }) || null; }
  get lastElementChild() { const k = this.childNodes.filter(function (c) { return c.nodeType === 1; }); return k[k.length - 1] || null; }
  createElement(t) { const e = new Element(this, t); return e; }
  createElementNS(ns, t) { return new Element(this, t, ns); }
  createTextNode(d) { return new Text(this, d); }
  createComment(d) { return new Comment(this, d); }
  createDocumentFragment() { return new Fragment(this); }
  createEvent() { return new Event(""); }
  getElementById(id) {
    const r = this.documentElement; if (!r) return null;
    if (r.getAttribute("id") === id) return r;
    let found = null;
    (function walk(n) { for (const c of n.childNodes) { if (found) return; if (c.nodeType === 1) { if (c.getAttribute("id") === id) { found = c; return; } walk(c); } } })(r);
    return found;
  }
  _qsa(sel) { const r = this.documentElement; if (!r) return []; const all = r.matches(sel) ? [r] : []; return all.concat(r._qsa(sel)); }
  querySelectorAll(sel) { return new NodeList(this._qsa(sel)); }
  querySelector(sel) { return this._qsa(sel)[0] || null; }
  getElementsByTagName(t) { return new HTMLCollection(this._qsa(t)); }
  getElementsByClassName(c) { return this.documentElement ? this.documentElement.getElementsByClassName(c) : new HTMLCollection([]); }
  get activeElement() {
    const a = this.__active;
    if (a && a.isConnected && a.__focusable) return a;
    return this.body;                               // focus fixup: no events
  }
  hasFocus() { return true; }
  getSelection() { return this.defaultView.getSelection(); }
  getAnimations() { return (this.__allAnims || []).filter(function (a) { return a.playState === "running"; }); }
  __setFocus(el, requested, isBlur) {
    const old = this.activeElement === this.body ? null : this.activeElement;
    if (!el && !isBlur) return;                     // not focusable: no-op, like a browser
    const next = el === this.body ? null : el;
    if (old === next) return;
    // as in a browser, nothing holds focus while blur/focusout run
    this.__active = null;
    if (old) {
      dispatch(old, new FocusEvent("focusout", { bubbles: true, relatedTarget: next }));
      dispatch(old, new FocusEvent("blur", { bubbles: false, relatedTarget: next }));
    }
    this.__active = next;
    if (next) {
      dispatch(next, new FocusEvent("focusin", { bubbles: true, relatedTarget: old }));
      dispatch(next, new FocusEvent("focus", { bubbles: false, relatedTarget: old }));
    }
  }
}
function queueRecords(doc, target, type, name, oldValue, added, removed) {
  for (const o of doc.__observers) {
    for (const reg of o.__targets) {
      const t = reg.target, opt = reg.opt;
      if (t !== target && !(opt.subtree && t.contains(target))) continue;
      if (type === "attributes") {
        if (!opt.attributes && !opt.attributeFilter) continue;
        if (opt.attributeFilter && opt.attributeFilter.indexOf(name) < 0) continue;
      } else if (type === "childList" && !opt.childList) continue;
      o.__queue.push({ type: type, target: target, attributeName: name, oldValue: oldValue,
        addedNodes: added, removedNodes: removed });
      if (!o.__scheduled) {
        o.__scheduled = true;
        queueMicrotask(function () { o.__scheduled = false; const recs = o.__queue.splice(0); if (recs.length && o.__targets.length) { try { o.__cb(recs, o); } catch (e) { reportError(doc, e); } } });
      }
      break;
    }
  }
}
function makeMutationObserver(doc) {
  return class MutationObserver {
    constructor(cb) { this.__cb = cb; this.__targets = []; this.__queue = []; this.__scheduled = false; }
    observe(target, opt) { this.__targets.push({ target: target, opt: opt || {} }); if (doc.__observers.indexOf(this) < 0) doc.__observers.push(this); }
    disconnect() { this.__targets = []; this.__queue = []; const i = doc.__observers.indexOf(this); if (i >= 0) doc.__observers.splice(i, 1); }
    takeRecords() { return this.__queue.splice(0); }
  };
}

// Build a window. opts.html: full document markup. opts.routes: {path: data |
// function(url, init) -> data}. Returns the vm context (a window) with helpers:
//   w.__run(code, name)   run a script in the window
//   w.__flushTimers(n)    run queued setTimeout callbacks (n rounds)
//   w.__tick()            await pending microtasks/promises
//   w.__errors            listener errors (thrown errors are collected here)
//   w.__fetches           [{url, init}] every fetch the code made
function createWindow(opts) {
  opts = opts || {};
  const doc = new Document();
  const html = opts.html || "<!doctype html><html><head></head><body></body></html>";
  const nodes = parseHTML(doc, html, null);
  for (const n of nodes) { if (n.nodeType === 1 || n.nodeType === 8) { doc.childNodes.push(n); n.parentNode = doc; } }
  if (!doc.documentElement) { const h = doc.createElement("html"); doc.childNodes.push(h); h.parentNode = doc; }
  const root = doc.documentElement;
  if (!root.querySelector("head")) root.insertBefore(doc.createElement("head"), root.firstChild);
  if (!root.querySelector("body")) root.appendChild(doc.createElement("body"));
  // scripts are data here; the test decides what to run
  const timers = []; let tid = 0;
  const intervals = [];
  const store = new Map();
  const w = {
    document: doc, console: console, __errors: [], __fetches: [], __timers: timers, __intervals: intervals,
    innerWidth: opts.width || 1440, innerHeight: opts.height || 900, devicePixelRatio: 2,
    navigator: { userAgent: "mini-dom", platform: "MacIntel", language: "en-US", languages: ["en-US"],
      clipboard: { writeText: async function () {}, readText: async function () { return ""; } }, maxTouchPoints: 0 },
    location: { href: "http://127.0.0.1:7788/", origin: "http://127.0.0.1:7788", host: "127.0.0.1:7788",
      hostname: "127.0.0.1", pathname: "/", search: "", hash: "", protocol: "http:", reload() {}, assign() {}, replace() {} },
    history: { pushState() {}, replaceState() {}, back() {}, state: null, length: 1 },
    localStorage: { getItem(k) { return store.has(k) ? store.get(k) : null; }, setItem(k, v) { store.set(k, String(v)); },
      removeItem(k) { store.delete(k); }, clear() { store.clear(); }, key(i) { return Array.from(store.keys())[i] || null; },
      get length() { return store.size; } },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    setTimeout(fn, ms) { const id = ++tid; timers.push({ id: id, fn: fn, ms: ms || 0, args: Array.prototype.slice.call(arguments, 2) }); return id; },
    clearTimeout(id) { const i = timers.findIndex(function (t) { return t.id === id; }); if (i >= 0) timers.splice(i, 1); },
    setInterval(fn, ms) { const id = ++tid; intervals.push({ id: id, fn: fn, ms: ms }); return id; },
    clearInterval(id) { const i = intervals.findIndex(function (t) { return t.id === id; }); if (i >= 0) intervals.splice(i, 1); },
    requestAnimationFrame(fn) { return w.setTimeout(function () { fn(Date.now()); }, 16); },
    cancelAnimationFrame(id) { w.clearTimeout(id); },
    queueMicrotask: queueMicrotask, structuredClone: structuredClone,
    Promise: Promise, URL: URL, URLSearchParams: URLSearchParams, AbortController: AbortController,
    TextEncoder: TextEncoder, TextDecoder: TextDecoder, Blob: typeof Blob !== "undefined" ? Blob : function () {},
    crypto: { randomUUID() { return "00000000-0000-4000-8000-" + String(++tid).padStart(12, "0"); },
      getRandomValues(a) { for (let i = 0; i < a.length; i++) a[i] = (i * 2654435761) >>> 0 & 0xff; return a; } },
    performance: { now() { return Date.now(); } },
    Intl: Intl, JSON: JSON, Math: Math, Date: Date,
    Node: Node, NodeList: NodeList, HTMLCollection: HTMLCollection, Element: Element, HTMLElement: Element, SVGElement: Element, Text: Text, Document: Document,
    Event: Event, CustomEvent: CustomEvent, UIEvent: UIEvent, KeyboardEvent: KeyboardEvent, MouseEvent: MouseEvent,
    PointerEvent: PointerEvent, FocusEvent: FocusEvent,
    MutationObserver: makeMutationObserver(doc),
    ResizeObserver: class { constructor(cb) { this.cb = cb; (w.__ros = w.__ros || []).push(this); } observe(t) { (this.t = this.t || []).push(t); } unobserve() {} disconnect() {} },
    IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
    CSS: { escape(s) { return String(s).replace(/[^A-Za-z0-9_-]/g, function (c) { return "\\" + c; }); }, supports() { return true; } },
    getComputedStyle(el) { return opts.computedStyle ? opts.computedStyle(el) : el.style; },
    getSelection() { return { toString() { return ""; }, removeAllRanges() {}, rangeCount: 0, isCollapsed: true }; },
    alert() {}, confirm() { return w.__confirm !== undefined ? w.__confirm : true; }, prompt() { return null; },
    scrollTo() {}, scrollBy() {}, open() { return null; }, focus() {}, blur() {}, print() {}
  };
  w.matchMedia = function (q) {
    const m = { media: q, matches: !!(opts.media && opts.media(q)), onchange: null, __ls: [],
      addEventListener(t, fn) { this.__ls.push(fn); }, removeEventListener() {},
      addListener(fn) { this.__ls.push(fn); }, removeListener() {} };
    (w.__mqls = w.__mqls || []).push(m);
    return m;
  };
  w.fetch = async function (url, init) {
    url = String(url);
    w.__fetches.push({ url: url, init: init || {} });
    if (init && init.signal && init.signal.aborted) { const e = new Error("aborted"); e.name = "AbortError"; throw e; }
    const path = url.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    const routes = w.__routes || {};
    let data = Object.prototype.hasOwnProperty.call(routes, path) ? routes[path] : (w.__defaultRoute !== undefined ? w.__defaultRoute : {});
    if (typeof data === "function") data = await data(url, init || {});
    if (data && data.__reject) throw new Error(data.__reject);
    const status = data && data.__status ? data.__status : 200;
    const body = JSON.stringify(data && data.__status ? (data.body || {}) : data);
    return { ok: status < 400, status: status, headers: { get() { return "application/json"; } },
      json: async function () { return JSON.parse(body); }, text: async function () { return body; } };
  };
  w.window = w; w.self = w; w.globalThis = w; w.top = w; w.parent = w;
  doc.defaultView = w;
  // window-level events share the node event machinery
  const winNode = new Node(doc);
  w.addEventListener = winNode.addEventListener.bind(winNode);
  w.removeEventListener = winNode.removeEventListener.bind(winNode);
  w.dispatchEvent = function (ev) { ev.target = w; fire(winNode, ev, 2); return !ev.defaultPrevented; };
  // dispatch() puts `w` first on every path that reaches the document
  w.__l = null;
  Object.defineProperty(w, "__l", { get() { return winNode.__l; }, set(v) { winNode.__l = v; } });
  const ctx = vm.createContext(w);
  w.__run = function (code, name) { return vm.runInContext(code, ctx, { filename: name || "inline.js" }); };
  w.__flushTimers = function (rounds) {
    rounds = rounds || 1;
    for (let r = 0; r < rounds; r++) {
      const batch = timers.splice(0);
      for (const t of batch) { try { t.fn.apply(null, t.args); } catch (e) { w.__errors.push(e); } }
    }
  };
  w.__tick = async function (n) { for (let i = 0; i < (n || 8); i++) await new Promise(function (r) { setImmediate(r); }); };
  w.__key = function (target, key, init) {
    const ev = new KeyboardEvent("keydown", Object.assign({ key: key, bubbles: true, cancelable: true }, init || {}));
    dispatch(target || doc.activeElement || doc.body, ev);
    return ev;
  };
  w.__click = function (target, init) {
    const ev = new MouseEvent("click", Object.assign({ bubbles: true, cancelable: true, detail: 1 }, init || {}));
    dispatch(target, ev);
    return ev;
  };
  return w;
}

// --------------------------------------------------------------------------
// CSS: a small cascade over the page's real <style> sheets
// --------------------------------------------------------------------------
// parseCSS(text) -> [{selector, decls:[{prop,value,important}], media:[...], order}]
// Handles @media (nested in each other), @supports (taken as true); skips
// @keyframes/@property/@font-face/@container. Comments are stripped.
function parseCSS(text, base) {
  text = String(text).replace(/\/\*[\s\S]*?\*\//g, "");
  const out = []; let order = base || 0;
  function block(src, media) {
    let i = 0;
    while (i < src.length) {
      while (i < src.length && /\s/.test(src[i])) i++;
      if (i >= src.length) break;
      const open = src.indexOf("{", i);
      if (open < 0) break;
      const head = src.slice(i, open).trim();
      let depth = 1, j = open + 1;
      while (j < src.length && depth) { if (src[j] === "{") depth++; else if (src[j] === "}") depth--; j++; }
      const inner = src.slice(open + 1, j - 1);
      i = j;
      if (head[0] === "@") {
        const at = head.slice(1).split(/[\s(]/)[0].toLowerCase();
        if (at === "media") block(inner, media.concat([head.slice(6).trim()]));
        else if (at === "supports") block(inner, media);
        continue;                                   // keyframes, property, container, font-face
      }
      const decls = [];
      inner.split(";").forEach(function (d) {
        const k = d.indexOf(":"); if (k < 0) return;
        const prop = d.slice(0, k).trim().toLowerCase(); let value = d.slice(k + 1).trim();
        const important = /!\s*important\s*$/i.test(value);
        if (important) value = value.replace(/!\s*important\s*$/i, "").trim();
        if (prop) decls.push({ prop: prop, value: value, important: important });
      });
      out.push({ selector: head, decls: decls, media: media, order: order++ });
    }
  }
  block(text, []);
  return out;
}
// media query against env {width, height, reduce, dark}
function mediaMatches(q, env) {
  return q.split(",").some(function (part) {
    part = part.trim().toLowerCase();
    let neg = false;
    if (part.indexOf("not ") === 0) { neg = true; part = part.slice(4); }
    part = part.replace(/^only\s+/, "").replace(/^(screen|all)\s*(and\s*)?/, "");
    const conds = part.match(/\([^)]*\)/g) || [];
    let ok = conds.every(function (c) {
      const m = c.slice(1, -1).split(":").map(function (x) { return x.trim(); });
      const f = m[0], v = m[1];
      if (f === "max-width") return env.width <= parseFloat(v);
      if (f === "min-width") return env.width >= parseFloat(v);
      if (f === "max-height") return env.height <= parseFloat(v);
      if (f === "min-height") return env.height >= parseFloat(v);
      if (f === "prefers-reduced-motion") return (v === "reduce") === !!env.reduce;
      if (f === "prefers-color-scheme") return (v === "dark") === !!env.dark;
      if (f === "hover") return v === "hover";
      if (f === "pointer") return v === "fine";
      throw new Error("mini-dom: unsupported media feature " + c);
    });
    return neg ? !ok : ok;
  });
}
function specificity(sel) {
  let a = 0, b = 0, c = 0;
  const s = sel.replace(/:(not|is|has)\(([^()]*(\([^()]*\))?[^()]*)\)/g, function (m, fn, arg) {
    if (fn === "where") return "";
    const best = splitTop(arg, ",").map(specificity).sort(function (x, y) { return y[0] - x[0] || y[1] - x[1] || y[2] - x[2]; })[0] || [0, 0, 0];
    a += best[0]; b += best[1]; c += best[2]; return "";
  }).replace(/:where\([^)]*\)/g, "");
  a += (s.match(/#[\w-]+/g) || []).length;
  b += (s.match(/\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+/g) || []).length;
  c += (s.replace(/::[\w-]+/g, function () { c++; return ""; }).match(/(^|[\s>+~(])[a-zA-Z][\w-]*/g) || []).length;
  return [a, b, c];
}
function cmpSpec(x, y) { return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; }
// The value `prop` resolves to on `el` (or its ::before/::after when pseudo is
// given), from every <style> in the document plus el's inline style. env:
// {width, height, reduce, dark}. Returns {value, rule} or null. Selectors the
// engine cannot read are skipped and listed in w.__cssSkipped.
function computedValue(w, el, prop, pseudo, env) {
  const doc = w.document;
  env = Object.assign({ width: w.innerWidth, height: w.innerHeight, reduce: false, dark: false }, env || {});
  if (!doc.__css || doc.__cssStamp !== doc.querySelectorAll("style").length) {
    let rules = [];
    doc.querySelectorAll("style").forEach(function (st) { rules = rules.concat(parseCSS(st.textContent, rules.length)); });
    doc.__css = rules; doc.__cssStamp = doc.querySelectorAll("style").length;
  }
  w.__cssSkipped = w.__cssSkipped || [];
  let best = null;
  const want = pseudo ? pseudo.replace(/^:+/, "") : null;
  for (const r of doc.__css) {
    const d = r.decls.filter(function (x) { return x.prop === prop; }).pop();
    if (!d) continue;
    if (!r.media.every(function (m) { return mediaMatches(m, env); })) continue;
    let spec = null;
    for (const one of splitTop(r.selector, ",")) {
      const pm = one.match(/::?(before|after)\s*$/);
      const pe = pm ? pm[1] : null;
      if ((pe || null) !== want) continue;
      const host = pm ? one.slice(0, pm.index) : one;
      let ok = false;
      try { ok = el.matches(host.trim() === "" || /[\s>+~]$/.test(host) ? host + "*" : host); }
      catch (e) { w.__cssSkipped.push(one); continue; }
      if (ok) { const sp = specificity(one); if (!spec || cmpSpec(sp, spec) > 0) spec = sp; }
    }
    if (!spec) continue;
    const cand = { value: d.value, important: d.important, spec: spec, order: r.order, rule: r };
    if (!best || (cand.important !== best.important ? cand.important :
        (cmpSpec(cand.spec, best.spec) || cand.order - best.order) > 0)) best = cand;
  }
  if (!pseudo) {
    const inline = el.style.getPropertyValue(prop);
    if (inline && !(best && best.important)) return { value: inline, rule: null };
  }
  return best ? { value: best.value, rule: best.rule } : null;
}

// Pull the inline <script> bodies (no src) out of a document, in order.
function inlineScripts(html) {
  const out = []; const re = /<script(\s[^>]*)?>([\s\S]*?)<\/script>/gi; let m;
  while ((m = re.exec(html))) { if (!/\ssrc\s*=/.test(m[1] || "")) out.push(m[2]); }
  return out;
}

function harness(title) {
  let pass = 0; const fails = [];
  return {
    check(name, cond, detail) {
      if (cond) { pass++; console.log("  ok   " + name); }
      else { fails.push(name); console.log("  FAIL " + name + (detail !== undefined ? "  <- " + (typeof detail === "string" ? detail : JSON.stringify(detail)) : "")); }
    },
    done() {
      console.log("TESTS " + pass + " passed " + fails.length + " failed");
      process.exit(fails.length ? 1 : 0);
    },
    get failed() { return fails.length; }
  };
}

// Boot the real dashboard page: index.html's markup + its inline script (and
// any aux_*.js named in opts.aux, in that order) against default API answers.
// opts.html overrides the page source (a test proving it fails on old code
// passes `git show <rev>:dashboard/index.html`). opts.routes merge over the
// defaults. Resolves once the boot-time fetches have settled.
const DEFAULT_MODELS = {
  models: [
    { id: "mlx-community/Primary-27B", label: "Primary 27B", active: true, downloaded: true, ram: 19, fit: "ok", download_gb: 17 },
    { id: "mlx-community/Small-9B", label: "Small 9B", downloaded: true, ram: 7, fit: "ok", download_gb: 6 },
    { id: "mlx-community/Huge-70B", label: "Huge 70B", downloaded: false, ram: 60, fit: "no", download_gb: 40 }
  ],
  paused: false, idle_suspended: false, thinking: { supported: true, enabled: false },
  mem: { machine_gb: 64, soft_gb: 50 }, ram_gb: 19, disk_free_gb: 400, disk_headroom_gb: 5,
  bg: { model: "mlx-community/Small-9B", label: "Small 9B", online: false }
};
const CURRENT = { w: null, hooked: false };
async function bootDashboard(opts) {
  opts = opts || {};
  const fs = require("fs"), path = require("path");
  const repo = opts.repo || process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
  const html = opts.html != null ? opts.html : fs.readFileSync(path.join(repo, "dashboard", "index.html"), "utf8");
  const w = createWindow({ html: html, width: opts.width, height: opts.height, media: opts.media, computedStyle: opts.computedStyle });
  const doc = w.document;
  w.__routes = Object.assign({
    "/api/models": JSON.parse(JSON.stringify(DEFAULT_MODELS)),
    "/api/claude/escalate": { enabled: false },
    "/api/hub": { order: [], widgets: {}, model_online: true },
    "/api/actions": { actions: [] },
    "/api/sessions": { sessions: [] },
    "/api/history": { messages: [] },
    "/api/catalog": { catalog: [] },
    "/api/capabilities": { skills: { total: 0, categories: [], items: [] }, memory: { count: 0, facts: [] },
      insights: { sessions: 0, tool_calls: 0, platforms: [], daily: [] } }
  }, opts.routes || {});
  // a rejected promise in page code is a page error, not a crashed suite
  CURRENT.w = w;
  if (!CURRENT.hooked) {
    CURRENT.hooked = true;
    process.on("unhandledRejection", function (e) { if (CURRENT.w) CURRENT.w.__errors.push(e); else throw e; });
  }
  // browsers expose every element id as a window property ("named access");
  // the page leans on it in places (e.g. the Mind sparkline)
  doc.querySelectorAll("[id]").forEach(function (el) {
    const id = el.id;
    if (/^[A-Za-z_$][\w$]*$/.test(id) && !(id in w))
      Object.defineProperty(w, id, { configurable: true, get: function () { return doc.getElementById(id); } });
  });
  if (opts.before) opts.before(w);
  const scripts = inlineScripts(html);
  for (let i = 0; i < scripts.length; i++) w.__run(scripts[i], "index.html#inline" + i);
  for (const f of (opts.aux || [])) {
    const src = opts.auxSource && opts.auxSource[f] != null ? opts.auxSource[f] : fs.readFileSync(path.join(repo, "dashboard", f), "utf8");
    w.__run(src, f);
  }
  await w.__tick();
  return w;
}

module.exports = { createWindow, bootDashboard, DEFAULT_MODELS, parseCSS, computedValue, mediaMatches, specificity, parseHTML, inlineScripts, harness, Event, KeyboardEvent, MouseEvent, PointerEvent, FocusEvent };
