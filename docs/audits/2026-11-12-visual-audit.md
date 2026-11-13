# Visual audit — 2026-11-12

Notes on what the app looks like today, what should change and what does not
make sense. This is a design review, not a code audit: nothing was changed.

## How this was done

- 38 screenshots of the live dashboard at commit `425e599`, taken read-only in
  headless Chromium (every non-GET request was blocked in the browser).
- Sizes: 1440×900, 1440×1500 for the Settings panels, 900×640 for the narrow
  layout. Dark theme for everything, light theme for four screens.
- Screens covered: Hub (chat split, chat hidden, chat full), Agent (Record and
  Screen rails), all twelve Settings panels, the model menu and its details,
  Customize, the Widget Center, the Weather / Markets / System pop-outs, and
  step 1 of first-run setup.

**Limits.** The app runs in WKWebView and these captures are Chromium, so glass
blur, font rendering and `<input type="time">` formatting may differ. Not
captured: the gear's Preferences dropdown, hover and focus states, motion, a
conversation with real messages and tool lines, an approval card, the GitHub
review sheet, Quick Ask, error states, and anything below the first 1,500 px of
a Settings panel. Findings marked *(check in app)* depend on one of these.

Ids are `V##` so they can be referred to later. **Broken** means a layout
defect, **Contradiction** means two parts of the screen disagree, **Unclear**
means the screen works but does not make sense to a reader, **Polish** is
everything smaller.

## The ten I would do first

1. **V01** — the Data & Network table in Settings › Connections is unreadable:
   two-word-wide columns, single rows up to 919 px tall.
2. **V11** — the app shows three different agent states at once ("sleeping",
   "Agent is running", "on watch").
3. **V02, V03** — three Settings cards have no inner padding and clip their own
   text.
4. **V21** — two of the twelve Settings entries open an empty placeholder.
5. **V12** — "Everything stays local." sits a click away from a table of 17
   things that leave the Mac.
6. **V23** — the Needs-you card, the top slot of the Hub, reads as a
   diagnostics panel when it is empty.
7. **V16** — in Proactive, schedules look switched on while their master switch
   is off.
8. **V13** — the model menu highlights a model that is not the active one.
9. **V24** — the chat and its controls are repeated: three places to pick a
   conversation, two composers on one screen.
10. **V09** — Recent fires shows raw Markdown link syntax.

## Broken

**V01 · Settings › Connections › Data & Network table.** Measured column widths
are Goes to 95 px, When 82 px, What 112 px, Last outbound 155 px, Switch 272 px.
The two columns that carry paragraphs get the least room, so text wraps at one
or two words per line and rows are 338, 919, 732 and 413 px tall; the Switch
column is mostly empty. The table is also 716 px wide inside a 673 px wrapper,
so it scrolls sideways by 43 px. This is the screen that is meant to make trust
visible, and nobody will read it in this state. A long how-to in one Switch cell
appears to set that column's width. Fix: give When and What most of the width
and let the Switch text wrap, or drop the table for stacked rows (destination
as a heading, one or two lines under it, the switch at the right).

**V02 · Settings › System & Data.** The "Code graph" and "Software update"
cards have no inner padding: the stat row and the "Hub nodes" list start at the
card border and are clipped on the left, and the "Explain" button runs past the
right edge. The file row in "Config as code" is cut off on the right
("exported Jul 5, 2:1…"). The Traces and Software update cards touch with no
gap, while the other cards on the page are spaced.

**V03 · Settings › Overview › First-run setup.** The title and description sit
flush against the left border, and the "last run" date wraps onto a second line
that the card clips.

**V04 · Clipped lists and inputs.** The "Ask Hermes about this…" box in the
Weather pop-out is cut by the bottom of the sheet. The Markets pop-out, the
Widget Center and the model menu all end in the middle of a row with no fade or
other sign that they scroll.

**V05 · Settings › Insights › Vitals.** Every tile's caption is cut with an
ellipsis ("p95 — · target <1…", "p50 31ms · targe…"), so the target each number
is judged against is never readable. The 5 + 3 tile layout leaves two holes.

**V06 · Model menu legibility.** The menu is translucent enough that the Hub
shows through its rows: Markets figures at 900 px wide, chat text at 1440.
*(check in app: WebKit's blur may hide more than Chromium's.)*

**V07 · Agent › right rail.** On Screen: the "On-screen activity" heading wraps
into two lines beside a cramped caption, the "Give the agent a Mac task"
placeholder is cut off, and "Send to agent" sits on the panel's bottom edge. On
Record: "0 reversible · 0 undone" runs to the card edge, and the card ends
halfway down a panel that is otherwise empty.

**V08 · Hub with the chat hidden.** The floating chat button covers the header
text of the GitHub Trending card.

**V09 · Settings › Proactive › Recent fires.** A fired brief is shown as raw
text: `[S&P 500](https://finance.yahoo.com/…)` and the like for more than
twenty lines. Render
the links, or show the titles only.

## Contradictions

**V11 · Agent state.** With the model idle-suspended, the header pill says
"sleeping", the Settings rail says "Pause agent — Agent is running" with a green
dot, and the Agent view says "Watching — your assistant is on watch" with a
green "On watch" line. Each is true of a different thing (the model, the pause
flag, the background loops), but a reader sees one assistant with three states.
Pick one vocabulary and use it everywhere: Asleep (wakes on your next message),
Awake, Paused.

**V12 · "Everything stays local."** The chat empty state ends with this
sentence. Settings › Connections lists 17 outbound paths. Say what is true:
"The model runs on this Mac."

**V13 · Model menu.** The highlighted, expanded row is stock Qwen3.8-27B while
the active model is the Uncensored build. The right-hand column mixes states and
actions ("sleeping", "switch", "download · 20.4 GB") in one style, so it is not
clear which is a button. Mark the active row, and make actions look like
buttons.

**V14 · Memory figures.** The System pop-out says 40 / 69 GB, the model advisor
says 64 GB, "Fits (25 of 48 GB)" and "GPU budget 52 GB", and the model menu says
"this Mac has 64 GB". 69 is decimal gigabytes and 64 is binary; 48 is the
advisor's plan limit and is never explained. Use one unit everywhere and say
once what the 48 is.

**V15 · Settings › Insights.** The Activity card is labelled "last 14 days"
over 7.9k messages and 8.0M tokens, while Model mix and Fuel on the same page
say there were no sessions in the last 14 days. The totals are lifetime; label
them so. The sparkline above them is a flat line.

**V16 · Settings › Proactive.** "Daily briefings" and "News updates" are off,
but the 8 AM, midday and evening switches and both breaking-alert switches under
them are drawn fully on. Dim the children while their master is off. Also: the
tile is titled "8AM World Brief" although its time is editable, "Midday pulse"
is set to 15:00, and the times display as 24-hour in a product whose rule is
12-hour *(check in app)*.

**V17 · Skill count.** Overview says Hermes "has learned 88 skills". The Skills
panel shows that 71 of them are in `.archive`.

**V18 · Temperature.** The Weather pop-out showed 51° while the widget and the
header pill showed 52° in the first capture run.

**V19 · One feature, five names.** "Claude Code · off" (header pill), "Claude
Bridge" (Settings entry), "Escalate to Claude" (the switch), "Claude
dialogues · 5" and a "Local | Claude" toggle (Agent view). At 900 px the header
pill shrinks to the single word "off". Choose one name.

## Unclear

**V21 · Empty Settings panels.** "Data & Sources" and "Appearance & Layout"
each open to "These controls arrive in a later update. Nothing is missing — this
panel just has no cards yet." Meanwhile the theme switch lives in the gear's
dropdown, and tickers, feeds and clocks are set elsewhere. Hide a panel until it
has content, or move those controls into it.

**V22 · Header.** The left half is empty. "Add" is the only filled, coloured
control and it is for the rarest action. The date pill repeats the Clock widget
and the weather pill repeats the Weather widget. The pills all carry the same
weight and sit in one row with no grouping. Settings is a gear, while Hub and Agent are tabs;
the Console and Desktop tabs still exist in the page but are hidden, and
`CLAUDE.md` still describes a Hub / Settings / Console control.

**V23 · Needs you.** The widest, topmost card. Empty, it shows "Nothing needs
you — but Messages is not being read", "Nothing scheduled — briefings are off.",
seven source chips of which four are greyed to near-invisibility, "Never 0",
"now precision — not enough data yet" and an unexplained "clear". That is a
status report about the inbox's plumbing. When nothing needs the owner, say so
in one line and put source problems behind one "2 sources are off" link.

**V24 · The chat is everywhere, several times.** It takes the right third of
the Hub and of every Settings panel, and the centre of Agent. In Agent a
conversation can be picked from the sidebar list, from a dropdown above the
chat and through a search icon; there are two "+" buttons; the quick-action
chips repeat the sidebar's Tools list and wrap to leave one chip alone on a
second row. The Screen rail adds a second composer ("Give the agent a Mac
task") next to the main one, with nothing saying how the two differ. The
toolbar in the Hub's chat is six unlabelled icons, with delete beside new.

**V25 · Settings › System & Data is a developer page.** "Code graph" lists this
repository's own files (`aux_watchtower.py`, and `motion.min.js`, which no
longer exists) and asks for "a function, file, or symbol". "Config as code"
shows "Layout drifted" and "Models drifted" in amber without saying what
drifted from what or what to do. Traces offers OTLP export. "Preview restore"
is a button that looks like a label. Put these behind a collapsed "Developer"
group.

**V26 · Settings › Memory & You-Model.** Two memory systems with the same
category names sit one above the other (tabs "INTERESTS, GOALS, PREFERENCES…"
and then "Goals, Now, Looking for…" again). Each area shows a meter like
"142 / 4000" beside "0 entries". Descriptions repeat their own heading and use
"§ entry". Tab labels mix "Core facts" with upper-case names. "Core memory can
be emptied, never deleted." needs a reason or should go.

**V27 · Settings › Skills.** Categories are raw folder names
(`.archive`, `autonomous-ai-agents`). Archived skills are listed first by
default. Every row has a purple dot with no meaning given. The list pages eight
at a time ("Show 8 more (80 left)").

**V28 · Settings › Permissions & Trust.** The clearest panel in the app. Three
things: a second "HIGH" group appears after "MEDIUM"; severity is shown twice
(group heading and a badge on every row); and the text column starts at a
different x in each group because the badges differ in width. The greyed "Auto"
with a small lock is not explained.

**V29 · Settings › Agent & Models › Model advisor.** Good content, said three
times: each plan gives its numbers in the headline, again in the sentence under
it and again in small print. Selected answers use two different styles (outlined
chips with a tick, and a filled segment). "614 GB/s bandwidth" means nothing to
the owner.

**V30 · Hub widgets.** Clock, Weather and System sit in fixed-height cards that
are about 40% empty. For You shows three 15-day-old items tagged "generic", each
with Useful / Noise buttons, then a dashed "Generic picks for now…" note and
blank space. News Feed prints the same source name on every row. Markets shows
4 of 12 symbols with no sign of more. Battery leaves a hole in the grid beside
it. Message Center is a card whose whole content is "Full Disk Access needed".

**V31 · Customize and the Widget Center.** Customize adds dashed outlines and ×
buttons but no drag handles, no "Done", and no pressed state on its own button.
In the Widget Center, widgets already on the Hub are dimmed until they are hard
to read and are mixed in with the ones that can be added.

**V32 · Settings › Insights.** "Prewarm" is listed as a platform beside Cli, Hub
and Telegram; it is an internal warm-up turn. Ranks 4–10 are numbers inside
circles too small to read. Skill names are cut off ("apple/osascript-c…") beside
a wide empty bar track. A "Memory files" list sits inside the "Skills in action"
card.

**V33 · System pop-out.** "Top by memory" bars are scaled to the largest row,
so 2.5% draws a full bar, while "Top by CPU" bars are on a 0–100 scale. The
charts are titled "30 min" but hold a few minutes of data squeezed into the
right corner. "Macintosh HD" and "Data" show identical numbers (they share one
container).

**V34 · First-run setup, step 1.** The first thing a new owner meets is the
same 17-row table of paragraphs. It is readable here, but it is a long legal
read before anything has happened. Lead with the three sentences above it and
put the table behind "See all 17".

## Polish

- **V35** — low contrast throughout the dark theme: disabled chips, timestamps,
  10 px upper-case labels, "local only" in the Screen rail.
- **V36** — the Overview headline is the only serif text in the app.
- **V37** — pop-out subtitles read like developer notes ("Right-now conditions
  plus the hours and week ahead for your area.").
- **V38** — in the light theme the Needs-you chips wrap and leave "GitHub — not
  connected" alone on a second line.
- **V39** — the Bridge panel is one switch on an otherwise empty page, under the
  subtitle "When and how the deep brain engages".
- **V40** — Traces offers a filled "Download JSONL" button when nothing was
  recorded.

## What holds up

The Markets, Weather and System pop-outs; the Permissions panel; the light
theme, which matches the dark one screen for screen; the 900 px layout, which
drops the side panels cleanly; and the model advisor's substance.

## Suggested order of work

1. Layout defects: V01, V02, V03, V04, V05, V07, V08, V09.
2. One state vocabulary and one name per feature: V11, V13, V19, V12.
3. Hide or fill what is empty: V21, V23, V30.
4. Settings clean-up: V16, V15, V17, V25, V26, V27.
5. Chat duplication (V24) and the header (V22) — these need a decision about
   what the three views are for before any pixels move.

## Implementation status (branch `fix/visual-2026-11-12`)

The "Broken" findings were fixed the same day by two implementers in separate
worktrees (Settings cards; shell and Agent view), each fix measured before and
after with the capture tool and checked again on the merged branch. Unit tier
on the merged branch: 17,530 passed, 0 failed, 0 skipped (strict). Nothing here
has been looked at in the app's WebKit window yet.

| Id | Status | What changed | Before → after |
|---|---|---|---|
| V01 | fixed | In the card, each destination is a two-line row: name, last outbound and switch on top; When and What side by side below. ARIA table roles are set after render. The first-run sheet keeps its table. | Sideways scroll 43 px → 0; tallest row 919 → 252 px at 1440 wide (309 px at 900) |
| V02 | fixed | Code graph and Software update get the padded card body and mount inside the panel's spacing column; the Config file row wraps. Software update now sits above Traces. | Content inset 1 → 15 px; gaps 15/30/0/0 → 15 px throughout |
| V03 | fixed | First-run setup card padded; the "last run" date stays on one line. | Inset 1 → 15 px; date no longer clipped |
| V04 | fixed | The pop-out's ask row is pinned to the bottom of the sheet. The model menu, Widget Center and pop-outs show a short fade while more lies below (`scrollCue()`). | Ask row bottom 849–952 → 836 in an 837 px sheet |
| V05 | fixed | Captions wrap; four tiles per row (two when narrow). Figures unchanged. | Captions cut 4 of 8 → 0; rows 5 + 3 → 4 + 4 |
| V06 | fixed | Model and Preferences menu surface 92% → 98% opaque, both themes. | Contrast of text seen through the menu: 17 → 4 grey levels (dark) |
| V07 | fixed | Screen rail: caption on its own line, taller task box, compacted empty states. Record rail: counts wrap under the title. | "Send to agent" 831–866 → 773–808 in an 876 px pane |
| V08 | partly fixed | 60 px of clearance at the end of the Hub in hidden-chat mode. | At end of scroll the last card ends 8 px above the button; mid-scroll the button still floats over a card |
| V09 | fixed | `[text](url)` in a fired brief is drawn as a link showing the text; only absolute http(s) URLs that pass `httpUrl()` link, the rest stays escaped text. | Rows with raw Markdown 10 → 0; tallest row 414 → 180 px |

New suites: `t_v01_network_table_roles.js`, `t_v02_settings_card_mounts.js`,
`t_v04_scroll_cue.js`, `t_v09_wt_recent_links.js` (each fails on the old files).

**Still open from this list**

- V08 at rest: a floating button over a scrolling grid always covers something.
  The real fix is to move "Show chat" into the header and drop the floating
  button — an owner decision, not done.
- V06: 98% opacity was chosen over restructuring the header so that menus can
  blur what is under them.
- `aux_doctor.js` still mounts outside the panel's spacing column and is spaced
  correctly only because of where it happens to land.
- To check in the app: the container query on Vitals, the sticky ask row with
  its own blur, the sticky fade inside padded scrollers, keyboard focus in the
  model menu across a 30 s rebuild, and how VoiceOver reads the Connections rows.

Everything under Contradictions, Unclear and Polish (V11–V40) is untouched.

## Second pass: tested in WebKit, and a contrast sweep (same day)

**WebKit.** The fixes were re-tested in the system WebKit, the engine the app's
window uses, with a small read-only harness (an off-screen `WKWebView`; every
non-GET request refused in the page). Same results as Chromium:

| What | Result in WebKit |
|---|---|
| V01 Connections | No sideways scroll (673 in 673 px; 585 in 585 at 900 wide); tallest row 262 px (316 at 900); all 17 rows are grids with table roles; every switch inside the card. First-run sheet still a real table, 680 in 680 px |
| V02 System & Data | Card gaps 15 / 15 / 15 px; content inset 15 px in Config, Code graph and Software update; nothing runs past a card edge |
| V04 ask box, scroll cue | Ask row sticky, its bottom 1 px above the sheet's; the cue is on at the top and off at the end of the scroll in the model menu, the Widget Center and the Markets pop-out |
| V04 model-menu rebuild | With a row focused, `loadModels()` rebuilt the menu: focus stayed on the same model, one cue, still the last child; three forced `innerHTML +=` rebuilds left one cue |
| V05 Vitals | 8 tiles in rows of 4 + 4, no caption cut, at 1440 and 900 wide |
| V06 menus | Surface alpha 0.98 in both themes; the Preferences dropdown matches |
| V07 Agent rail | Task box shows its whole placeholder (98 of 98 px); "Send to agent" at 769–804 in an 876 px pane; Record counts end at x = 1255 inside a 1412 px header |
| V08 hidden chat | At the end of the scroll the last card ends at 828, the button starts at 836 |
| V09 Recent fires | 24 links, 0 rows of raw Markdown, every `href` is `#`; a click opened exactly the `https://` URL through `window.open` |

Still not verifiable this way: anything that depends on backdrop blur. WebKit's
snapshot call does not draw `backdrop-filter`, so the pop-out sheet (a 13%
tint that relies on blur) looks transparent in a snapshot although its computed
style is correct. Glass legibility has to be judged in the app window itself.
VoiceOver was not run.

Two corrections to the notes above: the Proactive times (V16) are plain text
fields, so the 24-hour display is the app's own and is the same in WebKit; and
the model menu in WebKit is taller than in Chromium (822 px visible of 1,675).

**Contrast sweep.** Every screen, both themes, each control's label against the
colour actually behind it. Two new findings, both fixed on branch
`fix/visual-contrast`:

- **V41 — "Add watch" (Settings › Proactive) was close to invisible.** It is a
  tinted button that inherited the label colour meant for a solid accent fill:
  white on pale lavender in light (1.43:1), near-black on dark blue-grey in dark
  (2.11:1). It now uses the page ink.
- **V42 — white labels on the accent colour in the dark theme.** The accent is
  a light lavender in dark, and 15 rules in 13 modules hard-coded a white label
  on it (2.35:1): "Download JSONL", "Run checks", the context card's "Apply",
  the update, evals, dictation, prompt-budget, tool-budget and memory buttons,
  two count badges and the Google step dot. They now use `--iris-ink`, as the
  main buttons already did. Light theme is unchanged.
- Also adjusted: the "Claude dialogues" pill in the light theme (2.54:1 → its
  label is now mixed 35% toward the page ink).

After the fixes the sweep finds nothing below 3:1 except two disabled buttons.
New suite `t_v41_on_accent_ink.py` (15 failures on the old files) fails if any
rule pairs an accent background with a hard-coded white label again. The sweep
covers controls (buttons, tabs, chips, links, inputs), not body text, so the
low-contrast captions in V35 are still open.

## UI council, three rounds (same day)

The `/ui-hill-climb` skill, with three blind judges per round (Opus, Sonnet and
Fable at medium effort) scoring a shuffled panel of screenshots: seven Hermes
screens, five each from the Home Assistant demo, Grafana Play and Linear's public
site, and from round 2 the original Hermes screens as an unlabelled control.
Categories: hierarchy, typography, colour contrast, spacing and layout,
iconography, consistency. Implementers were never told the scores.

| Round | Hermes (Opus / Sonnet / Fable) | Control: original UI | Home Assistant | Linear | Grafana |
|---|---|---|---|---|---|
| 1 (original UI) | 6.2 / 6.4 / 6.0 | — | 7.6 / 7.5 / 7.6 | 8.6 / 8.3 / 8.7 | 5.9 / 6.6 / 6.8 |
| 2 | 6.9 / 7.4 / 7.0 | 6.1 / 6.7 / 6.0 | 7.9 / 7.6 / 7.8 | 8.3 / 8.1 / 8.3 | 6.5 / 6.6 / 6.5 |
| 3 | 7.2 / 7.5 / 6.8 | 6.4 / 6.7 / 6.0 | 7.7 / 7.8 / 7.7 | 8.7 / 8.7 / 8.9 | 6.0 / 7.0 / 6.8 |

- Rounds 1–2 moved every judge by +0.7 to +1.0 over the control. Round 3 held
  that margin (+0.8 with all three) and did not add to it: the climb stalled.
- Linear's unchanged screenshots scored 0.4–0.6 higher in round 3 than in
  round 2, so the "gap to the best reference" (median −2.4, −1.35, −1.6) is
  noisier than the margin over the control.
- Caveats: Linear's panel is its marketing site; the Home Assistant demo is in
  its light theme; judges recognised the products and saw that two options were
  builds of one product, so the blinding is partial. Captures are Chromium.
- Gates held throughout: no new axe violation, page error, failed request or
  overflow against the baseline (one known `label-content-name-mismatch` on the
  Agent screen).

What changed is in CHANGELOG ("UI council"). Findings above that the council
closed or partly closed: V11 (one state vocabulary: Awake, Asleep, Paused),
V13 (the highlighted model is the one in use; actions look like buttons), V23
(Needs-you empty state), V24 in part (chat collapsed on Settings), V28 in part
(titles aligned, keyboard operation), V29 in part, V30 in part (tile foot lines,
For You), V35 (hub and chat text sizes and contrast), V36 (no serif).

**Still raised by the judges after round 3, each waiting on an owner decision**

- The "no refusals" warning repeats on every uncensored model row (3 of 3):
  one group heading stating it once, with the badge kept on each row.
- Severity is shown twice in Permissions, section heading and pill (2 of 3).
- Clock and Weather cards have a dead band (2 of 3): allow them to be shorter
  than their row.
- The header: several pill styles and an empty left half (1 of 3; V22).
- Smaller: the Record rail's long retention note, truncated conversation
  titles, the advisor's run-on summary sentence.
