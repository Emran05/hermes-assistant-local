#!/usr/bin/env python3
"""plugin_enable.py — add a plugin to `plugins.enabled` in a Hermes config.

ONE implementation, three callers: `install.sh`, `update.sh` and the dashboard
(`dashboard/aux_toolbudget.py` imports `apply_enable`/`read_enabled`/`enable`
from this file by path).  Writing the YAML edit twice — once in shell, once in
Python — is exactly how the two drift, so neither script has its own copy.

Why not PyYAML: the dashboard is stdlib-only by policy and a round-trip load
+ dump would reformat the owner's whole config (comments lost, keys reordered,
lists reflowed).  This is a line editor.  It touches the `plugins:` block and
nothing else, it is byte-identical when the entry is already there, and it
backs the file up before every write that actually changes something.

    python3 hermes-plugins/plugin_enable.py tool-budget
    python3 hermes-plugins/plugin_enable.py tool-budget --config ~/.hermes/config.yaml
    python3 hermes-plugins/plugin_enable.py tool-budget --dry-run
    python3 hermes-plugins/plugin_enable.py tool-budget --check   # exit 0 if enabled

Exit codes: 0 done or already done (or, with --check, enabled), 1 not enabled
(--check only), 2 an error — including a config.yaml in a form this editor
refuses to touch (E16/E17: a quoted, duplicated or flow-style `plugins` key, a
tab, an anchor, a second document...), when NOTHING is written.

SAFE EDITING (E16/E17).  `enable()` writes only what `edit_enable()` returns:
the file is pre-checked (`precheck`), edited, re-read with a copy of
doctor.py's YAML-subset reader and verified (`verify_edit`) before the backup
and the atomic replace.  dashboard/aux_promptbudget.py and
dashboard/aux_context.py load this file by path for the same guard.

CONCURRENCY.  Three callers write the same file and two of them can run at the
same moment (the dashboard card while `update.sh` runs, say), so every
read->apply->replace goes through `config_lock()`: an `fcntl.flock(LOCK_EX)` on
`<config>.lock` beside the config.  **The lock PATH is the contract, not this
code** — `dashboard/aux_promptbudget.py` takes the same flock on the same path
from its own module so the two editors serialise against each other without
importing one another.  The temp file is created `O_CREAT|O_EXCL` with the pid
and a random token in its name: the old fixed `.tmp` name meant two writers
either clobbered each other's half-written file or raced `os.replace` into a
`FileNotFoundError`.

BLOCK SCOPING.  A sub-key is matched at the block's OWN child indent, captured
from its first child line, so an `enabled:` nested one level deeper (a plugin's
own options, say) is not mistaken for `plugins.enabled`.  Blank lines and
comments never end a block — a `# note` in column 0 used to, after which a
second `enabled:` key could be written above the real one.
"""

import argparse
import binascii
import contextlib
import datetime
import fcntl
import os
import re
import shutil
import sys
import time

DEFAULT_CONFIG = os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml")

# How long a writer waits for the other one before giving up. A config edit is
# a few milliseconds of work; 20 s means "something is wedged", not "busy".
LOCK_TIMEOUT = 20.0


# ---------------------------------------------------------------------------
# the shared write lock
# ---------------------------------------------------------------------------
def lock_path(config_path):
    """The advisory lock file every writer of `config_path` takes."""
    return config_path + ".lock"


@contextlib.contextmanager
def config_lock(config_path, timeout=LOCK_TIMEOUT):
    """`flock(LOCK_EX)` on `<config>.lock`, released on the way out.

    An advisory lock on a SEPARATE file, never on the config itself: the config
    is replaced by `os.replace`, so a lock held on its inode would stop
    guarding the path the moment the first writer finished.
    """
    path = lock_path(config_path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        "another process is still writing %s (waited %gs)"
                        % (config_path, timeout))
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def atomic_write(path, text, mode=0o600):
    """Replace `path` with `text` through a uniquely named temp file in the
    same directory. O_EXCL + pid + random token, so two writers can never share
    a temp name; the temp file is 0600 from birth and takes `mode` before the
    replace."""
    directory = os.path.dirname(path) or "."
    token = binascii.hexlify(os.urandom(4)).decode("ascii")
    tmp = os.path.join(directory, "%s.tmp-%d-%s"
                       % (os.path.basename(path), os.getpid(), token))
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# block scoping — shared by the reader and the writer
# ---------------------------------------------------------------------------
def _skippable(line):
    """Blank or a comment: never a block boundary, never a key."""
    s = line.strip()
    return (not s) or s.startswith("#")


def _indent_of(line):
    return len(line) - len(line.lstrip())


def _block_end(lines, start):
    """Index one past the last line of the block opened at `start`."""
    for i in range(start + 1, len(lines)):
        if _skippable(lines[i]):
            continue
        if _indent_of(lines[i]) == 0:
            return i
    return len(lines)


def _child_indent(lines, start, end):
    """The indent of the block's OWN children, from its first child line.
    None when the block has none."""
    for i in range(start + 1, end):
        if _skippable(lines[i]):
            continue
        return _indent_of(lines[i])
    return None


def _find_block(lines, key):
    """The `key:` header line.  A trailing comment and trailing spaces are
    accepted (E17: `plugins:  # mine` used to read as "no plugins block", so a
    second one was appended); any other decoration is precheck()'s to refuse."""
    for i, line in enumerate(lines):
        if re.match(r"^%s:[ \t]*(#.*)?$" % re.escape(key), line.rstrip("\r\n")):
            return i
    return None


# ---------------------------------------------------------------------------
# a read-only YAML SUBSET reader — a COPY of dashboard/doctor.py's (F03)
#
# DRIFT NOTE: everything from the `_HC_KEY_RE` regex to the end of
# `_hcfg_parse` is byte-identical to doctor.py and
# tools/tests/unit/t_e16_config_editors.py fails when the two differ — change
# both or neither.  Copied, not imported: install.sh and update.sh run this
# file standalone, so it must stay self-contained.  What it understands is
# what Hermes writes (block maps, block sequences at the key's own indent or
# deeper, scalars resolved the PyYAML way, one-level flow); anchors, aliases,
# tags and complex keys are reported as problems, never guessed at.
# ---------------------------------------------------------------------------
_HC_KEY_RE = re.compile(
    r"""^(?P<key>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'"""
    r"""|(?:[^\s\-?:,\[\]{}#&*!|>'"%@`]|[-?:](?=\S))(?:[^:]|:(?=\S))*?)"""
    r"""\s*:(?:[ \t]+(?P<rest>.*))?$""")
_HC_NULL = ("", "~", "null", "Null", "NULL")
_HC_BOOL = {"true": True, "yes": True, "on": True,
            "false": False, "no": False, "off": False}
# PyYAML's own YAML 1.1 implicit resolvers (resolver.py), so `0755` is 493 and
# `1e5` stays a string exactly as Hermes will read them.
_HC_INT_RE = re.compile(r"^[-+]?(?:0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|0|[1-9][0-9_]*"
                        r"|[1-9][0-9_]*(?::[0-5]?[0-9])+)$")
_HC_FLOAT_RE = re.compile(r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?"
                          r"|\.[0-9_]+(?:[eE][-+][0-9]+)?"
                          r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*"
                          r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_HC_DQ_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\",
              "/": "/", " ": " ", "e": "\x1b", "b": "\b", "a": "\a", "v": "\v",
              "f": "\f", "\t": "\t", "N": "\x85", "_": "\xa0", "L": "\u2028",
              "P": "\u2029"}
_HC_DQ_HEX = {"x": 2, "u": 4, "U": 8}          # PyYAML writes \x01, \u2028, \U0001F600


def _hc_quote_end(s):
    """Index of the quote closing the quoted scalar that starts `s`, or None."""
    q, i = s[0], 1
    while i < len(s):
        c = s[i]
        if q == '"' and c == "\\":
            i += 2
            continue
        if c == q:
            if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                i += 2                      # '' is an escaped quote
                continue
            return i
        i += 1
    return None


def _hc_unquote(s):
    if s[0] == "'":
        return s[1:-1].replace("''", "'")
    out, i, body = [], 0, s[1:-1]
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            e, n = body[i + 1], _HC_DQ_HEX.get(body[i + 1], 0)
            h = body[i + 2:i + 2 + n]
            if n and len(h) == n and all(x in "0123456789abcdefABCDEF" for x in h) \
                    and int(h, 16) <= 0x10FFFF:
                out.append(chr(int(h, 16)))
                i += 2 + n
                continue
            out.append(_HC_DQ_ESC.get(e, e))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _hc_key(k):
    """A mapping key the way PyYAML builds it: a quoted key verbatim, a plain
    one resolved like any plain scalar (`8080:` is an int, `on:` is True)."""
    if k[0] in "\"'":
        return _hc_unquote(k)
    return _hc_plain(k.strip())


def _hc_split_flow(inner):
    """Split the inside of a one-level flow collection on top-level commas."""
    parts, cur, q, i = [], [], None, 0
    while i < len(inner):
        c = inner[i]
        if q:
            cur.append(c)
            if c == q:
                q = None
        elif c in "\"'":
            q = c
            cur.append(c)
        elif c == ",":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _hc_plain(s):
    """A plain scalar resolved the way PyYAML (YAML 1.1) resolves it."""
    if s in _HC_NULL:
        return None
    low = s.lower()
    if low in _HC_BOOL and s in (low, low.capitalize(), low.upper()):
        return _HC_BOOL[low]
    if _HC_INT_RE.match(s) or _HC_FLOAT_RE.match(s):
        try:
            return _hc_number(s)
        except ValueError:
            return s
    return s


def _hc_number(s):
    """PyYAML's construct_yaml_int / construct_yaml_float for a matched scalar."""
    v = s.replace("_", "")
    sign = -1 if v[:1] == "-" else 1
    v = v.lstrip("+-")
    low = v.lower()
    if low in (".inf", ".nan"):
        return sign * float("inf") if low == ".inf" else float("nan")
    if ":" in v:                                   # base 60
        whole, _, frac = v.rpartition(":")
        n = 0
        for part in whole.split(":") + [frac.split(".")[0]]:
            n = n * 60 + int(part)
        if "." in frac:
            return sign * (n + float("0." + frac.split(".", 1)[1] or "0"))
        return sign * n
    if "." in v or "e" in low:
        return sign * float(v)
    if low.startswith("0b"):
        return sign * int(v[2:], 2)
    if low.startswith("0x"):
        return sign * int(v[2:], 16)
    if len(v) > 1 and v[0] == "0":
        return sign * int(v, 8)
    return sign * int(v)


class _HcParser:
    def __init__(self, raw):
        self.raw = raw
        self.toks = []                       # [lineno, indent, body]
        self.problems = []                   # [(lineno, why)]
        self.i = 0
        self.skip_to = 0                     # lines <= this were eaten by a block scalar
        for n, line in enumerate(raw, 1):
            s = line.rstrip()
            body = s.lstrip(" ")
            if not body or body.startswith("#"):
                continue
            if body.startswith("\t"):
                self.problems.append((n, "indented with a TAB"))
                continue
            ind = len(s) - len(body)
            if ind == 0 and (body in ("---", "...") or body.startswith("%")):
                continue
            self.toks.append([n, ind, body])

    # -- token stream ---------------------------------------------------------
    def peek(self):
        while self.i < len(self.toks) and self.toks[self.i][0] <= self.skip_to:
            self.i += 1
        return self.toks[self.i] if self.i < len(self.toks) else None

    def bad(self, lineno, why):
        self.problems.append((lineno, why))

    def drop_deeper(self, ind):
        """Skip every token nested under a line we could not read."""
        while True:
            t = self.peek()
            if t is None or t[1] <= ind:
                return
            self.i += 1

    @staticmethod
    def is_item(body):
        return body == "-" or body.startswith("- ")

    # -- grammar --------------------------------------------------------------
    def document(self):
        tree = {}
        while True:
            t = self.peek()
            if t is None:
                return tree
            if self.is_item(t[2]):
                self.bad(t[0], "a top-level list, not a mapping")
                self.i += 1
                self.drop_deeper(t[1])
                continue
            got = self.mapping(t[1])
            tree.update(got)
            t2 = self.peek()
            if t2 is not None and t2 is t:   # nothing consumed: never spin
                self.bad(t[0], "could not be read")
                self.i += 1

    def block(self, ind):
        t = self.peek()
        if t is not None and self.is_item(t[2]):
            return self.sequence(ind)
        return self.mapping(ind)

    def mapping(self, ind, first=None):
        d = {}
        if first is not None:
            self.pair(d, first[0], ind, first[1])
        while True:
            t = self.peek()
            if t is None or t[1] < ind:
                return d
            if t[1] > ind:
                self.bad(t[0], "unexpected indentation")
                self.i += 1
                self.drop_deeper(t[1] - 1)
                continue
            if self.is_item(t[2]):
                return d                    # a list item here belongs to the caller
            self.i += 1
            self.pair(d, t[0], ind, t[2])

    def pair(self, d, lineno, ind, body):
        m = _HC_KEY_RE.match(body)
        if not m:
            self.bad(lineno, "not a 'key: value' line")
            self.drop_deeper(ind)
            return
        d[_hc_key(m.group("key"))] = self.value(lineno, ind, m.group("rest") or "", seq_ok=True)

    def sequence(self, ind):
        out = []
        while True:
            t = self.peek()
            if t is None or t[1] < ind:
                return out
            if t[1] > ind:
                self.bad(t[0], "unexpected indentation")
                self.i += 1
                self.drop_deeper(t[1] - 1)
                continue
            if not self.is_item(t[2]):
                return out
            self.i += 1
            rest = t[2][1:]
            item = rest.lstrip(" ")
            cind = ind + 1 + (len(rest) - len(item))
            if not item or item.startswith("#"):
                nxt = self.peek()
                out.append(self.block(nxt[1]) if (nxt is not None and nxt[1] > ind) else None)
            elif self.is_item(item):
                self.bad(t[0], "a nested '- -' list is not supported")
                self.drop_deeper(ind)
                out.append(None)
            elif _HC_KEY_RE.match(item):
                out.append(self.mapping(cind, first=(t[0], item)))
            else:
                out.append(self.value(t[0], ind, item, seq_ok=False))

    def value(self, lineno, ind, rest, seq_ok):
        r = rest.strip()
        if r[:1] in ("&", "*", "!"):
            self.bad(lineno, "an anchor, alias or tag (not supported)")
            self.drop_deeper(ind)
            return None
        bs = re.match(r"^([|>])([-+0-9]*)\s*(#.*)?$", r)
        if bs:
            return self.block_scalar(lineno, ind, bs.group(1), bs.group(2))
        if not r or r.startswith("#"):
            t = self.peek()
            if t is not None and t[1] > ind:
                return self.block(t[1])
            if seq_ok and t is not None and t[1] == ind and self.is_item(t[2]):
                return self.sequence(ind)
            return None
        if r[:1] in "\"'" and _hc_quote_end(r) is None:
            return self.quoted_lines(lineno, ind, r)
        parts, last = [r], lineno
        while True:                          # a multi-line scalar's continuation
            t = self.peek()
            if t is None or t[1] <= ind:
                break
            gap = self.raw[last:t[0] - 1]    # the raw lines between the two
            if any(x.strip() for x in gap):  # a comment ends a plain scalar
                break
            parts.append(("\n" * len(gap) if gap else " ") + t[2])
            last = t[0]
            self.i += 1
        return self.scalar("".join(parts), lineno)

    def quoted_lines(self, lineno, ind, text):
        """A quoted scalar that does not close on its own line.  Its later
        lines are CONTENT whatever they start with — a `# heading` inside
        quotes is not a comment — so they are read raw, folded the way YAML
        folds a flow scalar (a break is a space, each blank line a newline, a
        trailing backslash in "..." joins with nothing), until it closes."""
        dq, blank, j = text[0] == '"', 0, lineno     # raw[lineno] is the NEXT line
        while j < len(self.raw):
            ln = self.raw[j]
            body = ln.strip(" \t")
            if not body:
                blank, j = blank + 1, j + 1
                continue
            if len(ln) - len(ln.lstrip(" ")) <= ind:
                break                                # dedented: it never closed
            if dq and (len(text) - len(text.rstrip("\\"))) % 2:
                text = text[:-1] + "\n" * blank + body
            else:
                text += ("\n" * blank if blank else " ") + body
            blank, j = 0, j + 1
            if _hc_quote_end(text) is not None:
                break
        self.skip_to = max(self.skip_to, j)
        self.problems = [p for p in self.problems if not lineno < p[0] <= j]
        return self.scalar(text, lineno)

    def block_scalar(self, lineno, ind, style, flags):
        lines, j = [], lineno               # raw[lineno] is the NEXT line
        while j < len(self.raw):
            ln = self.raw[j]
            if ln.strip() and (len(ln) - len(ln.lstrip(" "))) <= ind:
                break
            lines.append(ln.rstrip())
            j += 1
        self.skip_to = max(self.skip_to, j)
        # a TAB inside a block scalar is content, not bad indentation
        self.problems = [p for p in self.problems if not lineno < p[0] <= j]
        trail = 0
        while lines and not lines[-1].strip():
            lines.pop()
            trail += 1
        body = [x for x in lines if x.strip()]
        cut = min((len(x) - len(x.lstrip(" ")) for x in body), default=0)
        lines = [x[cut:] for x in lines]
        if style == "|":
            text = "\n".join(lines)
        else:                               # folded: one break -> a space,
            text, blank = "", 0             # each blank line -> a newline
            for x in lines:
                if not x.strip():
                    blank += 1
                    continue
                text += ("\n" * blank) if (blank or not text) else " "
                text += x
                blank = 0
        if not text or "-" in flags:        # chomping: strip / clip / keep
            return text
        return text + "\n" * (1 + (trail if "+" in flags else 0))

    def scalar(self, s, lineno):
        s = s.strip()
        if s[0] in "\"'":
            end = _hc_quote_end(s)
            if end is None:
                self.bad(lineno, "an unterminated quoted string")
                return s
            tail = s[end + 1:].strip()
            if tail and not tail.startswith("#"):
                self.bad(lineno, "text after a quoted value")
            return _hc_unquote(s[:end + 1])
        if s[0] in "[{":
            close = "]" if s[0] == "[" else "}"
            depth, q, end = 0, None, None
            for k, c in enumerate(s):
                if q:
                    if c == q:
                        q = None
                elif c in "\"'":
                    q = c
                elif c in "[{":
                    depth += 1
                elif c in "]}":
                    depth -= 1
                    if depth == 0:
                        end = k
                        break
            inner = s[1:end] if end is not None else None
            if inner is None or s[end] != close or any(c in inner for c in "[]{}"):
                self.bad(lineno, "a nested or unterminated flow collection")
                return None
            items = _hc_split_flow(inner)
            if close == "]":
                return [self.scalar(x, lineno) for x in items]
            d = {}
            for x in items:
                m = _HC_KEY_RE.match(x)
                if not m:
                    self.bad(lineno, "a flow mapping entry that is not 'key: value'")
                    continue
                d[_hc_key(m.group("key"))] = self.scalar(m.group("rest") or "~", lineno)
            return d
        if s[0] in "@`%":
            self.bad(lineno, "a reserved YAML indicator")
            return None
        if s[:2] in ("? ", ": ") or s in ("?", ":"):
            self.bad(lineno, "a complex '? key' (not supported)")
            return None
        m = re.search(r"[ \t]#", s)
        if m:
            s = s[:m.start()].rstrip()
        return _hc_plain(s)


def _hcfg_parse(text):
    """(tree, problems) for YAML text.  Pure, never raises: an unreadable line
    becomes a (lineno, why) problem and the rest of the file is still read."""
    try:
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if raw and raw[0].startswith("﻿"):
            raw[0] = raw[0][1:]
        p = _HcParser(raw)
        tree = p.document()
        return (tree if isinstance(tree, dict) else {}), sorted(p.problems)
    except Exception as e:                      # pragma: no cover — belt and braces
        return {}, [(0, "reader crashed — %s" % type(e).__name__)]


# ---------------------------------------------------------------------------
# safe in-place editing (audit E16/E17) — refuse what cannot be edited safely,
# verify what was edited, BEFORE anything is written
# ---------------------------------------------------------------------------
# The three config.yaml editors (this file, dashboard/aux_promptbudget.py and
# dashboard/aux_context.py — the last two load this file by path) are line
# editors.  A line editor that misreads the file's shape writes a SECOND
# top-level block (YAML last-wins, so the owner's block silently stops
# counting) or a list YAML rejects (Hermes then falls back to its defaults and
# approval-gate is not loaded) — and used to report success either way.  So
# every write now goes:
#
#   precheck(src, section)   refuse a form the editors cannot edit safely
#   new = <the editor>(src)  unchanged editors, list items now read at the
#                            key's own indent as well as deeper
#   match_newlines(src, new) a CRLF file stays CRLF
#   verify_edit(src, new, …) re-read `new` with the reader below and refuse it
#                            unless the section is there exactly once, holds
#                            exactly the intended value, every item that was
#                            in the list still is, and every OTHER top-level
#                            block is byte-identical
#
# and only then the backup + atomic replace.  A refusal is a ConfigEditError
# (a RuntimeError) naming the line; nothing is written.

class ConfigEditError(RuntimeError):
    """config.yaml is in a form the editors refuse to touch, or an edit did
    not verify.  Nothing was written.  `lineno` is 1-based, or None."""

    HINT = "edit it by hand or run `hermes config set …`"

    def __init__(self, why, lineno=None, verify=False):
        self.why = why
        self.lineno = lineno
        if verify:
            msg = ("Hermes Assistant could not verify its edit of config.yaml "
                   "(%s), so nothing was written; %s" % (why, self.HINT))
        elif lineno is not None:
            msg = ("config.yaml line %d uses a form Hermes Assistant cannot "
                   "edit safely (%s); nothing was written — %s"
                   % (lineno, why, self.HINT))
        else:
            msg = ("config.yaml uses a form Hermes Assistant cannot edit "
                   "safely (%s); nothing was written — %s" % (why, self.HINT))
        RuntimeError.__init__(self, msg)


REMOVE = object()           # verify_edit: this child key must be gone
_MISSING = object()
_DOC_MARK_RE = re.compile(r"^(---|\.\.\.)(?=\s|$)")
_MERGE_RE = re.compile(r"^(?:-\s+)?<<\s*:")
# what the reader says about a column-0 line that means "this is not a plain
# top-level mapping", as opposed to a value it could not read (tolerated
# outside the edited section: that block is verified byte-identical anyway)
_TOP_LEVEL_FATAL = ("a top-level list, not a mapping",
                    "not a 'key: value' line", "could not be read")


def parse_yaml(text):
    """(tree, problems) — doctor.py's YAML-subset reader (copied below)."""
    return _hcfg_parse(text)


def scalar_value(s):
    """A plain scalar exactly as the reader (and PyYAML) resolves it."""
    return _hc_plain(s)


def _raw_lines(text):
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if raw and raw[0].startswith("﻿"):
        raw[0] = raw[0][1:]
    return raw


def _canon(v):
    """Order-insensitive for mappings, exact (types included) otherwise."""
    if isinstance(v, dict):
        return ("{" + ",".join("%r:%s" % (k, _canon(v[k]))
                               for k in sorted(v, key=repr)) + "}")
    if isinstance(v, list):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    return "%s(%r)" % (type(v).__name__, v)


def _section_span(raw, header):
    """0-based [start, end) of the block whose header is raw[header]."""
    for i in range(header + 1, len(raw)):
        line = raw[i]
        if line.strip() and line[0] not in " \t" and not line.startswith("#"):
            return header, i
    return header, len(raw)


def precheck(text, section):
    """Raise ConfigEditError when `text` is in a form a line editor of the
    top-level `section` cannot edit safely.  Returns the 1-based line of the
    section's header, or None when the section is absent (an editor may then
    append it).

    Refused: a tab in the indentation; more than one YAML document; a
    column-0 line that is not `key: …`; the section's key quoted, written
    twice, or carrying anything but a comment on its own line (flow style,
    a scalar, an anchor/alias/tag); a section that is not a key: value block;
    inside the section, anything the reader cannot read (anchors, aliases,
    tags, multi-line flow), a merge key `<<`, or a quoted or duplicate child
    key."""
    raw = _raw_lines(text)
    tree, problems = _hcfg_parse(text)
    header = None
    content = False
    ended = False
    for n, line in enumerate(raw, 1):
        if not line.strip():
            continue
        lead = line[:len(line) - len(line.lstrip(" \t"))]
        if "\t" in lead:
            raise ConfigEditError("a TAB used for indentation", n)
        body = line.lstrip(" ")
        if body.startswith("#") or line[0] == " ":
            if line[0] == " " and ended:
                raise ConfigEditError("a second YAML document", n)
            continue
        dm = _DOC_MARK_RE.match(line)
        if dm or line.startswith("%"):
            if line.startswith("%") or dm.group(1) == "---":
                if content or ended:
                    raise ConfigEditError("a second YAML document", n)
                tail = line[3:].strip() if dm else ""
                if tail and not tail.startswith("#"):
                    raise ConfigEditError("content on the `---` line", n)
            else:
                ended = True
            continue
        if ended:
            raise ConfigEditError("a second YAML document", n)
        content = True
        if line.startswith("-"):
            continue                # an indentless list under the key above
        if _MERGE_RE.match(line):
            # a top-level `<<: *x` can supply the section itself; an appended
            # block would then silently replace the merged one (review R4)
            raise ConfigEditError("a merge key (<<) at the top level", n)
        km = _HC_KEY_RE.match(line)
        if not km:
            raise ConfigEditError("a top-level line that is not 'key: value'", n)
        key = km.group("key")
        if _hc_key(key) != section:
            continue
        if key[0] in "\"'":
            raise ConfigEditError("the %s key is quoted" % section, n)
        if header is not None:
            raise ConfigEditError("a second %s: block (a duplicate key; the "
                                  "first is on line %d)" % (section, header), n)
        rest = (km.group("rest") or "").strip()
        if rest and not rest.startswith("#"):
            if rest[0] in "[{":
                why = "flow style on the %s: line" % section
            elif rest[0] in "&*!":
                why = "an anchor, alias or tag on the %s: line" % section
            else:
                why = "a value on the %s: line itself" % section
            raise ConfigEditError(why, n)
        header = n
    for lineno, why in problems:
        if 0 < lineno <= len(raw) and raw[lineno - 1][:1] not in ("", " ", "\t") \
                and why in _TOP_LEVEL_FATAL:
            raise ConfigEditError(why, lineno)
        if why == "an unterminated quoted string":
            # the reader stops a quoted value at a line indented no deeper
            # than its key; PyYAML reads on to the closing quote.  Anywhere in
            # the file, such a value can hold a column-0 `plugins:` the line
            # editors would edit INSIDE the string (review R4)
            raise ConfigEditError("a quoted value that runs on over lines "
                                  "not indented under its key", lineno)
    if header is None:
        return None
    start, end = _section_span(raw, header - 1)
    if tree.get(section) is not None and not isinstance(tree.get(section), dict):
        raise ConfigEditError("the %s: section is not a key: value block"
                              % section, header)
    for lineno, why in problems:
        if start + 1 < lineno <= end:
            raise ConfigEditError(why, lineno)
    child, seen = None, {}
    for i in range(start + 1, end):
        line = raw[i]
        body = line.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        if _MERGE_RE.match(body):
            raise ConfigEditError("a merge key (<<)", i + 1)
        km = _HC_KEY_RE.match(body[2:] if body.startswith("- ") else body)
        val = ((km.group("rest") or "") if km else
               (body[2:] if body.startswith("- ") else "")).strip()
        if val[:1] in ("[", "{") and (val.count("[") + val.count("{")
                                      != val.count("]") + val.count("}")):
            raise ConfigEditError("a flow list or map that spans lines", i + 1)
        ind = len(line) - len(body)
        if child is None:
            child = ind
        if ind != child or body.startswith("- ") or body == "-":
            continue
        km = _HC_KEY_RE.match(body)
        if not km:
            continue                # the reader already judged this line
        key = km.group("key")
        if key[0] in "\"'":
            raise ConfigEditError("a quoted key inside %s:" % section, i + 1)
        name = _hc_key(key)
        if name in seen:
            raise ConfigEditError("%s.%s is written twice (first on line %d)"
                                  % (section, name, seen[name]), i + 1)
        seen[name] = i + 1
    return header


def child_line(text, section, key):
    """1-based line of `section.key` at the section's own child indent, or
    None.  For error messages."""
    raw = _raw_lines(text)
    for n, line in enumerate(raw):
        km = _HC_KEY_RE.match(line) if line[:1] not in ("", " ", "#", "-") else None
        if km and _hc_key(km.group("key")) == section:
            start, end = _section_span(raw, n)
            child = None
            for i in range(start + 1, end):
                body = raw[i].lstrip(" ")
                if not body or body.startswith("#"):
                    continue
                ind = len(raw[i]) - len(body)
                child = ind if child is None else child
                m = _HC_KEY_RE.match(body) if ind == child else None
                if m and _hc_key(m.group("key")) == key:
                    return i + 1
            return n + 1
    return None


def section_value(text, section):
    """The parsed top-level `section` mapping ({} when absent or empty)."""
    tree, _p = _hcfg_parse(text)
    v = tree.get(section)
    return v if isinstance(v, dict) else {}


def match_newlines(src, new):
    """A file written with CRLF throughout stays CRLF: the editors build new
    lines with a bare \\n, which YAML accepts but which would leave a mixed
    file behind."""
    if "\r\n" in src and src.count("\n") == src.count("\r\n"):
        return re.sub(r"(?<!\r)\n", "\r\n", new)
    return new


def _other_segments(text, section):
    """[(key, lines)] for every top-level block except `section`, plus the
    lines before the first key.  Lines keep their bytes (a CR included); the
    blank lines that close a block are not part of it."""
    segs, cur = [("", [])], None
    for line in text.split("\n"):
        if line.strip() and line[0] not in " \t#-" \
                and not _DOC_MARK_RE.match(line) and not line.startswith("%"):
            km = _HC_KEY_RE.match(line.rstrip("\r"))
            cur = _hc_key(km.group("key")) if km else line
            segs.append((cur, []))
        segs[-1][1].append(line)
    out = []
    for key, lines in segs:
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        if key != section or key == "":
            out.append((key, lines))
    return out


def verify_edit(src, new, section, changes, keep=None):
    """Raise ConfigEditError unless `new` is `src` with exactly `changes`
    applied to the top-level `section`:

      * `new` passes precheck() and holds the section exactly once;
      * it reads back with no more problems than `src` did;
      * every `changes` key reads back as the given value (REMOVE: absent);
      * every other key of the section reads back as before;
      * every item of keep[key] is still in that list;
      * every other top-level key's value is unchanged and its block (and the
        lines before the first key) is byte-identical.
    """
    def fail(why):
        raise ConfigEditError(why, verify=True)

    try:
        header = precheck(new, section)
    except ConfigEditError as e:
        fail("the result would have %s at line %s" % (e.why, e.lineno))
    if header is None:
        fail("the %s: section would be missing" % section)
    t0, p0 = _hcfg_parse(src)
    t1, p1 = _hcfg_parse(new)
    if len(p1) > len(p0):
        extra = [p for p in p1 if p not in p0] or p1
        fail("the result would not read back cleanly — line %d: %s" % extra[0])
    s0 = t0.get(section)
    s0 = s0 if isinstance(s0, dict) else {}
    s1 = t1.get(section)
    s1 = s1 if isinstance(s1, dict) else ({} if s1 is None else s1)
    if not isinstance(s1, dict):
        fail("%s would not be a key: value block" % section)
    for k, want in changes.items():
        if want is REMOVE:
            if k in s1:
                fail("%s.%s would still be there" % (section, k))
        elif k not in s1 or _canon(s1[k]) != _canon(want):
            fail("%s.%s would read back as %r, not %r"
                 % (section, k, s1.get(k), want))
    for k in set(s0) | set(s1):
        if k in changes:
            continue
        if _canon(s0.get(k, _MISSING)) != _canon(s1.get(k, _MISSING)):
            fail("%s.%s would change" % (section, k))
    for k, items in (keep or {}).items():
        got = s1.get(k)
        lost = [x for x in items if not isinstance(got, list) or x not in got]
        if lost:
            fail("%s.%s would lose %s" % (section, k, ", ".join(map(str, lost))))
    o0 = {k: v for k, v in t0.items() if k != section}
    o1 = {k: v for k, v in t1.items() if k != section}
    if _canon(o0) != _canon(o1):
        fail("a setting outside %s: would change" % section)
    if _other_segments(src, section) != _other_segments(new, section):
        fail("a line outside %s: would change" % section)
    return True


def read_enabled(src):
    """The `plugins.enabled` list. [] when the block is absent or malformed —
    never raises, never imports yaml.

    `enabled:` is matched only at the `plugins:` block's own child indent, so a
    deeper `enabled:` under some plugin's options is not read as this list."""
    out = []
    lines = src.splitlines()
    start = _find_block(lines, "plugins")
    if start is None:
        return out
    end = _block_end(lines, start)
    child = _child_indent(lines, start, end)
    if child is None:
        return out

    e_at = None
    rest = ""
    for i in range(start + 1, end):
        if _skippable(lines[i]):
            continue
        if _indent_of(lines[i]) != child:
            continue                                # a deeper key, not ours
        m = re.match(r"^\s*enabled:\s*(.*)$", lines[i])
        if m:
            e_at = i
            rest = (m.group(1) or "").strip()
            break
    if e_at is None:
        return out

    if rest.startswith("["):                        # inline flow list
        inner = rest[1:rest.rfind("]")] if "]" in rest else rest[1:]
        for part in inner.split(","):
            p = part.strip().strip("'\"")
            if p:
                out.append(p)
        return out

    # block list — items at the key's OWN indent count too (E16: PyYAML's
    # default dump writes `enabled:\n  - a`, and reading that as [] made the
    # writer add a deeper list above the old items, a file YAML rejects)
    for j in range(e_at + 1, end):
        if _skippable(lines[j]):
            continue
        mm = re.match(r"^(\s*)-\s+(\S.*?)\s*$", lines[j])
        if not mm or len(mm.group(1)) < child:
            break
        item = re.sub(r"\s+#.*$", "", mm.group(2))
        out.append(item.strip().strip("'\""))
    return out


def apply_enable(src, name):
    """PURE: return `src` with `name` present in plugins.enabled.

    Byte-identical when it is already there, so a re-run is a genuine no-op —
    no write, no backup.  Handles the four shapes the file can be in: no
    plugins block, a plugins block with no enabled key, an inline flow list
    (rewritten as a block list), and a block list (indentation preserved).
    """
    if name in read_enabled(src):
        return src

    lines = src.splitlines(True)
    start = _find_block(lines, "plugins")

    if start is None:
        tail = "" if (not src or src.endswith("\n")) else "\n"
        return src + tail + "plugins:\n  enabled:\n    - %s\n" % name

    end = _block_end(lines, start)
    child = _child_indent(lines, start, end)

    e_at = None
    if child is not None:
        for i in range(start + 1, end):
            if _skippable(lines[i]):
                continue
            if _indent_of(lines[i]) != child:
                continue                            # a deeper key, not ours
            if re.match(r"^\s*enabled:", lines[i]):
                e_at = i
                break

    if e_at is None:
        ind = 2 if child is None else child
        if not lines[start].endswith("\n"):        # `plugins:` ends the file
            lines[start] += "\n"
        return "".join(lines[:start + 1] +
                       ["%senabled:\n%s- %s\n"
                        % (" " * ind, " " * (ind + 2), name)] +
                       lines[start + 1:])

    key_indent = _indent_of(lines[e_at])
    rest = lines[e_at].split(":", 1)[1].strip()

    if rest.startswith("["):
        existing = []
        inner = rest[1:rest.rfind("]")] if "]" in rest else rest[1:]
        for part in inner.split(","):
            p = part.strip().strip("'\"")
            if p:
                existing.append(p)
        existing.append(name)
        # a comment after the flow list stays on the key line
        tail = rest[rest.rfind("]") + 1:].strip() if "]" in rest else ""
        note = ("  " + tail) if tail.startswith("#") else ""
        block = (" " * key_indent) + "enabled:" + note + "\n" + "".join(
            (" " * (key_indent + 2)) + "- %s\n" % e for e in existing)
        return "".join(lines[:e_at] + [block] + lines[e_at + 1:])

    # items at the key's own indent (indentless) or deeper; a new item takes
    # the indent the list already uses, so the file keeps its style
    item_indent = key_indent + 2
    last = e_at
    for i in range(e_at + 1, end):
        if _skippable(lines[i]):
            continue                    # decide on the next real line instead
        m = re.match(r"^(\s*)-(\s|$)", lines[i])
        if not m or len(m.group(1)) < key_indent:
            break
        item_indent = len(m.group(1))
        last = i
    nl = "" if lines[last].endswith("\n") else "\n"
    return "".join(lines[:last] + [lines[last] + nl] +
                   [(" " * item_indent) + "- %s\n" % name] + lines[last + 1:])


def edit_enable(src, name):
    """`apply_enable` behind the E16/E17 guard: refuse a form that cannot be
    edited safely, then re-read the result and refuse it unless plugins.enabled
    is exactly the old list plus `name` and nothing else in the file moved.
    Returns the new text (`src` itself for a no-op); raises ConfigEditError,
    having written nothing."""
    if src[:1] == "﻿":
        # a byte-order mark hid the header from the line editor (review R4)
        return "﻿" + edit_enable(src[1:], name)
    precheck(src, "plugins")
    old = section_value(src, "plugins").get("enabled")
    if old is not None and not isinstance(old, list):
        raise ConfigEditError("plugins.enabled is not a list",
                              child_line(src, "plugins", "enabled"))
    old = list(old or [])
    new = apply_enable(src, name)
    if new == src:
        if name not in old:             # the line reader and the YAML disagree
            raise ConfigEditError("%s reads as enabled to one reader and not "
                                  "to the other" % name, verify=True)
        return src
    new = match_newlines(src, new)
    want = old if name in old else old + [name]
    verify_edit(src, new, "plugins", {"enabled": want}, keep={"enabled": old})
    return new


def backup(path, tag):
    """Timestamped copy next to the config, 0600."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-%s-%s" % (path, tag, stamp)
    shutil.copy2(path, dst)
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    return dst


def enable(config_path, name, dry_run=False, tag=None):
    """Add `name` to plugins.enabled. Returns (changed, backup_path).

    The whole read -> apply -> replace runs under `config_lock()`, so a second
    writer (the dashboard card, the other script) sees this edit rather than
    the text we started from.  The replace is atomic through a uniquely named
    temp file in the same directory, so a crash mid-write cannot leave a
    half-config behind, and the original file mode is preserved.
    """
    with config_lock(config_path):
        # newline="": a CRLF file is read as written, so the lines this edit
        # does not touch stay byte-identical (universal newlines used to
        # rewrite the whole file as LF)
        with open(config_path, encoding="utf-8", newline="") as fh:
            src = fh.read()
        new = edit_enable(src, name)        # raises ConfigEditError: no write
        if new == src:
            return False, None
        if dry_run:
            return True, None
        bak = backup(config_path, tag or name)
        try:
            mode = os.stat(config_path).st_mode & 0o777
        except OSError:
            mode = 0o600
        atomic_write(config_path, new, mode)
        print("plugin_enable: plugins.enabled += %s (%s)" % (name, config_path),
              file=sys.stderr)
        return True, bak


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", help="plugin name, e.g. tool-budget")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="report whether it is enabled; change nothing")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.config):
        print("plugin_enable: no config at %s" % a.config, file=sys.stderr)
        return 2

    if a.check:
        with open(a.config, encoding="utf-8") as fh:
            on = a.name in read_enabled(fh.read())
        print("enabled" if on else "not enabled")
        return 0 if on else 1

    try:
        changed, bak = enable(a.config, a.name, dry_run=a.dry_run)
    except ConfigEditError as e:
        # exit non-zero so install.sh / update.sh notice: the old behaviour was
        # a broken or shadowed config and exit 0
        print("plugin_enable: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("plugin_enable: %s" % e, file=sys.stderr)
        return 2

    if not changed:
        print("already in plugins.enabled")
    elif a.dry_run:
        print("would add %s to plugins.enabled" % a.name)
    else:
        print("added %s to plugins.enabled (backup: %s)" % (a.name, bak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
