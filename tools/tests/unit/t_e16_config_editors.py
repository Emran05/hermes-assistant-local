#!/usr/bin/env python3
"""E16 / E17 — the three config.yaml editors on hand-edited files.

The editors (hermes-plugins/plugin_enable.py, dashboard/aux_promptbudget.py,
dashboard/aux_context.py) edit ~/.hermes/config.yaml as TEXT.  Two bug shapes
from the 2026-11-09 audit:

  E16  a block list written at its key's OWN indent (PyYAML's default dump
       style, `enabled:\\n  - a`) read as [] and was then written as a deeper
       list above the old items: a file PyYAML rejects, reported as success.
  E17  decoration on the section header line (`plugins:  # note`, a quoted
       key, flow style) was not recognised, so a SECOND top-level block was
       appended and YAML last-wins silently replaced the original.

For every file in the corpus below, each editor's real WRITE path either
applies the edit and the result parses to exactly the intended structure
(every other top-level block byte-identical, the section's other keys
unchanged, no duplicate key), or it writes NOTHING (file bytes, mode and the
backup set unchanged) and raises an error naming the line.  The structure is
asserted with doctor.py's stdlib YAML-subset reader; when `import yaml` works
in the running Python, PyYAML cross-checks every written file too (silent
otherwise — the unit tier's Python has no PyYAML).

Also: the drift guard on the copy of doctor's reader inside plugin_enable.py,
plugin_enable.py's exit status, and the error reaching the two dashboard
routes and the Tool budget install path.
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PE_PATH = os.path.join(REPO, "hermes-plugins", "plugin_enable.py")
PB_PATH = os.path.join(REPO, "dashboard", "aux_promptbudget.py")
CX_PATH = os.path.join(REPO, "dashboard", "aux_context.py")
TB_PATH = os.path.join(REPO, "dashboard", "aux_toolbudget.py")
DOC_PATH = os.path.join(REPO, "dashboard", "doctor.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + repr(detail)[:400]) if detail and not cond else ""))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PE = _load("pe_e16", PE_PATH)
DOC = _load("doctor_e16", DOC_PATH)

try:                                    # optional cross-check, silent if absent
    import yaml as _yaml                # noqa: F401
except Exception:                       # pragma: no cover — the unit tier's case
    _yaml = None

SB = tempfile.mkdtemp(prefix="e16-")
HERMES_DIR = os.path.join(SB, ".hermes")
os.makedirs(os.path.join(HERMES_DIR, "dashboard"), exist_ok=True)
CFG = os.path.join(HERMES_DIR, "config.yaml")

STATE_LOCK = threading.Lock()
PB = {"__name__": "aux_promptbudget", "HOME": SB,
      "HERMES": os.path.join(SB, "bin", "hermes"),
      "_hermes_env": lambda: dict(os.environ), "_state_lock": STATE_LOCK,
      "register_get": lambda p, f: None, "register_post": lambda p, f: None}
exec(compile(open(PB_PATH, encoding="utf-8").read(), PB_PATH, "exec"), PB)
CX = {"__name__": "aux_context", "HOME": SB,
      "DATA": os.path.join(HERMES_DIR, "dashboard"), "_state_lock": STATE_LOCK,
      "register_get": lambda p, f: None, "register_post": lambda p, f: None}
exec(compile(open(CX_PATH, encoding="utf-8").read(), CX_PATH, "exec"), CX)


# ---------------------------------------------------------------------------
# an independent structural reader: doctor's, plus a duplicate-key scan
# ---------------------------------------------------------------------------
def parse(text):
    return DOC._hcfg_parse(text)


def top_keys(text):
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line or line[0] in " \t#" or line.startswith(("---", "...", "%", "-")):
            continue
        out.append(line.split(":", 1)[0].strip().strip("'\""))
    return out


def segments(text):
    """{top-level key: its lines} with the key line, children and trailing
    comments — the unit the editors must leave byte-identical."""
    segs, cur = {}, None
    for line in text.replace("\r\n", "\n").split("\n"):
        if line and line[0] not in " \t#-":
            cur = line.split(":", 1)[0].strip().strip("'\"")
            segs.setdefault(cur, [])
        if cur is not None:
            segs[cur].append(line)
    out = {}
    for k, v in segs.items():
        while v and not v[-1].strip():
            v = v[:-1]
        out[k] = v
    return out


def canon(v):
    """Order-insensitive for mappings (YAML mappings are unordered), exact for
    everything else, types included (True is not 1, 0.6 is not "0.6")."""
    if isinstance(v, dict):
        return "{" + ",".join("%r:%s" % (k, canon(v[k])) for k in sorted(v, key=repr)) + "}"
    if isinstance(v, list):
        return "[" + ",".join(canon(x) for x in v) + "]"
    return "%s(%r)" % (type(v).__name__, v)


def pyyaml_ok(text):
    if _yaml is None:
        return True, None
    try:
        return True, _yaml.safe_load(text)
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
HEAD = "model:\n  default: mlx-community/Qwen3.8-27B-4bit\n  context_length: 65536\n"
TAIL = ("auxiliary:\n  compression:\n    model: auto\n"
        "toolsets:\n- web\n- terminal\n"            # an indentless list elsewhere
        "display:\n  skin: default\n")

SECTIONS = {
    "plugins": {
        "indented": ("plugins:\n  enabled:\n    - loop-breaker\n    - tool-budget\n"
                     "  disabled: []\n  entries:\n    loop-breaker:\n"
                     "      enabled: true\n"),
        "indentless": ("plugins:\n  enabled:\n  - loop-breaker\n  - tool-budget\n"
                       "  disabled: []\n"),
        "header_comment": ("plugins:  # mine\n  enabled:\n    - loop-breaker\n"
                           "  entries:\n    loop-breaker:\n      enabled: true\n"),
        "header_comment_indentless": ("plugins:   # local\n  enabled:\n"
                                      "  - loop-breaker\n  disabled: []\n"),
        "header_trailing_space": "plugins:   \n  enabled:\n    - loop-breaker\n",
        "inner_comment": ("plugins:\n  enabled:\n    - loop-breaker\n# hand note\n"
                          "    - tool-budget\n  disabled: []\n"),
        "key_comment": "plugins:\n  enabled:   # keep\n  - loop-breaker\n",
        "empty_flow": "plugins:\n  enabled: []\n  disabled: []\n",
        "flow_leaf": "plugins:\n  enabled: [loop-breaker, tool-budget]  # two\n",
        "no_enabled": "plugins:\n  disabled: []\n",
        "missing": "",
        "nested_same_name": "custom:\n  plugins:\n    enabled:\n    - other\n",
        # refused
        "quoted_key": "\"plugins\":\n  enabled:\n    - loop-breaker\n",
        "single_quoted_key": "'plugins':\n  enabled:\n    - loop-breaker\n",
        "flow_map": "plugins: {enabled: [loop-breaker, tool-budget]}\n",
        "flow_map_comment": "plugins: {enabled: [loop-breaker]}   # x\n",
        "scalar_header": "plugins: null\n",
        "duplicate": ("plugins:\n  enabled:\n    - loop-breaker\n"
                      "plugins:\n  enabled:\n    - tool-budget\n"),
        "duplicate_child": ("plugins:\n  enabled:\n    - loop-breaker\n"
                            "  enabled:\n    - tool-budget\n"),
        "tab_indent": "plugins:\n\tenabled:\n\t  - loop-breaker\n",
        "anchor": "plugins:\n  enabled: &pl\n    - loop-breaker\n",
        "alias": "plugins:\n  enabled: *pl\n",
        "merge": "plugins:\n  <<: *base\n  enabled:\n    - loop-breaker\n",
        "multi_doc": "plugins:\n  enabled:\n    - loop-breaker\n---\nother: 1\n",
        "scalar_enabled": "plugins:\n  enabled: loop-breaker\n",
        "multiline_flow": "plugins:\n  enabled: [loop-breaker,\n    tool-budget]\n",
    },
    "platform_toolsets": {
        "indented": ("platform_toolsets:\n  cli:\n    - hermes-cli\n"
                     "  telegram:\n    - hermes-telegram\n"),
        "indentless": ("platform_toolsets:\n  cli:\n  - hermes-cli\n  - memory\n"
                       "  telegram:\n  - hermes-telegram\n"),
        "header_comment": ("platform_toolsets: # per surface\n  cli:\n"
                           "    - hermes-cli\n  telegram:\n    - hermes-telegram\n"),
        "header_comment_indentless": ("platform_toolsets:  # mine\n  telegram:\n"
                                      "  - hermes-telegram\n  cli:\n  - hermes-cli\n"),
        "header_trailing_space": ("platform_toolsets:  \n  cli:\n    - hermes-cli\n"),
        "inner_comment": ("platform_toolsets:\n  cli:\n    - hermes-cli\n# note\n"
                          "    - memory\n  telegram:\n    - hermes-telegram\n"),
        "key_comment": ("platform_toolsets:\n  cli:   # mine\n  - hermes-cli\n"
                        "  telegram:\n  - hermes-telegram\n"),
        "empty_flow": "platform_toolsets:\n  cli: []\n  telegram: [hermes-telegram]\n",
        "flow_leaf": "platform_toolsets:\n  cli: [hermes-cli, memory]  # two\n",
        "no_enabled": "platform_toolsets:\n  telegram:\n    - hermes-telegram\n",
        "missing": "",
        "nested_same_name": "custom:\n  platform_toolsets:\n    cli:\n    - x\n",
        "quoted_key": "\"platform_toolsets\":\n  cli:\n    - hermes-cli\n",
        "single_quoted_key": "'platform_toolsets':\n  cli:\n    - hermes-cli\n",
        "flow_map": "platform_toolsets: {cli: [hermes-cli], telegram: [web]}\n",
        "flow_map_comment": "platform_toolsets: {cli: [hermes-cli]}  # x\n",
        "scalar_header": "platform_toolsets: ~\n",
        "duplicate": ("platform_toolsets:\n  cli:\n    - hermes-cli\n"
                      "platform_toolsets:\n  telegram:\n    - web\n"),
        "duplicate_child": ("platform_toolsets:\n  cli:\n    - hermes-cli\n"
                            "  cli:\n    - memory\n"),
        "tab_indent": "platform_toolsets:\n\tcli:\n\t  - hermes-cli\n",
        "anchor": "platform_toolsets:\n  cli: &c\n    - hermes-cli\n  telegram: *c\n",
        "alias": "platform_toolsets:\n  telegram: *c\n",
        "merge": "platform_toolsets:\n  <<: *base\n  cli:\n    - hermes-cli\n",
        "multi_doc": "platform_toolsets:\n  cli:\n    - hermes-cli\n...\n---\nother: 1\n",
        "scalar_enabled": "platform_toolsets:\n  cli: hermes-cli\n  telegram:\n    - web\n",
        "multiline_flow": "platform_toolsets:\n  cli: [hermes-cli,\n    memory]\n",
    },
    "compression": {
        "indented": ("compression:\n  enabled: false\n  threshold: 0.7\n"
                     "  target_ratio: 0.2\n"),
        "indentless": ("compression:\n  enabled: false\n  threshold: 0.7\n"
                       "  extra:\n  - a\n  - b\n"),
        "header_comment": ("compression:   # tuned by hand\n  enabled: false\n"
                           "  threshold: 0.7\n  target_ratio: 0.2\n"),
        "header_comment_indentless": ("compression:  # x\n  enabled: false\n"
                                      "  extra:\n  - a\n"),
        "header_trailing_space": "compression:   \n  enabled: false\n  threshold: 0.7\n",
        "inner_comment": ("compression:\n  enabled: false\n# tuned 2026-09\n"
                          "  threshold: 0.5\n  # protect the tail\n"
                          "  protect_last_n: 20\n"),
        "key_comment": "compression:\n  enabled: false\n  threshold: 0.7   # mine\n",
        "empty_flow": "compression:\n  enabled: false\n  extra: []\n",
        "flow_leaf": "compression:\n  enabled: false\n  extra: [a, b]  # two\n",
        "no_enabled": "compression:\n  enabled: false\n",
        "missing": "",
        "nested_same_name": "",          # TAIL carries auxiliary.compression
        "quoted_key": "\"compression\":\n  enabled: false\n  threshold: 0.7\n",
        "single_quoted_key": "'compression':\n  enabled: false\n",
        "flow_map": "compression: {enabled: false, threshold: 0.7}\n",
        "flow_map_comment": "compression: {enabled: false}  # x\n",
        "scalar_header": "compression: off\n",
        "duplicate": ("compression:\n  enabled: false\n"
                      "compression:\n  threshold: 0.7\n"),
        "duplicate_child": ("compression:\n  threshold: 0.7\n  enabled: false\n"
                            "  threshold: 0.8\n"),
        "tab_indent": "compression:\n\tenabled: false\n\tthreshold: 0.7\n",
        "anchor": "compression: \n  enabled: false\n  threshold: &t 0.7\n",
        "alias": "compression:\n  enabled: false\n  target_ratio: *t\n",
        "merge": "compression:\n  <<: *base\n  enabled: false\n",
        "multi_doc": "compression:\n  enabled: false\n--- \nother: 1\n",
        "scalar_enabled": "compression:\n  enabled: false\n  threshold: [0.7]\n",
        "multiline_flow": "compression:\n  enabled: false\n  extra: [a,\n    b]\n",
    },
}

REFUSE = {"quoted_key", "single_quoted_key", "flow_map", "flow_map_comment",
          "scalar_header", "duplicate", "duplicate_child", "tab_indent",
          "anchor", "alias", "merge", "multi_doc", "multiline_flow"}
# a scalar where a list belongs is refused by the two list editors; the
# compression editor replaces a scalar it owns (threshold) only when it is a
# scalar — `threshold: [0.7]` is not, so it is refused as well
REFUSE_TOO = {"scalar_enabled"}


def run_edit(section):
    """Run the editor's real WRITE path on CFG. Returns (expected_change)."""
    if section == "plugins":
        return PE.enable(CFG, "approval-gate")
    if section == "platform_toolsets":
        return PB["_pb_write_toolsets"]("cli", ["web", "terminal"])
    return CX["_cx_write"]({"threshold": 0.6})


def expected_section(section, before_sec, present=True):
    """The section as the edit should leave it, given the original (and
    whether the original had the section's header at all)."""
    sec = dict(before_sec) if isinstance(before_sec, dict) else {}
    if section == "plugins":
        old = sec.get("enabled")
        old = list(old) if isinstance(old, list) else []
        sec["enabled"] = old if "approval-gate" in old else old + ["approval-gate"]
    elif section == "platform_toolsets":
        sec["cli"] = ["web", "terminal"]
    else:
        if not present:
            sec = {"threshold": 0.6, "target_ratio": 0.2,
                   "protect_last_n": 20, "protect_first_n": 3}
        sec["threshold"] = 0.6
    return sec


def backups():
    return sorted(f for f in os.listdir(HERMES_DIR) if ".bak-" in f or ".tmp-" in f)


def write(text, newline="\n"):
    data = text.replace("\n", newline)
    with open(CFG, "w", encoding="utf-8", newline="") as fh:
        fh.write(data)
    os.chmod(CFG, 0o640)
    return data


def read():
    with open(CFG, encoding="utf-8", newline="") as fh:
        return fh.read()


def one_case(section, name, body, newline="\n"):
    tag = "%s/%s%s" % (section, name, " (CRLF)" if newline != "\n" else "")
    src = write(HEAD + body + TAIL, newline)
    bak0 = backups()
    err = None
    try:
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            run_edit(section)
    except Exception as e:                  # noqa: BLE001 — any refusal counts
        err = e
    out = read()
    refuse = name in REFUSE or name in REFUSE_TOO
    if refuse:
        check("%s: refused" % tag, err is not None, out[:300])
        check("%s: nothing written" % tag, out == src and backups() == bak0
              and (os.stat(CFG).st_mode & 0o777) == 0o640)
        if err is not None:
            msg = str(err)
            check("%s: the error names a line and says why" % tag,
                  re.search(r"config\.yaml line \d+ ", msg) is not None
                  and "cannot edit safely" in msg
                  and "hermes config set" in msg, msg)
        return
    check("%s: applied without error" % tag, err is None, err)
    tree0, prob0 = parse(src)
    tree1, prob1 = parse(out)
    check("%s: reads back with no new problem" % tag,
          len(prob1) <= len(prob0), prob1)
    check("%s: the section is there exactly once" % tag,
          top_keys(out).count(section) == 1, top_keys(out))
    want = expected_section(section, tree0.get(section), section in tree0)
    check("%s: the section is exactly what was intended" % tag,
          canon(tree1.get(section)) == canon(want), (tree1.get(section), want))
    others0 = {k: v for k, v in tree0.items() if k != section}
    others1 = {k: v for k, v in tree1.items() if k != section}
    check("%s: every other top-level value unchanged" % tag,
          canon(others0) == canon(others1))
    s0, s1 = segments(src), segments(out)
    same = all(s1.get(k) == v for k, v in s0.items() if k != section)
    check("%s: every other top-level block byte-identical" % tag, same,
          [(k, s0[k], s1.get(k)) for k in s0 if k != section and s1.get(k) != s0[k]])
    check("%s: mode kept" % tag, (os.stat(CFG).st_mode & 0o777) == 0o640)
    if newline == "\r\n":
        check("%s: every line ending stays CRLF" % tag,
              "\n" not in out.replace("\r\n", ""), out[-120:])
    ok, val = pyyaml_ok(out)
    check("%s: PyYAML (when present) loads it" % tag, ok, val)
    if _yaml is not None and ok:
        check("%s: PyYAML (when present) sees the intended section" % tag,
              canon((val or {}).get(section)) == canon(want),
              ((val or {}).get(section), want))
    # a second run is a genuine no-op
    if section == "plugins" or section == "platform_toolsets" or section == "compression":
        before2 = read()
        bak2 = backups()
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            run_edit(section)
        check("%s: a repeat writes nothing" % tag,
              read() == before2 and backups() == bak2)


def clean():
    for f in os.listdir(HERMES_DIR):
        if ".bak-" in f:
            os.remove(os.path.join(HERMES_DIR, f))


print("\n=== the corpus, through each editor's write path ===")
for section, cases in SECTIONS.items():
    for name, body in cases.items():
        one_case(section, name, body)
        clean()
    for name in ("indented", "indentless", "header_comment"):
        one_case(section, name, cases[name], newline="\r\n")
        clean()


print("\n=== style is preserved ===")
write(HEAD + SECTIONS["plugins"]["indentless"] + TAIL)
with contextlib.redirect_stderr(io.StringIO()):
    PE.enable(CFG, "approval-gate")
check("an indentless enabled list stays indentless",
      "  - tool-budget\n  - approval-gate\n  disabled: []" in read(), read())
write(HEAD + SECTIONS["plugins"]["header_comment"] + TAIL)
with contextlib.redirect_stderr(io.StringIO()):
    PE.enable(CFG, "approval-gate")
check("the header comment survives a plugins edit",
      "plugins:  # mine\n" in read() and read().count("plugins:") == 1, read())
write(HEAD + SECTIONS["platform_toolsets"]["indentless"] + TAIL)
with contextlib.redirect_stdout(io.StringIO()):
    PB["_pb_write_toolsets"]("cli", ["web", "terminal"])
check("an indentless toolset list stays indentless",
      "  cli:\n  - web\n  - terminal\n  telegram:\n  - hermes-telegram\n" in read(),
      read())
write(HEAD + SECTIONS["platform_toolsets"]["key_comment"] + TAIL)
with contextlib.redirect_stdout(io.StringIO()):
    PB["_pb_write_toolsets"]("cli", ["web"])
check("a comment on the platform key line survives",
      "  cli:   # mine\n  - web\n" in read(), read())
write(HEAD + SECTIONS["compression"]["header_comment"] + TAIL)
CX["_cx_write"]({"threshold": 0.6})
check("the compression header comment survives and enabled stays false",
      "compression:   # tuned by hand\n  enabled: false\n  threshold: 0.6\n" in read(),
      read())
check("the compression card reads what Hermes will read",
      CX["_cx_read_cfg"]().get("enabled") is False
      and CX["_cx_read_cfg"]().get("threshold") == 0.6)
clean()

print("\n=== readers ===")
ind = HEAD + SECTIONS["plugins"]["indentless"] + TAIL
check("read_enabled reads an indentless list",
      PE.read_enabled(ind) == ["loop-breaker", "tool-budget"], PE.read_enabled(ind))
hc = HEAD + SECTIONS["plugins"]["header_comment"] + TAIL
check("read_enabled reads under a commented header",
      PE.read_enabled(hc) == ["loop-breaker"], PE.read_enabled(hc))
pt = PB["_pb_parse_toolsets"](HEAD + SECTIONS["platform_toolsets"]["indentless"] + TAIL)
check("_pb_parse_toolsets reads indentless lists",
      pt == {"cli": ["hermes-cli", "memory"], "telegram": ["hermes-telegram"]}, pt)
pt = PB["_pb_parse_toolsets"](HEAD + SECTIONS["platform_toolsets"]["header_comment"])
check("_pb_parse_toolsets reads under a commented header",
      pt == {"cli": ["hermes-cli"], "telegram": ["hermes-telegram"]}, pt)
pt = PB["_pb_parse_toolsets"](HEAD + SECTIONS["platform_toolsets"]["key_comment"])
check("_pb_parse_toolsets reads a platform key with a comment",
      pt == {"cli": ["hermes-cli"], "telegram": ["hermes-telegram"]}, pt)


print("\n=== plugin_enable.py exit status ===")
for name in ("quoted_key", "flow_map", "duplicate", "tab_indent"):
    write(HEAD + SECTIONS["plugins"][name] + TAIL)
    before = read()
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = PE.main(["approval-gate", "--config", CFG])
    check("main exits 2 on %s" % name, rc == 2, rc)
    check("main says why on stderr for %s" % name,
          "cannot edit safely" in err.getvalue(), err.getvalue())
    check("main wrote nothing for %s" % name, read() == before and not backups())
write(HEAD + SECTIONS["plugins"]["indentless"] + TAIL)
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    rc = PE.main(["tool-budget", "--config", CFG, "--check"])
check("--check sees an indentless entry as enabled", rc == 0, rc)
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    rc = PE.main(["tool-budget", "--config", CFG])
check("an already-present indentless entry is a no-op (exit 0, no backup)",
      rc == 0 and not backups(), (rc, backups()))
clean()


print("\n=== the verifier refuses a bad edit even if an editor produces one ===")
src = HEAD + SECTIONS["plugins"]["indented"] + TAIL
bad = src.replace("    - tool-budget\n", "")            # drops an item
try:
    PE.verify_edit(src, bad, "plugins", {"enabled": ["loop-breaker", "approval-gate"]},
                   keep={"enabled": ["loop-breaker", "tool-budget"]})
    check("verify_edit refuses a dropped item", False)
except PE.ConfigEditError as e:
    check("verify_edit refuses a dropped item", "nothing was written" in str(e), str(e))
bad2 = src.replace("skin: default", "skin: dark")
try:
    PE.verify_edit(src, bad2, "plugins", {})
    check("verify_edit refuses a change outside the section", False)
except PE.ConfigEditError:
    check("verify_edit refuses a change outside the section", True)
bad3 = src + "plugins:\n  enabled: []\n"
try:
    PE.verify_edit(src, bad3, "plugins", {"enabled": []})
    check("verify_edit refuses a second top-level block", False)
except PE.ConfigEditError:
    check("verify_edit refuses a second top-level block", True)
# a broken editor is caught by the write path, nothing written
write(src)
real = PE.apply_enable
PE.apply_enable = lambda s, n: s.replace("  enabled:\n    - loop-breaker\n",
                                         "  enabled:\n    - %s\n  - loop-breaker\n" % n)
try:
    with contextlib.redirect_stderr(io.StringIO()):
        PE.enable(CFG, "approval-gate")
    check("enable() refuses what the editor broke", False)
except PE.ConfigEditError:
    check("enable() refuses what the editor broke", read() == src and not backups())
finally:
    PE.apply_enable = real


print("\n=== the error reaches the dashboard ===")
write(HEAD + SECTIONS["compression"]["quoted_key"] + TAIL)
Ctx = type("Ctx", (), {})
c = Ctx()
c.body = {"threshold": 0.6}
r = CX["_cx_compression_post"](c)
body, code = (r if isinstance(r, tuple) else (r, 200))
check("compression POST answers an error, not ok",
      body.get("ok") is False and code >= 400
      and "cannot edit safely" in str(body.get("error")), r)
check("compression GET says the section cannot be edited",
      "cannot edit safely" in str(CX["_cx_compression_payload"]().get("config_error")),
      CX["_cx_compression_payload"]().get("config_error"))
write(HEAD + SECTIONS["platform_toolsets"]["flow_map"] + TAIL)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        PB["set_prompt_budget_profile"]("lean")
    check("set_prompt_budget_profile refuses a flow-style section", False)
except Exception as e:                       # noqa: BLE001
    check("set_prompt_budget_profile refuses a flow-style section",
          "cannot edit safely" in str(e), str(e))
check("prompt budget wrote nothing", not backups())

TB = {"__name__": "aux_toolbudget", "HOME": SB, "HERE": os.path.join(REPO, "dashboard"),
      "DATA": os.path.join(HERMES_DIR, "dashboard"), "_state_lock": STATE_LOCK,
      "register_get": lambda p, f: None, "register_post": lambda p, f: None}
exec(compile(open(TB_PATH, encoding="utf-8").read(), TB_PATH, "exec"), TB)
write(HEAD + SECTIONS["plugins"]["flow_map"] + TAIL)
before = read()
try:
    TB["_tb_enable_in_config"]()
    check("Tool budget install refuses a flow-style plugins section", False)
except RuntimeError as e:
    check("Tool budget install refuses a flow-style plugins section",
          "cannot edit safely" in str(e), str(e))
check("Tool budget wrote nothing", read() == before and not backups())


print("\n=== a file that ends without a newline ===")
for section, body in (("plugins", "plugins:"), ("plugins", "plugins:\n  enabled:\n  - a"),
                      ("platform_toolsets", "platform_toolsets:"),
                      ("platform_toolsets", "platform_toolsets:\n  cli:\n  - a"),
                      ("compression", "compression:"),
                      ("compression", "compression:\n  threshold: 0.7")):
    src = write(HEAD + body)
    try:
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            run_edit(section)
        out = read()
        t1, p1 = parse(out)
        t0 = parse(src)[0]
        want = expected_section(section, t0.get(section), section in t0)
        check("%r at EOF: applied and exact" % body,
              not p1 and canon(t1.get(section)) == canon(want), (out, want))
    except Exception as e:                   # noqa: BLE001
        check("%r at EOF: applied and exact" % body, False, str(e))
    clean()


print("\n=== no helper, no write (fail closed) ===")
write(HEAD + SECTIONS["compression"]["indented"] + TAIL)
before = read()
saved_cx, saved_pb = dict(CX["_CX_YH"]), dict(PB["_PB_YH"])
CX["_CX_YH"].clear()
PB["_PB_YH"].clear()
real_here = CX.get("HERE")
CX["HERE"] = PB["HERE"] = os.path.join(SB, "nowhere", "dashboard")
for label, fn in (("compression", lambda: CX["_cx_write"]({"threshold": 0.6})),
                  ("prompt budget", lambda: PB["_pb_write_toolsets"]("cli", ["web"]))):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        check("%s refuses without plugin_enable.py" % label, False)
    except RuntimeError as e:
        check("%s refuses without plugin_enable.py" % label,
              "could not be loaded" in str(e) and read() == before and not backups(),
              str(e))
del CX["HERE"], PB["HERE"]
CX["_CX_YH"].update(saved_cx)
PB["_PB_YH"].update(saved_pb)


print("\n=== review R4: forms the subset reader and PyYAML read differently ===")
# Each of these loads in PyYAML (checked with PyYAML 6.0.3 in review) to
# something the line editors do not see: a quoted value whose later lines sit
# at column 0 (its content `plugins:` looked like the section header, so the
# edit landed INSIDE the string and success was reported), and a top-level
# `<<` merge whose merged section an appended block silently replaced.
QUOTED = {          # PyYAML: one key, note = "hand note plugins: enabled: - x zz: end"
    "plugins": 'note: "hand note\nplugins:\n  enabled:\n  - x\nzz: end"\n',
    "platform_toolsets": "note: 'hand note\nplatform_toolsets:\n  cli:\n  - x\nzz: end'\n",
    "compression": 'note: "hand note\ncompression:\n  threshold: 0.7\nzz: end"\n',
}
MERGE = ("base: &b\n  plugins:\n    enabled: [loop-breaker]\n"
         "  platform_toolsets:\n    telegram: [x]\n"
         "  compression:\n    enabled: false\n<<: *b\n")
for section in SECTIONS:
    for label, body in (("a quoted value running on at column 0", QUOTED[section]),
                        ("a top-level merge key", MERGE)):
        src = write(HEAD + body + TAIL)
        err = None
        try:
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                run_edit(section)
        except Exception as e:              # noqa: BLE001
            err = e
        check("%s: %s is refused, nothing written" % (section, label),
              err is not None and "nothing was written" in str(err)
              and read() == src and not backups(), (err, read()[:200]))
        clean()

# a UTF-8 byte-order mark before the section header: the line editors did not
# see the header, so a removal reported "unchanged" while the platform stayed
write("﻿platform_toolsets:\n  telegram:\n  - hermes-telegram\n  cli:\n"
      "  - hermes-cli\n" + TAIL)
with contextlib.redirect_stdout(io.StringIO()):
    PB["_pb_write_toolsets"]("telegram", None)
t1 = parse(read())[0]
check("BOM: a platform removal really removes it",
      read().startswith("﻿platform_toolsets:\n")
      and canon(t1.get("platform_toolsets")) == canon({"cli": ["hermes-cli"]}),
      read()[:200])
clean()
write("﻿plugins:\n  enabled:\n  - loop-breaker\n" + TAIL)
with contextlib.redirect_stderr(io.StringIO()):
    PE.enable(CFG, "approval-gate")
check("BOM: plugins.enabled is edited in place, BOM kept",
      read().startswith("﻿plugins:\n  enabled:\n  - loop-breaker\n"
                        "  - approval-gate\n")
      and top_keys(read().lstrip("\ufeff")).count("plugins") == 1,
      read()[:200])
clean()
write("﻿compression:\n  enabled: false\n  threshold: 0.7\n" + TAIL)
CX["_cx_write"]({"threshold": 0.6})
check("BOM: compression is edited in place, BOM kept",
      read().startswith("﻿compression:\n  enabled: false\n  threshold: 0.6\n"),
      read()[:200])
clean()


print("\n=== drift guard: plugin_enable's reader is doctor's, byte for byte ===")


def reader_src(path):
    text = open(path, encoding="utf-8").read()
    a = text.index("_HC_KEY_RE = re.compile(")
    b = text.index("def _hcfg_parse(text):")
    b = text.index("\n\n\n", b)
    return text[a:b]


check("plugin_enable.py carries doctor.py's YAML-subset reader unchanged",
      reader_src(PE_PATH) == reader_src(DOC_PATH))

shutil.rmtree(SB, ignore_errors=True)
print("TESTS %d passed %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
