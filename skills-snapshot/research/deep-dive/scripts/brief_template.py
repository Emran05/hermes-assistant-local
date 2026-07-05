#!/usr/bin/env python3
"""brief_template.py — render and validate deep-dive research briefs.

Two modes:

  Render a skeleton to fill in:
      python3 brief_template.py --skeleton "Topic of the dive"

  Validate a finished brief (reads stdin, exit 0 = valid, exit 1 = rejected):
      cat brief.md | python3 brief_template.py
      python3 brief_template.py --validate < brief.md

Validation contract (the skill's hard rules, enforced mechanically):
  1. All five sections present: TL;DR, Findings, Contested, Sources, Confidence.
  2. TL;DR is at most 7 non-empty lines.
  3. EVERY Findings bullet carries at least one http(s) URL. No URL, no claim.
  4. At least one Findings bullet is tagged [HEADLINE], and every [HEADLINE]
     bullet carries >= 2 URLs from >= 2 distinct hosts (independent sources).
  5. Sources section lists >= 2 distinct hosts overall.

The validator REFUSES (exit 1, reasons on stderr) rather than emitting a
broken brief. stdin is accepted so drills can pipe a pasted brief straight in.
"""

import re
import sys
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s\)\]>\"'`]+")

SKELETON = """# Deep Dive: {topic}

## TL;DR
- (<= 7 lines total. Answer first, caveats second.)

## Findings
- [HEADLINE] <main claim sentence> (<url-1>) (<url-2 from a DIFFERENT host>)
- <supporting claim, one sentence> (<url>)
- <supporting claim, one sentence> (<url>)

## Contested
- <claim sources disagree on>: <side A> (<url-A>) vs <side B> (<url-B>)
- (write "None" if nothing is contested)

## Sources
- <url> — <one-line note: what it is, why trusted>
- <url> — <one-line note>

## Confidence
<High|Medium|Low> — <one sentence: why, incl. staleness caveat for date-sensitive claims>

## Watch
- <optional: 'what to watch' bullets -> propose watchtower rules>

## Self-Report
- tool calls: <n> | web_extract: <n> | dead links found: <n> | recorder window: <start>-<end>
"""

SECTION_RE = re.compile(r"^#{1,3}\s*(.+?)\s*$")
REQUIRED = ["tl;dr", "findings", "contested", "sources", "confidence"]


def split_sections(text):
    sections, current, buf = {}, None, []
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            if current is not None:
                sections[current] = buf
            current = m.group(1).strip().lower().rstrip(":")
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = buf
    return sections


def hosts(urls):
    out = set()
    for u in urls:
        try:
            h = urlparse(u).netloc.lower()
            if h.startswith("www."):
                h = h[4:]
            if h:
                out.add(h)
        except ValueError:
            pass
    return out


def bullets(lines):
    """Group bullet lines (with continuations) into single logical bullets."""
    out, cur = [], None
    for ln in lines:
        s = ln.strip()
        if s.startswith(("- ", "* ")) or s in ("-", "*"):
            if cur:
                out.append(cur)
            cur = s.lstrip("-* ").strip()
        elif s and cur is not None:
            cur += " " + s
    if cur:
        out.append(cur)
    return out


def validate(text):
    errors = []
    secs = split_sections(text)

    def find(name):
        for k in secs:
            if name in k:
                return secs[k]
        return None

    for name in REQUIRED:
        if find(name) is None:
            errors.append("missing section: %s" % name.upper())
    if errors:
        return errors  # structure is broken; per-section checks would mislead

    tldr = [l for l in find("tl;dr") if l.strip()]
    if len(tldr) > 7:
        errors.append("TL;DR has %d non-empty lines (max 7)" % len(tldr))

    fb = bullets(find("findings"))
    if not fb:
        errors.append("Findings has no bullets")
    headline_count = 0
    for i, b in enumerate(fb, 1):
        urls = URL_RE.findall(b)
        if not urls:
            errors.append("Findings bullet %d has NO URL (no URL, no claim): %r"
                          % (i, b[:80]))
        if "[HEADLINE]" in b.upper():
            headline_count += 1
            h = hosts(urls)
            if len(urls) < 2 or len(h) < 2:
                errors.append(
                    "HEADLINE bullet %d needs >=2 URLs from >=2 distinct hosts "
                    "(got %d url(s), %d host(s))" % (i, len(urls), len(h)))
    if headline_count == 0:
        errors.append("no [HEADLINE] bullet in Findings (tag the main claim)")

    src_urls = URL_RE.findall("\n".join(find("sources")))
    if len(hosts(src_urls)) < 2:
        errors.append("Sources lists %d distinct host(s); need >=2 independent"
                      % len(hosts(src_urls)))

    conf = " ".join(find("confidence")).strip()
    if not re.search(r"\b(high|medium|low)\b", conf, re.I):
        errors.append("Confidence must state High/Medium/Low")

    return errors


def main():
    args = sys.argv[1:]
    if args and args[0] == "--skeleton":
        topic = " ".join(args[1:]) or "<topic>"
        sys.stdout.write(SKELETON.format(topic=topic))
        return 0
    if args and args[0] not in ("--validate",):
        sys.stderr.write(__doc__)
        return 2
    text = sys.stdin.read()
    if not text.strip():
        sys.stderr.write("REJECTED: empty input on stdin\n")
        return 1
    errors = validate(text)
    if errors:
        sys.stderr.write("REJECTED (%d problem(s)):\n" % len(errors))
        for e in errors:
            sys.stderr.write("  - %s\n" % e)
        return 1
    n_urls = len(set(URL_RE.findall(text)))
    print("VALID brief: %d unique URLs cited." % n_urls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
