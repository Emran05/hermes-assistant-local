#!/usr/bin/env python3
"""obsidian_sync.py — deploy the Graphify code knowledge graph into an Obsidian
vault as native, interlinked markdown notes. One note per community (subsystem)
plus a Map-of-Content index; cross-community edges become [[wikilinks]], so
Obsidian's own graph view renders the repo's architecture.

Usage:  obsidian_sync.py <vault_dir> [graph_json]
Writes ONLY under "<vault_dir>/Hermes Code Graph/" (a folder this tool owns and
is the only thing it ever clears). Idempotent: safe to re-run on every rebuild.
Every note is built in memory first and an empty or unreadable graph is
refused (exit 1) with the previous notes left in place; notes are written
tmp + os.replace, and only notes the new graph no longer has are removed.
"""
import json
import os
import re
import sys
import tempfile
import unicodedata

SUBFOLDER = "Hermes Code Graph"
COMM_DIR = "communities"


def _safe(name):
    """Filename/wikilink-safe: strip the chars Obsidian can't resolve in links."""
    name = re.sub(r'[\\/:*?"<>|\[\]#^]', "-", str(name or "untitled"))
    return (name.strip().strip(".") or "untitled")[:80]


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _atomic_write(path, text):
    """tmp in the same folder + os.replace: Obsidian never sees half a note."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _fold(name):
    return unicodedata.normalize("NFC", name).casefold()


class GraphError(Exception):
    """The graph cannot be synced; the vault is left exactly as it was."""


def _load(graph_json):
    try:
        with open(graph_json) as f:
            g = json.load(f)
    except (OSError, ValueError) as e:
        raise GraphError("graph.json unreadable: %s" % e)
    if not isinstance(g, dict):
        raise GraphError("graph.json is not a graph object")
    labels = {}
    try:
        labels = json.load(open(os.path.join(os.path.dirname(graph_json),
                                              ".graphify_labels.json")))
    except Exception:
        pass
    return g, labels


def build(vault, graph_json):
    g, labels = _load(graph_json)
    nodes = g.get("nodes") if isinstance(g.get("nodes"), list) else []
    nodes = [n for n in nodes if isinstance(n, dict)]
    links = g.get("links") if isinstance(g.get("links"), list) else []
    links = [lk for lk in links if isinstance(lk, dict)]
    if not nodes:
        raise GraphError("graph.json has no nodes (run: graphify update .)")
    if not isinstance(labels, dict):
        labels = {}
    byid = {n.get("id"): n for n in nodes}

    members = {}                       # community -> [nodes]
    for n in nodes:
        members.setdefault(n.get("community"), []).append(n)

    cadj = {}                          # community -> {neighbor communities}
    for lk in links:
        s, t = byid.get(lk.get("source")), byid.get(lk.get("target"))
        if not s or not t:
            continue
        cs, ct = s.get("community"), t.get("community")
        if cs is None or ct is None or cs == ct:
            continue
        cadj.setdefault(cs, set()).add(ct)
        cadj.setdefault(ct, set()).add(cs)

    def cname(cid):
        # The note's NAME — its filename and every [[link]] to it.  The label
        # is cut to leave room for the " (cN)" suffix: cutting the whole name
        # at 80 afterwards dropped the suffix from the filename only, so a
        # long label's links were dead and two long labels shared one file.
        suffix = " (c%s)" % re.sub(r'[\\/:*?"<>|\[\]#^]', "-", str(cid))
        label = _safe(labels.get(str(cid), "Community %s" % cid))
        return _safe(label[:max(1, 80 - len(suffix))].rstrip() + suffix)

    out = os.path.join(vault, SUBFOLDER)
    cdir = os.path.join(out, COMM_DIR)
    notes = {}                         # filename -> text, built before any write
    for cid, mem in members.items():
        title = cname(cid)
        files = {}
        for n in mem:
            files.setdefault(n.get("source_file") or "?", []).append(
                str(n.get("label") or n.get("id")))
        lines = ["---", "tags: [hermes/codegraph]", "community: %s" % cid,
                 "members: %d" % len(mem), "---", "",
                 "# %s" % title, "",
                 "> Subsystem in the Hermes code graph — %d nodes." % len(mem),
                 "", "## Files & symbols", ""]
        for f in sorted(files):
            syms = ", ".join("`%s`" % s for s in sorted(set(files[f]))[:20])
            lines.append("- **`%s`** — %s" % (f, syms))
        neigh = sorted(cadj.get(cid, []))
        if neigh:
            lines += ["", "## Connected subsystems", ""]
            lines += ["- [[%s]]" % cname(nc) for nc in neigh]
        notes[title + ".md"] = "\n".join(lines) + "\n"

    deg = {}
    for lk in links:
        deg[lk.get("source")] = deg.get(lk.get("source"), 0) + 1
        deg[lk.get("target")] = deg.get(lk.get("target"), 0) + 1
    gods = sorted(nodes, key=lambda n: deg.get(n.get("id"), 0), reverse=True)[:15]
    moc = ["---", "tags: [hermes/codegraph, moc]", "---", "",
           "# Hermes Code Graph", "",
           "Auto-generated from `graphify-out/graph.json` — "
           "%d nodes, %d edges, %d subsystems." % (len(nodes), len(links), len(members)),
           "", "## Hub nodes (highest connectivity)", ""]
    for n in gods:
        moc.append("- `%s` — `%s` (%d links)" % (
            n.get("label"), n.get("source_file"), deg.get(n.get("id"), 0)))
    moc += ["", "## Subsystems", ""]
    for cid in sorted(members, key=lambda c: -len(members[c])):
        moc.append("- [[%s]] — %d nodes" % (cname(cid), len(members[cid])))

    os.makedirs(cdir, exist_ok=True)
    for fn, text in notes.items():
        _atomic_write(os.path.join(cdir, fn), text)
    _atomic_write(os.path.join(out, "Hermes Code Graph.md"), "\n".join(moc) + "\n")
    # then drop only OUR notes the graph lost (compared the way APFS compares
    # names, so a note whose label only changed case is not deleted)
    keep = {_fold(fn) for fn in notes}
    for fn in os.listdir(cdir):
        if fn.endswith(".md") and _fold(fn) not in keep:
            try:
                os.remove(os.path.join(cdir, fn))
            except OSError:
                pass

    return len(notes), len(nodes), len(links)


def main():
    if len(sys.argv) < 2:
        print("usage: obsidian_sync.py <vault_dir> [graph_json]", file=sys.stderr)
        return 2
    vault = os.path.expanduser(sys.argv[1])
    graph_json = (sys.argv[2] if len(sys.argv) > 2 else
                  os.path.join(_script_dir(), "graphify-out", "graph.json"))
    if not os.path.isdir(vault):
        print("vault dir not found: %s" % vault, file=sys.stderr)
        return 1
    if not os.path.exists(graph_json):
        print("graph.json not found: %s (run: graphify update .)" % graph_json,
              file=sys.stderr)
        return 1
    try:
        w, nn, ne = build(vault, graph_json)
    except GraphError as e:
        print("%s — nothing synced, the vault is unchanged" % e, file=sys.stderr)
        return 1
    print("Synced %d subsystem notes + index -> %s/%s (%d nodes, %d edges)" % (
        w, vault, SUBFOLDER, nn, ne))
    return 0


if __name__ == "__main__":
    sys.exit(main())
