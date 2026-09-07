# aux_doctor.py — serves the health checks in dashboard/doctor.py over HTTP.
#
#   GET /api/doctor                 -> {"ok":true,"generated":<epoch>,
#                                       "summary":{"pass":n,"warn":n,"fail":n},
#                                       "checks":[{id,label,status,detail,fix,ms}]}
#   GET /api/doctor?format=text     -> the same run as the plain-text report
#   GET /api/doctor?fresh=1         -> bypass the short result cache
#
# WHY A SEPARATE FILE.  doctor.py is a standalone CLI: it must run on a Mac
# where the dashboard is DOWN, which is exactly when someone reaches for it.
# So it never imports server.py (that would exec every aux module and start
# their threads).  This module is the other half of that trade — it loads
# doctor.py by path with importlib and hands it the live server globals, so the
# SERVED answer is computed by the dashboard's own helpers (_weights_complete,
# _model_fit, _hf_python, _cb_claude_bin, _onb_fda, claude_escalation_enabled)
# while the CLI keeps its local fallbacks.  One list of checks, two front doors.
#
# LOAD ORDER.  aux files exec in sorted order, so this module runs BEFORE
# aux_index / aux_needsyou / aux_onboarding / aux_update.  Nothing here reads a
# foreign global at module load: `doctor.HOST` is bound to the LIVE globals()
# dict and every lookup happens inside a request, by name — the discipline
# aux_index.py documents.
#
# READ-ONLY, AND IT NEVER WAKES A MODEL.  See doctor.py's docstring: launchctl
# is only ever `list`, the model lanes are HTTP probes (no plist carries a
# Sockets key, so nothing is socket-activated), and no file is written.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an
# aux module — it rebinds the shared global.  Private aliases only.
import importlib.util as _doc_ilu
import os as _doc_os
import sys as _doc_sys
import threading as _doc_threading
import time as _doc_time

_DOC_PATH = _doc_os.path.join(HERE, "doctor.py")          # noqa: F821 (server.py)
_DOC_CACHE_TTL = 10.0        # seconds; the card's "Run checks" button sends fresh=1
_doc_lock = _doc_threading.Lock()
_doc_cache = {"at": 0.0, "payload": None}


def _doc_load():
    """Import dashboard/doctor.py by path, once, and give it the live globals.

    Loaded under a private module name so it can never collide with a
    site-packages `doctor`; a failure here is returned to the route as an
    error payload rather than taking the dashboard down."""
    mod = _doc_sys.modules.get("_hermes_doctor")
    if mod is None:
        spec = _doc_ilu.spec_from_file_location("_hermes_doctor", _DOC_PATH)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load %s" % _DOC_PATH)
        mod = _doc_ilu.module_from_spec(spec)
        _doc_sys.modules["_hermes_doctor"] = mod
        spec.loader.exec_module(mod)
    # globals() here IS server.py's module dict (aux files are exec'd into it),
    # so this is a live reference: helpers defined by aux modules that load
    # AFTER us are still found, because doctor resolves by name at call time.
    mod.HOST = globals()
    return mod


def _doc_payload(fresh=False):
    """One run, memoised for _DOC_CACHE_TTL.  The lock also collapses two
    simultaneous clicks into a single run instead of two concurrent sweeps of
    launchctl + sysctl + `hermes --version`."""
    now = _doc_time.time()
    with _doc_lock:
        cached = _doc_cache["payload"]
        if (not fresh) and cached and (now - _doc_cache["at"]) < _DOC_CACHE_TTL:
            return cached
        mod = _doc_load()
        p = mod.payload()
        _doc_cache["at"] = _doc_time.time()
        _doc_cache["payload"] = p
        return p


def _doc_route(ctx):
    fmt = (ctx.q1("format", "") or "").strip().lower()
    fresh = (ctx.q1("fresh", "") or "").strip().lower() in ("1", "true", "yes")
    try:
        p = _doc_payload(fresh)
    except Exception as e:
        err = "doctor could not run — %s: %s" % (type(e).__name__, e)
        if fmt in ("text", "txt", "plain"):
            return RawResponse(err + "\n", status=500)          # noqa: F821
        return {"ok": False, "error": err}, 500
    if fmt in ("text", "txt", "plain"):
        try:
            mod = _doc_load()
            # a fixed width: the caller is curl or the clipboard, not a tty
            body = mod.report(p["checks"], width=100)
        except Exception as e:
            return RawResponse("doctor could not render the report — %s: %s\n"
                               % (type(e).__name__, e), status=500)   # noqa: F821
        return RawResponse(body + "\n")                          # noqa: F821
    return p


register_get("/api/doctor", _doc_route)          # noqa: F821 (server.py)
