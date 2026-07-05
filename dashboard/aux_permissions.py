# aux_permissions.py — Graduated Permission Tiers routes (P1.3).
#
# A THIN aux route registrar, exec'd into server.py's globals by the aux-module
# loader (so register_get/register_post resolve).  It owns NO engine logic: the
# deterministic policy engine lives in the sibling module `permissions.py`,
# imported here (and independently by hermes_rpc.py) so there is exactly one
# lock / one mtime cache / one policy store in the process.  ZERO server.py edits.
#
# Handlers receive the RouteCtx (ctx.q1(...) for GET query params, ctx.body for
# POST JSON) and return a dict (-> 200) or a (dict, status) tuple.  For the POST
# setter we honor a `_status` key returned by permissions_set (403 floor / 400
# unknown / 500 internal), popping it and using it as the HTTP status.

import permissions as _pm


def _permissions_get(ctx):
    try:
        return _pm.permissions_payload()
    except Exception as e:                     # engine must never 500 the panel
        return {"ok": False, "error": type(e).__name__ + ": " + str(e),
                "trusted": True, "exists": False, "classes": [], "recent": []}


def _permissions_log(ctx):
    try:
        return _pm.permissions_log(ctx.q1("n", "50"))
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e), "entries": []}


def _permissions_test(ctx):
    try:
        return _pm.permissions_test(ctx.q1("pattern_key", ""), ctx.q1("command", ""))
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


def _permissions_set(ctx):
    r = _pm.permissions_set(ctx.body or {})
    if isinstance(r, dict) and "_status" in r:
        return (r, r.pop("_status", 200))
    return r


register_get("/api/permissions", _permissions_get)
register_get("/api/permissions/log", _permissions_log)
register_get("/api/permissions/test", _permissions_test)
register_post("/api/permissions", _permissions_set)
