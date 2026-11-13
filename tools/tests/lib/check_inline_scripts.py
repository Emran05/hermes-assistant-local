#!/usr/bin/env python3
"""Parse every INLINE <script> in an HTML file with `node --check`.

    python3 tools/tests/lib/check_inline_scripts.py [file.html ...]
    (default: every git-tracked *.html in the repo)

C85: CI ran `node --check` on every dashboard/*.js file but never on
index.html's own inline script (~140 KB, the whole app shell), so a syntax
error there — which blanks the UI — passed CI.  This extracts each <script>
element that has no `src` and is JavaScript (no type, a JS MIME type, or
type="module", checked as an ES module), writes it to a temp file padded with
blank lines so node's error line number IS the line in the HTML file, and runs
`node --check` on it.  Data blocks (application/json, importmap, text/template
…) are skipped.  Stdlib only.

Exit 0 when every inline script parses, 1 otherwise (or when there is no
HTML file to check); one line per script either way.
"""
import html.parser
import os
import subprocess
import sys
import tempfile

JS_TYPES = {"", "text/javascript", "application/javascript", "module",
            "text/ecmascript", "application/ecmascript"}


class _Scripts(html.parser.HTMLParser):
    """Collect (start_line, type, body) for inline scripts.

    html.parser hands a <script> body to handle_data verbatim (CDATA content
    model), so the body is exactly what the browser would run."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.found = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        self._cur = {"line": self.getpos()[0], "src": "src" in a,
                     "type": a.get("type", "").strip().lower(), "body": []}

    def handle_data(self, data):
        if self._cur is not None:
            if not self._cur["body"]:
                # the body starts on the line the parser is on now
                self._cur["body_line"] = self.getpos()[0]
            self._cur["body"].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._cur is not None:
            self.found.append(self._cur)
            self._cur = None


def scripts(text):
    p = _Scripts()
    p.feed(text)
    p.close()
    return p.found


def check_file(path, node="node"):
    """-> (n_checked, failures[list of str], lines[list of str])"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    found = scripts(text)
    n, fails, out = 0, [], []
    for s in found:
        if s["src"]:
            continue
        if s["type"] not in JS_TYPES:
            out.append("skip  %s:%d  <script type=%r> (not JavaScript)"
                       % (path, s["line"], s["type"]))
            continue
        body = "".join(s["body"])
        if not body.strip():
            continue
        n += 1
        pad = "\n" * (s.get("body_line", s["line"]) - 1)
        ext = ".mjs" if s["type"] == "module" else ".js"
        fd, tmp = tempfile.mkstemp(prefix="inline-script-", suffix=ext)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(pad + body)
            r = subprocess.run([node, "--check", tmp], capture_output=True,
                               text=True, timeout=120)
        finally:
            os.unlink(tmp)
        where = "%s:%d" % (path, s["line"])
        if r.returncode == 0:
            out.append("ok    %s  inline <script> (%d bytes)" % (where, len(body)))
        else:
            msg = (r.stderr or r.stdout).replace(os.path.realpath(tmp), path)
            msg = msg.replace(tmp, path).strip()
            fails.append(where)
            out.append("FAIL  %s  inline <script> does not parse:\n%s" % (where, msg))
    return n, fails, out


def tracked_html(root):
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "*.html"],
                           capture_output=True, text=True, timeout=30)
        return [os.path.join(root, p) for p in r.stdout.split() if p]
    except Exception:
        return []


def main(argv):
    files = argv[1:]
    if not files:
        root = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        files = tracked_html(root)
    if not files:
        print("no HTML files to check", file=sys.stderr)
        return 1
    total, bad = 0, []
    for path in files:
        n, fails, out = check_file(path)
        for ln in out:
            print(ln)
        total += n
        bad += fails
    print("%d inline script(s) checked, %d failed" % (total, len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
