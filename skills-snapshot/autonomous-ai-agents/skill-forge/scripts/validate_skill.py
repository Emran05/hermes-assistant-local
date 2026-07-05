#!/usr/bin/env python3
"""validate_skill.py — stdlib-only linter for forged Hermes SKILL.md packs.

Path mode:
    python3 validate_skill.py <path/to/SKILL.md | path/to/skill-dir>

Stdin mode (dry-run a draft before any file exists):
    cat draft.md | python3 validate_skill.py --stdin --name unit-convert --category productivity

Checks (all hard requirements — any FAIL => exit 1):
  * frontmatter present, required keys: name, description, version, platforms,
    metadata.hermes.tags
  * name is kebab-case and (path mode / --name) equals the skill directory name
  * category exists on disk under the skills root and is a real category
    (a directory WITHOUT its own top-level SKILL.md — i.e. not computer-use etc.)
  * description non-empty
  * house-style sections present: Safety & Approvals, Drill, Gotchas
  * secret-scan: API-key shapes, credential key=value pairs, auth-header values,
    hardcoded home paths — hard fail

Exit codes: 0 = PASS, 1 = FAIL, 2 = usage error.
No third-party deps (no pyyaml): frontmatter is parsed minimally.
"""

import os
import re
import sys

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Secret patterns require actual key/value material so that documentation may
# safely *mention* a prefix (e.g. "sk-") without tripping the scan.
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "OpenAI-style API key"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub personal access token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"), "Slack token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "Google API key"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
    (re.compile(r"token=[^\s\"'<>`$]{6,}", re.I), "inline token= value"),
    (re.compile(r"api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_-]{12,}", re.I), "inline api key value"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Authorization bearer value"),
    (re.compile(r"TELEGRAM\w*\s*=\s*\S{6,}"), "Telegram credential assignment"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+"), "hardcoded macOS home path"),
    (re.compile(r"/home/[A-Za-z0-9._-]+"), "hardcoded Linux home path"),
]

HOUSE_SECTIONS = [
    ("Safety & Approvals", re.compile(r"^#{1,6}\s+Safety\s*(?:&|and)\s*Approvals\b", re.I | re.M)),
    ("Drill", re.compile(r"^#{1,6}\s+Drill\b", re.I | re.M)),
    ("Gotchas", re.compile(r"^#{1,6}\s+Gotchas\b", re.I | re.M)),
]

REQUIRED_TOP_KEYS = ["name", "description", "version", "platforms", "metadata"]


def skills_root():
    return os.path.join(
        os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "skills"
    )


def parse_frontmatter(text):
    """Minimal frontmatter split. Returns (dict-of-top-level-raw-values, fm_text) or (None, None)."""
    if not text.startswith("---"):
        return None, None
    m = re.search(r"\n---\s*(\n|$)", text[3:])
    if not m:
        return None, None
    fm_text = text[3 : m.start() + 3]
    top = {}
    for line in fm_text.splitlines():
        if not line or line[0] in " \t#":
            continue
        km = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if km:
            top[km.group(1)] = km.group(2).strip()
    return top, fm_text


def strip_fences(text):
    """If the whole input is wrapped in a markdown code fence, unwrap it."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1])
    return text


def real_categories(root):
    cats = []
    if not os.path.isdir(root):
        return cats
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if os.path.isdir(p) and not os.path.isfile(os.path.join(p, "SKILL.md")):
            cats.append(entry)
    return cats


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def main(argv):
    args = argv[1:]
    use_stdin = "--stdin" in args
    name_hint = category_hint = None
    path = None
    i = 0
    positional = []
    while i < len(args):
        a = args[i]
        if a == "--stdin":
            pass
        elif a == "--name":
            i += 1
            name_hint = args[i] if i < len(args) else None
        elif a == "--category":
            i += 1
            category_hint = args[i] if i < len(args) else None
        elif a == "--skills-root":
            i += 1
            if i < len(args):
                os.environ["HERMES_HOME"] = ""  # ignored; explicit root below
                globals()["_ROOT_OVERRIDE"] = args[i]
        else:
            positional.append(a)
        i += 1

    root = globals().get("_ROOT_OVERRIDE") or skills_root()

    if use_stdin:
        text = strip_fences(sys.stdin.read())
        source = "<stdin>"
    else:
        if not positional:
            print("usage: validate_skill.py <SKILL.md|skill-dir> | --stdin [--name N] [--category C]")
            return 2
        path = positional[0]
        if os.path.isdir(path):
            path = os.path.join(path, "SKILL.md")
        if not os.path.isfile(path):
            print("FAIL no such file: %s" % path)
            return 1
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        source = path
        skill_dir = os.path.dirname(os.path.abspath(path))
        if name_hint is None:
            name_hint = os.path.basename(skill_dir)
        if category_hint is None:
            category_hint = os.path.basename(os.path.dirname(skill_dir))

    failures = []
    oks = []

    top, fm_text = parse_frontmatter(text)
    if top is None:
        failures.append("no YAML frontmatter (file must start with --- fence)")
        fm_text = ""
        top = {}
    else:
        oks.append("frontmatter fence found")

    # required keys
    for key in REQUIRED_TOP_KEYS:
        if key not in top:
            failures.append("missing required frontmatter key: %s" % key)
    if "metadata" in top:
        if not re.search(r"^\s+hermes\s*:", fm_text, re.M):
            failures.append("metadata block missing 'hermes:' subkey")
        elif not re.search(r"^\s+tags\s*:", fm_text, re.M):
            failures.append("metadata.hermes missing 'tags:'")
        else:
            oks.append("metadata.hermes.tags present")

    # name
    name = unquote(top.get("name", ""))
    if name:
        if not KEBAB.match(name):
            failures.append("name %r is not kebab-case" % name)
        elif name_hint and name != name_hint:
            failures.append("name %r != directory name %r" % (name, name_hint))
        else:
            oks.append("name %r kebab-case and matches directory" % name)

    # description
    desc = unquote(top.get("description", ""))
    if "description" in top and not desc:
        failures.append("description is empty")
    elif desc:
        oks.append("description non-empty (%d chars)" % len(desc))

    # version / platforms shape (light)
    if "version" in top and not top["version"]:
        failures.append("version is empty")
    if "platforms" in top and top["platforms"] in ("", "[]"):
        failures.append("platforms is empty")

    # category
    cats = real_categories(root)
    if category_hint:
        if cats and category_hint not in cats:
            failures.append(
                "category %r does not exist under %s (existing: %s) — never invent a "
                "top-level category without user OK; never nest under a flat single-skill dir"
                % (category_hint, root, ", ".join(cats) or "none")
            )
        elif cats:
            oks.append("category %r exists on disk" % category_hint)
    else:
        failures.append("no category (pass --category in --stdin mode)")

    # house-style sections
    for label, rx in HOUSE_SECTIONS:
        if rx.search(text):
            oks.append("house-style section present: %s" % label)
        else:
            failures.append("missing house-style section: ## %s" % label)

    # secret scan (whole file)
    for rx, label in SECRET_PATTERNS:
        m = rx.search(text)
        if m:
            snippet = m.group(0)
            if len(snippet) > 24:
                snippet = snippet[:24] + "…"
            failures.append("SECRET-SCAN hit (%s): %r — skills must not embed secrets or hardcoded home paths" % (label, snippet))

    for line in oks:
        print("OK   " + line)
    for line in failures:
        print("FAIL " + line)
    print("%s %s" % ("PASS" if not failures else "FAIL", source))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
