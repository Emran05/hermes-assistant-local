#!/usr/bin/env python3
"""Unit tests for the tool-budget plugin's retention function + hook.

Runs against a sandbox HERMES_HOME. No model, no network, no real ~/.hermes.
"""
import datetime
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PLUGIN = os.path.join(REPO, "hermes-plugins", "tool-budget", "__init__.py")

SANDBOX = tempfile.mkdtemp(prefix="tb-home-")
os.environ["HERMES_HOME"] = SANDBOX
os.makedirs(os.path.join(SANDBOX, "dashboard"), mode=0o700, exist_ok=True)

spec = importlib.util.spec_from_file_location("tool_budget_test", PLUGIN)
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)

PASS, FAIL = [], []

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (len(PASS), len(FAIL))))


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""))


def write_settings(obj):
    p = os.path.join(SANDBOX, "dashboard", "settings.json")
    if obj is None:
        if os.path.exists(p):
            os.remove(p)
        return
    with open(p, "w") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def clean_spill_and_log():
    shutil.rmtree(os.path.join(SANDBOX, "dashboard", "spill"), ignore_errors=True)
    for n in ("tool-budget.jsonl", "tool-budget.jsonl.1"):
        p = os.path.join(SANDBOX, "dashboard", n)
        if os.path.exists(p):
            os.remove(p)
    tb._gc_day[0] = ""


def log_rows():
    p = os.path.join(SANDBOX, "dashboard", "tool-budget.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def spill_files():
    root = os.path.join(SANDBOX, "dashboard", "spill")
    out = []
    for d, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.join(d, f))
    return out


print("\n=== 1. under-budget passthrough (no I/O, returns None) ===")
write_settings({"tool_budget": {"enabled": True, "max_chars": 24000, "spill": True}})
clean_spill_and_log()
small = "hello\n" * 100                      # 600 chars
check("<= 4000 chars returns None", tb.transform_tool_result(tool_name="read_file", result=small) is None)
mid = "x" * 10000                            # over the fast path, under 24k
check("under configured budget returns None", tb.transform_tool_result(tool_name="read_file", result=mid) is None)
check("no spill file was written", spill_files() == [], spill_files())
check("no log line was written", log_rows() == [])

print("\n=== 2. over-budget text: head/tail + marker + 0600 spill ===")
clean_spill_and_log()
lines = ["LINE-%05d %s" % (i, "abcdefghij" * 6) for i in range(2000)]
big = "\n".join(lines)                       # ~146k chars
out = tb.transform_tool_result(tool_name="read_file", result=big)
check("returned a string", isinstance(out, str))
check("output <= max_chars", len(out) <= 24000, len(out))
check("output much smaller than input", len(out) < len(big) / 4)
check("marker present", "omitted by tool-budget" in out)
check("marker says TRUNCATED not error", "TRUNCATED" in out and "SUCCEEDED" in out)
check("marker names the spill path", (spill_files() and spill_files()[0] in out) or False, spill_files())
check("no real home path leaked", os.path.expanduser("~") not in out.replace(SANDBOX, ""))
check("head kept (first line present)", out.startswith("LINE-00000"))
check("tail kept (last line present)", out.rstrip().endswith(lines[-1]))
sp = spill_files()
check("exactly one spill file", len(sp) == 1, sp)
if sp:
    st = os.stat(sp[0])
    check("spill file mode 0600", stat.S_IMODE(st.st_mode) == 0o600, oct(stat.S_IMODE(st.st_mode)))
    dmode = stat.S_IMODE(os.stat(os.path.dirname(sp[0])).st_mode)
    check("spill dir mode 0700", dmode == 0o700, oct(dmode))
    with open(sp[0]) as fh:
        check("spill holds the FULL original", fh.read() == big)
    check("spill dir is today's date", os.path.basename(os.path.dirname(sp[0])) == datetime.date.today().isoformat())
rows = log_rows()
check("one log row", len(rows) == 1, rows)
if rows:
    r = rows[0]
    check("log row shape", set(("ts", "tool", "kept_chars", "omitted_chars", "spill_path", "session", "est_tokens_saved")) <= set(r), sorted(r))
    check("log kept_chars matches", r["kept_chars"] == len(out), (r["kept_chars"], len(out)))
    check("log omitted_chars > 0", r["omitted_chars"] > 0)

print("\n=== 2b. head/tail bias per tool ===")
head_out = tb.retain(big, 24000, tool_name="read_file", spill_path="/x/y.txt")
tail_out = tb.retain(big, 24000, tool_name="terminal", spill_path="/x/y.txt")
hh = head_out.split("\n[…")[0]
ht = tail_out.split("\n[…")[0]
check("read_file is head-heavy (65/35)", len(hh) > len(ht) * 1.5, (len(hh), len(ht)))
check("terminal keeps the END", tail_out.rstrip().endswith(lines[-1]))
tail_tail = tail_out.split("…]\n", 1)[1]
head_tail = head_out.split("…]\n", 1)[1]
check("terminal's tail is bigger than read_file's", len(tail_tail) > len(head_tail) * 1.5, (len(tail_tail), len(head_tail)))

print("\n=== 2c. cuts land on line boundaries ===")
check("head ends on a line boundary", hh.endswith("\n") or hh == "")
check("tail starts on a whole line", head_tail.startswith("LINE-"))

print("\n=== 3. JSON that fits after minification stays WHOLE ===")
clean_spill_and_log()
obj = {"items": [{"id": i, "name": "item-%d" % i} for i in range(150)]}
pretty = json.dumps(obj, indent=4)           # padded well over 4000 chars
check("pretty JSON is over the fast path", len(pretty) > 4000, len(pretty))
write_settings({"tool_budget": {"max_chars": 8000}})
out = tb.transform_tool_result(tool_name="search_files", result=pretty)
check("JSON was rewritten", isinstance(out, str) and out != pretty)
check("JSON still parses", json.loads(out) == obj)
check("JSON fits the budget", len(out) <= 8000, len(out))
check("no marker in a whole-JSON result", "omitted by tool-budget" not in out)
check("no spill for a whole-JSON result", spill_files() == [])
check("no log row for a whole-JSON result", log_rows() == [])

print("\n=== 3b. JSON too big even minified falls back to text head/tail ===")
clean_spill_and_log()
huge = json.dumps({"items": [{"id": i, "blob": "z" * 200} for i in range(400)]})
write_settings({"tool_budget": {"max_chars": 8000}})
out = tb.transform_tool_result(tool_name="web_extract", result=huge)
check("huge JSON was truncated", isinstance(out, str) and "omitted by tool-budget" in out)
check("huge JSON output within budget", len(out) <= 8000, len(out))
check("spilled", len(spill_files()) == 1)

print("\n=== 4. multi-byte text is never split mid-character ===")
clean_spill_and_log()
write_settings({"tool_budget": {"max_chars": 5000}})
uni = ("日本語のテキスト — émoji 🌊🚀 ‑ ünïcödé line %d\n" % 0) * 0
uni = "".join("日本語テキスト %d — émoji 🌊🚀 ünïcödé\n" % i for i in range(600))
out = tb.transform_tool_result(tool_name="read_file", result=uni)
check("returned a string", isinstance(out, str))
check("round-trips through utf-8 unchanged", out.encode("utf-8").decode("utf-8") == out)
check("no replacement char introduced", "�" not in out)
check("no lone surrogate", all(not (0xD800 <= ord(c) <= 0xDFFF) for c in out))
head = out.split("\n[…")[0]
check("head is a prefix of the original", uni.startswith(head), repr(head[-20:]))
tail = out.split("…]\n", 1)[1]
check("tail is a suffix of the original", uni.endswith(tail), repr(tail[:20]))
sp = spill_files()
if sp:
    with open(sp[0], encoding="utf-8") as fh:
        check("spill round-trips the unicode", fh.read() == uni)
# a string with NO newlines at all (one long line of multi-byte text)
oneline = "漢字ひらがなカタカナ🌊" * 2000
out2 = tb.transform_tool_result(tool_name="terminal", result=oneline)
check("no-newline text is still cut safely", isinstance(out2, str) and out2.encode("utf-8").decode("utf-8") == out2)
check("no-newline text fits the budget", len(out2) <= 5000, len(out2))
check("no-newline head is a real prefix", oneline.startswith(out2.split("\n[…")[0]))

print("\n=== 5. corrupt / missing / hostile settings -> defaults ===")
clean_spill_and_log()
for label, payload in (("corrupt JSON", "{not json at all"),
                       ("empty file", ""),
                       ("missing file", None),
                       ("tool_budget is a string", {"tool_budget": "yes"}),
                       ("tool_budget is null", {"tool_budget": None}),
                       ("settings is a list", "[1,2,3]")):
    write_settings(payload)
    cfg = tb._read_config()
    check("%s -> defaults" % label, cfg == tb._DEFAULTS, cfg)
write_settings({"tool_budget": {"max_chars": 10}})
check("max_chars below the floor is ignored", tb._read_config()["max_chars"] == 24000)
write_settings({"tool_budget": {"max_chars": 9999999}})
check("max_chars above the ceiling is ignored", tb._read_config()["max_chars"] == 24000)
write_settings({"tool_budget": {"max_chars": "24000"}})
check("max_chars as a string is ignored", tb._read_config()["max_chars"] == 24000)
write_settings({"tool_budget": {"max_chars": True}})
check("max_chars as a bool is ignored", tb._read_config()["max_chars"] == 24000)
write_settings({"tool_budget": {"enabled": "no"}})
check("enabled as a string is ignored", tb._read_config()["enabled"] is True)
write_settings({"tool_budget": {"max_chars": 6000, "enabled": False, "spill": False}})
check("valid values are honoured", tb._read_config() == {"enabled": False, "max_chars": 6000, "spill": False}, tb._read_config())
check("disabled -> passthrough", tb.transform_tool_result(tool_name="terminal", result="q" * 50000) is None)

print("\n=== 5b. spill:false still truncates, marker says so ===")
clean_spill_and_log()
write_settings({"tool_budget": {"enabled": True, "max_chars": 6000, "spill": False}})
out = tb.transform_tool_result(tool_name="terminal", result="line\n" * 5000)
check("still truncated with spill off", isinstance(out, str) and "omitted by tool-budget" in out)
check("no spill file", spill_files() == [])
check("marker admits the middle is gone", "spill is off" in out)
check("log row has an empty spill_path", log_rows() and log_rows()[0]["spill_path"] == "")

print("\n=== 6. an exception inside returns the ORIGINAL (None) ===")
clean_spill_and_log()
write_settings({"tool_budget": {"enabled": True, "max_chars": 6000, "spill": True}})
orig_retain = tb.retain
try:
    tb.retain = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    check("retain() raising -> None (result unchanged)", tb.transform_tool_result(tool_name="terminal", result="z" * 50000) is None)
finally:
    tb.retain = orig_retain
orig_cfg = tb._read_config
try:
    tb._read_config = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    check("_read_config() raising -> None", tb.transform_tool_result(tool_name="terminal", result="z" * 50000) is None)
finally:
    tb._read_config = orig_cfg
check("non-str result -> None", tb.transform_tool_result(tool_name="terminal", result={"a": 1}) is None)
check("None result -> None", tb.transform_tool_result(tool_name="terminal", result=None) is None)
orig_spill = tb._spill_write
try:
    tb._spill_write = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    r = tb.transform_tool_result(tool_name="terminal", result="line\n" * 5000)
    check("a failing spill does not lose the result", r is None or isinstance(r, str))
finally:
    tb._spill_write = orig_spill
# the hook tolerates the extra kwargs the runtime always passes
r = tb.transform_tool_result(tool_name="terminal", args={"c": "ls"}, result="q\n" * 5000,
                             task_id="t", session_id="s", tool_call_id="tc", turn_id="tu",
                             api_request_id="a", duration_ms=12, status="ok",
                             error_type=None, error_message=None,
                             telemetry_schema_version=3)
check("accepts the runtime's full kwarg set", isinstance(r, str))
check("session hint recorded", log_rows() and log_rows()[-1]["session"] == "s", log_rows()[-1:] )

print("\n=== 7. spill GC drops day-dirs older than 7 days ===")
clean_spill_and_log()
root = os.path.join(SANDBOX, "dashboard", "spill")
old = (datetime.date.today() - datetime.timedelta(days=9)).isoformat()
recent = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
for d in (old, recent, "not-a-date"):
    os.makedirs(os.path.join(root, d), mode=0o700, exist_ok=True)
    open(os.path.join(root, d, "x.txt"), "w").write("x")
tb._gc_day[0] = ""
tb._maybe_gc()
check("9-day-old dir deleted", not os.path.exists(os.path.join(root, old)))
check("2-day-old dir kept", os.path.exists(os.path.join(root, recent)))
check("non-date dir untouched", os.path.exists(os.path.join(root, "not-a-date")))
before = tb._gc_day[0]
check("gc marked for today", before == datetime.date.today().isoformat())

print("\n=== 8. log rotation at ~2 MB ===")
clean_spill_and_log()
p = os.path.join(SANDBOX, "dashboard", "tool-budget.jsonl")
with open(p, "w") as fh:
    fh.write("x" * (2 * 1024 * 1024 + 10))
tb._append_log({"ts": "now", "tool": "t"})
check("rotated to .jsonl.1", os.path.exists(p + ".1"))
check("fresh log is small", os.path.getsize(p) < 200, os.path.getsize(p))

print("\n=== 9. register() wires the hook name the runtime fires ===")


class _Ctx:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, cb):
        self.hooks.append((name, cb))


c = _Ctx()
tb.register(c)
check("registers exactly one hook", len(c.hooks) == 1, c.hooks)
check("hook name is transform_tool_result", c.hooks and c.hooks[0][0] == "transform_tool_result")


# ===========================================================================
# review fixes 5/6/7 — the marker's third branch, and the swallowed failures
# that used to log nothing at all
# ===========================================================================
import contextlib
import io as _io


@contextlib.contextmanager
def capture_stderr():
    buf = _io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = old


def reset_said():
    for k in list(tb._said):
        tb._said[k] = False


print("\n=== 10. the marker has THREE branches (review fix 6) ===")
m_off = tb._marker(9, 900, None, False)
m_fail = tb._marker(9, 900, None, True)
m_ok = tb._marker(9, 900, "/x/y/z.txt", False)
check("spill off says so", "spill is off" in m_off, m_off)
check("spill FAILED does not say 'spill is off'", "spill is off" not in m_fail, m_fail)
check("spill FAILED says it could not be saved",
      "could NOT be saved to disk" in m_fail, m_fail)
check("a written spill names the path", "/x/y/z.txt" in m_ok, m_ok)
check("all three still say the tool SUCCEEDED",
      all("the tool SUCCEEDED" in m for m in (m_off, m_fail, m_ok)))
check("retain() carries the intent through",
      "could NOT be saved" in tb.retain("l\n" * 4000, 6000, tool_name="terminal",
                                        spill_path=None, spill_failed=True))
check("retain() default is unchanged (spill is off)",
      "spill is off" in tb.retain("l\n" * 4000, 6000, tool_name="terminal"))

print("\n=== 10b. a failed spill WRITE reaches marker + log + stderr ===")
clean_spill_and_log()
reset_said()
write_settings({"tool_budget": {"enabled": True, "max_chars": 6000, "spill": True}})
real_write = tb._spill_write
tb._spill_write = lambda tool, text: None          # the disk refuses
try:
    with capture_stderr() as err:
        out = tb.transform_tool_result(tool_name="terminal", result="line\n" * 5000)
    txt = err.getvalue()
finally:
    tb._spill_write = real_write
check("still truncated", isinstance(out, str) and "omitted by tool-budget" in out)
check("marker says the save failed", "could NOT be saved to disk" in out, out[:400])
check("marker does NOT claim spill is off", "spill is off" not in out)
rows = log_rows()
check("log row records spill_failed", rows and rows[-1].get("spill_failed") is True, rows[-1:])
check("log row has an empty spill_path", rows and rows[-1]["spill_path"] == "")
check("a failed spill complains once on stderr", "tool-budget:" in txt, txt)

print("\n=== 10c. spill:false records spill_failed False ===")
clean_spill_and_log()
write_settings({"tool_budget": {"enabled": True, "max_chars": 6000, "spill": False}})
tb.transform_tool_result(tool_name="terminal", result="line\n" * 5000)
rows = log_rows()
check("spill off is not spill failed", rows and rows[-1].get("spill_failed") is False, rows[-1:])

print("\n=== 11. the hook's own failure is logged ONCE (review fix 5) ===")
clean_spill_and_log()
reset_said()
write_settings({"tool_budget": {"enabled": True, "max_chars": 6000, "spill": False}})
orig = tb.retain
tb.retain = lambda *a, **k: (_ for _ in ()).throw(ValueError("kaboom"))
try:
    with capture_stderr() as err:
        r1 = tb.transform_tool_result(tool_name="terminal", result="z" * 50000)
        r2 = tb.transform_tool_result(tool_name="terminal", result="z" * 50000)
        r3 = tb.transform_tool_result(tool_name="terminal", result="z" * 50000)
    txt = err.getvalue()
finally:
    tb.retain = orig
check("still fail-open (None)", r1 is None and r2 is None and r3 is None)
check("the failure is on stderr", "hook failed, results pass through untrimmed" in txt, txt)
check("it names the exception", "ValueError: kaboom" in txt, txt)
check("exactly ONE line for three failures", txt.count("hook failed") == 1, txt)

print("\n=== 11b. an unwritable log complains once, and only once ===")
clean_spill_and_log()
reset_said()
lp = os.path.join(SANDBOX, "dashboard", "tool-budget.jsonl")
os.makedirs(lp, exist_ok=True)                     # a DIRECTORY where the log goes
try:
    with capture_stderr() as err:
        tb._append_log({"ts": "a"})
        tb._append_log({"ts": "b"})
    txt = err.getvalue()
finally:
    shutil.rmtree(lp, ignore_errors=True)
check("an unwritable log says so", "the truncation log could not be written" in txt, txt)
check("and says it only once", txt.count("could not be written") == 1, txt)

print("\n=== 12. register() also runs the spill GC (review fix 7) ===")
clean_spill_and_log()
root = os.path.join(SANDBOX, "dashboard", "spill")
stale_day = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
os.makedirs(os.path.join(root, stale_day), mode=0o700, exist_ok=True)
open(os.path.join(root, stale_day, "x.txt"), "w").write("x")
tb._gc_day[0] = ""
c2 = _Ctx()
tb.register(c2)
check("register() swept the old day-dir", not os.path.exists(os.path.join(root, stale_day)))
check("register() still wires the hook",
      len(c2.hooks) == 1 and c2.hooks[0][0] == "transform_tool_result", c2.hooks)

orig_gc = tb._maybe_gc
tb._maybe_gc = lambda: (_ for _ in ()).throw(OSError("read-only disk"))
try:
    c3 = _Ctx()
    tb.register(c3)
finally:
    tb._maybe_gc = orig_gc
check("a GC failure never stops registration",
      len(c3.hooks) == 1 and c3.hooks[0][0] == "transform_tool_result", c3.hooks)

shutil.rmtree(SANDBOX, ignore_errors=True)
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
