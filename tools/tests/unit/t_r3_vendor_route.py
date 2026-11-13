#!/usr/bin/env python3
"""R3 C1 (docs/plans/motion-system-2026-10.md §5): how server.py serves the
vendored animation libraries.

  * every VENDOR_JS path answers 200, application/javascript (utf-8), the
    immutable cache policy, a content-hash ETag, and EXACTLY the file's bytes;
  * /motion.min.js is an alias of the vendored Motion 14 file (no-cache +
    the same ETag, a matching If-None-Match is a 304) — it never reads a
    dashboard/motion.min.js again, even if one reappears;
  * the /vendor/ prefix is an exact allowlist: `..`, `%2e%2e`, `.map`,
    LICENSE.md, MANIFEST.json, case and slash variants are all 404, and never
    fall through to an API route registered under /vendor/;
  * the same-origin guard still runs first (bad Host / cross Origin -> 403);
  * /expand.js and /aux_*.js are served as before (no-store, no ETag).

The REAL Handler and the real vendor tables are ast-extracted from
dashboard/server.py (tools/tests/unit/_http_harness.py) and run on an
ephemeral loopback port; HERE points at the real dashboard/ for the byte
checks and at a temp copy for the decoy / missing-file cases.
"""
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check
DASH = H.REPO / "dashboard"
VENDOR_NAMES = ["VENDOR_JS", "VENDOR_ALIAS", "VENDOR_CACHE", "VENDOR_ALIAS_CACHE",
                "_vendor_file", "_etag_matches"]


def get(port, path, headers=None):
    return H.req(port, "GET", path, None, headers=headers)


def one(hdrs, k):
    v = hdrs.get(k.lower()) or []
    return v[0] if len(v) == 1 else (None if not v else v)


def etag_of(data):
    return '"%s"' % hashlib.sha256(data).hexdigest()[:32]


def main():
    print("R3 C1: /vendor/* and the /motion.min.js alias")
    with tempfile.TemporaryDirectory(prefix="hermes-r3route-") as d:
        ns = H.http_namespace(d)
        H.extract(VENDOR_NAMES, ns)
        vj, va = ns["VENDOR_JS"], ns["VENDOR_ALIAS"]

        # pure helpers
        vf, em = ns["_vendor_file"], ns["_etag_matches"]
        check("_vendor_file maps each allowlisted path to (file, immutable)",
              all(vf(p) == (vj[p], "public, max-age=31536000, immutable") for p in vj))
        check("_vendor_file maps the alias to (Motion 14, no-cache)",
              vf("/motion.min.js") == ("vendor/motion/motion-14.6.0.min.js", "no-cache"))
        check("_vendor_file refuses everything else (exact lookup, no normalising)",
              all(vf(p) is None for p in ("/vendor/", "/vendor/x.js", "/vendor/motion/../motion/motion-14.6.0.min.js",
                                          "/vendor/gsap/LICENSE.md", "vendor/gsap/gsap-3.15.0.min.js",
                                          "/VENDOR/gsap/gsap-3.15.0.min.js", "/motion.min.js/", "")))
        check("_etag_matches: exact, weak, list and * forms; absent/other never match",
              em('"a"', '"a"') and em('W/"a"', '"a"') and em('"x", "a"', '"a"') and em("*", '"a"')
              and not em(None, '"a"') and not em("", '"a"') and not em('"b"', '"a"')
              and not em('a', '"a"'))

        # a GET route registered under /vendor/ must never be reached
        reached = []
        ns["register_get"]("/vendor/x.js", lambda ctx: reached.append(1) or {"ok": True})

        ns["HERE"] = str(DASH)
        srv = H.Server(ns["Handler"])
        try:
            for path, rel in sorted(vj.items()):
                data = (DASH / rel).read_bytes()
                st, hdrs, body = get(srv.port, path)
                name = path.rsplit("/", 1)[1]
                check("%s: 200 with exactly the vendored bytes" % name,
                      st == 200 and body == data, (st, len(body), len(data)))
                check("%s: application/javascript; charset=utf-8, Content-Length" % name,
                      one(hdrs, "content-type") == "application/javascript; charset=utf-8"
                      and one(hdrs, "content-length") == str(len(data)), hdrs)
                check("%s: Cache-Control public, max-age=31536000, immutable" % name,
                      one(hdrs, "cache-control") == "public, max-age=31536000, immutable", hdrs)
                check("%s: a content-hash ETag" % name, one(hdrs, "etag") == etag_of(data), hdrs)
                check("%s: still framed-deny, never CORS" % name,
                      one(hdrs, "x-frame-options") == "DENY"
                      and not any(k.startswith("access-control-") for k in hdrs), hdrs)
                st, hdrs, body = get(srv.port, path, {"If-None-Match": etag_of(data)})
                check("%s: a matching If-None-Match is a 304 with no body" % name,
                      st == 304 and body == b"" and one(hdrs, "etag") == etag_of(data), (st, hdrs))

            motion = (DASH / va["/motion.min.js"]).read_bytes()
            st, hdrs, body = get(srv.port, "/motion.min.js")
            check("/motion.min.js: 200 with the vendored Motion 14 bytes",
                  st == 200 and body == motion, (st, len(body)))
            check("/motion.min.js: it is Motion 14, not the old jsdelivr 11.11.13 copy",
                  b"motion@11" not in body and b"jsdelivr" not in body
                  and hashlib.sha256(body).hexdigest()
                  == "5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf")
            check("/motion.min.js: Cache-Control no-cache (revalidated) + the same ETag",
                  one(hdrs, "cache-control") == "no-cache" and one(hdrs, "etag") == etag_of(motion), hdrs)
            st, hdrs, body = get(srv.port, "/motion.min.js", {"If-None-Match": 'W/' + etag_of(motion)})
            check("/motion.min.js: revalidation with its ETag is a 304", st == 304 and body == b"", st)
            st, hdrs, body = get(srv.port, "/motion.min.js", {"If-None-Match": '"stale"'})
            check("/motion.min.js: a stale ETag gets the full file", st == 200 and body == motion, st)
            st, _, body = get(srv.port, "/motion.min.js?v=14")
            check("/motion.min.js?query: the query string is ignored", st == 200 and body == motion, st)

            refused = [
                "/vendor/gsap/../../server.py", "/vendor/../server.py",
                "/vendor/gsap/../gsap/gsap-3.15.0.min.js",
                "/vendor/%2e%2e/server.py", "/vendor/gsap/%2e%2e/%2e%2e/server.py",
                "/vendor/%2E%2E/%2E%2E/dashboard/server.py", "/vendor/..%2fserver.py",
                "/vendor/motion%2fmotion-14.6.0.min.js",
                "/vendor/gsap/gsap-3.15.0.min.js.map", "/vendor/gsap/LICENSE.md",
                "/vendor/motion/LICENSE.md", "/vendor/MANIFEST.json", "/vendor/README.md",
                "/vendor/x.js", "/vendor/", "/vendor/motion/", "/vendor//motion/motion-14.6.0.min.js",
                "/vendor/motion/motion-14.6.0.min.js/", "/vendor/motion/./motion-14.6.0.min.js",
                "/VENDOR/motion/motion-14.6.0.min.js", "/vendor/motion/motion-14.6.0.MIN.js",
                "/vendor/motion/motion-14.6.0.min.js%00", "/vendor/motion/motion-14.6.0.min.js%20",
                "/vendor/motion/motion-11.11.13.min.js", "/vendor\\motion\\motion-14.6.0.min.js",
                "/motion.min.js/", "/motion.min.js.map", "/vendor/motion/motion.min.js",
                "/vendor", "/vendor/gsap/;/../../server.py",
            ]
            bad = []
            for p in refused:
                st, _, body = get(srv.port, p)
                if st != 404 or b"def do_GET" in body or b"!function" in body:
                    bad.append((p, st, len(body)))
            check("%d traversal / non-allowlisted paths are all 404 with no file body"
                  % len(refused), not bad, bad)
            check("a GET route registered under /vendor/ is never reached", not reached, reached)
            # urlparse() drops ;params from the last segment (every route has
            # always behaved so); it can still only ever yield an allowlisted file
            st, _, body = get(srv.port, "/vendor/anime/anime-4.5.0.umd.min.js;x")
            check("a ;params suffix resolves to the same allowlisted file, nothing else",
                  st == 200 and body == (DASH / vj["/vendor/anime/anime-4.5.0.umd.min.js"]).read_bytes(), st)

            # the table serves GET and nothing else: no other verb on an
            # allowlisted path returns the file (or a 304 that implies it)
            leaked = []
            for verb in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                for p in ("/vendor/gsap/gsap-3.15.0.min.js", "/motion.min.js"):
                    st, _, body = H.req(srv.port, verb, p, b"{}", {"Content-Type": "application/json"})
                    if st in (200, 304) or b"!function" in body or b"GreenSock" in body:
                        leaked.append((verb, p, st, len(body)))
            check("no verb but GET returns a vendored file (POST/PUT/DELETE/PATCH/OPTIONS/HEAD)",
                  not leaked, leaked)

            some = sorted(vj)[0]
            st, _, body = get(srv.port, some, {"Host": "evil.example"})
            check("the guard runs first: bad Host on /vendor/* is 403",
                  st == 403 and b"forbidden host" in body, (st, body[:80]))
            st, _, _ = get(srv.port, "/motion.min.js", {"Host": "rebind.example:7788"})
            check("...and on the /motion.min.js alias", st == 403, st)
            st, _, _ = get(srv.port, some, {"Origin": "http://evil.example"})
            check("a cross-origin Origin on /vendor/* is 403", st == 403, st)
            st, _, body = get(srv.port, some, {"Origin": "http://127.0.0.1:7788"})
            check("the hub's own Origin is served", st == 200 and body, st)
            check("no request raised inside the handler", not srv.errors, srv.errors[:1])
        finally:
            srv.close()

        # a temp HERE: a decoy dashboard/motion.min.js, a missing vendor file,
        # and the unchanged /expand.js + /aux_*.js rule
        here = os.path.join(d, "here2")
        os.makedirs(here)
        shutil.copytree(DASH / "vendor", os.path.join(here, "vendor"))
        os.remove(os.path.join(here, "vendor", "anime", "anime-4.5.0.umd.min.js"))
        with open(os.path.join(here, "motion.min.js"), "w") as f:
            f.write("/* DECOY motion@11.11.13 */\n")
        with open(os.path.join(here, "aux_t.js"), "w") as f:
            f.write("void 'aux';\n")
        with open(os.path.join(here, "expand.js"), "w") as f:
            f.write("void 'expand';\n")
        with open(os.path.join(here, "index.html"), "w") as f:
            f.write("<!doctype html><title>t</title>")
        ns["HERE"] = here
        srv = H.Server(ns["Handler"])
        try:
            st, _, body = get(srv.port, "/motion.min.js")
            check("a stray dashboard/motion.min.js is never served: the alias is the vendored file",
                  st == 200 and b"DECOY" not in body and body == motion, (st, body[:60]))
            st, _, body = get(srv.port, "/vendor/anime/anime-4.5.0.umd.min.js")
            check("an allowlisted file that is missing on disk is a plain 404",
                  st == 404 and b"Traceback" not in body, st)
            for p, want in (("/aux_t.js", b"void 'aux';\n"), ("/expand.js", b"void 'expand';\n")):
                st, hdrs, body = get(srv.port, p)
                check("%s still served as before: application/javascript, no-store, no ETag" % p,
                      st == 200 and body == want
                      and one(hdrs, "content-type") == "application/javascript"
                      and one(hdrs, "cache-control") == "no-store" and "etag" not in hdrs, (st, hdrs))
            st, _, _ = get(srv.port, "/aux_x/../vendor/gsap/gsap-3.15.0.min.js")
            check("an aux_* path with a slash is still refused", st == 404, st)
            check("no request raised inside the handler (temp HERE)", not srv.errors, srv.errors[:1])
        finally:
            srv.close()

        # the old harness contract: a namespace WITHOUT the vendor tables (what
        # every other _http_harness suite builds) still serves its routes —
        # do_GET only touches VENDOR_* for /vendor/ and /motion.min.js
        ns2 = H.http_namespace(os.path.join(d, "bare"))
        ns2["register_get"]("/t/json", lambda ctx: {"ok": True})
        srv = H.Server(ns2["Handler"])
        try:
            st, _, body = get(srv.port, "/t/json")
            check("a request for any other path never needs the vendor tables",
                  st == 200 and H.as_json(body) == {"ok": True}, (st, body[:80]))
        finally:
            srv.close()
    H.finish()


if __name__ == "__main__":
    main()
