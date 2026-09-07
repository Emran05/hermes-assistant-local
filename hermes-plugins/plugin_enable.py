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
"""

import argparse
import datetime
import os
import re
import shutil
import sys

DEFAULT_CONFIG = os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml")


def read_enabled(src):
    """The `plugins.enabled` list. [] when the block is absent or malformed —
    never raises, never imports yaml."""
    out = []
    lines = src.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        if re.match(r"^plugins:\s*$", lines[i]):
            break
        i += 1
    else:
        return out
    i += 1
    while i < n:
        line = lines[i]
        if line.strip() and re.match(r"^\S", line):
            break                                   # left the plugins: block
        m = re.match(r"^\s+enabled:\s*(.*)$", line)
        if m:
            rest = (m.group(1) or "").strip()
            if rest.startswith("["):                # inline flow list
                inner = rest[1:rest.rfind("]")] if "]" in rest else rest[1:]
                for part in inner.split(","):
                    p = part.strip().strip("'\"")
                    if p:
                        out.append(p)
                return out
            j = i + 1
            while j < n:                            # block list
                mm = re.match(r"^\s+-\s*(\S.*?)\s*$", lines[j])
                if not mm:
                    break
                out.append(mm.group(1).strip().strip("'\""))
                j += 1
            return out
        i += 1
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
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^plugins:\s*$", line):
            start = i
            break

    if start is None:
        tail = "" if (not src or src.endswith("\n")) else "\n"
        return src + tail + "plugins:\n  enabled:\n    - %s\n" % name

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and re.match(r"^\S", lines[i]):
            end = i
            break

    e_at = None
    for i in range(start + 1, end):
        if re.match(r"^\s+enabled:", lines[i]):
            e_at = i
            break

    if e_at is None:
        return "".join(lines[:start + 1] +
                       ["  enabled:\n    - %s\n" % name] + lines[start + 1:])

    key_indent = len(lines[e_at]) - len(lines[e_at].lstrip())
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
        m = re.match(r"^(\s+)-\s*\S", lines[i])
        if not m:
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

    An atomic replace through a temp file in the same directory, so a crash
    mid-write cannot leave a half-config behind, and the original file mode is
    preserved."""
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
    tmp = config_path + ".plugin-enable.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, config_path)
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
