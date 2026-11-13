#!/usr/bin/env python3
"""GitHub sandbox change pipeline — the adversarial review's regressions
(round 3, review of R3-GH2; docs/plans/github-sandbox-2026-10.md §3.4, §3.5).

Shares t_gh_changes.py's harness. Each section is a hole the review found:

  * C-text: an @mention after an UNMATCHED backtick, after a dot/slash/plus, as
    an HTML entity, after a backslash, or glued to another mention still pinged;
    the 8 KB cut could leave a lone backtick in front of a live mention; the
    Markdown comment idioms ([//]: # ...) were as invisible as <!-- --> and
    survived; a comment opener split by a hidden character survived in the title;
  * C-unicode: LRM/RLM/ALM, word joiner, soft hyphen, invisible operators, the
    GlassWorm variation-selector runs were not hidden characters; hidden
    characters in FILE NAMES were not looked at; a single stray non-UTF-8 byte
    switched off the credential and hidden-character scans for a whole file;
  * C-secret-shape: a vendor-shaped value in the title/description or in a
    file git calls binary was never looked at; findings beyond the cap
    vanished silently;
  * a push git reported but GitHub could not confirm sent NO Telegram notice;
  * a repository whose default branch is called `hermes` collided with the
    hermes/<slug> refs (a 500 after a half-written mirror);
  * draft / already-exists 422s are read from errors[] too, and an append-mode
    push adopts the branch's open PR instead of reporting a failure;
  * the change store is bounded; the speed bump works with server.py's real
    _hdr and a lower-cased RouteCtx.headers.
"""
import ast
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
BIG_TOKEN = "ghp_" + "Q9w8E7r6T5y4U3i2O1p0A9s8D7f6G5h4J3k2"        # 40 chars, vendor-shaped


def reset(sb):
    H.tgit(["checkout", "-q", "--", "."], cwd=sb, check_ok=False)
    H.tgit(["clean", "-q", "-fdx"], cwd=sb, check_ok=False)


_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_URL_TAIL = re.compile(r"(?i)(?:\b(?:https?|ftp)://|\bwww\.)[^\s<>()\[\]\"'`]*$")
# any @ that starts a mention GitHub could read (a user or org/team, chained or not)
_MENTION = re.compile(r"(?<![A-Za-z0-9_])(?:@[A-Za-z0-9][A-Za-z0-9-]{0,38}"
                      r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,99})?)+(?![A-Za-z0-9-])")


def certain_code(text):
    """Positions GitHub CERTAINLY renders as code — an INDEPENDENT walk (the
    suite does not let the code under test grade itself): fenced blocks, and
    single-line code spans parsed per table cell, cmark's way (a backslash
    escapes punctuation, so an escaped backtick opens nothing; a closer is the
    next raw backtick run of the same width). Nothing is certain when the text
    holds raw HTML."""
    if re.search(r"<[A-Za-z/!?]", text):
        return set()
    safe, lines, off, i = set(), text.split("\n"), 0, 0
    while i < len(lines):
        ln = lines[i]
        st = ln.lstrip(" ")
        if len(ln) - len(st) <= 3 and (st.startswith("```") or st.startswith("~~~")):
            ch, k = st[0], 0
            while k < len(st) and st[k] == ch:
                k += 1
            if not (ch == "`" and "`" in st[k:]):
                j, closed = i + 1, None
                while j < len(lines):
                    l2 = lines[j]
                    s2 = l2.lstrip(" ").rstrip(" \t")
                    if len(l2) - len(l2.lstrip(" ")) <= 3 and s2 and set(s2) == {ch} \
                            and len(s2) >= k:
                        closed = j
                        break
                    j += 1
                last = closed if closed is not None else len(lines) - 1
                span = sum(len(lines[x]) + 1 for x in range(i, last + 1))
                safe.update(range(off, off + span))
                off += span
                i = last + 1
                continue
        cells, cs, pos = [], 0, 0
        while pos < len(ln):
            if ln[pos] == "\\" and pos + 1 < len(ln) and ln[pos + 1] in _PUNCT:
                pos += 2
                continue
            if ln[pos] == "|":
                cells.append((cs, pos))
                cs = pos + 1
            pos += 1
        cells.append((cs, len(ln)))
        for cs, ce in cells:
            pos = cs
            while pos < ce:
                if ln[pos] == "\\" and pos + 1 < ce and ln[pos + 1] in _PUNCT:
                    pos += 2
                    continue
                if ln[pos] != "`":
                    pos += 1
                    continue
                a0 = pos
                while pos < ce and ln[pos] == "`":
                    pos += 1
                w, q, hit = pos - a0, pos, None
                while q < ce:
                    if ln[q] == "`":
                        b0 = q
                        while q < ce and ln[q] == "`":
                            q += 1
                        if q - b0 == w:
                            hit = (b0, q)
                            break
                    else:
                        q += 1
                if hit:
                    safe.update(range(off + a0, off + hit[1]))
                    pos = hit[1]
        off += len(ln) + 1
        i += 1
    return safe


def live(inp, out):
    """Mentions of `out` that nothing keeps harmless: not certainly code, not in
    an autolinked URL, and not wrapped by the pipeline's own delimiter — one
    backtick run of a width no UNCERTAIN run of the input (nor one less: an
    escaped run opens one shorter) has, equal on both sides, not fused with a neighbouring
    backtick or backslash, so it can pair only with its partner."""
    safe = certain_code(out)
    forbidden, sure = set(), certain_code(inp)
    for m in re.finditer(r"`+", inp):
        if m.start() not in sure:
            forbidden.update((len(m.group(0)), len(m.group(0)) - 1))
    res = []
    for m in _MENTION.finditer(out):
        if m.start() in safe or _URL_TAIL.search(out[:m.start()][-2048:]):
            continue
        l = m.start()
        while l > 0 and out[l - 1] == "`":
            l -= 1
        r = m.end()
        while r < len(out) and out[r] == "`":
            r += 1
        left, right = m.start() - l, r - m.end()
        ok = left >= 1 and left == right and left not in forbidden \
            and (out[l - 1] if l else "") not in ("`", "\\") \
            and (out[r] if r < len(out) else "") != "`"
        if not ok:
            res.append(m.group(0))
    return res


def only(r, cid):
    return [c for c in r.get("checks") or [] if c["id"] == cid]


class StubResp(object):
    def __init__(self, status, data=None, message=""):
        self.status, self.data, self.message, self.error = status, data, message, None


class StubAPI(object):
    """Just enough GitHubAPI for _ghc_open_pr: scripted answers, recorded calls."""

    def __init__(self, posts, gets=()):
        self.posts, self.gets, self.calls = list(posts), list(gets), []

    def post(self, path, body=None):
        self.calls.append(("POST", path, dict(body or {})))
        return self.posts.pop(0)

    def get(self, path, query=None):
        self.calls.append(("GET", path, dict(query or {})))
        return self.gets.pop(0)


def main():
    w = H.World("review")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.repo("octo/hb", {"README.md": "# hb\n"}, default_branch="hermes")
    w.start()
    ns = H.load(w)
    tok = H.connect(w, ns, ["octo/app"])
    clean = ns["_ghc_clean_text"]

    print("== C-text: nobody is pinged ==")
    neut = ns["_ghc_neutralise_mentions"]
    hostile = {
        "lone backtick": "`oops @octocat please",
        "backslash": "\\@octocat and \\`@octocat`",
        "dot, slash, plus, hyphen": "x.@octocat y/@octocat z+@octocat w-@octocat",
        "html entities": "&#64;octocat &#x40;octocat &commat;octocat &#0064;octocat",
        "after a code span": "`x`@octocat ``y``@octocat",
        "chained": "@a@b@c/team and @@octocat",
        "unmatched next to a span": "` @x ` and ` @y",
        "team": "cc @acme/core-team, @acme/x_y.z",
        "fence + stray": "```\n@inside\n```\n@outside ` @after",
        "escaped closer (cmark closes on the raw backtick)": "`a\\` @x `",
        "span across a blank line": "`\n\n@octocat\n\n`",
        "span across a list item": "text `\n- item @octocat `",
        "span across a heading": "# title `\n@octocat `",
        "span across table cells": "| ` | @octocat | ` |",
        "pipe inside a would-be span": "`a | @octocat`",
        "html block hides the backticks": "<div>\n`@octocat`\n</div>",
        "backtick inside an html tag": '<a title="`">@octocat</a>`',
        "an autolink in angle brackets": "<https://example.com> `@x` @y",
        "fence inside an html block": "<div>\n```\n@octocat\n```\n</div>",
        "info string with a backtick is no fence": "```a`b\n@octocat\n```",
        "tilde fence closed by backticks": "~~~\n```\n@octocat",
    }
    for label, text in hostile.items():
        out, _n = neut(text)
        check("%s: no live mention is left" % label, live(text, out) == [], (out, live(text, out)))
    for label, text in (("a fenced block (decorators, annotations)", "```py\n@property\ndef x(): pass\n```\n"),
                        ("a tilde fence", "~~~\n@Override\n~~~"),
                        ("an unclosed fence", "intro\n```\n@Override\nmore"),
                        ("an inline code span", "use `@Override` here"),
                        ("a double-backtick span", "use ``@a `b` @c`` here"),
                        ("inline code in a list item", "- call `@property`\n- and ``@x``")):
        out, _n = neut(text)
        check("code is left as it was: %s" % label, out == text, out)
    out, _n = neut("<div>\n@octocat\n</div> and `@Override`")
    check("raw HTML: the @ becomes the full-width one (a backtick span would not hold in an "
          "HTML block)", "\uff20octocat" in out and "@octocat" not in out, out)
    out, _n = neut("a `@octocat` b @c")
    check("mixed: a span kept, the loose one wrapped", out == "a `@octocat` b `@c`", out)
    out, _n = neut("lone ` and @c")
    check("...but not with the width of a lone backtick before it (it would pair)",
          out == "lone ` and ``@c``", out)
    t, b, _n = clean("t", "mail a@b.example, bob+tag@example.com, x@y")
    check("e-mail addresses are not mentions", b == "mail a@b.example, bob+tag@example.com, x@y",
          b)
    t1, b1, _n = clean("t", "see @octocat and `@x` and \\@y")
    t2, b2, _n = clean("t", b1)
    check("cleaning the cleaned text changes nothing (the owner's edit round trip)", b2 == b1,
          (b1, b2))
    urls = "see https://mastodon.social/@alice, https://medium.com/@bob/post and www.x.org/@y <https://a.b/@c>"
    t, b, _n = clean("t", urls)
    check("an @ inside an autolinked URL is part of the link, left alone", "@alice" in b
          and "@bob/post" in b and "www.x.org/@y" in b, b)
    for label, text in (("a space ends the URL", "x https://a.b/ @alice"),
                        ("a closing bracket ends it", "[x](https://a/b)@alice"),
                        ("a quote ends it", 'go to "https://a/"@alice'),
                        ("a non-http scheme is no autolink", "foo://a/@alice")):
        out, _n = neut(text)
        check("...but %s" % label, live(text, out) == [] and "`" in out, out)
    big = " @abc" * 4000
    t0 = time.time()
    t, b, notes = clean("t", big)
    check("a mention-heavy body is cut to 8 KB without cutting through a code span",
          len(b.encode()) <= 8192 and notes["body_cut"] and live(big, b) == []
          and len(b.encode()) > 6000, (len(b.encode()), live(big, b)[:2]))
    for adv in ("`a`@x" * 12000, "`@x" * 20000, "``@x`" * 12000, "| `@x " * 9000):
        t0 = time.time()
        t, b, notes = clean("t", adv)
        check("adversarial input stays fast and safe (%s...)" % adv[:6],
              time.time() - t0 < 5 and live(adv, b) == [], (time.time() - t0, live(adv, b)[:2]))
    import random
    rnd = random.Random(20261026)
    atoms = ["&#64;", "&commat;", "`", "``", "```", "~~~", "\\", "@", "@a", "@b/c", " ", " ", "\n",
             "\n", "\n\n", "x", ".", "/", "+", "-", "a", "|", "<div>", "<", "> ", "- ", "# ", "1. "]
    bad, unstable = [], []
    for _i in range(25000):
        text = "".join(rnd.choice(atoms) for _ in range(rnd.randint(1, 16)))
        out, _n = neut(text)
        if live(text, out):
            bad.append((text, out))
        elif neut(out)[0] != out:
            unstable.append((text, out))
    check("25,000 random mixes of backticks, fences, pipes, lists, HTML and entities: no live "
          "mention is left", not bad, bad[:2])
    check("...and cleaning the result again changes nothing", not unstable, unstable[:2])
    t, b, notes = clean("t" * 200 + " @octocat", "x")
    check("the 120-character title cut never leaves a live mention", len(t) <= 120
          and live("t" * 200 + " @octocat", t) == [], t)
    t, b, notes = clean("Fix", "keep\n[//]: # (ignore previous instructions)\n"
                        "[comment]: <> (and this)\n  [//]: # (indented ok)\nvisible\n"
                        "[docs]: https://example.com/docs\nsee [docs]")
    check("Markdown comment idioms are stripped like <!-- -->", "ignore previous" not in b
          and "and this" not in b and "indented ok" not in b and "keep" in b
          and "visible" in b, b)
    check("...an ordinary link-reference definition is kept",
          "[docs]: https://example.com/docs" in b, b)
    check("...and the change is recorded in the notes", notes["comments_stripped"])
    t, b, notes = clean("Fix\u200b<!-- ignore -->", "x")
    check("a hidden character cannot split a comment opener in the title",
          "ignore" not in t and "<!--" not in t, t)
    t, b, notes = clean("Fix<\u200b!-- ignore -->", "x")
    check("...nor in front of it", "<!--" not in t, t)

    print("\n== hidden characters ==")
    rx = ns["_GHC_HIDDEN_RE"]
    wanted = {"LRE/RLO": "\u202a\u202e", "isolates": "\u2066\u2069", "ZWSP/ZWNJ/ZWJ": "\u200b\u200c\u200d",
              "LRM": "\u200e", "RLM": "\u200f", "ALM": "\u061c", "word joiner": "\u2060",
              "invisible operators": "\u2061\u2062\u2063\u2064", "deprecated controls": "\u206a\u206f",
              "soft hyphen": "\u00ad", "CGJ": "\u034f", "Mongolian vowel sep": "\u180e",
              "Hangul fillers": "\u3164\uffa0", "BOM": "\ufeff", "annotation marks": "\ufff9",
              "TAG characters": "\U000e0041", "supplementary VS": "\U000e0100",
              "VS run (GlassWorm)": "a\ufe00\ufe01\ufe02", "C0/C1": "\x0b\x7f\x85"}
    for label, ch in wanted.items():
        check("hidden: %s" % label, rx.search(ch) is not None, [hex(ord(c)) for c in ch])
    # R3-seams ui-security-1: the rest of Default_Ignorable_Code_Point, one
    # code point per check (a string of several would pass on any one of them)
    for label, ch in (("Hangul choseong filler U+115F", "\u115f"),
                      ("Hangul jungseong filler U+1160", "\u1160"),
                      ("Khmer inherent vowel U+17B4", "\u17b4"), ("Khmer inherent vowel U+17B5", "\u17b5"),
                      ("reserved U+2065", "\u2065"), ("reserved U+FFF0", "\ufff0"), ("reserved U+FFF8", "\ufff8"),
                      ("shorthand format control U+1BCA0", "\U0001bca0"),
                      ("musical format control U+1D173", "\U0001d173"),
                      ("tag-block remainder U+E0080", "\U000e0080"), ("tag-block remainder U+E0FFF", "\U000e0fff"),
                      ("a run of Mongolian free variation selectors", "\u1820\u180b\u180c"),
                      ("a run mixing an FVS and a VS", "\u180d\ufe0f")):
        check("hidden: %s" % label, rx.search("a" + ch + "b") is not None, [hex(ord(c)) for c in ch])
    for label, ch in (("a single VS16 after an emoji", "\u2764\ufe0f"),
                      ("a keycap sequence", "1\ufe0f\u20e3"), ("tab/newline/CR", "a\tb\r\n"),
                      ("plain text, accents and CJK", "café 日本"),
                      ("one Mongolian free variation selector after a letter", "\u1820\u180b"),
                      ("NBSP", "a\u00a0b")):
        check("not hidden: %s" % label, rx.search(ch) is None, [hex(ord(c)) for c in ch])
    check("a Hangul choseong filler is marked",
          ns["_ghc_mark_hidden"]("exec(\u115f)") == "exec(⟦U+115F⟧)", ns["_ghc_mark_hidden"]("exec(\u115f)"))
    check("a VS run is marked char by char",
          ns["_ghc_mark_hidden"]("a\ufe00\ufe01b") == "a⟦U+FE00⟧⟦U+FE01⟧b")
    t, b, notes = clean("a\u200eb\u00adc", "a\u200eb\u00adc")
    check("title drops them, body shows them", t == "abc" and "⟦U+200E⟧" in b
          and "⟦U+00AD⟧" in b, (t, b))

    print("\n== the scans see what they used to skip ==")
    sb = H.prepare(ns, "octo/app")
    n = [0]

    def req(files, title="A change", body="", slug=None):
        reset(sb)
        for rel, data in files.items():
            H.write(sb, rel, data)
        n[0] += 1
        return H.request(ns, "octo/app", slug or ("r%03d" % n[0]), title=title, body=body)

    st, r = req({"src/mixed.py": b"x = 1\xff\n# \xe2\x80\xae evil\n"})
    check("a file with one stray non-UTF-8 byte still gets the hidden-character scan",
          st == 200 and only(r, "C-unicode") and only(r, "C-unicode")[0]["level"] == "warn", r)
    st, r = req({"src/mixed.py": b"x = 1\xff\ntoken = '" + BIG_TOKEN.encode() + b"'\n"})
    check("...and the credential-shape scan",
          any("mixed.py" in c["text"] for c in only(r, "C-secret-shape")), r.get("checks"))
    st, r = req({"tab\tname.py": "token = '" + BIG_TOKEN + "'\n"})
    check("a file NAME containing a tab is scanned as text (it used to read as binary and "
          "skip every content scan)", st == 200 and any(
              "tab" in c["text"] for c in only(r, "C-secret-shape"))
          and not only(r, "C-binary"), r.get("checks"))
    st, r = req({"docs/inv\u202eoice.txt": "hello\n"})
    names = [c for c in only(r, "C-unicode") if "file name" in c["text"]]
    check("a hidden character in a FILE NAME warns, shown visibly", st == 200 and names
          and names[0]["level"] == "warn"
          and "⟦U+202E⟧" in json.dumps(names[0]["samples"], ensure_ascii=False), r)
    # R3-seams ui-security-1: the invisible-identifier trick with the Hangul
    # filler U+115F (zero width, ID_Start) in an added line, U+1160 in a file
    # name, U+115F in the PR title
    st, r = req({"src/run.js": "const { timeout,\u115f} = req.query; exec(\u115f)\n",
                 "src/a\u1160b.js": "ok\n"}, title="Fix\u115f the timeout")
    added = [c for c in only(r, "C-unicode") if "file name" not in c["text"]]
    names = [c for c in only(r, "C-unicode") if "file name" in c["text"]]
    check("a Hangul filler (U+115F) in an added line warns (C-unicode), shown visibly",
          st == 200 and added and added[0]["level"] == "warn"
          and "⟦U+115F⟧" in json.dumps(added[0].get("samples"), ensure_ascii=False), r.get("checks"))
    check("...U+1160 in a FILE NAME warns too, shown visibly", names and names[0]["level"] == "warn"
          and "⟦U+1160⟧" in json.dumps(names[0].get("samples"), ensure_ascii=False), r.get("checks"))
    t, b, notes = clean("Fix\u115f the timeout", "x")
    check("...and the title drops it before it is pushed", t == "Fix the timeout", t)
    st, r = req({"blob.bin": b"\x00\x01PK\x00" + BIG_TOKEN.encode() + b"\x00"})
    check("a credential-shaped value inside a file git calls binary warns",
          any("binary" in c["text"] and "blob.bin" in c["text"] for c in only(r, "C-secret-shape")),
          r.get("checks"))
    check("...and the binary row is still listed", only(r, "C-binary"), r.get("checks"))
    st, r = req({"a.txt": "a\n"}, title="rotate " + BIG_TOKEN)
    check("a credential-shaped value in the TITLE warns",
          any(c["text"].startswith("The title") for c in only(r, "C-secret-shape")), r.get("checks"))
    st, r = req({"a.txt": "b\n"}, body="the key is\n" + BIG_TOKEN + "\n")
    check("...and in the DESCRIPTION", any(c["text"].startswith("The description")
                                           for c in only(r, "C-secret-shape")), r.get("checks"))
    lines = "".join("k%d = '%s'\n" % (i, "ghp_" + ("%036d" % i)) for i in range(26))
    st, r = req({"many.py": lines})
    shapes = only(r, "C-secret-shape")
    check("more findings than the cap: the overflow is said out loud (one more tick)",
          st == 200 and len(shapes) == 21 and shapes[-1].get("more") == 6
          and "more credential-shaped" in shapes[-1]["text"], [c["text"][:60] for c in shapes[-3:]])
    reset(sb)
    pem = b"-----BEGIN PRIVATE KEY-----\n" * 30000                  # ~830 KB, no END anywhere
    t0 = time.time()
    st, r = req({"keys.bin": b"\x00" + pem, "keys.txt": pem.replace(b"\n", b" ")})
    check("repeated BEGIN lines (the PEM regex is quadratic on them) do not stall the scan "
          "(binary blob or one huge text line)", time.time() - t0 < 20 and st == 200
          and only(r, "C-secret-pem"), (round(time.time() - t0, 1), st))
    reset(sb)

    gsec = "GOCSPX-" + "k3J9fQ2mZp7Lw5Xa8Rt1Yv4Nb6Hd"
    with open(os.path.join(ns["HOME"], ".hermes", "google_client_secret.json"), "w") as f:
        json.dump({"installed": {"client_id": "1234-abc.apps.googleusercontent.com",
                                 "client_secret": gsec}}, f)
    st, r = req({"conf/google.txt": "secret=" + gsec + "\n"})
    check("the Google connector's client_secret is a canary too (hard block)",
          st == 200 and only(r, "C-secret-known") and r.get("blocked") is True
          and gsec not in json.dumps(r), r.get("checks"))
    reset(sb)

    hd = os.path.join(ns["HOME"], ".hermes")
    os.makedirs(os.path.join(hd, "mcp-tokens"), exist_ok=True)
    asec, msec = "nous-" + "Zq8Lm3Vb7Kc2Xw9Rt4Yh1Pd6", "mcp-" + "Fj5Ng0Ba7Ue3Si9Ok2Wl8Cx4"
    with open(os.path.join(hd, "auth.json"), "w") as f:
        json.dump({"providers": {"nous": {"access_token": asec, "token_type": "Bearer"}}}, f)
    with open(os.path.join(hd, "mcp-tokens", "notion.json"), "w") as f:
        json.dump({"tokens": [{"refresh_token": msec}]}, f)
    for label, val in (("auth.json", asec), ("an mcp-tokens file", msec)):
        st, r = req({"conf/%s.txt" % label.split()[-1].replace(".", "_"): "x=" + val + "\n"})
        check("a secret from %s is a canary (hard block)" % label,
              st == 200 and only(r, "C-secret-known") and r.get("blocked") is True
              and val not in json.dumps(r), r.get("checks"))
    st, r = req({"conf/fine.txt": "token_type = Bearer\n"})
    check("...and a short non-secret value from the same file is not", st == 200
          and not only(r, "C-secret-known"), r.get("checks"))
    reset(sb)

    sec_at = "abcdef-@ghijklmnopqrstu"
    ns3 = H.load(w, home=ns["HOME"], secrets_env={"X_TOKEN": sec_at})
    H.write(sb, "raw.txt", "x\n")
    st, r = H.request(ns3, "octo/app", "raw-scan", title="rotate " + sec_at)
    check("a saved value that the mention wrapping would split is still found in the agent's "
          "RAW title (hard block)", st == 200 and r.get("blocked") is True
          and only(r, "C-secret-known"), (st, r.get("checks")))
    reset(sb)

    print("\n== a push GitHub could not confirm still tells the owner ==")
    H.write(sb, "src/login.py", "COPY = 'Unconfirmed'\n")
    st, r = H.request(ns, "octo/app", "unconfirmed", title="Unconfirmed")
    cid = r.get("id")
    real_verify = ns["_ghc_verify_head"]
    n_tg = len(ns["TELEGRAM"])
    ns["_ghc_verify_head"] = lambda api, full, branch, want: ns["_ghc_remote_head"](
        api, full, "main")               # a stale read: GitHub "does not show it yet"
    try:
        st, r = H.decide(ns, cid, "push_only")
    finally:
        ns["_ghc_verify_head"] = real_verify
    check("the push is reported failed (502 push_failed)", st == 502
          and r.get("error") == "push_failed", (st, r))
    check("...the branch DID land on the remote", "hermes/unconfirmed" in w.heads("octo/app"))
    check("...and a Telegram notice went out anyway (it may have happened)",
          len(ns["TELEGRAM"]) == n_tg + 1 and "could not confirm" in ns["TELEGRAM"][-1]
          and "octo/app" in ns["TELEGRAM"][-1] and "hermes/unconfirmed" in ns["TELEGRAM"][-1],
          ns["TELEGRAM"][n_tg:])
    check("...the ledger keeps the push row (so the drift check stays quiet)",
          ns["_gh_ledger_heads"]("octo/app").get("hermes/unconfirmed") ==
          w.heads("octo/app")["hermes/unconfirmed"])
    res = ns["_gh_run_check"](force=True)
    check("...and the drift check does not call it foreign", not any(
        d["branch"] == "hermes/unconfirmed" for d in res.get("drift_new") or []), res)
    check("...no token in the notice", not H.leaks(ns["TELEGRAM"], tok))
    # R3-seams github-1: the Settings card's Recent pushes lists a failed
    # change only WITH a pushed_sha (its "Unconfirmed" row); without one the
    # branch that may exist on GitHub was on no surface at all
    st, lst = ns["call"]("GET", "/api/github/changes")
    row = next((c for c in (lst.get("changes") or []) if c.get("id") == cid), {})
    check("...the list row keeps the sha that may have landed (state failed, pushed_sha = the commit)",
          st == 200 and row.get("state") == "failed"
          and row.get("pushed_sha") == w.heads("octo/app")["hermes/unconfirmed"], row)
    st, rr = ns["call"]("POST", "/api/github/changes/retract", {"id": cid}, headers=H.OWNER)
    check("...and it is still not retractable (state gates retract, not the sha)",
          st == 409 and rr.get("error") == "not_retractable", (st, rr))

    print("\n== rejecting never needs the Flight Recorder ==")
    reset(sb)
    H.write(sb, "docs/rej.md", "reject me\n")
    st, r = H.request(ns, "octo/app", "reject-me", title="Reject me")
    rid = r.get("id")
    ch = H.get_change(ns, rid)
    no_rec = H.load(w, recorder=False, home=ns["HOME"])
    st, r = no_rec["call"]("POST", "/api/github/changes/decide",
                           {"id": rid, "decision": "push_only", "digest": ch["digest"]},
                           headers=H.OWNER)
    check("approving without a recorder is still refused (503)", st == 503
          and r.get("error") == "recorder_unavailable", (st, r))
    st, r = no_rec["call"]("POST", "/api/github/changes/decide",
                           {"id": rid, "decision": "reject", "digest": ch["digest"]})
    check("...but rejecting works (it only reduces capability)", st == 200
          and r.get("state") == "rejected", (st, r))
    check("...and the change is rejected on disk", H.get_change(ns, rid)["state"] == "rejected")
    reset(sb)

    print("\n== a default branch called hermes ==")
    ns2 = H.load(w, home=ns["HOME"])
    ns2["_gh_repo"]                                      # connector loaded
    st, r = ns2["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/hb"},
                        headers=H.OWNER)
    st, r = ns2["call"]("POST", "/api/github/sandbox/prepare", {"repo": "octo/hb"})
    check("a repository whose default branch is `hermes` is refused up front (409), not a "
          "half-built mirror", st == 409 and r.get("error") == "bad_default_branch"
          or (st == 403 and r.get("error") == "not_enabled"), (st, r))
    row = {"full_name": "octo/hb", "enabled": True, "default_branch": "hermes"}
    real_conn, real_repo = ns2.get("_gh_connected"), ns2.get("_gh_repo")
    ns2["_gh_connected"] = lambda: True
    ns2["_gh_repo"] = lambda full: dict(row)
    try:
        st, r = ns2["call"]("POST", "/api/github/sandbox/prepare", {"repo": "octo/hb"})
    finally:
        ns2["_gh_connected"], ns2["_gh_repo"] = real_conn, real_repo
    check("...whatever the connector says about it", st == 409
          and r.get("error") == "bad_default_branch", (st, r))
    check("...and nothing was created on disk",
          not os.path.exists(ns2["_ghc_mirror_path"]("octo/hb")))

    print("\n== draft / already-exists 422s ==")
    rec = {"repo": "octo/app", "branch": "hermes/x", "default_branch": "main"}
    nested = StubResp(422, {"message": "Validation Failed", "errors": [
        {"resource": "PullRequest", "field": "draft",
         "message": "Draft pull requests are not supported in this repository."}]},
        "Validation Failed")
    ok201 = StubResp(201, {"number": 7, "html_url": "https://github.com/octo/app/pull/7"})
    api = StubAPI([nested, ok201])
    pr, err = ns["_ghc_open_pr"](api, rec, "Title", "Body")
    check("a draft 422 that only names draft in errors[] still retries as a ready PR",
          pr and pr["number"] == 7 and pr["draft"] is False and err is None
          and api.calls[1][2]["draft"] is False and api.calls[1][2]["title"] == "[Hermes] Title",
          (pr, err, api.calls))
    dup = StubResp(422, {"message": "Validation Failed", "errors": [
        {"resource": "PullRequest", "code": "custom",
         "message": "A pull request already exists for octo:hermes/x."}]}, "Validation Failed")
    mine = {"number": 3, "draft": True, "head": {"ref": "hermes/x"},
            "html_url": "https://github.com/octo/app/pull/3"}
    other = {"number": 9, "draft": False, "head": {"ref": "hermes/other"},
             "html_url": "https://github.com/octo/app/pull/9"}
    api = StubAPI([dup], gets=[StubResp(200, [other, mine])])
    pr, err = ns["_ghc_open_pr"](api, rec, "Title", "Body")
    check("an append-mode push adopts the branch's open PR (only the one for ITS head)",
          pr and pr["number"] == 3 and pr["existing"] is True and err is None
          and api.calls[1][2].get("head") == "octo:hermes/x", (pr, err, api.calls))
    api = StubAPI([dup], gets=[StubResp(200, [other])])
    pr, err = ns["_ghc_open_pr"](api, rec, "Title", "Body")
    check("...and with none it reports the failure", pr is None and "did not open" in err, err)
    api = StubAPI([StubResp(422, {"message": "Validation Failed"}, "Validation Failed")])
    pr, err = ns["_ghc_open_pr"](api, rec, "Title", "Body")
    check("an unrelated 422 is not mistaken for either", pr is None and len(api.calls) == 1, err)

    print("\n== the change store is bounded ==")
    ns["GHC_KEEP_MAX"] = 4
    now = time.time()
    states = ["rejected"] * 3 + ["withdrawn"] * 2 + ["failed", "pushed", "pr_open", "retracted",
                                                      "expired", "cancelled"]
    ids = []
    for i, stt in enumerate(states):
        cid = "ghc-%012x" % (0xabc000 + i)
        ids.append(cid)
        ns["_ghc_save"]({"id": cid, "state": stt, "repo": "octo/app", "branch": "hermes/s%d" % i,
                         "requested_at": now - 1000 + i, "expires_at": now + 99999,
                         "title": "t", "stat": {"files": 1, "add": 1, "del": 0, "bytes": 1}})
    pend = "ghc-%012x" % 0xdef000
    ns["_ghc_save"]({"id": pend, "state": "pending", "repo": "octo/app", "branch": "hermes/p",
                     "requested_at": now - 5000, "expires_at": now + 99999, "title": "t",
                     "stat": {"files": 1, "add": 1, "del": 0, "bytes": 1}})
    allr = ns["_ghc_all"]()
    left = {r["id"] for r in allr}
    check("finished records over the cap are removed (the cap counts every finished record)",
          len([r for r in allr if r["state"] not in ("pending", "pushing")]) == 4,
          [(r["id"][-4:], r["state"]) for r in allr])
    check("...a pending change is never removed, however old", pend in left)
    check("...pushed ones (what a retract needs) outlive the ones that never left the Mac",
          ids[6] in left and ids[7] in left, sorted(left))
    ns["GHC_KEEP_MAX"] = 300

    print("\n== the speed bump with server.py's real _hdr ==")
    src = os.path.join(H.DASH, "server.py")
    tree = ast.parse(open(src, encoding="utf-8").read())
    fn = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "_hdr"][0]
    real = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), src, "exec"), real)
    ns["_hdr"] = real["_hdr"]
    ns["ALLOWED_HOSTS"] = {"127.0.0.1:7788", "localhost:7788", "127.0.0.1", "localhost"}
    ctx = H.RouteCtx(body={}, raw_path="/x", headers=H.OWNER)
    check("a RouteCtx's lower-cased headers pass through the real _hdr",
          ns["_ghc_from_dashboard"](ctx) is True, ctx.headers)
    ctx = H.RouteCtx(body={}, raw_path="/x", headers=dict(H.OWNER, Origin="http://localhost"))
    check("...a port-less Origin that is not this Host is refused",
          ns["_ghc_from_dashboard"](ctx) is False)
    ctx = H.RouteCtx(body={}, raw_path="/x", headers=dict(H.OWNER, Host="evil.example:7788",
                                                           Origin="http://evil.example:7788"))
    check("...and a foreign Host/Origin pair is refused", ns["_ghc_from_dashboard"](ctx) is False)

    print("\n== nothing leaked ==")
    blob = json.dumps(ns["RESPONSES"], default=str)
    check("no response carries the token", not H.leaks(blob, tok))
    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
