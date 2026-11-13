#!/usr/bin/env python3
"""B28 — a comment inside `compression:` must not end the block.

A `# note` in column 0 used to read as the end of the block: the Compression
card showed runtime defaults for every key below the comment, and an edit
appended a SECOND `threshold:` above the comment while the original stayed
below it — a duplicate key PyYAML resolves to the old value, so the edit
silently did nothing.  aux_promptbudget fixed the same bug with `_pb_skippable`;
aux_context now follows the same rule.

Exec's the real aux module the way server.py does, against a throwaway HOME.
No model, no network, no dashboard."""
import io, os, re, sys, shutil, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_context.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def build(home, cfg_text):
    os.makedirs(os.path.join(home, ".hermes", "logs"), exist_ok=True)
    io.open(os.path.join(home, ".hermes", "config.yaml"), "w",
            encoding="utf-8").write(cfg_text)
    g = {"__name__": "aux_context_b28", "HOME": home,
         "DATA": os.path.join(home, ".hermes", "dashboard"),
         "_state_lock": threading.Lock(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def keys_in_block(text, block="compression"):
    """Every child key of `block:` as a YAML loader would see it: comments and
    blank lines skipped, the block ends at the next column-0 key."""
    out, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not line[0].isspace():
            inside = line.split(":", 1)[0] == block
            continue
        if inside:
            m = re.match(r"^(\s+)([A-Za-z0-9_]+):\s*(.*)$", line)
            if m and len(m.group(1)) == 2:
                out.append((m.group(2), m.group(3).split("#", 1)[0].strip()))
    return out


CFG = """model:
  default: mlx-community/Qwen3.8-27B
  context_length: 65536
compression:
  enabled: true
# tuned by hand 2026-09 — do not lower
  threshold: 0.5
  target_ratio: 0.2
  # protect the tail
  protect_last_n: 20
display:
  skin: default
"""

CFG_TAIL_COMMENT = """compression:
  threshold: 0.5

# ---- display ----
display:
  skin: default
"""


def run(tmp):
    print("\n1. the reader sees keys below a column-0 comment")
    home = os.path.join(tmp, "a")
    g = build(home, CFG)
    cfg = g["_cx_read_cfg"]()
    check("threshold read through the comment", cfg.get("threshold") == 0.5, cfg)
    check("target_ratio read", cfg.get("target_ratio") == 0.2, cfg)
    check("protect_last_n read under an indented comment",
          cfg.get("protect_last_n") == 20, cfg)
    check("enabled still read", cfg.get("enabled") is True, cfg)
    check("model section unaffected", cfg.get("context_length") == 65536, cfg)

    print("\n2. the writer rewrites the existing key, it does not duplicate it")
    out = g["_cx_apply_text"](CFG, {"threshold": 0.7})
    ks = keys_in_block(out)
    names = [k for k, _ in ks]
    check("exactly one threshold key", names.count("threshold") == 1, out)
    check("and it carries the new value", ("threshold", "0.7") in ks, out)
    check("the comment is preserved byte for byte",
          "\n# tuned by hand 2026-09 — do not lower\n  threshold: 0.7\n" in out, out)
    check("nothing else moved",
          out.replace("threshold: 0.7", "threshold: 0.5") == CFG, out)

    print("\n3. a key the block lacks lands inside the block, after its last line")
    out = g["_cx_apply_text"](CFG, {"protect_first_n": 4})
    ks = keys_in_block(out)
    check("protect_first_n added once", [k for k, _ in ks].count("protect_first_n") == 1, out)
    check("right after the block's last child, before display:",
          "  protect_last_n: 20\n  protect_first_n: 4\ndisplay:\n" in out, out)
    out = g["_cx_apply_text"](CFG_TAIL_COMMENT, {"target_ratio": 0.3})
    check("a trailing comment that introduces the next section stays with it",
          out == "compression:\n  threshold: 0.5\n  target_ratio: 0.3\n\n"
                 "# ---- display ----\ndisplay:\n  skin: default\n", out)

    print("\n4. an indented comment first does not set the child indent")
    src = "compression:\n    # explained below\n  threshold: 0.5\n"
    out = g["_cx_apply_text"](src, {"threshold": 0.6})
    check("rewritten in place at the real child indent",
          out == "compression:\n    # explained below\n  threshold: 0.6\n", out)

    print("\n5. the POST path round-trips through the file")
    r = g["_cx_compression_post"](type("C", (), {"body": {"threshold": 0.66},
                                                "query": {}})())
    body = r[0] if isinstance(r, tuple) else r
    text = io.open(os.path.join(home, ".hermes", "config.yaml"), encoding="utf-8").read()
    ks = keys_in_block(text)
    check("post answered ok", isinstance(body, dict) and body.get("ok") is True, body)
    check("file has one threshold = 0.66",
          [v for k, v in ks if k == "threshold"] == ["0.66"], text)
    check("and the reader agrees", g["_cx_read_cfg"]().get("threshold") == 0.66)


def main():
    tmp = tempfile.mkdtemp(prefix="b28-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
