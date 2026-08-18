# aux_graphify.py — dashboard corpus-RAG memory layer over the code knowledge
# graph (graphify-out/graph.json, built by the `graphify` CLI). Exposes a
# queryable view of the repo's structure so the dashboard — and, via
# /api/graph/query, the local Hermes agent — can TRAVERSE the graph instead of
# re-reading files. Fewer tokens per turn ⇒ smaller MLX prompt/KV cache.
#
# Read-only: this module never mutates the graph; rebuilding is the `graphify`
# CLI's job (`graphify update .` / `graphify watch .`).
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` — it
# rebinds the shared global. This module needs no datetime; keep it that way.
import json as _gfy_json
import os as _gfy_os
import threading as _gfy_threading

_GFY_LOCK = _gfy_threading.Lock()
# cached parse of graph.json, invalidated by mtime (the file is ~2.6MB — parse once)
_GFY_CACHE = {"path": None, "mtime": 0.0, "graph": None, "adj": None}


def _gfy_graph_path():
    """Locate graphify-out/graph.json (repo root = parent of the dashboard dir)."""
    cands = []
    try:  # server.py's __file__ is dashboard/server.py; repo root is its parent's parent
        root = _gfy_os.path.dirname(_gfy_os.path.dirname(_gfy_os.path.abspath(__file__)))
        cands.append(_gfy_os.path.join(root, "graphify-out", "graph.json"))
    except Exception:
        pass
    try:
        cands.append(_gfy_os.path.join(HOME, "HermesAssistant",  # noqa: F821
                                       "graphify-out", "graph.json"))
    except Exception:
        pass
    for c in cands:
        if c and _gfy_os.path.exists(c):
            return c
    return cands[0] if cands else None


def _gfy_load():
    """(graph, adjacency) from graph.json, cached by mtime; (None, None) if absent."""
    path = _gfy_graph_path()
    if not path or not _gfy_os.path.exists(path):
        return None, None
    try:
        mt = _gfy_os.path.getmtime(path)
    except OSError:
        return None, None
    with _GFY_LOCK:
        c = _GFY_CACHE
        if c["graph"] is not None and c["path"] == path and c["mtime"] == mt:
            return c["graph"], c["adj"]
        try:
            with open(path) as f:
                g = _gfy_json.load(f)
        except Exception:
            return None, None
        adj = {}       # id -> [(other_id, relation, direction)]
        for lk in g.get("links", []):
            s, t = lk.get("source"), lk.get("target")
            rel = lk.get("relation") or "rel"
            if s is not None:
                adj.setdefault(s, []).append((t, rel, "out"))
            if t is not None:
                adj.setdefault(t, []).append((s, rel, "in"))
        c.update(path=path, mtime=mt, graph=g, adj=adj)
        return g, adj


def _gfy_stats(ctx):
    """Graph overview: node/edge/community counts + the top 'god nodes' (highest
    degree — the hubs worth knowing about first)."""
    g, adj = _gfy_load()
    if g is None:
        return {"ok": False, "available": False,
                "hint": "build it: ~/.hermes/graphify-venv/bin/graphify update ."}
    nodes = g.get("nodes", [])
    links = g.get("links", [])
    comms = {n.get("community") for n in nodes if n.get("community") is not None}
    deg = {nid: len(v) for nid, v in (adj or {}).items()}
    top = sorted(nodes, key=lambda n: deg.get(n.get("id"), 0), reverse=True)[:12]
    god = [{"id": n.get("id"), "label": n.get("label"),
            "file": n.get("source_file"), "degree": deg.get(n.get("id"), 0),
            "community": n.get("community")} for n in top]
    return {"ok": True, "available": True, "nodes": len(nodes),
            "edges": len(links), "communities": len(comms),
            "built_at_commit": g.get("built_at_commit"), "god_nodes": god}


def _gfy_query(ctx):
    """Traverse the graph: find nodes matching ?q= (label / path / id substring)
    and return each with its immediate neighbors + relations — the cheap
    'explain this symbol' the agent uses instead of grepping the tree."""
    try:
        q = (ctx.query.get("q", [""])[0] or "").strip().lower()
    except Exception:
        q = ""
    g, adj = _gfy_load()
    if g is None:
        return {"ok": False, "available": False}
    if not q:
        return {"ok": True, "matches": [], "hint": "pass ?q=<name, path, or id>"}
    nodes = g.get("nodes", [])
    byid = {n.get("id"): n for n in nodes}
    hits = []
    for n in nodes:
        hay = " ".join(str(n.get(k, "")) for k in
                       ("label", "norm_label", "source_file", "id")).lower()
        if q in hay:
            hits.append(n)
        if len(hits) >= 8:
            break
    out = []
    for n in hits:
        nid = n.get("id")
        neigh, seen = [], set()
        for other, rel, dirn in (adj.get(nid, []) if adj else [])[:16]:
            if other in seen:
                continue
            seen.add(other)
            on = byid.get(other, {})
            neigh.append({"id": other, "label": on.get("label") or other,
                          "relation": rel, "dir": dirn,
                          "file": on.get("source_file")})
        out.append({"id": nid, "label": n.get("label"),
                    "file": n.get("source_file"),
                    "location": n.get("source_location"),
                    "community": n.get("community"), "neighbors": neigh})
    return {"ok": True, "query": q, "count": len(out), "matches": out}


register_get("/api/graph/stats", _gfy_stats)  # noqa: F821
register_get("/api/graph/query", _gfy_query)  # noqa: F821
