# aux_clip.py — Clipboard Actions (P2.3).
#
# Pure text-in / text-out transforms over whatever the user has on the clipboard
# (summarize / explain / translate / rewrite / extract / proofread).  Exec'd into
# server.py's globals by the aux-module loader, so it may use these server.py
# globals: MODEL_URL, model_online, active_model, get_settings, _cached.  It
# imports ALL its own stdlib deps (exec'd code cannot rely on server.py's
# function-local imports) and defines only new names (CLIP_*, _clip_*) so it
# clobbers nothing.
#
# SAFETY STORY (the whole point of this module):
#   * A transform is a DIRECT-TO-MODEL call — one urllib POST to the local MLX
#     server (127.0.0.1:8080) at /v1/chat/completions with NO "tools" field.
#     It therefore CANNOT emit an approval.request, cannot touch permissions.py,
#     cannot write a file, cannot send anything.  "The capability does not exist"
#     — not "we remembered to deny it."
#   * The ONLY outbound socket is loopback (CLIP_URL below).  No _ssl_context,
#     no external host, no `hermes send`, no subprocess, no telemetry.
#   * Clipboard bytes are never persisted: not to recorder.db, not to
#     settings.json, not to disk.  The server never reads the clipboard itself —
#     the app/popover reads NSPasteboard (or the browser reads it) and passes the
#     text in the POST body only.
#   * Escalation to the real, approval-gated agent is a separate explicit click
#     ("Open in chat") that reuses the normal /api/chat -> run_turn seam.

import os
import sys
import json
import time
import socket
import urllib.request
import urllib.error

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
CLIP_MAX_CHARS = 16000            # ~5k tokens; bounds the prompt so we never OOM
CLIP_TIMEOUT = 60                 # seconds for the single inference call

# Derive the chat-completions URL from the model base URL (server.py:MODEL_URL,
# e.g. http://127.0.0.1:8080/v1/models).  Loopback, plain http, no SSL context —
# exactly how model_online() hits the same server.
try:
    if "/v1/models" in MODEL_URL:
        CLIP_URL = MODEL_URL.replace("/v1/models", "/v1/chat/completions")
    else:
        CLIP_URL = "http://127.0.0.1:8080/v1/chat/completions"
except Exception:                                            # pragma: no cover
    CLIP_URL = "http://127.0.0.1:8080/v1/chat/completions"


# --------------------------------------------------------------------------
# action catalog — static; system prompts stay server-owned (never sent to UI)
# --------------------------------------------------------------------------
CLIP_ACTIONS = {
    "summarize": {
        "label": "Summarize", "opts": [],
        "temperature": 0.4, "max_tokens": 512,
        "system": ("You are a precise summarizer. Summarize the user's text in tight, "
                   "skimmable form: a one-line gist then 2-5 bullet points. Preserve names, "
                   "numbers, and decisions. Do not add facts that are not in the text. Output "
                   "only the summary, no preamble."),
    },
    "explain": {
        "label": "Explain", "opts": [],
        "temperature": 0.4, "max_tokens": 700,
        "system": ("Explain the user's text plainly for a smart non-expert. Define jargon, spell "
                   "out what it means and why it matters, in a few short paragraphs. If it is code, "
                   "explain what it does step by step. Only use information present in the text. "
                   "Output only the explanation."),
    },
    "translate": {
        "label": "Translate",
        "opts": [{"id": "to", "label": "Into", "type": "lang", "default": "English"}],
        "temperature": 0.2, "max_tokens": 1500,
        "system": ("You are a faithful translator. Translate the user's text into {to}. Preserve "
                   "meaning, tone, names, and formatting (lists, line breaks). Do not summarize, "
                   "explain, or add notes. If the text is already in {to}, return it unchanged. "
                   "Output only the translation."),
    },
    "rewrite": {
        "label": "Rewrite",
        "opts": [
            {"id": "tone", "label": "Tone", "type": "choice",
             "choices": ["clearer", "more concise", "more formal", "friendlier",
                         "more assertive", "simpler"], "default": "clearer"},
            {"id": "format", "label": "As", "type": "choice",
             "choices": ["prose", "bullet points", "an email", "a message"], "default": "prose"},
        ],
        "temperature": 0.5, "max_tokens": 1200,
        "system": ("Rewrite the user's text to be {tone}, formatted as {format}. Keep the original "
                   "meaning and all facts; change wording, not substance. Do not invent details. "
                   "Output only the rewritten text."),
    },
    "extract": {
        "label": "Extract",
        "opts": [{"id": "what", "label": "Pull out", "type": "choice",
                  "choices": ["action items", "key points", "dates & times",
                              "names & entities", "emails & links", "numbers & figures"],
                  "default": "action items"}],
        "temperature": 0.2, "max_tokens": 700,
        "system": ("Extract the {what} from the user's text as a clean list. Include only items "
                   "that actually appear in the text - never guess or infer beyond it. If there "
                   "are none, output exactly: (none found). Output only the list."),
    },
    "proofread": {
        "label": "Proofread", "opts": [],
        "temperature": 0.2, "max_tokens": 1400,
        "system": ("Correct spelling, grammar, and punctuation in the user's text. Preserve the "
                   "author's voice, meaning, and formatting; do not rewrite for style or add "
                   "content. Return the corrected text only - no list of changes, no commentary."),
    },
}
CLIP_ACTION_ORDER = ["summarize", "explain", "rewrite", "proofread", "translate", "extract"]


# --------------------------------------------------------------------------
# opts validation + prompt fill
# --------------------------------------------------------------------------
def _clip_fill(spec, opts):
    """Validate declared opts and fill the system template.

    Only the *values* are substituted; the template's {keys} are fixed, so a
    user string can never reach .format's field slots (no injection).
    """
    opts = opts if isinstance(opts, dict) else {}
    validated = {}
    for o in spec.get("opts", []):
        oid = o["id"]
        raw = opts.get(oid)
        if o["type"] == "choice":
            validated[oid] = raw if raw in o.get("choices", []) else o.get("default", "")
        elif o["type"] == "lang":
            v = raw if isinstance(raw, str) else ""
            v = v.strip()[:40]
            validated[oid] = v or o.get("default", "")
        else:
            validated[oid] = o.get("default", "")
    try:
        return spec["system"].format(**validated)
    except Exception:
        # A stray brace in a value can't KeyError (values are args, not the
        # template), but be defensive: fall back to the unfilled template.
        return spec["system"]


def _clip_complete(body):
    """One urllib POST to CLIP_URL (loopback).  Returns parsed JSON or raises."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        CLIP_URL, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=CLIP_TIMEOUT) as r:   # NO ssl ctx: loopback http
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------
# handlers  (each returns dict -> 200, or (dict, status) tuple)
# --------------------------------------------------------------------------
def _clip_run_handler(ctx):
    try:
        s = get_settings().get("clip") or {}
        if s.get("enabled") is False:
            return ({"ok": False, "error": "disabled"}, 503)

        b = ctx.body or {}
        action = b.get("action")
        if action not in CLIP_ACTIONS:
            return ({"ok": False, "error": "bad_action"}, 400)

        text = b.get("text")
        if not isinstance(text, str):
            return ({"ok": False, "error": "not_text"}, 400)
        text = text.replace("\x00", "").strip()
        if not text:
            return ({"ok": False, "error": "empty"}, 400)
        try:
            text.encode("utf-8")
        except Exception:
            return ({"ok": False, "error": "not_text"}, 400)
        truncated = len(text) > CLIP_MAX_CHARS
        if truncated:
            return ({"ok": False, "error": "too_long",
                     "limit": CLIP_MAX_CHARS, "got": len(text)}, 413)

        if not model_online():
            return ({"ok": False, "error": "model_offline"}, 503)

        spec = CLIP_ACTIONS[action]
        sys_prompt = _clip_fill(spec, b.get("opts"))
        payload = {
            "model": active_model(),
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": text}],
            "max_tokens": spec["max_tokens"],
            "temperature": spec["temperature"],
            "stream": False,
            # NOTE: no "tools" key, ever — this is the structural no-tool guarantee.
        }
        t0 = time.time()
        try:
            out = _clip_complete(payload)
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ValueError, KeyError) as e:
            return ({"ok": False, "error": "model_error",
                     "detail": str(e)[:200]}, 502)

        try:
            result = (out["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            return ({"ok": False, "error": "model_error",
                     "detail": "bad completion shape: " + str(e)[:120]}, 502)

        return {"ok": True, "action": action, "result": result,
                "model": payload["model"], "ms": int((time.time() - t0) * 1000),
                "in_chars": len(text), "out_chars": len(result),
                "truncated_input": truncated}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _clip_actions_payload():
    actions = {}
    for k, spec in CLIP_ACTIONS.items():
        actions[k] = {"label": spec["label"], "opts": spec.get("opts", [])}
    s = get_settings().get("clip") or {}
    return {"ok": True,
            "enabled": s.get("enabled") is not False,
            "order": CLIP_ACTION_ORDER,
            "actions": actions,
            "defaults": {"default_translate_to": s.get("default_translate_to", "English"),
                         "last_action": s.get("last_action", "summarize")}}


def _clip_actions_handler(ctx):
    try:
        return _cached("clip_actions", 10, _clip_actions_payload)
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


# --------------------------------------------------------------------------
# route registration (register_get/register_post live in server.py globals)
# --------------------------------------------------------------------------
# /api/clip/transform is the canonical endpoint the app/verification drives;
# /api/clip/run is registered as an alias so the P2.2 menu-bar popover and the
# P2.3 spec's "single shared endpoint" contract both resolve to the same handler.
register_post("/api/clip/transform", _clip_run_handler)
register_post("/api/clip/run", _clip_run_handler)
register_get("/api/clip/actions", _clip_actions_handler)
