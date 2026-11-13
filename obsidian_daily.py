#!/usr/bin/env python3
"""obsidian_daily.py — file the day's World Brief into a dated Obsidian note
(<vault>/Daily/YYYY-MM-DD.md), so the vault becomes a searchable life-log
alongside the code graph.

Idempotent + non-destructive: rewrites only the delimited Hermes block, so
anything YOU add to the daily note survives. Fired by a launchd WatchPaths
agent whenever the brief regenerates.

Usage: obsidian_daily.py <vault_dir> [briefing_json]
"""
import json
import os
import sys
import tempfile
import time

BRIEF_DEFAULT = os.path.expanduser("~/.hermes/dashboard/briefing.json")
START = "%% hermes:brief:start %%"
END = "%% hermes:brief:end %%"


def _t12(ts):
    """12-hour clock, no leading zero (e.g. '8:15 AM')."""
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")


def _atomic_write(path, text):
    """tmp in the same folder + os.replace, keeping the note's mode: a crash
    or a second run mid-write must never leave half of YOUR daily note.
    A symlinked note is written through, as a plain open() did."""
    path = os.path.realpath(path)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _block(brief):
    gen = brief.get("generated_at")
    when = _t12(gen) if gen else "today"
    body = (brief.get("reply") or "").strip().replace("\r\n", "\n")
    # The markers are ours: a brief that happens to contain one would nest
    # them and leave generated text outside the block on the next run.
    body = body.replace(START, "").replace(END, "")
    return START + "\n> Filed by Hermes at " + when + "\n\n" + body + "\n" + END


def _find_line(txt, mark, pos):
    """Index of the next `mark` that is a whole line of its own (as every
    block this script writes has it), or -1.  A marker the owner quoted inside
    a line of prose is the owner's text, never a block edge."""
    while True:
        i = txt.find(mark, pos)
        if i < 0:
            return -1
        j = i + len(mark)
        if (i == 0 or txt[i - 1] == "\n") and (
                j == len(txt) or txt[j] == "\n" or txt.startswith("\r\n", j)):
            return i
        pos = i + 1


def _spans(txt):
    """(start, end) of every well-formed generated block: a START line followed
    by an END line with NO other START line (and so no other END) in between.
    An orphan START — its END deleted by hand — never opens a span, so a match
    can never run from it across the owner's own text to a later block's END
    (E32)."""
    spans, pos = [], 0
    while True:
        s = _find_line(txt, START, pos)
        if s < 0:
            return spans
        e = _find_line(txt, END, s + len(START))
        if e < 0:
            return spans
        s2 = _find_line(txt, START, s + len(START))
        if 0 <= s2 < e:          # another START before this one's END: orphan
            pos = s2
            continue
        spans.append((s, e + len(END)))
        pos = e + len(END)


def _outside(txt, spans):
    out, pos = [], 0
    for s, e in spans:
        out.append(txt[pos:s])
        pos = e
    out.append(txt[pos:])
    return out


def _refile(orig, block):
    """The new note text, or None.  Replaces only well-formed generated blocks;
    with none (missing, orphaned, reordered markers) a fresh block is APPENDED
    and every existing byte stays put.  Whatever happens, the text outside the
    generated blocks must come out byte-identical, or nothing is written."""
    spans = _spans(orig)
    nl = "\r\n" if "\r\n" in orig else "\n"
    block = block.replace("\n", nl)
    if spans:
        parts = _outside(orig, spans)
        txt = parts[0] + "".join(block + p for p in parts[1:])
        new_spans, pos = [], len(parts[0])
        for p in parts[1:]:
            new_spans.append((pos, pos + len(block)))
            pos += len(block) + len(p)
        ok = _outside(txt, new_spans) == parts
    else:
        # A blank line before the block; never trim the owner's own text.
        sep = "" if not orig else (nl if orig.endswith(nl) else nl + nl)
        txt = orig + sep + block + nl
        ok = txt.startswith(orig)
    return txt if ok else None


def build(vault, briefing_json):
    try:
        brief = json.load(open(briefing_json))
    except Exception as e:
        print("no briefing (%s); skipping" % e, file=sys.stderr)
        return None
    if not (brief.get("reply") or "").strip():
        print("briefing empty; skipping", file=sys.stderr)
        return None
    ts = brief.get("generated_at")
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    ddir = os.path.join(vault, "Daily")
    os.makedirs(ddir, exist_ok=True)
    path = os.path.join(ddir, day + ".md")
    block = _block(brief)
    if os.path.exists(path):
        try:
            # newline="" keeps CRLF notes byte-for-byte; a note that is not
            # UTF-8 is not ours to rewrite.
            with open(path, encoding="utf-8", newline="") as f:
                orig = f.read()
        except (OSError, UnicodeDecodeError) as e:
            print("daily note unreadable (%s); not touching it" % e, file=sys.stderr)
            return None
        txt = _refile(orig, block)
        if txt is None:
            print("refusing to write: the note's own text would change", file=sys.stderr)
            return None
    else:
        txt = "---\ndate: %s\ntags: [hermes/daily]\n---\n\n# %s\n\n" % (day, day) + block + "\n"
    _atomic_write(path, txt)
    return path


def main():
    if len(sys.argv) < 2:
        print("usage: obsidian_daily.py <vault_dir> [briefing_json]", file=sys.stderr)
        return 2
    vault = os.path.expanduser(sys.argv[1])
    briefing = sys.argv[2] if len(sys.argv) > 2 else BRIEF_DEFAULT
    if not os.path.isdir(vault):
        print("vault dir not found: %s" % vault, file=sys.stderr)
        return 1
    path = build(vault, briefing)
    if path:
        print("Filed brief -> %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
