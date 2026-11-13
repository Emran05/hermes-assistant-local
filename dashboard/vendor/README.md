# dashboard/vendor: third-party browser libraries

The dashboard has no build step, no bundler and no CDN: everything the
WKWebView runs is served by `dashboard/server.py` from this checkout. The three
animation libraries the motion system uses
(`docs/plans/motion-system-2026-10.md`) live here, copied **byte for byte**
from their npm packages and pinned by sha256.

| Published path (served at) | Package | Source in the package | Global | License |
|---|---|---|---|---|
| `/vendor/motion/motion-14.6.0.min.js` | `motion@14.6.0` | `dist/motion.js` | `Motion` | MIT, `motion/LICENSE.md` |
| `/vendor/gsap/gsap-3.15.0.min.js` | `gsap@3.15.0` | `dist/gsap.min.js` | `gsap` | **GSAP Standard "No Charge" License**, `gsap/LICENSE.md` |
| `/vendor/gsap/Flip-3.15.0.min.js` | `gsap@3.15.0` | `dist/Flip.min.js` | `Flip` | same |
| `/vendor/gsap/CustomEase-3.15.0.min.js` | `gsap@3.15.0` | `dist/CustomEase.min.js` | `CustomEase` | same |
| `/vendor/anime/anime-4.5.0.umd.min.js` | `animejs@4.5.0` | `dist/bundles/anime.umd.min.js` | `anime` | MIT, `anime/LICENSE.md` |

`MANIFEST.json` records, for each file: the published `path`, the `file` under
`dashboard/`, the package, version and source, the global it defines, its
`bytes` and `sha256`, the npm tarball `integrity` and `resolved` URL from the
lockfile that installed it, and its license (plus the license file's sha256).

## Licenses: `gsap/` is NOT MIT

This repository is MIT-licensed. **`dashboard/vendor/gsap/` is not.** GSAP is
distributed under GreenSock's Standard "No Charge" License (Webflow), effective
2025-04-30, <https://gsap.com/standard-license>. The npm package ships no
LICENSE file, so `gsap/LICENSE.md` is a NOTICE written by
`tools/vendor-anim.sh`: what the files are, that they are not MIT, the
conditions this repo honours (every `@license` banner stays intact; never ship a
no-code animation builder on top of GSAP) and a pointer to the live terms. It is
an engineering reading, not legal advice.

`motion` (MIT, © Motion B.V.) and `animejs` (MIT, © Julian Garnier) ship their
own `LICENSE.md`, copied unchanged next to each file. `dist/motion.js` carries
no banner of its own, so its `LICENSE.md` is the only notice and must stay.

## How the files are served

`server.py` serves a vendored file only when its exact published path is a key
of `VENDOR_JS`; there is no directory listing and no path joining, so nothing
outside the table (a `..` path, a `.map`, `LICENSE.md`, `MANIFEST.json`) can be
reached; anything else under `/vendor/` is a 404, never a fall-through to an API
route. Responses are `application/javascript; charset=utf-8` with
`Cache-Control: public, max-age=31536000, immutable` and a content-hash `ETag`
(a matching `If-None-Match` is a 304): the version is in the file name, so a new
version is a new URL. The same-origin guard runs first, as for every route.
The server has no HEAD handler, so check the headers with
`curl -s -o /dev/null -D - http://127.0.0.1:7788/vendor/gsap/gsap-3.15.0.min.js`,
not `curl -I`.

`/motion.min.js` is an **alias** of the vendored Motion file (`VENDOR_ALIAS`),
answered with `Cache-Control: no-cache` plus an `ETag`. The frozen Quick Ask
skeleton in `app/main.swift` still requests that URL (`index.html` now loads the
versioned `/vendor/motion/motion-14.6.0.min.js`); the alias keeps the popover
working without a rebuild of the app (which would drop the Full Disk Access
grant). Every Motion call the code makes today runs
unchanged on Motion 14: `tools/tests/unit/t_r3_vendor_legacy_calls.js` runs each
call site on the real vendored file.

## Verifying

```bash
tools/vendor-anim.sh --check                       # files vs PINS vs MANIFEST.json
python3 tools/tests/unit/t_r3_vendor_manifest.py   # the same, plus banners, licenses, hygiene
python3 tools/tests/unit/t_r3_vendor_route.py      # what the server answers
node tools/tests/unit/t_r3_vendor_legacy_calls.js  # today's Motion calls on Motion 14
```

## Bumping a version

1. Install the new version into a node_modules of your own (`npm i motion@x.y.z`
   somewhere outside this repo) and read its changelog for breaking changes.
2. Edit `PINS` in `tools/vendor-anim.sh`: version, versioned file name and the
   new sha256 (the script prints the observed hash when it refuses a mismatch).
   For gsap, re-read the live license page and update `LICENSES` (the effective
   date) if the terms changed.
3. Run `tools/vendor-anim.sh --from /path/to/node_modules`. It refuses (and
   writes nothing) if a version or hash does not match, and prints a diff of
   `MANIFEST.json`. Delete the old versioned file.
4. Update `VENDOR_JS` / `VENDOR_ALIAS` in `dashboard/server.py` and the loader
   table in `dashboard/aux_anim.js`. The unit tests fail until the manifest,
   the server table and the files agree.
5. Run `tools/tests/run.sh unit`, then reload the app (Cmd+R).

The script never runs npm and never touches the network.
