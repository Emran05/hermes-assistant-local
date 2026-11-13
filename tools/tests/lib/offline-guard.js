'use strict';
// Offline guard for the unit tier's Node suites — the JS sibling of
// tools/tests/lib/usercustomize.py.
//
// Preloaded via `NODE_OPTIONS="--require .../offline-guard.js"` for the unit
// tier only (see run.sh do_unit). Inert unless HERMES_TESTS_OFFLINE=1 is set,
// so requiring this file outside the unit tier (or outside run.sh entirely)
// is harmless. With it set, every Node process the tier starts refuses:
//
//   * every NON-LOOPBACK destination (C81 — the tier used to refuse only
//     three local ports, so a suite could reach the internet unnoticed);
//   * the dashboard (7788), both model lanes (8080, 8081) and the serve
//     backend (9119), even on loopback.
//
// Loopback on any other port stays open (a suite may start its own
// throwaway server on port 0).
//
// Two layers:
//   1. net.Socket.prototype.connect — the one door every TCP client in Node
//      goes through: http/https, tls, undici (so fetch and the global
//      WebSocket), net.connect. This is the catch-all.
//   2. http.request/get, https.request/get and fetch, so the common calls
//      fail with a clear message at the call site. Each accepts a string, a
//      URL object or a Request — C82: `fetch(new URL(...))` used to fall
//      straight through to the real fetch because only a string or `.url`
//      was understood, and a URL object has neither.
//
// The http/https layer throws synchronously and fetch rejects, matching the
// Python guard's abruptness. Not covered: UDP (dgram) to an IP literal, and
// anything a suite shells out to (curl via child_process) — that process has
// to carry its own guard (a python3 child gets usercustomize automatically).
if (process.env.HERMES_TESTS_OFFLINE === '1') {
  // Read per call (cheap), so a self-check can block the port of a listener
  // it opened itself: a broken guard then reaches that listener, not a
  // service the owner is running.
  function blockedPorts() {
    var out = {};
    (process.env.HERMES_TESTS_BLOCKED_PORTS || '7788,8080,8081,9119')
      .split(',')
      .map(function (s) { return s.trim(); })
      .filter(Boolean)
      .forEach(function (p) { out[p] = true; });
    return out;
  }

  var LOCAL_NAMES = { '': true, 'localhost': true, 'localhost.': true,
    'ip6-localhost': true, 'ip6-loopback': true };

  function isLoopback(host) {
    var h = String(host == null ? '' : host).trim().toLowerCase();
    if (h.charAt(0) === '[' && h.charAt(h.length - 1) === ']') h = h.slice(1, -1);
    h = h.split('%')[0];
    if (LOCAL_NAMES[h]) return true;
    if (h === '::1' || h === '0:0:0:0:0:0:0:1') return true;
    var m = /^(?:::ffff:)?(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
    return !!(m && m[1] === '127');
  }

  // null when allowed, else the refusal message
  function verdict(host, port) {
    if (!isLoopback(host)) {
      return 'egress to ' + host + ':' + port +
        ' is out of bounds for the unit tier (non-loopback)';
    }
    if (blockedPorts()[String(port)]) {
      return host + ':' + port + ' is out of bounds for the unit tier';
    }
    return null;
  }

  function refusal(where, msg, code) {
    // HERMES_TESTS_EGRESS_LOG: one line per refusal, the same file the Python
    // guard writes (run.sh reports the count per suite)
    if (process.env.HERMES_TESTS_EGRESS_LOG) {
      try {
        require('fs').appendFileSync(process.env.HERMES_TESTS_EGRESS_LOG,
          (process.env.HERMES_TESTS_SUITE || '-') + ' pid=' + process.pid + ' ' +
          where + ' ' + msg + '\n');
      } catch (e) { /* best effort */ }
    }
    var e = new Error('HERMES_TESTS_OFFLINE: ' + where + ' ' + msg);
    e.code = code || 'ECONNREFUSED';
    return e;
  }

  // ---- layer 1: the socket itself --------------------------------------
  var net = require('net');
  var realConnect = net.Socket.prototype.connect;
  net.Socket.prototype.connect = function () {
    var a0 = arguments[0], opts = null, host, port;
    if (Array.isArray(a0)) opts = a0[0];                  // net.connect's normalized args
    else if (a0 && typeof a0 === 'object') opts = a0;
    if (opts) {
      if (opts.path) return realConnect.apply(this, arguments);   // unix socket
      host = opts.host; port = opts.port;
    } else if (typeof a0 === 'number' || (typeof a0 === 'string' && /^\d+$/.test(a0))) {
      port = a0; host = arguments[1];
      if (typeof host !== 'string') host = 'localhost';
    } else {
      return realConnect.apply(this, arguments);         // a path string
    }
    var msg = verdict(host == null ? 'localhost' : host, port);
    if (msg) throw refusal('connect', msg, isLoopback(host) ? 'ECONNREFUSED' : 'ENETUNREACH');
    return realConnect.apply(this, arguments);
  };

  // ---- layer 2: the call sites -----------------------------------------
  function targetOf(input, dflt) {
    // -> {host, port} or null when it cannot be parsed (the socket layer
    //    still catches whatever it turns into).
    var u = null;
    try {
      if (typeof input === 'string') u = new URL(input);
      else if (input instanceof URL) u = input;
      else if (input && typeof input.url === 'string') u = new URL(input.url);   // Request
      else if (input && typeof input.href === 'string') u = new URL(input.href);
    } catch (e) { u = null; }
    if (!u) return null;
    return { host: u.hostname, port: u.port || (u.protocol === 'https:' ? '443' : dflt) };
  }

  ['http', 'https'].forEach(function (name) {
    var mod;
    try { mod = require(name); } catch (e) { return; }
    var realRequest = mod.request;
    var dflt = name === 'https' ? '443' : '80';
    var patchedRequest = function (arg0) {
      var t = null;
      if (typeof arg0 === 'string' || arg0 instanceof URL) {
        t = targetOf(arg0, dflt);
      } else {
        var opts = arg0 || {};
        t = { host: opts.hostname || opts.host || 'localhost',
              port: String(opts.port || dflt) };
      }
      // http.request(url, options) — options win over the url
      var o2 = arguments[1];
      if (t && o2 && typeof o2 === 'object' && typeof o2 !== 'function') {
        if (o2.hostname || o2.host) t.host = o2.hostname || o2.host;
        if (o2.port) t.port = String(o2.port);
      }
      if (t) {
        var msg = verdict(t.host, t.port);
        if (msg) throw refusal(name + '.request', msg);
      }
      return realRequest.apply(mod, arguments);
    };
    mod.request = patchedRequest;
    // http.get()/https.get() build their own ClientRequest internally rather
    // than calling the exported `request` through `mod.request`, so route it
    // back through the patched version explicitly.
    mod.get = function () {
      var req = patchedRequest.apply(mod, arguments);
      req.end();
      return req;
    };
  });

  if (typeof globalThis.fetch === 'function') {
    var realFetch = globalThis.fetch;
    globalThis.fetch = function (input) {
      var t = targetOf(input, '80');
      if (t) {
        var msg = verdict(t.host, t.port);
        if (msg) return Promise.reject(refusal('fetch', msg));
      }
      return realFetch.apply(globalThis, arguments);
    };
  }

  globalThis.__HERMES_OFFLINE_GUARD__ = true;
  globalThis.__HERMES_OFFLINE_VERDICT__ = verdict;   // for tools/tests/unit/t_c81_offline_guard.py
}
