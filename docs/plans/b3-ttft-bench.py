#!/usr/bin/env python3
"""B3 prefix-stability TTFT bench for the local mlx_lm server.

Measures client-side TTFT (time to first streamed content token) for the
same total prompt content with a volatile line FIRST vs LAST, plus an
exact-repeat ceiling. Stdlib only; temperature 0; max_tokens 16; sequential
short calls so it stays light on the model server.

Run:  python3 docs/plans/b3-ttft-bench.py
Then check ~/.hermes/logs/mlx-server.log "Prompt processing progress" lines
for the server-side count of tokens actually prefilled per request.
"""
import json
import time
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"

# Stable filler approximating a system-prompt-sized shared block (~4k tokens).
SENT = ("The assistant maintains a stable operating context. Rule %d: verify "
        "before asserting, prefer primary sources, and keep responses "
        "grounded in files actually read during the current working session. ")
STABLE = "".join(SENT % i for i in range(260))

Q = "\nReply with the single word: ok"


def volatile(minute):
    return f"[context] Local time: Friday 2026-07-04 14:{minute:02d} EDT.\n"


def ttft(prompt):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 16,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    first = None
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                delta = json.loads(line[6:])["choices"][0]["delta"]
            except (ValueError, KeyError, IndexError):
                continue
            if delta.get("content") and first is None:
                first = time.perf_counter() - t0
    total = time.perf_counter() - t0
    return first, total


CASES = [
    ("volatile-FIRST cold", volatile(11) + STABLE + Q),
    ("volatile-FIRST warm (minute changed)", volatile(12) + STABLE + Q),
    ("volatile-LAST cold", STABLE + volatile(13) + Q),
    ("volatile-LAST warm (minute changed)", STABLE + volatile(14) + Q),
    ("exact repeat (ceiling)", STABLE + volatile(14) + Q),
]

if __name__ == "__main__":
    print(f"stable block: {len(STABLE)} chars")
    for name, prompt in CASES:
        f, tot = ttft(prompt)
        print(f"{name:42s} TTFT {f:7.3f}s   total {tot:7.3f}s")
        time.sleep(1.0)
