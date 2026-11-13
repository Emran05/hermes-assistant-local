#!/usr/bin/env python3
"""R3 C1 (docs/plans/motion-system-2026-10.md §2, §4, §12): the vendored
animation libraries are exactly what their npm packages shipped, and the
provenance around them is complete and consistent.

Checks the REAL files: dashboard/vendor/** (the five library builds, their
license files, MANIFEST.json, README.md), the VENDOR_JS / VENDOR_ALIAS tables
in dashboard/server.py (ast-read, not imported), and tools/vendor-anim.sh —
whose --check mode is run as is, and whose copy mode is run end to end
against a fake node_modules built in a temp dir (never the owner's
~/node_modules, never npm, never the network).
"""
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("HERMES_REPO") or HERE.parent.parent.parent)
DASH = REPO / "dashboard"
VEND = DASH / "vendor"
SCRIPT = REPO / "tools" / "vendor-anim.sh"

FAILS, PASSES = [], [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


def sha(b):
    return hashlib.sha256(b).hexdigest()


# What was reviewed (docs/plans/motion-system-2026-10.md §4.1). The test pins
# these on its own, so an edit to PINS + MANIFEST + the files together is still
# caught here and has to be a deliberate, reviewed change of this table too.
EXPECT = {
    "/vendor/motion/motion-14.6.0.min.js": (
        "motion", "14.6.0", "motion/dist/motion.js", "Motion", 172451,
        "5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf"),
    "/vendor/gsap/gsap-3.15.0.min.js": (
        "gsap", "3.15.0", "gsap/dist/gsap.min.js", "gsap", 72927,
        "92bb9a96476f983d212a2bc4f54c889039c1696dd4461d40a736860938570fbb"),
    "/vendor/gsap/Flip-3.15.0.min.js": (
        "gsap", "3.15.0", "gsap/dist/Flip.min.js", "Flip", 25534,
        "cbe3ca726350f8d230da38a14ce2384e7772e05a45cee6144dd7fe6dde868c2f"),
    "/vendor/gsap/CustomEase-3.15.0.min.js": (
        "gsap", "3.15.0", "gsap/dist/CustomEase.min.js", "CustomEase", 7143,
        "466e426a5c60c21c94b15a30a3dffacac9bb39ce8f4e07d071d7d4bb1be43390"),
    "/vendor/anime/anime-4.5.0.umd.min.js": (
        "animejs", "4.5.0", "animejs/dist/bundles/anime.umd.min.js", "anime", 118043,
        "8d5b3a58a1f64023a04a4cedeef135a2263b18da04b35177ac13438a0bad033b"),
}
INTEGRITY = {   # npm tarball integrity, from the lockfile that installed them
    "motion": "sha512-qqaZbyubgk7x3zxjPgP1l3Br/KQXol/IIV1uqb358SoN4ApcqNuV7+wyDPg/XLErmuyQL5lL8JVI/fOmDsmBKQ==",
    "gsap": "sha512-dMW4CWBTUK1AEEDeZc1g4xpPGIrSf9fJF960qbTZmN/QwZIWY5wgliS6JWl9/25fpTGJrMRtSjGtOmPnfjZB+A==",
    "animejs": "sha512-NQimYX+lz8WaXonGS9zVVoviCIAjONeJayxacUditaivYLqyXgGjFKjuyl0aUUhFKm5MriX9ty1TGovNzoJQWA==",
}
GSAP_BANNER = (
    "/*!\n * %s 3.15.0\n * https://gsap.com\n * \n"
    " * @license Copyright 2026, GreenSock. All rights reserved.\n"
    " * Subject to the terms at https://gsap.com/standard-license.\n"
    " * @author: Jack Doyle, jack@greensock.com\n */\n")
ANIME_BANNER = ("/**\n * Anime.js - UMD minified bundle\n * @version v4.5.0\n"
                " * @license MIT\n * @copyright 2026 - Julian Garnier\n */\n")
HOME_RE = re.compile(rb"/(Users|home)/[A-Za-z0-9._-]+")
HOME_OK = re.compile(rb"/(Users|home)/(YOU|you|USER|username|me|<[^>]+>|\$[A-Za-z_{])")


def server_tables():
    tree = ast.parse((DASH / "server.py").read_text(), filename="server.py")
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("VENDOR_JS", "VENDOR_ALIAS",
                                                        "VENDOR_CACHE", "VENDOR_ALIAS_CACHE"):
                    out[t.id] = ast.literal_eval(node.value)
    return out


def script_table(name, width):
    txt = SCRIPT.read_text()
    m = re.search(r"^%s='\n(.*?)\n'$" % name, txt, re.S | re.M)
    rows = []
    for line in (m.group(1).splitlines() if m else []):
        if line.strip():
            rows.append(line.strip().split("|"))
    return [r for r in rows if len(r) == width], bool(m)


def run(args, **kw):
    return subprocess.run(["bash", str(SCRIPT)] + args, capture_output=True,
                          text=True, timeout=60, **kw)


def main():
    print("R3 C1: vendored animation libraries, manifest and provenance")

    # ---- MANIFEST.json ---------------------------------------------------
    try:
        man = json.loads((VEND / "MANIFEST.json").read_text())
    except (OSError, ValueError) as e:
        man = None
        check("MANIFEST.json parses", False, e)
    check("MANIFEST.json is a list with one entry per vendored file",
          isinstance(man, list) and len(man) == len(EXPECT), man and len(man))
    man = man if isinstance(man, list) else []
    by_path = {e.get("path"): e for e in man if isinstance(e, dict)}
    check("its paths are exactly the reviewed published paths",
          set(by_path) == set(EXPECT), sorted(by_path))
    keys = {"path", "file", "lib", "package", "version", "source", "global", "bytes",
            "sha256", "npm_integrity", "npm_resolved", "license", "license_file",
            "license_sha256"}
    for path, (pkg, ver, src, glob, nbytes, digest) in EXPECT.items():
        e = by_path.get(path) or {}
        name = path.rsplit("/", 1)[1]
        check("%s: manifest has every field" % name, keys <= set(e), sorted(keys - set(e)))
        check("%s: package/version/source/global are the reviewed ones" % name,
              (e.get("package"), e.get("version"), e.get("source"), e.get("global"))
              == (pkg, ver, src, glob), e)
        check("%s: file is the published path under dashboard/" % name,
              e.get("file") == path.lstrip("/"), e.get("file"))
        try:
            data = (DASH / path.lstrip("/")).read_bytes()
        except OSError as ex:
            data = b""
            check("%s: the file exists" % name, False, ex)
        check("%s: bytes and sha256 match the manifest AND the reviewed pin" % name,
              len(data) == nbytes == e.get("bytes") and sha(data) == digest == e.get("sha256"),
              (len(data), sha(data)))
        check("%s: npm integrity + resolved recorded" % name,
              e.get("npm_integrity") == INTEGRITY[pkg]
              and e.get("npm_resolved") == "https://registry.npmjs.org/%s/-/%s-%s.tgz" % (pkg, pkg, ver),
              (e.get("npm_integrity"), e.get("npm_resolved")))
        try:
            lb = (DASH / e.get("license_file", "")).read_bytes()
        except (OSError, TypeError) as ex:
            lb = b""
            check("%s: its license file exists" % name, False, ex)
        check("%s: license file sits next to it and matches its recorded sha256" % name,
              e.get("license_file") == os.path.dirname(path.lstrip("/")) + "/LICENSE.md"
              and lb and sha(lb) == e.get("license_sha256"), e.get("license_file"))
        if pkg == "gsap":
            check("%s: license is GSAP Standard, with its URL and effective date" % name,
                  e.get("license") == 'GSAP Standard "No Charge" License'
                  and e.get("license_url") == "https://gsap.com/standard-license"
                  and e.get("effective") == "2025-04-30", e)
        else:
            check("%s: license is MIT" % name, e.get("license") == "MIT", e.get("license"))

    # ---- banners, globals, hygiene ---------------------------------------
    files = {p: (DASH / p.lstrip("/")).read_bytes() if (DASH / p.lstrip("/")).is_file() else b""
             for p in EXPECT}
    for plugin in ("GSAP", "Flip", "CustomEase"):
        f = {"GSAP": "gsap", "Flip": "Flip", "CustomEase": "CustomEase"}[plugin]
        data = files["/vendor/gsap/%s-3.15.0.min.js" % f]
        check("%s: the GreenSock @license banner is byte-intact at the top" % plugin,
              data.startswith((GSAP_BANNER % plugin).encode()), data[:260])
    a = files["/vendor/anime/anime-4.5.0.umd.min.js"]
    check("anime: the MIT banner names v4.5.0", a.startswith(ANIME_BANNER.encode()), a[:160])
    m = files["/vendor/motion/motion-14.6.0.min.js"]
    check("motion: a UMD build that defines window.Motion (it carries no banner)",
          b".Motion={})" in m[:400] and m.startswith(b"!function(t,e){"), m[:200])
    check("anime: a UMD build that defines window.anime", b".anime={})" in a[:700])
    for p, data in files.items():
        name = p.rsplit("/", 1)[1]
        check("%s: no sourceMappingURL" % name, b"sourceMappingURL" not in data)
        hits = [x.group(0) for x in HOME_RE.finditer(data) if not HOME_OK.match(x.group(0))]
        check("%s: no personal home path" % name, not hits, hits[:3])
        check("%s: no CR line endings (byte-exact across checkouts)" % name, b"\r" not in data)

    node = shutil.which("node")
    if node:
        for p in EXPECT:
            r = subprocess.run([node, "--check", str(DASH / p.lstrip("/"))],
                               capture_output=True, text=True, timeout=60)
            check("%s: node --check" % p.rsplit("/", 1)[1], r.returncode == 0, r.stderr[-300:])
    else:
        print("  SKIP node --check (node is not on PATH)")

    # ---- the directory holds exactly this, nothing stale -----------------
    present = sorted(str(x.relative_to(VEND)) for x in VEND.rglob("*") if x.is_file())
    want = sorted([p[len("/vendor/"):] for p in EXPECT] + ["MANIFEST.json", "README.md",
                  "motion/LICENSE.md", "gsap/LICENSE.md", "anime/LICENSE.md"])
    check("dashboard/vendor holds exactly the vendored files (no .map, no stale version)",
          present == want, sorted(set(present) ^ set(want)))
    for rel in ("MANIFEST.json", "README.md", "motion/LICENSE.md", "gsap/LICENSE.md",
                "anime/LICENSE.md"):
        try:
            data = (VEND / rel).read_bytes()
        except OSError:
            continue
        hits = [x.group(0) for x in HOME_RE.finditer(data) if not HOME_OK.match(x.group(0))]
        check("%s: no personal home path" % rel, not hits, hits[:3])

    # ---- licenses -----------------------------------------------------------
    def txt(rel):
        try:
            return (VEND / rel).read_text()
        except OSError:
            return ""
    mo, an, gs = txt("motion/LICENSE.md"), txt("anime/LICENSE.md"), txt("gsap/LICENSE.md")
    check("motion/LICENSE.md is the package's MIT license (Motion B.V.)",
          mo.startswith("The MIT License (MIT)") and "Motion](https://motion.dev) B.V." in mo
          and "Permission is hereby granted, free of charge" in mo)
    check("anime/LICENSE.md is the package's MIT license (Julian Garnier)",
          an.startswith("The MIT License") and "Julian Garnier" in an
          and "Permission is hereby granted, free of charge" in an)
    check("gsap/LICENSE.md says NOT MIT, names the license, URL and effective date",
          "NOT MIT" in gs and 'Standard "No Charge" License' in gs
          and "https://gsap.com/standard-license" in gs and "2025-04-30" in gs, gs[:300])
    check("gsap/LICENSE.md keeps the banners rule and the one-line prohibited use",
          "banner stays" in gs and "intact" in gs and "Prohibited use" in gs and "Webflow" in gs)
    check("gsap/LICENSE.md lists every vendored GSAP file",
          all(n in gs for n in ("gsap-3.15.0.min.js", "Flip-3.15.0.min.js",
                                "CustomEase-3.15.0.min.js")))
    rd = txt("README.md")
    check("vendor/README.md states the carve-out: gsap/ is NOT MIT",
          "`dashboard/vendor/gsap/` is not" in rd and "MIT" in rd, rd[:200])
    check("vendor/README.md documents the alias, the check and the bump procedure",
          "/motion.min.js" in rd and "--check" in rd and "Bumping a version" in rd)

    # ---- server.py tables agree with the manifest -------------------------
    tb = server_tables()
    vj, va = tb.get("VENDOR_JS") or {}, tb.get("VENDOR_ALIAS") or {}
    check("server.py VENDOR_JS keys are exactly the manifest paths",
          set(vj) == set(by_path) == set(EXPECT), sorted(set(vj) ^ set(by_path)))
    check("every VENDOR_JS value is the manifest's file for that path",
          all(vj.get(p) == by_path.get(p, {}).get("file") for p in EXPECT), vj)
    check("VENDOR_ALIAS is the one /motion.min.js alias of the vendored Motion file",
          va == {"/motion.min.js": "vendor/motion/motion-14.6.0.min.js"}
          and va["/motion.min.js"] in vj.values(), va)
    check("cache policy: immutable for versioned files, no-cache for the alias",
          tb.get("VENDOR_CACHE") == "public, max-age=31536000, immutable"
          and tb.get("VENDOR_ALIAS_CACHE") == "no-cache", tb)
    src = (DASH / "server.py").read_text()
    check("do_GET's spelled-out alias literal is VENDOR_ALIAS's key",
          all('path == "%s" or path.startswith("/vendor/")' % k in src for k in va), list(va))
    check("the old 86400 s motion cache rule is gone",
          '"max-age=86400" if path == "/motion.min.js"' not in src
          and not re.search(r"max-age=86400[^\n]*motion|motion[^\n]*max-age=86400", src))
    check("every allowlisted target is a plain relative file under dashboard/vendor/ "
          "(no '..', no absolute path, no backslash, no URL-escape)",
          all(isinstance(v, str) and v.startswith("vendor/") and ".." not in v.split("/")
              and "\\" not in v and "%" not in v and not v.startswith("/")
              for v in list(vj.values()) + list(va.values())), (vj, va))
    check("every VENDOR_JS key is /vendor/<lib>/<file>.js and names exactly its target "
          "(the published path is the file's path under dashboard/)",
          all(k.endswith(".js") and k == "/" + v for k, v in vj.items()), vj)

    # ---- the old copy is gone, and nothing loads a script the server lacks --
    check("dashboard/motion.min.js (11.11.13, unverified provenance) is deleted",
          not (DASH / "motion.min.js").exists())
    sig = (b".Motion={})", b"@license Copyright 2026, GreenSock", b"Anime.js - UMD")
    stray = sorted(p.name for p in DASH.glob("*.js")
                   if any(x in p.read_bytes()[:2000] for x in sig))
    check("no library build lives outside dashboard/vendor/", not stray, stray)

    def servable(src):
        if src in vj or src in va:
            return (DASH / (vj.get(src) or va[src])).is_file()
        if src == "/expand.js" or (src.startswith("/aux_") and src.endswith(".js")
                                   and "/" not in src[1:]):
            return (DASH / src[1:]).is_file()
        return False
    shell = (DASH / "index.html").read_text()
    srcs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', shell)
    swift = (REPO / "app" / "main.swift").read_text()
    qm = re.search(r'let QUICKASK_HTML = """(.*?)"""', swift, re.S)
    qsrcs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', qm.group(1)) if qm else []
    check("every <script src> in index.html is a file the server serves",
          srcs and all(servable(x) for x in srcs), [x for x in srcs if not servable(x)])
    check("the frozen Quick Ask skeleton's scripts are all served (/motion.min.js via the alias)",
          "/motion.min.js" in qsrcs and all(servable(x) for x in qsrcs),
          [x for x in qsrcs if not servable(x)] or qsrcs)
    # A "/vendor/…" URL the dashboard's own code NAMES is a complete script
    # URL inside a string literal or attribute value: the loader table
    # (`"/vendor/gsap/gsap-3.15.0.min.js"`), a <script src>, a selector. A bare
    # `/vendor/...` in a comment, `dashboard/vendor/MANIFEST.json` in prose, or
    # a prefix a selector builds (`"/vendor/motion/motion-"`) is not a URL —
    # matching those failed this check the moment aux_anim.js (cluster
    # a-motion-runtime) merged, with its header comment and its version probe.
    url_re = re.compile(r"""["'`](/vendor/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+\.js)["'`]""")
    check("the URL matcher reads quoted script URLs, not comments, prose or selector prefixes",
          url_re.findall('["/vendor/gsap/gsap-3.15.0.min.js", \'/vendor/anime/a-4.5.0.js\']'
                         ' <script src="/vendor/motion/motion-14.6.0.min.js">'
                         ' script[src="/vendor/x/y.js"]') ==
          ["/vendor/gsap/gsap-3.15.0.min.js", "/vendor/anime/a-4.5.0.js",
           "/vendor/motion/motion-14.6.0.min.js", "/vendor/x/y.js"]
          and not url_re.findall(' * injected from /vendor/... on demand; see dashboard/vendor/MANIFEST.json'
                                 ' script[src*="/vendor/motion/motion-"] "/vendor/gsap/" "dashboard/vendor/a.js"'))
    refs = set()
    for f in sorted(DASH.glob("*.js")) + [DASH / "index.html"]:
        refs |= set(url_re.findall(f.read_text(errors="replace")))
    check("every /vendor/ URL the dashboard's own code names is allowlisted",
          all(r in vj for r in refs), sorted(r for r in refs if r not in vj))

    # ---- tools/vendor-anim.sh ---------------------------------------------
    check("tools/vendor-anim.sh exists and is executable",
          SCRIPT.is_file() and os.access(SCRIPT, os.X_OK))
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    check("tools/vendor-anim.sh passes bash -n", r.returncode == 0, r.stderr)
    pins, found = script_table("PINS", 7)
    check("the script's PINS table parses", found and len(pins) == len(EXPECT), pins)
    agree = all(
        ("/vendor/" + d) in EXPECT
        and EXPECT["/vendor/" + d][:4] == (pk, v, s, g) and EXPECT["/vendor/" + d][5] == h
        for (_lib, pk, v, s, d, g, h) in pins)
    check("PINS agree with the manifest and the reviewed pins", agree, pins)
    lics, found = script_table("LICENSES", 5)
    check("the script's LICENSES table covers each package",
          found and {x[0] for x in lics} == {"motion", "gsap", "animejs"}, lics)
    body = SCRIPT.read_text()
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    check("the script never runs npm/curl/wget and imports no network module",
          not re.search(r"(^|[;|&(]|\$\()[ \t]*(npm|npx|curl|wget)\b", code, re.M)
          and "import difflib, hashlib, json, os, sys, tempfile" in code
          and not re.search(r"\b(urllib|socket|http\.client|requests)\b", code))
    check("the script runs its python isolated (python3 -I)", "exec python3 -I -" in code)

    r = run(["--check"])
    check("tools/vendor-anim.sh --check passes on the committed tree",
          r.returncode == 0 and "ok (5 files" in r.stdout, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory(prefix="hermes-r3vendor-") as d:
        d = Path(d)
        nm, out = d / "node_modules", d / "out"
        for p, (pkg, ver, srcrel, _g, _b, _h) in EXPECT.items():
            dst = nm / srcrel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(DASH / p.lstrip("/"), dst)
            (nm / pkg / "package.json").write_text(json.dumps({"name": pkg, "version": ver}))
        shutil.copyfile(VEND / "motion" / "LICENSE.md", nm / "motion" / "LICENSE.md")
        shutil.copyfile(VEND / "anime" / "LICENSE.md", nm / "animejs" / "LICENSE.md")
        lock = {"packages": {"node_modules/%s" % k: {
            "version": EXPECT[[p for p in EXPECT if EXPECT[p][0] == k][0]][1],
            "resolved": "https://registry.npmjs.org/%s/-/%s-%s.tgz" % (
                k, k, EXPECT[[p for p in EXPECT if EXPECT[p][0] == k][0]][1]),
            "integrity": v} for k, v in INTEGRITY.items()}}
        (d / "package-lock.json").write_text(json.dumps(lock))

        r = run(["--from", str(nm), "--out", str(out)], env=dict(os.environ, HOME=str(d)))
        check("copy mode from a node_modules succeeds", r.returncode == 0, r.stdout + r.stderr)
        same = (out / "MANIFEST.json").is_file() and \
            (out / "MANIFEST.json").read_bytes() == (VEND / "MANIFEST.json").read_bytes()
        check("it reproduces the committed MANIFEST.json byte for byte "
              "(integrity read from <from>/../package-lock.json)", same)
        allsame = all((out / rel).is_file() and (out / rel).read_bytes() == (VEND / rel).read_bytes()
                      for rel in want if rel != "README.md")
        check("...and every library + license file byte for byte (incl. the GSAP NOTICE)",
              allsame)
        check("it prints a diff of MANIFEST.json", "+++ MANIFEST.json (after)" in r.stdout)
        r = run(["--from", str(nm), "--out", str(out)])
        check("a second run is a no-op", r.returncode == 0 and "MANIFEST.json unchanged" in r.stdout
              and "wrote" not in r.stdout, r.stdout)
        r = run(["--check", "--out", str(out)])
        check("--check passes on the reproduced tree", r.returncode == 0, r.stdout)

        # no lockfile: a fresh out has no provenance to carry over (null), an
        # existing one keeps the integrity it already recorded for that sha256
        os.rename(d / "package-lock.json", d / "lock.off")
        out2 = d / "out2"
        r = run(["--from", str(nm), "--out", str(out2)])
        m2 = json.loads((out2 / "MANIFEST.json").read_text()) if r.returncode == 0 else []
        check("without a lockfile the integrity is recorded as null, not invented",
              r.returncode == 0 and m2 and all(e["npm_integrity"] is None for e in m2), r.stdout)
        r = run(["--from", str(nm), "--out", str(out)])
        check("...and an existing manifest keeps the integrity it recorded for the same file",
              r.returncode == 0 and (out / "MANIFEST.json").read_bytes()
              == (VEND / "MANIFEST.json").read_bytes(), r.stdout)

        # refusals: nothing is written
        out3 = d / "out3"
        tgt = nm / "gsap" / "dist" / "Flip.min.js"
        good = tgt.read_bytes()
        tgt.write_bytes(good.replace(b"Flip 3.15.0", b"Flip 3.15.1", 1))
        r = run(["--from", str(nm), "--out", str(out3)])
        check("a source whose sha256 is not the pin is refused, and the hash is printed",
              r.returncode == 1 and "refused, nothing written" in r.stdout
              and "Flip.min.js: sha256" in r.stdout, r.stdout)
        check("...and nothing was written", not out3.exists())
        tgt.write_bytes(good)
        (nm / "animejs" / "package.json").write_text(json.dumps({"name": "animejs", "version": "4.6.0"}))
        r = run(["--from", str(nm), "--out", str(out3)])
        check("an installed version that is not the pin is refused",
              r.returncode == 1 and "installed animejs@4.6.0, pinned animejs@4.5.0" in r.stdout
              and not out3.exists(), r.stdout)
        (nm / "animejs" / "package.json").write_text(json.dumps({"name": "animejs", "version": "4.5.0"}))
        r = run(["--from", str(d / "nope"), "--out", str(out3)])
        check("a missing node_modules is refused", r.returncode != 0 and not out3.exists())

        # --check catches drift in the committed copy
        (out / "gsap" / "LICENSE.md").write_text("MIT\n")
        r = run(["--check", "--out", str(out)])
        check("--check fails when the GSAP NOTICE is replaced",
              r.returncode == 1 and "gsap/LICENSE.md" in r.stdout, r.stdout)
        shutil.copyfile(VEND / "gsap" / "LICENSE.md", out / "gsap" / "LICENSE.md")
        f = out / "motion" / "motion-14.6.0.min.js"
        f.write_bytes(f.read_bytes() + b"\n")
        r = run(["--check", "--out", str(out)])
        check("--check fails when a library file is edited",
              r.returncode == 1 and "motion/motion-14.6.0.min.js: sha256" in r.stdout, r.stdout)

    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
