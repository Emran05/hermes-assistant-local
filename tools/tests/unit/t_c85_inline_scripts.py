#!/usr/bin/env python3
"""Regression test for C85 (docs/audits/2026-10-21-codebase-audit.md).

CI ran `node --check` on every dashboard/*.js file but never parsed
index.html's own inline <script> — the ~140 KB app shell — so a syntax error
there (which blanks the whole UI) passed every check.  The unit tier did not
look at it either.

tools/tests/lib/check_inline_scripts.py now extracts each inline script and
runs `node --check` on it; ci.yml calls it, and this suite runs it against the
REAL index.html (so the unit tier — and therefore CI on macOS and release.sh's
preflight — covers the shell too) and against fixtures that prove it can fail:
a broken script fails and the reported line is the HTML line, external and
data scripts are left alone, a module is checked as a module.
"""
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
TOOL = os.path.join(REPO, "tools", "tests", "lib", "check_inline_scripts.py")
INDEX = os.path.join(REPO, "dashboard", "index.html")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-800:].replace("\n", "\n    | ")))


def run(*files):
    p = subprocess.run([sys.executable, TOOL] + list(files), capture_output=True,
                       text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


TMP = tempfile.mkdtemp(prefix="t-c85-")
import atexit, shutil  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)   # leave no temp dir behind


def fixture(name, text):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


print("\n1. the real dashboard/index.html")
rc, out = run(INDEX)
check("every inline script in index.html parses", rc == 0, out)
check("the big app-shell script was actually checked (not skipped)",
      any(ln.startswith("ok") and "index.html:" in ln and int(ln.split("(")[1].split()[0]) > 50000
          for ln in out.splitlines() if ln.startswith("ok")), out)
rc, out = run()
check("with no arguments it checks every tracked *.html", rc == 0
      and "index.html" in out and "inline script(s) checked, 0 failed" in out, out)

print("\n2. it can fail, and points at the right line")
bad = fixture("bad.html", "<!doctype html>\n<html><head>\n<title>x</title>\n"
              "<script>\nconst ok = 1;\nconst broken = ;\nconst after = 2;\n</script>\n"
              "</head></html>\n")
rc, out = run(bad)
check("a syntax error in an inline script fails", rc == 1, out)
check("the failure names the <script> tag's line", "bad.html:4" in out, out)
check("node's own message carries the HTML line of the error (6)",
      bad + ":6" in out and "inline-script-" not in out, out)

print("\n3. what it leaves alone")
mixed = fixture("mixed.html",
                "<html><body>\n"
                "<script src=\"/does-not-exist.js\"></script>\n"
                "<script type=\"application/json\">{\"not\": js at all</script>\n"
                "<script type=\"text/template\"><div>{{ x }}</div></script>\n"
                "<script>var s = \"a </scr\" + \"ipt> inside a string\"; var t = 1 < 2;</script>\n"
                "<script type=\"module\">import { a } from \"./a.mjs\"; export const b = a;</script>\n"
                "<script>\n\n</script>\n"
                "</body></html>\n")
rc, out = run(mixed)
check("external, data and empty scripts are not parsed; JS ones pass", rc == 0, out)
check("exactly the two JavaScript bodies were checked",
      "2 inline script(s) checked, 0 failed" in out, out)
check("data blocks are reported as skipped, by type",
      "application/json" in out and "text/template" in out, out)
mod_bad = fixture("modbad.html", "<script type=\"module\">export default {;</script>\n")
rc, out = run(mod_bad)
check("a broken module script fails too", rc == 1, out)
rc, out = run(os.path.join(TMP, "missing.html"))
check("a missing file is an error, not a pass", rc != 0, out)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
