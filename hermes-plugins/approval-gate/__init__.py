"""approval-gate — computer_use desktop actions and writes to sensitive paths
go through Hermes' OWN approval prompt (audit 2026-10-21 C111, C116).

WHY
---
C111. `tools/computer_use/tool.py:_request_approval()` allows everything when
no approval callback is registered, and only the interactive CLI registers one
(cli.py). So in `hermes serve` (the dashboard), the Telegram gateway and
`hermes -z`, every click / type / key / set_value / drag ran with nobody asked:
the agent could focus Terminal, type a command and press Return, and no
approval card appeared.

C116. `write_file` / `patch` have no approval path at all. Hermes' own guards
refuse /etc, /private/var, the docker socket, ~/.hermes/config.yaml, .env, ssh
keys and a few credential files. Shell rc files, ~/Library/LaunchAgents,
~/.hermes/permissions.json, mcp-allow.json, the plugins (this one included) and
the dashboard's own code were each one call away.

HOW
---
A `pre_tool_call` hook (hermes_cli/plugins.py `get_pre_tool_call_block_message`:
return {"action": "block", "message": ...} to refuse; anything else allows).
A call that needs the owner is asked through the SAME machinery the terminal
tool's dangerous-command guard uses (tools/approval.py):

  * gateway / serve: the per-session notify callback that serve and the
    Telegram gateway register (`register_gateway_notify`) and the queue wait
    `_await_gateway_decision`. Serve emits `approval.request`, so the dashboard
    shows its approval card and its Trust policy applies to the pattern key;
    Telegram shows its approval prompt. The agent thread blocks until the owner
    answers or `approvals.gateway_timeout` (300 s) passes.
  * interactive CLI: the per-thread approval callback the CLI registers
    (`tools.terminal_tool._get_approval_callback`). computer_use there is left
    to the tool's own CLI prompt, so nothing is asked twice.
  * anything else (`hermes -z`, cron, a background thread, a delegate_task
    subagent whose thread only carries Hermes' canned auto-answer — even
    `delegation.subagent_auto_approve: true` — a gateway session whose notify
    callback is gone) has NO approval surface, so the call is
    BLOCKED with a reason the model can relay. Fail closed: an exception inside
    the gate, for a call it gates, also blocks.

YOLO (`HERMES_YOLO_MODE`, which `hermes -z` sets on purpose, audit C36) and
`approvals.mode` are deliberately NOT consulted. Honouring them would re-open
exactly the unattended path this closes.

WHAT IS GATED
-------------
computer_use: every action except the read-only ones (capture, wait,
list_apps) and `focus_app` without `raise_window` (cua-driver's focus_app is a
pure window selector). An action this file does not know is gated.

write_file / patch: a target (the `path` argument, plus every
`*** Update/Add/Delete/Move File:` header of a V4A patch, read the way Hermes'
own tools/patch_parser.py reads it: split on newlines only, a lone CR kept, its regexes mirrored
here and the parser itself called when importable) that falls, in any of its
forms, under one of `_PATH_CLASSES`. The forms (audit 2026-11-09 E10, E13):
a relative path resolved by Hermes' own task resolver (this process's cwd only
when that resolver is unavailable), an absolute one as written, each with
symlinks resolved, without a /System/Volumes/Data firmlink prefix, and as the
kernel names the file (F_GETPATH on its deepest existing ancestor). A path
through /.vol, /.nofollow, /.resolve or /.file is refused outright, for reads
and searches too. A patch with a header-shaped line the gate cannot read, or
one carrying a control or hidden character (a CRLF line end aside), asks under
its own key (E11). A path is judged by the layer that opens it (review R4-c01):
write_file's and replace mode's `path` by the task resolver, which is what
Hermes hands its shell layer; a V4A header and the `path` of read_file /
search_files reach that shell layer RAW, so they are judged where IT puts them
— the TERMINAL's $HOME for `~/` (an earlier `export HOME=` changes it), the
terminal env's live cwd for a relative path (on Telegram the task resolver does
not follow `cd`) — by asking the live env, and failing closed when it cannot
answer; a `~name` there that is not a user (bash reads ~0 as its cwd, ~- as
$OLDPWD) is refused outright.
A media tool (plain open()) and an MCP server (a child of this
process) resolve a relative path against THIS process's cwd — ~/.hermes on the
Telegram gateway — so theirs is judged there as well. The classes: shell startup files (and files they commonly
source: ~/.cargo/env, ~/.p10k.zsh, oh-my-zsh, ~/.curlrc...), ~/.ssh, ~/Library/LaunchAgents, a program directory on PATH
(~/.local/bin — the `hermes` the serve LaunchAgent execs at every restart —
~/bin, ~/.cargo/bin and the other per-tool bins, the version managers
(~/.pyenv, ~/.nvm, ~/.rustup...), all of /opt/homebrew (the dashboard's own
python3, stdlib and site-packages live there) and /usr/local's bin, lib,
Cellar and opt, the user site-packages, and every directory on this process's PATH that
is under ~ or writable by it, ~ itself excepted), git configuration and hooks
(~/.gitconfig, ~/.config/git, and in ANY repository .git/config,
.git/config.worktree, .git/commondir and .git/hooks/, the same in a bare
`<name>.git`, and a worktree's `.git` file), package-manager config (.npmrc, .yarnrc,
pip.conf, uv, poetry, cargo, gem, gradle init scripts...), HERMES_HOME and
~/.hermes (except dashboard/inbox/), ~/.claude and ~/.claude.json, and this
Hermes Assistant install directory. A `path` target that Hermes itself
already refuses (config.yaml, .env, ~/.ssh, ~/.npmrc...) is left to Hermes, so
the owner is never asked to approve a write that cannot happen. A V4A header
target never is: Hermes checks the RAW relative header against its own
process cwd and then writes it in the terminal's, so its refusal does not hold
there (E12).

The card shows long text as its head AND tail with the omitted count between
them, says `replace_all`, and shows bidi / default-ignorable characters as
⟦U+XXXX⟧ (E14). The whole card is kept under 3,000 characters, cut the same way, because
Telegram's prompt drops everything past 3,800. Only "once", "session", "always" and "approve" approve; any
other answer refuses (E67).

Other writers, same path classes, same keys (audit C116 follow-up):
  * an MCP tool (mcp_<server>_<tool>) whose name has a word starting with a
    write verb (write, edit, create, move, copy, delete, save...) or has no
    read verb at all (read, get, list, search, tree, info...), so an unknown
    verb fails safe: every path-shaped value under a path-named argument
    (path, paths, source, destination, filePath, repo_path, path2, output, a
    file: uri..., nested up to four levels; more than 1,024 of them fails
    closed). A bare word ("main") is an identifier, not a path. An MCP server
    is another process, so NO Hermes file guard applies and nothing is "left
    to Hermes" for it: ~/.ssh is gated there.
  * a media tool's output argument (text_to_speech `output_path`, and the
    same keys on image_generate / video_generate should they grow one): the
    tts tool rejects `..` but writes anywhere else with plain open(). Its own
    caches under HERMES_HOME (audio_cache, cache/audio, image_cache,
    cache/images, video_cache, cache/videos) are exempt for these tools only,
    and only as written AND resolved, so a symlink planted there is classed by
    what it points at.

GitHub sandbox additions (round 3, docs/plans/github-sandbox-2026-10.md §5.3)
— DEFENCE IN DEPTH, NOT A BOUNDARY. The agent runs as the owner's macOS user,
so `cat`, an encoded command or a script it writes first gets past every one
of these. They stop an honest mistake or an injected instruction that names
the thing literally, and each leaves an audit line:
  * class `secret` (HARD BLOCK, never asked): read_file / search_files, and
    an MCP tool that does not write, on ~/.hermes/dashboard/github/** — the
    folder that holds Hermes's GitHub key. Path forms are the ones `_forms()`
    already checks (task-relative, as written, symlinks resolved). A
    search_files whose root is an ANCESTOR the search would descend from
    (~/.hermes, ~/.hermes/dashboard: nothing hidden between them, and the
    search tools skip hidden folders below the root, never the root itself)
    is blocked too, because it would grep the key file. This mirrors Hermes'
    own `get_read_block_error` for .env / mcp-tokens, whose docstring says
    "not a security boundary"; so does this one. Writes there were already
    the `hermes` class (asked).
  * class `sandbox-git` (ASKED): a write anywhere inside a `.git` folder under
    ~/HermesSandbox. Hooks or config planted in a sandbox clone run on the
    OWNER's next manual git command in that folder. It sorts before `git`, so
    a sandbox hook carries this key even where `git` also matches, and a
    `git` allowlist entry does not pass it.
  * terminal / execute_code / process(write|submit) TRIPWIRES (HARD BLOCK via
    `_refused()`): the command text names an owner-only GitHub route
    (/api/github/changes/decide, /changes/retract, /connect, /drift/ack,
    /pause with `false`), Hermes's GitHub key file, a command that prints a
    GitHub credential (`gh auth token`, `gh auth status --show-token`,
    `git credential fill` or a helper's `get`, `security
    find-internet-password`/`find-generic-password` with -w/-g for
    github.com), or a `git push` to a github.com URL. A string match, trivially
    evaded by encoding; everything else (git status, git diff, pytest, curl to
    the agent-callable /api/github routes) passes untouched. Stable keys:
    github:decide, github:retract, github:connect, github:unpause,
    github:drift-ack, github:credential, github:gh-token,
    github:credential-fill, github:keychain, github:push. The five owner-route
    keys skip the free text of the skill's own `gh-sandbox.sh request` (its
    title, body, mode and a here-document fed to it): that script only ever
    posts /api/github/changes, so a request that NAMES a route is not a call.

APPROVAL SCOPE
--------------
computer_use: "approve" / "once" covers this one call. "session" (and
"always", which is downgraded) covers that ACTION for the rest of the session,
the same scope the tool's own CLI prompt uses. A file write is always once.
The dashboard card answers "once" (or "deny"), so in the dashboard every
desktop action asks; Telegram's prompt also offers "session".

Pattern keys: "computer_use:<action>" plus one key per path class; a write
that touches several classes carries all of their keys, most sensitive first,
and passes unasked only when every one is allowlisted. A path-class key names
the CLASS, not the tool, so an MCP or media write to ~/.zshrc carries the same
key as a write_file to it and one allowlist entry or Trust tier covers both.
The dashboard's Trust panel classes them with its own heuristic: computer_use,
login items, program directories, git and package-manager config land in
"unknown" (always asks), the Hermes and install-dir keys in "hermes-self"
(asks), and shell rc files and ~/.ssh in "credential-write", whose shipped
tier is NEVER, so the dashboard auto-denies those until the owner sets that
class to Ask, exactly as it does for a terminal write to a shell rc file. Keep
every key STABLE, and keep "rm "/"delete"/"filesystem"/"block device" out of
new ones: permissions.py's heuristic would file them under a different class.

OPT-OUT
-------
* The whole gate: remove `approval-gate` from `plugins.enabled` in
  ~/.hermes/config.yaml (or `hermes plugins disable approval-gate`), then
  restart serve and the gateway.
* One action or one path class: add its pattern key to `command_allowlist` in
  ~/.hermes/config.yaml, for example `computer_use:scroll`. That is Hermes' own
  permanent allowlist, consulted through `tools.approval.is_approved`, and it
  applies to unattended runs too.

Never raises out of the hook. No import at module load beyond the stdlib: the
Hermes modules are imported inside the hook, so the file loads (and unit-tests)
anywhere.
"""

import json
import logging
import os
import re
import shlex
import sys
import threading
import unicodedata

try:                                  # F_GETPATH: the kernel's own name for an
    import fcntl as _fcntl            # open file (macOS); absent elsewhere
except Exception:                     # pragma: no cover - non-POSIX
    _fcntl = None

logger = logging.getLogger("hermes_plugins.approval_gate")

_TAG = "[approval-gate]"

# --------------------------------------------------------------------------
# computer_use
# --------------------------------------------------------------------------
# Mirrors tools/computer_use/tool.py: _SAFE_ACTIONS are read-only; every other
# action (the tool's _DESTRUCTIVE_ACTIONS, or anything added later) is gated.
_CU_READ_ONLY = frozenset({"capture", "wait", "list_apps"})

_FILE_TOOLS = frozenset({"write_file", "patch"})
# read tools checked ONLY for the `secret` class (Hermes's GitHub key folder)
_READ_TOOLS = frozenset({"read_file", "search_files"})
# tools whose argument is a command line / program text: the GitHub tripwires
_SHELL_TOOLS = {"terminal": ("command",), "execute_code": ("code",),
                "process": ("data",)}
_GATED_TOOLS = (frozenset({"computer_use"}) | _FILE_TOOLS | _READ_TOOLS
                | frozenset(_SHELL_TOOLS))

# Media tools that save a file wherever their output argument says. Only the
# output keys are read: an input image path is not a write.
_MEDIA_TOOLS = frozenset({"text_to_speech", "image_generate", "video_generate",
                          "xai_video_edit", "xai_video_extend"})
_MEDIA_OUT_ARGS = ("output_path", "output_file", "output_dir", "save_path",
                   "save_to")
# their own default output directories, relative to HERMES_HOME
_MEDIA_CACHES = ("audio_cache", os.path.join("cache", "audio"), "image_cache",
                 os.path.join("cache", "images"), "video_cache",
                 os.path.join("cache", "videos"))

# MCP tools are named mcp_<server>_<tool> (tools/mcp_tool.py). A name with a
# word (snake_case or camelCase) that STARTS with one of these may write a
# file; so may a name with none of _MCP_READ_WORDS, because an unknown verb
# ("sync", "apply", "commit") fails safe. Over-matching only costs a prompt,
# and only when a path argument really falls in a gated class.
_MCP_WRITE_WORDS = ("write", "edit", "creat", "make", "mk", "move", "renam",
                    "copy", "append", "patch", "put", "set", "save", "store",
                    "upload", "download", "updat", "modif", "insert", "replac",
                    "overwrit", "delet", "remov", "rm", "unlink", "mv", "cp",
                    "touch", "chmod", "chown", "symlink", "link", "extract",
                    "unzip", "untar", "truncat",
                    # verbs that write and often sit next to a read noun
                    # ("apply_diff", "export_query_results", "sync_status")
                    "apply", "export", "generat", "convert", "render", "dump",
                    "compress", "restor", "revert", "merge", "sync")
_MCP_READ_WORDS = frozenset((
    "read", "get", "list", "search", "find", "grep", "stat", "info", "tree",
    "view", "show", "query", "count", "exists", "describe", "head", "tail",
    "cat", "diff", "status", "log", "fetch", "lookup", "inspect", "check",
    "resources", "resource", "prompt", "prompts"))
# argument names an MCP tool carries a path in, compared as their joined words
# (filePath, file_path and file-path are all "filepath"), plus any name whose
# LAST word is one of _MCP_PATH_WORDS (repo_path, outputFile, target_dir)
_MCP_PATH_ARGS = frozenset((
    "path", "paths", "file", "files", "filepath", "filepaths", "filename",
    "source", "sources", "destination", "dest", "target", "src", "dst", "from",
    "to", "oldpath", "newpath", "sourcepath", "destinationpath", "targetpath",
    "directory", "dir", "dirpath", "folder", "outputpath", "outputfile",
    "location", "uri", "output", "out"))
_MCP_PATH_WORDS = frozenset((
    "path", "paths", "file", "files", "filename", "filenames", "dir", "dirs",
    "directory", "directories", "folder", "folders", "destination", "dest",
    "source", "sources", "src", "dst", "target", "uri", "uris", "location",
    "output"))
# More path-shaped values than this in one call fails CLOSED: a cap that
# stopped looking would let padding carry the one real target past the gate.
_MCP_MAX_PATHS = 1024

_SHOW = 600          # chars of typed text / file content shown on the card
# The whole card, after escapes and ⟦U+XXXX⟧ marks have widened it. Telegram's
# prompt keeps the first 3,800 characters of a command and drops the rest, so a
# longer card would lose its tail (and the omitted count) there.
_CARD_MAX = 3000

# Shown on the card as ⟦U+XXXX⟧ instead of raw (audit 2026-11-09 E14): bidi
# controls, zero-width and other default-ignorable code points, TAG characters,
# runs of two or more variation selectors, C0 controls except \t \n \r, DEL
# and C1 controls. A COPY of dashboard/aux_ghchanges.py _GHC_HIDDEN_RE (the
# GitHub review sheet's rule), not an import: this file loads inside Hermes,
# not the dashboard. t_e14_gate_card_and_answers.py compares the two code point
# by code point; change both or neither.
_HIDDEN_RE = re.compile(
    "(?:[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u2028-\u202e"
    "\u2060-\u206f\u3164\uffa0\ufeff\ufff0-\ufffb"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
    "\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|[\ufe00-\ufe0f\u180b-\u180d\u180f]{2,})")


def _mark_hidden(s):
    """`s` with every hidden character shown as ⟦U+XXXX⟧, so a bidi override
    cannot reorder the card and a zero-width character cannot hide in it."""
    return _HIDDEN_RE.sub(
        lambda m: "".join("⟦U+%04X⟧" % ord(c) for c in m.group(0)), s)


def _cut(s, limit):
    """(head, omitted, tail): the first two thirds and the last third of
    `limit` characters, so a payload at the END of a long text is on the card
    too (audit 2026-11-09 E14). `omitted` is 0 when `s` fits whole."""
    if len(s) <= limit:
        return s, 0, ""
    head = max(1, limit * 2 // 3)
    tail = max(1, limit - head)
    return s[:head], len(s) - head - tail, s[len(s) - tail:]


def _cu_gated(args):
    """True when this computer_use call changes something on screen."""
    action = str(args.get("action") or "").strip().lower()
    if not action:
        return False                  # the tool answers "missing action" itself
    if action in _CU_READ_ONLY:
        return False
    if action == "focus_app" and not args.get("raise_window"):
        return False                  # selects a target window, raises nothing
    return True


def _q(text, limit=_SHOW):
    """A value quoted for the approval card: newlines visible, length bounded
    by showing the head AND the tail with the omitted count between them."""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    head, omitted, tail = _cut(s, limit)
    if not omitted:
        return json.dumps(head, ensure_ascii=False)
    return "%s …(%d chars omitted)… %s" % (json.dumps(head, ensure_ascii=False),
                                          omitted, json.dumps(tail, ensure_ascii=False))


def _cu_command(action, args):
    """What the owner reads on the approval card."""
    bits = ["computer_use", action]
    app = args.get("app")
    if action == "type":
        bits.append(_q(args.get("text")))
    elif action == "key":
        bits.append(_q(args.get("keys"), 80))
    elif action == "set_value":
        bits.append(_q(args.get("value")))
    elif action == "scroll":
        bits.append("%s x%s" % (args.get("direction", "down"), args.get("amount", 3)))
    elif action == "drag":
        bits.append("%s -> %s" % (args.get("from_element", args.get("from_coordinate")),
                                  args.get("to_element", args.get("to_coordinate"))))
    elif action == "focus_app":
        bits.append(_q(app, 80) + " (raise window)")
        app = None
    if args.get("button") not in (None, "", "left"):
        bits.append("button " + _q(args.get("button"), 40))
    if action not in ("drag", "focus_app", "scroll", "wait"):
        if args.get("element") is not None:
            bits.append("on element #%s" % args.get("element"))
        elif args.get("coordinate"):
            bits.append("at %s" % (tuple(args.get("coordinate")),))
    if args.get("modifiers"):
        bits.append("modifiers %s" % (args.get("modifiers"),))
    if app:
        bits.append("in " + _q(app, 80))
    return " ".join(str(b) for b in bits)


# --------------------------------------------------------------------------
# sensitive paths
# --------------------------------------------------------------------------
_RC_FILES = (".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout",
             ".bashrc", ".bash_profile", ".bash_login", ".bash_logout",
             ".profile",
             # files the startup files commonly source, and the config the
             # skills' own curl/wget read (audit 2026-11-09 E68)
             ".bash_aliases", ".zsh_aliases", ".aliases", ".p10k.zsh",
             os.path.join(".cargo", "env"), os.path.join(".rokit", "env"),
             ".curlrc", ".wgetrc", ".netrc")
# folders whose files a startup file sources (framework plugins, init scripts)
_RC_DIRS = (os.path.join(".config", "fish"), os.path.join(".config", "zsh"),
            ".oh-my-zsh", ".zprezto", os.path.join(".orbstack", "shell"),
            os.path.join(".opam", "opam-init"))

# (pattern key, why it matters). The pattern key is what the dashboard's Trust
# panel classifies and what `command_allowlist` matches, so keep each one
# STABLE: no paths, no counts.
_PATH_CLASSES = {
    "rc": ("write_file/patch to a sensitive credential/SSH/shell-rc path",
           "a shell startup file runs in every new terminal, so writing it "
           "is code execution"),
    "login": ("write_file/patch to a login item (~/Library/LaunchAgents)",
              "a LaunchAgent runs at every login, so writing one is code "
              "execution"),
    "hermes": ("write_file/patch to Hermes config, policy or plugin code (~/.hermes)",
               "Hermes' own config, approval policy, memory, plugins and code "
               "live here; a write can change what the agent is allowed to do"),
    "install": ("write_file/patch to the Hermes Assistant install directory",
                "the dashboard's own code runs as you on its next restart"),
    "claude": ("write_file/patch to Claude Code config (~/.claude)",
               "Claude Code's settings, hooks and MCP servers run code as you"),
    # C116 follow-up. The words are chosen for permissions.py's heuristic:
    # "SSH" lands in credential-write (NEVER, like rc); the other three hold
    # none of its trigger words and land in "unknown" (always asks).
    "ssh": ("write_file/patch to SSH configuration or keys (~/.ssh)",
            "~/.ssh/config runs any command through ProxyCommand or "
            "LocalCommand, and authorized_keys grants a login"),
    "exec": ("write_file/patch to a program directory on PATH (~/.local/bin, ~/bin)",
             "a file there runs whenever its name is called (the always-on "
             "serve LaunchAgent runs ~/.local/bin/hermes at every restart), "
             "so writing one is code execution"),
    "git": ("write_file/patch to git configuration or hooks (~/.gitconfig, "
            "~/.config/git, .git/hooks)",
            "git config points hooksPath, aliases, pagers and fsmonitor at "
            "programs, so writing it is code execution on the next git command"),
    "pkg": ("write_file/patch to a package-manager config (~/.npmrc, pip.conf, "
            "~/.cargo/config)",
            "package-manager config chooses where packages come from and what "
            "runs on install, so writing it is code execution on the next "
            "install"),
    # GitHub sandbox (round 3, §5.3). No "hermes" in the key: the Trust
    # heuristic would file a key naming ~/HermesSandbox under hermes-self;
    # these words land in "unknown" (always asks), like "git".
    "sandbox-git": ("write_file/patch inside a sandbox clone's .git folder",
                    "hooks or config planted in a sandbox clone's .git folder "
                    "run as you on your next git command in that folder"),
    # audit 2026-11-09 E11. A V4A header carrying a control or hidden
    # character, or a header-shaped line the gate cannot parse, may name any
    # file. No trigger word of permissions.py's heuristic: lands in "unknown"
    # (always asks).
    "v4a-unread": ("write_file/patch with a patch header the approval gate cannot read",
                   "a patch header with a control or hidden character, or one "
                   "the gate cannot parse, may name any file, so the owner "
                   "reads the patch before it runs"),
}


# Most sensitive first: the order a path is classified in, and the order a
# multi-class write lists its keys in. sandbox-git sorts before git: a hook in
# a sandbox clone matches both, and it must carry the sandbox key (a `git`
# allowlist entry must not pass it).
_CLASS_ORDER = ("rc", "ssh", "login", "exec", "sandbox-git", "git", "pkg",
                "hermes", "install", "claude", "v4a-unread")

# under ~ (relative), program directories that are on PATH for one tool or
# another; each is a prefix row of the "exec" class
_EXEC_HOME_DIRS = (
    os.path.join(".local", "bin"), "bin", os.path.join(".cargo", "bin"),
    os.path.join("go", "bin"),
    # version managers, whole: a shim or bin entry execs the interpreter and
    # packages installed beside it (~/.pyenv/versions, ~/.nvm/versions/...)
    ".bun", ".deno", ".npm-global", ".volta", ".pyenv", ".rbenv", ".nvm",
    ".rustup",
    # the user site-packages: a .pth or usercustomize.py there runs in every
    # Python process this user starts, the dashboard's included
    os.path.join("Library", "Python"), os.path.join(".local", "lib"),
)
# user-writable on a Homebrew Mac, and ahead of /usr/bin on PATH. All of
# /opt/homebrew, not only bin: the dashboard runs /opt/homebrew/bin/python3,
# whose stdlib (Cellar) and site-packages (lib) are this user's to write, so a
# sitecustomize.py or .pth there runs at the next dashboard start.
_EXEC_SYSTEM_DIRS = ("/usr/local/bin", "/usr/local/sbin", "/usr/local/lib",
                     "/usr/local/Cellar", "/usr/local/opt", "/opt/homebrew")

_GIT_FILES = (".gitconfig",)                                     # exact
_GIT_DIRS = (os.path.join(".config", "git"), ".git-templates", ".git_template")

_PKG_FILES = (".npmrc", ".yarnrc", ".yarnrc.yml", ".bunfig.toml", ".pypirc",
              ".condarc", ".gemrc", ".pydistutils.cfg",
              os.path.join(".cargo", "config"),
              os.path.join(".cargo", "config.toml"),
              os.path.join(".m2", "settings.xml"),
              os.path.join(".gradle", "gradle.properties"))
_PKG_DIRS = (".pip", os.path.join(".config", "pip"),
             os.path.join("Library", "Application Support", "pip"),
             os.path.join(".config", "uv"), os.path.join(".config", "pypoetry"),
             os.path.join("Library", "Application Support", "pypoetry"),
             os.path.join(".config", "pnpm"),
             os.path.join("Library", "Preferences", "pnpm"),
             os.path.join(".gradle", "init.d"))


def _real(p):
    try:
        return os.path.realpath(p)
    except Exception:
        return os.path.abspath(p)


def _lead(p):
    """One leading slash: POSIX lets normpath keep `//x`, macOS reads it as /x."""
    return "/" + p.lstrip("/") if p.startswith("/") else p


def _fold(p):
    # APFS is case-insensitive by default: ~/.ZSHRC is ~/.zshrc. Folding both
    # sides can only over-match on a case-sensitive volume (fail safe).
    return _lead(os.path.normpath(p)).casefold()


# macOS spells one file several ways that are not symlinks, so realpath()
# leaves them alone (audit 2026-11-09 E10): the Data volume's firmlink
# (/System/Volumes/Data<path> is the same file as <path>), and the kernel's own path
# prefixes /.nofollow/<path>, /.resolve/<n>/<path>, /.vol/<dev>/<inode>/...
# and /.file/id=<dev>.<inode>. The firmlink prefix is stripped before a path
# is classified; a path under one of the kernel prefixes is REFUSED outright
# (there is nothing to approve: the owner could not tell from it which file it
# is). Below that, `_canon` asks the kernel which file a path names.
_DATA_VOLUME = ("system", "volumes", "data")
_ALIAS_ROOTS = frozenset((".vol", ".nofollow", ".resolve", ".file"))


def _strip_data(p):
    """`p` (absolute) without any leading /System/Volumes/Data."""
    if not p.startswith("/"):
        return p
    parts = _lead(os.path.normpath(p)).split("/")[1:]
    while [c.casefold() for c in parts[:3]] == list(_DATA_VOLUME):
        parts = parts[3:]
    return "/" + "/".join(parts)


def _alias_root(p):
    """True when absolute `p` goes through a kernel path prefix (/.vol ...)."""
    if not p.startswith("/"):
        return False
    parts = _strip_data(p).split("/")
    return len(parts) > 1 and parts[1].casefold() in _ALIAS_ROOTS


_F_GETPATH = getattr(_fcntl, "F_GETPATH", None) if _fcntl is not None else None
if _F_GETPATH is None and _fcntl is not None and sys.platform == "darwin":
    _F_GETPATH = 50                   # <sys/fcntl.h>; Python < 3.9 lacks the name
_OPEN_PROBE = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
               | getattr(os, "O_CLOEXEC", 0)
               | getattr(os, "O_EVTONLY", 0x8000 if sys.platform == "darwin" else 0))


def _canon(p):
    """The kernel's own path for the file `p` names (F_GETPATH on the deepest
    ancestor that exists, plus the part that does not), or None where the
    platform has no F_GETPATH or nothing can be opened. Collapses the firmlink,
    /.nofollow, /.resolve and /.vol spellings to the one the roots use. Opened
    O_EVTONLY|O_NONBLOCK|O_NOCTTY: no data is read and a FIFO cannot block."""
    if _F_GETPATH is None or not isinstance(p, str) or not p.startswith("/"):
        return None
    cur, tail = _lead(os.path.normpath(p)), []
    for _ in range(512):
        try:
            fd = os.open(cur, _OPEN_PROBE)
        except (OSError, ValueError):
            parent, name = os.path.split(cur)
            if parent == cur or not name:
                return None
            tail.insert(0, name)
            cur = parent
            continue
        try:
            buf = _fcntl.fcntl(fd, _F_GETPATH, bytes(1024))   # MAXPATHLEN
        except (OSError, ValueError):
            return None
        finally:
            os.close(fd)
        base = os.fsdecode(bytes(buf).split(b"\0", 1)[0])
        if not base.startswith("/"):
            return None
        return os.path.join(base, *tail) if tail else base
    return None


def _root_forms(root):
    """Every folded spelling of a protected root: as written, symlinks
    resolved, and the kernel's own path for it."""
    out = {_fold(root), _fold(_real(root)), _fold(_strip_data(_real(root)))}
    c = _canon(_real(root))
    if c:
        out.add(_fold(c))
    return out


def _under(p, root):
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def _home():
    return os.path.expanduser("~")


def _hermes_homes():
    out = [os.path.join(_home(), ".hermes")]
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        out.append(os.path.expanduser(env))
    return out


def _install_dirs():
    """This checkout (the plugin is symlinked from <repo>/hermes-plugins) and
    the default ~/HermesAssistant, each only when it really is one."""
    out = []
    here = os.path.dirname(os.path.dirname(os.path.dirname(_real(__file__))))
    for d in (here, os.path.join(_home(), "HermesAssistant")):
        if os.path.isfile(os.path.join(d, "dashboard", "server.py")):
            out.append(d)
    return out


def _path_bins(home):
    """The program directories on THIS process's PATH a write could plant a
    command in: every absolute entry under ~ (it exists or can be created) and
    every other entry this process can write to (/opt/homebrew/bin, a
    python.org framework bin...). ~ itself, or a directory above it, is left
    out: the gate cannot tell a command from a document there, and asking on
    every write under ~ would teach the owner to click yes."""
    out = []
    homes = {_fold(home), _fold(_real(home))}
    for raw in (os.environ.get("PATH") or "").split(os.pathsep):
        d = os.path.expanduser(raw.strip())
        if not d or not os.path.isabs(d) or os.path.normpath(d) == os.sep:
            continue                  # "" / "." mean the cwd: nothing to judge
        f = _fold(d)
        if any(h == f or _under(h, f) for h in homes):
            continue
        if any(_under(f, h) for h in homes) or os.access(d, os.W_OK):
            out.append(d)
    return out


def _roots():
    """[(class, root, exact)], both the literal and the symlink-resolved form
    of every root so a path matches whichever way it is written."""
    home = _home()
    rows = []
    for name in _RC_FILES:
        rows.append(("rc", os.path.join(home, name), True))
    for d in _RC_DIRS:
        rows.append(("rc", os.path.join(home, d), False))
    rows.append(("ssh", os.path.join(home, ".ssh"), False))
    rows.append(("login", os.path.join(home, "Library", "LaunchAgents"), False))
    for d in _EXEC_HOME_DIRS:
        rows.append(("exec", os.path.join(home, d), False))
    for d in _EXEC_SYSTEM_DIRS:
        rows.append(("exec", d, False))
    for d in _path_bins(home):
        rows.append(("exec", d, False))
    for name in _GIT_FILES:
        rows.append(("git", os.path.join(home, name), True))
    for d in _GIT_DIRS:
        rows.append(("git", os.path.join(home, d), False))
    for name in _PKG_FILES:
        rows.append(("pkg", os.path.join(home, name), True))
    for d in _PKG_DIRS:
        rows.append(("pkg", os.path.join(home, d), False))
    for h in _hermes_homes():
        rows.append(("hermes", h, False))
    for d in _install_dirs():
        rows.append(("install", d, False))
    rows.append(("claude", os.path.join(home, ".claude"), False))
    rows.append(("claude", os.path.join(home, ".claude.json"), True))
    out = []
    for cls, root, exact in rows:
        for form in _root_forms(root):
            out.append((cls, form, exact))
    return out


# inside ANY repository's .git directory (or a bare `<name>.git` one): its
# config (and a submodule's or a worktree's) and its hooks run code on the next
# git command in that repo, and `commondir` says where that config is read
# from. A worktree's or submodule's `.git` is a FILE ("gitdir: <path>") that
# says the same for the whole repository, so writing it is gated too.
_GIT_CONFIG_NAMES = ("config", "config.worktree", "commondir")


def _git_internal(form):
    parts = form.split(os.sep)
    for i, part in enumerate(parts):
        bare = part != ".git"
        if bare and not (part.endswith(".git") and len(part) > 4):
            continue
        rest = parts[i + 1:]
        if not rest:
            if not bare:
                return True               # a worktree's / submodule's gitfile
            continue
        if bare and len(rest) > 1 and rest[0] not in ("hooks", "modules", "worktrees"):
            continue                      # a checkout merely NAMED x.git
        if rest[-1] in _GIT_CONFIG_NAMES or "hooks" in rest:
            return True
    return False


# --------------------------------------------------------------------------
# GitHub sandbox (round 3, §5.3): defence in depth, labelled as such
# --------------------------------------------------------------------------
def _gh_secret_roots():
    """Hermes's GitHub key folder — dashboard/server.py's DATA is always
    ~/.hermes/dashboard, so aux_github's store is ~/.hermes/dashboard/github —
    as written and with symlinks resolved."""
    d = os.path.join(_home(), ".hermes", "dashboard", "github")
    return sorted(_root_forms(d))


def _sandbox_roots():
    d = os.path.join(_home(), "HermesSandbox")
    return sorted(_root_forms(d))


def _sandbox_git(form):
    """True for a path inside a `.git` folder (or a `.git` gitfile) anywhere
    under ~/HermesSandbox."""
    for root in _sandbox_roots():
        if form != root and _under(form, root):
            if ".git" in form[len(root):].split(os.sep):
                return True
    return False


def _secret_hit(forms, search=False):
    """True when a read (or, with `search`, a recursive search rooted at one
    of `forms`) reaches Hermes's GitHub key folder. A search descends into
    every folder below its root except hidden ones (rg's default, and the
    grep/find fallbacks Hermes mirrors it with), never skipping the root
    itself — so ~/.hermes reaches dashboard/github and ~ does not."""
    for form in forms:
        for root in _gh_secret_roots():
            if _under(form, root):
                return True
            if search and _under(root, form) and root != form:
                rest = root[len(form.rstrip(os.sep)) + 1:].split(os.sep)
                if not any(p.startswith(".") for p in rest if p):
                    return True
    return False


def classify_read(path, task_id="", search=False, via="tool"):
    """"secret" when this read / search reaches the GitHub key folder."""
    if search and (not isinstance(path, str) or not path.strip()):
        path = "."                    # search_files' own default root
    if not isinstance(path, str) or not path.strip():
        return None
    return "secret" if _secret_hit(_forms(path.strip(), task_id, via), search) else None


def _secret_block(tool_name, path, task_id="", via="tool"):
    logger.warning("approval-gate: %s of the GitHub key folder blocked (secret)",
                   tool_name)
    return _block(
        "%s of %s was refused: that folder holds Hermes's GitHub key, which the "
        "agent never needs and never reads (approval-gate class secret; defence "
        "in depth, not a security boundary). Nothing was read. Do NOT read it "
        "another way (terminal, a script, a copy). GitHub work goes through the "
        "github-sandbox skill: edit in the sandbox, request a change, and let "
        "the owner decide." % (tool_name, _show_path(path, task_id, via)))


# The terminal tripwires. (key, compiled pattern, what it does). Matched on
# the command text after `//` and `/./` runs are collapsed and backslash-newline
# line continuations are joined, case-insensitive.
# A string match: trivially evaded by encoding; it is here to stop an honest
# mistake or an injected instruction that names the thing literally, and to
# leave an audit line.
_GH_TRIPWIRES = (
    ("github:decide", re.compile(r"/api/github/changes/decide", re.I),
     "deciding a GitHub change is the owner's call, on the dashboard's review card"),
    ("github:retract", re.compile(r"/api/github/changes/retract", re.I),
     "retracting a pushed GitHub change is the owner's call"),
    ("github:connect", re.compile(r"/api/github/connect", re.I),
     "connecting a GitHub key is the owner's call"),
    ("github:unpause", re.compile(r"/api/github/pause[\s\S]{0,400}?\bfalse\b"
                                  r"|\bfalse\b[\s\S]{0,400}?/api/github/pause", re.I),
     "turning GitHub writes back on is the owner's call"),
    ("github:drift-ack", re.compile(r"/api/github/drift/ack", re.I),
     "dismissing a GitHub drift alert is the owner's call"),
    # The key file by name, or the folder it lives in: `cat ~/.hermes/dashboard/
    # github/*`, `cd` into it, `ls`, `grep -r` all name the folder and never the
    # file. Anchored on `.hermes/` (or $HERMES_HOME) so a project of the owner's
    # that happens to have a src/dashboard/github/ folder is not caught.
    ("github:credential", re.compile(
        r"dashboard/github/credential"
        r"|\.hermes/dashboard/github(?![\w.-])"
        r"|HERMES_HOME\}?/dashboard/github(?![\w.-])", re.I),
     "Hermes's GitHub key is never the agent's to read"),
    ("github:gh-token", re.compile(
        r"\bgh\s+auth\s+(?:token\b|status\b[^\n;|&]*?(?:--show-token\b|\s-[a-z]*t\b))", re.I),
     "printing the GitHub CLI's token hands the agent a credential"),
    ("github:credential-fill", re.compile(
        r"\bgit[\s-]+credential(?:-[a-z0-9]+)?\s+(?:fill|get)\b", re.I),
     "asking git for a saved GitHub password hands the agent a credential"),
    ("github:keychain", lambda t: _keychain_read(t),
     "reading a GitHub password from the Keychain hands the agent a credential"),
    # `git ... push`, then (options, then) the FIRST argument is a github.com
    # remote: a URL, scp-style git@github.com:..., or --repo=<url>. A looser
    # "github.com anywhere after push" refused an ordinary `git commit -m "Docs:
    # how to git push to github.com"` and any change request whose title or
    # description says so. A push through a NAMED remote (origin) is not seen:
    # this is a tripwire, not a boundary. (`://` is `:/{1,2}` because
    # _shell_text collapses `//` runs first.)
    ("github:push", re.compile(
        r"""\bgit\b[\s\S]{0,240}?\bpush\b"""
        r"""(?:[\s"',]+(?:(?:-o|--push-option|--receive-pack|--exec|--repo)[\s"',]+[^\s"',]+"""
        r"""|-[^\s"',]*))*"""
        r"""[\s"',]+(?:-[^\s"',=]*=)?(?:[a-z][a-z0-9+.-]*:/{1,2})?(?:[^\s"',@/]*@)?"""
        r"""github\.com(?![\w-])""", re.I),
     "publishing goes through a change request the owner approves, never a "
     "direct git push"),
)
_TRIP_MAX = 4 * 1024 * 1024          # chars of one command text that are scanned

_KC_CMD = re.compile(r"find-(?:internet|generic)-password", re.I)
# -g / -w (alone or in a cluster such as -gs) print the secret; -s, -a, -l... do
# not, which is why the read-only ambient detector's own call never trips this
_KC_SHOW = re.compile(r"(?:^|\s)-[A-Za-z]*[gw][A-Za-z]*(?=\s|$)")


def _keychain_read(text):
    """One simple command (split on newline ; | &) that asks `security` to
    print a github.com password."""
    for seg in re.split(r"[\n;|&]", text):
        if _KC_CMD.search(seg) and _KC_SHOW.search(seg) and "github.com" in seg.lower():
            return True
    return False


def _shell_text(tool_name, args):
    """The command / program text a shell-like call carries, as one string."""
    if tool_name == "process" and str(args.get("action") or "").strip().lower() \
            not in ("write", "submit"):
        return ""
    bits = []
    for k in _SHELL_TOOLS.get(tool_name, ()):
        v = args.get(k)
        if v is None:
            continue
        if not isinstance(v, str):
            try:
                v = json.dumps(v, ensure_ascii=False, default=str)
            except Exception:
                v = repr(v)
        bits.append(v)
    text = "\n".join(bits)[:_TRIP_MAX]
    # a backslash-newline is a line continuation: `security find-internet-password
    # \<newline> -s github.com -w` is ONE command
    # (only after an ODD run of backslashes: `\\<newline>` is an escaped
    # backslash and then a real command separator, which _owner_route_text
    # must still see as one)
    text = re.sub(r"(?<!\\)((?:\\\\)*)\\\r?\n", r"\1 ", text)
    # "dashboard//github/./credential" names the same file
    return re.sub(r"/(?:\./)+", "/", re.sub(r"/{2,}", "/", text))


# The owner-route tripwires are matched WITHOUT the free text of the skill's
# own change request, `gh-sandbox.sh request <repo> <slug> <title> [body|-]
# [new|append]` (its title, body and mode, and a here-document fed to it as the
# body). That script posts only /api/github/changes, so "Fix the
# /api/github/changes/decide guard" is a description, not a call — scanned
# whole, the skill's documented command was refused and the refusal sent the
# agent back to the very call it had made. Everything else is scanned whole: a
# request chained (; | & newline) with a real curl still trips, the repo and
# slug are kept, and a request whose text could RUN something (a $(...) or
# backtick outside single quotes, an unquoted <(...) / >(...), either of the
# first two in a here-document whose delimiter is unquoted) is not trimmed.
_GH_OWNER_ROUTE_KEYS = frozenset(("github:decide", "github:retract", "github:connect",
                                  "github:unpause", "github:drift-ack"))
_REQ_WORD = re.compile(r"\brequest\b")
_REQ_SHELLS = frozenset(("bash", "sh", "zsh"))
_REQ_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REQ_VAR = re.compile(r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$")
_HEREDOC_WORD = re.compile(r"""(?:'[^'\n]*'|"[^"\n]*"|\\.|[^\s;|&<>()'"\\])+""")


def _can_run(cmd):
    """Does command line `cmd` hold a command substitution ($(...) or a
    backtick) outside single quotes, or an unquoted process substitution?"""
    q, i, n = None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if q == "'":
            if c == "'":
                q = None
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "`" or (c == "$" and cmd.startswith("$(", i)):
            return True
        if q == '"':
            if c == '"':
                q = None
        elif c in "'\"":
            q = c
        elif c in "<>" and cmd.startswith("(", i + 1):
            return True
        i += 1
    return False


def _body_can_run(body):
    """The same for an UNQUOTED here-document's body (only a backslash quotes)."""
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
            continue
        if c == "`" or (c == "$" and body.startswith("$(", i)):
            return True
        i += 1
    return False


def _shell_segments(text):
    """`text` as simple commands: [[command, [(here-document body, quoted), ...]],
    ...] (`quoted`: its delimiter was, so the body expands nothing).
    Unquoted ; | & and newlines end a command; a here-document's lines belong
    to the command that opened it. Quotes and backslashes are honoured, and
    anything unbalanced just runs on to the end (a command this cannot read is
    kept whole by the caller)."""
    segs, cur, pend = [], [], []
    q, i, n = None, 0, len(text)

    def close():
        segs.append(["".join(cur), []])
        del cur[:]
        for p in pend:
            if p[0] is None:
                p[0] = len(segs) - 1

    while i < n:
        c = text[i]
        if q == "'":
            cur.append(c)
            i += 1
            if c == "'":
                q = None
            continue
        if c == "\\" and i + 1 < n:
            cur.append(text[i:i + 2])
            i += 2
            continue
        if q == '"':
            cur.append(c)
            i += 1
            if c == '"':
                q = None
            continue
        if c in "'\"":
            q = c
            cur.append(c)
            i += 1
            continue
        if c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            j = i + 2
            strip = j < n and text[j] == "-"
            if strip:
                j += 1
            while j < n and text[j] in " \t":
                j += 1
            m = _HEREDOC_WORD.match(text, j)
            if m:
                w = m.group(0)
                pend.append([None, re.sub(r"""['"\\]""", "", w), strip,
                             bool(re.search(r"""['"\\]""", w))])
                cur.append(text[i:m.end()])
                i = m.end()
                continue
        if c in ";|&\n":
            close()
            i += 1
            if c == "\n" and pend:
                for p in pend:
                    body = []
                    while i < n:
                        e = text.find("\n", i)
                        line = text[i:] if e < 0 else text[i:e]
                        i = n if e < 0 else e + 1
                        if (line.lstrip("\t") if p[2] else line) == p[1]:
                            break
                        body.append(line)
                    if p[0] is not None:
                        segs[p[0]][1].append(("\n".join(body), p[3]))
                del pend[:]
            continue
        cur.append(c)
        i += 1
    if cur:
        close()
    return segs


_REQ_DECLARE = frozenset(("export", "declare", "typeset", "local", "readonly"))


def _assignments(segs):
    """{NAME: [value, ...]} for every `NAME=value` the text assigns: a command
    of its own (`G=x;`), an `export`/`declare` word, or an inline prefix
    (`G=x cmd`). A segment shlex cannot read assigns nothing here."""
    out = {}
    for cmd, _bodies in segs:
        try:
            argv = shlex.split(cmd)
        except ValueError:
            continue
        i = 0
        while i < len(argv) and (argv[i] in _REQ_DECLARE or argv[i].startswith("-")):
            i += 1
        while i < len(argv) and _REQ_ASSIGN.match(argv[i]):
            name, _eq, value = argv[i].partition("=")
            out.setdefault(name, []).append(value)
            i += 1
    return out


def _request_head(cmd, bodies, assigns=None):
    """The part of a `gh-sandbox.sh request` command that is kept (through the
    slug), or None when `cmd` is any other command, cannot be read, or carries
    something that could run. A `$VAR` script stands for gh-sandbox.sh only
    while this text does not assign that variable to anything else
    (`G=curl; $G request ... <route>` is a curl call, not a request): a
    variable the text never assigns is the skill's own `G`, set by the owner's
    shell or an earlier call."""
    if _can_run(cmd) or any(not quoted and _body_can_run(b) for b, quoted in bodies):
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    i = 0
    while i < len(argv) and _REQ_ASSIGN.match(argv[i]):
        i += 1
    if i < len(argv) and os.path.basename(argv[i]) in _REQ_SHELLS:
        i += 1
    if i + 1 >= len(argv) or argv[i + 1] != "request":
        return None
    if os.path.basename(argv[i]) != "gh-sandbox.sh":
        if not _REQ_VAR.match(argv[i]):
            return None
        name = argv[i].strip("${}")
        for v in (assigns or {}).get(name, ()):
            if os.path.basename(v) != "gh-sandbox.sh":
                return None
    return " ".join(shlex.quote(a) for a in argv[:i + 4])


def _owner_route_text(text):
    """`text` without the free text of every gh-sandbox.sh request in it."""
    if not _REQ_WORD.search(text):
        return text
    try:
        out = []
        segs = _shell_segments(text)
        assigns = _assignments(segs)
        for cmd, bodies in segs:
            head = _request_head(cmd, bodies, assigns)
            if head is None:
                out.append(cmd)
                out.extend(b for b, _quoted in bodies)
            else:
                out.append(head)
        return "\n".join(out)
    except Exception:
        return text


def tripwires(tool_name, args):
    """[(key, why)] for every GitHub tripwire this call's text sets off."""
    text = _shell_text(tool_name, args if isinstance(args, dict) else {})
    if not text:
        return []
    out = []
    owner_text = None
    for key, m, why in _GH_TRIPWIRES:
        hit = m.search(text) if hasattr(m, "search") else m(text)
        if hit and key in _GH_OWNER_ROUTE_KEYS:
            if owner_text is None:
                owner_text = _owner_route_text(text)
            hit = m.search(owner_text)
        if hit:
            out.append((key, why))
    return out


def _tripwire_block(tool_name, hits):
    keys = ", ".join(k for k, _w in hits)
    logger.warning("approval-gate: GitHub tripwire %s blocked a %s call", keys,
                   tool_name)
    return _refused(
        "This %s call (approval-gate tripwire %s)" % (tool_name, keys),
        "refused: %s. Publishing to GitHub is the owner's decision: request a "
        "change with the github-sandbox skill, tell the owner it is waiting for "
        "their review in the dashboard, and stop" % "; ".join(w for _k, w in hits))


def _exempt(p):
    """The chat upload inbox is data the owner hands the agent, not policy."""
    for h in _hermes_homes():
        inbox = os.path.join(h, "dashboard", "inbox")
        for form in _root_forms(inbox):
            if _under(p, form):
                return True
    return False


# --------------------------------------------------------------------------
# who resolves the path (review R4-c01)
# --------------------------------------------------------------------------
# A path is judged by the layer that will open it, named by `via`:
#   "tool"     write_file's and replace mode's `path`: Hermes' tool layer
#              resolves it (_resolve_path_for_task) and hands the shell layer
#              the absolute result.
#   "shell"    every V4A header, and the `path` of read_file / search_files:
#              these reach tools/file_operations.py RAW. That layer expands a
#              leading ~ with the TERMINAL's $HOME (`export HOME=...` in an
#              earlier terminal call changes it) and lets bash expand ~name
#              (~0 is its cwd, ~- its $OLDPWD), and a relative path lands in
#              the shared terminal env's live cwd, whichever session moved it
#              last — on Telegram the task resolver does not follow `cd`.
#   "process"  a media tool (plain open()) or an MCP server (a child of this
#              process): a relative path is this process's cwd, which on the
#              Telegram gateway is ~/.hermes.
_CALL = threading.local()            # per-hook-call memo of the shell layer's answers
_SHELL_TILDE_NAME = re.compile(r"[a-zA-Z0-9._-]+")    # file_operations._expand_path


def _shell_tilde(path):
    """True when `path` (raw to the shell layer) starts with ~name and `name`
    is not a user on this Mac: Hermes passes such a name to bash, which reads
    ~0 / ~-N / ~+N as its directory stack and ~- as $OLDPWD, so no path form
    the gate can compute says which file it is."""
    if not isinstance(path, str):
        return False
    p = path.strip()
    if not p.startswith("~") or p == "~" or p.startswith("~/"):
        return False
    name = p[1:].split("/", 1)[0]
    if not _SHELL_TILDE_NAME.fullmatch(name):
        return False                  # Hermes leaves it literal: a folder named ~x
    try:
        import pwd
        pwd.getpwnam(name)
        return False                  # ~user: expanduser() and bash agree
    except Exception:
        return True


def _tilde_block(tool_name, path):
    logger.warning("approval-gate: %s through a shell ~name path refused", tool_name)
    return _block(
        "%s of %s was refused: a path starting with ~ and a name that is not a "
        "user (~0, ~-, ~1...) is expanded by the terminal's shell, so the "
        "approval gate cannot tell which file it is. Nothing was done. Use the "
        "file's ordinary path (absolute, or starting with ~/), and do NOT reach "
        "it another way." % (tool_name, _mark_hidden(json.dumps(path[:300], ensure_ascii=False))))


def _env_keys(task_id):
    """The keys Hermes files this task's terminal env under: the container key
    (subagents share the parent's "default" env), then the raw task id."""
    key = task_id or "default"
    try:
        from tools.terminal_tool import _resolve_container_task_id
        ckey = _resolve_container_task_id(key)
    except Exception:
        ckey = key
    return [ckey] if ckey == key else [ckey, key]


def _shell_env(task_id):
    """The live terminal environment Hermes' shell layer runs this task's file
    operations in, or None when there is none (or this Hermes has no such
    registry)."""
    try:
        from tools import terminal_tool as TT
    except Exception:
        return None
    envs = getattr(TT, "_active_environments", None)
    if not isinstance(envs, dict):
        return None
    for k in _env_keys(task_id):
        if envs.get(k) is not None:
            return envs[k]
    return None


def _remembered_cwds(task_id):
    """With no live terminal env, where Hermes starts the next one (tools/
    file_tools.py _get_file_ops): the cwd its stale file-ops entry was built
    in, else the last cwd it remembered for the env that was cleaned up."""
    out = []
    try:
        from tools import file_tools as FT
    except Exception:
        return out
    cache = getattr(FT, "_file_ops_cache", None)
    last = getattr(FT, "_last_known_cwd", None)
    for k in _env_keys(task_id):
        for c in (getattr(cache.get(k), "cwd", None) if isinstance(cache, dict) else None,
                  last.get(k) if isinstance(last, dict) else None):
            if isinstance(c, str) and os.path.isabs(c) and c not in out:
                out.append(c)
    return out


def _shell_forms(path, task_id):
    """Where Hermes' SHELL layer puts a path it is handed raw, [normalised,
    symlinks resolved]: its own `_expand_path` (the terminal's $HOME for ~),
    then the terminal env's live cwd for a relative result. With no live env
    (the next one starts with this process's HOME, which expanduser() and the
    task resolver already use) only a relative path under a remembered cwd is
    added. A live env that cannot be asked RAISES, so the hook fails closed
    rather than judge a file the shell will not touch."""
    memo = getattr(_CALL, "shell", None)
    key = (path, task_id or "default")
    if memo is not None and key in memo:
        return memo[key]
    out = []
    env = _shell_env(task_id)
    if env is not None:
        from tools.file_operations import ShellFileOperations
        ops = ShellFileOperations(env)
        p = ops._expand_path(path)
        if not isinstance(p, str) or not p:
            raise ValueError("the shell layer returned no path")
        if not os.path.isabs(p):
            cwd = getattr(env, "cwd", None) or getattr(ops, "cwd", None)
            if isinstance(cwd, str) and cwd.startswith("~"):
                cwd = ops._expand_path(cwd)
            if not isinstance(cwd, str) or not os.path.isabs(cwd):
                raise ValueError("the terminal's working directory is unknown")
            p = os.path.join(cwd, p)
        out = [os.path.normpath(p), _real(p)]
    elif not os.path.isabs(os.path.expanduser(path)):
        for cwd in _remembered_cwds(task_id):
            p = os.path.join(cwd, path)
            out.extend([os.path.normpath(p), _real(p)])
    if memo is not None:
        memo[key] = out
    return out


def _base_forms(path, task_id, via="tool"):
    """The absolute paths `path` can mean, before any alias handling: Hermes'
    own task-relative resolution (and its realpath), and an absolute path as
    written (and its realpath). A RELATIVE path handed to Hermes' own file
    tools is judged against the task's cwd, the way they resolve it; this
    process's cwd (the Telegram gateway runs in ~/.hermes) is used when
    Hermes' resolver is unavailable (audit 2026-11-09 E13) and for the writers
    that really use it (`via` "process": media and MCP tools). `via` "shell"
    adds where the shell layer puts a path it is handed raw."""
    out = []
    resolved = None
    try:
        from tools.file_tools import _resolve_path_for_task
        r = str(_resolve_path_for_task(path, task_id or "default"))
        if os.path.isabs(r):
            resolved = r
    except Exception:
        resolved = None
    shell = []
    if via == "shell" or (via == "tool" and resolved is None):
        # (no resolver answer: write_file / patch hand the shell the raw path)
        shell = _shell_forms(path, task_id)
    told = via == "shell" and bool(shell) and _shell_env(task_id) is not None
    if told:
        # The shell layer said where it goes. The task resolver's answer is
        # not where a raw path lands, and under a stale anchor (Telegram, after
        # a `cd` into ~/.hermes and back) it would only raise a false card.
        resolved = None
    p = os.path.expanduser(path)
    if os.path.isabs(p):
        out.extend([os.path.normpath(p), _real(p)])
    elif (resolved is None and not told) or via == "process":
        p = os.path.join(os.getcwd(), p)
        out.extend([os.path.normpath(p), _real(p)])
    if resolved is not None:
        out.extend([os.path.normpath(resolved), _real(resolved)])
    out.extend(shell)
    return out


def _forms(path, task_id, via="tool"):
    """Every way `path` can be read, folded: each base form, the same without a
    /System/Volumes/Data prefix, and the kernel's own path for it (E10)."""
    seen, uniq = set(), []
    for b in _base_forms(path, task_id, via):
        for f in (b, _strip_data(b), _canon(b)):
            if not f:
                continue
            k = _fold(f)
            if k not in seen:
                seen.add(k)
                uniq.append(k)
    return uniq


def _aliased(path, task_id="", via="tool"):
    """True when `path`, in any of its base forms, goes through /.vol,
    /.nofollow, /.resolve or /.file (after any firmlink prefix)."""
    if not isinstance(path, str) or not path.strip():
        return False
    return any(_alias_root(b) for b in _base_forms(path.strip(), task_id, via))


def _alias_block(tool_name, path):
    logger.warning("approval-gate: %s through a macOS alias path refused", tool_name)
    return _block(
        "%s of %s was refused: that path goes through a macOS alias prefix "
        "(/.vol, /.nofollow, /.resolve or /.file), which names a file without "
        "its real path, so the approval gate cannot tell which file it is. "
        "Nothing was done. Use the file's ordinary path, and do NOT reach it "
        "another way." % (tool_name, _mark_hidden(json.dumps(path[:300], ensure_ascii=False))))


def _media_cache(p):
    """A media tool's own default output directory under HERMES_HOME."""
    for h in _hermes_homes():
        for c in _MEDIA_CACHES:
            d = os.path.join(h, c)
            for form in _root_forms(d):
                if _under(p, form):
                    return True
    return False


def classify_path(path, task_id="", media=False, via="tool"):
    """The `_PATH_CLASSES` key a write to `path` falls under, or None.
    `media`: the writer is a media tool, whose own caches are exempt.
    `via`: the layer that resolves the path (see `_base_forms`)."""
    if not isinstance(path, str) or not path.strip():
        return None
    forms = _forms(path.strip(), task_id, via)
    roots = _roots()
    for order in _CLASS_ORDER:
        for form in forms:
            if order == "hermes" and (_exempt(form) or (media and _media_cache(form))):
                continue
            if order == "sandbox-git" and _sandbox_git(form):
                return "sandbox-git"
            if order == "git" and _git_internal(form):
                return "git"
            for cls, root, exact in roots:
                if cls != order:
                    continue
                if (form == root) if exact else _under(form, root):
                    return cls
    return None


# Hermes Agent 0.18 tools/patch_parser.py parse_v4a_patch, verbatim: the patch
# is split on "\n" ONLY, each line is re.match()ed against these four (in this
# order) and the captured path is .strip()ped, so `\s` swallows a lone "\r"
# (and a tab, NBSP...) inside a header (audit 2026-11-09 E11).
_V4A_UPSTREAM = (re.compile(r'\*\*\*\s*Update\s+File:\s*(.+)'),
                 re.compile(r'\*\*\*\s*Add\s+File:\s*(.+)'),
                 re.compile(r'\*\*\*\s*Delete\s+File:\s*(.+)'))
_V4A_UPSTREAM_MOVE = re.compile(r'\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)')
# ... and wider, on the same lines: any case, leading space, and Codex's
# `*** Move to:` form (the safe side: a wider net only costs a prompt)
_V4A_FILE = re.compile(r"^\s*\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$",
                       re.IGNORECASE)
_V4A_MOVE = re.compile(r"^\s*\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+?)\s*$",
                       re.IGNORECASE)
_V4A_MOVE_TO = re.compile(r"^\s*\*\*\*\s*Move\s+to:\s*(.+?)\s*$", re.IGNORECASE)
# a header-SHAPED line, tested with hidden characters removed: one that the
# regexes above cannot read, or that carries a hidden character, is unreadable
_V4A_SHAPE = re.compile(r"\s*\*\*\*\s*(?:update|add|delete|move)\s*(?:file|to)",
                        re.IGNORECASE)


def _hidden_char(c):
    return unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") or bool(_HIDDEN_RE.match(c))


def _v4a_scan(text):
    """(targets, unreadable) for a V4A patch: every path a header names — as
    Hermes' own parser reads it (called when importable, and mirrored by
    `_V4A_UPSTREAM` either way) and as the wider regexes read it — and
    whether some header-shaped line carries a control/hidden character other
    than one trailing CR (a CRLF line end), or could not be read at all."""
    out, unreadable = [], False
    for ln in text.split("\n"):
        found = []
        for rx in _V4A_UPSTREAM:
            m = rx.match(ln)
            if m:
                found.append(m.group(1).strip())
        m = _V4A_UPSTREAM_MOVE.match(ln)
        if m:
            found.extend([m.group(1).strip(), m.group(2).strip()])
        m = _V4A_FILE.match(ln)
        if m:
            found.append(m.group(1))
        m = _V4A_MOVE.match(ln)
        if m:
            found.extend([m.group(1), m.group(2)])
        m = _V4A_MOVE_TO.match(ln)
        if m:
            found.append(m.group(1))
        body = ln[:-1] if ln.endswith("\r") else ln
        bare = "".join(c for c in body if not _hidden_char(c))
        if _V4A_SHAPE.match(bare) and (bare != body or not found
                                       or not all(f.strip() for f in found)):
            unreadable = True
        out.extend(f for f in found if f.strip())
    try:
        from tools.patch_parser import parse_v4a_patch
        ops, _err = parse_v4a_patch(text)
        for op in ops or ():
            for attr in ("file_path", "new_path"):
                v = getattr(op, attr, None)
                if isinstance(v, str) and v.strip():
                    out.append(v)
    except Exception:
        pass                          # the mirrored regexes above stand alone
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq, unreadable


def _file_targets(tool_name, args):
    """(path targets, patch-header targets, unreadable header) of a
    write_file / patch call. Hermes hands write_file and replace mode the
    RESOLVED path, so its own refusal of a `path` target can be trusted; a V4A
    header reaches the shell layer raw and is judged by Hermes against a
    different cwd, so a header target is never dropped (audit 2026-11-09 E12)."""
    paths = []
    p = args.get("path")
    if isinstance(p, str) and p.strip():
        paths.append(p.strip())
    headers, unreadable = [], False
    if tool_name == "patch" and isinstance(args.get("patch"), str):
        headers, unreadable = _v4a_scan(args["patch"])
    return paths, headers, unreadable


def write_targets(tool_name, args):
    """Every file a write_file / patch call would touch. Over-approximates the
    V4A parser (case-insensitive, leading space allowed) — the safe side."""
    paths, headers, _unreadable = _file_targets(tool_name, args)
    return paths + [h for h in headers if h not in paths]


def _words(name):
    """snake_case, kebab-case and camelCase words of a name, lower-cased."""
    out = []
    for chunk in re.split(r"[^A-Za-z0-9]+", str(name or "")):
        out.extend(w.lower() for w in
                   re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", chunk))
    return out


def mcp_writes(tool_name):
    """True when an MCP tool may write a file: its name carries a write verb,
    or it carries no read verb at all (an unknown verb fails safe)."""
    if not str(tool_name or "").startswith("mcp_"):
        return False
    words = _words(tool_name)[1:]                 # drop the "mcp" prefix
    if any(w.startswith(_MCP_WRITE_WORDS) for w in words):
        return True
    return not any(w in _MCP_READ_WORDS for w in words)


def _path_value(v):
    """`v` as a filesystem path when it looks like one, else None. A bare
    word ("main", "web") is an identifier, not a path: resolving it against
    the cwd (serve's is $HOME, so "bin" would be ~/bin) would gate ordinary
    calls. A newline does not make a value "not a path": it is a legal file
    name character, so `LaunchAgents/a\\nb.plist` is still a login item."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or len(s) > 4096:
        return None
    if s[:5].lower() == "file:":                  # file:///x, file://host/x, file:/x
        try:
            from urllib.parse import unquote, urlparse
            s = unquote(urlparse(s).path or "")
        except Exception:
            return None
        return s or None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", s):
        return None                               # http://, s3://: not a local file
    if s.startswith(("/", "~", ".")) or "/" in s:
        return s
    return None


def mcp_targets(args):
    """Every path an MCP tool's arguments name: string (or list-of-string)
    values under a path-named key ("path", "paths", "source", "destination",
    "filePath", "repo_path", "path2"...), at any depth up to four. More than
    `_MCP_MAX_PATHS` raises, so the hook fails closed rather than skip one."""
    out = []

    def walk(obj, depth):
        if depth > 4:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                w = _words(k)
                while w and w[-1].isdigit():
                    w.pop()                       # path2, file_1 name a path too
                named = bool(w) and ("".join(w) in _MCP_PATH_ARGS
                                     or w[-1] in _MCP_PATH_WORDS)
                vals = v if isinstance(v, (list, tuple)) else [v]
                for item in vals:
                    p = _path_value(item) if named else None
                    if p:
                        if len(out) >= _MCP_MAX_PATHS:
                            raise ValueError("more than %d path arguments"
                                             % _MCP_MAX_PATHS)
                        out.append(p)
                    elif isinstance(item, (dict, list, tuple)):
                        walk(item, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item, depth + 1)

    walk(args if isinstance(args, dict) else {}, 0)
    return out


def media_targets(args):
    """The file a media tool saves to, when the call names one."""
    out = []
    for k in _MEDIA_OUT_ARGS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _hermes_refuses(path, task_id):
    """True when Hermes' own file tools refuse this write anyway."""
    try:
        from tools.file_tools import _check_sensitive_path
        if _check_sensitive_path(path, task_id or "default"):
            return True
    except Exception:
        pass
    # is_write_denied() resolves a relative path against THIS process's cwd,
    # but write_file/patch write the path resolved against the task's cwd.
    # Ask it about the file Hermes will really write, or a relative path that
    # is denied under ~ (".ssh/config") would skip the gate for the same name
    # under a gated cwd.
    target = path
    try:
        from tools.file_tools import _resolve_path_for_task
        target = str(_resolve_path_for_task(path, task_id or "default"))
    except Exception:
        pass
    try:
        from agent.file_safety import is_write_denied
        if is_write_denied(target):
            return True
    except Exception:
        pass
    return False


def _show_path(p, task_id="", via="tool"):
    """The file the tool will really write, as the owner reads it on the card:
    resolved the way write_file/patch resolve it (against the TASK's cwd, not
    this process's), so a relative path is never shown as a different file. A
    path the shell layer is handed raw (`via` "shell") is shown where that
    layer puts it."""
    home = _home()
    real = None
    if via == "shell":
        try:
            real = (_shell_forms(p, task_id) or [None])[0]
        except Exception:
            real = None
    try:
        if not real:
            from tools.file_tools import _resolve_path_for_task
            real = str(_resolve_path_for_task(p, task_id or "default"))
    except Exception:
        real = None
    if not real:
        real = _real(os.path.expanduser(p))
    for h in (home, _real(home)):
        if h and real.startswith(h + os.sep):
            return "~" + real[len(h):]
    return real


_UNREADABLE_WHERE = "a patch header the approval gate cannot read"


def _where(targets, task_id=""):
    """`targets`: [(path or None, via)]."""
    return ", ".join(_UNREADABLE_WHERE if t is None else _show_path(t, task_id, via)
                     for t, via in targets)


def _write_command(tool_name, args, targets, task_id=""):
    where = _where(targets, task_id)
    if tool_name not in _FILE_TOOLS:
        # MCP / media: the whole call, so the owner sees what is written
        try:
            shown = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            shown = str(args)
        head, omitted, tail = _cut(shown, _SHOW)
        if omitted:
            shown = "%s …(%d chars omitted)… %s" % (head, omitted, tail)
        return "%s %s: %s" % (tool_name, where, shown)
    if tool_name == "write_file":
        content = args.get("content")
        n = len(content) if isinstance(content, str) else 0
        return "write_file %s (%d chars): %s" % (where, n, _q(content))
    if args.get("patch"):
        return "patch %s: %s" % (where, _q(args.get("patch")))
    every = ""
    if args.get("replace_all") not in (None, False, 0, ""):
        every = " (replace_all=%s: EVERY occurrence)" % _q(args.get("replace_all"), 20)
    return "patch %s: replace %s with %s%s" % (where, _q(args.get("old_string"), 300),
                                              _q(args.get("new_string"), 300), every)


# --------------------------------------------------------------------------
# asking — through tools/approval.py, the dangerous-command guard's own path
# --------------------------------------------------------------------------
def _truthy(name):
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


# Per-thread approval callbacks Hermes installs that answer without asking
# anyone: tools/delegate_tool.py _subagent_auto_approve / _subagent_auto_deny
# and agent/background_review.py _bg_review_auto_deny.
_AUTO_ANSWERS = ("_subagent_auto_", "_bg_review_auto_")

# The answers that approve, on every surface; anything else refuses.
_APPROVING = ("once", "session", "always", "approve")


def _redact(text):
    try:
        from agent.redact import redact_sensitive_text
        return redact_sensitive_text(text)
    except Exception:
        return text


def _block(msg):
    return {"action": "block", "message": "%s BLOCKED: %s" % (_TAG, msg)}


def _no_surface(what):
    return _block(
        "%s needs the owner's approval, and this run has no way to ask (a "
        "one-shot `hermes -z`, a cron job or another unattended run). Nothing was "
        "done. Do NOT try to reach the same result another way (terminal, "
        "osascript, a script, the clipboard). Tell the user what you wanted "
        "to do so they can do it themselves, or ask them to repeat the request "
        "from the dashboard chat or Telegram, where they can approve it." % what)


def _refused(what, reason):
    return _block(
        "%s was %s. The owner has NOT consented. Do NOT retry it, do NOT "
        "rephrase it, and do NOT reach the same result another way (terminal, "
        "osascript, a script, the clipboard). Stop and tell the user what you "
        "wanted to do." % (what, reason))


def _gateway_surface(A, session_key):
    """The notify callback for this session when it is a live gateway/serve
    approval context, else None."""
    if _truthy("HERMES_CRON_SESSION"):
        return None                   # cron never has a listener (upstream rule)
    ctx = getattr(A, "_is_gateway_approval_context", None)
    try:
        gw = bool(ctx()) if callable(ctx) else _truthy("HERMES_GATEWAY_SESSION")
    except Exception:
        gw = False
    if not (gw or _truthy("HERMES_EXEC_ASK")):
        return None
    lock = getattr(A, "_lock", None)
    cbs = getattr(A, "_gateway_notify_cbs", None)
    if lock is None or not isinstance(cbs, dict):
        return None
    with lock:
        return cbs.get(session_key)


def _ask(what, keys, command, description, allow_session):
    """None when approved, else a block directive. `keys` are the pattern keys
    this call carries; EVERY one must be approved (one write can touch several
    path classes, and allowlisting one class must not pass the others)."""
    from tools import approval as A

    session_key = A.get_current_session_key()
    keys = [k for k in keys if not A.is_approved(session_key, k)]
    if not keys:
        return None                   # approved earlier this session, or allowlisted
    key = keys[0]

    command, description = _mark_hidden(_redact(command)), _mark_hidden(_redact(description))
    head, omitted, tail = _cut(command, _CARD_MAX)
    if omitted:
        command = "%s …(%d more chars of this card omitted)… %s" % (head, omitted, tail)

    notify = _gateway_surface(A, session_key)
    if notify is not None:
        wait = getattr(A, "_await_gateway_decision")
        decision = wait(session_key, notify, {
            "command": command,
            "description": description,
            "pattern_key": key,
            "pattern_keys": list(keys),
            "allow_permanent": False,
        }, surface="gateway")
        if decision.get("notify_failed"):
            return _block("%s needs the owner's approval and the approval "
                          "request could not be delivered. Nothing was done. "
                          "Do NOT retry." % what)
        if not decision.get("resolved"):
            return _refused(what, "not approved: the approval request timed "
                                  "out. Silence is not consent")
        choice = decision.get("choice")
        # Only the documented approving answers approve (audit 2026-11-09
        # E67): "deny", None, "", "decline"/"cancel" (MCP elicitation shares
        # this queue) or anything else a client sends is a refusal.
        if not isinstance(choice, str) or choice not in _APPROVING:
            return _refused(what, "denied (by the owner, or by the dashboard's "
                                  "Trust policy)")
        if allow_session and choice in ("session", "always"):
            A.approve_session(session_key, key)
        logger.info("approval-gate: %s approved (%s)", key, choice)
        return None

    cb = None
    try:
        from tools.terminal_tool import _get_approval_callback
        cb = _get_approval_callback()
    except Exception:
        cb = None
    if getattr(cb, "__name__", "").startswith(_AUTO_ANSWERS):
        # A canned answer installed for a thread nobody watches (a delegate_task
        # subagent, the background review), not a person. With
        # delegation.subagent_auto_approve it says yes to everything, which is
        # YOLO by another name: treat it as no surface at all.
        cb = None
    if cb is not None:
        try:
            choice = cb(command, description, allow_permanent=False)
        except Exception as e:
            logger.warning("approval-gate: CLI approval prompt failed: %s", e)
            choice = "deny"
        if isinstance(choice, str) and choice in _APPROVING:
            if allow_session and choice in ("session", "always"):
                A.approve_session(session_key, key)
            return None
        return _refused(what, "denied by the user")

    logger.warning("approval-gate: %s blocked (no approval surface)", key)
    return _no_surface(what)


def _decide(tool_name, args, task_id):
    # GitHub sandbox (§5.3) — tripwires and the key-folder read block. Both
    # are hard blocks and neither ever asks: there is nothing to approve.
    if tool_name in _SHELL_TOOLS:
        hits = tripwires(tool_name, args)
        return _tripwire_block(tool_name, hits) if hits else None
    if tool_name in _READ_TOOLS:
        p = args.get("path")
        search = tool_name == "search_files"
        root = p if isinstance(p, str) and p.strip() else ("." if search else "")
        # both tools hand the shell layer this path raw
        if _shell_tilde(root):
            return _tilde_block(tool_name, root)
        if _aliased(root, task_id, "shell"):
            return _alias_block(tool_name, root)
        if classify_read(p if isinstance(p, str) else "", task_id, search=search,
                         via="shell"):
            return _secret_block(tool_name, p if isinstance(p, str) and p.strip()
                                 else ".", task_id, "shell")
        return None
    if str(tool_name or "").startswith("mcp_") and not mcp_writes(tool_name):
        for t in mcp_targets(args):
            if _aliased(t, task_id, "process"):
                return _alias_block(tool_name, t)
            if classify_read(t, task_id, via="process"):
                return _secret_block(tool_name, t, task_id)
        return None

    if tool_name == "computer_use":
        if not _cu_gated(args):
            return None
        mod = sys.modules.get("tools.computer_use.tool")
        if mod is None:
            from tools.computer_use import tool as mod
        if getattr(mod, "_approval_callback", None) is not None:
            return None               # interactive CLI: the tool asks itself
        action = str(args.get("action")).strip().lower()
        return _ask("computer_use `%s` (it acts on the owner's screen)" % action,
                    ["computer_use:%s" % action],
                    _cu_command(action, args),
                    "desktop control: the agent wants to %s on this Mac's "
                    "screen (approval-gate)" % action,
                    allow_session=True)

    gated = []
    if tool_name in _FILE_TOOLS:
        paths, headers, unreadable = _file_targets(tool_name, args)
        for t in headers:
            if _shell_tilde(t):
                return _tilde_block(tool_name, t)
        for t, via in [(t, "tool") for t in paths] + [(t, "shell") for t in headers]:
            if _aliased(t, task_id, via):
                return _alias_block(tool_name, t)
        for t in paths:
            # write_file / replace mode hand Hermes this path resolved, so a
            # write Hermes refuses anyway is left to it (no pointless card)
            cls = classify_path(t, task_id)
            if cls and not _hermes_refuses(t, task_id):
                gated.append((cls, t, "tool"))
        for t in headers:
            # never dropped on Hermes' word: it checks the RAW relative header
            # against its own process cwd, then writes it in the terminal's
            # (audit 2026-11-09 E12); judged where the shell layer puts it
            cls = classify_path(t, task_id, via="shell")
            shown = _show_path(t, task_id, "shell")
            if cls and not any(c == cls and p is not None and _show_path(p, task_id, v) == shown
                               for c, p, v in gated):
                gated.append((cls, t, "shell"))
        if unreadable:
            gated.append(("v4a-unread", None, "shell"))
    else:
        # An MCP server is another process and a media tool writes with plain
        # open(): Hermes' file guard covers neither, so nothing is left to it,
        # and a relative path is this process's cwd, not the task's.
        media = tool_name in _MEDIA_TOOLS
        for t in (media_targets(args) if media else mcp_targets(args)):
            if _aliased(t, task_id, "process"):
                return _alias_block(tool_name, t)
            cls = classify_path(t, task_id, media=media, via="process")
            if cls:
                gated.append((cls, t, "process"))
    if not gated:
        return None
    # Every class this call touches, most sensitive first, so the Trust policy
    # sees them all (most restrictive wins there) and an allowlisted class
    # cannot carry an un-allowlisted one through.
    classes = [c for c in _CLASS_ORDER if any(g[0] == c for g in gated)]
    targets = [(t, via) for _c, t, via in gated]
    return _ask("%s to %s" % (tool_name, _where(targets, task_id)),
                [_PATH_CLASSES[c][0] for c in classes],
                _write_command(tool_name, args, targets, task_id),
                "; ".join(_PATH_CLASSES[c][1] for c in classes) + " (approval-gate)",
                allow_session=False)


def _gated_tool(tool_name):
    # every MCP tool: one that writes goes through the path classes, one that
    # only reads is checked for the `secret` class alone
    return (tool_name in _GATED_TOOLS or tool_name in _MEDIA_TOOLS
            or str(tool_name or "").startswith("mcp_"))


def _pre_tool_call(tool_name="", args=None, task_id="", session_id="",
                   turn_id="", **_):
    try:
        if not _gated_tool(tool_name):
            return None
    except Exception:
        pass                          # cannot tell: _decide fails closed below
    _CALL.shell = {}                  # one answer per path from the shell layer
    try:
        return _decide(tool_name, args if isinstance(args, dict) else {}, task_id)
    except Exception as e:
        # Fail CLOSED for the calls this gate exists for. A crashed gate that
        # allowed would be the very hole it closes.
        logger.warning("approval-gate: %s failed closed: %s: %s",
                       tool_name, type(e).__name__, e)
        return _block("the approval gate could not check this `%s` call "
                      "(%s), so it was not run. Do NOT retry it another way; "
                      "tell the user." % (tool_name, type(e).__name__))
    finally:
        _CALL.shell = None


def register(ctx):
    ctx.register_hook("pre_tool_call", _pre_tool_call)
