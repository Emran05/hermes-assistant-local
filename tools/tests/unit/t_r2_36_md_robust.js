// R2-36 — the shared Markdown renderer (dashboard/aux_md.js) on input real
// model text produces:
//   * [x](https://en.wikipedia.org/wiki/Foo_(bar)) cut the href at the first
//     ")" and left a stray ")" in the prose;
//   * [`c`](https://x.y) left a raw "\u00000\u0000" placeholder as the link
//     text, because the stash restore was not recursive (the code span was
//     stashed INSIDE the link's stashed html);
//   * 8000 nested ">" overflowed the stack and THREW out of render(), and
//     loadHistory renders a whole conversation in one loop, so one message
//     ended the loop and the rest of the history never drew.
// Runs the REAL module; no DOM needed.
"use strict";
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const md = require(path.join(REPO, "dashboard", "aux_md.js"));

let fails = 0, checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails++;
}
function render(src) {
  try { return { out: md.render(src) }; }
  catch (e) { return { err: e && e.constructor && e.constructor.name + ": " + e.message }; }
}
const A = (href, text) => '<a href="' + href + '" target="_blank" rel="noopener">' + (text || href) + "</a>";

console.log("R2-36: balanced-paren link URLs");
{
  const r = render("[x](https://en.wikipedia.org/wiki/Foo_(bar))");
  check("a Markdown link keeps its balanced (…) group in the href",
    r.out === "<p>" + A("https://en.wikipedia.org/wiki/Foo_(bar)", "x") + "</p>", r);
  const b = render("see https://en.wikipedia.org/wiki/Foo_(bar) ok");
  check("a bare URL keeps its balanced (…) group",
    b.out === "<p>see " + A("https://en.wikipedia.org/wiki/Foo_(bar)") + " ok</p>", b);
  const p = render("(see https://x.y/a)");
  check("a bare URL wrapped in parentheses leaves the closing paren as prose",
    p.out === "<p>(see " + A("https://x.y/a") + ")</p>", p);
  const q = render("Docs: https://x.y/Foo_(bar)).");
  check("an unbalanced trailing ) and the full stop stay outside the href",
    q.out === "<p>Docs: " + A("https://x.y/Foo_(bar)") + ").</p>", q);
  const u = render("[a](https://b.c/(d)");
  check("an unbalanced Markdown link URL still ends at its first ) (old behaviour)",
    u.out === "<p>" + A("https://b.c/(d", "a") + "</p>", u);
  const j = render("[x](javascript:alert(1)) and [y](https://ok.z/(q))");
  check("the balanced form does not widen the scheme rule (no javascript: link)",
    j.out && j.out.indexOf('href="javascript') < 0 && j.out.indexOf(A("https://ok.z/(q)", "y")) >= 0, j);
}

console.log("R2-36: placeholders never leak");
{
  const r = render("[`c`](https://x.y)");
  check("a code span as link text is restored inside the anchor",
    r.out === "<p>" + A("https://x.y", "<code>c</code>") + "</p>", r);
  check("no NUL placeholder survives", r.out && r.out.indexOf("\u0000") < 0, r);
  const two = render("[`a` and `b`](https://x.y/p) then `c`");
  check("several nested fragments all restore",
    two.out === "<p>" + A("https://x.y/p", "<code>a</code> and <code>b</code>") + " then <code>c</code></p>", two);
  const forged = render("x \u00000\u0000 y `c`");
  check("a NUL token in the source cannot pull a stashed fragment out of order",
    forged.out === "<p>x 0 y <code>c</code></p>", forged);
}

console.log("R2-36: deep blockquotes cannot throw");
{
  const r = render(">".repeat(8000) + " hi");
  check("8000 nested > renders instead of throwing", typeof r.out === "string", r.err);
  if (r.out) {
    const depth = (r.out.match(/<blockquote>/g) || []).length;
    check("nesting is capped at 50 levels", depth === 50, depth);
    check("the text past the cap is kept (escaped)", r.out.indexOf("&gt;&gt; hi") >= 0, r.out.slice(-80));
  }
  const ok = render("> a\n> > b\n> > > c");
  check("ordinary nesting is unchanged",
    ok.out === "<blockquote><p>a</p><blockquote><p>b</p><blockquote><p>c</p></blockquote></blockquote></blockquote>", ok);
}

console.log("R2-36: render() degrades instead of throwing");
{
  // Force a failure deep inside the block renderer for one marked input and
  // check render() hands back the escaped text, the same fallback index.html's
  // renderMd uses when aux_md.js is missing.
  const realSplit = String.prototype.split;
  String.prototype.split = function () {
    if (String(this).indexOf("BOOM-R236") >= 0) throw new RangeError("Maximum call stack size exceeded");
    return realSplit.apply(this, arguments);
  };
  let r;
  try { r = render("BOOM-R236 <b>x</b>\nline2"); }
  finally { String.prototype.split = realSplit; }
  check("a renderer failure is caught inside render()", typeof r.out === "string", r.err);
  check("the fallback is the escaped text with line breaks",
    r.out === "<p>BOOM-R236 &lt;b&gt;x&lt;/b&gt;<br>line2</p>", r.out);
  const next = render("**still** works");
  check("the next message renders normally", next.out === "<p><strong>still</strong> works</p>", next);
}

console.log(`TESTS ${checks - fails} passed ${fails} failed`);
process.exit(fails ? 1 : 0);
