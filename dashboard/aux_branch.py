# Conversation checkpoints + branching (1.2.5) — aux module, exec'd into
# server.py's globals like every other aux_*.py.
#
# WHY THIS IS RUNTIME-FREE. Hermes Agent already knows how to branch
# (`session.branch` in tui_gateway/server.py) but only from the CURRENT TIP —
# there is no "branch at message N" RPC. It does, however, let a brand-new
# session be born with a transcript: `session.create` takes a `messages` seed
# (`_coerce_seed_history`, tui_gateway/server.py ~L4721). So a branch is built
# entirely on THIS side: copy the first N messages of the dashboard
# conversation into a new chats/<id>.json, and when that conversation's first
# prompt is submitted, mint its agent session WITH the copied prefix as the
# seed. Nothing in ~/.hermes/hermes-agent is patched.
#
# WHAT THE SEED CAN CARRY (verified against `_coerce_seed_history`):
#   accepted : {"role": "user"|"assistant"|"system",
#               "content": <non-empty str>}   ("text" is accepted as a
#                                              content alias)
#   dropped  : everything else — tool calls, tool results, tool_call_id,
#              message ids, reasoning, attachments. There is no "tool" role.
# So a branch carries the USER/ASSISTANT TEXT of the prefix and nothing more;
# tool results from before the branch point are gone. The UI says so, and
# README says so. Do not promise otherwise.
#
# APPEND-ONLY. A branch never edits the source: the child file is written
# first (a crash leaves an orphan child, which is harmless), then one row is
# appended to the source's `branches` list. `save_chat` (server.py) refuses a
# write that would shorten a conversation unless the caller passes
# truncate=True, so "the checkpoint is still there" is an invariant, not a
# convention.

import copy as _br_copy
import random as _br_random
import time as _br_time
import urllib.parse as _br_urlparse

import re as _br_re

_BR_MAX_DEPTH = 24          # lineage walk cap — a cycle can only ever be a bug
_BR_TITLE_CAP = 48

# Same rule as aux_convos._cv_clean_title (80 chars, control chars stripped,
# whitespace runs collapsed) — used to sanitise a user-supplied branch title
# before it lands in a chat file. Resolved BY NAME at call time in
# _br_sanitize_title rather than imported: aux modules exec in sorted order
# and "aux_branch.py" runs before "aux_convos.py", so at THIS module's own
# exec time _cv_clean_title does not exist yet as a global — but _br_branch()
# itself only runs from a request handler, long after every aux module has
# loaded, the same discipline aux_index/aux_onboarding document. The literal
# regex/cap below is a local fallback for the (unlikely) case aux_convos.py
# itself failed to load.
_BR_CTRL_RE = _br_re.compile(r"[\x00-\x1f\x7f]")
_BR_TITLE_SANITIZE_MAX = 80


def _br_clean_title_fallback(t):
    t = _BR_CTRL_RE.sub(" ", str(t if t is not None else ""))
    return " ".join(t.split())[:_BR_TITLE_SANITIZE_MAX].strip()


def _br_sanitize_title(t):
    fn = globals().get("_cv_clean_title")
    if callable(fn):
        try:
            return fn(t)
        except Exception:
            pass
    return _br_clean_title_fallback(t)


def _br_log(msg):
    print("[branch] %s" % msg, file=sys.stderr, flush=True)   # noqa: F821


def _br_valid(sid):
    """A branchable conversation id: well-formed, real, and not reserved —
    prewarm or the needs-you draft session (C38: a branch of it was a listed,
    indexed copy of third-party text). Resolved by name at call time."""
    if not sid or not SESSION_RE.match(sid):                  # noqa: F821
        return False
    hidden = (globals().get("HIDDEN_SESSIONS")
              or (PREWARM_SESSION, "needsyou"))               # noqa: F821
    if sid.casefold() in hidden:      # any letter case (E69): one file on APFS
        return False
    return os.path.exists(chat_path(sid))                     # noqa: F821


def _br_new_id():
    """A fresh conversation id in the shape the UI's newId() mints."""
    day = _br_time.strftime("%Y-%m-%d", _br_time.gmtime())
    for _ in range(50):
        sid = "chat-%s-%s" % (day, "".join(
            _br_random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
            for _ in range(5)))
        if not os.path.exists(chat_path(sid)):                # noqa: F821
            return sid
    return "chat-%s-%s" % (day, uuid.uuid4().hex[:8])         # noqa: F821


def _br_title_of(sid, chat=None):
    """Same title rule list_sessions() uses, so the tree and the sidebar agree."""
    chat = chat if chat is not None else load_chat(sid)       # noqa: F821
    msgs = chat.get("messages") or []
    return (chat.get("title")
            or (msgs[0].get("text", "")[:_BR_TITLE_CAP] if msgs else "")
            or sid)


def br_turn_count(messages):
    """How many USER turns a prefix carries — what "turn n" means in the UI.

    at_index counts MESSAGES (user and assistant alike); a human counts turns.
    Kept derived rather than stored so an older chats/*.json needs no migration.
    """
    return sum(1 for m in (messages or [])
               if isinstance(m, dict) and m.get("role") == "user")


# --------------------------------------------------------------------------
# the seed builder — pure, and the one thing worth unit-testing
# --------------------------------------------------------------------------
# dashboard role -> the roles `_coerce_seed_history` will keep. Anything not
# in this map is dropped rather than guessed at: a role we do not recognise is
# not worth inventing an assistant turn for.
_BR_ROLE = {"user": "user", "bot": "assistant",
            "assistant": "assistant", "system": "system"}


def br_seed_history(messages):
    """dashboard messages -> the `messages` param of `session.create`.

    Mirrors `_coerce_seed_history` (hermes-agent tui_gateway/server.py) exactly:
    role must be user/assistant/system and content must be a non-empty string,
    or the gateway drops the row. Building the same shape here means what we
    send is what the branch actually gets — no silent shrinkage.

    Dashboard error bubbles (`err: True` — "the model was asleep", "could not
    reach the backend") are NOT the assistant's words and are never seeded.
    """
    out = []
    for m in (messages or []):
        if not isinstance(m, dict) or m.get("err"):
            continue
        role = _BR_ROLE.get(m.get("role"))
        if role is None:
            continue
        text = m.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        out.append({"role": role, "content": text})
    return out


def _br_serve_key_live(key):
    """Is `key` still a row in state.db's sessions table?

    `sessions.parent_session_id` is a REAL foreign key (hermes_state.py L742,
    with PRAGMA foreign_keys=ON), and `_ensure_session_db_row` swallows the
    IntegrityError — so passing a stale parent key would silently cost the
    branch its DB row (and with it `_persist_branch_seed`, which is the only
    thing that makes a seeded transcript survive a resume). Cheap read-only
    check instead of a silent loss.
    """
    if not key or not os.path.exists(STATE_DB):               # noqa: F821
        return False
    try:
        import sqlite3
        uri = "file:" + _br_urlparse.quote(STATE_DB) + "?mode=ro"   # noqa: F821
        con = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            row = con.execute("SELECT 1 FROM sessions WHERE id=? LIMIT 1",
                              (key,)).fetchone()
        finally:
            con.close()
        return bool(row)
    except Exception:
        return False


def _br_seed_of(chat_meta):
    """The seed a forked chat would be created with ([] when none).

    The trailing user message is dropped: POST /api/chat appends the turn to
    the JSON *before* _chat_worker runs, and that same text is already the
    `prompt` about to be submitted — seeding it too would show the model the
    question twice. Everything before it is context, which is the point.
    """
    msgs = list(chat_meta.get("messages") or [])
    if msgs and isinstance(msgs[-1], dict) and msgs[-1].get("role") == "user":
        msgs = msgs[:-1]
    return br_seed_history(msgs)


def br_seed_params(chat_meta):
    """hermes_rpc.SEED_HOOK — extra `session.create` params for a forked chat.

    Returns {} for every ordinary conversation, so the normal minting path is
    byte-for-byte what it was. For a branch it returns
    {"messages": [...], "parent_session_id": "<a live state.db key>"?}.

    The parent is the SOURCE conversation's serve_key when that is still a
    live state.db row. When it is not (the source has no agent session, or its
    row was pruned), it is the branch's OWN previous serve_key when the
    run_turn wrapper below asked for a re-seed (`_br_reseed_parent`): upstream
    persists a seed only for a session that has a parent, and without one the
    branch forgot everything before its first turn as soon as serve's orphan
    reaper dropped the live session (C120).
    """
    try:
        if not isinstance(chat_meta, dict):
            return {}
        forked = chat_meta.get("forked_from")
        if not isinstance(forked, dict):
            return {}
        seed = _br_seed_of(chat_meta)
        if not seed:
            return {}
        params = {"messages": seed}
        src = forked.get("session") or ""
        if _br_valid(src):
            key = (load_chat(src).get("serve_key") or "").strip()   # noqa: F821
            if _br_serve_key_live(key):
                params["parent_session_id"] = key
        if "parent_session_id" not in params:
            own = (chat_meta.get("_br_reseed_parent") or "").strip()
            if own and _br_serve_key_live(own):
                params["parent_session_id"] = own
        return params
    except Exception as e:                                    # pragma: no cover
        _br_log("seed build failed (branch starts empty): %r" % e)
        return {}


def _br_row_parent(key):
    """state.db `sessions.parent_session_id` of `key`, read-only.

    None = no such row (or state.db unreadable); "" = a row with NO parent,
    which for a branch means upstream never persisted its seed.

    A row stamped `model_config._branched_from` counts as anchored even with
    its parent gone: upstream stamps that marker exactly when it was created
    WITH a parent (so `_persist_branch_seed` ran), and deleting the source
    session later NULLs the child's parent_session_id but keeps the marker
    (hermes_state.delete_session). Re-seeding that branch would only swap its
    persisted history — tool calls included — for the text-only transcript."""
    if not key or not os.path.exists(STATE_DB):               # noqa: F821
        return None
    try:
        import sqlite3
        uri = "file:" + _br_urlparse.quote(STATE_DB) + "?mode=ro"   # noqa: F821
        con = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            try:
                row = con.execute(
                    "SELECT parent_session_id, json_extract(COALESCE("
                    "model_config, '{}'), '$._branched_from') FROM sessions "
                    "WHERE id=? LIMIT 1", (key,)).fetchone()
            except sqlite3.OperationalError:     # an older state.db: no column
                row = con.execute("SELECT parent_session_id, NULL FROM sessions "
                                  "WHERE id=? LIMIT 1", (key,)).fetchone()
        finally:
            con.close()
        return None if row is None else (row[0] or row[1] or "")
    except Exception:
        return None


def br_needs_reseed(chat_meta):
    """The branch's current serve_key when its agent session was created WITHOUT
    a parent (so its pre-branch seed lives only in serve's memory and the next
    session.resume would drop it), else "".

    C120: upstream `_persist_branch_seed` writes the seed to state.db only when
    `parent_session_id` is set, and br_seed_params can give a parent only while
    the source chat has a live state.db row. The branch's first turn is fine
    (the live session carries the seed); 20 s later serve's orphan reaper drops
    that session and session.resume rebuilds it from state.db, where the branch
    holds only its own turns. The fix: re-create it ONCE, seeded with the whole
    transcript so far and parented to its own previous row — a real row, so
    upstream persists the seed and every later resume has the full context."""
    if not isinstance(chat_meta, dict) or not isinstance(
            chat_meta.get("forked_from"), dict):
        return ""
    key = (chat_meta.get("serve_key") or "").strip()
    if not key or _br_row_parent(key) != "":
        return ""        # no session yet, no row (a resume re-seeds anyway), or anchored
    return key if _br_seed_of(chat_meta) else ""


def _br_wrap_run_turn(orig):
    """hermes_rpc.run_turn, re-seeding an unanchored branch first (C120).

    Same runtime-override pattern aux_index uses for save_chat: server.py calls
    `hermes_rpc.run_turn` by attribute at call time, so rebinding it here wraps
    every dashboard turn without editing hermes_rpc. For anything that is not
    an unanchored branch it is a pass-through."""
    if getattr(orig, "_br_wrapped", False):
        return orig

    def run_turn(job, chat_meta, prompt, save_meta, *args, **kwargs):
        prev = ""
        try:
            prev = br_needs_reseed(chat_meta)
        except Exception as e:                                # pragma: no cover
            _br_log("re-seed check failed: %r" % e)
        if prev:
            _br_log("branch session %s has no persisted seed — re-creating it "
                    "with the full transcript, parented to itself" % prev)
            chat_meta["serve_sid"] = ""      # in memory only: run_turn creates,
            chat_meta["serve_key"] = ""      # then save_meta() persists the new ids
            chat_meta["_br_reseed_parent"] = prev
        try:
            return orig(job, chat_meta, prompt, save_meta, *args, **kwargs)
        finally:
            if isinstance(chat_meta, dict):
                chat_meta.pop("_br_reseed_parent", None)

    run_turn._br_wrapped = True
    run_turn.__wrapped__ = orig
    return run_turn


# --------------------------------------------------------------------------
# POST /api/sessions/branch
# --------------------------------------------------------------------------
def _br_branch(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    src = str(body.get("session") or "").strip()
    if not _br_valid(src):
        return ({"ok": False, "error": "unknown conversation"}, 400)

    src_chat = load_chat(src)                                 # noqa: F821
    msgs = src_chat.get("messages") or []
    try:
        at = int(body.get("at_index"))
    except (TypeError, ValueError, OverflowError):
        # OverflowError: json permits Infinity/-Infinity/NaN by default, and
        # int(float("inf")) raises OverflowError rather than ValueError — an
        # uncaught one here would surface as a generic 500 instead of the
        # same clean 400 every other bad at_index gets.
        return ({"ok": False, "error": "at_index must be a number"}, 400)
    # at_index is a COUNT of leading messages to carry, so 1..len (0 would be
    # an empty branch, which is just a new conversation).
    if at < 1 or at > len(msgs):
        return ({"ok": False, "error": "at_index out of range"}, 400)

    now = _br_time.time()
    new_id = _br_new_id()
    base = _br_title_of(src, src_chat)
    # A caller-supplied title is untrusted input — sanitise the same way a
    # rename does (aux_convos._cv_clean_title) so a huge/control-char-laden
    # title can't bloat the chat file or break the sidebar row.
    title = _br_sanitize_title(body.get("title")) or (base[:40] + " (branch)")

    # 1. the child, written first — deep-copied so nothing aliases the source
    #    list, timestamps kept verbatim (a branch is the same history, not a
    #    re-send of it). serve_sid/serve_key are deliberately NOT copied: the
    #    branch gets its own agent session, lazily, on its first prompt.
    child = {
        "messages": _br_copy.deepcopy(msgs[:at]),
        "title": title,
        "forked_from": {"session": src, "at_index": at, "ts": now},
    }
    try:
        save_chat(new_id, child)                              # noqa: F821
    except Exception as e:
        _br_log("child write failed: %r" % e)
        return ({"ok": False, "error": "could not write the branch"}, 500)

    # 2. append one row to the source. save_chat_update holds one lock across
    #    the read, the append and the write, so the retry loop below is now
    #    belt-and-braces (a transient write error) rather than the mechanism
    #    that made this safe — the old load/append/save pair could lose the
    #    registration to a turn that landed in between (2026-09-10 audit A03).
    row = {"child": new_id, "at_index": at, "ts": now}
    linked = False
    for _ in range(3):
        try:
            save_chat_update(                                 # noqa: F821
                src, lambda cur: cur.setdefault("branches", []).append(row))
            linked = True
            break
        except Exception as e:                                # pragma: no cover
            _br_log("source link retry: %r" % e)
            _br_time.sleep(0.05)
    if not linked:
        # The branch itself is fine and knows its parent; only the parent's
        # back-reference is missing. Say so rather than pretending.
        _br_log("branch %s created but %s could not be linked" % (new_id, src))

    return {"ok": True, "session": new_id, "title": title, "at_index": at,
            "turn": br_turn_count(child["messages"]),
            "messages": len(child["messages"]), "linked": linked,
            "forked_from": child["forked_from"],
            # one honest sentence the UI can show verbatim
            "note": "Tool results from before this point are not carried "
                    "into the branch."}


# --------------------------------------------------------------------------
# GET /api/sessions/tree?session=<id>
# --------------------------------------------------------------------------
def _br_node(sid, at_index=None):
    """One lineage row. `exists` false = the file was deleted out from under us."""
    if not _br_valid(sid):
        return {"id": sid, "exists": False, "title": sid, "messages": 0,
                "updated": 0, "at_index": at_index, "turn": None}
    chat = load_chat(sid)                                     # noqa: F821
    msgs = chat.get("messages") or []
    try:
        updated = os.path.getmtime(chat_path(sid))            # noqa: F821
    except OSError:                                           # pragma: no cover
        updated = 0
    return {
        "id": sid, "exists": True, "title": _br_title_of(sid, chat),
        "messages": len(msgs), "updated": updated,
        "last_ts": (msgs[-1].get("ts") if msgs else None),
        "at_index": at_index,
        "turn": (br_turn_count(msgs[:at_index]) if at_index is not None else None),
        "branches": len(chat.get("branches") or []),
        "pinned": bool(chat.get("pinned")),
        # None until this branch's first turn actually runs (hermes_rpc.
        # run_turn only stamps it once a session.create for a forked chat
        # happens); False means SEED_HOOK ran but seeded no messages
        # (context lost — see hermes_rpc's SEED_HOOK log line for why).
        "seeded": chat.get("seeded"),
    }


def _br_tree(ctx):
    sid = (ctx.query.get("session") or [""])[0].strip()
    if not _br_valid(sid):
        return ({"ok": False, "error": "unknown conversation"}, 400)

    chat = load_chat(sid)                                     # noqa: F821

    # ancestors, nearest parent first. Each row carries the at_index at which
    # its CHILD was cut, which is the number the UI wants to show.
    ancestors, seen, cur, cut = [], {sid}, chat, None
    for _ in range(_BR_MAX_DEPTH):
        forked = cur.get("forked_from")
        if not isinstance(forked, dict):
            break
        parent = str(forked.get("session") or "").strip()
        try:
            cut = int(forked.get("at_index"))
        except (TypeError, ValueError):
            cut = None
        if not parent or parent in seen:
            break                       # a cycle is a bug, not a lineage
        seen.add(parent)
        ancestors.append(_br_node(parent, cut))
        if not _br_valid(parent):
            break
        cur = load_chat(parent)                               # noqa: F821

    children = []
    for b in (chat.get("branches") or []):
        if not isinstance(b, dict):
            continue
        try:
            ci = int(b.get("at_index"))
        except (TypeError, ValueError):
            ci = None
        children.append(dict(_br_node(str(b.get("child") or ""), ci),
                             ts=b.get("ts")))

    return {"ok": True, "session": sid, "node": _br_node(sid),
            "forked_from": chat.get("forked_from"),
            "ancestors": ancestors, "children": children}


register_post("/api/sessions/branch", _br_branch)             # noqa: F821
register_get("/api/sessions/tree", _br_tree)                  # noqa: F821

# The seed rides in through the same hook shape aux_recorder uses for
# RECORDER_HOOK: server.py imports hermes_rpc function-locally, so import it
# here rather than reaching for a global that may not be bound.
try:
    import hermes_rpc as _br_rpc
    _br_rpc.SEED_HOOK = br_seed_params
    _br_rpc.run_turn = _br_wrap_run_turn(_br_rpc.run_turn)
except Exception as _br_e:                                    # pragma: no cover
    _br_log("seed hook not wired — branches will start with no context: %r"
            % _br_e)
