#!/usr/bin/env python3
"""t_e27_dictation_negation.py — E27 and E107 (2026-11-09 audit).

E27: the default "rules" cleanup turned "Don't send the email, no wait, send
the email" into "Don't send the email" — the splice kept the negation the
speaker had just taken back.  The verifier's 20 realistic corrections are all
here: no output may carry the opposite meaning.  A negated case is either left
exactly as spoken or corrected to the un-negated phrase; as-spoken is always
acceptable, meaning-flipped never is.

E107: filler removal deleted real words ("the ER", "AH-64", "Er"), turned
"uh-huh"/"uh-oh" into "-huh"/"-oh", dropped a "Mhm" answer — in every style,
verbatim included.  Fillers now match case-sensitively (never ALL CAPS), a
hyphen is part of the word, "mhm" is an answer, and verbatim strips nothing.

Runs the REAL module, exec'd the way server.py does.  Pure function, offline.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(HERE)))
MODULE = os.path.join(REPO, "dashboard", "aux_dictation.py")
PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got in want if isinstance(want, (set, tuple, list)) else got == want
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL %s\n       got:  %r\n       want: %r" % (name, got, want))


SANDBOX = tempfile.mkdtemp(prefix="hermes-e27-")
NS = {"__name__": "aux_dictation", "HOME": SANDBOX, "DATA": SANDBOX,
      "HERE": os.path.join(REPO, "dashboard"),
      "SETTINGS_FILE": os.path.join(SANDBOX, "settings.json"),
      "register_get": lambda p, f: None, "register_post": lambda p, f: None}
with open(MODULE) as f:
    exec(compile(f.read(), MODULE, "exec"), NS)
clean = NS["dictation_clean_rules"]


def spoken(text, style="prose"):
    """What the cleanup gives when it changes no word."""
    if style == "verbatim":
        return text
    out = text[0].upper() + text[1:]
    if style == "prose" and out[-1] not in ".!?":
        out += "."
    return out


def fmt(text, style="prose"):
    return spoken(text, style)


# (spoken text, acceptable outputs besides as-spoken, the meaning-flipped output)
NEG = [
    ("Don't send the email, no wait, send the email", [], "Don't send the email."),
    ("Do not delete the folder, scratch that, delete the folder", [], "Do not delete the folder."),
    ("I won't be at the meeting, I mean, be at the meeting at noon", [],
     "I won't be at the meeting at noon."),
    ("We should never sell the shares. No wait, sell the shares now.", [],
     "We should never sell the shares now."),
    ("Please don't approve the invoice, actually, approve the invoice", [], None),
    ("She is not coming tomorrow, no wait, coming tomorrow after all", [],
     "She is not coming tomorrow after all."),
    ("Tell him not to call the client, I mean, to call the client today", [],
     "Tell him not to call the client today."),
    ("I can't join the call, sorry I meant, join the call at three", [],
     "I can't join the call at three."),
    ("Don't turn off the alarm. Scratch that, turn off the alarm.", [], "Don't turn off the alarm."),
    ("Never mind the report, no wait, mind the report deadline", [], "Never mind the report deadline."),
    ("Don't send the email to Sarah, no wait, send the email to John", [],
     "Don't send the email to John."),
    ("Do not call before nine, I mean, call before ten", [], None),
    ("I don't think we should go, no wait, we should go", [], "I don't think we should go."),
    ("We are not ready to ship, actually, ready to ship tomorrow", [], None),
    # the reviewer's five
    ("Please do not merge the pull request, scratch that, merge the pull request", [],
     "Please do not merge the pull request."),
    ("I can't make it tonight, sorry I meant, make it tomorrow", [], "I can't make it tomorrow."),
    ("I'm not coming to dinner, no wait, coming to dinner after all", [],
     "I'm not coming to dinner after all."),
    # neighbours: other spellings of a negator, curly apostrophe, a negator
    # far back in the kept part, a sentence-leading correction
    ("I cannot send the report, no wait, send the report", [], "I cannot send the report."),
    ("I don’t want the red, no wait, want the blue", [], "I don’t want the blue."),
    ("dont ship it on friday, no wait, ship it on monday", [], "Dont ship it on monday."),
    ("Nobody should open the door, no wait, open the door now", [],
     "Nobody should open the door now."),
    ("We will never merge the branch. Scratch that, merge the branch today.", [],
     "We will never merge the branch today."),
    # review: a negation elsewhere in the correction must not let the withdrawn
    # one through ("not the text" is about something else)
    ("Don't send the email, no wait, send the email, not the text", [],
     "Don't send the email, not the text."),
    ("Don't delete the logs, no wait, delete the logs but not the backups", [],
     "Don't delete the logs but not the backups."),
    ("Do not call Sam, scratch that, call Sam and not Alex", [],
     "Do not call Sam and not Alex."),
    ("We won\u2019t ship it. No wait, ship it, never mind the docs.", [],
     "We won\u2019t ship it, never mind the docs."),
    # review: a correction that ADDS a negation is never dropped
    ("Send the email, no wait, don't send the email", [], "Send the email."),
    ("Ship it on Friday, no wait, don't ship it on Friday", [], "Ship it on Friday."),
    ("Charge the card, no wait, don't charge the card, refund it", [],
     "Charge the card, refund it."),
]
POS = [
    ("Send it to Sarah, no wait, send it to John", "Send it to John."),
    ("Meet me at five, I mean, at six", "Meet me at six."),
    ("Book the table for four. Scratch that, book the table for six.", "Book the table for six."),
    ("The file is in the downloads folder, actually, in the documents folder", None),
    ("Call me on Monday, no wait, call me on Tuesday", "Call me on Tuesday."),
    ("meet me on Tuesday, I mean, on Wednesday", "Meet me on Wednesday."),
]

print("E27 — no correction may flip a negation")
for style in ("prose", "casual", "verbatim"):
    for text, extra, flipped in NEG:
        out = clean(text, style)
        ok = [spoken(text, style)] + [fmt(e, style) for e in extra]
        check("[%s] %s" % (style, text), out, ok)
        if flipped:
            check("[%s] not flipped: %s" % (style, text),
                  out.rstrip(".").lower() != flipped.rstrip(".").lower(), True)
for text, want in POS:
    out = clean(text)
    check("still handled: %s" % text, out, spoken(text) if want is None else want)

print("E27 — milk/eggs: corrected or left as spoken, never flipped")
t = "Don't forget the milk, scratch that, the eggs"
check("milk/eggs", clean(t), {"Don't forget the eggs.", spoken(t)})

print("E27 — a negation the correction REPEATS still corrects")
check("negation repeated", clean("don't send it to Sarah, no wait, don't send it to John"),
      "Don't send it to John.")
check("an added negation is kept", clean("I do want it, no wait, I do not want it"),
      "I do not want it.")

# ---------------------------------------------------------------------------
print("E107 — fillers are hesitation sounds, not words")
for style in ("prose", "casual", "verbatim"):
    for text in ("Take him to the ER right now", "So the AH-64 is a helicopter",
                 "Er is the symbol for erbium", "Uh-huh, that works for me",
                 "Uh-oh, the build broke", "uh-huh", "Mhm", "mhm",
                 "Did you get the package? Mhm.", "echo ER", "UM is a university",
                 "the ah-ha moment", "hmm-hmm, sure"):
        check("[%s] %s" % (style, text), clean(text, style), spoken(text, style))
check("[verbatim] fillers stay in verbatim", clean("uh make clean", "verbatim"), "uh make clean")
check("[verbatim] um stays", clean("um, git commit dash m fix", "verbatim"),
      "um, git commit dash m fix")
check("prose still drops a lower-case uh", clean("this is, uh, a test"), "This is a test.")
check("prose still drops a sentence-case Um", clean("Um, this is a test."), "This is a test.")
check("prose still drops hmm", clean("hmm let me think"), "Let me think.")
check("casual still drops um", clean("um, on my way", "casual"), "On my way")

shutil.rmtree(SANDBOX, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
