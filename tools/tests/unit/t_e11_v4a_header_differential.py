#!/usr/bin/env python3
"""approval-gate V4A patch headers (audit 2026-11-09 E11).

A lone carriage return in a header (`*** Add File: \\r<path>`, `*** Add\\rFile:`,
`*** Move File: \\r<src> -> <dst>`) used to hide the target: the gate split the
patch on `\\r` as well as `\\n`, while Hermes' tools/patch_parser.py splits on
`\\n` only and lets `\\s` swallow the `\\r`. This suite drives the REAL plugin
(Hermes stubbed at its boundary, scratch HOME under /tmp) and checks:

  * the reported CR cases are gated, and a plain CRLF patch is not;
  * a header carrying a control or hidden character (tab, VT, FF, NEL, a
    non-trailing CR, BOM, zero-width, bidi...) or a header-shaped line the
    gate cannot read makes the whole patch ASK;
  * when tools.patch_parser is importable the gate also uses it;
  * a DIFFERENTIAL FUZZ: a few hundred generated headers (CR, CRLF, tabs,
    NBSP and other unicode spaces, case variants, no space after the colon,
    quoted paths, ./, ../, ~, $HOME, backslashes, BOM, duplicates, `*** Move
    to:`) — every path upstream's parser would write must be one of the gate's
    targets, and a patch any of whose paths falls in a protected class must be
    gated. The oracle is a faithful copy of upstream's header handling
    (Hermes Agent 0.18, tools/patch_parser.py parse_v4a_patch, commit
    19d4174454), vendored below; when the real file exists on this machine it
    is compared too (silently nothing when it does not).
"""
import importlib.util
import logging
import os
import random
import re
import sys
import tempfile
import threading
import types
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PLUGIN = os.path.join(REPO, "hermes-plugins", "approval-gate", "__init__.py")
# resolved BEFORE HOME points at the scratch folder; read, never written
REAL_PARSER = os.path.join(
    os.environ.get("HERMES_AGENT_DIR")
    or os.path.join(os.path.expanduser("~"), ".hermes", "hermes-agent"),
    "tools", "patch_parser.py")

_TMP = "/tmp" if os.path.isdir("/tmp") else None
SANDBOX = tempfile.mkdtemp(prefix="e11-home-", dir=_TMP)
os.environ["HOME"] = SANDBOX
os.environ["HERMES_HOME"] = os.path.join(SANDBOX, ".hermes")
os.makedirs(os.environ["HERMES_HOME"], mode=0o700)
for _k in ("HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK", "HERMES_CRON_SESSION",
           "HERMES_SESSION_PLATFORM", "HERMES_INTERACTIVE", "HERMES_YOLO_MODE"):
    os.environ.pop(_k, None)

PASSES, FAILS = [0], []


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  PASS  " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + (("  — " + str(extra)[:900]) if extra else ""))


def _pkg(name):
    m = types.ModuleType(name)
    m.__path__ = []
    sys.modules[name] = m
    return m


class FakeApproval(types.ModuleType):
    def __init__(self):
        super().__init__("tools.approval")
        self._lock = threading.Lock()
        self._gateway_notify_cbs = {}

    def get_current_session_key(self, default="default"):
        return "sess-1"

    def is_approved(self, session_key, key):
        return False

    def approve_session(self, session_key, key):
        pass

    def _is_gateway_approval_context(self):
        return False

    def _await_gateway_decision(self, *a, **k):
        raise AssertionError("no surface in this suite")


def _resolve(p, task_id="default"):
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(SANDBOX, p)           # the task's cwd is ~
    return os.path.realpath(p)


A = FakeApproval()
CU = types.ModuleType("tools.computer_use.tool")
CU._approval_callback = None
TT = types.ModuleType("tools.terminal_tool")
TT._get_approval_callback = lambda: None
FT = types.ModuleType("tools.file_tools")
FT._resolve_path_for_task = _resolve
FT._check_sensitive_path = lambda p, task_id="default": None
FS = types.ModuleType("agent.file_safety")
FS.is_write_denied = lambda p: False
RD = types.ModuleType("agent.redact")
RD.redact_sensitive_text = lambda t: t
_pkg("tools")
_pkg("tools.computer_use")
_pkg("agent")
sys.modules.update({"tools.approval": A, "tools.computer_use.tool": CU,
                    "tools.terminal_tool": TT, "tools.file_tools": FT,
                    "agent.file_safety": FS, "agent.redact": RD})
sys.modules["tools"].approval = A
sys.modules["tools.computer_use"].tool = CU

_log = logging.getLogger("hermes_plugins.approval_gate")
_log.addHandler(logging.NullHandler())
_log.propagate = False
spec = importlib.util.spec_from_file_location("approval_gate_e11", PLUGIN)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def gate(tool, args):
    return G._pre_tool_call(tool_name=tool, args=args, task_id="t1",
                            session_id="s1", turn_id="u1")


def blocked(r):
    return isinstance(r, dict) and r.get("action") == "block" and \
        "[approval-gate]" in (r.get("message") or "")


def home(rel):
    return os.path.join(SANDBOX, rel)


RHOME = os.path.realpath(SANDBOX)

# ---------------------------------------------------------------------------
# The oracle: upstream's header handling, copied (not imported) from Hermes
# Agent 0.18 tools/patch_parser.py parse_v4a_patch — the line split, the
# Begin/End window, the four regexes in their elif order, and .strip().
# Every header path it returns is one Hermes may write.
# ---------------------------------------------------------------------------
_UP_UPDATE = re.compile(r'\*\*\*\s*Update\s+File:\s*(.+)')
_UP_ADD = re.compile(r'\*\*\*\s*Add\s+File:\s*(.+)')
_UP_DELETE = re.compile(r'\*\*\*\s*Delete\s+File:\s*(.+)')
_UP_MOVE = re.compile(r'\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)')


def oracle(patch_content):
    lines = patch_content.split('\n')
    start_idx = end_idx = None
    for i, line in enumerate(lines):
        if '*** Begin Patch' in line or '***Begin Patch' in line:
            start_idx = i
        elif '*** End Patch' in line or '***End Patch' in line:
            end_idx = i
            break
    if start_idx is None:
        start_idx = -1
    if end_idx is None:
        end_idx = len(lines)
    out = []
    for line in lines[start_idx + 1:end_idx]:
        u, a, d = _UP_UPDATE.match(line), _UP_ADD.match(line), _UP_DELETE.match(line)
        m = _UP_MOVE.match(line)
        if u:
            out.append(u.group(1).strip())
        elif a:
            out.append(a.group(1).strip())
        elif d:
            out.append(d.group(1).strip())
        elif m:
            out.extend([m.group(1).strip(), m.group(2).strip()])
    return [p for p in out if p]


def _load_real_parser():
    """Hermes' own parser, when this machine has the checkout. exec'd from
    source (no bytecode is written next to it); None when absent."""
    if not os.path.isfile(REAL_PARSER):
        return None
    try:
        with open(REAL_PARSER, encoding="utf-8") as fh:
            src = fh.read()
        mod = types.ModuleType("hermes_real_patch_parser_e11")
        mod.__file__ = REAL_PARSER
        sys.modules[mod.__name__] = mod          # dataclasses look it up
        exec(compile(src, REAL_PARSER, "exec"), mod.__dict__)
        return mod.parse_v4a_patch
    except Exception as e:                       # present but unloadable: say so
        return e


def real_paths(parse, patch):
    ops, _err = parse(patch)
    out = []
    for op in ops:
        out.append(op.file_path)
        if getattr(op, "new_path", None):
            out.append(op.new_path)
    return out


# ---------------------------------------------------------------------------
def reported_cases():
    print("E11 — the reported carriage-return headers")
    zshrc, login, prof = home(".zshrc"), home(".bash_login"), home(".profile")
    cases = [
        ("Add File: \\r<abs ~/.zshrc>", "*** Begin Patch\n*** Add File: \r%s\n+x\n*** End Patch" % zshrc),
        ("Add\\rFile: <~/.bash_login>", "*** Begin Patch\n*** Add\rFile: %s\n+x\n*** End Patch" % login),
        ("Move File: \\r<src> -> <~/.profile>",
         "*** Begin Patch\n*** Move File: \r/tmp/src -> %s\n*** End Patch" % prof),
        ("Move File: src ->\\r<~/.profile>",
         "*** Begin Patch\n*** Move File: /tmp/src ->\r%s\n*** End Patch" % prof),
        ("Update File:\\r~/.zshrc", "*** Begin Patch\n*** Update File:\r~/.zshrc\n@@\n-a\n+b\n*** End Patch"),
        ("***\\rAdd File: ~/.zshrc", "*** Begin Patch\n***\rAdd File: ~/.zshrc\n+x\n*** End Patch"),
    ]
    for name, patch in cases:
        r = gate("patch", {"mode": "patch", "patch": patch})
        check("E11: `%s` is gated (oracle writes %s)" % (name, oracle(patch)), blocked(r), r)
        t = G.write_targets("patch", {"mode": "patch", "patch": patch})
        check("E11: ... and every path the oracle writes is a gate target",
              set(oracle(patch)) <= set(t), (oracle(patch), t))
    crlf = "*** Begin Patch\r\n*** Add File: notes/x.md\r\n+hello\r\n*** End Patch\r\n"
    check("E11 control: a plain CRLF patch to an ordinary file is not asked",
          gate("patch", {"mode": "patch", "patch": crlf}) is None,
          gate("patch", {"mode": "patch", "patch": crlf}))
    crlf_rc = "*** Begin Patch\r\n*** Add File: ~/.zshrc\r\n+x\r\n*** End Patch\r\n"
    check("E11 control: a CRLF patch to ~/.zshrc is gated (trailing CR stripped)",
          blocked(gate("patch", {"mode": "patch", "patch": crlf_rc})))
    plain = "*** Begin Patch\n*** Update File: notes/x.md\n@@\n-a\n+b\n*** End Patch\n"
    check("E11 control: an ordinary patch is untouched",
          gate("patch", {"mode": "patch", "patch": plain}) is None)
    check("E11 control: a hunk ADDING a line that reads like a header is not a header",
          gate("patch", {"mode": "patch", "patch":
                         "*** Update File: notes/v4a.md\n@@\n-a\n+*** Add File: ~/.zshrc\n"}) is None)


def ask_cases():
    print("E11 — headers the gate cannot read answer ASK")
    bad = [
        ("tab inside a header", "*** Add File:\t\tnotes/x.md"),
        ("VT in a header", "*** Add File: notes/\x0bx.md"),
        ("NUL in a header", "*** Add File: notes/x\x00.md"),
        ("CR in the middle of the path", "*** Add File: notes/a\rb.md"),
        ("two trailing CRs", "*** Add File: notes/x.md\r\r"),
        ("BOM before the stars", "﻿*** Add File: notes/x.md"),
        ("zero-width space in the verb", "*** Ad​d File: notes/x.md"),
        ("RLO in the path", "*** Add File: notes/‮x.md"),
        ("NEL as the separator", "*** Add\x85File: notes/x.md"),
        ("header with no colon", "*** Add File notes/x.md"),
        ("header with no space before File", "*** AddFile: notes/x.md"),
        ("Move without an arrow", "*** Move File: notes/a.md"),
        ("header with an empty path", "*** Add File:   "),
    ]
    for name, hdr in bad:
        patch = "*** Begin Patch\n%s\n+x\n*** End Patch\n" % hdr
        r = gate("patch", {"mode": "patch", "patch": patch})
        check("E11: %s -> ASK (no surface: blocked)" % name, blocked(r), r)
    serve = "*** Begin Patch\n*** Add File:\tnotes/x.md\n+x\n*** End Patch\n"
    os.environ["HERMES_EXEC_ASK"] = "1"         # a gateway/serve session
    asked = []
    A._gateway_notify_cbs["sess-1"] = lambda d: None
    A._await_gateway_decision = lambda sk, n, data, surface="gateway": (
        asked.append(data) or {"resolved": True, "choice": "deny"})
    try:
        gate("patch", {"mode": "patch", "patch": serve})
    finally:
        os.environ.pop("HERMES_EXEC_ASK", None)
        A._gateway_notify_cbs.clear()
    key = asked[0].get("pattern_key") if asked else None
    check("E11: the unreadable header asks with its own stable key, and the "
          "card marks the tab", asked and key == G._PATH_CLASSES["v4a-unread"][0]
          and "patch" in key and "\\t" in asked[0].get("command", ""), asked)
    sys.path.insert(0, os.path.join(REPO, "dashboard"))
    import permissions as PM  # noqa: E402  (HOME is the scratch folder)
    d = PM.decide({"pattern_key": key, "pattern_keys": [key]}) if key else {}
    check("E11: the Trust panel never auto-approves that key", d.get("tier") == "ask", d)


def upstream_wiring():
    print("E11 — the gate also asks Hermes' own parser when it is importable")

    class Op:
        def __init__(self, f, n=None):
            self.file_path, self.new_path = f, n
    pp = types.ModuleType("tools.patch_parser")
    pp.parse_v4a_patch = lambda text: ([Op("notes/a.md", "~/.zshrc")], None)
    sys.modules["tools.patch_parser"] = pp
    try:
        r = gate("patch", {"mode": "patch", "patch": "nothing the regexes see"})
        check("E11: a target only upstream's parser reports is gated", blocked(r), r)

        def boom(text):
            raise ValueError("parser exploded")
        pp.parse_v4a_patch = boom
        check("E11: a parser that raises leaves the faithful fallback in charge",
              gate("patch", {"mode": "patch", "patch": "*** Add File: notes/a.md\n+x\n"}) is None
              and blocked(gate("patch", {"mode": "patch",
                                         "patch": "*** Add File: ~/.zshrc\n+x\n"})))
    finally:
        sys.modules.pop("tools.patch_parser", None)


# ---------------------------------------------------------------------------
# the differential fuzz
# ---------------------------------------------------------------------------
PATHS = ["~/.zshrc", RHOME + "/.bashrc", ".zprofile", "./.bash_profile",
         "notes/../.profile", "$HOME/.zshrc", '"~/.zshrc"', "'%s/.zshrc'" % RHOME,
         "~\\.zshrc", "notes\\x.md", "~/Library/LaunchAgents/a.plist",
         "~/.ssh/config", "notes/ok.md", "/tmp/ordinary.txt", "~/.hermes/permissions.json",
         "notes/a b.md", "notes/été.md", ".hermes/plugins/x.py"]
LEADS = ["", "", "", " ", "\t", "﻿", "\r", " "]
STARS = ["***", "*** ", "***  ", "***\t", "***\r", "*** ", "*** ", "***　",
         "*** ​"]
VERBS = ["Add", "Update", "Delete", "Move", "add", "ADD", "uPdate", "DELETE", "move"]
GAPS = [" ", " ", "  ", "\t", "\r", " ", " ", "　", "\x0b", "\x0c",
        "\x1c", "\x85", " ", "", "​"]
FILES = ["File", "File", "file", "FILE"]
AFTER = [" ", " ", "", "  ", "\t", "\r", " ", " ", " \r"]
TAILS = ["", "", "", " ", "\r", "\t", " ", "\r\r", " 　"]
ARROWS = [" -> ", "->", "  ->\t", " ->\r", "\r-> ", " - > ", " => "]


def _header(rng):
    verb = rng.choice(VERBS)
    head = rng.choice(LEADS) + rng.choice(STARS) + verb + rng.choice(GAPS) + \
        rng.choice(FILES) + ":" + rng.choice(AFTER)
    if verb.lower() == "move":
        body = rng.choice(PATHS) + rng.choice(ARROWS) + rng.choice(PATHS)
    else:
        body = rng.choice(PATHS)
    return head + body + rng.choice(TAILS)


def _patch(rng):
    hdrs = [_header(rng) for _ in range(rng.choice((1, 1, 2, 3)))]
    if rng.random() < 0.15:
        hdrs.append(hdrs[0])                          # a duplicate header
    if rng.random() < 0.15:
        hdrs.append("*** Move to: " + rng.choice(PATHS))
    parts = []
    for h in hdrs:
        parts.append(h)
        parts.extend(["@@ ctx @@", "-old", "+new"])
    nl = "\r\n" if rng.random() < 0.2 else "\n"
    begin = rng.choice(["*** Begin Patch", "***Begin Patch", "*** Begin Patch\r", ""])
    end = rng.choice(["*** End Patch", "***End Patch", ""])
    lines = ([begin] if begin else []) + parts + ([end] if end else [])
    text = nl.join(lines)
    if rng.random() < 0.1:
        text = "﻿" + text
    if rng.random() < 0.1:                            # a header outside the window
        text = text + "\n*** Add File: ~/.zlogin\n+x"
    return text


def _hidden_header(patch):
    """True when a header-shaped line carries a control/format character
    other than the one trailing CR of a CRLF line ending."""
    hidden = ("Cc", "Cf", "Zl", "Zp")
    for ln in patch.split("\n"):
        body = ln[:-1] if ln.endswith("\r") else ln
        bare = "".join(c for c in body if unicodedata.category(c) not in hidden)
        if not re.search(r"\*\*\*\s*(?:update|add|delete|move)\s*(?:file|to)", bare, re.I):
            continue
        if bare != body:
            return True
    return False


def fuzz():
    print("E11 — differential fuzz of header extraction against upstream's parser")
    rng = random.Random(20261109)
    patches = [_patch(rng) for _ in range(400)]
    # plus every single-header shape, systematically, for the CR family
    for lead in ("", "\r"):
        for gap in ("\r", " \r", "\r ", "\r\r"):
            for after in ("\r", " \r", ""):
                for verb in ("Add", "Update", "Delete"):
                    patches.append("*** Begin Patch\n%s*** %s%sFile:%s~/.zshrc\n+x\n*** End Patch"
                                   % (lead, verb, gap, after))
                patches.append("*** Begin Patch\n%s*** Move%sFile:%s/tmp/a ->%s~/.zshrc\n*** End Patch"
                               % (lead, gap, after, after))
    miss, leak, unread_ok, n_paths, n_protected = [], [], [], 0, 0
    real = _load_real_parser()
    real_miss = []
    for patch in patches:
        args = {"mode": "patch", "patch": patch}
        want = oracle(patch)
        n_paths += len(want)
        got = set(G.write_targets("patch", args))
        lost = [p for p in want if p not in got]
        if lost:
            miss.append((patch, lost, sorted(got)))
        protected = [p for p in want if G.classify_path(p, "t1")]
        r = gate("patch", args)
        if protected:
            n_protected += 1
            if not blocked(r):
                leak.append((patch, protected, r))
        if _hidden_header(patch) and not blocked(r):
            unread_ok.append((patch, r))
        if callable(real):
            rp = real_paths(real, patch)
            if [p for p in rp if p not in got] or [p for p in rp if p not in want]:
                real_miss.append((patch, rp, want, sorted(got)))
    check("E11 fuzz: %d patches, %d header paths — every path the oracle writes is "
          "a gate target" % (len(patches), n_paths), not miss, miss[:3])
    check("E11 fuzz: every one of the %d patches that writes a protected path is "
          "gated" % n_protected, not leak and n_protected > 50, leak[:3])
    check("E11 fuzz: every patch with a control/format character in a header asks",
          not unread_ok, unread_ok[:3])
    if real is not None:                       # only on a machine with Hermes
        check("E11 fuzz: Hermes' real tools/patch_parser.py loads", callable(real), real)
        if callable(real):
            check("E11 fuzz: the real parser agrees with the vendored oracle and "
                  "every path it writes is a gate target", not real_miss, real_miss[:3])


def main():
    reported_cases()
    ask_cases()
    upstream_wiring()
    fuzz()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
