# aux_appleapps.py — the two-switch "Apple apps" gate for Reminders/Notes
# automation.
#
# Owner complaint: several code paths ran `osascript -e 'tell application
# "Reminders"/"Notes" ...'`, which LAUNCHES those apps (and leaves them open)
# every time a widget's cache TTL expired or a pop-out expander ran. Google
# Calendar is connected, so for now the owner wants ONLY that used for
# calendar/task context — Reminders and Notes automation default OFF.
#
# server.py's `apple_apps_enabled(kind)` (kind = "reminders" | "notes") is the
# single choke point every Reminders/Notes osascript call site reads BEFORE
# shelling out — grep 'tell application "Reminders"' / '"Notes"' across the
# repo to verify every call site is gated. This module only owns the setting
# itself and its two HTTP routes:
#
#   GET  /api/apple_apps   -> {"ok": true, "reminders": bool, "notes": bool}
#   POST /api/apple_apps   -> {"reminders"?: bool, "notes"?: bool}
#                              (either or both; an omitted key is left as-is)
#
# settings.json shape: {"apple_apps": {"reminders": bool, "notes": bool}}.
# Read fresh via get_settings() (a small read_json, no cache) everywhere it
# matters; written as a read-modify-write of the WHOLE settings blob under
# server.py's `_state_lock` — settings.json is shared by every module, so a
# partial write here must never clobber unrelated config (same pattern as
# aux_claudebridge._cb_set_escalation).
#
# Globals used from server.py (exec'd into these): get_settings,
# settings_update, register_get, register_post.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an
# aux module. Not applicable here — this module touches no dates.

def _aa_get():
    cfg = get_settings().get("apple_apps")                        # noqa: F821
    cfg = cfg if isinstance(cfg, dict) else {}
    return {"reminders": bool(cfg.get("reminders")),
            "notes": bool(cfg.get("notes"))}


def _aa_set(patch):
    """Persist `patch` (a dict with 'reminders' and/or 'notes' keys) through
    server.py's settings_update() — one locked read-modify-write of
    settings.json, touching only the `apple_apps` sub-dict (2026-09-10 audit
    A01). Always leaves a stderr trace — the owner turning app-launching back
    on is worth a line in the log, same reasoning as the Claude escalation
    switch."""
    seen = {}

    def _apply(s):
        cfg = s.get("apple_apps") if isinstance(s.get("apple_apps"), dict) else {}
        seen["prev"] = dict(cfg)
        if "reminders" in patch:
            cfg["reminders"] = bool(patch["reminders"])
        if "notes" in patch:
            cfg["notes"] = bool(patch["notes"])
        s["apple_apps"] = cfg
        seen["cfg"] = cfg

    settings_update(_apply)                                        # noqa: F821
    prev, cfg = seen.get("prev", {}), seen.get("cfg", {})
    try:
        print("[aux_appleapps] apple_apps %s -> %s" % (prev, cfg), flush=True)
    except Exception:
        pass
    return {"reminders": bool(cfg.get("reminders")),
            "notes": bool(cfg.get("notes"))}


def _aa_get_handler(ctx):
    d = _aa_get()
    return {"ok": True, "reminders": d["reminders"], "notes": d["notes"]}


def _aa_post_handler(ctx):
    b = ctx.body if isinstance(getattr(ctx, "body", None), dict) else {}
    patch = {}
    if "reminders" in b:
        patch["reminders"] = bool(b.get("reminders"))
    if "notes" in b:
        patch["notes"] = bool(b.get("notes"))
    if not patch:
        return ({"ok": False,
                 "error": "nothing to change — send 'reminders' and/or "
                          "'notes' (bool)"}, 400)
    try:
        d = _aa_set(patch)
        return {"ok": True, "reminders": d["reminders"], "notes": d["notes"]}
    except Exception as e:
        return ({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)


register_get("/api/apple_apps", _aa_get_handler)                    # noqa: F821
register_post("/api/apple_apps", _aa_post_handler)                  # noqa: F821
