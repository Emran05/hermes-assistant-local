'use strict';
// The REAL top-level helpers of dashboard/index.html's inline <script>, for a
// Node unit suite that renders an aux module (C84).
//
// Two suites used to give the module under test a hand-written `esc()`: one
// escaped single quotes (the real one does not), the other escaped nothing at
// all — so neither could see what the shipped escaping really does. This pulls
// the named `function <name>(...)` declaration out of the inline script by
// text and evaluates it on its own:
//
//   const { indexFunction } = require('<repo>/tools/tests/lib/index_helpers.js');
//   globalThis.esc = indexFunction('esc');
//
// Only for small, self-contained helpers (no free variables but builtins): the
// declaration is found at the start of a line and taken up to the line where
// its braces balance — braces inside strings or regexes are not special-cased,
// so a helper whose literals hold unbalanced braces must not be loaded this way
// (the extraction then throws rather than returning something half-cut).
const fs = require('fs');
const path = require('path');

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, '..', '..', '..');
const INDEX = path.join(REPO, 'dashboard', 'index.html');

function indexFunction(name) {
  const lines = fs.readFileSync(INDEX, 'utf8').split('\n');
  const start = lines.findIndex((ln) => ln.startsWith('function ' + name + '('));
  if (start < 0) throw new Error('index.html has no top-level function ' + name + '()');
  let depth = 0, seen = false;
  const out = [];
  for (let i = start; i < lines.length; i++) {
    out.push(lines[i]);
    for (const ch of lines[i]) {
      if (ch === '{') { depth++; seen = true; } else if (ch === '}') depth--;
    }
    if (seen && depth === 0) {
      // eslint-disable-next-line no-new-func
      return new Function(out.join('\n') + '\nreturn ' + name + ';')();
    }
    if (depth < 0) break;
  }
  throw new Error('could not cut function ' + name + '() out of index.html');
}

module.exports = { indexFunction, INDEX };
