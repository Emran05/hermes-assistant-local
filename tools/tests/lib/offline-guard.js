'use strict';
// Offline guard for the unit tier's Node suites — the JS sibling of
// tools/tests/lib/usercustomize.py.
//
// The Python half of the unit tier refuses TCP connects to the dashboard
// (7788), model server (8080) and serve backend (9119) ports for every
// python3 the tier starts, so a Python unit test that quietly depended on
// the owner's running Mac fails here instead of passing by accident. Node
// had no equivalent — a Node unit suite could still `fetch()` or
// `http.request()` straight into a live dashboard and nobody would notice
// until it broke on a machine with nothing running.
//
// Preloaded via `NODE_OPTIONS="--require .../offline-guard.js"` for the unit
// tier only (see run.sh do_unit). Stubs http.request/https.request/fetch so
// any attempt to reach a blocked port on localhost throws or rejects instead
// of connecting. Inert unless HERMES_TESTS_OFFLINE=1 is set, so requiring
// this file outside the unit tier (or outside run.sh entirely) is harmless.
if (process.env.HERMES_TESTS_OFFLINE === '1') {
  var BLOCKED = {};
  (process.env.HERMES_TESTS_BLOCKED_PORTS || '7788,8080,9119')
    .split(',')
    .map(function (s) { return s.trim(); })
    .filter(Boolean)
    .forEach(function (p) { BLOCKED[p] = true; });

  var LOCAL_HOSTS = { '127.0.0.1': true, 'localhost': true, '::1': true, '[::1]': true };

  function blockedTarget(host, port) {
    return !!LOCAL_HOSTS[String(host || '127.0.0.1')] && !!BLOCKED[String(port)];
  }

  function refuse(where, host, port) {
    var e = new Error('HERMES_TESTS_OFFLINE: ' + where + ' to ' + host + ':' +
      port + ' is out of bounds for the unit tier');
    e.code = 'ECONNREFUSED';
    throw e;
  }

  ['http', 'https'].forEach(function (name) {
    var mod;
    try { mod = require(name); } catch (e) { return; }
    var realRequest = mod.request;
    var patchedRequest = function (arg0, arg1) {
      var host, port;
      if (typeof arg0 === 'string') {
        var u = new URL(arg0);
        host = u.hostname;
        port = u.port || (name === 'https' ? '443' : '80');
      } else {
        var opts = arg0 || {};
        host = opts.hostname || opts.host || '127.0.0.1';
        port = String(opts.port || (name === 'https' ? 443 : 80));
      }
      if (blockedTarget(host, port)) refuse(name + '.request', host, port);
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
    globalThis.fetch = function (input, init) {
      var u;
      try {
        u = new URL(typeof input === 'string' ? input : input && input.url);
      } catch (e) {
        return realFetch.apply(globalThis, arguments);
      }
      var port = u.port || (u.protocol === 'https:' ? '443' : '80');
      if (blockedTarget(u.hostname, port)) {
        return Promise.reject(new Error('HERMES_TESTS_OFFLINE: fetch to ' +
          u.hostname + ':' + port + ' is out of bounds for the unit tier'));
      }
      return realFetch.apply(globalThis, arguments);
    };
  }

  globalThis.__HERMES_OFFLINE_GUARD__ = true;
}
