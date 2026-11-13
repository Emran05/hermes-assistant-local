# permissions.py — Graduated Permission Tiers engine (P1.3).
#
# A REAL sibling module (import permissions), NOT exec-included: it is imported
# by BOTH dashboard/hermes_rpc.py (the enforcement seam) and
# dashboard/aux_permissions.py (the route registrar), so a single sys.modules
# entry means one lock, one mtime cache, one policy store shared across the
# HTTP threads and the chat-turn thread.
#
# It sits IN FRONT OF hermes-agent's own `approvals.mode: manual`: hermes keeps
# asking; this layer answers "once" (auto-approve) or "deny" (auto-deny) on the
# user's behalf when — and only when — the user has pre-decided a class into
# AUTO/NEVER via the Trust panel.  It NEVER sends session/always, so hermes's
# own allowlists never grow and ~/.hermes/permissions.json stays the single,
# visible source of graduated trust.
#
# SAFETY POSTURE (deliberately stricter than the spec's example JSON):
#   * SHIPPED_DEFAULT = "ask".  Merely installing this feature changes NOTHING —
#     every class that the spec would default to AUTO instead falls back to ASK.
#     AUTO is honored ONLY when a class is EXPLICITLY written "auto" in
#     permissions.json by the user (via the panel).  The three critical classes
#     keep their default of "never".
#   * Every failure mode (missing file, parse error, unknown key, tamper
#     detection, respond error) degrades toward ASK — never toward AUTO.
#   * Floors are enforced at BOTH write time (the API rejects) and read time
#     (decide() clamps), so a hand-crafted "credential-write":"auto" line still
#     resolves as ASK.
#   * Serve reports only the FIRST dangerous pattern a command matches (E03),
#     so decide() re-scans the command text against every pattern it knows
#     and resolves most-restrictive over all matches.  AUTO is returned only
#     for a command it has fully judged: no command text, a text longer than
#     SCAN_MAX, a redaction mark serve left in it, or any shell control
#     operator / substitution (; & | newline ` $( <( >( <<) turns AUTO into ASK.  Never and floors apply to every
#     match, so a credential write behind an Auto prefix is auto-denied.
#     So does anything upstream's detector reads differently from this scan:
#     ( ) { }, a control character, or a command word written with a quote,
#     a backslash or an expansion ("rm", ${x:-rm}).
#   * Sidecar-hash mismatch (an out-of-band edit) marks the policy UNTRUSTED and
#     suspends all AUTO until a human clicks Review & re-trust.
#
# Self-contained: stdlib only, and only these seven modules.

import hashlib
import json
import os
import re
import threading
import time
import unicodedata

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
HOME = os.path.expanduser("~")
HERMES_DIR = os.path.join(HOME, ".hermes")
DASH_DIR = os.path.join(HERMES_DIR, "dashboard")
PERM_FILE = os.path.join(HERMES_DIR, "permissions.json")          # mode 600
SIDECAR = os.path.join(DASH_DIR, "permissions.sha")               # mode 600
# The bytes the sidecar's hash was taken over (C126): written with every
# signature, so an out-of-band edit can be reviewed as a DIFF against what was
# last approved.  Only honoured while its sha256 still equals the sidecar.
SIGNED_COPY = os.path.join(DASH_DIR, "permissions.signed.json")   # mode 600
LOG_FILE = os.path.join(DASH_DIR, "permissions-log.jsonl")        # append-only

LOG_MAX = 1024 * 1024        # 1 MB -> rotate to .1 (single generation)
CMD_TRUNC = 2000             # execute_code scripts can be huge
CMD_SCRUB_SLACK = 16384      # also scrubbed past the cut: a straddling key (even PEM) fits
RECENT_WINDOW = 7 * 86400    # per-class "this week" aggregation window

TIERS = ("auto", "ask", "never")
_RANK = {"auto": 0, "ask": 1, "never": 2}     # most-restrictive wins
SHIPPED_DEFAULT = "ask"                        # the fail-safe fallback tier

# --------------------------------------------------------------------------
# class taxonomy — 19 canonical action classes
# (17 hermes-pattern classes + shortcuts-run, the P3.1 dashboard action bus,
#  + github-write, the GitHub sandbox change pipeline's pushes and PRs)
# CLASS_META[id] = {label, risk, default, floor, desc}
#   * default : the DOCUMENTED per-class default from the spec table (display
#               reference only — the ACTUAL shipped fallback is _shipped_tier()).
#   * floor   : the least-restrictive tier a class may EVER be set to.  "ask"
#               means the class can never run silently (auto is unrepresentable
#               and clamped); "" means auto is permitted.
# --------------------------------------------------------------------------
CLASS_META = {
    "read-only": {
        "label": "Read-only actions", "risk": "low",
        "default": "auto", "floor": "",
        "desc": "Reserved for future read-only bus tools — no hermes patterns today."},
    "git-destructive": {
        "label": "Git history & working tree", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "git reset --hard, force push, branch -D, clean -f — loses work or rewrites history."},
    "file-perms": {
        "label": "File permissions & ownership", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "World-writable chmod and recursive chown to root."},
    "project-config": {
        "label": "Project env/config writes", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "Overwrites of project-local .env / config files."},
    "process-control": {
        "label": "Process & service control", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "Stop/restart services, kill or force-kill processes."},
    "container-lifecycle": {
        "label": "Container lifecycle", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "docker / docker compose restart, stop, kill, down."},
    "mcp-consent": {
        "label": "MCP elicitation consent", "risk": "med",
        "default": "ask", "floor": "ask",
        "desc": "An MCP elicitation is a question — auto-answering it silently consents."},
    "shortcuts-run": {
        "label": "macOS Shortcuts runs", "risk": "med",
        "default": "ask", "floor": "ask",
        "desc": "Runs a user-exposed macOS Shortcut through the dashboard action bus — allowlisted per shortcut, never silent."},
    "github-write": {
        "label": "GitHub pushes & pull requests", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Pushes a change you approved to a hermes/ branch of a repository you connected and can open a pull request. Never silent."},
    "destructive-delete": {
        "label": "Destructive deletes", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "rm -r, find -delete, xargs rm, Windows del — irreversible file loss."},
    "sql-destructive": {
        "label": "Destructive SQL", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "DROP, TRUNCATE, DELETE without WHERE."},
    "arbitrary-exec": {
        "label": "Arbitrary code execution", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Shell -c, pipe-to-shell, decode-and-run, heredoc scripts — runs unvetted code."},
    "execute-code": {
        "label": "execute_code scripts", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Whole-script code_execution tool runs."},
    "system-config": {
        "label": "System config writes", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Overwrites or in-place edits of /etc system configuration."},
    "hermes-self": {
        "label": "Hermes self-modification", "risk": "critical",
        "default": "ask", "floor": "ask",
        "desc": "Kills/restarts the gateway or edits ~/.hermes config — the security policy itself."},
    "credential-write": {
        "label": "Credential & SSH writes", "risk": "critical",
        "default": "never", "floor": "ask",
        "desc": "Writes to SSH keys, credential files, shell rc files."},
    "disk-device": {
        "label": "Disk & block devices", "risk": "critical",
        "default": "never", "floor": "ask",
        "desc": "mkfs, dd, raw writes to block devices."},
    "privilege-escalation": {
        "label": "Privilege escalation", "risk": "critical",
        "default": "never", "floor": "ask",
        "desc": "sudo with stdin/askpass/shell/list privilege flags."},
    "unknown": {
        "label": "Unrecognized actions", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Any pattern not in the table — new upstream patterns land here and always ask."},
}

# Display order for the Trust panel + GET payload: critical -> high -> med -> low.
CLASS_ORDER = [
    "credential-write", "disk-device", "privilege-escalation", "hermes-self",
    "destructive-delete", "sql-destructive", "arbitrary-exec", "execute-code",
    "system-config", "unknown",
    "git-destructive", "file-perms", "project-config", "process-control",
    "container-lifecycle", "mcp-consent", "shortcuts-run", "github-write",
    "read-only",
]

# --------------------------------------------------------------------------
# PATTERN_CLASS — every hermes pattern_key -> canonical class.
# The 72 unique DANGEROUS_PATTERNS description keys (approval.py:498-712;
# "start gateway outside systemd" appears twice) + the 2 event literals.
# "fork bomb" is the one DANGEROUS key absent from the spec taxonomy — it is a
# HARDLINE pattern that is blocked upstream anyway, so it maps to `unknown`
# (floor ask, never auto) per the fail-safe rule for unplaceable keys.
# --------------------------------------------------------------------------
PATTERN_CLASS = {
    # --- destructive-delete (8) ---
    "delete in root path": "destructive-delete",
    "recursive delete": "destructive-delete",
    "recursive delete (long flag)": "destructive-delete",
    "Windows cmd destructive delete": "destructive-delete",
    "Windows PowerShell destructive delete": "destructive-delete",
    "xargs with rm": "destructive-delete",
    "find -exec/-execdir rm": "destructive-delete",
    "find -delete": "destructive-delete",
    # --- git-destructive (7) ---
    "git reset --hard (destroys uncommitted changes)": "git-destructive",
    "git force push (rewrites remote history)": "git-destructive",
    "git force push short flag (rewrites remote history)": "git-destructive",
    "git clean with force (deletes untracked files)": "git-destructive",
    "git branch force delete": "git-destructive",
    "git branch force delete (long flags)": "git-destructive",
    "git branch force delete (long flags, force-first)": "git-destructive",
    # --- file-perms (4) ---
    "world/other-writable permissions": "file-perms",
    "recursive world/other-writable (long flag)": "file-perms",
    "recursive chown to root": "file-perms",
    "recursive chown to root (long flag)": "file-perms",
    # --- project-config (3) ---
    "overwrite project env/config via tee": "project-config",
    "overwrite project env/config via redirection": "project-config",
    "overwrite project env/config file": "project-config",
    # --- process-control (6) ---
    "stop/restart system service": "process-control",
    "kill all processes": "process-control",
    "force kill processes": "process-control",
    "force kill processes (killall -KILL)": "process-control",
    "force kill processes (killall -s KILL)": "process-control",
    "kill processes by regex (killall -r)": "process-control",
    # --- container-lifecycle (2) ---
    "docker compose restart/stop/kill/down (container lifecycle)": "container-lifecycle",
    "docker restart/stop/kill (container lifecycle)": "container-lifecycle",
    # --- sql-destructive (3) ---
    "SQL DROP": "sql-destructive",
    "SQL DELETE without WHERE": "sql-destructive",
    "SQL TRUNCATE": "sql-destructive",
    # --- arbitrary-exec (13) ---
    "shell command via -c/-lc flag": "arbitrary-exec",
    "script execution via -e/-c flag": "arbitrary-exec",
    "pipe remote content to shell": "arbitrary-exec",
    "execute remote script via process substitution": "arbitrary-exec",
    "execute remote content via command substitution": "arbitrary-exec",
    "pipe decoded content to shell (possible command obfuscation)": "arbitrary-exec",
    "pipe xxd-decoded content to shell (possible command obfuscation)": "arbitrary-exec",
    "pipe tr-transformed output to shell (possible command obfuscation)": "arbitrary-exec",
    "pipe openssl-decoded content to shell (possible command obfuscation)": "arbitrary-exec",
    "script execution via heredoc": "arbitrary-exec",
    "shell execution via heredoc": "arbitrary-exec",
    "chmod +x followed by immediate execution": "arbitrary-exec",
    "PowerShell encoded command execution": "arbitrary-exec",
    # --- system-config (6) ---
    "overwrite system config": "system-config",
    "overwrite system file via tee": "system-config",
    "overwrite system file via redirection": "system-config",
    "copy/move file into system config path": "system-config",
    "in-place edit of system config": "system-config",
    "in-place edit of system config (long flag)": "system-config",
    # --- hermes-self (10) ---
    "stop/restart hermes gateway (kills running agents)": "hermes-self",
    "hermes update (restarts gateway, kills running agents)": "hermes-self",
    "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')": "hermes-self",
    "kill hermes/gateway process (self-termination)": "hermes-self",
    "kill process via pgrep/pidof expansion (self-termination)": "hermes-self",
    "kill process via backtick pgrep/pidof expansion (self-termination)": "hermes-self",
    "stop/restart hermes launchd service (kills running agents)": "hermes-self",
    "in-place edit of Hermes config/env": "hermes-self",
    "in-place edit of Hermes config/env (long flag)": "hermes-self",
    "in-place edit of Hermes config/env (perl/ruby)": "hermes-self",
    # --- credential-write (4) ---
    "copy/move file into sensitive credential/SSH/shell-rc path": "credential-write",
    "in-place edit of sensitive credential/SSH/shell-rc path": "credential-write",
    "in-place edit of sensitive credential/SSH/shell-rc path (long flag)": "credential-write",
    "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)": "credential-write",
    # --- disk-device (3) ---
    "format filesystem": "disk-device",
    "disk copy": "disk-device",
    "write to block device": "disk-device",
    # --- privilege-escalation (2) ---
    "sudo with privilege flag (stdin/askpass/shell/list)": "privilege-escalation",
    "sudo with combined-flag privilege escalation": "privilege-escalation",
    # --- unplaceable DANGEROUS key -> unknown (fail-safe, never auto) ---
    "fork bomb": "unknown",
    # --- event literals (non-terminal sources) ---
    "execute_code": "execute-code",
    "mcp_elicitation": "mcp-consent",
}

# Cheap import-time consistency guard: every mapped class must exist.
for _pk, _cid in PATTERN_CLASS.items():
    assert _cid in CLASS_META, "PATTERN_CLASS maps %r to unknown class %r" % (_pk, _cid)

# Precompute per-class pattern counts for the panel.
PATTERN_COUNT = {cid: 0 for cid in CLASS_META}
for _cid in PATTERN_CLASS.values():
    PATTERN_COUNT[_cid] = PATTERN_COUNT.get(_cid, 0) + 1

# --------------------------------------------------------------------------
# Command scanner (E03, 2026-11-09 audit).
#
# Upstream reports only the FIRST DANGEROUS_PATTERN a command matches, so a
# compound command — `pkill -9 node; cp k ~/.ssh/authorized_keys` — arrived as
# one process-control key and an Auto on that class approved the credential
# write behind it.  decide() therefore re-scans the command text itself against
# every pattern below and resolves most-restrictive over ALL of them.
#
# _DANGER_PATTERNS is a COPY of upstream tools/approval.py DANGEROUS_PATTERNS
# (Hermes 0.18.0, 19d4174454; comments dropped, helper names prefixed _DP),
# not an import: this is a stdlib-only sibling module and serve's code is not
# importable from the dashboard's Python.  Its descriptions are exactly the
# terminal keys of PATTERN_CLASS (t_e03_compound_auto.py checks that); when
# upstream adds or edits a pattern, update PATTERN_CLASS and this table
# together.  The key serve reports is always resolved too and any compound
# command is held to Ask regardless, but a pattern that is missing or older
# here IS a gap for one simple command that matches both an Auto pattern and
# the changed one: re-compare the table after every Hermes upgrade.
# --------------------------------------------------------------------------
_DP_SSH_SENSITIVE_PATH = r'(?:~|\$home|\$\{home\})/\.ssh(?:/|$)'
_DP_HERMES_ENV_PATH = (
    r'(?:~\/\.hermes/|'
    r'(?:\$home|\$\{home\})/\.hermes/|'
    r'(?:\$hermes_home|\$\{hermes_home\})/)'
    r'\.env\b'
)
_DP_HERMES_CONFIG_PATH = (
    r'(?:~\/\.hermes/|'
    r'(?:\$home|\$\{home\})/\.hermes/|'
    r'(?:\$hermes_home|\$\{hermes_home\})/)'
    r'config\.yaml\b'
)
_DP_PROJECT_ENV_PATH = r'(?:(?:/|\.{1,2}/)?(?:[^\s/"\'`]+/)*\.env(?:\.[^/\s"\'`]+)*)'
_DP_PROJECT_CONFIG_PATH = r'(?:(?:/|\.{1,2}/)?(?:[^\s/"\'`]+/)*config\.yaml)'
_DP_SHELL_RC_FILES = (
    r'(?:~|\$home|\$\{home\})/\.'
    r'(?:bashrc|zshrc|profile|bash_profile|zprofile)\b'
)
_DP_CREDENTIAL_FILES = (
    r'(?:~|\$home|\$\{home\})/\.'
    r'(?:netrc|pgpass|npmrc|pypirc)\b'
)
_DP_MACOS_PRIVATE_SYSTEM_PATH = r'/private/(?:etc|var|tmp|home)/'
_DP_SYSTEM_CONFIG_PATH = (
    rf'(?:/etc/|{_DP_MACOS_PRIVATE_SYSTEM_PATH})'
)
_DP_SENSITIVE_WRITE_TARGET = (
    rf'(?:{_DP_SYSTEM_CONFIG_PATH}|/dev/sd|'
    rf'{_DP_SSH_SENSITIVE_PATH}|'
    rf'{_DP_HERMES_ENV_PATH}|'
    rf'{_DP_HERMES_CONFIG_PATH}|'
    rf'{_DP_SHELL_RC_FILES}|'
    rf'{_DP_CREDENTIAL_FILES})'
)
_DP_USER_SENSITIVE_WRITE_TARGET = (
    rf'(?:{_DP_SSH_SENSITIVE_PATH}|'
    rf'{_DP_SHELL_RC_FILES}|'
    rf'{_DP_CREDENTIAL_FILES})'
)
_DP_PROJECT_SENSITIVE_WRITE_TARGET = rf'(?:{_DP_PROJECT_ENV_PATH}|{_DP_PROJECT_CONFIG_PATH})'
_DP_COMMAND_TAIL = r'(?:\s*(?:&&|\|\||;).*)?$'
_DP_WRITE_TARGET_BOUNDARY = r'(?=[\s;&|<>"\']|$)'
_DANGER_PATTERNS = [
    (r'\brm\s+(-[^\s]*\s+)*/', "delete in root path"),
    (r'\brm\s+-[^\s]*r', "recursive delete"),
    (r'\brm\s+--recursive\b', "recursive delete (long flag)"),
    (r'\bcmd(?:\.exe)?\s+/(?:c|k)\s+.*\b(?:del|erase|rd|rmdir)\b', "Windows cmd destructive delete"),
    (r'\b(?:powershell|pwsh)(?:\.exe)?\b(?:\s+-\S+)*\s+(?:-(?:command|c)\s+)?["\']?(?:remove-item|rmdir|erase|del|rd|ri|rm)\b', "Windows PowerShell destructive delete"),
    (r'\b(?:powershell|pwsh)(?:\.exe)?\b.*\s-(?:encodedcommand|enc|e)\b', "PowerShell encoded command execution"),
    (r'\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b', "world/other-writable permissions"),
    (r'\bchmod\s+--recursive\b.*(777|666|o\+[rwx]*w|a\+[rwx]*w)', "recursive world/other-writable (long flag)"),
    (r'\bchown\s+(-[^\s]*)?R\s+root', "recursive chown to root"),
    (r'\bchown\s+--recur[a-z]*\b.*root', "recursive chown to root (long flag)"),
    (r'\bmkfs\b', "format filesystem"),
    (r'\bdd\s+.*if=', "disk copy"),
    (r'>\s*/dev/sd', "write to block device"),
    (r'\bDROP\s+(TABLE|DATABASE)\b', "SQL DROP"),
    (r'\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)', "SQL DELETE without WHERE"),
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),
    (rf'>\s*{_DP_SYSTEM_CONFIG_PATH}', "overwrite system config"),
    (r'\bsystemctl\s+(-[^\s]+\s+)*(stop|restart|disable|mask)\b', "stop/restart system service"),
    (r'\bkill\s+-9\s+-1\b', "kill all processes"),
    (r'\bpkill\s+-9\b', "force kill processes"),
    (r'\bkillall\s+(-[^\s]*\s+)*-(9|KILL|SIGKILL)\b', "force kill processes (killall -KILL)"),
    (r'\bkillall\s+(-[^\s]*\s+)*-s\s+(KILL|SIGKILL|9)\b', "force kill processes (killall -s KILL)"),
    (r'\bkillall\s+(-[^\s]*\s+)*-r\b', "kill processes by regex (killall -r)"),
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb"),
    (r'\b(bash|sh|zsh|ksh)\s+-[^\s]*c(\s+|$)', "shell command via -c/-lc flag"),
    (r'\b(python[23]?|perl|ruby|node)\s+-[ec]\s+', "script execution via -e/-c flag"),
    (r'\b(curl|wget)\b.*\|\s*(?:[/\w]*/)?(?:ba)?sh(?:\s|$|-c)', "pipe remote content to shell"),
    (r'\b(bash|sh|zsh|ksh)\s+<\s*<?\s*\(\s*(curl|wget)\b', "execute remote script via process substitution"),
    (r'(?:\beval\b|\bsource\b|\.)\s*(?:\$\(\s*|`\s*)(?:curl|wget)\b', "execute remote content via command substitution"),
    (r'\b(base64|base32|base16)\s+(?:-[dD]|--decode)\b.*\|\s*\b(bash|sh|zsh|ksh|dash)\b',
     "pipe decoded content to shell (possible command obfuscation)"),
    (r'\bxxd\s+-r\b.*\|\s*\b(bash|sh|zsh|ksh|dash)\b',
     "pipe xxd-decoded content to shell (possible command obfuscation)"),
    (r'\becho\b[^|]*\|\s*\btr\b[^|]*\|\s*\b(bash|sh|zsh|ksh|dash)\b',
     "pipe tr-transformed output to shell (possible command obfuscation)"),
    (r'\bopenssl\b.*\b(?:base64|enc)\b[^|]*\s+-[dD]\b[^|]*\|\s*\b(bash|sh|zsh|ksh|dash)\b',
     "pipe openssl-decoded content to shell (possible command obfuscation)"),
    (rf'\btee\b.*["\']?{_DP_SENSITIVE_WRITE_TARGET}', "overwrite system file via tee"),
    (rf'>>?\s*["\']?{_DP_SENSITIVE_WRITE_TARGET}', "overwrite system file via redirection"),
    (rf'\btee\b.*["\']?{_DP_PROJECT_SENSITIVE_WRITE_TARGET}["\']?{_DP_WRITE_TARGET_BOUNDARY}', "overwrite project env/config via tee"),
    (rf'>>?\s*["\']?{_DP_PROJECT_SENSITIVE_WRITE_TARGET}["\']?{_DP_WRITE_TARGET_BOUNDARY}', "overwrite project env/config via redirection"),
    (r'\bxargs\s+.*\brm\b', "xargs with rm"),
    (r'\bfind\b.*-exec(?:dir)?\s+(/\S*/)?rm\b', "find -exec/-execdir rm"),
    (r'\bfind\b.*-delete\b', "find -delete"),
    (r'\bhermes\s+(?:-{1,2}\S+(?:\s+\S+)?\s+)*gateway\s+(stop|restart)\b', "stop/restart hermes gateway (kills running agents)"),
    (r'\bhermes\s+update\b', "hermes update (restarts gateway, kills running agents)"),
    (r'\bdocker\s+compose\s+(restart|stop|kill|down)\b', "docker compose restart/stop/kill/down (container lifecycle)"),
    (r'\bdocker\s+(restart|stop|kill)\b', "docker restart/stop/kill (container lifecycle)"),
    (r'gateway\s+run\b.*(&\s*$|&\s*;|\bdisown\b|\bsetsid\b)', "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')"),
    (r'\bnohup\b.*gateway\s+run\b', "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')"),
    (r'\b(pkill|killall)\b.*\b(hermes|gateway|cli\.py)\b', "kill hermes/gateway process (self-termination)"),
    (r'\bkill\b.*\$\(\s*(pgrep|pidof)\b', "kill process via pgrep/pidof expansion (self-termination)"),
    (r'\bkill\b.*`\s*(pgrep|pidof)\b', "kill process via backtick pgrep/pidof expansion (self-termination)"),
    (r'\blaunchctl\s+(stop|kickstart|bootout|unload|kill|disable|remove)\b.*\b(hermes|ai\.hermes)\b', "stop/restart hermes launchd service (kills running agents)"),
    (rf'\b(cp|mv|install)\b.*\s{_DP_SYSTEM_CONFIG_PATH}', "copy/move file into system config path"),
    (rf'\b(cp|mv|install)\b.*\s["\']?{_DP_PROJECT_SENSITIVE_WRITE_TARGET}["\']?{_DP_COMMAND_TAIL}', "overwrite project env/config file"),
    (rf'\b(cp|mv|install)\b.*\s["\']?{_DP_SENSITIVE_WRITE_TARGET}[^\s"\']*["\']?{_DP_COMMAND_TAIL}', "copy/move file into sensitive credential/SSH/shell-rc path"),
    (rf'\bsed\s+-[^\s]*i.*(?:{_DP_USER_SENSITIVE_WRITE_TARGET})[^\s"\']*', "in-place edit of sensitive credential/SSH/shell-rc path"),
    (rf'\bsed\s+--in-place\b.*(?:{_DP_USER_SENSITIVE_WRITE_TARGET})[^\s"\']*', "in-place edit of sensitive credential/SSH/shell-rc path (long flag)"),
    (rf'\b(?:perl|ruby)\b.*(?:^|\s)-[^\s]*i\b.*(?:{_DP_USER_SENSITIVE_WRITE_TARGET})[^\s"\']*', "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)"),
    (rf'\bsed\s+-[^\s]*i.*\s{_DP_SYSTEM_CONFIG_PATH}', "in-place edit of system config"),
    (rf'\bsed\s+--in-place\b.*\s{_DP_SYSTEM_CONFIG_PATH}', "in-place edit of system config (long flag)"),
    (rf'\bsed\s+-[^\s]*i.*(?:{_DP_HERMES_CONFIG_PATH}|{_DP_HERMES_ENV_PATH})', "in-place edit of Hermes config/env"),
    (rf'\bsed\s+--in-place\b.*(?:{_DP_HERMES_CONFIG_PATH}|{_DP_HERMES_ENV_PATH})', "in-place edit of Hermes config/env (long flag)"),
    (rf'\b(?:perl|ruby)\b.*(?:^|\s)-[^\s]*i\b.*(?:{_DP_HERMES_CONFIG_PATH}|{_DP_HERMES_ENV_PATH})', "in-place edit of Hermes config/env (perl/ruby)"),
    (r'\b(python[23]?|perl|ruby|node)\s+<<', "script execution via heredoc"),
    (r'\b(bash|sh|zsh|ksh)\s+<<', "shell execution via heredoc"),
    (r'\bgit\s+reset\s+--h(?:a(?:r(?:d)?)?)?\b', "git reset --hard (destroys uncommitted changes)"),
    (r'\bgit\s+push\b.*--forc[a-z]*\b', "git force push (rewrites remote history)"),
    (r'\bgit\s+push\b.*-f\b', "git force push short flag (rewrites remote history)"),
    (r'\bgit\s+clean\s+-[^\s]*f', "git clean with force (deletes untracked files)"),
    (r'\bgit\s+branch\s+-D\b', "git branch force delete"),
    (r'\bgit\s+branch\b[^;|&\n]*?(?:-d\b|--delete\b)[^;|&\n]*?(?:-f\b|--force\b)', "git branch force delete (long flags)"),
    (r'\bgit\s+branch\b[^;|&\n]*?(?:-f\b|--force\b)[^;|&\n]*?(?:-d\b|--delete\b)', "git branch force delete (long flags, force-first)"),
    (r'\bchmod\s+\+x\b.*[;&|]+\s*\./', "chmod +x followed by immediate execution"),
    (r'\bsudo\b[^;|&\n]*?\s+(?:-s\b|--st[a-z]*\b|-a\b|--a[a-z]*\b)',
     "sudo with privilege flag (stdin/askpass/shell/list)"),
    (r'\bsudo\b[^;|&\n]*?\s+-[a-z]*[sa][a-z]*\b',
     "sudo with combined-flag privilege escalation"),
]

_DP_RE_FLAGS = re.IGNORECASE | re.DOTALL
# Two of upstream's fragments are AMBIGUOUS: a run of `.x` after `.env`, or of
# `--flag` after `hermes`, can be split in exponentially many ways, so a
# 90-character command (`hermes --a --a … x`, `> .env.a.a.a…/`) kept the regex
# engine busy for minutes to hours — with the GIL held, i.e. the whole
# dashboard frozen — and decide() is reachable from GET /api/permissions/test.
# Each is compiled as an equivalent UNAMBIGUOUS form (the same language:
# t_e03_scanner_evasion.py compares them exhaustively), so the table above
# stays a verbatim copy of upstream's.  What is left is polynomial, and
# SCAN_MAX bounds it.  A new `(…)*` / `(…)+` group in the table must be audited
# the same way (the same test lists the audited ones).
_DP_LINEAR = (
    # `.` is itself in the class, so (\.C+)* == (\.C+)?
    (r'(?:\.[^/\s"\'`]+)*', r'(?:\.[^/\s"\'`]+)?'),
    # flag tokens, each optionally followed by one value token: a value that
    # is itself a flag is read as the next flag (the only reading kept)
    (r'(?:-{1,2}\S+(?:\s+\S+)?\s+)*', r'(?:-\S+\s+(?:(?:[^-\s]\S*|-)\s+)?)*'),
)


def _dp_linear(rx):
    for _amb, _lin in _DP_LINEAR:
        rx = rx.replace(_amb, _lin)
    return rx


_DANGER_COMPILED = [(re.compile(_dp_linear(_rx), _DP_RE_FLAGS), _desc)
                    for _rx, _desc in _DANGER_PATTERNS]

# Shell control operators and substitutions.  Any of them means the text can
# run more than one command, which a single-pattern verdict has not judged, so
# AUTO becomes ASK.  Deliberately NOT quote-aware (an operator inside quotes
# also asks) — when unsure, ask.  `&` covers `&&` and `2>&1`, `|` covers `||`,
# `<<` every here-doc / here-string.
_SHELL_OP_RE = re.compile(r"[;&|`\r\n]|\$\(|<\(|>\(|<<")
# Serve REDACTS the command before it sends it (agent/redact.py: "***",
# "head...tail", "[REDACTED PRIVATE KEY]", "«redacted:…»"), and a redacted span
# can hide an operator and a second command (a PEM-shaped span swallows
# everything up to its END line).  A command carrying any of these marks was
# not fully seen, so AUTO becomes ASK.
_REDACT_MARK_RE = re.compile(r"\*\*\*|\.\.\.|\u2026|\u00abredacted|\[REDACTED", re.I)
# Longest command the scanner judges; anything longer is not judged, so AUTO
# becomes ASK.  Bounds the regex work on the approval path: several of
# upstream's patterns are cubic (`perl -i perl -i …`, `tee tee … a/a/…`), and
# the regex engine holds the GIL, so the whole dashboard waits.  Measured worst
# cases on an M5 Max: 512 chars 0.07 s, 1 KB 0.45 s, 2 KB 3 s, 8 KB 40 s.
SCAN_MAX = 512
_DP_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
                         r"|\x1b[@-Z\\-_]")
# Upstream's home-prefix fold (tools/approval.py _home_prefix_fold_regex):
# the home's components joined by ANY run of separators, with any leading run,
# then the rest of the path token — `//Users/me/.ssh/x` and `/Users//me/.ssh/x`
# are the same file to the shell and fold to `~/.ssh/x` like `/Users/me/.ssh/x`.
_DP_PATH_TAIL = r"""(?P<tail>(?:[/\\][^/\\\s'"`;|&<>()]*)+)"""
# any user's home, not only this one: more matches can only make it stricter
_DP_HOME_RE = re.compile(r"""[/\\]+(?:Users|home)[/\\]+[^/\\\s'"`;|&<>()]+"""
                         + _DP_PATH_TAIL, re.I)
_DP_FOLD_RX = {}


def _dp_fold_rx(path):
    rx = _DP_FOLD_RX.get(path)
    if rx is None:
        comps = [c for c in re.split(r"[/\\]+", path) if c]
        if len(comps) < 2:
            rx = False                 # degenerate (/, "", /home) — never fold
        else:
            rx = re.compile(r"[/\\]*" + r"[/\\]+".join(re.escape(c) for c in comps)
                            + _DP_PATH_TAIL)
        if len(_DP_FOLD_RX) < 64:
            _DP_FOLD_RX[path] = rx
    return rx


def _dp_fold_tail(to):
    return lambda m: to + m.group("tail").replace("\\", "/")


def _dp_fold(cmd, paths, to):
    seen = []
    for path in paths:
        if not path or not isinstance(path, str):
            continue
        for cand in (path, os.path.realpath(path)):
            if cand not in seen:
                seen.append(cand)
    for path in sorted(seen, key=len, reverse=True):
        rx = _dp_fold_rx(path)
        if rx:
            cmd = rx.sub(_dp_fold_tail(to), cmd)
    return cmd


def _dp_normalize(cmd, any_home=False):
    """A stdlib port of upstream _normalize_command_for_detection: ANSI and
    NUL stripped, NFKC, line continuations joined, this user's home prefixes
    folded to ~/, backslash escapes and empty quotes removed, $IFS expanded
    to a space.  `any_home` also folds /Users/<name>/ and /home/<name>/ for
    any name — a SECOND variant for the scanner, never a replacement: a fold
    rewrites the text, so it can remove a match as well as add one."""
    cmd = _DP_ANSI_RE.sub("", cmd).replace("\x00", "")
    cmd = unicodedata.normalize("NFKC", cmd)
    cmd = re.sub(r"\\\r?\n", "", cmd)
    cmd = _dp_fold(cmd, (os.environ.get("HERMES_HOME"), HERMES_DIR), "~/.hermes")
    cmd = _dp_fold(cmd, (HOME, os.environ.get("HOME")), "~")
    if any_home:
        cmd = _DP_HOME_RE.sub(_dp_fold_tail("~"), cmd)
    cmd = re.sub(r"\\([^\n])", r"\1", cmd)
    cmd = re.sub(r"''|\"\"", "", cmd)
    cmd = re.sub(r"\$\{IFS\b[^}]*\}|\$IFS\b", " ", cmd, flags=re.I)
    return cmd


# Upstream matches its patterns over more than the normalized text: it marks a
# new command start at every `(` and `{`, strips full ECMA-48 control
# sequences, and DE-OBFUSCATES each word it takes for a command name — "rm",
# r'm', ${x:-rm} (tools/approval.py _command_detection_variants).  This scanner
# does none of that, so a command written that way has not been fully judged
# and AUTO becomes ASK:  `"cp" k ~/.ssh/authorized_keys "pkill -9"` reached
# serve's detector as a force-kill and matched no credential pattern here.
_DP_OPAQUE_RE = re.compile(r"[(){}\x00-\x08\x0b-\x1f\x7f-\x9f]")
_DP_WORD_SYNTAX_RE = re.compile(r"""['"`$\\]""")
_DP_WRAPPERS = frozenset(("sudo", "env", "exec", "nohup", "setsid", "time",
                          "command", "builtin"))
_DP_OPTS_WITH_ARG = frozenset(("-c", "--close-from", "-g", "--group", "-h", "--host",
                               "-p", "--prompt", "-u", "--user"))
_DP_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


def _dp_word_obscured(norm):
    """True when a word upstream reads as a command name — the first word, and
    each word after sudo / env / exec / nohup / setsid / time / command /
    builtin, their options and VAR=value prefixes (upstream
    _iter_shell_command_word_spans, at most 12 words) — carries a quote, a
    backslash or an expansion."""
    n = 0
    skip_opts = skip_arg = False
    for word in norm.split():
        if n >= 12:
            break
        if _DP_WORD_SYNTAX_RE.search(word):
            return True
        n += 1
        low = word.lower()
        if skip_arg:
            skip_arg = False
            continue
        if skip_opts and low.startswith("-"):
            skip_arg = low in _DP_OPTS_WITH_ARG
            continue
        if low in _DP_WRAPPERS:
            skip_opts = low in ("sudo", "env")
            continue
        if _DP_ASSIGN_RE.fullmatch(word):
            skip_opts = False
            continue
        break
    return False


def _scan_command(cmd):
    """Every _DANGER_PATTERNS key the command matches, in table order, over
    the raw text and its normalized forms (a union: never fewer keys)."""
    variants = [cmd.lower()]
    for any_home in (False, True):
        norm = _dp_normalize(cmd, any_home).lower()
        if norm not in variants:
            variants.append(norm)
    out = []
    for rx, desc in _DANGER_COMPILED:
        if desc not in out and any(rx.search(v) for v in variants):
            out.append(desc)
    return out

# --------------------------------------------------------------------------
# module state
# --------------------------------------------------------------------------
_lock = threading.RLock()          # re-entrant: _load/_save may nest under it
_STATE = {"mtime": None, "policy": {}, "trusted": True, "error": None, "good": {},
          "sha": ""}       # sha: sha256 of the bytes `policy` came from (E115)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _shipped_tier(cid):
    """The fail-safe fallback tier used when a class is not explicitly set.

    Everything falls to ASK except the three classes the spec defaults to
    NEVER (credential-write / disk-device / privilege-escalation)."""
    meta = CLASS_META.get(cid)
    if meta and meta.get("default") == "never":
        return "never"
    return SHIPPED_DEFAULT


def _floor(cid):
    meta = CLASS_META.get(cid)
    return meta.get("floor", "ask") if meta else "ask"


def _auto_allowed(cid):
    return _floor(cid) != "ask"


def _heuristic(key):
    """Class for a key absent from PATTERN_CLASS (future upstream patterns).
    Biased toward the most restrictive plausible class; else `unknown`."""
    k = key.lower()
    if "credential" in k or "ssh" in k or "shell-rc" in k:
        return "credential-write"
    if "hermes" in k or "gateway" in k:
        return "hermes-self"
    if "sudo" in k:
        return "privilege-escalation"
    if "delete" in k or "rm " in k:
        return "destructive-delete"
    if "block device" in k or "filesystem" in k:
        return "disk-device"
    return "unknown"


def _class_of(key):
    if key.startswith("shortcuts-run:"):   # dashboard action bus (aux_shortcuts)
        return "shortcuts-run"
    # the GitHub change pipeline (aux_ghchanges): BEFORE the heuristic, or a
    # repository named hermes-… or …delete… would be read as hermes-self or
    # destructive-delete
    if key.startswith("github-write:"):
        return "github-write"
    return PATTERN_CLASS.get(key) or _heuristic(key)


def _ensure_dirs():
    for d in (HERMES_DIR, DASH_DIR):
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
        except OSError:
            pass


def _atomic_write(path, raw, mode=0o600):
    """temp + fsync + os.replace, stdlib-only (no tempfile dependency)."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), hashlib.sha256(raw).hexdigest()[:8])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# policy load / save / integrity
# --------------------------------------------------------------------------
def _read_sidecar():
    try:
        with open(SIDECAR) as f:
            return f.read().strip()
    except OSError:
        return ""


def _normalize(policy):
    """Return a clean, serializable policy (valid class ids + valid tiers only)."""
    classes = {}
    src = policy.get("classes") if isinstance(policy, dict) else None
    if isinstance(src, dict):
        for cid, tier in src.items():
            if cid in CLASS_META and tier in TIERS:
                classes[cid] = tier
    patterns = {}
    psrc = policy.get("patterns") if isinstance(policy, dict) else None
    if isinstance(psrc, dict):
        for k, tier in psrc.items():
            if isinstance(k, str) and k and tier in TIERS:
                patterns[k] = tier
    return {"version": 1, "updated": round(time.time(), 3),
            "updated_by": "dashboard", "classes": classes, "patterns": patterns}


def _load():
    """Return (policy, trusted, error).  Never raises.  Re-reads the file only
    when its mtime changes; caches the parse behind the module lock."""
    with _lock:
        try:
            st = os.stat(PERM_FILE)
        except OSError:
            # File absent: safe defaults, and there is nothing to distrust.
            _STATE.update(mtime=None, policy={}, trusted=True, error=None, sha="")
            return ({}, True, None)
        mtime = st.st_mtime
        if mtime == _STATE["mtime"]:
            return (_STATE["policy"], _STATE["trusted"], _STATE["error"])
        try:
            with open(PERM_FILE, "rb") as f:
                raw = f.read()
        except OSError as e:
            policy = _STATE.get("good") or {}
            _STATE.update(mtime=mtime, policy=policy, trusted=False,
                          error="unreadable: " + str(e), sha=None)
            return (policy, False, _STATE["error"])
        sha = hashlib.sha256(raw).hexdigest()
        trusted = bool(_read_sidecar()) and (sha == _read_sidecar())
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("policy is not a JSON object")
        except Exception as e:
            # Keep the last-known-good policy so user-set NEVER entries survive
            # a malformed write; mark untrusted so every AUTO clamps to ASK.
            policy = _STATE.get("good") or {}
            _STATE.update(mtime=mtime, policy=policy, trusted=False,
                          error="malformed: " + str(e), sha=sha)
            return (policy, False, _STATE["error"])
        _STATE.update(mtime=mtime, policy=parsed, trusted=trusted,
                      error=None, good=parsed, sha=sha)
        if trusted:
            _ensure_signed_copy(raw, sha)
        return (parsed, trusted, None)


def _write_signed(raw):
    """Sign `raw` (bytes of the policy file): sidecar hash + signed copy."""
    _atomic_write(SIDECAR, (hashlib.sha256(raw).hexdigest() + "\n").encode(), 0o600)
    try:
        _atomic_write(SIGNED_COPY, raw, 0o600)
    except OSError:
        pass   # the copy only feeds the re-trust review; the hash is the trust


def _ensure_signed_copy(raw, sha):
    """A policy signed before SIGNED_COPY existed has no copy to diff against;
    the first trusted load writes one, so the FIRST out-of-band edit after an
    upgrade is already reviewable as a diff.  Best-effort, never raises."""
    try:
        with open(SIGNED_COPY, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == sha:
                return
    except OSError:
        pass
    try:
        _ensure_dirs()
        _atomic_write(SIGNED_COPY, raw, 0o600)
    except Exception:
        pass


def _signed_view():
    """The last signed policy as {classes: {cid: tier}, patterns: {key: tier}}
    — class tiers resolved like the live rows (shipped default when unset) —
    or None when there is no copy, or it is not what the sidecar signed."""
    try:
        with open(SIGNED_COPY, "rb") as f:
            raw = f.read()
        side = _read_sidecar()
        if not side or hashlib.sha256(raw).hexdigest() != side:
            return None
        pol = json.loads(raw.decode("utf-8"))
        if not isinstance(pol, dict):
            return None
    except Exception:
        return None
    pats = pol.get("patterns")
    pats = pats if isinstance(pats, dict) else {}
    return {"classes": {cid: _class_tier(pol, cid) for cid in CLASS_ORDER},
            "patterns": {k: v for k, v in pats.items()
                         if isinstance(k, str) and k and v in TIERS}}


def _save(policy):
    """Atomic write of the normalized policy + sidecar rewrite + cache bust."""
    with _lock:
        _ensure_dirs()
        norm = _normalize(policy)
        raw = (json.dumps(norm, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(PERM_FILE, raw, 0o600)
        _write_signed(raw)
        _STATE["mtime"] = None        # force a fresh read on next _load()
        return norm


# --------------------------------------------------------------------------
# resolution engine — decide(payload)
# --------------------------------------------------------------------------
def _pattern_tier(policy, key):
    pats = policy.get("patterns") if isinstance(policy, dict) else None
    if isinstance(pats, dict):
        v = pats.get(key)
        if v in TIERS:
            return v
    return None


def _class_tier(policy, cid):
    classes = policy.get("classes") if isinstance(policy, dict) else None
    if isinstance(classes, dict):
        v = classes.get(cid)
        if v in TIERS:
            return v
    return _shipped_tier(cid)


REASON_KEY_MAX = 96      # the longest upstream description is 77 characters


def _reason_key(key):
    """A pattern key as decide()'s `reason` shows it: one line, printable,
    at most REASON_KEY_MAX characters. A key is not always the dashboard's
    own word — the approval-gate plugin builds `computer_use:<action>` from
    the AGENT's tool call — and the approval cards print the reason as the
    Trust policy's voice, so the key part is bounded and the policy's own
    words ("… is set to Ask") always follow it. The verdict's `pattern_key`
    stays the full key."""
    s = " ".join(str(key).split())
    s = "".join(ch if ch.isprintable() else "\ufffd" for ch in s)
    if len(s) > REASON_KEY_MAX:
        s = s[:REASON_KEY_MAX - 1] + "\u2026"
    return s


def decide(payload):
    """Deterministic, total, never-raising resolution of one approval.request.

    Returns {tier, class, classes, pattern_key, reason, clamped}.
    tier is one of auto|ask|never; the enforcement seam sends "once" for auto,
    "deny" for never, and surfaces the card for ask."""
    try:
        if not isinstance(payload, dict):
            payload = {}
        keys = payload.get("pattern_keys")
        if not isinstance(keys, list):
            keys = None
        if keys is None:
            pk = payload.get("pattern_key")
            keys = [pk] if pk else []
        keys = [k for k in keys if isinstance(k, str) and k]

        policy, trusted, _err = _load()

        if not keys:
            return {"tier": "ask", "class": "unknown", "classes": ["unknown"],
                    "pattern_key": "", "clamped": False,
                    "reason": "no pattern key → unknown is set to Ask"}

        # E03: serve sends only the FIRST pattern the command matched, so the
        # command is judged here against every pattern this module knows and
        # each extra match joins the most-restrictive resolution below.  What
        # cannot be judged — no command text, a text too long to scan, more
        # than one command — may still be Ask or Never, never Auto (`unjudged`
        # is applied after the resolution).
        cmd = payload.get("command")
        cmd = cmd if isinstance(cmd, str) else ""
        unjudged = ""
        if not cmd.strip():
            unjudged = "no command text to check — asking"
        elif len(cmd) > SCAN_MAX:
            unjudged = "command too long to check — asking"
        else:
            for k in _scan_command(cmd):
                if k not in keys:
                    keys.append(k)
            norm = _dp_normalize(cmd)
            if _SHELL_OP_RE.search(cmd) or _SHELL_OP_RE.search(norm):
                unjudged = "more than one command — asking"
            elif _REDACT_MARK_RE.search(cmd):
                unjudged = "part of the command was redacted — asking"
            elif (_DP_OPAQUE_RE.search(cmd) or _DP_OPAQUE_RE.search(norm)
                  or _dp_word_obscured(norm)):
                unjudged = "it is written in a way that cannot be fully checked — asking"

        resolved = []   # (rank, tier, cls, key, floor_clamped)
        for key in keys:
            cls = _class_of(key)
            raw = _pattern_tier(policy, key)
            if raw is None:
                raw = _class_tier(policy, cls)
            fc = False
            if raw == "auto" and _floor(cls) == "ask":
                raw = "ask"
                fc = True
            resolved.append((_RANK[raw], raw, cls, key, fc))

        # most-restrictive wins; ties resolve to first occurrence
        best = max(resolved, key=lambda r: r[0])
        _rank, tier, cls, key, floor_clamped = best

        trust_clamped = False
        if not trusted and tier == "auto":
            tier = "ask"
            trust_clamped = True

        scope_clamped = False
        if unjudged and tier == "auto":
            tier = "ask"
            scope_clamped = True

        classes = []
        for r in resolved:
            if r[2] not in classes:
                classes.append(r[2])

        shown = _reason_key(key)
        reason = "%s → %s is set to %s" % (shown, cls, tier.capitalize())
        if trust_clamped:
            reason += " — auto suspended (policy changed outside the dashboard)"
        elif scope_clamped:
            reason = "%s → %s is set to Auto, but %s" % (shown, cls, unjudged)
        elif floor_clamped:
            reason += " (safety floor)"

        return {"tier": tier, "class": cls, "classes": classes,
                "pattern_key": key,
                "clamped": bool(floor_clamped or trust_clamped or scope_clamped),
                "reason": reason}
    except Exception as e:
        # Absolute fail-safe: anything unexpected still asks, never auto.
        return {"tier": "ask", "class": "unknown", "classes": ["unknown"],
                "pattern_key": "", "clamped": True,
                "reason": "policy error → asking (%s)" % (type(e).__name__,)}


# --------------------------------------------------------------------------
# audit log — append-only JSONL, 1 MB single-generation rotation
# --------------------------------------------------------------------------
# The audit log keeps the command text of every approval, and an approval is
# exactly where a command carrying a credential shows up (an `export
# OPENAI_API_KEY=…`, a curl with a Bearer header).  So every command is scrubbed
# before it is stored (R2-15), with the same scrubber the chat export and the
# MCP server use — COPIED VERBATIM from aux_convos.py, not imported: this is a
# real sibling module that hermes_rpc imports, and aux_convos is exec'd into
# server.py's globals.  This is a third copy of the block (its own header still
# says "twice"); tools/tests/unit/t_r2_15_audit_log.py compares these bytes
# with aux_convos.py's, so change all three or none.  The log runs its value
# rules through _audit_scrub() below, which leaves out the ~/.hermes path rule.

# ===== BEGIN MIRRORED SECRET BLOCK — KEEP BYTE-IDENTICAL (1.1.5, B21) =====
# Everything from this line to the END marker is the WHOLE scrubber, and it
# exists twice: dashboard/aux_convos.py (the export; aux_trace.py, doctor.py
# and aux_memlayer.py resolve it from there by name) and dashboard/hermes_mcp.py
# (every MCP result).  Copied, not imported — the MCP server must not pull
# server.py's globals into its process.  Change one, change the other; the
# unit tier compares the bytes between the two markers.
#
# LABELED secrets: a key that names a credential, `:` or `=`, then the value.
# B21 (2026-10-08 audit) widened it four ways, each for a reproduced leak:
#   * a JSON-quoted key, `"api_key": "sk-…"` — the closing quote sat between
#     the key and the colon, so nothing matched;
#   * a name that only ENDS in a credential word — OPENAI_API_KEY=,
#     GITHUB_TOKEN=, client_secret:, authToken:, aws_secret_access_key = .
#     The keyword is deliberately NOT anchored at a word start (an unanchored
#     fixed keyword also keeps this linear; a lazy identifier prefix in front
#     would be quadratic on a long dotted run), while the \b AFTER it is what
#     keeps max_tokens=4096 and prompt_tokens: 19127 out;
#   * `Authorization: Basic …` and `Token …`, not only Bearer;
#   * a value with punctuation in it — `hunter2!x` used to leak its tail.
# The value must sit on the same line ([ \t], not \s) and cannot start with
# `[`, so an already-redacted value is never matched twice.  Group 1 (key and
# separator) is kept, so JSON stays JSON: `"password": "[redacted]"` — and so
# does JSON quoted inside a string (`{\"api_key\": \"sk-…\"}`, the shape an
# echoed tool call has): a quote may carry one backslash, and the value stops
# at a backslash so the closing `\"` survives.
_CV_SECRET_RE = re.compile(
    r"((?:serve_sid|serve_key|session_token|token|api[_-]?key|apikey|"
    r"secret|password|passwd|passphrase|passcode|authorization)"
    r"(?:[_.-]?(?:access|key|secret|token|value|base)){0,3}\b"
    r"(?:\\?[\"'])?[ \t]*[:=][ \t]*(?:\\?[\"'])?)"
    r"(?:(?:Bearer|Basic|Token)[ \t]+)?"
    r"([^\s\"'`,;(){}\[\]<>\\]{6,})", re.I)
_CV_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9_\-.=]{8,}", re.I)
# PROSE: "my password is hunter22", "the API key was sk-abc123".  Narrower on
# purpose — the value must carry a digit, or a symbol with more of the word
# after it, so "the token is expired", "the secret is out" and "the API key
# was invalid!" survive.  A passphrase of plain words is missed; a redactor that
# ate ordinary sentences would get switched off.
_CV_PROSE_SECRET_RE = re.compile(
    r"(\b(?:password|passwd|passphrase|passcode|api[ _-]?key|secret[ _-]?key|"
    r"(?:access|auth|bot|api)[ _-]?token|token|secret)"
    r"[ \t]+(?:is|was)[ \t]*:?[ \t]+[\"'`]?)"
    r"(?=[^\s\"'`,;(){}\[\]<>]*"
    r"(?:[0-9]|[!@#$%^*~][^\s\"'`,;(){}\[\]<>]))"
    r"([^\s\"'`,;(){}\[\]<>]{6,})", re.I)
_CV_HERMES_PATH_RE = re.compile(
    r"(?:~|/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+)/\.hermes"
    r"(?:/[^\s`'\"),;]*)?")

# Raw, UNLABELED secrets.  The key=value rule above only fires when a label
# ("token:", "api_key=") precedes the value and the Bearer rule only when the
# scheme does — but an agent that echoes a bare `sk-ant-...`, a PEM block or a
# Telegram bot token leaks exactly as hard with no label anywhere in sight.
# Ordered most-specific-first where prefixes overlap (sk-ant- before sk-) so
# the narrower shape wins the match, and PEM first because its body is base64
# that a later pattern could nibble at.  Every entry is anchored on a fixed
# vendor prefix (or a digits-colon shape) plus a minimum length, so ordinary
# prose and a 40-char git sha — hex only, no prefix, no colon — cannot match.
# That restraint is the point: a redactor that eats normal text gets switched
# off, and a switched-off redactor protects nothing.
_CV_RAW_SECRET_RES = (
    # PEM private key — multiline; non-greedy so two keys are two matches
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
               r"-----END [A-Z ]*PRIVATE KEY-----"),
    # JWT: three base64url segments, the first being the `{"alg"...` header
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),                  # Anthropic
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),                      # OpenAI-style
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),   # GitHub PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),               # GH fine-grained
    re.compile(r"\bAKIA[0-9A-Z]{16}"),                           # AWS key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}"),               # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                     # Google API key
    # E82 (2026-11-09 audit): the shapes this app itself handles that used to
    # pass unlabeled — Google OAuth access/refresh tokens and client secret,
    # Hugging Face, Stripe (secret + restricted, live + test), npm, GitLab.
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),                   # Google access
    re.compile(r"\b1//0[A-Za-z0-9_-]{20,}"),                      # Google refresh
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}"),                   # Google client
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),                         # Hugging Face
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),        # Stripe
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),                        # npm
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),                    # GitLab PAT
    # a password in a web address, scheme://user:password@host — only the
    # user:password part goes, so "postgres://[redacted]@db:5432" still says
    # which database.  The lookbehind pins every match to just after "://",
    # which keeps it linear; a "user@host" with no password is left alone.
    re.compile(r"(?<=://)[^\s/:@\[\]]+:[^\s/@]+(?=@)"),
    # Telegram bot token, <8-10 digit bot id>:<35 chars>.  Deliberately NO \b
    # in front: the shape this Mac actually leaks is the send URL
    # ".../bot<id>:<secret>/sendMessage", where "bot" runs straight into the
    # digits and a word boundary would never match.
    re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}"),
)


def _cv_redact_raw(text):
    """Replace secrets that arrive with no label in front of them.

    Runs LAST, after the labeled and Bearer rules: those already collapsed the
    values they own, so whatever still matches here genuinely had no label.
    """
    for _rx in _CV_RAW_SECRET_RES:
        text = _rx.sub("[redacted]", text)
    return text


def _cv_redact(text):
    """Path, key=value, Bearer, prose, then the unlabeled raw shapes."""
    text = _CV_HERMES_PATH_RE.sub("[redacted path]", text)
    # key=value FIRST: its value pattern swallows a "Bearer <tok>" whole, so
    # "Authorization: Bearer hx_..." collapses to one "[redacted]" instead of
    # the standalone rule leaving a bare "Bearer" for this one to redact again
    text = _CV_SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    text = _CV_BEARER_RE.sub("Bearer [redacted]", text)
    text = _CV_PROSE_SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    return _cv_redact_raw(text)          # unlabeled shapes last (1.1.5)
# ===== END MIRRORED SECRET BLOCK ==========================================


def _audit_scrub(text):
    """_cv_redact minus its first rule, the ~/.hermes PATH rule.

    That rule exists for an export that leaves the Mac; here it would turn
    every hermes-self approval ("sed -i ... ~/.hermes/config.yaml") into an
    unreadable "[redacted path]", and a path is not a secret.  Every VALUE
    rule runs, in _cv_redact's own order: key=value, Bearer, prose, raw."""
    text = _CV_SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    text = _CV_BEARER_RE.sub("Bearer [redacted]", text)
    text = _CV_PROSE_SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    return _cv_redact_raw(text)


_LOG_OLD_FIXED = [False]     # the .1 generation is re-chmodded once per process


def _log_append(entry):
    """Append one JSON line, mode 0600 (R2-15).

    The log was opened with open(..., "a"), i.e. created at the default umask
    (0644) although it holds command text.  It is now created 0600, an existing
    live file is tightened on every append (fchmod, one syscall) and the .1
    generation once per process and after each rotation — os.replace keeps the
    mode of the file it moves, so a file rotated before this fix stays 0644
    until something chmods it."""
    try:
        _ensure_dirs()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        rotated = False
        try:
            if os.path.getsize(LOG_FILE) > LOG_MAX:
                os.replace(LOG_FILE, LOG_FILE + ".1")
                rotated = True
        except OSError:
            pass
        if rotated or not _LOG_OLD_FIXED[0]:
            _LOG_OLD_FIXED[0] = True
            try:
                os.chmod(LOG_FILE + ".1", 0o600)
            except OSError:
                pass
        fd = os.open(LOG_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            f.write(line)
    except Exception:
        pass   # audit must never break a turn


def audit(job, payload, verdict, action, choice=""):
    """Append one audit entry.  Best-effort; swallows all errors.

    action in auto-approved | auto-denied | asked | user-approve | user-deny
    (the policy and the owner's answers) | expired (serve had already dropped
    the approval) | timeout (unanswered past its TTL, denied) | desync-denied
    (denied because the queue fell out of step with serve) | respond-failed
    (the answer could not be delivered) | stopped-denied (an Approve the
    owner gave was not sent because they stopped the turn first; denied)
    — the strings hermes_rpc.py writes;
    aux_trust.js ACT_BADGE names each one, aux_metrics.py counts them. Older
    logs may also carry policy-error. `_log_policy_change` writes
    policy-change. `job` may be the chat-job dict (has id/session) or a string."""
    try:
        if isinstance(job, dict):
            jid = job.get("id") or job.get("job") or ""
            sess = job.get("session") or job.get("serve_sid") or job.get("sid") or ""
        elif isinstance(job, str):
            jid, sess = job, ""
        else:
            jid, sess = "", ""
        verdict = verdict if isinstance(verdict, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        pk = payload.get("pattern_key") or ""
        if not pk:
            ks = payload.get("pattern_keys")
            if isinstance(ks, list) and ks and isinstance(ks[0], str):
                pk = ks[0]
        # scrub BEFORE the cut: a secret straddling CMD_TRUNC would otherwise
        # be shortened below the scrubber's minimum length and kept (R2-15).
        # Only a bounded window is scrubbed, though: this runs on the approval
        # path, the command can be a whole script, and the PEM rule is
        # quadratic in BEGIN lines with no END (192 KB of them took ~3 s).
        command = str(payload.get("command") or "")[:CMD_TRUNC + CMD_SCRUB_SLACK]
        command = _audit_scrub(command)[:CMD_TRUNC]
        entry = {"ts": round(time.time(), 3), "job": jid, "session": sess,
                 "pattern_key": pk, "class": verdict.get("class", ""),
                 "tier": verdict.get("tier", ""), "action": action,
                 "command": command}
        if choice:
            entry["choice"] = choice
        _log_append(entry)
    except Exception:
        pass


def _log_policy_change(op, detail):
    entry = {"ts": round(time.time(), 3), "job": "", "session": "",
             "pattern_key": detail.get("pattern", ""),
             "class": detail.get("class", ""), "tier": detail.get("tier", ""),
             "action": "policy-change", "op": op}
    _log_append(entry)


def _tail_entries(limit):
    """Parse up to `limit` most recent audit lines; return oldest-first list."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except ValueError:
            continue
    return out


def _recent_stats(entries):
    """Per-class {auto-approved, auto-denied, asked} counts within the window."""
    cutoff = time.time() - RECENT_WINDOW
    stats = {}
    for e in entries:
        try:
            if float(e.get("ts", 0)) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        cls = e.get("class")
        act = e.get("action")
        if cls not in CLASS_META or act not in ("auto-approved", "auto-denied", "asked"):
            continue
        d = stats.setdefault(cls, {"auto-approved": 0, "auto-denied": 0, "asked": 0})
        d[act] += 1
    return stats


# --------------------------------------------------------------------------
# HTTP-facing surface (called by aux_permissions.py handlers)
# --------------------------------------------------------------------------
def permissions_payload():
    """GET /api/permissions — 19 classes ordered critical->low + recent audit."""
    with _lock:
        policy, trusted, error = _load()
        sha = _STATE.get("sha")        # the bytes this payload was built from (E115)
    entries = _tail_entries(500)
    stats = _recent_stats(entries)
    classes = []
    for cid in CLASS_ORDER:
        meta = CLASS_META[cid]
        classes.append({
            "id": cid, "label": meta["label"], "risk": meta["risk"],
            "tier": _class_tier(policy, cid),
            "default": _shipped_tier(cid), "floor": meta["floor"],
            "auto_allowed": _auto_allowed(cid), "desc": meta["desc"],
            "pattern_count": PATTERN_COUNT.get(cid, 0),
            "recent": stats.get(cid, {"auto-approved": 0, "auto-denied": 0, "asked": 0}),
        })
    patterns = policy.get("patterns") if isinstance(policy, dict) else {}
    if not isinstance(patterns, dict):
        patterns = {}
    recent = list(reversed(entries[-20:]))
    out = {"ok": True, "trusted": bool(trusted), "path": PERM_FILE,
           "exists": os.path.exists(PERM_FILE), "classes": classes,
           "patterns": patterns, "pattern_overrides": _pattern_overrides(policy, patterns, trusted),
           "recent": recent,
           # E115: Review & re-trust (and any save over an untrusted policy)
           # sends this back; the write is refused if the file changed since
           "sha": sha if isinstance(sha, str) else None}
    if error:
        out["policy_error"] = error
    if not trusted:
        signed = _signed_view()
        if signed is not None:
            out["signed"] = signed    # aux_trust.js diffs the review against it
    return out


def _pattern_overrides(policy, patterns, trusted):
    """Display rows for every pattern-level tier (C126) — read-only.

    decide() resolves a pattern's own tier BEFORE its class tier, so an entry
    like {"git force push (rewrites remote history)": "auto"} auto-approves a
    force-push while the class row still reads Ask. The panel used to render
    `patterns` nowhere; these rows say what each entry is, which class it
    overrides and what decide() will actually do with it (`effective`, after
    the safety floor and the untrusted-policy clamp, the same two clamps
    decide() applies). Autos sort first."""
    rows = []
    for key, tier in patterns.items():
        if not isinstance(key, str) or not key or tier not in TIERS:
            continue                    # decide() ignores these too
        cid = _class_of(key.strip())
        eff, why, bad = tier, "", ""
        if key != key.strip():
            # A hand-edited key with stray spaces: no approval ever carries
            # that key, so decide() never matches it and its class tier is
            # what actually runs.  Shown (so Clear can remove it), never
            # counted as an override (C126).
            eff, bad = _class_tier(policy, cid), "whitespace"
            why = "never matches: the key has spaces at its start or end"
        elif tier == "auto" and _floor(cid) == "ask":
            eff, why = "ask", "safety floor"
        elif tier == "auto" and not trusted:
            eff, why = "ask", "auto suspended until the policy is re-trusted"
        row = {"pattern": key, "tier": tier, "class": cid,
               "class_label": CLASS_META.get(cid, {}).get("label", cid),
               "class_tier": _class_tier(policy, cid), "effective": eff, "clamped_by": why,
               "known": key in PATTERN_CLASS}
        if bad:
            row["invalid"] = bad
        rows.append(row)
    order = {c: i for i, c in enumerate(CLASS_ORDER)}
    rows.sort(key=lambda r: (_RANK[r["tier"]], order.get(r["class"], 99), r["pattern"]))
    return rows


def permissions_log(n=50):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 50
    n = max(1, min(500, n))
    entries = _tail_entries(n)
    return {"ok": True, "entries": list(reversed(entries))}


def permissions_test(pattern_key, command=""):
    """GET /api/permissions/test — dry-run decide(); never mutates state."""
    payload = {"pattern_key": pattern_key or "", "command": command or ""}
    return {"ok": True, "verdict": decide(payload)}


def _floor_error(cid, tier):
    return {"ok": False,
            "error": "'%s' has a safety floor of '%s' and cannot be set to '%s'"
                     % (cid, _floor(cid), tier),
            "_status": 403}


def _file_sha():
    """sha256 of permissions.json now; "" when absent, None when unreadable."""
    try:
        with open(PERM_FILE, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""
    except OSError:
        return None


def _stale_error(body):
    """E115: the request must carry the sha of the file the owner reviewed
    (GET /api/permissions `sha`) and it must still be the file on disk.
    Returns None when it is, else the error dict.  Fail closed: no sha, a
    non-string, or an unreadable file is refused."""
    given = body.get("sha") if isinstance(body, dict) else None
    if not isinstance(given, str):
        return {"ok": False, "error": "sha of the reviewed policy is required",
                "_status": 400}
    cur = _file_sha()
    if cur is None or given != cur:
        return {"ok": False, "stale": True, "_status": 409,
                "error": "the policy file changed since you reviewed it — review it again"}
    return None


def permissions_set(body):
    """POST /api/permissions — set_class/set_pattern/clear_pattern/reset/retrust.

    Returns a dict.  On error it carries `_status` (400 unknown, 403 floor); the
    route registrar pops it and uses it as the HTTP status.  Never raises."""
    try:
        if not isinstance(body, dict):
            return {"ok": False, "error": "body must be a JSON object", "_status": 400}
        op = body.get("op")

        with _lock:
            if op == "set_class":
                cid = body.get("class")
                tier = body.get("tier")
                if cid not in CLASS_META:
                    return {"ok": False, "error": "unknown class %r" % (cid,), "_status": 400}
                if tier not in TIERS:
                    return {"ok": False, "error": "unknown tier %r" % (tier,), "_status": 400}
                if tier == "auto" and _floor(cid) == "ask":
                    return _floor_error(cid, tier)
                policy, _t, _e = _load()
                if not _t:                      # this save re-signs the file
                    err = _stale_error(body)
                    if err:
                        return err
                classes = dict(policy.get("classes") or {})
                classes[cid] = tier
                policy = dict(policy)
                policy["classes"] = classes
                _save(policy)
                _log_policy_change("set_class", {"class": cid, "tier": tier})
                return {"ok": True, "classes": permissions_payload()["classes"]}

            if op == "set_pattern":
                pat = body.get("pattern")
                tier = body.get("tier")
                if not isinstance(pat, str) or not pat.strip():
                    return {"ok": False, "error": "empty pattern", "_status": 400}
                pat = pat.strip()
                if tier not in TIERS:
                    return {"ok": False, "error": "unknown tier %r" % (tier,), "_status": 400}
                cid = _class_of(pat)
                if tier == "auto" and _floor(cid) == "ask":
                    return _floor_error(cid, tier)
                policy, _t, _e = _load()
                if not _t:                      # this save re-signs the file
                    err = _stale_error(body)
                    if err:
                        return err
                pats = dict(policy.get("patterns") or {})
                pats[pat] = tier
                policy = dict(policy)
                policy["patterns"] = pats
                _save(policy)
                _log_policy_change("set_pattern", {"pattern": pat, "class": cid, "tier": tier})
                return {"ok": True, "patterns": _save_view()}

            if op == "clear_pattern":
                pat = body.get("pattern")
                if not isinstance(pat, str) or not pat:
                    return {"ok": False, "error": "empty pattern", "_status": 400}
                policy, _t, _e = _load()
                if not _t:                      # this save re-signs the file
                    err = _stale_error(body)
                    if err:
                        return err
                pats = dict(policy.get("patterns") or {})
                # The EXACT stored key first: the panel sends the key as
                # stored, and a hand-edited " key " used to be stripped here,
                # pop nothing and save anyway (C126).  A stripped match is
                # kept for API callers that send a padded copy of a clean key.
                # A key of spaces only is listed (and offered a Clear) too, so
                # it is only "empty" when no such key is stored.
                if pat not in pats:
                    pat = pat.strip()
                    if not pat:
                        return {"ok": False, "error": "empty pattern", "_status": 400}
                if pat not in pats:
                    return {"ok": False, "error": "no such pattern override",
                            "_status": 404}
                pats.pop(pat, None)
                policy = dict(policy)
                policy["patterns"] = pats
                _save(policy)
                _log_policy_change("clear_pattern", {"pattern": pat})
                return {"ok": True, "patterns": _save_view()}

            if op == "reset":
                _save({"classes": {}, "patterns": {}})
                _log_policy_change("reset", {})
                return {"ok": True, "classes": permissions_payload()["classes"]}

            if op == "retrust":
                # E115: sign only the bytes the owner reviewed — the request
                # carries their sha (GET `sha`); a file that changed since is
                # refused (409) and nothing is signed.
                given = body.get("sha")
                if not isinstance(given, str):
                    return {"ok": False, "error": "sha of the reviewed policy is required",
                            "_status": 400}
                try:
                    with open(PERM_FILE, "rb") as f:
                        raw = f.read()
                except FileNotFoundError:
                    raw = None    # no policy file yet -> nothing to trust; defaults are safe
                except OSError as e:
                    return {"ok": False, "error": "policy unreadable: %s" % e, "_status": 500}
                cur = "" if raw is None else hashlib.sha256(raw).hexdigest()
                if given != cur:
                    return {"ok": False, "stale": True, "_status": 409,
                            "error": "the policy file changed since you reviewed it — review it again"}
                if raw is not None:
                    _ensure_dirs()
                    _write_signed(raw)
                    _STATE["mtime"] = None
                _log_policy_change("retrust", {})
                return {"ok": True, "trusted": True}

            return {"ok": False, "error": "unknown op", "_status": 400}
    except Exception as e:
        return {"ok": False, "error": "internal: " + str(e), "_status": 500}


# --------------------------------------------------------------------------
# Config-as-Code import (E112) — aux_config.py's restore goes through these.
#
# A restore used to REPLACE the policy and SIGN it in one step: the plan said
# only "policy differs", every Never the owner set after the export vanished,
# and the imported Auto entries were live at once — the review that an
# out-of-band edit gets (sidecar mismatch -> untrusted -> Review & re-trust)
# never ran.  Now an import MERGES (the snapshot's entries win for the keys it
# names; the owner's other entries are kept, so a Never is only ever changed by
# an entry the plan lists) and is written UNSIGNED, so the policy lands
# untrusted and every Auto stays suspended until the owner re-trusts it in the
# Trust panel, against a diff of the last signed policy.  An Auto the safety
# floor forbids is not merged at all (the API would refuse it too).
# --------------------------------------------------------------------------
def _eff_tier(policy, key):
    t = _pattern_tier(policy, key)
    return t if t is not None else _class_tier(policy, _class_of(key))


def _import_merge(live, imported):
    """(merged policy {classes, patterns}, ignored [str]) — pure."""
    live = live if isinstance(live, dict) else {}
    imported = imported if isinstance(imported, dict) else {}
    lc = live.get("classes") if isinstance(live.get("classes"), dict) else {}
    lp = live.get("patterns") if isinstance(live.get("patterns"), dict) else {}
    classes = {k: v for k, v in lc.items() if k in CLASS_META and v in TIERS}
    patterns = {k: v for k, v in lp.items() if isinstance(k, str) and k and v in TIERS}
    ignored = []
    ic = imported.get("classes") if isinstance(imported.get("classes"), dict) else {}
    for cid, tier in ic.items():
        if cid not in CLASS_META or tier not in TIERS:
            continue
        if tier == "auto" and _floor(cid) == "ask":
            ignored.append("%s: Auto is below its safety floor" % cid)
            continue
        classes[cid] = tier
    ip = imported.get("patterns") if isinstance(imported.get("patterns"), dict) else {}
    for key, tier in ip.items():
        if not isinstance(key, str) or not key.strip() or tier not in TIERS:
            continue
        key = key.strip()
        if tier == "auto" and _floor(_class_of(key)) == "ask":
            ignored.append("%s: Auto is below its safety floor" % key)
            continue
        patterns[key] = tier
    return {"classes": classes, "patterns": patterns}, ignored


def _import_rows(live, merged):
    """One row per class / pattern whose tier the import would change, with
    the EFFECTIVE tier before and after; `loosened` when it gets less strict."""
    rows = []
    for cid in CLASS_ORDER:
        f, t = _class_tier(live, cid), _class_tier(merged, cid)
        if f != t:
            rows.append({"kind": "class", "id": cid,
                         "label": CLASS_META[cid]["label"], "from": f, "to": t,
                         "loosened": _RANK[t] < _RANK[f]})
    lp = live.get("patterns") if isinstance(live.get("patterns"), dict) else {}
    mp = merged.get("patterns") or {}
    for key in sorted(set(lp) | set(mp)):
        if not isinstance(key, str) or lp.get(key) == mp.get(key):
            continue
        f, t = _eff_tier(live, key), _eff_tier(merged, key)
        rows.append({"kind": "pattern", "id": key, "label": key,
                     "class": _class_of(key.strip()), "from": f, "to": t,
                     "loosened": _RANK[t] < _RANK[f]})
    rows.sort(key=lambda r: (not r["loosened"],))       # loosened first, stable
    return rows


def import_plan(imported):
    """Dry run of import_policy(): {changed, rows, ignored, loosened}."""
    policy, _t, _e = _load()
    live = policy if isinstance(policy, dict) else {}
    merged, ignored = _import_merge(live, imported)
    rows = _import_rows(live, merged)
    changed = (merged["classes"] != {k: v for k, v in (live.get("classes") or {}).items()
                                     if k in CLASS_META and v in TIERS}
               or merged["patterns"] != {k: v for k, v in (live.get("patterns") or {}).items()
                                         if isinstance(k, str) and k and v in TIERS})
    return {"changed": bool(changed), "rows": rows, "ignored": ignored,
            "loosened": sum(1 for r in rows if r["loosened"])}


def import_policy(imported):
    """Merge `imported` into the live policy and write it UNSIGNED (it lands
    untrusted: Auto suspended until Review & re-trust).  Nothing is written
    when the merge changes nothing.  Returns import_plan()'s dict + trusted."""
    with _lock:
        plan = import_plan(imported)
        if not plan["changed"]:
            _p, trusted, _e = _load()
            plan["trusted"] = bool(trusted)
            return plan
        policy, _t, _e = _load()
        merged, _ign = _import_merge(policy if isinstance(policy, dict) else {}, imported)
        _ensure_dirs()
        norm = _normalize(merged)
        norm["updated_by"] = "config-import"
        raw = (json.dumps(norm, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(PERM_FILE, raw, 0o600)    # deliberately NOT _write_signed
        _STATE["mtime"] = None
        _log_policy_change("import", {})
        plan["trusted"] = False
        return plan


def _save_view():
    policy, _t, _e = _load()
    pats = policy.get("patterns") if isinstance(policy, dict) else {}
    return pats if isinstance(pats, dict) else {}
