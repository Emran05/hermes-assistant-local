# GSAP 3.15.0: license notice (NOT MIT)

The JavaScript files in this directory are GreenSock's GSAP 3.15.0 browser
builds, copied byte for byte from the npm package `gsap@3.15.0`:

    dist/gsap.min.js        ->  gsap-3.15.0.min.js
    dist/Flip.min.js        ->  Flip-3.15.0.min.js
    dist/CustomEase.min.js  ->  CustomEase-3.15.0.min.js

They are NOT covered by this repository's MIT license. They are licensed
under GreenSock's GSAP Standard "No Charge" License (Webflow),
effective 2025-04-30:

    https://gsap.com/standard-license

The package ships no LICENSE file. Its package.json `license` field reads
"Standard 'no charge' license: https://gsap.com/standard-license.", and
each file's banner says the code is subject to the terms at that address.

Conditions this repository honours:

- Every file's `/*! ... @license Copyright ..., GreenSock ... */` banner stays
  intact. The files are never edited, re-minified or concatenated;
  `dashboard/vendor/MANIFEST.json` pins each one's sha256 and the unit test
  `tools/tests/unit/t_r3_vendor_manifest.py` enforces it.
- Prohibited use, in one line: GSAP must not power a tool that lets people
  build visual animations without code in a way that competes with Webflow,
  so this project never ships a "design your own animation" editor on it.
- Webflow may change or withdraw these terms. The version is pinned, and the
  effective date above is the one this copy was reviewed against.

This is an engineering reading of the terms, not legal advice. Re-read the
live page before redistributing these files in a new context.

Copyright (c) 2008-2026, GreenSock. All rights reserved.
