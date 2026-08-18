#!/usr/bin/env python3
"""obsidian_sync.py — deploy the Graphify code knowledge graph into an Obsidian
vault as native, interlinked markdown notes. One note per community (subsystem)
plus a Map-of-Content index; cross-community edges become [[wikilinks]], so
Obsidian's own graph view renders the repo's architecture.

Usage:  obsidian_sync.py <vault_dir> [graph_json]
Writes ONLY under "<vault_dir>/Hermes Code Graph/" (a folder this tool owns and
is the only thing it ever clears). Idempotent: safe to re-run on every rebuild.
"""
import json
import os
import re
import sys

SUBFOLDER = "Hermes Code Graph"
COMM_DIR = "communities"


def _safe(name):
    """Filename/wikilink-safe: strip the chars Obsidian can't resolve in links."""
    name = re.sub(r'[\\/:*?"<>|\[\]#^]', "-", str(name or "untitled"))
    return (name.strip().strip(".") or "untitled")[:80]


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _load(graph_json):
    with open(graph_json) as f:
        g = json.load(f)
    labels = {}
    try:
        labels = json.load(open(os.path.join(os.path.dirname(graph_json),
                                              ".graphify_labels.json")))
    except Exception:
        pass
    return g, labels


def build(vault, graph_json):
    g, labels = _load(graph_json)
    nodes = g.get("nodes", [])
    links = g.get("links", [])
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
        return "%s (c%s)" % (_safe(labels.get(str(cid), "Community %s" % cid)), cid)

    out = os.path.join(vault, SUBFOLDER)
    cdir = os.path.join(out, COMM_DIR)
    os.makedirs(cdir, exist_ok=True)
    for fn in os.listdir(cdir):        # clear only OUR prior notes
        if fn.endswith(".md"):
            try:
                os.remove(os.path.join(cdir, fn))
            except OSError:
                pass

    written = 0
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
        with open(os.path.join(cdir, _safe(title) + ".md"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        written += 1

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
    with open(os.path.join(out, "Hermes Code Graph.md"), "w") as fh:
        fh.write("\n".join(moc) + "\n")

    return written, len(nodes), len(links)


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
    w, nn, ne = build(vault, graph_json)
    print("Synced %d subsystem notes + index -> %s/%s (%d nodes, %d edges)" % (
        w, vault, SUBFOLDER, nn, ne))
    return 0


if __name__ == "__main__":
    sys.exit(main())
