#!/usr/bin/env python3
"""approval-gate path forms (audit 2026-11-09 E10, E12, E13, E68).

Drives the REAL hermes-plugins/approval-gate/__init__.py with the Hermes
modules it imports stubbed at their boundary (the t_c111 Part A pattern), with
a scratch HOME under /tmp. Nothing is written outside that HOME; nothing is
asked of a real approval surface.

  E10  macOS alias spellings of a path (/System/Volumes/Data/..., /.nofollow,
       /.resolve/N, /.vol/<dev>/<ino>, /.file) are classed by the file they
       name, or refused outright — for writes, the GitHub key-folder read block
       and searches alike. On Darwin the real kernel forms are exercised too.
  E12  a classified V4A patch target is never dropped because Hermes "would
       refuse it": Hermes checks the raw relative header, not the file.
  E13  relative paths are judged against Hermes' own task resolver when it
       answers (the Telegram gateway's process cwd is ~/.hermes); the process
       cwd is used only when the resolver is unavailable.
  E68  files the shell startup files commonly source are in the `rc` class.
"""
import importlib.util
import logging
import os
import stat
import sys
import tempfile
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PLUGIN = os.path.join(REPO, "hermes-plugins", "approval-gate", "__init__.py")

_TMP = "/tmp" if os.path.isdir("/tmp") else None
SANDBOX = tempfile.mkdtemp(prefix="e10-home-", dir=_TMP)
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
        print("  FAIL  " + name + (("  — " + str(extra)[:600]) if extra else ""))


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
        self.decision = {"resolved": True, "choice": "deny"}
        self.asked = []

    def get_current_session_key(self, default="default"):
        return "sess-1"

    def is_approved(self, session_key, key):
        return False

    def approve_session(self, session_key, key):
        pass

    def _is_gateway_approval_context(self):
        return os.environ.get("HERMES_GATEWAY_SESSION") == "1"

    def _await_gateway_decision(self, session_key, notify, data, surface="gateway"):
        self.asked.append(dict(data))
        return dict(self.decision)


# Hermes' task resolver: relative paths against TASK["cwd"] (the terminal's
# live cwd / TERMINAL_CWD), absolute ones Path.resolve()d; TASK["raise"]
# simulates a Hermes build without it.
TASK = {"cwd": None, "raise": False}


def _resolve(p, task_id="default"):
    if TASK["raise"]:
        raise RuntimeError("no resolver")
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(TASK["cwd"] or os.getcwd(), p)
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
# upstream: realpath against the PROCESS cwd, string compares
FS.is_write_denied = lambda p: os.path.realpath(os.path.expanduser(p)).startswith(
    os.path.realpath(os.path.join(SANDBOX, ".ssh")) + os.sep)
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
spec = importlib.util.spec_from_file_location("approval_gate_e10", PLUGIN)
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


DATA = "/System/Volumes/Data"
RHOME = os.path.realpath(SANDBOX)            # /private/tmp/... on macOS
H = os.environ["HERMES_HOME"]
CRED = os.path.join(RHOME, ".hermes", "dashboard", "github", "credential.json")
DARWIN = sys.platform == "darwin"


def e10():
    print("E10 — macOS alias spellings")
    for d in (".hermes/dashboard/github", "Library", ".claude", "Documents"):
        os.makedirs(home(d), exist_ok=True)
    for f in (".zshrc", ".hermes/dashboard/github/credential.json"):
        with open(home(f), "w") as fh:
            fh.write("x\n")
    st_home = os.stat(SANDBOX)
    vol_dir = "/.vol/%d/%d" % (st_home.st_dev, st_home.st_ino)

    writes = [
        ("firmlink ~/.zshrc", DATA + RHOME + "/.zshrc"),
        ("firmlink LaunchAgents (folder absent)", DATA + RHOME + "/Library/LaunchAgents/x.plist"),
        ("firmlink ~/.hermes/permissions.json", DATA + RHOME + "/.hermes/permissions.json"),
        ("firmlink ~/.claude/settings.json", DATA + RHOME + "/.claude/settings.json"),
        ("firmlink install dir", DATA + os.path.realpath(REPO) + "/dashboard/server.py"),
        ("firmlink, double leading slash", "/" + DATA + RHOME + "/.zshrc"),
        ("firmlink, case-folded", DATA.lower() + RHOME + "/.zshrc"),
        ("firmlink twice", DATA + DATA + RHOME + "/.zshrc"),
        ("/.nofollow ~/.zshrc", "/.nofollow" + RHOME + "/.zshrc"),
        ("/.resolve/1 ~/.zshrc", "/.resolve/1" + RHOME + "/.zshrc"),
        ("/.resolve/0 ~/.zshrc", "/.resolve/0" + RHOME + "/.zshrc"),
        ("/.vol/<dev>/<dir-ino>/.zshrc", vol_dir + "/.zshrc"),
        ("/.vol/<dev>/<dir-ino>/new file", vol_dir + "/notes.txt"),
        ("/.file/id=", "/.file/id=%d.%d" % (st_home.st_dev, st_home.st_ino)),
        ("//.vol", "//.vol/1/2/x"),
        ("/.VOL upper case", "/.VOL/1/2/x"),
        ("/./.nofollow", "/./.nofollow" + RHOME + "/.zshrc"),
        ("firmlink + /.nofollow", DATA + "/.nofollow" + RHOME + "/.zshrc"),
    ]
    for name, p in writes:
        r = gate("write_file", {"path": p, "content": "x"})
        check("E10: write_file %s is not allowed unasked" % name, blocked(r), r)
    # the same through the other writers
    check("E10: patch replace mode on a firmlink permissions.json is gated",
          blocked(gate("patch", {"path": DATA + RHOME + "/.hermes/permissions.json",
                                 "old_string": "a", "new_string": "b"})))
    check("E10: a V4A Add File on a firmlink ~/.bash_profile is gated",
          blocked(gate("patch", {"mode": "patch", "patch":
                                 "*** Begin Patch\n*** Add File: %s/.bash_profile\n+x\n"
                                 "*** End Patch\n" % (DATA + RHOME)})))
    check("E10: a V4A Move onto a /.nofollow ~/.profile is gated",
          blocked(gate("patch", {"mode": "patch", "patch":
                                 "*** Begin Patch\n*** Move File: /tmp/a -> /.nofollow%s/.profile\n"
                                 "*** End Patch\n" % RHOME})))
    check("E10: an MCP write to a firmlink ~/.zshrc is gated",
          blocked(gate("mcp_fs_write_file", {"path": DATA + RHOME + "/.zshrc",
                                             "content": "x"})))
    check("E10: an MCP write to a /.vol path is refused",
          blocked(gate("mcp_fs_write_file", {"path": vol_dir + "/x", "content": "x"})))
    check("E10: a media tool saving onto a firmlink LaunchAgent is gated",
          blocked(gate("text_to_speech", {"text": "hi", "output_path":
                                          DATA + RHOME + "/Library/LaunchAgents/a.plist"})))
    # a symlink whose target is spelled through the firmlink
    link = home("Documents/innocent.txt")
    os.symlink(DATA + RHOME + "/.zshrc", link)
    check("E10: a symlink pointing at the firmlink spelling of ~/.zshrc is classed rc",
          G.classify_path(link) == "rc", G.classify_path(link))
    vlink = home("Documents/vol-link")
    os.symlink("/.vol/1/2", vlink)
    check("E10: a symlink into /.vol is refused through its resolved form",
          blocked(gate("write_file", {"path": vlink + "/x", "content": "x"}))
          and blocked(gate("read_file", {"path": vlink + "/x"})))
    check("E10: classify_path on the firmlink spelling names the class",
          G.classify_path(DATA + RHOME + "/.zshrc") == "rc"
          and G.classify_path(DATA + RHOME + "/.hermes/permissions.json") == "hermes",
          (G.classify_path(DATA + RHOME + "/.zshrc"),
           G.classify_path(DATA + RHOME + "/.hermes/permissions.json")))

    # reads: the GitHub key-folder hard block, every spelling
    reads = [
        ("read_file firmlink credential.json", "read_file", {"path": DATA + CRED}),
        ("read_file /.nofollow credential.json", "read_file", {"path": "/.nofollow" + CRED}),
        ("read_file /.resolve/1 credential.json", "read_file", {"path": "/.resolve/1" + CRED}),
        ("read_file /.vol path", "read_file", {"path": "/.vol/1/2"}),
        ("search_files rooted at firmlink ~/.hermes/dashboard", "search_files",
         {"pattern": "github_pat_", "path": DATA + RHOME + "/.hermes/dashboard"}),
        ("search_files rooted at firmlink ~/.hermes", "search_files",
         {"pattern": "x", "path": DATA + RHOME + "/.hermes"}),
        ("search_files rooted at /.nofollow ~/.hermes", "search_files",
         {"pattern": "x", "path": "/.nofollow" + RHOME + "/.hermes"}),
        ("search_files rooted at a /.vol folder", "search_files",
         {"pattern": "x", "path": vol_dir}),
        ("MCP read of a /.nofollow credential.json", "mcp_fs_read_file",
         {"path": "/.nofollow" + CRED}),
        ("MCP read of a firmlink credential.json", "mcp_fs_read_file",
         {"path": DATA + CRED}),
    ]
    for name, tool, args in reads:
        check("E10: %s is refused" % name, blocked(gate(tool, args)), gate(tool, args))

    # controls: an ordinary file spelled through the firmlink stays ordinary
    check("E10 control: a firmlink spelling of an ordinary file is not gated",
          gate("write_file", {"path": DATA + RHOME + "/Documents/a.md",
                              "content": "x"}) is None)
    check("E10 control: reading an ordinary file through the firmlink is allowed",
          gate("read_file", {"path": DATA + RHOME + "/Documents/a.md"}) is None)
    check("E10 control: search_files rooted at the firmlink ~ is allowed (.hermes is hidden)",
          gate("search_files", {"pattern": "x", "path": DATA + RHOME}) is None)
    check("E10 control: /tmp/x and a folder merely NAMED .volume are untouched",
          gate("write_file", {"path": "/tmp/x.txt", "content": "x"}) is None
          and gate("write_file", {"path": home("Documents/.volume/x"), "content": "x"}) is None)

    if DARWIN:
        # the real kernel forms: every spelling names the same inode, and the
        # identity form (F_GETPATH) collapses them to the canonical path
        st = os.stat(home(".zshrc"))
        vol_file = "/.vol/%d/%d" % (st.st_dev, st.st_ino)
        same = all(os.path.samefile(p, home(".zshrc")) for p in
                   (DATA + RHOME + "/.zshrc", "/.nofollow" + RHOME + "/.zshrc",
                    "/.resolve/1" + RHOME + "/.zshrc", vol_dir + "/.zshrc", vol_file))
        check("E10 (Darwin): the alias spellings really name ~/.zshrc on this OS", same)
        canon = G._canon
        want = G._fold(os.path.join(RHOME, ".zshrc"))
        got = [G._fold(canon(p) or "") for p in
               (DATA + RHOME + "/.zshrc", vol_file, vol_dir + "/.zshrc",
                DATA + RHOME + "/Library/LaunchAgents/new/x.plist")]
        check("E10 (Darwin): F_GETPATH on the deepest existing ancestor collapses "
              "the firmlink and /.vol spellings",
              got[:3] == [want] * 3
              and got[3] == G._fold(os.path.join(RHOME, "Library/LaunchAgents/new/x.plist")),
              got)
        check("E10 (Darwin): classify_path of a /.vol file inode is rc",
              G.classify_path(vol_file) == "rc", G.classify_path(vol_file))
        r = gate("read_file", {"path": "/.vol/%d/%d" % (os.stat(CRED).st_dev,
                                                         os.stat(CRED).st_ino)})
        check("E10 (Darwin): read_file of the key file by /.vol inode is refused",
              blocked(r), r)
    # a FIFO must not hang the identity probe
    fifo = home("Documents/pipe")
    os.mkfifo(fifo)
    t0 = time.time()
    G.classify_path(fifo)
    check("E10: canonicalising a FIFO does not block", time.time() - t0 < 5)


def e12():
    print("E12 — a classified V4A target is never dropped")
    os.makedirs(home(".ssh"), exist_ok=True)
    TASK["cwd"] = home(".ssh")                 # after `terminal cd ~/.ssh`
    try:
        for hdr in ("*** Add File: authorized_keys", "*** Update File: config",
                    "*** Move File: /tmp/k -> authorized_keys"):
            r = gate("patch", {"mode": "patch", "patch":
                               "*** Begin Patch\n%s\n@@\n-a\n+ssh-ed25519 AAAA\n"
                               "*** End Patch\n" % hdr})
            check("E12: after cd ~/.ssh, V4A `%s` is gated" % hdr, blocked(r), r)
        check("E12: a V4A header naming ~/.ssh/authorized_keys absolutely is gated",
              blocked(gate("patch", {"mode": "patch", "patch":
                                     "*** Begin Patch\n*** Add File: ~/.ssh/authorized_keys\n"
                                     "+x\n*** End Patch\n"})))
        # write_file hands Hermes the resolved absolute path, so Hermes' own
        # refusal there is trusted (no pointless card)
        check("E12 control: write_file authorized_keys (relative) is still left to Hermes",
              gate("write_file", {"path": "authorized_keys", "content": "x"}) is None)
        check("E12 control: write_file ~/.ssh/config is still left to Hermes",
              gate("write_file", {"path": "~/.ssh/config", "content": "x"}) is None)
    finally:
        TASK["cwd"] = None


def e13():
    print("E13 — relative paths follow Hermes' own task resolver")
    os.makedirs(home(".hermes/dashboard/github"), exist_ok=True)
    os.makedirs(home("notes"), exist_ok=True)
    cwd = os.getcwd()
    os.chdir(H)                                # the gateway's WorkingDirectory
    TASK["cwd"] = SANDBOX                      # TERMINAL_CWD = $HOME
    try:
        cases = [
            ("search_files with no path (default '.')", "search_files", {"pattern": "x"}, False),
            ("search_files path='.'", "search_files", {"pattern": "x", "path": "."}, False),
            ("search_files path=''", "search_files", {"pattern": "x", "path": ""}, False),
            ("write_file notes/todo.md", "write_file",
             {"path": "notes/todo.md", "content": "x"}, False),
            ("patch replace README.md", "patch",
             {"path": "README.md", "old_string": "a", "new_string": "b"}, False),
            ("V4A Add File notes/x.md", "patch",
             {"mode": "patch", "patch": "*** Add File: notes/x.md\n+x\n"}, False),
            ("read_file github/credential.json (relative to ~)", "read_file",
             {"path": "dashboard/github/credential.json"}, False),
            ("search_files path='~/.hermes'", "search_files",
             {"pattern": "x", "path": "~/.hermes"}, True),
            ("search_files path='.hermes/dashboard' (task-relative)", "search_files",
             {"pattern": "x", "path": ".hermes/dashboard"}, True),
            ("write_file .zshrc (task-relative)", "write_file",
             {"path": ".zshrc", "content": "x"}, True),
            ("write_file absolute ~/.hermes/permissions.json", "write_file",
             {"path": "~/.hermes/permissions.json", "content": "x"}, True),
        ]
        for name, tool, args, want_block in cases:
            r = gate(tool, args)
            check("E13 (gateway layout): %s -> %s" % (name, "gated" if want_block else "allowed"),
                  blocked(r) if want_block else r is None, r)
        TASK["raise"] = True                   # no resolver: the process cwd it is
        check("E13: without Hermes' resolver, search_files '.' in ~/.hermes is "
              "still blocked (process cwd fallback, fail safe)",
              blocked(gate("search_files", {"pattern": "x"})))
        check("E13: without Hermes' resolver, a relative write is judged against "
              "the process cwd",
              blocked(gate("write_file", {"path": "permissions.json", "content": "x"})))
    finally:
        TASK["raise"] = False
        TASK["cwd"] = None
        os.chdir(cwd)


def e68():
    print("E68 — files the shell startup files source")
    rc = ["~/.cargo/env", "~/.rokit/env", "~/.curlrc", "~/.wgetrc", "~/.netrc",
          "~/.p10k.zsh", "~/.oh-my-zsh/custom/evil.zsh", "~/.oh-my-zsh/oh-my-zsh.sh",
          "~/.orbstack/shell/init.zsh", "~/.opam/opam-init/init.zsh",
          "~/.bash_aliases", "~/.zsh_aliases", "~/.aliases",
          "~/.config/zsh/.zshrc", "~/.zprezto/init.zsh"]
    bad = [(p, G.classify_path(p)) for p in rc if G.classify_path(p) != "rc"]
    check("E68: %d commonly sourced files are in the rc class" % len(rc), not bad, bad)
    keep = [("~/.npmrc", "pkg"), ("~/.cargo/bin/x", "exec"), ("~/.cargo/config.toml", "pkg"),
            ("~/.cargo/registry/index/x", None), ("~/.curlrc.bak", None),
            ("~/.cargo/environment", None)]
    bad = [(p, w, G.classify_path(p)) for p, w in keep if G.classify_path(p) != w]
    check("E68 control: neighbours keep their class", not bad, bad)
    check("E68: write_file ~/.cargo/env is gated",
          blocked(gate("write_file", {"path": "~/.cargo/env", "content": "x"})))


def main():
    e10()
    e12()
    e13()
    e68()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
