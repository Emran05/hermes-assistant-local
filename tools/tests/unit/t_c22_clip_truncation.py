#!/usr/bin/env python3
"""C22 — a clipboard transform cut off at max_tokens must say so.

_clip_run_handler accepts up to CLIP_MAX_CHARS of input but caps the output at
the action's max_tokens, and it never read finish_reason: a proofread of a
long email that stopped at 1,400 tokens came back ok:true as if it were the
whole corrected text, with truncated_input:false reading as "nothing was cut".
Now the payload carries finish_reason and truncated_output.

Exec's the real aux_clip.py with server.py's globals stubbed and the one model
call (_clip_complete) replaced — no model, no network, no dashboard."""
import io, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_clip.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " %r" % (detail,)))
    if not cond:
        FAILS.append(name)


class Ctx:
    def __init__(self, body):
        self.body = body
        self.query = {}


def build():
    routes = {}
    g = {"__name__": "aux_clip_c22",
         "MODEL_URL": "http://127.0.0.1:8080/v1/models",
         "model_online": lambda: True,
         "active_model": lambda: "test-model",
         "get_settings": lambda: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: routes.__setitem__(("GET", p), f),
         "register_post": lambda p, f: routes.__setitem__(("POST", p), f)}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g, routes


def answer(g, completion):
    sent = []

    def fake(payload):
        sent.append(payload)
        return completion
    g["_clip_complete"] = fake
    return sent


def body_of(r):
    return r[0] if isinstance(r, tuple) else r


def run():
    g, routes = build()
    handler = routes[("POST", "/api/clip/transform")]
    long_text = "word " * 3000                # 15,000 chars: accepted (< 16,000)

    print("\n1. finish_reason 'length' -> ok, but flagged as cut off")
    sent = answer(g, {"choices": [{"message": {"content": "partial text"},
                                   "finish_reason": "length"}],
                      "usage": {"completion_tokens": 1400}})
    r = body_of(handler(Ctx({"action": "proofread", "text": long_text})))
    check("still ok (the partial result is usable)", r.get("ok") is True, r)
    check("truncated_output is true", r.get("truncated_output") is True, r)
    check("finish_reason passed through", r.get("finish_reason") == "length", r)
    check("the cap that was hit is reported",
          r.get("max_tokens") == g["CLIP_ACTIONS"]["proofread"]["max_tokens"], r)
    check("the request really carried that cap",
          sent and sent[0].get("max_tokens") == r.get("max_tokens"),
          sent and sent[0].get("max_tokens"))
    check("no tools field ever (the structural no-tool guarantee holds)",
          sent and "tools" not in sent[0], sent and sorted(sent[0]))

    print("\n2. finish_reason 'stop' -> not flagged")
    answer(g, {"choices": [{"message": {"content": "all of it"},
                            "finish_reason": "stop"}]})
    r = body_of(handler(Ctx({"action": "summarize", "text": "short text"})))
    check("ok", r.get("ok") is True, r)
    check("truncated_output is false", r.get("truncated_output") is False, r)
    check("finish_reason 'stop'", r.get("finish_reason") == "stop", r)

    print("\n3. a server that omits finish_reason: usage at the cap still flags it")
    answer(g, {"choices": [{"message": {"content": "x"}}],
               "usage": {"completion_tokens": 1500}})
    r = body_of(handler(Ctx({"action": "translate", "text": long_text})))
    check("flagged from usage.completion_tokens >= max_tokens",
          r.get("truncated_output") is True, r)
    answer(g, {"choices": [{"message": {"content": "x"}}],
               "usage": {"completion_tokens": 12}})
    r = body_of(handler(Ctx({"action": "translate", "text": "hola"})))
    check("well under the cap -> not flagged", r.get("truncated_output") is False, r)
    answer(g, {"choices": [{"message": {"content": "x"}}]})
    r = body_of(handler(Ctx({"action": "translate", "text": "hola"})))
    check("no finish_reason and no usage -> not flagged, no crash",
          r.get("ok") is True and r.get("truncated_output") is False, r)
    answer(g, {"choices": [{"message": {"content": "x"}}],
               "usage": {"completion_tokens": "garbage"}})
    r = body_of(handler(Ctx({"action": "translate", "text": "hola"})))
    check("a malformed usage block is not an error",
          r.get("ok") is True and r.get("truncated_output") is False, r)

    print("\n4. the old field keeps its (honest) meaning")
    check("truncated_input stays false: over-limit input is refused, not clamped",
          r.get("truncated_input") is False, r)
    r2 = handler(Ctx({"action": "summarize", "text": "a" * 20000}))
    check("over-limit input is still a 413",
          isinstance(r2, tuple) and r2[1] == 413, r2)


def main():
    run()
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
