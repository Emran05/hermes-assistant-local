#!/usr/bin/env python3
"""t_b16_c124_dictation_rules.py — the default "rules" cleanup must not change
what was said.

2026-10-08 audit B16 and 2026-10-21 audit C124: `dictation_clean_rules`, which
runs on EVERY dictation by default, deleted the sentence before "I meant to…",
collapsed "had had", deleted "you know" mid-question, treated "actually no" and
"wait no one" as self-corrections, dropped "kind of like", collapsed repeated
numbers and split "3.5" into "3. 5" — in verbatim (Terminal) style too.

Every case below is run through the REAL module, exec'd the way server.py
does.  Pure function, no settings, no I/O: the sandbox namespace only has to
satisfy the module-level constants.

    python3 t_b16_c124_dictation_rules.py [-v]
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(HERE)))
MODULE = os.path.join(REPO, "dashboard", "aux_dictation.py")

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        if VERBOSE:
            print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s\n       got:  %r\n       want: %r" % (name, got, want))


SANDBOX = tempfile.mkdtemp(prefix="hermes-b16-c124-")
NS = {
    "__name__": "aux_dictation",
    "HOME": SANDBOX,
    "DATA": SANDBOX,
    "HERE": os.path.join(REPO, "dashboard"),
    "SETTINGS_FILE": os.path.join(SANDBOX, "settings.json"),
    "register_get": lambda p, f: None,
    "register_post": lambda p, f: None,
}
with open(MODULE) as f:
    exec(compile(f.read(), MODULE, "exec"), NS)
clean = NS["dictation_clean_rules"]


def same(name, text, style="prose"):
    """The cleanup may tidy case and the closing stop, never the words."""
    out = clean(text, style)
    want = text if style == "verbatim" else text[0].upper() + text[1:]
    if style == "prose" and want[-1] not in ".!?":
        want += "."
    check(name, out, want)


# ---------------------------------------------------------------------------
print("B16 — 'I meant' and 'had had'")
same("'I meant to' does not delete the sentence before it",
     "I wanted to tell the whole team about the launch. I meant to call you yesterday.")
same("even set off by a comma, a one-word anchor cannot replace eight words",
     "I wanted to tell the whole team about the launch, I meant, to call you")
same("had had", "she had had enough")
same("that that", "I know that that is true")
same("I mean it", "I mean it")
same("I meant it", "I meant it")
same("what I mean is", "what I mean is simple")
same("trailing 'what I meant'", "that is what I meant")
check("a comma-set 'I mean' still corrects",
      clean("meet me on Tuesday, I mean, on Wednesday"), "Meet me on Wednesday.")

# ---------------------------------------------------------------------------
print("C124 — 'you know' only as an aside")
same("Do you know where", "Do you know where she went")
same("As you know", "As you know the deadline moved")
same("Did you know that", "Did you know that")
same("you know what I mean", "you know what I mean")
same("I know you know it", "I know you know it")
check("comma-wrapped aside goes", clean("it is, you know, fine"), "It is fine.")
check("sentence-initial aside goes", clean("You know, it works"), "It works.")
check("trailing aside goes", clean("it works, you know"), "It works.")
check("aside after a full stop goes",
      clean("It is late. You know, we should go"), "It is late. We should go.")

print("C124 — 'kind of like' / 'sort of like' are comparisons")
same("kind of like", "it is kind of like a dog")
same("sort of like", "it is sort of like a dog")

# ---------------------------------------------------------------------------
print("C124 — markers that are not corrections")
same("'actually no' is the answer", "The answer is actually no")
same("'Actually no.' answers the question before it", "Is it done. Actually no.")
same("'Wait no one' does not replace the previous sentence",
     "I was going to send it. Wait no one told me")
same("'Wait no one' as the first sentence", "Wait no one told me")
same("'No, sorry,' answering", "I sent it yesterday. No, sorry, I did not see it.")
same("'no, sorry' with nothing to anchor", "The answer is no, sorry, yes")
same("'no wait' as a noun phrase", "There's no wait time")
same("'scratch that' as a verb", "I'll scratch that off the list")
same("'or rather' with no anchor keeps both", "red or rather crimson")
same("'or rather' with no anchor keeps the subject", "he said or rather shouted")
same("a marker with nothing after it is kept", "I'll be there at five, no sorry")
# A lone pronoun after the marker starts a new clause; it is not an anchor.
same("'wait, no' inside a question", "Should I wait, no I should go")
same("'no, sorry' quoted inside a sentence", "I told him no, sorry, I can't make it")
same("a pronoun anchor would splice two clauses", "put it in the box, no wait, it goes in the drawer")
same("'no sorry' answering inside one sentence", "Can you send it, no sorry, you should send it")
same("'I mean,' restating with a pronoun", "it is fine, I mean, it works")

print("C124 — real corrections still work")
check("anchored 'or rather'", clean("the blue one or rather the green one"),
      "The green one.")
check("selftest transcript (cross-sentence, three-word anchor)",
      clean("Um, this is a test of the local dictation system. "
            "No, wait, the local dictation pipeline."),
      "This is a test of the local dictation pipeline.")
check("no-comma 'no wait'",
      clean("send it to the blue folder no wait the green folder"),
      "Send it to the green folder.")
check("two-word anchor, last occurrence",
      clean("open the local file, no wait, the local folder"),
      "Open the local folder.")
check("a pronoun still anchors as part of a longer anchor",
      clean("I want the red one, no wait, I want the blue one"), "I want the blue one.")
check("a contraction is not a lone pronoun",
      clean("It's red, no wait, it's blue"), "It's blue.")
check("two corrections in one sentence",
      clean("put it in the red box, no wait, the blue box, no wait, the green box"),
      "Put it in the green box.")

# ---------------------------------------------------------------------------
print("C124 — repeated words that were meant")
same("digit groups", "call 555 555 1234")
same("a number sequence", "the sequence is 1 1 2 3 5 8")
same("very very", "it was very very good")
same("no no no", "no no no do not do that")
same("on on (phrasal verb + preposition)", "turn the lights on on Monday")
same("to to", "who did you talk to to get this")
same("a place name", "we flew to Bora Bora")
check("a real stutter still goes", clean("the the file is open"), "The file is open.")
check("I I I still goes", clean("I I I think so"), "I think so.")
check("and and still goes", clean("salt and and pepper"), "Salt and pepper.")

print("C124 — verbatim (Terminal) is data")
same("ls -a a", "ls -a a", "verbatim")
same("echo the the", "echo the the", "verbatim")
same("cd ../src", "cd ../src and run it", "verbatim")
same("a version number", "pip install foo==3.5.1", "verbatim")

print("C124 — a dot inside a token is not a sentence end")
same("3.5", "version 3.5 is out")
check("$4.99 (casual)", clean("the price is $4.99 today", "casual"),
      "The price is $4.99 today")
same("example.com", "go to example.com now")
check("a real sentence end still capitalises",
      clean("hello there. how are you"), "Hello there. How are you.")
check("'?' then a new sentence", clean("are we done? yes we are"),
      "Are we done? Yes we are.")

print("cost — a pathological transcript stays cheap")
t0 = time.time()
clean("." * 8000)
clean(("no wait the " * 700)[:8000])
elapsed = time.time() - t0
check("8000-char pathological inputs under 2 s (%.2fs)" % elapsed, elapsed < 2.0, True)

shutil.rmtree(SANDBOX, ignore_errors=True)
print()
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
