'use strict';
// Is the unit tier's Node offline guard ACTIVE in this process?
//
// run.sh runs this with the tier's NODE_OPTIONS already exported, so it
// checks that the PRELOAD took effect — it deliberately does not require()
// the guard itself (C82: an explicit require succeeds even when NODE_OPTIONS
// was dropped or overridden, so it proved nothing).  Then it proves the guard
// behaviourally against a listener it opened itself, so a broken guard
// reaches that listener and never a service the owner is running.  Every
// call shape a suite might use is tried, fetch(new URL(...)) included.
//
// Exit 0 = guarded.  Any other exit prints why on stderr.
const net = require('net');
const http = require('http');

function bad(code, msg) {
  process.stderr.write('node offline guard: ' + msg + '\n');
  process.exit(code);
}

if (globalThis.__HERMES_OFFLINE_GUARD__ !== true) {
  bad(2, 'the preload did not run (NODE_OPTIONS dropped or overridden?)');
}

const srv = net.createServer((s) => s.destroy());
srv.listen(0, '127.0.0.1', async () => {
  const port = srv.address().port;
  process.env.HERMES_TESTS_BLOCKED_PORTS = String(port);
  const base = 'http://127.0.0.1:' + port + '/';
  const guarded = (e) => e && /HERMES_TESTS_OFFLINE/.test(String(e.message));
  const shapes = [
    ['fetch(string)', () => fetch(base)],
    ['fetch(new URL())', () => fetch(new URL(base))],
    ['fetch(new Request())', () => fetch(new Request(base))],
  ];
  for (const [name, fn] of shapes) {
    try {
      await fn();
      bad(3, name + ' reached a blocked port');
    } catch (e) {
      if (!guarded(e)) bad(4, name + ' failed, but not by the guard: ' + e.message);
    }
  }
  const sync = [
    ['http.get(new URL())', () => http.get(new URL(base))],
    ['http.request(options)', () => http.request({ host: '127.0.0.1', port: port })],
    ['net.connect(port, host)', () => net.connect(port, '127.0.0.1')],
    ['net.connect(non-loopback)', () => net.connect({ host: 'hermes-guard-selfcheck.invalid', port: 80 })],
  ];
  for (const [name, fn] of sync) {
    let threw = null;
    try {
      const r = fn();
      if (r && typeof r.on === 'function') r.on('error', () => {});
      if (r && typeof r.destroy === 'function') r.destroy();
    } catch (e) { threw = e; }
    if (!guarded(threw)) bad(5, name + ' was not refused by the guard');
  }
  srv.close();
  process.exit(0);
});
