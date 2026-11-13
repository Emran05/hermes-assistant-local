#!/usr/bin/env python3
"""Regression test for E128 — docs/audits/2026-11-09-codebase-audit.md.

update.sh's comments described SHA256SUMS as protection against "an attacker
with the release URL" and as a "signed-ish list". The sums are built, published
and fetched alongside the tarball, by the same job, from the same host and with
the same token, so they detect corruption, not tampering, and nothing is signed.
The comments now say so; the check itself (required, never skipped, not
overridable by --force) is unchanged.
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRC = open(os.path.join(REPO, "update.sh")).read()
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  -- %s" % (name, extra))


flat = re.sub(r"\s*\n\s*#\s*", " ", SRC)          # comments joined across lines
for bad in ("signed-ish", "signature check", "That is the one place an attacker"):
    check("no overstated claim: %r" % bad, bad not in SRC)
check("says it detects corruption, not tampering",
      "DETECTS CORRUPTION" in flat and "does NOT detect TAMPERING" in flat)
check("says nothing is signed and names the trust root",
      "Nothing here is signed" in flat and "release repo slug plus GitHub's TLS" in flat)
check("the header says the same", "detects corruption, not tampering (nothing is signed)" in flat)
check("the check is still mandatory (no sums -> refusal, not overridable)",
      "publishes no SHA256SUMS asset — refusing to install an unverified tarball "
      "(not overridable with --force)" in SRC and 'SHA256 mismatch for $BASE' in SRC)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
