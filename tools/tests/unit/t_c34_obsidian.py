#!/usr/bin/env python3
"""C34 (docs/audits/2026-10-21-codebase-audit.md) — the two Obsidian exporters.

  obsidian_sync.py  deleted every previous community note BEFORE it looked at
                    the new graph, so an empty or broken graph.json (or a write
                    that failed half-way) wiped the folder — and an empty graph
                    still exited 0.  Wikilinks used the untruncated name while
                    filenames were cut to 80 characters, which also cut the
                    " (cN)" suffix: a long label's links were dead and two long
                    labels shared one file.
  obsidian_daily.py rewrote the owner's daily note in place (open "w"
                    truncates first), so a failed write lost the whole note.

Runs the REAL scripts as subprocesses against throwaway vaults.  A lone UTF-16
surrogate in the input (valid JSON, unencodable as UTF-8) makes the write fail
for real, mid-run, on both the old and the new code.  Offline.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SYNC = os.path.join(REPO, "obsidian_sync.py")
DAILY = os.path.join(REPO, "obsidian_daily.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="t-c34-")
LONG_A = "Dashboard server request routing and the aux module loader plus every handler it exec's"
LONG_B = "Dashboard server request routing and the aux module loader plus the settings transaction"


def run(script, *args):
    env = dict(os.environ, HOME=os.path.join(TMP, "home"))
    r = subprocess.run([sys.executable, script] + list(args), capture_output=True, text=True,
                       env=env, timeout=60)
    return r.returncode, r.stdout + r.stderr


def graph(path, comms, labels=None, raw=None):
    """comms: {cid: [node ids]}; one cross-community link between the first two."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if raw is not None:
        open(path, "w").write(raw)
    else:
        nodes, links = [], []
        for cid, ids in comms.items():
            for i in ids:
                nodes.append({"id": i, "label": i, "community": cid, "source_file": "f%s.py" % cid})
        cids = list(comms)
        for a, b in zip(cids, cids[1:]):
            links.append({"source": comms[a][0], "target": comms[b][0]})
        json.dump({"nodes": nodes, "links": links}, open(path, "w"))
    lab = os.path.join(os.path.dirname(path), ".graphify_labels.json")
    if labels is not None:
        json.dump(labels, open(lab, "w"))
    elif os.path.exists(lab):
        os.remove(lab)


def notes(vault):
    d = os.path.join(vault, "Hermes Code Graph", "communities")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def links_of(text):
    return re.findall(r"\[\[([^\]]+)\]\]", text)


def t_sync():
    print("\nC34a. obsidian_sync: names, links and the stale-note sweep")
    vault = os.path.join(TMP, "vault")
    os.makedirs(vault)
    gj = os.path.join(TMP, "g1", "graph.json")
    graph(gj, {0: ["a", "b"], 1: ["c"], 2: ["d"]}, labels={"0": LONG_A, "1": LONG_B, "2": "Short"})
    rc, out = run(SYNC, vault, gj)
    check("a good graph syncs (exit 0)", rc == 0, out)
    names = notes(vault)
    check("one note per community — two long labels do not share a file", len(names) == 3, names)
    check("every note name keeps its (cN) suffix and fits 80 chars",
          all(re.search(r" \(c\d\)\.md$", n) and len(n) - 3 <= 80 for n in names), names)
    cdir = os.path.join(vault, "Hermes Code Graph", "communities")
    stems = {n[:-3] for n in names}
    texts = [open(os.path.join(cdir, n)).read() for n in names]
    texts.append(open(os.path.join(vault, "Hermes Code Graph", "Hermes Code Graph.md")).read())
    every = [lk for t in texts for lk in links_of(t)]
    check("premise: the notes and the index link to each other", len(every) >= 5, every)
    dead = [lk for lk in every if lk not in stems]
    check("every [[wikilink]] resolves to a note that exists (no dead long-label links)",
          dead == [], dead)

    # the owner's own files in the folder, and a note the next graph drops
    mine = os.path.join(cdir, "my-notes.txt")
    open(mine, "w").write("mine")
    graph(gj, {0: ["a", "b"], 2: ["d"]}, labels={"0": LONG_A, "2": "Short"})
    rc, out = run(SYNC, vault, gj)
    after = notes(vault)
    check("re-sync without community 1 -> exit 0", rc == 0, out)
    check("the dropped community's note is removed, the others kept",
          len([n for n in after if n.endswith(".md")]) == 2
          and not any("(c1)" in n for n in after), after)
    check("a non-note file in the folder is left alone", os.path.exists(mine), after)
    check("no temp files are left behind", not glob.glob(os.path.join(vault, "**", ".*tmp"), recursive=True),
          glob.glob(os.path.join(vault, "**", "*"), recursive=True))

    before = {n: open(os.path.join(cdir, n), "rb").read() for n in notes(vault)}
    moc = os.path.join(vault, "Hermes Code Graph", "Hermes Code Graph.md")
    moc_before = open(moc, "rb").read()
    for what, raw in (("an empty graph", '{"nodes": [], "links": []}'),
                      ("a graph with no nodes key", "{}"),
                      ("a graph that is not an object", "[]"),
                      ("broken JSON", '{"nodes": [')):
        graph(gj, {}, raw=raw)
        rc, out = run(SYNC, vault, gj)
        check("%s -> exit 1" % what, rc == 1, (rc, out[-300:]))
        check("  and every previous note is still there, unchanged",
              {n: open(os.path.join(cdir, n), "rb").read() for n in notes(vault)} == before
              and open(moc, "rb").read() == moc_before, notes(vault))

    # a write that fails half-way (a lone surrogate cannot be encoded)
    # the bad community comes FIRST, so the old delete-then-write order
    # would have nothing re-written before it fails
    graph(gj, {5: ["\ud800bad"], 0: ["a"], 2: ["d"]}, labels={"0": LONG_A, "2": "Short"})
    rc, out = run(SYNC, vault, gj)
    check("premise: a note that cannot be written fails the run", rc != 0, out[-300:])
    check("  and the notes already in the vault were NOT wiped first",
          all(os.path.exists(os.path.join(cdir, n)) for n in before), notes(vault))
    check("  and no temp file is left behind",
          not glob.glob(os.path.join(cdir, ".*tmp")), os.listdir(cdir))


def t_daily():
    print("\nC34b. obsidian_daily: the owner's daily note is replaced atomically")
    vault = os.path.join(TMP, "dvault")
    os.makedirs(os.path.join(vault, "Daily"))
    bj = os.path.join(TMP, "briefing.json")
    ts = 1793030400                     # a fixed day
    day = __import__("time").strftime("%Y-%m-%d", __import__("time").localtime(ts))
    note = os.path.join(vault, "Daily", day + ".md")
    mine = "---\ndate: %s\n---\n\n# %s\n\nMY OWN NOTES — must survive\n" % (day, day)
    open(note, "w").write(mine)
    os.chmod(note, 0o640)
    json.dump({"generated_at": ts, "reply": "Brief one."}, open(bj, "w"))
    rc, out = run(DAILY, vault, bj)
    txt = open(note).read()
    check("the brief is filed (exit 0)", rc == 0 and "Brief one." in txt, (rc, out))
    check("the owner's text survives", "MY OWN NOTES — must survive" in txt, txt)
    check("the note keeps its mode", (os.stat(note).st_mode & 0o777) == 0o640, oct(os.stat(note).st_mode))
    json.dump({"generated_at": ts, "reply": "Brief two."}, open(bj, "w"))
    rc, out = run(DAILY, vault, bj)
    txt = open(note).read()
    check("a re-run replaces only the Hermes block", rc == 0 and "Brief two." in txt
          and "Brief one." not in txt and txt.count("hermes:brief:start") == 1, txt)

    good = open(note, "rb").read()
    open(bj, "w").write('{"generated_at": %d, "reply": "bad \\ud800 brief"}' % ts)
    rc, out = run(DAILY, vault, bj)
    check("premise: a brief that cannot be written fails the run", rc != 0, out[-300:])
    check("  and the daily note is byte-for-byte what it was (not truncated)",
          open(note, "rb").read() == good, open(note, "rb").read()[:200])
    check("  and no temp file is left behind",
          not glob.glob(os.path.join(vault, "Daily", ".*tmp")), os.listdir(os.path.join(vault, "Daily")))
    json.dump({"generated_at": ts + 86400 * 3, "reply": "New day."}, open(bj, "w"))
    rc, out = run(DAILY, vault, bj)
    made = [n for n in os.listdir(os.path.join(vault, "Daily")) if n != day + ".md" and n.endswith(".md")]
    check("a new day's note is created", rc == 0 and len(made) == 1, (rc, out, made))


def main():
    t_sync()
    t_daily()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
