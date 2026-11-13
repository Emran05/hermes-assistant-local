#!/usr/bin/env python3
"""B18 (server half) — only http(s) links leave the providers, and a Recent
Activity file is REVEALED in Finder, never opened.

A widget link ends in window.open, which the app shell hands to
NSWorkspace.open: that opens ANY scheme with its default handler, so a feed
item whose <link> is file:///…/x.command (or an x-apple…:/custom app URL) ran
on one click, and Recent Activity rows were file:// URLs on purpose.

Runs the REAL code: _http_url/w_rss/w_quicklinks/_cached are pulled out of
server.py by AST, and the whole of expanders_extra.py is exec'd with server.py's
globals stubbed (subprocess replaced, no network, no dashboard)."""
import ast, atexit, io, os, re, shutil, subprocess, sys, tempfile, time, types, urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "server.py")
EXTRA = os.path.join(REPO, "dashboard", "expanders_extra.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def server_funcs(g, names):
    """exec the named top-level defs (and plain NAME = ... assignments) of
    server.py into g (those that exist)."""
    src = io.open(SERVER, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and node.name in names) or (
                isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in names for t in node.targets)):
            exec(compile(ast.Module([node], []), SERVER, "exec"), g)
    return g


FEED = """<?xml version="1.0"?><rss><channel><title>Feed</title>
<item><title>Good</title><link>https://example.com/a</link></item>
<item><title>Plain http</title><link>http://example.com/b</link></item>
<item><title>Runs a file</title><link>file:///Users/you/Downloads/run.command</link></item>
<item><title>Settings pane</title><link>x-apple.systempreferences:com.apple.preference</link></item>
<item><title>Script</title><link>javascript:alert(1)</link></item>
<item><title>No host</title><link>https:relative</link></item>
</channel></rss>"""


def base_globals(settings):
    g = {"__name__": "srv_b18", "time": time, "os": os, "re": re,
         "urllib": urllib, "subprocess": subprocess, "json": __import__("json"),
         "_widget_cache": {}, "get_settings": lambda: settings,
         "_http_text": lambda url, timeout=None: FEED}
    return server_funcs(g, {"_http_url", "w_rss", "w_quicklinks", "_cached", "_strip_html",
                            # _cached's last-good fallback + retry backoff (C12)
                            "_cache_failed", "_widget_cache_good", "_widget_cache_fails",
                            "_CACHE_RETRY_S", "_CACHE_STALE_X"})


def main():
    print("hub RSS card (w_rss)")
    g = base_globals({"rss_feeds": ["https://feeds.example.com/x.xml"]})
    items = g["w_rss"]()["items"]
    by = {i["title"]: i["url"] for i in items}
    check("all six items still listed", len(items) == 6, items)
    check("https link kept", by.get("Good") == "https://example.com/a", by)
    check("http link kept", by.get("Plain http") == "http://example.com/b", by)
    check("file: link blanked", by.get("Runs a file") == "", by)
    check("x-apple…: link blanked", by.get("Settings pane") == "", by)
    check("javascript: link blanked", by.get("Script") == "", by)
    check("an http(s) URL without a host blanked", by.get("No host") == "", by)

    print("hub Quick Links card (w_quicklinks)")
    g = base_globals({"quicklinks": [
        {"label": "GH", "url": "https://github.com"},
        {"label": "Docs", "url": "file:///Users/you/Documents"},
        {"label": "App", "url": "obsidian://open?vault=x"},
        {"label": "Local", "url": "http://localhost:3000"},
        "not a dict"]})
    links = g["w_quicklinks"]()["links"]
    check("only http(s) tiles reach the card",
          [l.get("label") if isinstance(l, dict) else l for l in links] == ["GH", "Local"], links)
    g = base_globals({})
    check("the defaults are untouched", len(g["w_quicklinks"]()["links"]) == 4)

    print("expanders_extra: News Desk feed parser + /api/files/reveal")
    root = tempfile.mkdtemp(prefix="b18-")
    atexit.register(shutil.rmtree, root, True)
    granted = os.path.join(root, "Projects")
    outside = os.path.join(root, "Elsewhere")
    os.makedirs(granted)
    os.makedirs(outside)
    target = os.path.join(granted, "run.command")
    io.open(target, "w").write("#!/bin/sh\necho hi\n")
    os.chmod(target, 0o755)
    secret = os.path.join(outside, "secret.command")
    io.open(secret, "w").write("x")
    link_out = os.path.join(granted, "sneaky.command")
    os.symlink(secret, link_out)

    spawned = []

    class FakeSub(types.ModuleType):
        SubprocessError = subprocess.SubprocessError
        TimeoutExpired = subprocess.TimeoutExpired

        def run(self, argv, **kw):
            spawned.append(list(argv))
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    routes = {}
    g = base_globals({})
    g.update({"EXPANDERS": {}, "HOME": root, "subprocess": FakeSub("subprocess"),
              "register_post": lambda p, f: routes.__setitem__(p, f),
              "register_get": lambda p, f: None,
              "get_access": lambda: {"dirs": [granted]}})
    exec(compile(io.open(EXTRA, encoding="utf-8").read(), EXTRA, "exec"), g)

    feed_items = g["_news_parse_feed"]("https://feeds.example.com/x.xml", "Ex", 10)
    titles = [i["title"] for i in feed_items]
    check("News Desk keeps the http(s) items", titles[:2] == ["Good", "Plain http"], titles)
    check("and drops every other scheme", all(i["url"].startswith(("http://", "https://")) for i in feed_items)
          and len(feed_items) == 2, feed_items)

    h = routes.get("/api/files/reveal")
    check("POST /api/files/reveal is registered", callable(h), sorted(routes))
    if not callable(h):
        return

    class Ctx:
        def __init__(self, body):
            self.body = body
            self.query = {}

    def call(body):
        r = h(Ctx(body))
        return r if isinstance(r, tuple) else (r, 200)

    out, st = call({"path": target})
    check("a file in a granted folder is revealed", st == 200 and out.get("ok") is True, (out, st))
    check("with open -R (select in Finder), never a plain open",
          spawned == [["/usr/bin/open", "-R", os.path.realpath(target)]], spawned)
    spawned.clear()
    out, st = call({"path": secret})
    check("outside every granted folder -> 403", st == 403 and not spawned, (out, st, spawned))
    out, st = call({"path": link_out})
    check("a symlink pointing out of the folder -> 403", st == 403 and not spawned, (out, st, spawned))
    out, st = call({"path": os.path.join(granted, "..", "Elsewhere", "secret.command")})
    check("a ../ escape -> 403", st == 403 and not spawned, (out, st))
    out, st = call({"path": os.path.join(granted, "gone.txt")})
    check("a missing file -> 404", st == 404 and not spawned, (out, st))
    for bad in ("run.command", "", None, 7, "/x\x00y", "/" + "a" * 5000):
        out, st = call({"path": bad})
        check("bad path %r -> 400" % (str(bad)[:20],), st == 400 and not spawned, (out, st))
    out, st = call([])
    check("a non-object body -> 400", st == 400 and not spawned, (out, st))
    out, st = call({"path": granted})
    check("the granted folder itself may be revealed", st == 200 and len(spawned) == 1, (out, st))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # a crash is a failure, never a silent pass
        import traceback
        traceback.print_exc()
        FAILS.append("crashed: %s" % e)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len([f for f in FAILS if not f.startswith("crashed")]),
                                          len(FAILS)))
    sys.exit(1 if FAILS else 0)
