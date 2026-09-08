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
(--check only), 2 an error.

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
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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
    for i, line in enumerate(lines):
        if re.match(r"^%s:\s*$" % re.escape(key), line):
            return i
    return None


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

    for j in range(e_at + 1, end):                  # block list
        if _skippable(lines[j]):
            continue
        mm = re.match(r"^(\s+)-\s*(\S.*?)\s*$", lines[j])
        if not mm or len(mm.group(1)) <= child:
            break
        out.append(mm.group(2).strip().strip("'\""))
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
        block = (" " * key_indent) + "enabled:\n" + "".join(
            (" " * (key_indent + 2)) + "- %s\n" % e for e in existing)
        return "".join(lines[:e_at] + [block] + lines[e_at + 1:])

    item_indent = key_indent + 2
    last = e_at
    for i in range(e_at + 1, end):
        if _skippable(lines[i]):
            continue                    # decide on the next real line instead
        m = re.match(r"^(\s+)-\s*\S", lines[i])
        if not m or len(m.group(1)) <= key_indent:
            break
        item_indent = len(m.group(1))
        last = i
    return "".join(lines[:last + 1] +
                   [(" " * item_indent) + "- %s\n" % name] + lines[last + 1:])


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
        with open(config_path, encoding="utf-8") as fh:
            src = fh.read()
        new = apply_enable(src, name)
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
