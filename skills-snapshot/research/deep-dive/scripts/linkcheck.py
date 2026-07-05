#!/usr/bin/env python3
"""linkcheck.py — verify every citation in a brief actually resolves.

Usage:
    python3 linkcheck.py URL [URL ...]        # check explicit URLs
    cat brief.md | python3 linkcheck.py       # extract + check all URLs in text

Output: one line per URL — "OK <status> <url>" or "DEAD <reason> <url>".
Exit codes: 0 = all alive, 2 = at least one dead, 1 = no URLs found.

Notes:
  * Uses HEAD first, falls back to GET (some servers 405/403 HEAD).
  * SSL: framework Python 3.12 ships without system root certs wired up, so
    this uses the certifi-bundle pattern (same as dashboard/server.py
    _ssl_context()) and degrades to the default context if certifi is absent.
  * Never follows more than the default redirect chain; 2xx/3xx = alive.
    405/403/429 on both HEAD and GET are reported alive-but-guarded (OK) —
    bot walls are not dead links.
"""

import re
import ssl
import sys
import urllib.request
import urllib.error

URL_RE = re.compile(r"https?://[^\s\)\]>\"'`]+")
TIMEOUT = 10
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) hermes-deep-dive-linkcheck/1.0"}


def _ssl_context():
    # python.org framework builds ship without system root certs wired up;
    # use certifi's bundle when available so HTTPS doesn't CERTIFICATE_VERIFY_FAILED.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_CTX = _ssl_context()
GUARDED = (403, 405, 429)  # bot walls / method blocks: page exists, robot refused


def probe(url, method):
    req = urllib.request.Request(url, headers=UA, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
        return r.status


def check(url):
    """Returns (alive: bool, detail: str). Tries HEAD, then GET."""
    last = "failed"
    for method in ("HEAD", "GET"):
        try:
            return True, str(probe(url, method))
        except urllib.error.HTTPError as e:
            last = e.code                     # keep the GET verdict if both fail
        except urllib.error.URLError as e:
            last = "unreachable:%s" % getattr(e, "reason", e)
        except Exception as e:  # noqa: BLE001 — report, never crash the sweep
            last = "error:%s" % type(e).__name__
    if isinstance(last, int) and last in GUARDED:
        return True, "%d-guarded" % last      # bot wall, not a dead link
    return False, str(last)


def main():
    urls = sys.argv[1:]
    if not urls:
        text = sys.stdin.read()
        # de-dup, keep order
        seen = set()
        urls = []
        for u in URL_RE.findall(text):
            u = u.rstrip(".,;")
            if u not in seen:
                seen.add(u)
                urls.append(u)
    if not urls:
        sys.stderr.write("no URLs found\n")
        return 1
    dead = 0
    for u in urls:
        alive, detail = check(u)
        print("%s %s %s" % ("OK  " if alive else "DEAD", detail, u))
        if not alive:
            dead += 1
    print("-- %d checked, %d dead" % (len(urls), dead))
    return 2 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
