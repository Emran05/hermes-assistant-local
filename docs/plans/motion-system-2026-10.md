# Motion system for the Hermes dashboard (2026-10)

Status: design, round 2. Implementation happens in round 3 (clusters in section 12).
Target tree: the `integration` worktree (`dashboard/` is served by the stdlib
`server.py` into the WKWebView app shell; no build step, no bundler, no CDN).
To be committed as `docs/plans/motion-system-2026-10.md`.

The owner installed three animation libraries and asked for all three to be
used: GSAP 3.15.0, anime.js 4.5.0 and Motion 14.6.0 (in `~/node_modules`).
Three animation engines in one dashboard overlap by nature. This design gives
each one a job the other two do not do, loads two of them only when needed,
and puts every global rule (reduced motion, hidden window, refresh quietness,
compositor-only properties, tokens) in one runtime module. Call sites stay
small and degrade to the final state when anything is missing.

---

## 0. Decisions on one screen

1. **Each library has its own job, and CSS keeps a fourth.**
   - **Motion 14.6.0 is loaded at startup.** It handles one element (or one flat
     list) changing state: enter and exit, list entrances, spring nudges. The
     existing global `animate()` and `revealStagger()` route to it through a shim.
   - **GSAP 3.15.0 is loaded when first needed** (core, Flip and CustomEase only).
     It handles layout, through FLIP (Customize reorder, add and remove; Needs-you
     rows closing the gap). It also runs multi-track sequences that must be
     interrupted or reverted as one unit: the view entrance, the model-swap
     overlay and the onboarding steps.
   - **anime.js 4.5.0 is loaded when first needed.** It handles values, SVG
     geometry and text: number count-ups, stroke draw-ins and word reveals.
   - **CSS keeps** every infinite loop (aurora, live dot, thinking dots, spinners,
     shimmer, Customize jiggle, weather glyphs, sigil orbits). It also keeps the
     popover entrances (`glassIn`, replayed on unhide), the gel press, the toggles,
     the scroll-edge glass and the toast show and hide. These already run on the
     compositor and already honour both reduce switches. Moving them to JS would
     only cost frames.
2. **One runtime module, `dashboard/aux_anim.js`, exposes `window.HermesAnim`.**
   The API is small and synchronous. It never waits for a library to load: if
   the library is not ready, the call jumps to the final state. It enforces
   reduced motion (the OS media query OR `:root[data-reduce]`), skips everything
   while `document.hidden`, keeps background refreshes quiet, restricts animation
   to transform and opacity, and holds the shared tokens.
3. **The libraries are vendored under `dashboard/vendor/<lib>/`.** File names
   carry the version. The server serves them through an explicit allowlist. A
   `MANIFEST.json` pins each file's sha256 and a unit test checks it, and license
   files sit next to the code. `dashboard/motion.min.js` is deleted (it is
   motion@11.11.13 from jsdelivr and its provenance was never verified; audit
   2026-10-21, deferred item 8). `/motion.min.js` stays as a server alias,
   because the frozen Quick Ask skeleton in `app/main.swift` still requests it.
4. **CSS owns the individual transform properties** (`translate`, `rotate`,
   `scale`) and **JS libraries own `transform`.** The two compose, so a jiggle, a
   gel press and a Flip can overlap without fighting. The one place that breaks
   this rule today is the Customize jiggle, which animates `transform` and would
   override any JS transform. It moves to `rotate`.
5. **Implementation is split into five clusters with disjoint files** (section
   12). C1 handles vendoring and serving and C2 the runtime module. C3 covers
   index.html, C4 the Needs-you, onboarding and Agent pages, and C5 the pop-out
   numbers plus fixes for animations that repaint or replay on every refresh.

---

## 1. What the code does today (evidence)

| # | Finding | Where |
|---|---|---|
| E1 | The static JS allowlist is `/motion.min.js`, `/expand.js` and any single-segment `/aux_*.js`, sent as `application/javascript`. `motion.min.js` is cached with `max-age=86400`; everything else is `no-store`. Nothing under a subdirectory is served. | `dashboard/server.py` 5211-5224 |
| E2 | `dashboard/motion.min.js` is **motion@11.11.13** (jsdelivr banner), 63,719 B, sha256 `93355a30…5d`. Its version, provenance and license were never verified. | file header; audit `docs/audits/2026-10-21-codebase-audit.md` deferred item 8 |
| E3 | The frozen Quick Ask popover loads `/motion.min.js`, `/aux_quickask.js` and `/aux_clip.js`. `main.swift` must not be rebuilt casually, because a rebuild drops the Full Disk Access grant. | `app/main.swift` 17-27 (`QUICKASK_HTML`) |
| E4 | `index.html` captures `const M=window.Motion` and wraps it in a global `animate(el,kf,opt)`. About 20 calls in index.html and about 40 in aux modules go through it. | `index.html` 1467-1469; per-file counts in section 7 |
| E5 | **Motion 11 ignores the `easing` option** (it was renamed `ease` in 11.0). Every `easing:SPRING`, `easing:[...]` or `easing:'ease-out'` call today runs on Motion's default curve. The vendored bundle has exactly one `easing` token, and that one is inside WAAPI keyframe plumbing. The Motion upgrade guide confirms the rename. | `motion.min.js`; Motion docs `js/upgrade-guide` (11.0) |
| E6 | `REDUCE` is the OS setting read once at load, so the in-app switch does not reach callers that test it. aux_prefs closes that gap by wrapping `Element.prototype.animate` and `window.Motion.animate`, which forces duration 0 while either switch is on. It also adds a CSS kill rule for `:root[data-reduce]`. | `index.html` 1506; `aux_prefs.js` 100-104, 209-276 |
| E7 | aux_glass re-measures the lit surface on `transitionrun`, `animationstart` and their end events. **JS-driven animations fire none of these**, so during a GSAP or anime move the pointer light measures a stale box (the C05 class of bug). | `aux_glass.js` 180-191 |
| E8 | aux_window's MutationObserver watches the header subtree's attributes (only `data-glint` and `--mx/--my` writes are excluded). Any library that writes inline style every frame on a header element triggers a debounced re-measure on every frame. | `aux_window.js` 51-69 |
| E9 | The Customize jiggle animates `transform` (`@keyframes wjig`). A CSS animation outranks inline style, so a JS transform on a jiggling card (Flip) would not show. | `index.html` 881-887 |
| E10 | Toggling Customize, adding a widget and removing one all call `renderHub()`, which runs `innerHTML=''`, a full rebuild and `revealStagger` across all cards. Every layout change replays the boot entrance and nothing moves to its new place. Drag reflow re-slots the DOM on every `dragover` with no motion. | `index.html` 2314-2361, 2381-2394, 2398-2410 |
| E11 | The 60 s `refreshHub` patches widget data in place, and its comment says no re-animation. That holds today only because the hub RENDER bodies do not animate. Nothing enforces it. | `index.html` 2363-2380, 3371 |
| E12 | **The onboarding step entrance replays on every repaint.** `.onb-col{animation:onb-in}` runs again on each `paint()`, and `paint()` runs on every toggle and **every 2.5 s during a model download** (`pollModels`). | `aux_onboarding.js` 531-536, 956-980, 1174-1195 |
| E13 | **Flight Recorder rows re-fade every 3 s** while the Agent page's Record rail is open. The Agent rail polls `loadRecorder()`, which rebuilds the rows with `innerHTML` and runs `animate(rows,{opacity:[0,1]})`. The undo flash animates `backgroundColor` to a `color-mix(var(--iris)…)` string, which is a paint property and a value JS cannot interpolate. | `aux_recorder.js` 352-380, 736-744; `aux_agent.js` 1087-1093 |
| E14 | The model-swap overlay is driven by `setTimeout` chains (1300, 2200 and 2600 ms, then a 360 ms hide). Those timers keep firing while the window is hidden. | `index.html` 3197-3229 |
| E15 | Meters animate `width`: `.meter i`, `.mmmem-bar i`, `.cs-ai-membar i`, `@keyframes mxW{from{width:0}}`, plus the aux_memory and aux_youmodel meters. Each transition is a layout on every frame. | `index.html` 412, 604, 642, 1215-1216; `aux_memory.js` 89; `aux_youmodel.js` 105 |
| E16 | The pop-out uses a CSS entrance (grows from the clicked card), a Motion exit, and a 170 ms `setTimeout` hide with a cancel-on-reopen race guard. The pattern works, so it is kept; only the timer goes. | `index.html` 2669-2731 |
| E17 | Mind/Settings growth stats (`g-skills`, `g-mem`, `g-sessions`, `g-tools`, `a-msgs`, `a-in`, `a-out`) are written with `textContent`. The activity sparkline draws in with a WAAPI `strokeDashoffset`. Both run on every `setView('mind')`. | `index.html` 3382-3443 |
| E18 | Motion 14 `press()` sets `tabIndex=0` on any element that is not already focusable (`tabindex")||(t.tabIndex=0)` in `motion/dist/motion.js`). | 14.6.0 bundle |
| E19 | In the 14.6.0 source, `MotionGlobalConfig.skipAnimations` makes every hybrid `animate()` apply its final keyframe on the next frame and resolve. | `motion-dom/dist/es/animation/interfaces/motion-value.mjs` 72-90 |
| E20 | The repo's mini DOM (`tools/tests/unit/_a11y_dom.js`) boots the real index.html in node. **Real GSAP and anime do not behave faithfully there.** GSAP's CSSPlugin does not recognise mini-DOM elements ("Invalid property opacity… Missing plugin?"), and an anime `complete()` wrote no style. Real Motion 14 loads and runs. The unit tier therefore stubs the libraries, and only the browser tier runs the real ones. | probe run 2026-10-26 (scratch, not committed) |
| E21 | WKWebView limits. The app sets no `isInspectable`, so Web Inspector cannot attach (and main.swift is frozen); profile with Safari against `http://127.0.0.1:7788`. WebKit has no `requestIdleCallback`. Individual transform properties and CSS `linear()` easing are supported. `feDisplacementMap` stays banned. | `app/main.swift`; CLAUDE.md "hub runs in a WKWebView" |
| E22 | CI runs `node --check` on `git ls-files 'dashboard/*.js'`. The git pathspec `*` crosses `/`, so vendored files get checked too, which is fine because all five pass. CI also fails on any tracked `/Users/<name>` path; none of the vendored files contain one. | `.github/workflows/ci.yml` 44-51, 64-74 |

---

## 2. Licenses and redistribution

| Package | License | Redistribution inside this repo and its release tarball | Notice required |
|---|---|---|---|
| motion 14.6.0 | MIT (© 2024 Motion B.V.) | Yes | Ship `LICENSE.md` next to the file. `dist/motion.js` carries **no** banner, so the license file is the only notice. |
| animejs 4.5.0 | MIT (© 2025 Julian Garnier) | Yes | Keep the bundle's `@license MIT` banner and ship `LICENSE.md`. |
| gsap 3.15.0 | GSAP Standard "No Charge" License (Webflow), effective 2025-04-30 | **Yes, with conditions** (below) | Keep every file's `/*! … @license Copyright 2026, GreenSock … Subject to the terms at https://gsap.com/standard-license */` banner **intact**, and ship a NOTICE. |

**Findings for GSAP.**

- `~/node_modules/gsap` has **no LICENSE file.** The terms appear only in
  `package.json` (`"license": "Standard 'no charge' license: https://gsap.com/standard-license."`),
  in the README's License section, and in the banner of each dist file.
- On 2026-10-26, `https://gsap.com/standard-license` redirected to
  `/community/standard-license` and then to `/offline/` (the GSAP forums are down).
  The text was read from the ScanCode LicenseDB mirror
  (`gsap-standard-no-charge-2025`), which shows effective and last-modified dates
  of 2025-04-30.
- **The grant** is a license to "use, reproduce, display, and implement" GSAP for
  Permitted Uses. Use on any website, web application or digital interface is a
  Permitted Use. The license does not prohibit redistribution, and every
  recipient of the repo or tarball is itself a licensee under the same public
  terms. Vendoring the dist files into an MIT repository that drives a web UI is
  therefore inside the grant.
- **Conditions we must honour.**
  1. Do not remove or alter GSAP's proprietary notices. The files are copied
     byte for byte, and the sha256 test enforces it.
  2. Do not use GSAP in a tool that lets users build visual animations without
     code in a way that competes with Webflow (a Prohibited Use). Concretely:
     never ship a "design your own widget animation" editor or similar on top of
     GSAP.
  3. Webflow may change the terms or terminate on non-compliance. The version is
     pinned and the effective date is recorded in `MANIFEST.json`.
- **Carve-out.** The repo `LICENSE` is MIT. `dashboard/vendor/README.md` and the
  root `README.md` third-party section must state that `dashboard/vendor/gsap/`
  is **not** MIT-licensed. This is an engineering reading, not legal advice; the
  owner should re-check the live page once gsap.com is back online.

All plugins in 3.13 and later are free, including Flip, DrawSVG, SplitText,
MorphSVG, ScrollTrigger and CustomEase. Only **Flip and CustomEase** are vendored.
anime already covers SVG and text, and this app has no scroll-linked animation
(section 3).

---

## 3. Role split

### 3.1 The rule

> One *kind of change* has one owner. If a call would fit two libraries, use the
> owner from this table and do not add a second path.

| Kind of change | Owner | Why this library | What the others must not do here |
|---|---|---|---|
| One element or a flat list entering, leaving or nudged by a state change (open, close, send, act) | **Motion** | Hybrid `animate()` runs opacity and transforms on WAAPI, so off the main thread (best for WKWebView). Springs keep their velocity when interrupted. It is the incumbent behind 60 call sites. | GSAP and anime do not run single-element enter/exit. |
| Elements moving because the **layout** changed: reorder, add, remove, a gap closing | **GSAP Flip** | Mature FLIP with `data-flip-id` matching across an `innerHTML` rebuild, `onEnter`, `isFlipping`, and `simple:true`. Motion 14's `animateLayout` has no vanilla exit (no AnimatePresence) and anime's `createLayout` is newer and less proven. | Motion `animateLayout` and anime `createLayout` are not used. |
| A **sequence** of more than two tracks that must finish, revert or be cut off as one unit (view entrance, model-swap show/ready/fail, onboarding step) | **GSAP timeline** | Labels and position parameters, `gsap.context().revert()` for cancel, and `progress(1)` for an instant consistent end. CustomEase gives exact parity with the CSS curves. | Motion `animate(sequence)` and anime `createTimeline` are not used for these. |
| A **value**, **SVG geometry** or **text** changing: count-ups, stroke draw-in, word reveal | **anime.js** | It tweens plain objects (`{v}`) for formatted numbers, and has `svg.createDrawable` and accessible `splitText`. This is the work CSS cannot do. | GSAP DrawSVG, SplitText and MorphSVG are not vendored. Motion does not count. |
| Infinite loops, hover, focus, the press feel, popover entrances, toggles, toast show/hide | **CSS** | Compositor-only, and the reduce media query plus the aux_prefs kill rule already cover it. | **No JS library may run an infinite animation.** |

### 3.2 Uses I declined, and why

- **anime for the thinking indicator or the live dot.** Both are infinite
  opacity/transform loops that already run as CSS on the compositor (S tier) and
  stop under either reduce switch. An anime loop would run on the main thread
  forever. They stay CSS.
- **Motion `press()` and `hover()` across the UI.** The CSS gel press is already
  tuned and costs nothing. `press()` adds `tabIndex=0` to non-focusable targets
  (E18), which would put every hub card in the Tab order and work against the
  k14a keyboard work. Not used in round 3. A later `HermesAnim.press(el)` may wrap
  it, but must refuse targets that are not native buttons.
- **Motion for popover entrances.** The CSS `glassIn` keyframes replay on
  every unhide, grow from the anchor, and need no script. They stay. Motion owns
  the exits, because an exit needs a "done" callback before `hidden=true`.
- **GSAP DrawSVG, SplitText and MorphSVG.** anime owns SVG and text. If anime's
  draw or split ever disappoints, each GSAP plugin is a single extra file
  (4.4 KB, 7.7 KB, 21.2 KB).
- **ScrollTrigger and anime `onScroll`.** The app has a fixed viewport with
  internal scrollers and no scroll-linked storytelling. Not used.
- **The segmented-control glide.** It is hand-built in aux_glass (WAAPI, S
  tier), and migrating it would buy nothing.

---

## 4. Vendoring

### 4.1 Files

All five are UMD/IIFE builds that define a global when loaded by a plain
`<script>`. All passed `node --check`. None contains a `sourceMappingURL` or a
`/Users/` path.

| Published path (server key) | Source in `~/node_modules` | Bytes | gzip -9 | Global | sha256 |
|---|---|---|---|---|---|
| `/vendor/motion/motion-14.6.0.min.js` | `motion/dist/motion.js` (already minified; `motion.dev.js` is the 657 KB dev build) | 172,451 | 57,240 | `Motion` | `5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf` |
| `/vendor/gsap/gsap-3.15.0.min.js` | `gsap/dist/gsap.min.js` | 72,927 | 28,314 | `gsap` | `92bb9a96476f983d212a2bc4f54c889039c1696dd4461d40a736860938570fbb` |
| `/vendor/gsap/Flip-3.15.0.min.js` | `gsap/dist/Flip.min.js` | 25,534 | 9,706 | `Flip` | `cbe3ca726350f8d230da38a14ce2384e7772e05a45cee6144dd7fe6dde868c2f` |
| `/vendor/gsap/CustomEase-3.15.0.min.js` | `gsap/dist/CustomEase.min.js` | 7,143 | 3,673 | `CustomEase` | `466e426a5c60c21c94b15a30a3dffacac9bb39ce8f4e07d071d7d4bb1be43390` |
| `/vendor/anime/anime-4.5.0.umd.min.js` | `animejs/dist/bundles/anime.umd.min.js` | 118,043 | 40,592 | `anime` | `8d5b3a58a1f64023a04a4cedeef135a2263b18da04b35177ac13438a0bad033b` |

Totals: 172 KB loaded at startup (it replaces 64 KB, so +109 KB). Loaded on
demand: GSAP's three files (105.6 KB) and anime (118 KB). Measured in V8,
compiling each bundle takes 0.1-1.6 ms. JavaScriptCore is in the same range;
the budget in section 9 allows 10 ms each including evaluation.

npm `integrity` values from `~/package-lock.json`, to record in the manifest:

- gsap-3.15.0.tgz `sha512-dMW4CWBTUK1AEEDeZc1g4xpPGIrSf9fJF960qbTZmN/QwZIWY5wgliS6JWl9/25fpTGJrMRtSjGtOmPnfjZB+A==`
- animejs-4.5.0.tgz `sha512-NQimYX+lz8WaXonGS9zVVoviCIAjONeJayxacUditaivYLqyXgGjFKjuyl0aUUhFKm5MriX9ty1TGovNzoJQWA==`
- motion-14.6.0.tgz `sha512-qqaZbyubgk7x3zxjPgP1l3Br/KQXol/IIV1uqb358SoN4ApcqNuV7+wyDPg/XLErmuyQL5lL8JVI/fOmDsmBKQ==`

### 4.2 Layout on disk

```
dashboard/vendor/
  README.md            what is here, why, the license carve-out, how to bump a version
  MANIFEST.json        [{path, package, version, source, bytes, sha256, npm_integrity, license, license_url?, effective?}]
  motion/motion-14.6.0.min.js   motion/LICENSE.md   (copied from the package)
  gsap/gsap-3.15.0.min.js  gsap/Flip-3.15.0.min.js  gsap/CustomEase-3.15.0.min.js
  gsap/LICENSE.md      NOTICE: not MIT; GSAP Standard "No Charge" License, effective
                       2025-04-30, https://gsap.com/standard-license; banners must stay
                       intact; Prohibited Uses summarised in one line
  anime/anime-4.5.0.umd.min.js  anime/LICENSE.md  (copied from the package)
```

`tools/vendor-anim.sh` is new, written in bash plus `python3 -I` stdlib. It takes
`--from "${NODE_MODULES:-$HOME/node_modules}"` and:

1. Checks each `package.json` version against the pinned table in the script.
2. Copies the five files byte for byte under their versioned names, and copies
   the two MIT license files.
3. Writes `MANIFEST.json` with sha256, bytes, and the integrity value from
   `$HOME/package-lock.json` if that file exists.
4. Prints a diff.

It never runs npm and never touches the network. Bumping a version means
running the script, then editing `VENDOR_JS` (server) and `LIBS` (aux_anim). The
manifest test fails until all three agree.

`dashboard/motion.min.js` is **deleted**. The `motion.min.js` skip lines in
`.github/workflows/ci.yml` (48), `tools/release.sh` (89) and
`tools/tests/unit/t_b17_frame_headers.py` (75) go with it. That test only
iterates the top level of `dashboard/`, so `vendor/` is not scanned.

---

## 5. Serving (server.py)

Replace the `/motion.min.js` branch of the static block (5211-5224) with an
explicit table and one helper. Matching is an exact dict lookup, so no path
traversal can reach anything that is not listed.

```python
# Vendored animation libraries (docs/plans/motion-system-2026-10.md). An
# explicit allowlist, never a directory: a file is served only when its exact
# published path is a key here; tools/tests/unit/t_vendor_manifest.py checks
# each one against dashboard/vendor/MANIFEST.json (sha256) at test time.
VENDOR_JS = {
    "/vendor/motion/motion-14.6.0.min.js":   "vendor/motion/motion-14.6.0.min.js",
    "/vendor/gsap/gsap-3.15.0.min.js":       "vendor/gsap/gsap-3.15.0.min.js",
    "/vendor/gsap/Flip-3.15.0.min.js":       "vendor/gsap/Flip-3.15.0.min.js",
    "/vendor/gsap/CustomEase-3.15.0.min.js": "vendor/gsap/CustomEase-3.15.0.min.js",
    "/vendor/anime/anime-4.5.0.umd.min.js":  "vendor/anime/anime-4.5.0.umd.min.js",
}
# app/main.swift's frozen Quick Ask skeleton still requests /motion.min.js.
# An alias, revalidated, so the popover picks up a new version on its next load.
VENDOR_ALIAS = {"/motion.min.js": "vendor/motion/motion-14.6.0.min.js"}
```

In `do_GET`, before the `/expand.js` and `/aux_*.js` branch:

```python
elif path in VENDOR_JS or path in VENDOR_ALIAS:
    self._static_js(VENDOR_JS.get(path) or VENDOR_ALIAS[path],
                    "no-cache" if path in VENDOR_ALIAS
                    else "public, max-age=31536000, immutable")
elif path == "/expand.js" or (path.startswith("/aux_") and path.endswith(".js")
                              and "/" not in path[1:]):
    self._static_js(path.lstrip("/"), "no-store")
```

`_static_js(rel, cache)` holds the existing read, 404 and header code
(`Content-Type: application/javascript`, `Content-Length`, `Cache-Control`).
`self._guard()` already runs before dispatch, so the Host and Origin rules apply
to vendor files too. No CORS headers are added. `/aux_anim.js` needs no server
change because the existing `/aux_*.js` rule covers it.

The versioned names make `immutable` safe. The main window loads the new
`/vendor/motion/...` URL, so a cached v11 copy of `/motion.min.js` cannot reach
it. The popover may keep its v11 copy for up to a day under the old
`max-age=86400`, which is harmless: v11 and v14 share the call shape aux_quickask
uses.

---

## 6. The runtime module `dashboard/aux_anim.js`

### 6.1 Load order (index.html)

```html
<script src="/vendor/motion/motion-14.6.0.min.js"></script>
<script src="/aux_anim.js"></script>      <!-- before the inline shell script -->
<script src="/aux_md.js"></script>
<script> …inline shell… </script>
```

GSAP and anime are **never** in index.html; aux_anim injects them. aux_anim uses
no index.html globals, so it can run first. It is an ES5-style IIFE like the other
aux files. Targets: 500 lines or fewer, no `setInterval` anywhere, no `innerHTML`.

### 6.2 Install-time effects (all idempotent: `window.__auxAnim` guard)

1. **Reduced motion from the first script.** If `localStorage.hermes_reduce==='1'`
   and the attribute is missing, set `:root[data-reduce]`. This mirrors what
   aux_prefs does when it installs, but earlier: aux_prefs loads after the
   inline script. The localStorage read is wrapped in try/catch.
2. `MQ = matchMedia('(prefers-reduced-motion: reduce)')`.
   `reduced() = HTML.hasAttribute('data-reduce') || MQ.matches`, read live on
   every call. These are the same two inputs aux_glass and aux_prefs use.
3. `Motion.MotionGlobalConfig.skipAnimations = reduced()` when the v14 config
   object exists (feature-detected; v11 has none). It is kept in sync by an `MQ`
   change listener and a MutationObserver on `<html>` with
   `attributeFilter:['data-reduce']`. Turning reduce on also calls `settle()`.
4. `visibilitychange`: going hidden calls `settle()` (see 6.6). Going visible
   schedules the idle prefetch if it never ran.
5. **The view-entrance observer.** One MutationObserver on `.stage` (subtree,
   `attributeFilter:['hidden']`) keeps only the records whose target is a direct
   `.view` child that just became visible, and runs `sequence('view', …)` on it
   (section 8.3). This decouples the entrance from both `setView`
   implementations (index.html's and aux_agent's wrapper). It is installed on
   DOMContentLoaded if `.stage` is not parsed yet. The boot view is skipped
   through `once('view:boot')`.
6. **Idle prefetch.** On `load`, if not reduced and not hidden, one
   `setTimeout(prefetch, 2500)`. `prefetch` loads gsap, then anime. Under
   reduced motion it never loads either library: nothing would animate, so
   reduced-motion users never pay for them. If reduce is turned off later,
   prefetch is scheduled then. Library loads are also kicked early on the first
   `pointerdown` on `#edit-toggle`, on `header .seg` and on `#model-pill`. WebKit
   has no `requestIdleCallback` (E21), hence the timer.

### 6.3 Library loader

```js
LIBS = {
  gsap:  { files: ['/vendor/gsap/gsap-3.15.0.min.js', '/vendor/gsap/Flip-3.15.0.min.js',
                   '/vendor/gsap/CustomEase-3.15.0.min.js'],
           ok: function () { return !!(window.gsap && window.Flip && window.CustomEase); },
           init: initGsap },
  anime: { files: ['/vendor/anime/anime-4.5.0.umd.min.js'],
           ok: function () { return !!(window.anime && window.anime.animate); },
           init: initAnime }
}
```

- `load(lib)` appends one `<script src async=false>` per file to `<head>`, which
  preserves execution order. It resolves when the last file loads and `ok()`
  holds, then runs `init` once. The URLs come only from this constant.
- States are `idle`, `loading`, `ready` and `failed`. **There is one attempt per
  page life.** `failed` is permanent (one `console.warn`), and every call that
  needs that library then takes the instant path. The app stays fully usable.
- `initGsap`:
  - `gsap.registerPlugin(Flip, CustomEase)`.
  - `CustomEase.create('hermes.out', '.22,1,.36,1')`, and the same for `gel`,
    `pop`, `press` and `expo` from section 6.4. Verified: CustomEase accepts the
    four-number form, and `hermes.gel(0.5) = 1.087`, an overshoot as intended.
  - `gsap.defaults({ease:'hermes.out', duration:.22, overwrite:'auto'})`,
    `gsap.config({nullTargetWarn:false, autoSleep:60})` and
    `gsap.ticker.lagSmoothing(500, 33)`.
- `initAnime`: assert `anime.engine.pauseOnDocumentHidden === true` (the default)
  and set it if missing.

### 6.4 Tokens (one source; CSS mirrors it and a parity test locks them together)

```js
T = {
  ease:   { out:[.22,1,.36,1], gel:[.34,1.56,.64,1], pop:[.22,1.28,.42,1],
            press:[.2,0,.4,1], expo:[.16,1,.3,1] },
  dur:    { press:.09, micro:.15, small:.22, pop:.32, view:.36, layout:.42,
            surface:.45, rise:.46, count:.8, draw:.9 },            // seconds
  spring: { snappy:{type:'spring', visualDuration:.22, bounce:.15},
            gel:   {type:'spring', visualDuration:.34, bounce:.3},
            soft:  {type:'spring', visualDuration:.4,  bounce:.12} },
  stagger:{ step:.035, max:12, total:.4 },                        // at most 12 animate; the tail lands at once
  hold:   { ready:1.3, slow:2.2, fail:2.6 }                       // swap-overlay reading time, NOT motion
}
```

CSS in `:root` only. Durations and curves do not depend on the theme, so the
theme trap does not apply to them. Any new **colour** token, by contrast, must be
declared in all four palette blocks.

```css
--ease-out:cubic-bezier(.22,1,.36,1); --ease-expo:cubic-bezier(.16,1,.3,1);
--d-press:90ms; --d-micro:150ms; --d-small:220ms; --d-pop:320ms; --d-view:360ms;
--d-layout:420ms; --d-surface:450ms;
```

The existing `--gel`, `--press` and `--pop` stay as they are. In JS, `SPRING`
(index.html 1507) becomes `'cubic-bezier(.22,1,.36,1)'`, the value of `T.ease.out`.

Mapping per library: Motion uses the `ease` arrays and spring objects as written.
GSAP uses `'hermes.<name>'`, falling back to `power3.out`, `back.out(1.6)`,
`back.out(1.2)`, `power2.inOut` and `expo.out` if CustomEase failed. anime uses
`anime.cubicBezier(...T.ease.x)`. **Motion springs only take two keyframes**; three
or more fall back to a tween with `T.ease.out`.

### 6.5 API (normative for C3, C4 and C5)

`window.HermesAnim` is frozen, and `HermesAnim.v === 1`. Every method is
synchronous and never throws, since it catches internally.

| Member | Engine | Contract |
|---|---|---|
| `T` | none | Frozen tokens, as in 6.4. |
| `reduced()` | none | Reads both switches live. |
| `live()` | none | `!reduced() && !document.hidden && quietDepth === 0`. When false, every call below takes the **instant path**. |
| `quiet(fn)` | none | Runs `fn`, and every `HermesAnim` call made synchronously inside it is instant. Nests, uses try/finally, and returns `fn`'s value. Covers synchronous code only; async renderers pass `{key}` instead. |
| `once(key)` | none | Returns true the first time a key is seen in this page life, false after. Keys look like `'hub:boot'`, `'ny:act:'+id` or `'rec:'+id`. |
| `ready(lib)` / `load(lib)` | none | `'gsap'` or `'anime'`. `load` returns a Promise of a boolean. Callers never need either one; both exist for tests and for the early kicks. |
| `enter(target, preset, opts?)` | Motion | Element or list. `opts`: `{delay, key}`; a key already seen means instant. On finish, the inline `opacity` and `transform` it wrote are **cleared**, so CSS keeps control (hover, jiggle, the next entrance). |
| `exit(target, preset, done?, opts?)` | Motion | `done` runs **exactly once**, after the animation, or **synchronously before return** on the instant path. `done` must hide or remove the element; the runtime clears the inline styles after `done` returns. |
| `stagger(targets, preset, opts?)` | Motion | `enter` with `delay: i*step`. `step = min(opts.step or T.stagger.step, T.stagger.total/n)`. Only the first `T.stagger.max` targets animate. |
| `spring(el, values, name?, opts?)` | Motion | A one-off value change on a named token spring (`snappy`, `gel` or `soft`). Three or more keyframes use a tween. |
| `legacy(el, keyframes, options)` | Motion | The old global `animate()` contract (6.7). Returns Motion controls, a WAAPI Animation, or `undefined`. |
| `flip(scope, mutate, opts?)` | GSAP Flip | Calls `Flip.getState(targets)`, then `mutate()`, then `Flip.from(state, …)`. Targets default to `scope`'s `[data-flip-id]` descendants. `opts`: `{duration=T.dur.layout, ease:'out', enter:'pop'|false, exclude:el, max:24}`. On the instant path, `mutate()` runs synchronously and that is all. More than `max` targets also takes the instant path. |
| `isFlipping(el)` | GSAP Flip | `Flip.isFlipping(el)` when GSAP is ready, else false. Used for drag hysteresis. |
| `sequence(name, build, opts?)` | GSAP | `build(tl, T)` adds tweens to `gsap.timeline({defaults:{ease:'hermes.out'}})` inside a `gsap.context`. **One live sequence per name**: starting `name` again first does `progress(1)` and then `kill()` on the old one. `opts`: `{done, instant}`. On the instant path, `opts.instant(T)` runs (if given), then `opts.done`. `build` may only animate **from** a state **to** the element's CSS state, so skipping it is always correct. It also adds `clearProps:'opacity,transform'` on from-tweens. |
| `hold(seconds, fn)` | GSAP / timer | A wait that is not motion, such as the swap overlay's reading time. With GSAP ready it is `gsap.delayedCall`, otherwise one `setTimeout`. While hidden, `fn` runs immediately and no timer is set. Pending holds fire when the page goes hidden. |
| `count(el, to, opts?)` | anime | Counts from `opts.from`, else from the last value shown on that element (`el.__haVal`), else 0, up to `to`. `opts`: `{fmt=n=>Math.round(n).toLocaleString(), dur=T.dur.count, ease:'expo', delay}`. `textContent` is written only when the formatted string changes. If `from===to`, or on the instant path, the final text is written at once. While counting, the final value is set as `aria-label`, which is removed at the end. A `min-width` in `ch` is set from the final string to keep the width stable. |
| `draw(paths, opts?)` | anime | `svg.createDrawable` with `draw: ['0 0','0 1']`, `opts`: `{dur=T.dur.draw, delay}`. On the instant path the path is not touched, because fully drawn is the default. |
| `words(el, opts?)` | anime | `splitText(el, {words:true})` with the accessible default, then words move from y `0.5em` to 0 with opacity 0 to 1, a 30 ms stagger and `T.dur.small*2`. Afterwards `split.revert()` returns the element to plain text. Plain-text headings only. |
| `settle()` | all | Finishes every running handle now and calls pending `done`s. Running: GSAP `progress(1)`; anime `.complete()`; Motion `.complete()`. |
| `stats()` | none | `{started, instant:{reduced, hidden, quiet, nolib, header}, running, libs:{motion:'14.6.0'|'legacy'|null, gsap:state, anime:state}}`, for tests and a possible doctor line. |

**Handle**: `{done:boolean, finished:Promise<void>, cancel(), finish()}`.

- `cancel()` stops the animation, removes the inline styles this handle wrote (a
  GSAP context revert, Motion `cancel()`, anime `revert()`) and **never** calls
  `done`. This is the reopen-during-exit pattern openPop and aux_prefs already use.
- `finish()` jumps to the end and calls `done`.
- On the instant path, `done === true` and `finished` is already resolved.

**Motion presets** (`enter`, `stagger`, `exit`):

| Preset | Keyframes | Transition |
|---|---|---|
| `rise` | opacity [0,1], y [10,0] | `dur.rise`, `ease.out` |
| `fade` | opacity [0,1] | `dur.small`, `ease.out` |
| `bubble` | opacity [0,1], y [6,0] | `dur.small`, `ease.out` |
| `pop` | opacity [0,1], scale [.92,1] | opacity `dur.small`; scale `spring.gel` |
| `slideL` / `slideR` | opacity [0,1], x [-18,0] / [24,0] | opacity `dur.small`; x `spring.soft` |
| `fab` | opacity [0,1], scale [.5,1] | `spring.gel` (opacity tween) |
| `toast` | opacity [0,1], y [8,0] | `spring.snappy` |
| exit `popOut` | opacity [1,0], scale [1,.95] | `.18`, `ease.press` |
| exit `fadeOut` | opacity [1,0] | `dur.micro`, `ease.press` |
| exit `dismiss` | opacity [1,0], x [0,16] | `.2`, `ease.press` |
| exit `defer` | opacity [1,0], y [0,-6], scale [1,.98] | `.2`, `ease.press` |
| exit `shrinkOut` | opacity [1,0], scale [1,.86] | `dur.small`, `ease.press` |

### 6.6 The global rules, and where each is enforced

| Rule | Enforcement |
|---|---|
| **Either reduce switch gives instant end states** | Every call checks `live()`; `MotionGlobalConfig.skipAnimations` mirrors `reduced()`; `settle()` runs when reduce turns on. GSAP and anime are never loaded while reduced. CSS keeps its two existing kill paths (the media query block at index.html 1105-1120 and aux_prefs' `:root[data-reduce] *` rule), and aux_prefs' WAAPI and Motion gate stays as a second layer. |
| **Paused or skipped while `document.hidden`** | New calls take the instant path. Going hidden runs `settle()`, which jumps to the end with no resume and no catch-up burst. `hold()` fires at once. aux_anim schedules **no** timer and no rAF while hidden: prefetch waits for `visible`. anime's `pauseOnDocumentHidden` stays true. GSAP autosleeps and WebKit stops rAF for hidden pages. CSS loops already stop rendering, and the aurora keeps `body.bg-paused`. |
| **No animation on background refresh** | `refreshHub` (60 s) runs its fills inside `quiet()`. Entrances on re-rendered lists use `{key}`, so each item animates once per page life. `count()` is a no-op when the value did not change. The fixes for E12 and E13 are in section 8. |
| **Only transform and opacity** | Presets touch nothing else. `flip` uses `simple:true` and, in this grid, sizes never change, so Flip writes only transforms. `sequence` builders are reviewed against the list in section 9. **No library writes colour, box-shadow, filter or backdrop-filter, or any layout property.** Colour changes are classes plus CSS transitions, because JS cannot resolve `var()` or `color-mix()` and a theme switch mid-tween would leave stale inline colours. |
| **No layout thrash** | Read, then write. Flip batches its reads in `getState` and `from`, and nothing reads layout inside an `onUpdate`. Count-ups fix `min-width` in `ch`, and `.num` already has `font-variant-numeric:tabular-nums`. |
| **No JS library on the header subtree** | `flip`, `sequence`, `count`, `draw` and `words` refuse targets inside `header` (instant path plus one warning). Header motion stays CSS or Motion/WAAPI, so aux_window's MutationObserver is not hit every frame (E8). |
| **Pointer light stays on the glass** | aux_anim dispatches `hermes:animstart` and `hermes:animend` (CustomEvent, bubbling) **once** per animation on its target or Flip scope. aux_glass adds both names to its existing start and end lists (180-191), which is a two-word change. |
| **CSS owns `translate`, `rotate` and `scale`; JS owns `transform`** | Jiggle moves to `rotate` (C3). `clearProps` clears `transform` only, never the `transform-origin` that aux_glass sets for the gel press. |

### 6.7 Legacy `animate()` shim, and the Motion 11 to 14 migration

index.html keeps its global names so that about 60 call sites need no edit:

```js
const M=window.Motion||null;
const animate=(el,kf,opt)=>{try{const A=window.HermesAnim;
  return A?A.legacy(el,kf,opt):(M?M.animate(el,kf,opt):el.animate(kf,opt));}catch(e){}};
function revealStagger(nodes,step){const A=window.HermesAnim;
  if(A)return A.stagger(nodes,'rise',{step:(step||55)/1000});
  /* old WAAPI body stays as the no-runtime fallback */}
const HA=()=>window.HermesAnim||ANIM_NULL;   // ANIM_NULL: every method instant; hold()->setTimeout
```

`legacy(el, kf, opt)` steps:

1. Copy `opt`. Translate **`easing` to `ease`**:
   - `cubic-bezier(a,b,c,d)` becomes `[a,b,c,d]`, and a four-number array passes through.
   - `'ease-out'`, `'ease-in'`, `'ease-in-out'` and `'linear'` become `easeOut`, `easeIn`, `easeInOut` and `linear`.
   - `'ease'` becomes `[.25,.1,.25,1]`, and anything unknown is dropped.
   - An explicit `ease` is left untouched.
2. Map WAAPI-only keys: `iterations` becomes `repeat`, and `fill` and `direction`
   are dropped.
3. If not `live()`, set `duration=0, delay=0, repeat=0`. This is the same shape
   aux_prefs' gate produces, so `t_c03_reduce_motion.js` keeps its contract.
4. Call `window.Motion.animate`, **looked up per call** so aux_prefs' wrapper stays
   in the path. Without Motion, fall back to `el.animate`.

Motion 14 needs nothing else: the upgrade guide lists no breaking vanilla changes
from 12 to 14. The 12.0 gesture-signature change does not matter here because
nothing calls `press`, `hover` or `inView` today. The keyframe-object-of-arrays
calls (`{opacity:[0,1], transform:[…]}`), element lists, `x`, `scale` and
`rotate` all work unchanged.

**A visible change is expected.** Because `easing` was silently ignored (E5), the
roughly 40 aux calls that pass one will now run on the curve their authors
wrote, usually `cubic-bezier(.22,1,.36,1)` or `(.2,.7,.3,1)`, instead of Motion's
default. That is the intent. C3 and C5 should spot-check the Agent showin cards,
the Settings panels and the pop-out reveals.

The Quick Ask popover does not load aux_anim, because the frozen skeleton has
only three scripts. It talks to Motion 14 directly through the `/motion.min.js`
alias with the same `{duration, easing}` call, which still works with `easing`
ignored as before. aux_quickask.js is not changed in round 3.

---

## 7. Call-site inventory and how each one migrates

| Group | Count | Treatment |
|---|---|---|
| index.html `animate()` / `revealStagger()` | ~24 | Rewritten in C3 to presets, sequences or Flip (section 8). |
| aux modules calling the bare `animate`: aux_memory 6, aux_agent 6, aux_youmodel 3, aux_settings_shell 3, aux_trust 2, aux_recorder 2, aux_desktop 2, aux_config 2, aux_clip 2, aux_watchtower, aux_shortcuts, aux_prefs, aux_metrics, aux_messages, aux_google, aux_foryou, aux_claude_usage 1 each | ~40 | **Unchanged**: they pick up the shim automatically. Only the defective ones change: aux_recorder (C5, E13), aux_agent's timers (C4), and the aux_memory and aux_youmodel meters (C5, E15). |
| expand.js | 3 | Unchanged (shim). |
| aux_glass seg-thumb WAAPI | 1 | Unchanged. aux_glass only gains the two event names. |
| aux_quickask / aux_clip direct `Motion.animate` | 3 | Unchanged (they run in the popover). |

---

## 8. Surface-by-surface choreography

Durations are in seconds and eases are token names. **Reduced or hidden** always
means the final state at once.

### 8.1 Hub boot and refresh (C3)

- **First `renderHub` of the page life**, gated by `once('hub:boot')`: Motion
  `stagger(cards, 'rise')` with step .035, up to 12 animated, total .4 or less.
  It replaces today's 35 ms WAAPI `revealStagger`.
- **`refreshHub` (60 s)**: `HA().quiet(() => fills)`, so no animation. When the
  order changed, the rebuild is also quiet (it is background, not the user).
- **Live System meters**: `.meter i` and similar move from `width` to
  `transform:scaleX(var(--p))` with `transform-origin:left`. The radius moves to
  the track, which already has `overflow:hidden`. The 5 s `liveSystem` writes
  `style.setProperty('--p', p/100)` instead of `style.width`. CSS keeps
  `transition: transform .7s cubic-bezier(.2,.8,.2,1)`. This moves the meters
  from F tier to S tier. The same applies to `.mmmem-bar i`, `.cs-ai-membar i`
  and `.mx-bar i` (whose `mxW` becomes `@keyframes mxS{from{transform:scaleX(0)}}`).

### 8.2 Customize mode (C3), using GSAP Flip

- **Toggle**: no rebuild. Toggle `.editing` on `#wgrid`, flip `draggable` on the
  existing cards, and attach the drag listeners once at build time behind an
  `if(!hubEditing)return` guard. The remove buttons appear through CSS
  (`opacity` and `scale` on `--d-micro`). The **jiggle moves to `rotate`**:
  `@keyframes wjig{0%,100%{rotate:-.35deg}50%{rotate:.35deg}}`.
- **Drag reflow**: in `dragover`, when the computed insertion point differs from
  the current position, and `!HA().isFlipping(over)`, and at least 140 ms have
  passed since the last re-slot:
  `HA().flip(grid, () => insertBefore(...), {duration:.32, exclude:drag})`. The
  hysteresis stops the ping-pong where a card slides under the cursor and
  re-triggers.
- **Remove**:
  1. `HA().exit(card, 'shrinkOut', () => card.style.display='none')`, with the
     `POST /api/layout {op:'remove'}` in parallel.
  2. When both are done, `HA().flip(grid, buildFrom(d))`. The other cards slide
     into the gap over `layout` .42 with `out`.
- **Add (Widget Center)**:
  1. The `.mcard` badge does `spring(badge, {scale:[.6,1]}, 'gel')`.
  2. Behind the modal, `HA().flip(grid, buildFrom(d), {enter:'pop'})`: the new
     card goes from opacity 0 and scale .9 to 1 over .38 on `pop`, and the others
     shift over .42 on `out`.
- **Rendering**: `renderHub(pre, how)` splits into a fetch and a synchronous
  `buildFrom(d)`. `how` is `'boot'`, `'flip'` or `'quiet'`. Every card gets
  `data-flip-id=id`, and Flip matches cards by that id across the `innerHTML`
  rebuild.

### 8.3 View switch: Hub, Agent and Settings (C2 owns the observer, C3 removes the CSS)

The observer from 6.2 step 5 plays `sequence('view', …)` on the view that became
visible:

```
tl.fromTo(view,  {opacity:0}, {opacity:1, duration:.18})
  .from(parts.slice(0,12), {y:8, opacity:0, duration:.32,
        stagger:{each:Math.min(.03, .2/parts.length)}, clearProps:'opacity,transform'}, .02)
```

The `parts` for each view:

| View | Parts |
|---|---|
| `#view-hub` | its `.wgrid>.w` |
| `#view-agent` | its child columns |
| `#view-mind` (Settings) | `#set-rail` plus the visible panel |
| anything else | its element children |

The whole entrance takes .55 s or less. A rapid re-switch ends the old timeline
with `progress(1)`. The outgoing view still hides at once, so no `hidden`
semantics change for `liveSystem` and the other code that tests them. C3 deletes
`animation:fade .35s ease` from `.view` (index.html 612-613) so the two do not
double up. The seg-thumb glide in aux_glass is unchanged.

### 8.4 Chat (C3)

- **Modes** (setChatMode):
  - Full: `enter($('chat-side'), 'slideL')`, then the chat column goes from
    opacity .4 and scale .985 to 1 on `spring.soft`.
  - Hidden: `enter(fab, 'fab')`, then `stagger(cards, 'rise', {step:.02})`.
  - Normal: `enter(chatcol, 'slideR')`.
- **Bubbles**: the user bubble on send, and the bot bubble at its first token,
  use `enter(b, 'bubble')`. History loads run inside `quiet`. Token streaming
  never animates.
- The **approval card** does `enter(box, 'pop', {key:'ap:'+aid})` and the
  **deep card** does `'rise'`.
- The **thinking dots, the caret and the live dot** stay CSS.
- **Toast**: show and hide stay on the CSS transition. Re-triggering while
  visible adds `spring(t, {scale:[1,1.04,1]})`; three keyframes means a tween of
  `dur.small`.

### 8.5 Pop-out and menus (C3)

- **Pop-out**: the entrance stays CSS. The exit becomes
  `popExit=[HA().exit(sheet,'popOut'), HA().exit(wpop,'fadeOut',()=>{wpop.hidden=true})]`,
  and the 170 ms timer goes. Reopening calls `handle.cancel()` on both (the same
  race guard as today).
- **Model menu**: the entrance stays CSS. Closing gains `exit(menu,'popOut')`
  with `dur.micro`; today it hides instantly. The prefs menu is unchanged
  (aux_prefs, through the shim).

### 8.6 Model-swap overlay (C3), using a GSAP sequence and an anime draw

- **show**:
  ```
  sequence('swap', tl => tl
    .fromTo(o,    {opacity:0},           {opacity:1, duration:.2})
    .fromTo(card, {opacity:0, scale:.92}, {opacity:1, scale:1, duration:.45, ease:'hermes.pop'}, 0)
    .from([title, name, sub], {y:6, opacity:0, duration:.3, stagger:.05}, .12))
  ```
  The ring spin stays a CSS keyframe.
- **ready**:
  1. `ring.classList.add('done')`, which clears the border through CSS.
  2. A check `<path>` is inserted and `draw(path, {dur:.36})` runs.
  3. The title fades to 0 over .1, its text is set to "Ready", and it fades back
     over .16.
  4. `hold(T.hold.ready)`.
  5. Exit `{card y→-6, opacity→0, .22 'hermes.press'}` and `{o opacity→0, .3}`.
  6. `done: o.hidden=true`, then inline styles are cleared.
- **slow** (still loading): the ring gets `--warn` through a class, then
  `hold(2.2)`, then the same exit.
- **fail**: the ring gets `--bad` through a class, the card shakes on x through
  `[0,-6,6,-4,4,0]` over .42, then `hold(2.6)`, then the exit. Reduced motion:
  no shake, and the hold still applies because reading time is not motion.
- This **removes every `setTimeout` chain** from E14. `t_b29_swap_overlay.py`
  runs without aux_anim, so it goes through `ANIM_NULL` and `hold` uses
  `setTimeout`; the observable states are unchanged.

### 8.7 Settings Overview growth stats and sparkline (C3), using anime

- `loadCapabilities` calls `HA().count($('g-skills'), n, {delay: i*.06})` for
  the four counters, and `{fmt:kfmt}` for `a-msgs`, `a-in` and `a-out`. From the
  last value to the new one, so re-entering with the same numbers does nothing.
- The sparkline does `draw(svg.querySelector('path[stroke]'))` over .9, the area
  path fades in through `enter(area, 'fade', {delay:.3})`, and the end-point dot
  does `enter(dot, 'pop', {delay:.7})`. It runs once per change in the data
  (`key:'spark:'+hash(by_day)`). `animateSpark()` is deleted.
- `.gstat .v` gets `contain:layout paint`.

### 8.8 Needs-you (C4): Motion exit, then a GSAP Flip

- On an `ok` answer to Done, Snooze or reclassify:
  1. `exit(row, a==='done' ? 'dismiss' : 'defer', () => row.style.visibility='hidden')`.
  2. `reload(host, expanded, {flip:true})`. When the data arrives,
     `HA().flip(host, () => draw(host, d, …), {duration:.32})` runs, matching rows
     by `data-flip-id` (C4 adds that attribute to `row()`), so the remaining rows
     close the gap.
  3. The "Now" count does `count(el, n)`.
- On failure: no animation. The row stays and the message shows, as today.
- Hub-widget refreshes are quiet (8.1).

### 8.9 Onboarding (C4), using a GSAP sequence and anime words

- `paint()` remembers the last painted step. **Only a step change animates.**
  `dir` is `+1` going forward and `-1` going back.
  ```
  sequence('onb', tl => tl
    .from(col, {x:24*dir, opacity:0, duration:.32})
    .from(newRailDot, {scale:.6, duration:.38, ease:'hermes.gel'}, 0))
  ```
  Then `words(stepHeading, {delay:.06})`.
- On the first open, the sheet goes from opacity 0 and scale .98 over `dur.pop`.
  On the summary step, `count()` runs on the figures shown.
- The CSS `onb-in` keyframes and their reduced-motion override are **deleted**
  (aux_onboarding.js 531-536). That fixes E12: toggles and the 2.5 s download
  poll repaint with no motion.

### 8.10 Agent page (C4)

- The ticker crossfade becomes
  `exit(el, 'fadeOut', () => {el.textContent = next; enter(el, 'fade');})`, and
  its `setTimeout(260)` goes.
- `tickSigil` becomes `spring(ring, {rotate:[a-12, a]}, 'snappy')`.
- `blipEkg` becomes `spring(dot, {scale:[1,1.5,1]})`, a tween because it has
  three keyframes.
- Showin cards use `enter(card, 'rise')`. The head icon uses
  `spring(ic, {rotate:[0,360]}, 'gel')`.
- The Record-rail 3 s poll wraps its `loadRecorder()` call in `HA().quiet`; C5
  makes the recorder itself animate only new rows.
- Sigil orbits, blooms and shimmers stay CSS.

### 8.11 Pop-out numbers and paint clean-up (C5)

- **aux_recorder**: rows animate through `enter(row, 'fade', {key:'rec:'+id})`,
  so only new ids move and the 3 s repaint is still (E13). The undo flash becomes
  a `.rec-flash` class whose `::after` overlay runs a CSS opacity keyframe over
  .15, replacing the `backgroundColor` tween.
- **aux_claude_usage pop-out**: the token totals do `count(el, n, {fmt:kfmt})`
  on open. The bar reveal stays (shim).
- **aux_metrics pop-out**: the headline numbers use `count()`.
- **aux_memory and aux_youmodel meters** move from `width` to `scaleX`, as in 8.1.

---

## 9. Performance budget (MotionScore-style; our own rubric, not an official grade)

The tiers below borrow MotionScore's idea, render-pipeline cost per animation.
They are **this repo's rubric**: an official MotionScore audit needs the Motion+
server, which was not authorised in this session.

| Tier | Meaning | Allowed for |
|---|---|---|
| **S** | Compositor only: CSS or WAAPI on transform and opacity | Every Motion preset; CSS loops; popover entrances; meters once they use scaleX |
| **A** | Transform or opacity written by JS each frame, with no layout | Flip (24 targets or fewer), `sequence`, `spring` on SVG |
| **B** | One batched layout read at the start, then A | Flip's `getState` / `from` |
| **C** | Paint each frame on a small area | `count` (8 nodes or fewer, contained), `draw` (2 paths or fewer, each about 300x60 px or smaller) |
| **F** | Layout each frame (width, height, top, left, margin, grid, scroll loops) | **Forbidden.** E15 is fixed in C3 and C5. |

**Budgets** (measured at 60 Hz, 16.7 ms frames; ProMotion is 8.3 ms):

- **Main-thread JS for all running animations**: 3 ms per frame or less at p95.
  Flip's first frame (two read passes) 6 ms or less for 20 cards. No frame over
  20 ms more than twice per choreography.
- **Concurrency caps**:
  - one live sequence per name;
  - Flip 24 targets or fewer (beyond that, instant);
  - stagger 12 or fewer (beyond that, instant);
  - 8 count-ups or fewer;
  - 2 draws or fewer.
- **Glass**: moving `backdrop-filter` surfaces cost a GPU re-blur each frame. A
  full-hub Flip (20 or more cards) passes only if the WebKit probe in section
  10.3 shows 2 or fewer frames over 20 ms. If it fails, Flip adds `.anim-lite`
  for the flight (`backdrop-filter:none` plus a `--glass-solid` colour token
  declared in **all four** palette blocks).
- **Startup**: Motion 14 plus aux_anim parse and evaluate in 10 ms or less. GSAP
  and anime are never on the critical path (prefetch at load + 2.5 s, and never
  under reduced motion).
- **Idle**: zero rAF callbacks 1 s after the last animation ends (GSAP
  autosleep, an idle anime engine, an idle Motion frameloop). No timers and no
  rAF from aux_anim while hidden.
- **Durations**: UI motion lasts .5 s or less, except count .8, draw .9, and the
  swap holds, which are reading time and not motion. Everything is
  interruptible.

---

## 10. Test plan

### 10.1 Unit tier (node, under the offline guard; CI runs it)

Every suite uses `tools/tests/unit/_a11y_dom.js` (`bootDashboard`,
`createWindow`, fake timers in `w.__timers` and `w.__intervals`, rAF mapped to
`setTimeout(16)`, and `opts.media` for the reduce media query).
`_anim_stubs.js` (C2) installs recording stubs: `window.Motion` with `animate`,
`MotionGlobalConfig` and `stagger`; `window.gsap` with timeline, context,
delayedCall, registerPlugin and ticker; `window.Flip` with getState, from and
isFlipping; `window.CustomEase`; and `window.anime` with animate, createDrawable,
splitText, cubicBezier and engine. The stubs return handles whose
finish/complete calls are recorded and **schedule no timers**. Real GSAP and anime
are not used in this tier (E20).

**`t_anim_runtime.js` (C2)**

- **Shape**: the API is frozen and idempotent, and `v===1`.
- **OS reduce** (`media` matches): every method's final state is applied
  synchronously; `exit` calls `done` before returning; no stub receives a
  duration above 0; `skipAnimations===true`; `__timers` and `__intervals` do not
  grow; no `<script src="/vendor/…">` is appended.
- **In-app reduce**: setting `data-reduce` after boot makes the MutationObserver
  flip `skipAnimations` and call `settle()` (running stub handles finish).
  Removing it reverses both. The boot path honours
  `localStorage.hermes_reduce==='1'` before aux_prefs exists.
- **Hidden**: `document.hidden=true` plus `visibilitychange` finishes running
  handles; new calls take the instant path; **aux_anim adds zero timers and zero
  rAF**; `hold()` runs `fn` at once; prefetch is not scheduled. Becoming visible
  schedules exactly one prefetch.
- **`quiet` and `once`**: nesting works, an exception inside `quiet` still
  restores the state, and `once` returns true only the first time.
- **Loader**: the first `flip()` with GSAP absent runs `mutate` synchronously
  and appends the three scripts in order with `async=false`. A simulated `load`
  that sets the stubs makes the next `flip()` go through `Flip.getState/from`.
  A simulated `error` leaves the state at `failed`, with no retry, and later
  calls take the instant path.
- **URL agreement**: every `LIBS` URL is a key of `server.py`'s `VENDOR_JS`
  (parsed from the source text) and an entry of `MANIFEST.json`.
- **`legacy()`**:
  - the easing translation table;
  - not live gives `duration:0, delay:0, repeat:0` (the t_c03 contract);
  - with Motion absent it falls back to `el.animate`;
  - `Motion.animate` is looked up per call, so a wrapper installed later is used.
- **Handles**: `exit` calls `done` exactly once; `cancel()` means `done` is never
  called and the inline styles are removed; `enter` clears its inline styles on
  finish.
- **Header**: `flip`, `sequence` and `count` refuse targets inside `<header>`,
  and `stats().instant.header` increments.
- **Pointer light**: one `hermes:animstart` and one `hermes:animend` per
  animation; with aux_glass loaded, that is enough to make it re-measure.
- **View observer**: un-hiding `.stage>.view` gives one `sequence('view')`; the
  boot view does not; a fast double switch finishes the first.

**`t_anim_tokens.js` (C2)**: parses index.html's `:root` and asserts
`--gel/--press/--pop/--ease-out/--ease-expo === T.ease.*` and
`--d-* === T.dur.*×1000`.

**`t_vendor_manifest.py` (C1)**

- every MANIFEST file exists and matches its sha256 and byte count;
- the version banners match (`GSAP 3.15.0`, `Flip 3.15.0`, `CustomEase 3.15.0`,
  `Anime.js … v4.5.0`);
- every GSAP file still carries `@license` and the standard-license URL;
- `LICENSE.md` exists for each library;
- no `sourceMappingURL` and no `/Users/` in any vendored file;
- `node --check` passes on each;
- `dashboard/motion.min.js` is gone;
- the keys of `VENDOR_JS`, `VENDOR_ALIAS` and `MANIFEST` agree.

**`t_vendor_route.py` (C1)**, using the existing `_http_harness.py` / `server_real` pattern:

- each `VENDOR_JS` key returns 200, `application/javascript`, and
  `public, max-age=31536000, immutable`;
- `/motion.min.js` returns 200 with the body byte-identical to the vendored
  Motion file, plus `no-cache`;
- these return 404: `/vendor/gsap/../../server.py`, `%2e%2e` variants,
  `/vendor/gsap/gsap-3.15.0.min.js.map`, `/vendor/gsap/LICENSE.md`,
  `/vendor/x.js`, `/vendor/`;
- a bad `Host` gets 403 on `/vendor/...` (the guard covers static files).

**`t_anim_hub.js` (C3)**, using the real index.html, aux_anim, aux_glass and the stubs:

- **Boot and refresh**:
  - the first render gives one `stagger` call with 12 or fewer animated;
  - `refreshHub` with the same order and data makes zero stub calls;
  - `refreshHub` with a new order gives a quiet rebuild and no Flip.
- **Customize and layout**:
  - toggling Customize keeps the card nodes (no rebuild), and the jiggle's
    computed `animation` targets `rotate` (mini cascade `computedValue`);
  - remove gives a `shrinkOut` exit, then a Flip;
  - add gives a Flip with `enter`;
  - drag reflow respects the 140 ms hysteresis.
- **View**: the CSS `.view` has no `animation`.
- **Overlays and stats**:
  - swap show, ready and fail each give one `sequence('swap')`, with **no new
    `setTimeout`** while GSAP is present, and `hidden` set in `done`;
  - the Mind counts run on first entry and not on a re-entry with the same
    values;
  - the pop-out exit and its reopen race still work;
  - the meters' computed width does not change while `--p` does.
- **Reduced**: every one of the above reaches its final state at once.
- **Existing suites must stay green**: `t_b29_swap_overlay.py` (through
  `ANIM_NULL`), `t_c02_press_origin.js`, `t_c03_reduce_motion.js`,
  `t_c05_pointer_light.js`.

**`t_anim_surfaces.js` (C4)**

- **Needs-you**:
  - Done gives `exit('dismiss')` then a Flip keyed by `data-flip-id`;
  - Snooze gives `defer`;
  - a failed POST gives no exit and the row stays;
  - the existing `t_b04_needsyou_act_seen.js` and
    `t_c103_needsyou_button_names.js` stay green.
- **Onboarding**:
  - a step change gives `sequence('onb')` plus `words`;
  - a pref toggle or the download-poll repaint gives zero animation calls;
  - the `onb-in` text is gone.
- **Agent**:
  - the crossfade sets no `setTimeout`;
  - the Record-rail poll runs inside `quiet`.

**`t_anim_popouts.js` (C5)**

- the recorder's first paint animates 12 rows or fewer, and a second paint with
  the same ids animates zero;
- the flash uses a class, with no `backgroundColor` tween;
- the usage and metrics pop-outs call `count()`;
- the aux_memory and aux_youmodel meters use scaleX.

### 10.2 Live tier

None is needed. Nothing new reads or writes server state.

### 10.3 Browser tier (optional; Playwright; C2 owns `tools/tests/browser/t_motion_frames.py`)

- It prefers `pw.webkit` when that build is installed (`playwright install
  webkit`, an owner action that needs a download) and falls back to Chromium
  with a `SKIP webkit` note. Today only Chromium builds are installed. WebKit is
  the engine that matters (WKWebView).
- It loads `http://127.0.0.1:7788` with **real** libraries. It mocks `/api/chat`,
  `/api/chat/poll` and the model routes the way the Quick Ask harness does, so a
  model never wakes.
- It drives each choreography through `HermesAnim` and the real UI (Customize
  remove and add with 20 widgets, view switch, swap show, ready and fail, the
  Needs-you act, onboarding next and back, the Mind counts) while a rAF probe
  samples frame deltas. Assertions:
  - the budgets in section 9;
  - `document.getAnimations()` during the Motion presets shows WAAPI animations
    on transform and opacity only;
  - no rAF callbacks 1 s after the last animation;
  - under `emulate_media(reduced_motion='reduce')` every choreography is done
    within one frame;
  - with `data-reduce` set it is the same;
  - a hidden-page simulation (`Object.defineProperty(document,'hidden')` plus
    `visibilitychange`) settles everything.
- It also runs the `.anim-lite` gate for the glass Flip.
- An official MotionScore runtime audit (`npx motionscore <url> --agent`) is
  something the owner can run optionally. It needs npx and network access, and
  saving needs Motion+; it is never part of CI.

---

## 11. Rollout order

1. **C1** (vendor and serve) and **C2** (runtime) land first. They can be built
   in parallel, and C2's tests need only the stubs.
2. **C3, C4 and C5** are built in parallel against the API in 6.5. Each must stay
   correct with `HermesAnim` absent (`ANIM_NULL` or `if(!A)`), present but with
   libraries missing (instant), and live.
3. Integration: C1, then C2, then C3, then C4 and C5. Then run the unit tier.
   Then a manual pass in the app (reload with Cmd+R after the dashboard restarts),
   with Reduce motion on and off, the OS setting on, the window minimised during
   a swap, and a 20-widget Customize session. Then the optional browser probe.

---

## 12. Round-3 clusters (owned files are disjoint)

### C1: vendor, serve, provenance and docs

**Owns:**

- `dashboard/vendor/**` (new): `README.md`, `MANIFEST.json`, the five
  library files and the three `LICENSE.md` files
- `dashboard/motion.min.js` (delete)
- `dashboard/server.py` (the static block only: `VENDOR_JS`, `VENDOR_ALIAS`,
  `_static_js`)
- `tools/vendor-anim.sh` (new)
- `tools/tests/unit/t_vendor_manifest.py` (new)
- `tools/tests/unit/t_vendor_route.py` (new)
- `tools/tests/unit/t_b17_frame_headers.py` (the exclusion name)
- `.github/workflows/ci.yml` (drop the `motion.min.js` skip)
- `tools/release.sh` (drop the skip)
- `README.md` (a third-party notices section with the GSAP carve-out)
- `CLAUDE.md` (one "Motion system" architecture bullet: the roles, vendor paths,
  the API, the rules, how to bump a version, and the Quick Ask alias)

**Done when** both new suites pass, CI's syntax and home-path steps are clean,
and `curl -sI localhost:7788/vendor/gsap/gsap-3.15.0.min.js` shows the immutable
header.

### C2: runtime module

**Owns:**

- `dashboard/aux_anim.js` (new)
- `dashboard/aux_glass.js` (only the two `hermes:anim*` event names)
- `tools/tests/unit/_anim_stubs.js` (new)
- `tools/tests/unit/t_anim_runtime.js` (new)
- `tools/tests/unit/t_anim_tokens.js` (new)
- `tools/tests/browser/t_motion_frames.py` (new)

**Work:** everything in section 6 (the loader, tokens, presets, rules, handles,
the view-entrance observer and the legacy translation) and section 10.3.
**Done when** `t_anim_runtime.js` and `t_anim_tokens.js` pass, `t_c05` still
passes with the new events, and aux_anim has no `setInterval` (grep).

### C3: index.html surfaces

**Owns:**

- `dashboard/index.html`
- `tools/tests/unit/t_anim_hub.js` (new)
- adjustments, only if the extracted functions need `HA` / `ANIM_NULL`:
  `tools/tests/unit/t_b29_swap_overlay.py`, `tools/tests/unit/_chat_ui.py`,
  `tools/tests/unit/t_c03_reduce_motion.js`

**Work:**

- the script tags (6.1);
- the CSS tokens `--ease-out`, `--ease-expo` and `--d-*`;
- delete `.view{animation:fade}`;
- the jiggle on `rotate`;
- the meters on `scaleX`, with the 5 s `liveSystem` writing `--p`;
- the `animate`, `revealStagger` and `HA()` / `ANIM_NULL` shims (6.7);
- `renderHub` split into fetch plus `buildFrom(d)` with boot, flip and quiet;
- Customize with no rebuild, drag-reflow Flip, remove and add;
- `refreshHub` quiet;
- the pop-out and model-menu exits;
- chat modes, bubbles, the approval and deep cards, and the toast nudge;
- the model-swap sequence with `hold` and a check `draw`;
- the Settings Overview counts and the sparkline `draw`
  (sections 8.1-8.7).

**Done when** `t_anim_hub.js` passes and every existing index.html suite stays
green.

### C4: Needs-you, onboarding and Agent

**Owns:**

- `dashboard/aux_needsyou.js`
- `dashboard/aux_onboarding.js`
- `dashboard/aux_agent.js`
- `tools/tests/unit/t_anim_surfaces.js` (new)
- adjustments if needed: `tools/tests/unit/t_b04_needsyou_act_seen.js`,
  `tools/tests/unit/t_c103_needsyou_button_names.js`

**Work:** sections 8.8 (`data-flip-id` on rows, exit then Flip on an `ok`
answer, the Now count), 8.9 (a step-change-only sequence, `words`, the first-open
sheet, the summary counts, and deleting the `onb-in` CSS) and 8.10 (the
crossfade without a timer, the sigil, EKG and showin calls through `HA()`, and
the Record-rail poll inside `quiet`). **Done when** `t_anim_surfaces.js` passes,
the existing Needs-you and onboarding suites are green, and the browser onboarding
test `t_onboarding_ui.py` still passes when run.

### C5: pop-out numbers and paint clean-up

**Owns:**

- `dashboard/aux_recorder.js`
- `dashboard/aux_memory.js`
- `dashboard/aux_youmodel.js`
- `dashboard/aux_claude_usage.js`
- `dashboard/aux_metrics.js`
- `tools/tests/unit/t_anim_popouts.js` (new)

**Work:** section 8.11: recorder rows animate only when new, the flash moves to a
CSS class, the usage and metrics pop-outs get count-ups, and the meters move to
scaleX. **Done when** `t_anim_popouts.js` passes and the existing recorder,
memory and youmodel suites are green.

---

## 13. Risks and open questions

1. **Three engines in one app.** This is redundant by construction. The role
   table, the lazy loading (GSAP and anime are fetched only once they are
   needed, and never under reduced motion) and the "one kind of change, one
   owner" rule keep the overlap contained. Motion at startup still grows from 64
   to 172 KB.
2. **Visible easing change.** About 40 legacy calls finally get the `easing` they
   asked for (E5), which Motion 11 ignored. Spot-check them.
3. **GSAP license.** It is not OSI, and Webflow can modify or revoke it. The repo
   is MIT and public, so the carve-out notice is required, and a no-code
   animation builder must never be shipped. The license page was offline on
   2026-10-26; the text came from the ScanCode LicenseDB mirror, and the owner
   should re-read the live page.
4. **WebKit backdrop-filter cost** during a full-hub Flip has not been measured.
   It is gated by the browser probe, with `.anim-lite` as the fallback.
   Playwright WebKit is not installed on this Mac; JavaScriptCore is not V8, and
   the startup and parse numbers here are V8 proxies.
5. **The unit tier cannot run real GSAP or anime** in the mini DOM (E20). Real-library
   coverage exists only in the optional browser tier.
6. **Drag plus Flip.** The HTML5 drag-and-drop ping-pong is the classic failure. Hysteresis and
   `isFlipping` mitigate it; tune the 140 ms by hand.
7. **View-entrance observer.** It is a MutationObserver, so a future view that toggles
   `hidden` on a `.stage>.view` gets an entrance for free. A view that should not
   animate must opt out with `data-anim="off"` (C2 supports this attribute).
8. **Quick Ask popover.** It moves to Motion 14 through the alias but keeps its
   ignored `easing` options. It is harmless and out of scope; `main.swift` stays
   frozen.
9. **No official MotionScore grade was produced.** The Motion+ MCP server needs
   authorisation (`/mcp`) and its audit method is gated. Section 9 is this
   repo's own rubric.
10. **Cross-cluster contract.** C3, C4 and C5 code against section 6.5 before C2
    exists. The `ANIM_NULL` and `if(!A)` paths are the safety net, and a naming
    drift shows up as a failure in `t_anim_hub.js` or `t_anim_surfaces.js` at
    integration.
