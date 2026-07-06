# aux_youmodel.py — the You-Model (Proactive-Intelligence Phase-1 WS 1.1).
#
# A THIN read/append VIEW over aux_memory.py — no new storage engine.  The
# You-Model is five typed markdown files (GOALS/NOW/LOOKING-FOR/INTERESTS/
# PREFERENCES.md) plus people/<slug>.md cards, all living in ~/.hermes/memories/
# where aux_memory already versions, snapshots, locks and char-meters them.
# Entries inside each typed file use the same "\n§\n" delimiter as the core
# files so every entry is an independently editable card, but the files stay
# kind="freeform" to aux_memory (content-based save, 128 KB cap).
#
# exec'd into server.py globals AFTER aux_memory.py (sorted aux load order:
# "aux_youmodel" > "aux_memory"), so it may use aux_memory's globals directly:
#   MEM_DIR, ENTRY_DELIM, MAX_FREEFORM, _mem_valid_name, _mem_path, _mem_etag,
#   _mem_create_handler, _mem_save_handler
# and server.py's RouteCtx / register_get / register_post.  Every write goes
# THROUGH _mem_create_handler/_mem_save_handler so it inherits the flock,
# snapshot, recorder and etag machinery for free.  Defines only _ym_* / YM_*
# names.  No datetime import needed (per the CLAUDE.md aux-module alias rule).

import os as _ym_os
import sys as _ym_sys

# --------------------------------------------------------------------------
# the typed files (order = display order) + their scaffolding templates.
# Templates are one-line HTML comments: invisible in rendered markdown, shown
# as the section hint in the dashboard card, and preserved as entry 0.
# --------------------------------------------------------------------------
YM_FILES = [
    ("GOALS.md", "Goals",
     "<!-- Goals — explicit objectives with a time horizon, one per § entry "
     "(e.g. \"Ship Hermes publicly — by October 2026\"). Revisit quarterly. -->"),
    ("NOW.md", "Now",
     "<!-- Now — what you are actively working on this week or month, one "
     "project per § entry. The freshest signal Hermes matches against. -->"),
    ("LOOKING-FOR.md", "Looking for",
     "<!-- Looking for — open loops: people to meet, roles to hire, things to "
     "buy, questions to answer. Each § entry is a standing subscription Hermes "
     "keeps scanning for. -->"),
    ("INTERESTS.md", "Interests",
     "<!-- Interests — topics you care about, one per § entry, ideally with a "
     "weight (high/med/low) and an as-of date so stale interests can decay. -->"),
    ("PREFERENCES.md", "Preferences",
     "<!-- Preferences — tone, when it is OK to interrupt you, what counts as "
     "noise. Feeds the interruptibility gate. -->"),
]
YM_NAMES   = [n for n, _, _ in YM_FILES]

# aux_memory's snapshot/trash paths embed the "people/" prefix verbatim
# (snapshots/memory/people/<f>, memory-trash/people/<f>) but only the top-level
# dirs exist — without these mirrors, saving over or deleting a people card
# 500s at the snapshot/move step.  Same never-fail mkdir pattern as aux_memory.
for _ym_d in (_ym_os.path.join(MEM_SNAP, "people"),
              _ym_os.path.join(MEM_TRASH, "people")):
    try:
        _ym_os.makedirs(_ym_d, mode=0o700, exist_ok=True)
    except Exception as _ym_e:                            # pragma: no cover
        print("[aux_youmodel] mkdir %s failed: %s" % (_ym_d, _ym_e),
              file=_ym_sys.stderr)
YM_BUDGET  = 4000        # advisory per-file char budget for the meter (display
                         # only; the hard cap stays aux_memory's MAX_FREEFORM)
YM_ADD_MAX = 2000        # one proposed entry should never be an essay
YM_PEOPLE_READ_CAP = 8192


def _ym_is_hint(entry):
    """Template/comment entries render as the section hint, not as cards."""
    e = entry.strip()
    return e.startswith("<!--") and e.endswith("-->")


def _ym_hint_text(entry):
    return entry.strip()[4:-3].strip()


def _ym_read(path):
    """(text, etag, mtime) or (None, None, None). Reads are lock-free — the
    writers in aux_memory use atomic replace, so we never see a partial file."""
    try:
        st = _ym_os.stat(path)
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return (None, None, None)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    return (text, _mem_etag(raw), st.st_mtime)


def _ym_norm(res):
    """aux_memory handlers return dict or (dict, status) — normalize."""
    if isinstance(res, tuple):
        return res[0], (res[1] if len(res) > 1 else 200)
    return res, 200


# --------------------------------------------------------------------------
# GET /api/youmodel — read-only aggregate of the whole You-Model
# --------------------------------------------------------------------------
def _ym_get_handler(ctx):
    try:
        files = []
        filled = 0
        for name, label, template in YM_FILES:
            path = _ym_os.path.join(MEM_DIR, name)
            text, etag, mtime = _ym_read(path)
            row = {"name": name, "label": label, "exists": text is not None,
                   "char_used": 0, "char_budget": YM_BUDGET,
                   "hint": _ym_hint_text(template), "entries": [],
                   "entry_count": 0, "etag": etag, "mtime": mtime}
            if text is not None:
                ents = [s.strip() for s in text.split(ENTRY_DELIM) if s.strip()]
                real = [e for e in ents if not _ym_is_hint(e)]
                hints = [e for e in ents if _ym_is_hint(e)]
                if hints:
                    row["hint"] = _ym_hint_text(hints[0])
                row["entries"] = ents          # full list incl. template, for exact rebuilds
                row["entry_count"] = len(real)
                row["char_used"] = len(text)
                if real:
                    filled += 1
            files.append(row)

        people = []
        pdir = _ym_os.path.join(MEM_DIR, "people")
        try:
            pents = sorted(_ym_os.scandir(pdir), key=lambda e: e.name.lower())
        except OSError:
            pents = []
        for e in pents:
            if e.name.startswith(".") or not e.name.endswith(".md"):
                continue
            try:
                if e.is_symlink() or not e.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            text, etag, mtime = _ym_read(e.path)
            if text is None:
                continue
            preview = ""
            for ln in text.splitlines():
                if ln.strip() and not _ym_is_hint(ln):
                    preview = ln.strip()[:140]
                    break
            people.append({"name": "people/" + e.name,
                           "slug": e.name[:-3],
                           "preview": preview,
                           "content": text[:YM_PEOPLE_READ_CAP],
                           "truncated": len(text) > YM_PEOPLE_READ_CAP,
                           "char_used": len(text),
                           "etag": etag, "mtime": mtime})

        return {"ok": True, "dir": MEM_DIR, "files": files, "people": people,
                "filled": filled, "total": len(YM_FILES),
                "empty": (filled == 0 and not people)}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


# --------------------------------------------------------------------------
# POST /api/youmodel/seed — create any MISSING typed file with its template.
# Never overwrites: existence is checked here AND raced-safely again inside
# _mem_create_handler (409 exists -> counted as skipped).
# --------------------------------------------------------------------------
def _ym_seed_handler(ctx):
    try:
        created, skipped, errors = [], [], []
        for name, label, template in YM_FILES:
            path = _ym_os.path.join(MEM_DIR, name)
            if _ym_os.path.isfile(path):
                skipped.append(name)
                continue
            obj, status = _ym_norm(_mem_create_handler(
                RouteCtx(body={"name": name, "content": template + "\n"})))
            if obj.get("ok"):
                created.append(name)
            elif obj.get("error") == "exists":
                skipped.append(name)
            else:
                errors.append({"name": name,
                               "error": obj.get("error", "HTTP %d" % status)})
        out = {"ok": not errors, "created": created, "skipped": skipped}
        if errors:
            out["errors"] = errors
            return (out, 500)
        return out
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


# --------------------------------------------------------------------------
# POST /api/youmodel/add — append ONE §-delimited entry to a typed file or a
# people/<slug>.md card.  Creates the file (with template scaffolding for
# typed files) if missing.  The save itself rides /api/memory/save's handler,
# so flock/snapshot/recorder/conflict semantics are aux_memory's.
# --------------------------------------------------------------------------
def _ym_add_handler(ctx):
    try:
        b = ctx.body or {}
        name = (b.get("file") or b.get("name") or "").strip()
        text = b.get("text")
        if name not in YM_NAMES and not (name.startswith("people/")
                                         and _mem_valid_name(name)):
            return ({"ok": False, "error": "bad_file",
                     "hint": "file must be one of %s or people/<slug>.md"
                             % "/".join(YM_NAMES)}, 400)
        if not isinstance(text, str) or not text.strip():
            return ({"ok": False, "error": "bad_text"}, 400)
        text = text.strip()
        if len(text) > YM_ADD_MAX:
            return ({"ok": False, "error": "too_long", "limit": YM_ADD_MAX}, 400)
        if text == "§" or ENTRY_DELIM in ("\n" + text + "\n"):
            return ({"ok": False, "error": "bad_entry",
                     "hint": "An entry can't contain the § delimiter line."}, 400)

        try:
            path = _mem_path(name)
        except _MemBadName:
            return ({"ok": False, "error": "bad_name"}, 400)

        if not _ym_os.path.isfile(path):
            # create-with-first-entry (typed files get their template as entry 0)
            template = next((t for n, _, t in YM_FILES if n == name), None)
            content = (template + ENTRY_DELIM + text) if template else text
            obj, status = _ym_norm(_mem_create_handler(
                RouteCtx(body={"name": name, "content": content})))
            if obj.get("ok"):
                return {"ok": True, "file": name, "created": True,
                        "etag": (obj.get("file") or {}).get("etag")}
            if obj.get("error") != "exists":       # real failure
                return (obj, status)
            # lost the race — fall through to the append path

        cur, etag, _mt = _ym_read(path)
        if cur is None:
            return ({"ok": False, "error": "missing"}, 404)
        new = (cur.rstrip() + ENTRY_DELIM + text) if cur.strip() else text
        obj, status = _ym_norm(_mem_save_handler(RouteCtx(
            body={"name": name, "base_etag": etag, "content": new})))
        if obj.get("ok"):
            obj["file"] = name
            obj["created"] = False
            return obj
        return (obj, status)   # conflict/locked/over-limit pass straight through
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


register_get("/api/youmodel", _ym_get_handler)
register_post("/api/youmodel/seed", _ym_seed_handler)
register_post("/api/youmodel/add", _ym_add_handler)
