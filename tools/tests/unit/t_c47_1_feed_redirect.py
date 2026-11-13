#!/usr/bin/env python3
"""C47.1 — a feed redirect to http:// is refused BEFORE it is followed.

BEFORE: _intel_fetch_feed used urlopen(context=...), whose stock
HTTPRedirectHandler follows an https -> http Location: the cleartext request
went out and only the response was dropped (by a geturl() check).
AFTER: the fetcher's own opener carries _WTHttpsOnlyRedirect, which raises on
any non-https Location before the next request is built.

Real urllib against two loopback HTTP servers (the offline guard allows
127.0.0.1): server A answers 302 -> http://127.0.0.1:<B>/landed, server B
counts every request it receives. A count of zero on B is the proof that the
cleartext hop was never sent. Drives the real module (see _wt_harness.py).
"""
import http.server
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
RSS = (b'<?xml version="1.0"?><rss><channel><item><title>Hello</title>'
       b'<link>https://example.org/a</link></item></channel></rss>')


def serve(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    print("C47.1: redirects off https are refused before they are followed")
    g, _home, _data = H.load()
    hits = {"B": 0}

    class B(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits["B"] += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(RSS)

        def log_message(self, *a):
            pass
    b = serve(B)
    landed = "http://127.0.0.1:%d/landed" % b.server_address[1]

    class A(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", landed)
            self.end_headers()

        def log_message(self, *a):
            pass
    a = serve(A)
    start = "http://127.0.0.1:%d/feed" % a.server_address[1]

    # control: the stdlib's stock redirect handler DOES send the cleartext hop
    try:
        urllib.request.build_opener().open(start, timeout=5).read()
    except Exception as e:
        check("control: stock opener reached B", False, repr(e))
    check("control: the stock redirect handler sends the http:// hop",
          hits["B"] == 1, hits)

    mk = g.get("_intel_opener")
    check("the feed fetcher has its own opener", callable(mk), "no _intel_opener")
    err = None
    if callable(mk):
        hits["B"] = 0
        try:
            mk().open(start, timeout=5).read()
        except urllib.error.HTTPError as e:
            err = e
        except Exception as e:                       # pragma: no cover
            err = e
    check("THE FIX: the http:// Location is never requested", hits["B"] == 0, hits)
    check("... the open fails with an HTTPError instead",
          isinstance(err, urllib.error.HTTPError) and "non-https" in str(err), repr(err))

    # the refusal is a pure scheme check: an https Location is still followed
    cls = g.get("_WTHttpsOnlyRedirect")
    if cls is None:
        check("an https redirect is still followed", False, "no _WTHttpsOnlyRedirect")
    else:
        req = urllib.request.Request("https://example.org/feed")
        nxt = cls().redirect_request(req, None, 301, "Moved", {},
                                     "https://feeds.example.org/rss")
        check("an https redirect is still followed",
              nxt is not None and nxt.full_url == "https://feeds.example.org/rss", nxt)
        for bad in ("http://example.org/x", "HTTP://example.org/x", "ftp://x/y", "file:///etc/hosts"):
            try:
                cls().redirect_request(req, None, 302, "Found", {}, bad)
                ok = False
            except urllib.error.HTTPError:
                ok = True
            check("redirect to %s is refused" % bad.split(":")[0], ok)

    # wiring: _intel_fetch_feed really fetches through that opener
    made = []
    if callable(mk):
        def spy():
            op = mk()
            made.append(op)
            return op
        g["_intel_opener"] = spy
    with H.patch_urlopen(lambda req: H.FakeResp(RSS, req.full_url)) as net:
        out = g["_intel_fetch_feed"]("https://example.org/feed", "X", "News")
    check("_intel_fetch_feed opens the feed through _intel_opener()",
          len(made) == 1 and net.calls and net.calls[0][0] == "https://example.org/feed",
          (made, net.calls))
    check("... whose redirect handler is the https-only one (the stock one is gone)",
          made and any(type(h).__name__ == "_WTHttpsOnlyRedirect" for h in made[0].handlers)
          and not any(type(h) is urllib.request.HTTPRedirectHandler for h in made[0].handlers),
          made and [type(h).__name__ for h in made[0].handlers])
    check("an https feed still parses", len(out) == 1 and out[0]["title"] == "Hello", out)
    a.shutdown()
    b.shutdown()
    H.finish()


if __name__ == "__main__":
    main()
