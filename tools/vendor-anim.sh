#!/usr/bin/env bash
# tools/vendor-anim.sh — vendor the browser builds of the three animation
# libraries the dashboard uses (docs/plans/motion-system-2026-10.md §4).
#
#   tools/vendor-anim.sh [--from DIR] [--out DIR] [--lock FILE]   copy + write MANIFEST
#   tools/vendor-anim.sh --check [--out DIR]                      verify only, writes nothing
#
# --from   an npm node_modules directory (default: ${NODE_MODULES:-$HOME/node_modules})
# --out    the vendor directory (default: dashboard/vendor in this checkout)
# --lock   the package-lock.json that recorded the install (default:
#          <from>/../package-lock.json, then <from>/.package-lock.json); it is
#          only read for each tarball's npm `integrity`, which goes in MANIFEST
#
# What it does, in order:
#   1. checks each package.json name + version against PINS below, and each
#      source file's sha256 against its pin — any mismatch stops the run and
#      nothing is written (a bump is a reviewed edit of PINS, see
#      dashboard/vendor/README.md);
#   2. copies the five files byte for byte under their versioned names, copies
#      the two MIT LICENSE.md files, and writes the GSAP NOTICE (gsap ships no
#      license file; its terms are not MIT);
#   3. writes dashboard/vendor/MANIFEST.json (path, file, package, version,
#      source, global, bytes, sha256, npm integrity, license...);
#   4. prints a unified diff of MANIFEST.json against the previous one.
#
# It never runs npm and never touches the network: bash plus `python3 -I`
# (stdlib only). --check needs no node_modules at all — it re-verifies the
# committed files against PINS and MANIFEST, the same facts
# tools/tests/unit/t_r3_vendor_manifest.py asserts.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# lib|package|version|source (under node_modules)|dest (under vendor/)|global|sha256
PINS='
motion|motion|14.6.0|motion/dist/motion.js|motion/motion-14.6.0.min.js|Motion|5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf
gsap|gsap|3.15.0|gsap/dist/gsap.min.js|gsap/gsap-3.15.0.min.js|gsap|92bb9a96476f983d212a2bc4f54c889039c1696dd4461d40a736860938570fbb
gsap|gsap|3.15.0|gsap/dist/Flip.min.js|gsap/Flip-3.15.0.min.js|Flip|cbe3ca726350f8d230da38a14ce2384e7772e05a45cee6144dd7fe6dde868c2f
gsap|gsap|3.15.0|gsap/dist/CustomEase.min.js|gsap/CustomEase-3.15.0.min.js|CustomEase|466e426a5c60c21c94b15a30a3dffacac9bb39ce8f4e07d071d7d4bb1be43390
anime|animejs|4.5.0|animejs/dist/bundles/anime.umd.min.js|anime/anime-4.5.0.umd.min.js|anime|8d5b3a58a1f64023a04a4cedeef135a2263b18da04b35177ac13438a0bad033b
'

# package|license|license file (copy:<file in the package> or notice)|license_url|effective
LICENSES='
motion|MIT|copy:LICENSE.md||
gsap|GSAP Standard "No Charge" License|notice|https://gsap.com/standard-license|2025-04-30
animejs|MIT|copy:LICENSE.md||
'

MODE=write
FROM="${NODE_MODULES:-$HOME/node_modules}"
OUT="$ROOT/dashboard/vendor"
LOCK=""

usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --from)  [ $# -ge 2 ] || { echo "--from needs a directory" >&2; exit 2; }; FROM="$2"; shift 2 ;;
    --out)   [ $# -ge 2 ] || { echo "--out needs a directory" >&2; exit 2; }; OUT="$2"; shift 2 ;;
    --lock)  [ $# -ge 2 ] || { echo "--lock needs a file" >&2; exit 2; }; LOCK="$2"; shift 2 ;;
    --check) MODE=check; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

PINS="$PINS" LICENSES="$LICENSES" exec python3 -I - "$MODE" "$FROM" "$OUT" "$LOCK" <<'PY'
import difflib, hashlib, json, os, sys, tempfile

mode, src_root, out, lock = sys.argv[1:5]
ERR = []


def table(name, width):
    rows = []
    for line in os.environ[name].splitlines():
        line = line.strip()
        if not line:
            continue
        f = line.split("|")
        if len(f) != width:
            sys.exit("vendor-anim: malformed %s row: %r" % (name, line))
        rows.append(f)
    return rows


PINS = [dict(zip(("lib", "package", "version", "source", "dest", "global",
                  "sha256"), r)) for r in table("PINS", 7)]
LICS = {r[0]: dict(zip(("license", "file", "url", "effective"), r[1:]))
        for r in table("LICENSES", 5)}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read(p):
    with open(p, "rb") as f:
        return f.read()


def lic_dest(pin):
    return os.path.dirname(pin["dest"]) + "/LICENSE.md"


def notice(pkg):
    """The NOTICE for a package that ships no license file (gsap). Generated
    from PINS + LICENSES so the version, file list and dates cannot drift."""
    pins = [p for p in PINS if p["package"] == pkg]
    lic = LICS[pkg]
    ver = pins[0]["version"]
    w = max(len(p["source"].split("/", 1)[1]) for p in pins)
    files = "".join("    %-*s  ->  %s\n" % (w, p["source"].split("/", 1)[1],
                                           os.path.basename(p["dest"])) for p in pins)
    return (
        "# GSAP %(v)s: license notice (NOT MIT)\n\n"
        "The JavaScript files in this directory are GreenSock's GSAP %(v)s browser\n"
        "builds, copied byte for byte from the npm package `gsap@%(v)s`:\n\n"
        "%(files)s\n"
        "They are NOT covered by this repository's MIT license. They are licensed\n"
        "under GreenSock's %(lic)s (Webflow),\n"
        "effective %(eff)s:\n\n"
        "    %(url)s\n\n"
        "The package ships no LICENSE file. Its package.json `license` field reads\n"
        "\"Standard 'no charge' license: https://gsap.com/standard-license.\", and\n"
        "each file's banner says the code is subject to the terms at that address.\n\n"
        "Conditions this repository honours:\n\n"
        "- Every file's `/*! ... @license Copyright ..., GreenSock ... */` banner stays\n"
        "  intact. The files are never edited, re-minified or concatenated;\n"
        "  `dashboard/vendor/MANIFEST.json` pins each one's sha256 and the unit test\n"
        "  `tools/tests/unit/t_r3_vendor_manifest.py` enforces it.\n"
        "- Prohibited use, in one line: GSAP must not power a tool that lets people\n"
        "  build visual animations without code in a way that competes with Webflow,\n"
        "  so this project never ships a \"design your own animation\" editor on it.\n"
        "- Webflow may change or withdraw these terms. The version is pinned, and the\n"
        "  effective date above is the one this copy was reviewed against.\n\n"
        "This is an engineering reading of the terms, not legal advice. Re-read the\n"
        "live page before redistributing these files in a new context.\n\n"
        "Copyright (c) 2008-2026, GreenSock. All rights reserved.\n"
    ) % {"v": ver, "files": files, "lic": lic["license"],
         "eff": lic["effective"], "url": lic["url"]}


def manifest_entry(pin, data, lic_bytes, integ):
    lic = LICS[pin["package"]]
    e = {
        "path": "/vendor/" + pin["dest"],
        "file": "vendor/" + pin["dest"],
        "lib": pin["lib"],
        "package": pin["package"],
        "version": pin["version"],
        "source": pin["source"],
        "global": pin["global"],
        "bytes": len(data),
        "sha256": sha(data),
        "npm_integrity": integ.get("integrity"),
        "npm_resolved": integ.get("resolved"),
        "license": lic["license"],
        "license_file": "vendor/" + lic_dest(pin),
        "license_sha256": sha(lic_bytes),
    }
    if lic["url"]:
        e["license_url"] = lic["url"]
    if lic["effective"]:
        e["effective"] = lic["effective"]
    return e


def dump(obj):
    return json.dumps(obj, indent=2) + "\n"


def old_manifest():
    try:
        m = json.loads(read(os.path.join(out, "MANIFEST.json")))
        return m if isinstance(m, list) else []
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- --check ---
if mode == "check":
    man = old_manifest()
    by_path = {e.get("path"): e for e in man if isinstance(e, dict)}
    if len(by_path) != len(PINS):
        ERR.append("MANIFEST.json has %d entries, PINS has %d" % (len(by_path), len(PINS)))
    for pin in PINS:
        p = os.path.join(out, pin["dest"])
        try:
            data = read(p)
        except OSError:
            ERR.append("missing: %s" % pin["dest"])
            continue
        if sha(data) != pin["sha256"]:
            ERR.append("%s: sha256 %s != pinned %s" % (pin["dest"], sha(data), pin["sha256"]))
        e = by_path.get("/vendor/" + pin["dest"])
        if not e:
            ERR.append("MANIFEST.json has no entry for /vendor/%s" % pin["dest"])
            continue
        for k, want in (("file", "vendor/" + pin["dest"]), ("package", pin["package"]),
                        ("version", pin["version"]), ("source", pin["source"]),
                        ("global", pin["global"]), ("sha256", pin["sha256"]),
                        ("bytes", len(data)), ("license", LICS[pin["package"]]["license"])):
            if e.get(k) != want:
                ERR.append("MANIFEST %s: %s is %r, expected %r" % (pin["dest"], k, e.get(k), want))
        try:
            lb = read(os.path.join(out, lic_dest(pin)))
        except OSError:
            ERR.append("missing license file: %s" % lic_dest(pin))
            continue
        if e.get("license_sha256") != sha(lb):
            ERR.append("%s changed since MANIFEST.json was written" % lic_dest(pin))
        if LICS[pin["package"]]["file"] == "notice" and lb.decode() != notice(pin["package"]):
            ERR.append("%s is not the NOTICE this script writes" % lic_dest(pin))
    if ERR:
        print("vendor-anim --check: FAILED")
        for e in ERR:
            print("  " + e)
        sys.exit(1)
    print("vendor-anim --check: ok (%d files match PINS and MANIFEST.json)" % len(PINS))
    sys.exit(0)

# ------------------------------------------------------------------ write ---
if not os.path.isdir(src_root):
    sys.exit("vendor-anim: no node_modules at %s (pass --from DIR)" % src_root)

lock_data = {}
cands = [lock] if lock else [os.path.join(os.path.dirname(os.path.abspath(src_root)), "package-lock.json"),
                             os.path.join(src_root, ".package-lock.json")]
for c in cands:
    try:
        lock_data = json.loads(read(c)).get("packages") or {}
        break
    except (OSError, ValueError, AttributeError):
        continue
if lock and not lock_data:
    sys.exit("vendor-anim: could not read --lock %s" % lock)

prev = old_manifest()
prev_by_path = {e.get("path"): e for e in prev if isinstance(e, dict)}

srcs, licenses = {}, {}
for pin in PINS:
    pj = os.path.join(src_root, pin["package"], "package.json")
    try:
        meta = json.loads(read(pj))
    except (OSError, ValueError):
        ERR.append("%s: cannot read %s" % (pin["package"], pj))
        continue
    if meta.get("name") != pin["package"] or meta.get("version") != pin["version"]:
        ERR.append("%s: installed %s@%s, pinned %s@%s" % (
            pin["package"], meta.get("name"), meta.get("version"), pin["package"], pin["version"]))
        continue
    try:
        data = read(os.path.join(src_root, pin["source"]))
    except OSError:
        ERR.append("missing source: %s" % pin["source"])
        continue
    if sha(data) != pin["sha256"]:
        ERR.append("%s: sha256 %s != pinned %s (if this is a reviewed bump, update PINS)"
                   % (pin["source"], sha(data), pin["sha256"]))
        continue
    srcs[pin["dest"]] = data
    lic = LICS[pin["package"]]
    if lic["file"].startswith("copy:"):
        lp = os.path.join(src_root, pin["package"], lic["file"][5:])
        try:
            licenses[lic_dest(pin)] = read(lp)
        except OSError:
            ERR.append("%s: missing %s" % (pin["package"], lic["file"][5:]))
    else:
        licenses[lic_dest(pin)] = notice(pin["package"]).encode()

if ERR:
    print("vendor-anim: refused, nothing written:")
    for e in ERR:
        print("  " + e)
    sys.exit(1)

entries = []
for pin in PINS:
    row = lock_data.get("node_modules/" + pin["package"]) or {}
    integ = {}
    if row.get("version") == pin["version"]:
        integ = {"integrity": row.get("integrity"), "resolved": row.get("resolved")}
    else:
        old = prev_by_path.get("/vendor/" + pin["dest"]) or {}
        if old.get("version") == pin["version"] and old.get("sha256") == pin["sha256"]:
            integ = {"integrity": old.get("npm_integrity"), "resolved": old.get("npm_resolved")}
    entries.append(manifest_entry(pin, srcs[pin["dest"]], licenses[lic_dest(pin)], integ))


def put(rel, data):
    p = os.path.join(out, rel)
    try:
        if read(p) == data:
            return "same   "
    except OSError:
        pass
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".vendor-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return "wrote  "


for rel in [p["dest"] for p in PINS] + sorted(licenses):
    data = srcs.get(rel, licenses.get(rel))
    print("  %s %s (%d bytes)" % (put(rel, data), rel, len(data)))
new = dump(entries)
old_text = dump(prev) if prev else ""
print("  %s MANIFEST.json" % put("MANIFEST.json", new.encode()))
diff = list(difflib.unified_diff(old_text.splitlines(True), new.splitlines(True),
                                 "MANIFEST.json (before)", "MANIFEST.json (after)"))
print("".join(diff) if diff else "MANIFEST.json unchanged")
PY
