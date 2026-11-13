// aux_github.js — the GitHub sandbox UI (round 3, cluster R3-GH3).
//
// docs/plans/github-sandbox-2026-10.md §1.2 (owner flow), §1.3 (exact copy),
// §4 (UI surfaces), §5.1 (routes, consumed here), §7 (UI tests). Backend:
// aux_github.py (the connector: connect / status / pause / revoke / check)
// and aux_ghchanges.py (the change pipeline: changes / decide / retract).
//
// FOUR SURFACES, ONE FILE
//   (a) Settings › Connections › GitHub card. Mounted DIRECTLY into
//       #sec-connections (the aux_network.js rule: never the #view-mind
//       fallback, which the shell's relocator would send to System & Data).
//       Not connected: the connect wizard. Connected: account + expiry, the
//       Pause switch, Disconnect, Revoke now, repositories with their chips,
//       sandbox paths, the recommended GitHub rule, other GitHub access on this
//       Mac, changes waiting for review, recent pushes and drift alerts.
//   (b) The full-viewport REVIEW SHEET (#gh-review): the whole diff, every
//       check, the PR text exactly as it will be sent, and the three
//       decisions. It inherits the app palette (index.html declares it in all
//       four blocks); no token is re-declared here.
//   (c) A Hub toast "Hermes has a GitHub change for you" when the status
//       reports a NEW pending change, plus the #github-review=<id> deep link.
//   (d) The onboarding step `github`, registered through the onboarding
//       extra-steps hook (window.hermesOnboardingSteps /
//       hermesOnboarding.registerStep) — aux_onboarding.* is never edited.
//   The connect wizard is ONE component (connectHTML + wireConnect) shared by
//   the card and the onboarding step (§1.2: "share one component").
//
// TOKEN CUSTODY (client side). The token is read out of its password field at
// the moment Check token is pressed, the field is emptied in the same task,
// and the value lives only in that call's local variable until the POST
// leaves. It is never put in module state, a DOM attribute, a log line,
// localStorage or a re-render; the field is cleared after every attempt,
// whatever the answer. The connector's answers never carry it either.
//
// OWNER-ONLY ROUTES (decide approve, retract, connect, repos add, check, drift
// ack, pause off) are plain same-origin fetch POSTs from this page: the
// browser attaches Origin + Sec-Fetch-Site, which is the pipeline's interim
// speed bump (§5.4, B02 — not a boundary). X-Hermes-Nonce is sent whenever
// window.hermesNonce exists (the F05 hand-off). A 403 owner_only is explained,
// never retried.
//
// NETWORK. Every call here is a same-origin /api route on this Mac. Nothing in
// this file talks to GitHub; the only things that do are the connector's own
// routes (connect, repos add, check, revoke) and the pipeline's decide /
// retract, each on an explicit button press. "Open GitHub" and PR links go to
// the owner's DEFAULT browser through window.open (the app shell hands
// '_blank' to NSWorkspace; the app window never navigates), and only https
// github.com URLs are ever opened.
//
// POLL. GET /api/github/status at most every 60 s, only while the page is
// visible and GitHub is connected. The first read of a page life only SEEDS
// the set of pending ids (no toast for changes that already existed); a later
// read that shows a new id raises the toast once. Nothing animates on a poll.
//
// MOTION through window.HermesAnim only (index.html's HA()), transform and
// opacity only: the review sheet fades in and out, a decision's result fades
// in once. Either reduce switch, a hidden window or no runtime land the end
// state at once. Nothing animates inside <header>, and no element here runs a
// CSS animation on an individual transform property.
//
// Design laws (CLAUDE.md): zero emoji (bespoke two-tone SVG), esc() on every
// interpolation (third-party text also has its hidden characters made
// visible as ⟦U+XXXX⟧), explicit transition-property, >=40 px targets, 12-hour
// times, flat rows (no cards inside cards), every global typeof-guarded so a
// headless harness can eval this file.
(function () {
  "use strict";

  var W = (typeof window !== "undefined") ? window
        : (typeof globalThis !== "undefined") ? globalThis : null;
  if (!W) return;
  if (W.__auxGitHub) return;
  W.__auxGitHub = true;

  function D() { return (typeof document !== "undefined") ? document : (W.document || null); }

  // esc() from index.html when present; it does not escape the apostrophe,
  // so that one is added here (no interpolation below sits in a single-quoted
  // attribute, but a future edit must not be able to make one unsafe).
  function E(s) {
    var v;
    if (typeof esc === "function") {
      try { v = esc(s); } catch (e) { v = null; }
    }
    if (v == null) {
      v = String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }
    return String(v).replace(/'/g, "&#39;");
  }

  // ---- hidden characters (mirrors aux_ghchanges.py _GHC_HIDDEN_RE) ---------
  // The server marks hidden characters in check samples and file names; the
  // diff and the agent's text arrive raw, so the same class is made visible
  // here. If they ever differ, the server's list wins: change both (the unit
  // tier compares the two code point by code point, t_r3_hidden_chars.py).
  // Every Default_Ignorable_Code_Point is in it — the Hangul fillers
  // U+115F/U+1160 next to U+3164/U+FFA0, the whole U+E0000-E0FFF block — except
  // that a LONE variation selector (VS16 after an emoji, one Mongolian FVS) is
  // ordinary text and only a run of two or more is hidden. No
  // \p{Default_Ignorable_Code_Point}: Python's re has no \p{}, and it would
  // flag that lone selector.
  var HIDDEN_RE = null;
  try {
    HIDDEN_RE = new RegExp("(?:[\\u00ad\\u034f\\u061c\\u115f\\u1160\\u17b4\\u17b5\\u180e\\u200b-\\u200f\\u2028-\\u202e" +
      "\\u2060-\\u206f\\u3164\\uffa0\\ufeff\\ufff0-\\ufffb" +
      "\\u{1bca0}-\\u{1bca3}\\u{1d173}-\\u{1d17a}\\u{e0000}-\\u{e0fff}" +
      "\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]|[\\ufe00-\\ufe0f\\u180b-\\u180d\\u180f]{2,})", "gu");
  } catch (e) {
    try {
      HIDDEN_RE = new RegExp("(?:[\\u00ad\\u034f\\u061c\\u115f\\u1160\\u17b4\\u17b5\\u180e\\u200b-\\u200f\\u2028-\\u202e" +
        "\\u2060-\\u206f\\u3164\\uffa0\\ufeff\\ufff0-\\ufffb" +
        "\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]|[\\ufe00-\\ufe0f\\u180b-\\u180d\\u180f]{2,})", "g");
    } catch (e2) { HIDDEN_RE = null; }
  }
  function hex4(n) { var h = Number(n).toString(16).toUpperCase(); while (h.length < 4) h = "0" + h; return h; }
  function markHidden(s) {
    s = String(s == null ? "" : s);
    if (!HIDDEN_RE) return s;
    return s.replace(HIDDEN_RE, function (m) {
      var out = "";
      Array.from(m).forEach(function (ch) { out += "⟦U+" + hex4(ch.codePointAt(0)) + "⟧"; });
      return out;
    });
  }
  // a marker (the server's or ours) stands out from the text around it
  function hl(escaped) {
    return String(escaped).replace(/⟦U\+[0-9A-F]{4,6}⟧/g, function (m) {
      return '<span class="gh-hid">' + m + "</span>";
    });
  }
  // third-party text: hidden characters visible, then escaped, then marked
  function TX(s) { return hl(E(markHidden(s))); }
  // ONE diff (or preview) line. On top of TX: a CRLF line's trailing CR is
  // dropped, any other CR is marked ⟦U+000D⟧ (the HTML parser would turn it
  // into a second row that reads as a different diff line, E61), and a run of
  // BLANK_RUN or more blanks (or BLANK_COLS columns) is drawn as ⟦N spaces⟧ instead of as empty area
  // that can push code out of sight (E02). U+2800 (the braille blank) draws
  // nothing either. A run of MARK_RUN or more combining marks is drawn as
  // ⟦N combining marks⟧: stacked, they rise over the lines above and can bury
  // a line under ink (two or three on one letter is ordinary text).
  // the server's C-longline rule (aux_ghchanges.py GHC_WS_RUN / GHC_WS_COLS,
  // _ghc_blank_width): a run counts at 40 blanks OR 80 columns, a tab being 8
  // columns and U+3000 / U+2800 two. The approval cards use the same rule
  // (index.html shape, aux_quickask.js qaShape; t_s3_shape_copies.py).
  var BLANK_RUN = 40, BLANK_COLS = 80;
  var BLANK_RE = new RegExp("[ \\t\\u00a0\\u1680\\u2000-\\u200a\\u202f\\u205f\\u2800\\u3000]{2,}", "g");
  function blankCols(run) {
    var cols = 0;
    for (var i = 0; i < run.length; i++) {
      var ch = run.charAt(i);
      cols += ch === "\t" ? 8 : (ch === "\u3000" || ch === "\u2800") ? 2 : 1;
    }
    return cols;
  }
  var MARK_RUN = 4;
  var MARK_RE = null;
  try { MARK_RE = new RegExp("[\\p{Mn}\\p{Me}]{" + MARK_RUN + ",}", "gu"); } catch (e) {
    MARK_RE = new RegExp("[\\u0300-\\u036f\\u0483-\\u0489\\u0591-\\u05c7\\u0610-\\u061a\\u064b-\\u065f\\u0670" +
      "\\u06d6-\\u06ed\\u0900-\\u0903\\u093a-\\u094f\\u0e31-\\u0e3a\\u0e47-\\u0e4e\\u0f71-\\u0fbc\\u1ab0-\\u1aff" +
      "\\u1dc0-\\u1dff\\u20d0-\\u20f0\\ua670-\\ua672\\ufe20-\\ufe2f]{" + MARK_RUN + ",}", "g");
  }
  function blankWord(run) {
    var sp = 0, tb = 0, ot = 0;
    for (var i = 0; i < run.length; i++) {
      var ch = run.charAt(i);
      if (ch === " ") sp++; else if (ch === "\t") tb++; else ot++;
    }
    var parts = [];
    if (sp) parts.push(plural(sp, "space"));
    if (tb) parts.push(plural(tb, "tab"));
    if (ot) parts.push(plural(ot, "other blank character"));
    return parts.length > 2 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts.join(" and ");
  }
  function DX(s) {
    // hidden characters first (a variation-selector run keeps its own marker)
    var t = markHidden(s).replace(/\r/g, "⟦U+000D⟧"), out = "", last = 0, m;
    MARK_RE.lastIndex = 0;
    while ((m = MARK_RE.exec(t))) {
      out += hl(E(t.slice(last, m.index))) + '<span class="ghd-ws">' +
        E("⟦" + plural(Array.from(m[0]).length, "combining mark") + "⟧") + "</span>";
      last = m.index + m[0].length;
    }
    return out + hl(E(t.slice(last)));
  }
  function DL(l) {
    l = String(l == null ? "" : l);
    if (l.charAt(l.length - 1) === "\r") l = l.slice(0, -1);
    var out = "", last = 0, m;
    BLANK_RE.lastIndex = 0;
    while ((m = BLANK_RE.exec(l))) {
      if (m[0].length < BLANK_RUN && blankCols(m[0]) < BLANK_COLS) continue;   // an ordinary gap
      out += DX(l.slice(last, m.index)) + '<span class="ghd-ws">' + E("⟦" + blankWord(m[0]) + "⟧") + "</span>";
      last = m.index + m[0].length;
    }
    return out + DX(l.slice(last));
  }
  function num(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }

  // ---- constants -------------------------------------------------------------
  var CARD_ID = "mind-extra-github";
  var PANEL_ID = "sec-connections";
  var SHEET_ID = "gh-review";
  var CSS_ID = "gh-css";
  var ID_RE = /^ghc-[0-9a-f]{12}$/;
  var HASH_RE = /^#github-review=(ghc-[0-9a-f]{12})$/;
  var POLL_MS = 60000;
  var REFRESH_MS = 15000;
  var DIFF_LINES = 3000;
  var TOAST_MSG = "Hermes has a GitHub change for you";
  var TOAST_MS = 9000;
  // the longest title the server keeps (aux_ghchanges.py GHC_TITLE_MAX): the
  // field stops there, so what you see is what is sent
  var TITLE_MAX = 120;
  // the most of a description the server keeps, in UTF-8 bytes (aux_ghchanges.py
  // GHC_BODY_MAX, a MIRROR: change both or neither). An edited description
  // over it would be cut AFTER approval, so the sheet refuses it first (E64).
  var BODY_MAX = 8 * 1024;
  // text the owner EDITS goes through the same cleaning as the first version
  // (_ghc_clean_text) after Approve; unedited text is already clean
  var CLEAN_NOTE = "If you change this text, Hermes cleans it the same way before it is sent: the title " +
    "stays one line, hidden comments are removed, hidden characters are dropped or shown as ⟦U+…⟧, " +
    "and @mentions are wrapped so nobody is notified.";

  // §1.3, verbatim
  var COPY = {
    eyebrow: "Optional · GitHub",
    title: "Let Hermes propose code changes on GitHub",
    sub: "Nothing is pushed until you approve it here, and never to your main branch.",
    p1: "Hermes can work on a copy of a repository in a sandbox folder on this Mac. When it " +
      "has a change, you see the whole diff here and decide. If you approve, Hermes pushes " +
      "one commit to a branch named hermes/… and can open a draft pull request. It never " +
      "merges, never pushes to your default branch, and never changes repository settings.",
    p2: "You create the key yourself on GitHub: a token limited to the repositories you " +
      "pick, with two permissions, Contents and Pull requests. It expires in 30 days. " +
      "Hermes refuses any token that can do more.",
    steps: [
      ["Open GitHub. The form is filled in for you."],
      ["Under Repository access, choose ", { b: "Only select repositories" },
        " and pick the ones Hermes may work on."],
      ["Press ", { b: "Generate token" }, ", copy it, and paste it below."]
    ],
    open: "Open GitHub to create the token",
    check: "Check token",
    skip: "Skip — connect later in Settings › Connections",
    tokenLabel: "Token",
    tokenPh: "github_pat_…",
    tokenHelp: "Stored only on this Mac, readable only by your account. Never shown again.",
    reposLabel: "Which repositories?",
    reposHelp: "Optional, one owner/repo per line. Hermes uses it to tell you what to tick " +
      "on GitHub and to check the result; GitHub’s form cannot be filled in with them.",
    workflow: "Workflows cannot be checked through GitHub’s API, so Hermes refuses any " +
      "change to "
  };
  var OWNER_TEXT = "Only you can do this, from the Hermes window itself. This request did " +
    "not arrive from the dashboard window (or its Quick Ask), so nothing was changed. Open " +
    "the dashboard and try it there.";
  var STALE_TEXT = "This change was updated or decided somewhere else since you opened it. " +
    "It has been reloaded — review it again before you decide.";

  // ---- glyphs (two-tone: accent fill + currentColor stroke; zero emoji) -----
  function svg(inner, size, cls) {
    var s = size || 16;
    return '<svg class="gh-ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" width="' + s +
      '" height="' + s + '" aria-hidden="true" focusable="false">' + inner + "</svg>";
  }
  var ST = 'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" ' +
    'stroke-linejoin="round"';
  var G = {
    branch: '<circle cx="7" cy="6" r="2.4" fill="var(--iris)" opacity=".22"/>' +
      '<circle cx="17" cy="8.5" r="2.4" fill="var(--iris)" opacity=".22"/>' +
      '<circle cx="7" cy="6" r="2.4" ' + ST + '/><circle cx="7" cy="18" r="2.4" ' + ST + "/>" +
      '<circle cx="17" cy="8.5" r="2.4" ' + ST + "/>" +
      '<path d="M7 8.4v7.2M17 10.9c0 3.4-3.4 4-7.6 5" ' + ST + "/>",
    close: '<circle cx="12" cy="12" r="8.6" fill="var(--iris)" opacity=".12"/>' +
      '<path d="M8.6 8.6l6.8 6.8M15.4 8.6l-6.8 6.8" ' + ST + "/>",
    ext: '<rect x="4.5" y="7" width="12.5" height="12.5" rx="2.4" fill="var(--iris)" opacity=".16"/>' +
      '<path d="M13 4.5h6.5V11M19.5 4.5l-8 8M17 14v3.6a2 2 0 0 1-2 2H6.4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2H10" ' +
      ST + "/>",
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.2" fill="var(--iris)" opacity=".16"/>' +
      '<rect x="8.5" y="8.5" width="11" height="11" rx="2.2" ' + ST + "/>" +
      '<path d="M15.5 5.5v-.3a1.7 1.7 0 0 0-1.7-1.7H6.2a1.7 1.7 0 0 0-1.7 1.7v7.6a1.7 1.7 0 0 0 1.7 1.7h.3" ' +
      ST + "/>",
    folder: '<path d="M3.8 7.6a2 2 0 0 1 2-2h3.6l2 2.2h6.8a2 2 0 0 1 2 2v7.6a2 2 0 0 1-2 2H5.8a2 2 0 0 1-2-2Z" ' +
      'fill="var(--iris)" opacity=".16"/>' +
      '<path d="M3.8 7.6a2 2 0 0 1 2-2h3.6l2 2.2h6.8a2 2 0 0 1 2 2v7.6a2 2 0 0 1-2 2H5.8a2 2 0 0 1-2-2Z" ' +
      ST + "/>",
    warn: '<path d="M12 4.2 21 19.5H3Z" fill="var(--warn)" opacity=".2"/>' +
      '<path d="M12 4.2 21 19.5H3Z" ' + ST + '/><path d="M12 10v4.2M12 16.9v.1" ' + ST + "/>",
    ok: '<circle cx="12" cy="12" r="8.6" fill="var(--ok)" opacity=".18"/>' +
      '<circle cx="12" cy="12" r="8.6" ' + ST + '/><path d="M8.3 12.3l2.5 2.5 5-5.2" ' + ST + "/>",
    block: '<circle cx="12" cy="12" r="8.6" fill="var(--bad)" opacity=".18"/>' +
      '<circle cx="12" cy="12" r="8.6" ' + ST + '/><path d="M6.1 6.1l11.8 11.8" ' + ST + "/>",
    info: '<circle cx="12" cy="12" r="8.6" fill="var(--iris)" opacity=".14"/>' +
      '<circle cx="12" cy="12" r="8.6" ' + ST + '/><path d="M12 11v5.2M12 7.8v.1" ' + ST + "/>",
    lock: '<rect x="5" y="10.5" width="14" height="9.5" rx="2.2" fill="var(--iris)" opacity=".18"/>' +
      '<rect x="5" y="10.5" width="14" height="9.5" rx="2.2" ' + ST + "/>" +
      '<path d="M8.4 10.5V8a3.6 3.6 0 0 1 7.2 0v2.5" ' + ST + "/>",
    chev: '<path d="M9.5 6.5 15 12l-5.5 5.5" ' + ST + "/>",
    file: '<path d="M6.5 3.8h7l4 4v11.4a1.6 1.6 0 0 1-1.6 1.6H6.5a1.6 1.6 0 0 1-1.6-1.6V5.4a1.6 1.6 0 0 1 1.6-1.6Z" ' +
      'fill="var(--iris)" opacity=".14"/>' +
      '<path d="M6.5 3.8h7l4 4v11.4a1.6 1.6 0 0 1-1.6 1.6H6.5a1.6 1.6 0 0 1-1.6-1.6V5.4a1.6 1.6 0 0 1 1.6-1.6ZM13.5 3.8v4h4" ' +
      ST + "/>"
  };

  // ---- motion (index.html's HA(), or an instant stand-in) -------------------
  var H0 = { done: true, finished: Promise.resolve(), cancel: function () {}, finish: function () {} };
  var NULL_ANIM = {
    enter: function () { return H0; },
    exit: function (t, p, done) { if (typeof done === "function") { try { done(); } catch (e) {} } return H0; }
  };
  function anim() {
    try { if (typeof HA === "function") { var a = HA(); if (a && a.enter && a.exit) return a; } } catch (e) {}
    return (W.HermesAnim && W.HermesAnim.enter) ? W.HermesAnim : NULL_ANIM;
  }

  // ---- time (12-hour, absolute; "N h ago" only for an age) -------------------
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function toDate(ts) {
    if (typeof ts !== "number" || !isFinite(ts) || ts <= 0) return null;
    var d = new Date(ts > 1e12 ? ts : ts * 1000);
    return isNaN(d.getTime()) ? null : d;
  }
  function clock(d) {
    var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  function t12(ts, now) {
    var d = toDate(ts); if (!d) return "";
    var n = now ? new Date(now) : new Date();
    if (d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() &&
        d.getDate() === n.getDate()) return clock(d);
    return MON[d.getMonth()] + " " + d.getDate() +
      (d.getFullYear() !== n.getFullYear() ? ", " + d.getFullYear() : "") + ", " + clock(d);
  }
  function dateLong(ts) {
    var d = toDate(ts); if (!d) return "";
    return MON[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }
  function ago(ts, now) {
    var d = toDate(ts); if (!d) return "";
    var s = ((now || Date.now()) - d.getTime()) / 1000;
    if (s < 45) return "just now";
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    var days = Math.round(s / 86400);
    return days === 1 ? "1 day ago" : days + " days ago";
  }
  function sha7(s) { s = String(s || ""); return /^[0-9a-f]{7,64}$/.test(s) ? s.slice(0, 7) : ""; }
  function bytes(n) {
    n = Number(n); if (!isFinite(n) || n < 0) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
  function nplural(n, one, many) { return num(n) + " " + (n === 1 ? one : (many || one + "s")); }

  // ---- URLs + clipboard ------------------------------------------------------
  // Only https://github.com/... is ever handed to window.open: the app shell
  // passes '_blank' to NSWorkspace.open, which opens ANY scheme (B18).
  function ghUrl(u) {
    try {
      var x = new URL(String(u || "").trim());
      if (x.protocol === "https:" && x.hostname === "github.com" && !x.username && !x.password)
        return x.href;
    } catch (e) {}
    return "";
  }
  function openExternal(u) {
    var v = ghUrl(u);
    if (!v) return false;
    try { W.open(v, "_blank", "noopener"); return true; } catch (e) { return false; }
  }
  function copyText(t) {
    t = String(t == null ? "" : t);
    try {
      var mh = W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers.hermesClipWrite;
      if (mh) { mh.postMessage({ action: "write", text: t }); return Promise.resolve(true); }
    } catch (e) {}
    try {
      var nv = W.navigator;
      if (nv && nv.clipboard && nv.clipboard.writeText) {
        return nv.clipboard.writeText(t).then(function () { return true; }, function () { return false; });
      }
    } catch (e) {}
    return Promise.resolve(false);
  }
  function say(msg, ms) {
    if (typeof toast === "function") { try { toast(msg, ms); } catch (e) {} }
  }
  function copyAndSay(t, what) {
    return copyText(t).then(function (ok) {
      say(ok ? (what || "Copied") + " to the clipboard." : "Could not copy. Select the text and copy it yourself.");
      return ok;
    });
  }

  // ---- requests --------------------------------------------------------------
  function nonce() {
    var v = W.hermesNonce;
    if (typeof v === "function") { try { v = v(); } catch (e) { v = null; } }
    return (typeof v === "string" && /^[\x21-\x7e]{1,512}$/.test(v)) ? v : null;
  }
  // -> {ok, status, data, net}. Never throws. A POST is a plain same-origin
  // fetch, so the browser attaches Origin + Sec-Fetch-Site itself.
  async function req(method, url, body) {
    var init = { method: method, headers: {}, credentials: "same-origin" };
    if (method === "POST") {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body || {});
      var n = nonce();
      if (n) init.headers["X-Hermes-Nonce"] = n;
    }
    var r;
    try { r = await fetch(url, init); } catch (e) { return { ok: false, status: 0, data: null, net: true }; }
    var data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    return { ok: !!r.ok && !!data && data.ok !== false, status: r.status || 0, data: data, net: false };
  }
  function get(url) { return req("GET", url); }
  function post(url, body) { return req("POST", url, body); }

  // ==========================================================================
  // state
  // ==========================================================================
  function newConn() {
    return { busy: false, result: null, refusal: null, err: "", reposText: "", createHint: "",
             unchecked: false, createUrl: null };
  }
  var S = {
    status: null,          // last /api/github/status payload (never holds a token)
    statusErr: "",
    ambient: null,         // last GET /api/github/ambient .ambient
    ambientBusy: false,
    changes: null,         // last GET /api/github/changes .changes
    access: null,          // granted folder list (for Show in Finder)
    cardAt: 0,
    cardLoaded: false,
    cardDirty: false,
    busy: "",              // the card action in flight
    notice: null,          // {cls, text, link?, lines?}
    conn: { card: newConn(), step: newConn() },
    step: { status: null, read: false, reading: false, skipped: false },
    // toast poll
    seen: Object.create(null),
    drafts: Object.create(null),   // id -> {digest, title, body}: PR text typed, then Esc (never ticks)
    seeded: false,
    pollId: 0,
    lastPollAt: 0,
    polling: false,
    toastId: null, toastObs: null,
    // review sheet
    review: null
  };

  // ==========================================================================
  // CSS (one <style> in <head>, shared by the card, the step and the sheet)
  // ==========================================================================
  var MONO = "ui-monospace,SFMono-Regular,Menlo,monospace";
  function CSS() {
    var C = "#" + CARD_ID, R = "#" + SHEET_ID;
    return "" +
      // ---- shared pieces -----------------------------------------------------
      ".gh-ic{flex:0 0 auto;vertical-align:-3px}" +
      ".gh-b{display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;" +
        "min-height:40px;padding:0 15px;border-radius:10px;font:inherit;font-size:12.5px;" +
        "font-weight:560;cursor:pointer;color:inherit;" +
        "border:1px solid var(--field-edge);background:var(--field);" +
        "transition-property:background-color,border-color,color,transform,opacity;" +
        "transition-duration:150ms;transition-timing-function:ease-out}" +
      ".gh-b:hover:not(:disabled){border-color:var(--iris)}" +
      ".gh-b:active:not(:disabled){transform:scale(.97)}" +
      ".gh-b:focus-visible{outline:2px solid var(--iris);outline-offset:2px}" +
      ".gh-b:disabled{opacity:.45;cursor:default}" +
      ".gh-b.gh-go{border-color:transparent;background:var(--iris);color:var(--iris-ink);font-weight:620}" +
      ".gh-b.gh-go:hover:not(:disabled){border-color:transparent;background:var(--iris-2)}" +
      ".gh-b.gh-bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent)}" +
      ".gh-b.gh-bad:hover:not(:disabled){border-color:var(--bad)}" +
      ".gh-b.gh-sq{width:40px;padding:0}" +
      ".gh-p{margin:0 0 11px;font-size:13px;line-height:1.6;text-wrap:pretty}" +
      ".gh-note{margin:6px 0 0;font-size:11.5px;line-height:1.55;color:var(--muted);text-wrap:pretty}" +
      ".gh-err{margin:10px 0 0;font-size:12px;line-height:1.55;color:var(--bad);text-wrap:pretty}" +
      ".gh-okl{margin:10px 0 0;font-size:12px;line-height:1.55;color:var(--ok);text-wrap:pretty}" +
      ".gh-warnl{margin:10px 0 0;font-size:12px;line-height:1.55;color:var(--warn);text-wrap:pretty}" +
      ".gh-chip{display:inline-flex;align-items:center;gap:4px;padding:1px 8px;border-radius:20px;" +
        "font-size:10.5px;font-weight:650;letter-spacing:.02em;white-space:nowrap;" +
        "color:var(--muted);background:color-mix(in srgb,var(--ink) 8%,transparent)}" +
      ".gh-chip.ok{color:var(--ok);background:color-mix(in srgb,var(--ok) 15%,transparent)}" +
      ".gh-chip.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 16%,transparent)}" +
      ".gh-chip.bad{color:var(--bad);background:color-mix(in srgb,var(--bad) 14%,transparent)}" +
      ".gh-chip.iris{color:var(--iris);background:color-mix(in srgb,var(--iris) 14%,transparent)}" +
      ".gh-hid{font-family:" + MONO + ";font-size:.92em;padding:0 2px;border-radius:4px;" +
        "color:var(--warn);background:color-mix(in srgb,var(--warn) 18%,transparent)}" +
      ".gh-h{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin:22px 0 8px;" +
        "padding-bottom:7px;border-bottom:1px solid var(--hairline)}" +
      ".gh-h h2,.gh-h h3{margin:0;font-size:12px;font-weight:680;letter-spacing:.02em}" +
      ".gh-h span{font-size:11.5px;color:var(--muted)}" +
      ".gh-lab{display:block;margin:14px 0 5px;font-size:12px;font-weight:620}" +
      ".gh-field{display:block;width:100%;box-sizing:border-box;min-height:40px;padding:8px 10px;" +
        "border-radius:9px;font:inherit;font-size:13px;color:inherit;" +
        "border:1px solid var(--field-edge);background:var(--field);" +
        "transition-property:border-color;transition-duration:150ms;transition-timing-function:ease-out}" +
      ".gh-field:focus-visible{outline:2px solid var(--iris);outline-offset:1px}" +
      "textarea.gh-field{resize:vertical;line-height:1.5}" +
      ".gh-mono{font-family:" + MONO + ";font-size:12px}" +
      ".gh-pre{margin:8px 0 0;padding:10px 12px;border-radius:9px;max-height:260px;overflow:auto;" +
        "font-family:" + MONO + ";font-size:11.5px;line-height:1.5;white-space:pre;" +
        "background:color-mix(in srgb,var(--ink) 6%,transparent);border:1px solid var(--hairline)}" +
      ".gh-pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere}" +
      ".gh-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;min-height:48px;padding:8px 0;" +
        "border-bottom:1px solid var(--hairline)}" +
      ".gh-row:last-child{border-bottom:0}" +
      ".gh-row-txt{flex:1 1 220px;min-width:0}" +
      ".gh-row-l{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font-size:13px;font-weight:600;" +
        "overflow-wrap:anywhere}" +
      ".gh-row-d{display:block;margin-top:2px;font-size:11.5px;line-height:1.5;color:var(--muted);" +
        "text-wrap:pretty;overflow-wrap:anywhere}" +
      ".gh-row-ctl{flex:0 0 auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap}" +
      ".gh-code{font-family:" + MONO + ";font-size:11px;padding:1px 5px;border-radius:5px;" +
        "background:color-mix(in srgb,var(--ink) 8%,transparent);overflow-wrap:anywhere}" +
      // the switch (a real checkbox; the track is decoration)
      ".gh-sw{position:relative;display:inline-flex;align-items:center;gap:8px;min-height:40px;cursor:pointer}" +
      ".gh-sw input{position:absolute;opacity:0;width:1px;height:1px;margin:0}" +
      ".gh-sw-t{position:relative;width:38px;height:22px;border-radius:12px;flex:0 0 auto;" +
        "background:color-mix(in srgb,var(--ink) 18%,transparent);" +
        "transition-property:background-color;transition-duration:160ms;transition-timing-function:ease-out}" +
      ".gh-sw-t::after{content:'';position:absolute;left:3px;top:3px;width:16px;height:16px;" +
        "border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);" +
        "transition-property:transform;transition-duration:160ms;transition-timing-function:ease-out}" +
      ".gh-sw input:checked+.gh-sw-t{background:var(--warn)}" +
      ".gh-sw input:checked+.gh-sw-t::after{transform:translateX(16px)}" +
      ".gh-sw input:focus-visible+.gh-sw-t{outline:2px solid var(--iris);outline-offset:2px}" +
      ".gh-sw input:disabled+.gh-sw-t{opacity:.5}" +
      ".gh-sw-l{font-size:12px;font-weight:600;min-width:46px}" +
      // ---- the connect component (card + onboarding step) --------------------
      ".ghc ol.ghc-steps{margin:4px 0 6px;padding-left:20px;font-size:13px;line-height:1.6}" +
      ".ghc ol.ghc-steps li{margin:0 0 3px;text-wrap:pretty}" +
      ".ghc-act{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px}" +
      ".ghc .ghc-panel{margin:14px 0 4px;padding:2px 0 2px 14px;border-left:2px solid var(--ok)}" +
      ".ghc .ghc-panel.ref{border-left-color:var(--bad)}" +
      ".ghc .ghc-panel-h{display:flex;align-items:center;gap:7px;margin:0 0 6px;font-size:13px;font-weight:650}" +
      ".ghc ul.ghc-lines{margin:0;padding:0 0 0 18px;font-size:12.5px;line-height:1.6}" +
      ".ghc ul.ghc-lines li{margin:0 0 4px;text-wrap:pretty}" +
      ".ghc ul.ghc-repos{list-style:none;margin:6px 0;padding:0;display:grid;gap:5px}" +
      ".ghc ul.ghc-repos li{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font-size:12.5px}" +
      // ---- the card ----------------------------------------------------------
      C + " .body{padding-bottom:14px}" +
      C + " h2 .gh-badge{margin-left:8px}" +
      C + " .gh-lede{margin:2px 0 12px;font-size:12.5px;line-height:1.55;color:var(--muted);" +
        "text-wrap:pretty;max-width:64ch}" +
      C + " .gh-att{display:flex;align-items:flex-start;gap:9px;padding:9px 0;" +
        "border-bottom:1px solid var(--hairline);font-size:12.5px;line-height:1.55}" +
      C + " .gh-att:last-child{border-bottom:0}" +
      C + " .gh-att.bad .gh-ic{color:var(--bad)}" +
      C + " .gh-att.warn .gh-ic{color:var(--warn)}" +
      C + " .gh-att.bad .gh-att-t>b:first-child,"+ C + " .gh-att.bad>.gh-att-t{color:var(--bad)}" +
      C + " .gh-att .gh-att-t{flex:1 1 auto;min-width:0;text-wrap:pretty}" +
      C + " .gh-att>.gh-b{flex:0 0 auto}" +
      C + " .gh-amb-c{display:block;margin:8px 0 0}" +
      C + " .gh-amb-c .gh-amb-l{display:block;font-size:11.5px;color:var(--muted);margin-bottom:3px}" +
      C + " .gh-amb-row{display:flex;align-items:center;gap:8px}" +
      C + " .gh-amb-row code{flex:1 1 auto;min-width:0;padding:6px 8px}" +
      C + " details.gh-more{margin-top:8px}" +
      C + " details.gh-more summary{display:inline-flex;align-items:center;gap:6px;min-height:40px;" +
        "cursor:pointer;font-size:12px;font-weight:600;color:var(--muted);list-style:none}" +
      C + " details.gh-more summary::-webkit-details-marker{display:none}" +
      C + " details.gh-more summary:focus-visible{outline:2px solid var(--iris);outline-offset:2px;border-radius:6px}" +
      C + " details.gh-more .gh-chev{transition-property:transform;transition-duration:160ms;" +
        "transition-timing-function:ease-out}" +
      C + " details.gh-more[open] .gh-chev{transform:rotate(90deg)}" +
      // ---- the review sheet --------------------------------------------------
      R + "{position:fixed;inset:0;z-index:9050;display:flex;flex-direction:column;" +
        "background:var(--ground);color:var(--ink);" +
        "-webkit-font-smoothing:antialiased;" +
        'font:14px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}' +
      R + "[hidden]{display:none!important}" +
      R + " *{box-sizing:border-box}" +
      R + " .ghr-bar,"+ R + " .ghr-foot{flex:0 0 auto;display:flex;align-items:center;gap:10px;" +
        "padding:10px 22px;background:color-mix(in srgb,var(--ground) 90%,transparent);" +
        "-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}" +
      R + " .ghr-bar{border-bottom:1px solid var(--hairline)}" +
      R + " .ghr-foot{border-top:1px solid var(--hairline)}" +
      R + " .ghr-bar-t{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:650;" +
        "letter-spacing:.02em;color:var(--muted);margin-right:auto}" +
      R + " .ghr-scroll{flex:1 1 auto;overflow-y:auto;overflow-x:hidden}" +
      R + " .ghr-col{width:100%;max-width:1000px;margin:0 auto;padding:24px 22px 30px}" +
      R + " .ghr-eye{margin:0 0 8px;font-size:10.5px;font-weight:700;letter-spacing:.09em;" +
        "text-transform:uppercase;color:var(--muted)}" +
      R + " h1.ghr-t{margin:0 0 8px;font-size:22px;line-height:1.25;font-weight:640;" +
        "letter-spacing:-.01em;text-wrap:balance;overflow-wrap:anywhere}" +
      R + " h1.ghr-t:focus{outline:none}" +
      R + " .ghr-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;" +
        "color:var(--muted)}" +
      R + " .ghr-meta .ghr-repo{font-weight:650;color:var(--ink);overflow-wrap:anywhere}" +
      R + " .ghr-what{margin:14px 0 0;padding:10px 0 10px 14px;border-left:2px solid var(--iris);" +
        "font-size:13px;line-height:1.6;text-wrap:pretty}" +
      R + " .ghr-state{margin:14px 0 0;padding:10px 0 10px 14px;border-left:2px solid var(--muted);" +
        "font-size:13px;line-height:1.6;text-wrap:pretty}" +
      R + " .ghr-state.ok{border-left-color:var(--ok)}" +
      R + " .ghr-state.bad{border-left-color:var(--bad)}" +
      R + " .ghr-state.warn{border-left-color:var(--warn)}" +
      R + " ul.ghr-checks,"+ R + " ul.ghr-files{list-style:none;margin:0;padding:0}" +
      R + " .ghr-ck{display:flex;align-items:flex-start;gap:10px;padding:10px 0 10px 12px;" +
        "border-bottom:1px solid var(--hairline);border-left:2px solid var(--hairline)}" +
      R + " .ghr-ck:last-child{border-bottom:0}" +
      R + " .ghr-ck.block{border-left-color:var(--bad)}" +
      R + " .ghr-ck.warn{border-left-color:var(--warn)}" +
      R + " .ghr-ck.confirm{border-left-color:var(--iris)}" +
      R + " .ghr-ck.missing{background:color-mix(in srgb,var(--warn) 9%,transparent)}" +
      R + " .ghr-ck label{display:flex;align-items:flex-start;gap:10px;min-height:40px;cursor:pointer;width:100%}" +
      R + " .ghr-ck input[type=checkbox]{flex:0 0 auto;width:18px;height:18px;margin:2px 0 0;" +
        "accent-color:var(--iris)}" +
      R + " .ghr-ck-b{flex:1 1 auto;min-width:0;font-size:12.5px;line-height:1.55;text-wrap:pretty;" +
        "overflow-wrap:anywhere}" +
      R + " .ghr-ck-lv{display:inline-block;margin-right:6px}" +
      R + " ul.ghr-samples{margin:6px 0 0;padding:0;list-style:none;display:grid;gap:3px}" +
      R + " ul.ghr-samples li{font-size:11.5px;color:var(--muted);overflow-wrap:anywhere}" +
      R + " .ghr-file{display:flex;align-items:center;gap:9px;min-height:36px;padding:6px 0;" +
        "border-bottom:1px solid var(--hairline);font-size:12.5px}" +
      R + " .ghr-file:last-child{border-bottom:0}" +
      R + " .ghr-file .ghr-fp{flex:1 1 auto;min-width:0;font-family:" + MONO + ";font-size:12px;" +
        "overflow-wrap:anywhere}" +
      R + " .ghr-file .ghr-st{flex:0 0 auto;width:70px;font-size:11px;color:var(--muted)}" +
      R + " .ghr-file .ghr-n{flex:0 0 auto;font-family:" + MONO + ";font-size:11.5px;" +
        "font-variant-numeric:tabular-nums;white-space:nowrap}" +
      R + " .ghr-n .a{color:var(--ok)}" + R + " .ghr-n .d{color:var(--bad)}" +
      // the diff viewer: monospace, every line escaped. Lines WRAP (E02): the
      // app hides every scrollbar, so a line running past the box would hide
      // what it carries. A wrapped line's later rows sit 2ch further in
      // (hanging indent) beside a bar, so one line never reads as several;
      // the bar's height is the line's minus one row (18px), so a one-row
      // line draws none. overflow:clip: a line paints only inside its own box,
      // so nothing it carries can be drawn over another line.
      R + " .ghd{border:1px solid var(--hairline);border-radius:10px;overflow:hidden}" +
      R + " .ghd-file+.ghd-file{border-top:1px solid var(--hairline)}" +
      R + " .ghd-fh{padding:7px 12px;font-family:" + MONO + ";font-size:11.5px;font-weight:600;" +
        "background:color-mix(in srgb,var(--ink) 5%,transparent);overflow-wrap:anywhere}" +
      R + " .ghd-body{overflow-x:auto}" +
      R + " .ghd-lines{display:block;padding:4px 0}" +
      R + " .ghd-l{display:block;padding:0 12px 0 calc(12px + 2ch);text-indent:-2ch;white-space:pre-wrap;" +
        "overflow-wrap:anywhere;word-break:normal;font-family:" + MONO + ";font-size:11.5px;line-height:18px;tab-size:4;overflow:clip;" +
        "background-image:linear-gradient(var(--faint),var(--faint));background-repeat:no-repeat;" +
        "background-size:2px calc(100% - 18px);background-position:5px 18px}" +
      R + " .ghd-l.add{background-color:color-mix(in srgb,var(--ok) 13%,transparent)}" +
      R + " .ghd-l.del{background-color:color-mix(in srgb,var(--bad) 12%,transparent)}" +
      R + " .ghd-l.hunk{color:var(--iris);background-color:color-mix(in srgb,var(--iris) 7%,transparent)}" +
      R + " .ghd-l.meta{color:var(--faint)}" +
      // a long run of blanks, drawn as a marker instead of as empty area
      R + " .ghd-ws{color:var(--warn);font-weight:600}" +
      R + " .ghd-bin{margin:0;padding:9px 12px;font-size:12px;color:var(--muted)}" +
      R + " .ghd-bin.warn{color:var(--warn)}" +
      R + " .ghd-more{padding:10px 12px}" +
      R + " .ghd-more.top{border-bottom:1px solid var(--hairline);display:flex;align-items:center;gap:10px;" +
        "flex-wrap:wrap;font-size:12px}" +
      R + " .ghd-more.top>span{flex:1 1 260px;color:var(--warn)}" +
      R + " .ghr-count{margin:4px 0 0;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}" +
      R + " .ghr-count.over{color:var(--bad);font-weight:600}" +
      R + " textarea.ghr-body{min-height:150px;font-family:" + MONO + ";font-size:12px}" +
      R + " .ghr-foot-in{display:flex;align-items:center;gap:10px;flex-wrap:wrap;width:100%;" +
        "max-width:1000px;margin:0 auto}" +
      R + " .ghr-why{margin-right:auto;flex:1 1 220px;min-width:0;font-size:12px;line-height:1.45;" +
        "color:var(--muted);text-wrap:pretty}" +
      R + " .ghr-msg{margin:16px 0 0;font-size:12.5px;line-height:1.55;text-wrap:pretty}" +
      R + " .ghr-msg.bad{color:var(--bad)}" + R + " .ghr-msg.warn{color:var(--warn)}" +
      R + " .ghr-msg.ok{color:var(--ok)}" +
      "@media (max-width:620px){" + R + " .ghr-col{padding:18px 16px 24px}" +
        R + " .ghr-bar," + R + " .ghr-foot{padding:10px 16px}" +
        R + " h1.ghr-t{font-size:19px}}" +
      // ---- reduced motion: both switches -------------------------------------
      "@media (prefers-reduced-motion:reduce){" +
        ".gh-b,.gh-field,.gh-sw-t,.gh-sw-t::after,.gh-chev{transition-duration:1ms}" +
        ".gh-b:active:not(:disabled){transform:none}}" +
      ":root[data-reduce] .gh-b,:root[data-reduce] .gh-field,:root[data-reduce] .gh-sw-t," +
        ":root[data-reduce] .gh-sw-t::after,:root[data-reduce] .gh-chev{transition-duration:1ms}" +
      ":root[data-reduce] .gh-b:active:not(:disabled){transform:none}";
  }
  function ensureCSS() {
    var d = D(); if (!d) return;
    if (d.getElementById(CSS_ID)) return;
    try {
      var s = d.createElement("style");
      s.id = CSS_ID;
      s.textContent = CSS();
      (d.head || d.documentElement || d.body).appendChild(s);
    } catch (e) {}
  }

  // ==========================================================================
  // small pieces
  // ==========================================================================
  function visChip(priv) {
    if (priv === true) return '<span class="gh-chip">' + svg(G.lock, 11) + "Private</span>";
    if (priv === false) return '<span class="gh-chip warn">Public</span>';
    return '<span class="gh-chip">Visibility unknown</span>';
  }
  function protChip(row) {
    var b = (row && row.default_branch) || "main";
    var p = row && row.protection;
    var st = p && p.state;
    if (st === "protected") return '<span class="gh-chip ok" title="' + E(p.text || "") + '">' +
      TX(b) + " protected</span>";
    if (st === "not_protected") return '<span class="gh-chip warn" title="' + E(p.gap_text || p.text || "") +
      '">' + TX(b) + " not protected</span>";
    return '<span class="gh-chip">' + TX(b) + " protection unknown</span>";
  }
  function codeSpans(text) {
    // `x` spans in connector copy -> <code>, every segment escaped
    var parts = String(text == null ? "" : text).split("`");
    return parts.map(function (p, i) {
      return i % 2 ? '<code class="gh-code">' + E(p) + "</code>" : E(p);
    }).join("");
  }
  function li(parts) {
    return "<li>" + parts.map(function (p) {
      return typeof p === "string" ? E(p) : "<b>" + E(p.b) + "</b>";
    }).join("") + "</li>";
  }
  function parseRepos(text) {
    return String(text || "").split(/[\n,]+/).map(function (x) { return x.trim(); })
      .filter(Boolean).slice(0, 20);
  }
  function focusedField(el) {
    var d = D();
    try {
      var a = d && d.activeElement;
      if (!a || !el || !el.contains || !el.contains(a)) return false;
      var t = (a.tagName || "").toLowerCase();
      if (t === "textarea") return true;
      // a switch or a tick being clicked is not typing: only text fields count
      return t === "input" && /^(text|password|search|url|email|)$/.test(String(a.getAttribute("type") || "").toLowerCase());
    } catch (e) { return false; }
  }

  // ==========================================================================
  // (shared) the connect component — §1.2 / §1.3
  // ==========================================================================
  // st: S.conn.card | S.conn.step. p: an id prefix unique per instance.
  function connectHTML(st, p, opts) {
    st = st || newConn();
    p = p || "ghc";
    opts = opts || {};
    var h = '<div class="ghc" data-ghc="' + E(p) + '">';
    if (opts.intro !== false) {
      h += '<p class="gh-p">' + E(COPY.p1) + "</p>" + '<p class="gh-p">' + E(COPY.p2) + "</p>";
    }
    if (st.result) return h + successHTML(st.result, opts.ambient) + "</div>";
    h += '<ol class="ghc-steps">' + COPY.steps.map(li).join("") + "</ol>";
    h += '<label class="gh-lab" for="' + E(p) + '-repos">' + E(COPY.reposLabel) + "</label>" +
      '<textarea class="gh-field gh-mono" id="' + E(p) + '-repos" data-gh-repos rows="3" ' +
      'spellcheck="false" autocapitalize="off" autocomplete="off" aria-describedby="' + E(p) + '-repos-help" ' +
      'placeholder="owner/repo"></textarea>' +
      '<p class="gh-note" id="' + E(p) + '-repos-help">' + E(COPY.reposHelp) + "</p>";
    h += '<div class="ghc-act"><button type="button" class="gh-b" data-gh="open">' +
      svg(G.ext, 15) + E(COPY.open) + "</button></div>";
    if (st.createHint) h += '<p class="gh-note" role="status">' + E(st.createHint) + "</p>";
    h += '<label class="gh-lab" for="' + E(p) + '-token">' + E(COPY.tokenLabel) + "</label>" +
      '<input class="gh-field gh-mono" type="password" id="' + E(p) + '-token" data-gh-token ' +
      'autocomplete="off" spellcheck="false" autocapitalize="off" autocorrect="off" ' +
      'aria-describedby="' + E(p) + '-token-help" ' +
      'placeholder="' + E(COPY.tokenPh) + '"' + (st.busy ? " disabled" : "") + ">" +
      '<p class="gh-note" id="' + E(p) + '-token-help">' + E(COPY.tokenHelp) + "</p>";
    h += '<div class="ghc-act"><button type="button" class="gh-b gh-go" data-gh="check"' +
      (st.busy ? " disabled" : "") + ">" + (st.busy ? "Checking with GitHub…" : E(COPY.check)) +
      "</button>" +
      (opts.skip ? '<button type="button" class="gh-b" data-gh="skip">' + E(COPY.skip) + "</button>" : "") +
      "</div>";
    if (st.refusal) h += refusalHTML(st.refusal);
    if (st.err) h += '<p class="gh-err" role="alert">' + E(st.err) + "</p>";
    return h + "</div>";
  }

  // "Connected as @login. This token can reach <n> repositories: ... Checked:
  // ... Workflows cannot be checked ... Expires <Mon D, YYYY>."
  function checkedLine(c) {
    c = c || {};
    var bits = [];
    bits.push(c.contents_write === "verified" ? "Contents write (verified)"
      : c.contents_write === "partial" ? "Contents write (some repositories)" : "Contents write");
    bits.push(c.pull_requests === "partial" ? "Pull requests (some repositories)" : "Pull requests");
    [["administration", "Administration"], ["secrets", "Secrets"], ["variables", "Variables"],
     ["webhooks", "Webhooks"], ["actions", "Actions"]].forEach(function (k) {
      var v = c[k[0]];
      if (v === "public_unchecked") bits.push(k[1] + " (cannot be checked on public repositories)");
      else if (v === "present") bits.push(k[1] + " present");
      else if (v === "unknown") bits.push(k[1] + " unknown");
      else bits.push(k[1] + " absent");
    });
    return bits.join(" · ");
  }
  function successHTML(res, ambient) {
    var repos = Array.isArray(res.repos) ? res.repos : [];
    var h = '<div class="ghc-panel" role="status">' +
      '<p class="ghc-panel-h">' + svg(G.ok, 16) + "Connected as <b>@" + TX(res.login || "?") +
      "</b>.</p>";
    h += '<p class="gh-p">This token can reach ' + E(plural(repos.length, "repository", "repositories")) +
      (repos.length ? ":" : ".") + "</p>";
    if (repos.length) {
      h += '<ul class="ghc-repos">' + repos.map(function (r) {
        return '<li><code class="gh-code">' + TX(r.full_name) + "</code>" + visChip(r.private) +
          protChip(r) + (r.enabled ? '<span class="gh-chip iris">Hermes may work here</span>' : "") + "</li>";
      }).join("") + "</ul>";
    }
    h += '<p class="gh-p">Checked: ' + E(checkedLine(res.checks_passed)) + ". " + E(COPY.workflow) +
      '<code class="gh-code">.github/workflows</code>.</p>';
    if (typeof res.expires_at === "number") {
      h += '<p class="gh-p">Expires ' + E(dateLong(res.expires_at)) + ".</p>";
    } else if (res.no_expiry) {
      h += '<p class="gh-warnl">GitHub did not report an expiry date for this token. Give it one ' +
        "(30 days) on GitHub, or Revoke it when you are done.</p>";
    }
    h += reachHTML(res);
    (Array.isArray(res.warnings) ? res.warnings : []).forEach(function (w) {
      h += '<p class="gh-warnl">' + TX(w) + "</p>";
    });
    if (ambient !== undefined) h += ambientHTML(ambient, true);
    return h + "</div>";
  }
  function refusalHTML(ref) {
    var lines = Array.isArray(ref.lines) ? ref.lines : [];
    return '<div class="ghc-panel ref" role="alert">' +
      '<p class="ghc-panel-h">' + svg(G.block, 16) + "Not connected</p>" +
      '<ul class="ghc-lines">' + lines.map(function (l) { return "<li>" + TX(l) + "</li>"; }).join("") +
      "</ul>" + (ref.final ? '<p class="gh-p" style="margin-top:8px">' + TX(ref.final) + "</p>" : "") +
      "</div>";
  }

  // wire one connect component. host: the element holding it. opts: {repaint,
  // onConnected(payload), onSkip}
  function wireConnect(host, st, opts) {
    if (!host || !host.querySelector) return;
    var root = host.querySelector(".ghc") || host;
    var ta = root.querySelector("textarea[data-gh-repos]");
    if (ta) {
      ta.value = st.reposText || "";
      ta.oninput = function () { st.reposText = String(ta.value || ""); };
    }
    var tok = root.querySelector("input[data-gh-token]");
    if (tok) {
      tok.onkeydown = function (ev) {
        if (ev && ev.key === "Enter") { ev.preventDefault(); connectCheck(root, st, opts); }
      };
    }
    var each = function (sel, fn) {
      Array.prototype.slice.call(root.querySelectorAll(sel)).forEach(fn);
    };
    each("button[data-gh]", function (b) {
      var a = b.getAttribute("data-gh");
      if (a === "open") {
        b.onclick = function () { connectOpen(root, st, opts); };
        // warm the (local, pure) create_url answer BEFORE the click, so the click can
        // open GitHub synchronously (see connectOpen)
        b.onpointerenter = b.onfocus = function () { prefetchCreate(root, st); };
      }
      else if (a === "check") b.onclick = function () { connectCheck(root, st, opts); };
      else if (a === "skip") b.onclick = function () {
        clearToken(root);
        if (opts && typeof opts.onSkip === "function") opts.onSkip();
      };
    });
  }
  function clearToken(root) {
    try {
      var t = root && root.querySelector ? root.querySelector("input[data-gh-token]") : null;
      if (t) t.value = "";
      return t;
    } catch (e) { return null; }
  }
  // GET /api/github/create_url (a LOCAL, pure route: no GitHub request) for the
  // repositories in the field, remembered per list. Nothing secret is in it.
  function prefetchCreate(root, st) {
    var ta = root && root.querySelector ? root.querySelector("textarea[data-gh-repos]") : null;
    if (ta) st.reposText = String(ta.value || "");
    var key = parseRepos(st.reposText).join(",");
    var rec = st.createUrl;
    // (10 minutes: the form's description carries today's date)
    if (rec && rec.key === key && (rec.pending || rec.ok) && Date.now() - rec.at < 600000) return rec.p;
    rec = st.createUrl = { key: key, url: "", target: null, ok: false, pending: true, p: null, at: Date.now() };
    rec.p = get("/api/github/create_url?repos=" + encodeURIComponent(key)).then(function (r) {
      rec.pending = false;
      var u = r && r.ok && r.data ? ghUrl(r.data.url) : "";
      if (u) { rec.url = u; rec.ok = true; rec.target = r.data.target_name || null; }
      return rec;
    });
    return rec.p;
  }
  // Open GitHub's prefilled token form in the DEFAULT browser. WebKit only lets
  // window.open through while the owner's click is still being handled, and an
  // await ends that: so a warm answer (prefetched when the pointer reached the
  // button or it got focus) is opened right here, in the click, and only a cold
  // one falls back to fetch-then-open — which a browser may refuse, hence the
  // "press it once more" line (the second press is warm).
  function connectOpen(root, st, opts) {
    var ta = root.querySelector ? root.querySelector("textarea[data-gh-repos]") : null;
    if (ta) st.reposText = String(ta.value || "");
    var repos = parseRepos(st.reposText), key = repos.join(",");
    var again = function () { if (opts && typeof opts.repaint === "function") opts.repaint(); };
    var done = function (rec, warm) {
      st.err = "";
      if (!rec || !rec.ok || !openExternal(rec.url)) {
        st.createHint = "";
        st.err = "Could not open GitHub from here. On github.com go to Settings › Developer " +
          "settings › Personal access tokens › Fine-grained tokens › Generate new token.";
      } else {
        var owners = {};
        repos.forEach(function (x) { owners[String(x.split("/")[0]).toLowerCase()] = 1; });
        st.createHint = (warm ? "GitHub opened in your browser."
          : "GitHub should have opened in your browser. If it did not, press the button once more.") +
          (rec.target
            ? " If it asks for a resource owner, choose " + rec.target + "."
            : Object.keys(owners).length > 1
              ? " Your repositories have different owners: pick the owner on GitHub’s form."
              : "");
      }
      again();
    };
    var rec = st.createUrl;
    if (rec && rec.key === key && rec.ok && Date.now() - rec.at < 600000) { done(rec, true); return Promise.resolve(); }
    return prefetchCreate(root, st).then(function (r) { done(r, false); });
  }
  // Check token. The value is read, the field emptied, and the POST sent — the
  // token exists only in `tok` below until then, and in nothing that outlives
  // this call.
  async function connectCheck(root, st, opts) {
    if (st.busy) return;
    var inp = root.querySelector ? root.querySelector("input[data-gh-token]") : null;
    var tok = inp ? String(inp.value || "") : "";
    if (inp) inp.value = "";
    tok = tok.trim();
    var ta = root.querySelector ? root.querySelector("textarea[data-gh-repos]") : null;
    if (ta) st.reposText = String(ta.value || "");
    st.err = ""; st.refusal = null; st.unchecked = false;
    if (!tok) {
      st.err = "Paste the token first.";
      if (opts && opts.repaint) opts.repaint();
      return;
    }
    st.busy = true;
    if (opts && opts.repaint) opts.repaint();
    var r;
    try {
      r = await post("/api/github/connect", { token: tok, repos: parseRepos(st.reposText) });
    } finally {
      tok = "";
      st.busy = false;
    }
    var d = r.data || {};
    if (r.ok && d.stored === true) {
      st.result = d;
      st.refusal = null;
      S.status = d;
      S.statusErr = "";
      S.notice = null;
      afterConnected(d);
      if (opts && typeof opts.onConnected === "function") { try { opts.onConnected(d); } catch (e) {} }
    } else if (d.refused) {
      st.refusal = { lines: d.lines || [], final: d.final || "", retry: !!d.retry };
    } else if (r.status === 403 && d.error === "owner_only") {
      st.err = OWNER_TEXT;
    } else if (r.status === 400 || r.status === 409) {
      st.err = (d.hint || d.error || "GitHub did not accept that.") +
        (Array.isArray(d.bad) && d.bad.length ? " (" + d.bad.join(", ") + ")" : "");
    } else {
      st.err = "Hermes could not finish checking this token, so nothing was stored. Try again " +
        "in a minute.";
    }
    // whatever the answer: the field is empty (it was emptied above; a repaint
    // re-creates it empty)
    clearToken(root);
    if (opts && opts.repaint) opts.repaint();
  }
  // connected (from the card OR the step): seed the poll and refresh the card.
  // The onboarding step keeps its own copy of the status (read once per page
  // life): a connect made from the card must reach it too, or a later
  // "Run setup" would still say "not connected" and ask for a token again.
  function afterConnected() {
    S.step.status = S.status;
    S.step.read = true;
    loadAmbient(false).then(function () {
      if (S.conn.step.result && typeof S.stepRepaint === "function") { try { S.stepRepaint(); } catch (e) {} }
      paintCard(false);
    });
    seed().then(function () { pollStart(); });
    loadChanges().then(function () { paintCard(false); });
  }

  // ==========================================================================
  // (a) the Settings › Connections card
  // ==========================================================================
  async function readStatus(quiet) {
    S.lastPollAt = Date.now();          // any status read counts toward the 60 s
    var r = await get("/api/github/status" + (quiet ? "?scan=0" : ""));
    if (r.ok && r.data && typeof r.data.connected === "boolean") {
      S.status = r.data;
      S.statusErr = "";
      // ...and any newer answer (the 60 s poll, a card load) corrects that copy
      if (S.step.read) S.step.status = r.data;
      return r.data;
    }
    S.statusErr = r.status === 404
      ? "The GitHub connector is not loaded. Restart the dashboard to load it."
      : "Could not read the GitHub connection.";
    return null;
  }
  async function loadAmbient(fresh) {
    var r = await get("/api/github/ambient" + (fresh ? "?fresh=1" : ""));
    if (r.ok && r.data && r.data.ambient) S.ambient = r.data.ambient;
    return S.ambient;
  }
  async function loadChanges() {
    var r = await get("/api/github/changes");
    if (r.ok && r.data && Array.isArray(r.data.changes)) S.changes = r.data.changes;
    return S.changes;
  }
  async function loadAccess() {
    var r = await get("/api/access");
    S.access = (r.data && Array.isArray(r.data.dirs)) ? r.data.dirs.filter(function (x) {
      return typeof x === "string" && x.charAt(0) === "/";
    }) : [];
    return S.access;
  }
  async function loadCard() {
    var st = await readStatus(false);
    if (st && st.connected) {
      await Promise.all([loadAmbient(false), loadChanges(), loadAccess()]);
      if (!S.seeded) { await seed(); }
      pollStart();
    }
    S.cardLoaded = true;
    S.cardAt = Date.now();
  }

  function sameIds(a, b) {
    return (a || []).slice().sort().join(",") === (b || []).slice().sort().join(",");
  }
  function pendingList() {
    return (S.changes || []).filter(function (c) { return c && c.state === "pending" && ID_RE.test(c.id); });
  }
  function recentPushes() {
    return (S.changes || []).filter(function (c) {
      return c && ID_RE.test(c.id) && (c.state === "pushed" || c.state === "pr_open" ||
        c.state === "retracted" || (c.state === "failed" && c.pushed_sha));
    }).sort(function (a, b) { return (b.decided_at || 0) - (a.decided_at || 0); }).slice(0, 10);
  }
  function insideGranted(p) {
    if (typeof p !== "string" || p.charAt(0) !== "/") return false;
    return (S.access || []).some(function (d) {
      var x = d.replace(/\/+$/, "");
      return x && (p === x || p.indexOf(x + "/") === 0);
    });
  }

  function cardHTML() {
    var st = S.status;
    var n = pendingList().length;
    var h = "<h2>" + svg(G.branch, 17) + "GitHub" +
      (st && st.connected && n ? '<span class="gh-chip warn gh-badge">' + E(n + " waiting") + "</span>" : "") +
      "</h2><div class=\"body\">";
    if (!st) {
      h += '<p class="gh-note">' + E(S.statusErr || "Reading the GitHub connection…") + "</p>";
    } else if (st.connected) {
      h += connectedHTML(st);
    } else {
      if (S.conn.card.result) S.conn.card = newConn();     // a stale success from before a disconnect
      h += noticeHTML();
      h += '<p class="gh-lede">' + E(COPY.sub) + "</p>";
      h += connectHTML(S.conn.card, "ghcard", { intro: true, skip: false });
    }
    return h + "</div>";
  }
  function noticeHTML() {
    var n = S.notice;
    if (!n) return "";
    var cls = n.cls === "bad" ? "gh-err" : n.cls === "warn" ? "gh-warnl" : n.cls === "ok" ? "gh-okl" : "gh-note";
    var h = '<p class="' + cls + '" role="status">' + TX(n.text) + "</p>";
    (n.lines || []).forEach(function (l) { h += '<p class="gh-note">' + TX(l) + "</p>"; });
    if (n.link && ghUrl(n.link)) {
      h += '<div class="ghc-act"><button type="button" class="gh-b" data-gh="link" data-url="' +
        E(ghUrl(n.link)) + '">' + svg(G.ext, 15) + "Open your GitHub tokens page</button></div>";
    }
    return h;
  }

  function connectedHTML(st) {
    var h = "";
    var busy = S.busy;
    var dis = busy ? " disabled" : "";
    if (S.conn.card.result) {
      h += '<div class="ghc">' + successHTML(S.conn.card.result) +
        '<div class="ghc-act"><button type="button" class="gh-b" data-gh="dismiss">Done</button></div></div>';
    }
    h += noticeHTML();
    // ---- needs attention (holds, expiry, drift) ----------------------------
    var att = Array.isArray(st.attention) ? st.attention : [];
    var drift = (Array.isArray(st.drift) ? st.drift : []).filter(function (d) {
      return d && !d.acked && !d.resolved;
    });
    if (att.length) {
      h += '<div class="gh-h"><h3>Needs your attention</h3></div>';
      att.forEach(function (a) {
        var dr = null;
        if (/^drift:/.test(String(a.id || ""))) {
          dr = drift.filter(function (d) { return "drift:" + d.repo + ":" + d.branch === a.id; })[0] || null;
        }
        h += '<div class="gh-att ' + (a.level === "bad" ? "bad" : "warn") + '">' +
          svg(a.level === "bad" ? G.block : G.warn, 15) +
          '<span class="gh-att-t">' + TX(a.text) + (a.since ? ' <span class="gh-note">· since ' +
            E(t12(a.since)) + "</span>" : "") + "</span>" +
          (dr ? '<button type="button" class="gh-b" data-gh="drift-ack" data-repo="' + E(dr.repo) +
            '" data-branch="' + E(dr.branch) + '"' + dis + ">That was me</button>" : "") +
          "</div>";
      });
    }
    // ---- account --------------------------------------------------------------
    var exp = "";
    if (typeof st.expires_at === "number") {
      var days = typeof st.expires_in_days === "number" ? st.expires_in_days : null;
      exp = "Expires " + dateLong(st.expires_at) + (days !== null
        ? (days < 0 ? " (expired)" : days < 1 ? " (within a day)" : " (in " + plural(Math.floor(days), "day") + ")")
        : "");
    } else if (st.no_expiry) {
      exp = "GitHub did not report an expiry date for this key.";
    }
    var expWarn = st.no_expiry || (typeof st.expires_in_days === "number" && st.expires_in_days <= 7);
    h += '<div class="gh-h"><h3>Account</h3></div>';
    h += '<div class="gh-row"><div class="gh-row-txt"><span class="gh-row-l">@' + TX(st.login || "?") +
      '<span class="gh-chip">Fine-grained token</span></span>' +
      '<span class="gh-row-d' + (expWarn ? '" style="color:var(--warn)' : "") + '">' + E(exp) + "</span></div>" +
      '<div class="gh-row-ctl">' +
      '<button type="button" class="gh-b" data-gh="check-now"' + dis + ">" +
        (busy === "check" ? "Checking…" : "Check now") + "</button>" +
      '<button type="button" class="gh-b" data-gh="disconnect"' + dis + ">Disconnect</button>" +
      '<button type="button" class="gh-b gh-bad" data-gh="revoke"' + dis + ">Revoke now</button>" +
      "</div></div>";
    h += reachHTML(st);
    // ---- the kill switch --------------------------------------------------------
    var p = st.paused || {};
    var holds = Array.isArray(p.holds) ? p.holds : [];
    var sw = !!p.by_owner_switch;
    h += '<div class="gh-row"><div class="gh-row-txt"><span class="gh-row-l">Pause GitHub writes</span>' +
      '<span class="gh-row-d">' + E(holds.length
        ? "Writes are held until this is fixed: " + holds.map(function (x) { return x.text || x.id; }).join(" ")
        : "Stops every new change request and every push at once. Reading and the checks keep running.") +
      "</span></div>" +
      '<div class="gh-row-ctl"><label class="gh-sw"><input type="checkbox" role="switch" data-gh="pause"' +
      (sw ? " checked" : "") + dis + ' aria-label="Pause GitHub writes">' +
      '<span class="gh-sw-t" aria-hidden="true"></span><span class="gh-sw-l">' +
      (st.writes_paused ? "Paused" : "Off") + "</span></label></div></div>";
    var pb = st.push_budget;
    if (pb && typeof pb.limit === "number") {
      h += '<p class="gh-note">Pushes in the last 24 hours: ' + E((pb.used || 0) + " of " + pb.limit) +
        ". Every push messages you on Telegram.</p>";
    }
    // ---- waiting for review --------------------------------------------------
    var pend = pendingList();
    if (pend.length) {
      markSeenIfVisible(pend);
      h += '<div class="gh-h"><h3>Waiting for your review</h3><span>' + E(String(pend.length)) + "</span></div>";
      pend.forEach(function (c) {
        h += '<div class="gh-row"><div class="gh-row-txt"><span class="gh-row-l">' + TX(c.title) +
          (c.blocked ? '<span class="gh-chip bad">Blocked</span>' : "") + "</span>" +
          '<span class="gh-row-d">' + TX(c.repo) + " · " + TX(c.branch) + " · requested " +
          E(t12(c.requested_at)) + "</span></div>" +
          '<div class="gh-row-ctl"><button type="button" class="gh-b gh-go" data-gh="review" data-id="' +
          E(c.id) + '">Review</button></div></div>';
      });
    }
    // ---- repositories ------------------------------------------------------------
    var repos = Array.isArray(st.repos) ? st.repos : [];
    h += '<div class="gh-h"><h3>Repositories</h3><span>' + E(repos.length ? "Hermes only works on the ones you enable"
      : "None yet") + "</span></div>";
    repos.forEach(function (r, i) {
      var key = String(r.full_name || "");
      var rb = busy === "repo:" + key;
      var why = r.enabled ? (r.writes_ok === false ? r.writes_blocked : "") : (r.enable_blocked || "");
      var sb = typeof r.sandbox === "string" ? r.sandbox : "";
      var finder = r.prepared && insideGranted(sb);
      h += '<div class="gh-row"><div class="gh-row-txt"><span class="gh-row-l"><span class="gh-mono">' +
        TX(key) + "</span>" + visChip(r.private) + protChip(r) +
        (r.enabled ? '<span class="gh-chip iris">Enabled</span>' : "") + "</span>" +
        (why ? '<span class="gh-row-d" style="color:var(--warn)">' + TX(why) + "</span>" : "") +
        (r.protection && r.protection.state === "not_protected" && r.protection.gap_text
          ? '<span class="gh-row-d">' + TX(r.protection.gap_text) + "</span>" : "") +
        (sb ? '<span class="gh-row-d">Sandbox: <code class="gh-code">' + TX(sb) + "</code>" +
          (r.prepared ? "" : " (not prepared yet)") + "</span>" : "") +
        (r.last_check ? '<span class="gh-row-d">Last checked ' + E(t12(r.last_check)) +
          (r.check_error ? " · " + TX(r.check_error) : "") + "</span>" : "") +
        "</div><div class=\"gh-row-ctl\">" +
        (finder ? '<button type="button" class="gh-b" data-gh="finder" data-i="' + i + '"' + dis + ">" +
          svg(G.folder, 15) + "Show in Finder</button>" : "") +
        (r.enabled
          ? '<button type="button" class="gh-b" data-gh="repo-off" data-i="' + i + '"' + dis + ">" +
            (rb ? "Working…" : "Disable") + "</button>"
          : '<button type="button" class="gh-b" data-gh="repo-on" data-i="' + i + '"' + dis + ">" +
            (rb ? "Checking…" : "Enable") + "</button>") +
        "</div></div>";
    });
    // ---- recent pushes --------------------------------------------------------
    h += recentHTML(st);
    // ---- the recommended rule ------------------------------------------------
    var rs = st.ruleset || {};
    if (rs.recommended) {
      h += '<div class="gh-h"><h3>Recommended GitHub rule</h3><span>Protect your default branch on GitHub itself</span></div>';
      h += '<p class="gh-p">' + E(rs.text || "") + "</p>" +
        '<p class="gh-note">On GitHub: ' + E(rs.path || "Settings › Rules › Rulesets") +
        ", then paste this:</p>" +
        '<pre class="gh-pre" tabindex="0" aria-label="Recommended ruleset JSON">' +
        E(JSON.stringify(rs.recommended, null, 2)) + "</pre>" +
        '<div class="ghc-act"><button type="button" class="gh-b" data-gh="copy-rule">' + svg(G.copy, 15) +
        "Copy</button></div>";
      if (rs.light) {
        h += '<details class="gh-more"><summary>' + svg(G.chev, 14, "gh-chev") +
          "A lighter rule, if you push straight to your default branch yourself</summary>" +
          '<p class="gh-note">A lighter rule that only blocks force-pushes and deletion:</p>' +
          '<pre class="gh-pre" tabindex="0" aria-label="Lighter ruleset JSON">' +
          E(JSON.stringify(rs.light, null, 2)) + "</pre>" +
          '<div class="ghc-act"><button type="button" class="gh-b" data-gh="copy-light">' + svg(G.copy, 15) +
          "Copy</button></div></details>";
      }
    }
    // ---- other GitHub access on this Mac ----------------------------------------
    h += ambientHTML(S.ambient, false);
    // ---- the honest custody note + check schedule --------------------------------
    var ck = st.check || {};
    h += '<div class="gh-h"><h3>How this is protected</h3></div>';
    if (st.headline) h += '<p class="gh-note">' + E(reachHeadline(st)) + "</p>";
    if (ck.last) {
      h += '<p class="gh-note">Last read-only check ' + E(t12(ck.last)) +
        (ck.ok === false && ck.error ? " · it failed: " + TX(ck.error) : "") +
        (ck.next ? " · next around " + E(t12(ck.next)) : "") + ".</p>";
    }
    return h;
  }

  // E08: only write_reach "contained" means the scope wall PROVED the key
  // cannot write to a repository the owner did not pick. Anything else that is
  // present ("unchecked", "all_repositories", …) is said as a warning, and the
  // headline's unqualified "can only touch" claim is softened. Absent (an
  // older connector): today's rendering.
  var REACH_FALLBACK = "Hermes could not check whether this key reaches repositories you did not pick.";
  var REACH_CLAIM = "the key can only touch the repositories you picked";
  function reachUnproven(st) {
    return !!st && st.write_reach != null && st.write_reach !== "contained";
  }
  function reachHTML(st) {
    if (!reachUnproven(st)) return "";
    var note = typeof st.write_reach_note === "string" && st.write_reach_note.trim() ? st.write_reach_note : REACH_FALLBACK;
    return '<p class="gh-warnl" data-gh-reach role="note">' + TX(note) + "</p>";
  }
  function reachHeadline(st) {
    var t = String(st.headline || "");
    if (!reachUnproven(st)) return t;
    return t.split(REACH_CLAIM).join("the key is meant to touch only the repositories you picked (see the warning " +
      "under Account)");
  }

  function recentHTML(st) {
    var rows = [];
    if (Array.isArray(st.recent_pushes)) {
      // a ledger-shaped list, when the connector provides one
      rows = st.recent_pushes.slice(0, 10).map(function (x) {
        return { title: x.title || (x.branch || ""), repo: x.repo, branch: x.branch, when: x.ts,
                 sha: x.sha, url: x.pr_url, num: x.pr_number, state: x.event || "push", id: x.change_id };
      });
    } else {
      rows = recentPushes().map(function (c) {
        var pr = c.pr || {};
        return { title: c.title, repo: c.repo, branch: c.branch, when: c.decided_at, sha: c.pushed_sha,
                 url: pr.url, num: pr.number, existing: !!pr.existing, state: c.state, id: c.id };
      });
    }
    if (!rows.length) return "";
    var h = '<div class="gh-h"><h3>Recent pushes</h3><span>Newest first</span></div>';
    rows.forEach(function (x) {
      var chip = x.state === "pr_open" ? '<span class="gh-chip ok">' + (x.existing ? "PR updated" : "Draft PR open") + "</span>"
        : x.state === "retracted" ? '<span class="gh-chip">Retracted</span>'
        : x.state === "failed" ? '<span class="gh-chip bad">Unconfirmed</span>'
        : '<span class="gh-chip iris">Pushed</span>';
      var url = ghUrl(x.url);
      h += '<div class="gh-row"><div class="gh-row-txt"><span class="gh-row-l">' + TX(x.title) + chip + "</span>" +
        '<span class="gh-row-d">' + TX(x.repo) + " · " + TX(x.branch) +
        (sha7(x.sha) ? " @ " + E(sha7(x.sha)) : "") + (x.when ? " · " + E(t12(x.when)) : "") +
        "</span></div><div class=\"gh-row-ctl\">" +
        (url ? '<button type="button" class="gh-b" data-gh="link" data-url="' + E(url) + '">' + svg(G.ext, 15) +
          (typeof x.num === "number" ? "PR #" + E(String(x.num)) : "Pull request") + "</button>" : "") +
        (ID_RE.test(String(x.id || "")) ? '<button type="button" class="gh-b" data-gh="review" data-id="' +
          E(x.id) + '">Details</button>' : "") +
        "</div></div>";
    });
    return h;
  }

  function ambientHTML(amb, compact) {
    var h = '<div class="gh-h"><h3>Other GitHub access on this Mac</h3><span>Hermes changes none of this</span></div>';
    if (!amb) {
      return h + '<p class="gh-note">Not checked yet.</p>' + (compact ? "" :
        '<div class="ghc-act"><button type="button" class="gh-b" data-gh="ambient">Check this Mac</button></div>');
    }
    var items = (Array.isArray(amb.items) ? amb.items : []).filter(function (i) { return i && i.present; });
    var someUnknown = (Array.isArray(amb.items) ? amb.items : []).some(function (i) { return i && i.present === null; });
    if (!items.length && !someUnknown) {
      h += '<p class="gh-p">Hermes found no other GitHub access on this Mac: no signed-in GitHub CLI, ' +
        "no saved github.com password for git, no SSH key loaded for GitHub and no GitHub token in " +
        "its environment.</p>";
    } else if (!items.length) {
      h += '<p class="gh-p">Hermes found no other GitHub access it could read, but not every place could be checked:</p>';
    }
    items.forEach(function (it, i) {
      h += '<div class="gh-att warn">' + svg(G.warn, 15) + '<span class="gh-att-t"><b>' + E(it.title || it.id) +
        "</b> — " + codeSpans(it.detail || "") +
        (Array.isArray(it.commands) ? it.commands.map(function (c, j) {
          return '<span class="gh-amb-c"><span class="gh-amb-l">' + E(c.label || "") + "</span>" +
            '<span class="gh-amb-row"><code class="gh-code">' + E(c.cmd || "") + "</code>" +
            (compact ? "" : '<button type="button" class="gh-b gh-sq" data-gh="copy-cmd" data-i="' + i +
              '" data-j="' + j + '" aria-label="Copy the command" title="Copy the command">' + svg(G.copy, 15) +
              "</button>") + "</span></span>";
        }).join("") : "") + "</span></div>";
    });
    // a probe that could not answer (present: null) is said, never read as "nothing found"
    var unknown = (Array.isArray(amb.items) ? amb.items : []).filter(function (i) { return i && i.present === null; });
    unknown.forEach(function (it) {
      h += '<p class="gh-note"><b>' + E(it.title || it.id) + "</b> — could not be checked. " +
        codeSpans(it.detail || "") + "</p>";
    });
    if (items.length && amb.accept_text) h += '<p class="gh-note">' + E(amb.accept_text) + "</p>";
    if (amb.note) h += '<p class="gh-note">' + E(amb.note) +
      (amb.checked_at ? " Checked " + E(t12(amb.checked_at)) + "." : "") + "</p>";
    if (!compact) h += '<div class="ghc-act"><button type="button" class="gh-b" data-gh="ambient"' +
      (S.ambientBusy ? " disabled" : "") + ">Check again</button></div>";
    return h;
  }

  function markSeenIfVisible(list) {
    var d = D();
    try {
      var panel = d && d.getElementById(PANEL_ID);
      var mind = d && d.getElementById("view-mind");
      if (!panel || panel.hidden || (mind && mind.hidden)) return;
      list.forEach(function (c) { S.seen[c.id] = 1; });
    } catch (e) {}
  }

  var relocatedOnce = false;
  function paintCard(force) {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    if (!panel) return;                    // wait for the shell; never #view-mind
    ensureCSS();
    var body = panel.querySelector(".set-body") || panel;
    var el = d.getElementById(CARD_ID);
    if (!el) {
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass set-legacy";
      var anchor = body.querySelector("[data-legacy-slot]");
      if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(el, anchor);
      else body.appendChild(el);
    } else if (el.parentNode !== body && !body.contains(el)) {
      try { body.appendChild(el); } catch (e) {}
    }
    // a field the owner is typing in (the token!) is never wiped by a repaint
    // that the owner did not cause
    if (!force && focusedField(el)) { S.cardDirty = true; return; }
    var ta = el.querySelector ? el.querySelector("textarea[data-gh-repos]") : null;
    if (ta) S.conn.card.reposText = String(ta.value || "");
    S.cardDirty = false;
    // a disclosure the owner opened (the lighter rule) stays open across the repaint
    var keepOpen = !!(el.querySelector && el.querySelector("details.gh-more[open]"));
    try { el.innerHTML = cardHTML(); } catch (e) { return; }
    if (keepOpen) {
      try { var dm = el.querySelector("details.gh-more"); if (dm) dm.setAttribute("open", ""); } catch (e) {}
    }
    wireCard(el);
    if (!relocatedOnce) {
      relocatedOnce = true;
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  function wireCard(el) {
    if (!el || !el.querySelectorAll) return;
    var cc = el.querySelector(".ghc");
    if (cc && !S.conn.card.result) {
      wireConnect(el, S.conn.card, { repaint: function () { paintCard(true); } });
    }
    Array.prototype.slice.call(el.querySelectorAll("[data-gh]")).forEach(function (b) {
      var a = b.getAttribute("data-gh");
      if (a === "pause") { b.onchange = function () { setPause(!!b.checked); }; return; }
      if (a === "open" || a === "check" || a === "skip") return;   // wireConnect's
      b.onclick = function () { cardAct(a, b); };
    });
  }

  async function cardAct(a, b) {
    var st = S.status || {};
    var repos = Array.isArray(st.repos) ? st.repos : [];
    var idx = parseInt(b.getAttribute("data-i"), 10);
    if (a === "review") { var id = b.getAttribute("data-id"); if (ID_RE.test(String(id || ""))) openReview(id); return; }
    if (a === "link") { if (!openExternal(b.getAttribute("data-url"))) say("Only github.com links open from here."); return; }
    if (a === "dismiss") { S.conn.card.result = null; paintCard(true); return; }
    if (a === "copy-rule") { copyAndSay(JSON.stringify((st.ruleset || {}).recommended, null, 2), "The rule was copied"); return; }
    if (a === "copy-light") { copyAndSay(JSON.stringify((st.ruleset || {}).light, null, 2), "The rule was copied"); return; }
    if (a === "copy-cmd") {
      var items = ((S.ambient && S.ambient.items) || []).filter(function (i) { return i && i.present; });
      var it = items[idx], c = it && it.commands && it.commands[parseInt(b.getAttribute("data-j"), 10)];
      if (c && c.cmd) copyAndSay(c.cmd, "The command was copied");
      return;
    }
    if (a === "ambient") {
      S.ambientBusy = true; paintCard(true);
      await loadAmbient(true);
      S.ambientBusy = false; paintCard(true);
      return;
    }
    if (a === "finder") {
      var row = repos[idx];
      if (!row || !row.sandbox) return;
      var rr = await post("/api/files/reveal", { path: row.sandbox });
      if (!rr.ok) say("Could not show that folder in Finder.");
      return;
    }
    if (S.busy) return;
    if (a === "check-now") return cardPost("check", "/api/github/check", {}, function (d) {
      var c = d.check || {};
      return c.ok === false ? { cls: "bad", text: "The check failed: " + (c.error || "unknown error") + "." }
        : { cls: "ok", text: "Checked with GitHub just now." };
    });
    if (a === "disconnect") {
      if (!ask("Disconnect GitHub? Hermes deletes the key from this Mac and cancels changes waiting for " +
        "review. Your sandbox folders stay. The key keeps working on GitHub until it expires, unless " +
        "you delete it there or use Revoke now.")) return;
      return cardPost("disconnect", "/api/github/disconnect", {}, function (d) {
        forgetConnected();
        return { cls: "", text: d.hint || "Disconnected.", link: d.manage_url,
                 lines: (d.sandboxes_kept || []).length ? ["Sandboxes kept: " + d.sandboxes_kept.join(", ")] : [] };
      });
    }
    if (a === "revoke") {
      if (!ask("Revoke this key on GitHub now? GitHub stops accepting it everywhere and emails you " +
        "about it. Hermes then disconnects. This cannot be undone.")) return;
      return cardPost("revoke", "/api/github/revoke", {}, function (d) {
        forgetConnected();
        return { cls: d.revoked ? "ok" : "warn", text: d.message || "Revoked.", link: d.manage_url };
      });
    }
    if (a === "repo-on" || a === "repo-off") {
      var r = repos[idx];
      if (!r || !r.full_name) return;
      return cardPost("repo:" + r.full_name, "/api/github/repos",
        { op: a === "repo-on" ? "add" : "remove", repo: r.full_name }, function (d) {
          var ad = d.added;
          if (ad && !ad.enabled) return { cls: "warn", text: ad.full_name + " could not be enabled: " +
            (ad.enable_blocked || "GitHub refused it.") };
          return null;
        });
    }
    if (a === "drift-ack") {
      return cardPost("drift", "/api/github/drift/ack",
        { repo: b.getAttribute("data-repo"), branch: b.getAttribute("data-branch") }, function () { return null; });
    }
  }
  function forgetConnected() {
    pollStop();
    S.conn.card = newConn();
    S.conn.step = newConn();
    S.step.status = null;
    S.step.read = false;
  }
  function ask(msg) {
    try { return typeof W.confirm === "function" ? !!W.confirm(msg) : false; } catch (e) { return false; }
  }
  // one owner action from the card: the answer is a status payload on success
  async function cardPost(tag, url, body, onOk) {
    S.busy = tag;
    S.notice = null;
    paintCard(true);
    var r = await post(url, body);
    S.busy = "";
    var d = r.data || {};
    if (r.ok && typeof d.connected === "boolean") {
      S.status = d;
      S.notice = onOk ? onOk(d) : null;
    } else if (r.status === 403 && d.error === "owner_only") {
      S.notice = { cls: "bad", text: OWNER_TEXT };
    } else if (d.refused) {
      S.notice = { cls: "bad", text: d.held ? "GitHub writes are now held:" : "GitHub refused it:",
                   lines: (d.lines || []).concat(d.final ? [d.final] : []) };
    } else if (r.status === 409 && d.error === "held") {
      S.notice = { cls: "warn", text: d.hint || "Writes stay paused until this is fixed.",
                   lines: (d.holds || []).map(function (x) { return x.text || x.id; }) };
    } else {
      S.notice = { cls: r.status === 429 ? "warn" : "bad", text: d.hint || (d.error ? "That did not work (" + d.error + ")." :
        "Could not reach the dashboard.") };
    }
    if (r.ok) loadChanges().then(function () { paintCard(false); });
    paintCard(true);
    return r;
  }
  async function setPause(on) {
    if (S.busy) { paintCard(true); return; }
    return cardPost("pause", "/api/github/pause", { paused: on === true, source: "dashboard" }, function (d) {
      return { cls: on ? "warn" : "ok", text: on ? "GitHub writes are paused." : (d.writes_paused
        ? "Your switch is off, but writes are still held (see above)." : "GitHub writes are on again.") };
    });
  }

  async function mount() {
    if (!S.cardLoaded || (Date.now() - (S.cardAt || 0)) >= REFRESH_MS) {
      try { await loadCard(); } catch (e) {}
    }
    paintCard(false);
  }

  // Settings › Connections, with this card on screen
  function openCard() {
    try { if (typeof W.setView === "function") W.setView("mind"); } catch (e) {}
    try { if (typeof W.settingsEnsureShell === "function") W.settingsEnsureShell(); } catch (e) {}
    try { if (typeof W.settingsShow === "function") W.settingsShow("connections"); } catch (e) {}
    return mount().then(function () {
      var d = D(), el = d && d.getElementById(CARD_ID);
      if (el) {
        try { el.scrollIntoView({ block: "start", behavior: anim().live && anim().live() ? "smooth" : "auto" }); } catch (e) {}
      }
      return !!el;
    });
  }

  // ==========================================================================
  // (b) the review sheet
  // ==========================================================================
  function newReview(id) {
    return { open: true, id: id, change: null, loading: true, err: "", acks: {}, confirmPublic: false,
             title: null, body: null, busy: "", result: null, msg: null, missing: [], diffAll: false,
             lastFocus: null, back: null, handle: null };
  }
  function setChange(R, c) {
    R.change = c;
    R.title = typeof c.title === "string" ? c.title : "";
    R.body = typeof c.body === "string" ? c.body : "";
    R.acks = {};
    R.confirmPublic = false;
    R.missing = [];
    // the fields on screen belong to the OLD content: the next repaint must
    // not read them back over the new text
    try {
      var n = sheetEl();
      var t = n && n.querySelector("input[data-ghr-title]"), b = n && n.querySelector("textarea[data-ghr-body]");
      if (t) t.__ghWired = false;
      if (b) b.__ghWired = false;
    } catch (e) {}
  }
  function warnAcks(c) {
    var out = [];
    ((c && c.checks) || []).forEach(function (k) {
      if (k && k.level === "warn" && typeof k.ack === "string" && out.indexOf(k.ack) < 0) out.push(k.ack);
    });
    ((c && c.needs_ack) || []).forEach(function (a) {
      if (typeof a === "string" && out.indexOf(a) < 0) out.push(a);
    });
    return out;
  }
  function blockChecks(c) {
    return ((c && c.checks) || []).filter(function (k) { return k && k.level === "block"; });
  }
  // PURE: the files this sheet does NOT show the content of, from the file
  // list AND the diff's own binary sections, and which of them no warn/block
  // C-binary check names (an older server sent C-binary as info, which asks
  // for nothing: then the sheet asks itself). "Shown" is what the diff DRAWS:
  // a section git printed as text, or a binary section with its whole text
  // preview — never a preview that is cut short, empty, or not drawn at all
  // (no section of its own, or a header two files share).
  function unseenFiles(c) {
    var out = [], seen = {}, drawn = {};
    var secs = diffModel(c && c.diff, c && c.files, Infinity).secs;
    secs.forEach(function (s) { if (s.name && (!s.binary || (s.preview !== null && !s.previewCut))) drawn[s.name] = 1; });
    var add = function (p, always) {
      if (!p || seen[p] || (!always && drawn[p])) return;
      seen[p] = 1;
      out.push(p);
    };
    ((c && c.files) || []).forEach(function (f) { if (f && f.binary === true) add(String(f.path || ""), false); });
    // a binary section without its whole preview is not shown, whatever else
    // carries the same name
    secs.forEach(function (s) { if (s.binary && (s.preview === null || s.previewCut)) add(s.name || "?", true); });
    var covered = function (p) {
      return ((c && c.checks) || []).some(function (k) {
        if (!k || (k.level !== "warn" && k.level !== "block")) return false;
        if (String(k.id || "").split(":")[0] !== "C-binary") return false;
        return k.path === p || (Array.isArray(k.paths) && k.paths.indexOf(p) >= 0);
      });
    };
    return { all: out, uncovered: out.filter(function (p) { return !covered(p); }) };
  }
  // PURE: ticks the SHEET asks for on top of the server's (never sent: the
  // server checks only its own acks). A warn check with no ack string is still
  // a warning; a file not shown and a diff the server cut at 1 MB are approved
  // only with a tick that says so.
  function uiAcks(c) {
    var out = [];
    ((c && c.checks) || []).forEach(function (k, i) {
      if (k && k.level === "warn" && typeof k.ack !== "string") out.push({ key: "ui:warn:" + i, check: k });
    });
    var u = unseenFiles(c).uncovered;
    if (u.length) {
      out.push({ key: "ui:binary", paths: u,
        text: "I am approving " + plural(u.length, "file") + " whose content is not shown here." });
    }
    if (c && c.diff_truncated === true) {
      out.push({ key: "ui:cut", text: "The diff is cut at 1 MB. I have read the rest in the sandbox, and I " +
        "approve what is not shown here." });
    }
    return out;
  }
  // PURE: what the server will make of an EDITED description, in bytes, a
  // little on the high side: CRs become newlines, hidden characters become
  // ⟦U+XXXX⟧ markers, and each @ may be wrapped in a code span (_ghc_fit).
  function utf8Len(t) {
    t = String(t == null ? "" : t);
    var b = 0;
    for (var i = 0; i < t.length; i++) {
      var cc = t.charCodeAt(i);
      if (cc < 0x80) b += 1;
      else if (cc < 0x800) b += 2;
      else if (cc >= 0xd800 && cc < 0xdc00 && i + 1 < t.length) { b += 4; i++; }
      else b += 3;
    }
    return b;
  }
  // ...and untrusted.body()'s neutralise() grows "<|x|>", "<think", "<<<" and
  // ">>>" (a "<<<" costs a byte more: 2,640 of them made 10,560 bytes); a
  // mention's code span is delimited by a backtick run one wider than any the
  // text uses (and any a stripped <!-- --> joins), plus a space each side.
  function backtickWidth(s) {
    var used = {}, w = 1;
    (s.match(/`+/g) || []).forEach(function (r) { used[r.length] = 1; used[r.length - 1] = 1; });
    while (used[w]) w++;
    return w;
  }
  function bodyCost(s) {
    s = String(s == null ? "" : s).replace(/\r\n?/g, "\n");
    var t = markHidden(s)
      .replace(/<\|([^|<>\n]{0,40})\|>/g, "<| $1 |>")
      .replace(/<(\/?)(tool_call|tool_response|think|tools|function|system|im_start|im_end)\b/gi, "< $1$2")
      .split("<<<").join("< <<").split(">>>").join("> >>");
    var w = Math.max(backtickWidth(t), backtickWidth(t.replace(/<!--[\s\S]*?-->/g, "")));
    return utf8Len(t) + (2 * w + 2) * (s.match(/@|＠|&#0*64;|&#x0*40;|&commat;/gi) || []).length;
  }
  function bodyEdited(c, R) { return typeof (R || {}).body === "string" && R.body !== (c.body || ""); }
  // PURE: may the owner approve this change in this state of the sheet?
  function gate(c, R) {
    R = R || {};
    if (!c) return { ok: false, reason: "" };
    if (c.state !== "pending") return { ok: false, reason: "This change is no longer waiting for a decision." };
    if (R.busy) return { ok: false, reason: R.busy === "reject" ? "Rejecting…" : "Working…" };
    if (c.approvable !== true || c.blocked === true || blockChecks(c).length) {
      return { ok: false, reason: "A check blocks this change, so it cannot be approved. Reject it, or ask " +
        "Hermes to fix the sandbox and request it again." };
    }
    var left = warnAcks(c).concat(uiAcks(c).map(function (u) { return u.key; }))
      .filter(function (a) { return !(R.acks && R.acks[a]); });
    if (left.length) return { ok: false, reason: "Tick every warning above to approve (" + left.length + " left)." };
    var hidden = R.diffAll === true ? 0 : diffModel(c.diff, c.files, DIFF_LINES).left;
    if (hidden > 0) {
      return { ok: false, reason: "Show the remaining " + nplural(hidden, "line") + " of the diff to approve: " +
        "you approve only what you have seen." };
    }
    if (c.confirm_public === true && R.confirmPublic !== true) {
      return { ok: false, reason: "Confirm that this repository is public to approve." };
    }
    if (bodyEdited(c, R) && bodyCost(R.body) > BODY_MAX) {
      return { ok: false, reason: "The description is too long: Hermes sends at most 8 KB (" + num(BODY_MAX) +
        " bytes) and would cut the rest after you approve. Shorten it to approve." };
    }
    var t = typeof R.title === "string" ? R.title : c.title;
    if (!String(t || "").trim()) return { ok: false, reason: "The pull request needs a title." };
    return { ok: true, reason: "Approving pushes one commit to " + (c.branch || "a hermes/ branch") +
      ". Nothing is merged." };
  }
  // PURE: the decide body. Title/body only when the owner edited them (the
  // server re-sanitises edited text; unedited text is already the final text).
  function decideBody(c, R, decision) {
    R = R || {};
    var b = { id: c.id, digest: c.digest, decision: decision };
    if (decision === "reject") return b;
    b.acks = warnAcks(c).filter(function (a) { return !!(R.acks && R.acks[a]); });
    if (c.confirm_public === true && R.confirmPublic === true) b.confirm_public = true;
    if (typeof R.title === "string" && R.title !== (c.title || "")) b.title = R.title;
    if (typeof R.body === "string" && R.body !== (c.body || "")) b.body = R.body;
    return b;
  }

  var STATE_LINE = {
    pending: ["", "Waiting for your decision"],
    pushing: ["warn", "Pushing…"],
    pushed: ["ok", "Pushed"],
    pr_open: ["ok", "Pushed · pull request open"],
    rejected: ["", "You rejected this change. Nothing was pushed."],
    withdrawn: ["", "Hermes withdrew this change. Nothing was pushed."],
    expired: ["", "This change expired without a decision. Nothing was pushed."],
    failed: ["bad", "This change failed."],
    cancelled: ["", "This change was cancelled. Nothing was pushed."],
    retracted: ["", "Retracted: the branch was deleted on GitHub."]
  };
  function whatText(c) {
    var def = c.default_branch || "the default branch";
    var t;
    if (c.mode === "append") {
      t = "Hermes adds one commit on top of " + c.branch + " on " + c.repo + " (it never force-pushes). " +
        "With “Push and open draft PR” it updates that branch’s open pull request, or opens a draft " +
        "one into " + def + ".";
    } else {
      t = "Hermes pushes one commit to a new branch, " + c.branch + ", on " + c.repo + ". With “Push and " +
        "open draft PR” it also opens a draft pull request into " + def + ".";
    }
    t += " Nothing is merged, " + def + " is never touched, and Telegram tells you what was pushed.";
    if (c.private === false) t += " This repository is public: the commit is visible to everyone the moment it is pushed.";
    return t;
  }
  function sanitizedNote(s) {
    if (!s || typeof s !== "object") return "";
    var n = function (v) { return v === true ? 1 : (typeof v === "number" && v > 0 ? v : 0); };
    var parts = [];
    if (n(s.comments_stripped)) parts.push(s.comments_stripped === true ? "removed hidden comments"
      : "removed " + plural(s.comments_stripped, "hidden comment"));
    if (n(s.mentions)) parts.push(s.mentions === true ? "wrapped @mentions so nobody is notified"
      : "wrapped " + plural(s.mentions, "@mention") + " so nobody is notified");
    if (n(s.hidden_chars)) parts.push(s.hidden_chars === true ? "removed hidden characters"
      : "removed " + plural(s.hidden_chars, "hidden character"));
    if (s.title_cut) parts.push("shortened the title");
    if (s.body_cut) parts.push("cut the description at 8 KB");
    return parts.length ? "Before showing you this text, Hermes " + parts.join(", ") + "." : "";
  }

  function checkWhere(k) {
    var h = "";
    if (typeof k.path === "string" && k.path) {
      h += ' <code class="gh-code">' + TX(k.path) + (typeof k.line === "number" && k.line > 0 ? ":" + k.line : "") +
        "</code>";
    }
    var list = function (label, arr) {
      if (!Array.isArray(arr) || !arr.length) return "";
      return '<span class="gh-note" style="display:block">' + E(label) + " " + arr.slice(0, 20).map(function (p) {
        return '<code class="gh-code">' + TX(p) + "</code>";
      }).join(" ") + (arr.length > 20 ? " …" : "") + "</span>";
    };
    h += list("Files:", k.paths);
    h += list("Workflows:", k.workflows);
    h += list("Edited files it runs:", k.files);
    if (typeof k.size === "number" && k.level === "info") {
      h += '<span class="gh-note" style="display:block">' + E(bytes(k.size)) +
        (k.sha256 ? " · sha256 " + E(String(k.sha256).slice(0, 16)) + "…" : "") + "</span>";
    }
    if (Array.isArray(k.samples) && k.samples.length) {
      h += '<ul class="ghr-samples">' + k.samples.slice(0, 20).map(function (s) {
        s = s || {};
        var where = s.path ? '<code class="gh-code">' + TX(s.path) +
          (typeof s.line === "number" && s.line > 0 ? ":" + s.line : "") + "</code> " : "";
        return "<li>" + where + (s.sample != null && s.sample !== s.path ? TX(s.sample) : "") + "</li>";
      }).join("") + "</ul>";
      var total = typeof k.total === "number" ? k.total : k.samples.length;
      if (total > Math.min(20, k.samples.length)) {
        h += '<span class="gh-note" style="display:block">and ' + E(String(total - Math.min(20, k.samples.length))) +
          " more</span>";
      }
    }
    if (typeof k.more === "number" && k.more > 0) {
      h += '<span class="gh-note" style="display:block">and ' + E(plural(k.more, "more finding")) + "</span>";
    }
    return h;
  }
  var LEVEL = {
    block: ["bad", "Blocks approval", G.block],
    warn: ["warn", "Needs your tick", G.warn],
    info: ["", "Info", G.info],
    confirm: ["iris", "Confirm", G.info]
  };
  function checksHTML(c, R) {
    R = R || {};
    var checks = Array.isArray(c.checks) ? c.checks.slice() : [];
    var pending = c.state === "pending";
    if (c.confirm_public === true && !checks.some(function (k) { return k && k.level === "confirm"; })) {
      checks.push({ id: "C-public", level: "confirm",
        text: "I understand this is public. It is visible to everyone the moment it is pushed." });
    }
    var order = { block: 0, warn: 1, confirm: 2, info: 3 };
    checks.sort(function (a, b) { return (order[a.level] || 4) - (order[b.level] || 4); });
    var nb = checks.filter(function (k) { return k.level === "block"; }).length;
    var nw = checks.filter(function (k) { return k.level === "warn"; }).length;
    // a file whose content is not shown is never summed up as "nothing blocks"
    var unseen = unseenFiles(c).all.length;
    var ui = pending ? uiAcks(c).filter(function (u) { return u.key.indexOf("ui:warn:") !== 0; }) : [];
    var head = [nb ? plural(nb, "blocks", "block") : "", nw ? plural(nw, "warning") : "",
                unseen ? plural(unseen, "file") + " not shown" : ""].filter(Boolean).join(" · ");
    var h = '<div class="gh-h"><h2>Checks</h2><span>' + E(head ||
      (checks.length ? "nothing blocks this change" : "nothing to report")) + "</span></div>";
    if (!checks.length && !ui.length) {
      return h + '<p class="gh-note">' + (unseen ? E(plural(unseen, "file") + " in this change " +
        (unseen === 1 ? "is" : "are") + " not shown here (see Diff).") : "No check found anything to report.") + "</p>";
    }
    var uiRow = function (key, body) {
      var miss = (R.missing || []).indexOf(key) >= 0;
      return '<li class="ghr-ck warn' + (miss ? " missing" : "") + '"><label><input type="checkbox" data-ack="' +
        E(key) + '"' + (R.acks && R.acks[key] ? " checked" : "") + ">" + body + "</label></li>";
    };
    h += '<ul class="ghr-checks">';
    checks.forEach(function (k) {
      var L = LEVEL[k.level] || LEVEL.info;
      // a decided change has nothing left to tick: its warnings read as such
      if (!pending && k.level === "warn") L = ["warn", "Warning", G.warn];
      if (!pending && k.level === "confirm") L = ["iris", "Public repository", G.info];
      var badge = '<span class="gh-chip ' + L[0] + ' ghr-ck-lv">' + E(L[1]) + "</span>";
      var body = '<span class="ghr-ck-b">' + badge + TX(k.text) + checkWhere(k) + "</span>";
      if (!pending && (k.level === "warn" || k.level === "confirm")) {
        h += '<li class="ghr-ck ' + (k.level === "warn" ? "warn" : "confirm") + '">' + svg(L[2], 16) + body + "</li>";
      } else if (k.level === "warn" && typeof k.ack === "string") {
        var miss = (R.missing || []).indexOf(k.ack) >= 0;
        h += '<li class="ghr-ck warn' + (miss ? " missing" : "") + '"><label><input type="checkbox" data-ack="' +
          E(k.ack) + '"' + (R.acks && R.acks[k.ack] ? " checked" : "") +
          ' aria-label="' + E("I have read this warning: " + markHidden(k.text || "")) + '">' + body + "</label></li>";
      } else if (k.level === "warn") {
        // a warning with no ack id is still a warning: the sheet asks for the
        // tick itself (never sent; the server asks for none)
        h += uiRow("ui:warn:" + (c.checks || []).indexOf(k), body);
      } else if (k.level === "confirm") {
        h += '<li class="ghr-ck confirm' + ((R.missing || []).indexOf("confirm_public") >= 0 ? " missing" : "") +
          '"><label><input type="checkbox" data-confirm-public="1"' + (R.confirmPublic ? " checked" : "") +
          ">" + body + "</label></li>";
      } else {
        h += '<li class="ghr-ck ' + (k.level === "block" ? "block" : "info") + '">' + svg(L[2], 16) + body + "</li>";
      }
    });
    ui.forEach(function (u) {
      var badge = '<span class="gh-chip warn ghr-ck-lv">' + E(LEVEL.warn[1]) + "</span>";
      var files = u.paths ? '<span class="gh-note" style="display:block">' + u.paths.slice(0, 20).map(function (p) {
        return '<code class="gh-code">' + TX(p) + "</code>";
      }).join(" ") + (u.paths.length > 20 ? " …" : "") + "</span>" : "";
      h += uiRow(u.key, '<span class="ghr-ck-b">' + badge + E(u.text) + files + "</span>");
    });
    return h + "</ul>";
  }
  var STATUS_WORD = { A: "Added", M: "Changed", D: "Deleted", R: "Renamed", C: "Copied", T: "Type changed" };
  function filesHTML(c) {
    var files = Array.isArray(c.files) ? c.files : [];
    var st = c.stat || {};
    var h = '<div class="gh-h"><h2>Files</h2><span>' + E(plural(files.length || st.files || 0, "file") +
      " · +" + (st.add || 0) + " −" + (st.del || 0)) + "</span></div>";
    if (!files.length) return h + '<p class="gh-note">No files.</p>';
    h += '<ul class="ghr-files">';
    files.forEach(function (f) {
      f = f || {};
      var s = String(f.status || "").charAt(0);
      var mode = "";
      if (f.new_mode === "120000") mode = "symlink";
      else if (f.new_mode === "100755" && f.old_mode !== "100755" && s !== "D") mode = "now executable";
      var n = f.binary
        ? '<span class="ghr-n">binary · ' + E(bytes(f.size)) + "</span>"
        : '<span class="ghr-n"><span class="a">+' + E(String(f.add == null ? 0 : f.add)) + '</span> <span class="d">−' +
          E(String(f.del == null ? 0 : f.del)) + "</span></span>";
      h += '<li class="ghr-file">' + svg(G.file, 15) + '<span class="ghr-st">' + E(STATUS_WORD[s] || s || "") + "</span>" +
        '<span class="ghr-fp">' + (f.old_path && f.old_path !== f.path ? TX(f.old_path) + " → " : "") +
        TX(f.path) + (mode ? ' <span class="gh-chip warn">' + E(mode) + "</span>" : "") + "</span>" + n + "</li>";
    });
    return h + "</ul>";
  }
  // A section's file name. The `diff --git a/X b/Y` header cannot be split
  // when a path holds " b/" (E61), so: the file list's own header for that
  // file, then the +++/---/rename lines before the first hunk (git appends a
  // TAB to those when the name has a space), then a header whose two halves
  // are the same path, and only then the old greedy split.
  function sectionName(head, lines, files) {
    // ...only when exactly ONE file has that header: "q b/q b/q" renamed to
    // "q" and a new "q b/q" both read `diff --git a/q b/q b/q b/q`
    var fl = Array.isArray(files) ? files : [], hit = [];
    for (var i = 0; i < fl.length; i++) {
      var f = fl[i];
      if (!f || typeof f.path !== "string" || !f.path) continue;
      var a = typeof f.old_path === "string" && f.old_path ? f.old_path : f.path;
      if (head === "diff --git a/" + a + " b/" + f.path && hit.indexOf(f.path) < 0) hit.push(f.path);
    }
    if (hit.length === 1) return hit[0];
    var plus = null, minus = null, ren = null;
    for (var j = 0; j < lines.length; j++) {
      var l = lines[j];
      if (/^@@/.test(l)) break;
      if (l.indexOf("+++ ") === 0) plus = l.slice(4).replace(/\t$/, "");
      else if (l.indexOf("--- ") === 0) minus = l.slice(4).replace(/\t$/, "");
      else if (l.indexOf("rename to ") === 0) ren = l.slice(10);
    }
    if (plus && plus !== "/dev/null") return plus.replace(/^b\//, "");
    if (minus && minus !== "/dev/null") return minus.replace(/^a\//, "");
    if (ren) return ren;
    var rest = head.replace(/^diff --git a\//, "");
    if (rest !== head && (rest.length - 3) % 2 === 0) {
      var half = (rest.length - 3) / 2;
      if (rest.slice(half, half + 3) === " b/" && rest.slice(0, half) === rest.slice(half + 3)) return rest.slice(0, half);
    }
    var m = /^diff --git a\/(.*) b\/(.*)$/.exec(head);
    return m ? m[2] : head.replace(/^diff --git /, "");
  }
  // PURE: the diff cut into file sections, and how many of its rows a cap of
  // `cap` hides. gate() and diffHTML() count the SAME rows, so Approve can
  // never be on while the sheet hides a line. A binary section's rows are its
  // text_preview (none without one).
  var DM = { raw: null, files: null, secs: null };
  function diffParse(text, files) {
    var raw = String(text == null ? "" : text);
    if (DM.secs && DM.raw === raw && DM.files === files) return DM.secs;
    var lines = raw ? raw.split("\n") : [];
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    var secs = [], cur = null;
    lines.forEach(function (l) {
      if (/^diff --git /.test(l) || !cur) {
        cur = { head: /^diff --git /.test(l) ? l : "", lines: [], binary: false };
        secs.push(cur);
        if (cur.head) return;
      }
      if (/^Binary files .* differ$/.test(l) || /^GIT binary patch$/.test(l)) cur.binary = true;
      cur.lines.push(l);
    });
    secs.forEach(function (s) {
      s.name = s.head ? sectionName(s.head, s.lines, files) : "";
      s.preview = null;
      s.previewCut = false;
      if (s.binary) {
        // a preview counts only when it has something to read; one the server
        // cut short is drawn, but the file is still "not shown" (unseenFiles)
        var f = (Array.isArray(files) ? files : []).filter(function (x) { return x && x.path === s.name; })[0];
        if (f && typeof f.text_preview === "string" && f.text_preview.trim()) {
          s.preview = f.text_preview.split("\n");
          if (s.preview.length > 1 && s.preview[s.preview.length - 1] === "") s.preview.pop();
          s.previewCut = f.text_preview_truncated === true;
        }
      }
      s.rows = s.binary ? (s.preview || []) : s.lines;
    });
    DM = { raw: raw, files: files, secs: secs };
    return secs;
  }
  function diffModel(text, files, cap) {
    var secs = diffParse(text, files), shown = 0, left = 0;
    var out = secs.map(function (s) {
      var show = Math.max(0, Math.min(s.rows.length, cap - shown));
      shown += show;
      left += s.rows.length - show;
      return { name: s.name, binary: s.binary, preview: s.preview, previewCut: s.previewCut, rows: s.rows, show: show,
               cut: s.rows.length - show };
    });
    return { secs: out, left: left, shown: shown };
  }
  function moreBtn(left) {
    return '<button type="button" class="gh-b" data-ghr="diff-all">Show the remaining ' + E(nplural(left, "line")) +
      "</button>";
  }
  // the diff: one section per file; a binary section shows its text preview
  // or one line saying it is not shown; EVERY line through DL (hidden
  // characters and long blank runs visible, then escaped)
  function diffHTML(text, opts) {
    opts = opts || {};
    var raw = String(text == null ? "" : text);
    if (!raw) return '<p class="gh-note">No diff text.</p>';
    var M = diffModel(raw, opts.files, opts.all ? Infinity : (opts.cap || DIFF_LINES));
    var h = '<div class="ghd">';
    // the cut is said FIRST, where it is seen, not only at the very end
    if (M.left > 0) {
      h += '<div class="ghd-more top"><span>Showing the first ' + E(num(M.shown)) + " lines. " +
        E(num(M.left)) + " more are not shown yet, and Approve stays off until you show them.</span>" +
        moreBtn(M.left) + "</div>";
    }
    M.secs.forEach(function (s) {
      h += '<div class="ghd-file">' + (s.name ? '<div class="ghd-fh">' + TX(s.name) + "</div>" : "");
      if (s.binary && s.preview === null) {
        h += '<p class="ghd-bin warn">Binary file — not shown. Its size and sha256 are listed under Checks.</p>';
      } else {
        if (s.binary) h += '<p class="ghd-bin">Git treats this file as binary. This is its content as text:</p>';
        if (s.show > 0) {
          h += '<div class="ghd-body"><div class="ghd-lines">';
          // The `---`/`+++` FILE headers exist only before a section's first hunk.
          // Inside a hunk every line is content, so an added line that happens to
          // read `++ x` (shown as `+++ x`) is still an ADDED line, never a dimmed
          // header: a hostile file must not be able to hide what it adds.
          var inHunk = false;
          for (var i = 0; i < s.show; i++) {
            var l = s.rows[i], cls;
            if (s.binary) cls = "ctx";
            else if (/^@@/.test(l)) { inHunk = true; cls = "hunk"; }
            else if (inHunk) {
              cls = l.charAt(0) === "+" ? "add" : l.charAt(0) === "-" ? "del"
                : l.charAt(0) === "\\" ? "meta" : "ctx";
            } else {
              cls = /^(\+\+\+ |--- |index |new file mode|deleted file mode|old mode|new mode|similarity index|dissimilarity index|rename (from|to) |copy (from|to) |\\ No newline)/.test(l) ? "meta"
                : l.charAt(0) === "+" ? "add" : l.charAt(0) === "-" ? "del" : "ctx";
            }
            h += '<span class="ghd-l ' + cls + '">' + (l ? DL(l) : " ") + "</span>";
          }
          h += "</div></div>";
        }
        if (s.cut > 0) {
          h += '<p class="ghd-bin warn">' + E(nplural(s.cut, "more line")) + " of this file not shown yet.</p>";
        }
        if (s.previewCut) {
          h += '<p class="ghd-bin warn">This preview is cut short: the rest of this file is not shown. Its size and ' +
            "sha256 are listed under Checks.</p>";
        }
      }
      h += "</div>";
    });
    if (M.left > 0) h += '<div class="ghd-more">' + moreBtn(M.left) + "</div>";
    return h + "</div>";
  }
  // the Diff heading: never "the whole change" while anything is not shown
  function diffHead(c, R) {
    var parts = [];
    if (c.diff_truncated) parts.push("cut at 1 MB");
    var left = (R || {}).diffAll === true ? 0 : diffModel(c.diff, c.files, DIFF_LINES).left;
    if (left > 0) parts.push("first " + num(DIFF_LINES) + " lines · " + num(left) + " more not shown yet");
    var u = unseenFiles(c).all.length;
    if (u) parts.push(plural(u, "file") + " not shown");
    return parts.length ? parts.join(" · ") : "the whole change";
  }
  // the description's size against the server's cap, in bytes (E64)
  function countText(c, R) {
    var edited = bodyEdited(c, R);
    var n = edited ? bodyCost(R.body) : utf8Len(c.body || "");
    return { over: edited && n > BODY_MAX, text: (edited ? "About " : "") + num(n) + " of " + num(BODY_MAX) +
      " bytes" + (edited && n > BODY_MAX ? ": too long. Hermes would cut the end after you approve." : "") };
  }
  function countHTML(c, R) {
    var k = countText(c, R);
    return '<p class="ghr-count' + (k.over ? " over" : "") + '" id="gh-rv-count" data-ghr-count>' + E(k.text) + "</p>";
  }
  function footerPreview(c, body) {
    return (String(body || "") ? "…your description\n\n---\n" : "") + String(c.pr_footer || "");
  }

  function sheetHTML(R) {
    var c = R.change;
    var bar = '<div class="ghr-bar"><span class="ghr-bar-t">' + svg(G.branch, 16) + "GitHub change" +
      (c ? " · " + TX(c.repo) : "") + "</span>" +
      '<button type="button" class="gh-b gh-sq" data-ghr="close" aria-label="Close without deciding" ' +
      'title="Close without deciding (Esc)">' + svg(G.close, 18) + "</button></div>";
    var h = '<div class="ghr-scroll"><div class="ghr-col">';
    if (R.loading) {
      h += '<p class="ghr-eye">GitHub change</p><h1 class="ghr-t" id="gh-rv-h" tabindex="-1">Loading the change…</h1>';
    } else if (!c) {
      h += '<p class="ghr-eye">GitHub change</p><h1 class="ghr-t" id="gh-rv-h" tabindex="-1">' +
        E(R.err || "This change could not be loaded.") + "</h1>";
    } else {
      var sl = STATE_LINE[c.state] || ["", String(c.state || "")];
      var base = c.base || {};
      h += '<p class="ghr-eye">' + E(c.state === "pending" ? "A change is waiting for you" : "GitHub change") + "</p>";
      h += '<h1 class="ghr-t" id="gh-rv-h" tabindex="-1">' + TX(c.title) + "</h1>";
      h += '<div class="ghr-meta"><span class="ghr-repo">' + TX(c.repo) + "</span>" + visChip(c.private) +
        '<code class="gh-code">' + TX(c.branch) + "</code>" +
        (base.branch ? "<span>based on " + TX(base.branch) + (sha7(base.sha) ? " @ <code class=\"gh-code\">" +
          E(sha7(base.sha)) + "</code>" : "") + (base.at ? " (" + E(ago(base.at)) + ")" : "") + "</span>" : "") +
        "</div>";
      if (c.state === "pending") {
        h += '<p class="ghr-what"><b>What happens if you approve:</b> ' + TX(whatText(c)) +
          (c.expires_at && t12(c.expires_at) ? " This request expires " +
            (/^\d/.test(t12(c.expires_at)) ? "at " : "on ") + E(t12(c.expires_at)) + "." : "") + "</p>";
      } else {
        var pr = c.pr || {};
        var stTxt = sl[1];
        if (c.state === "pr_open" && typeof pr.number === "number") {
          stTxt = pr.existing ? "Pushed onto the open pull request #" + pr.number + "."
            : "Pushed, and " + (pr.draft === false ? "pull request" : "draft pull request") + " #" + pr.number + " is open.";
        } else if (c.state === "pushed" && sha7(c.pushed_sha)) {
          stTxt = "Pushed " + c.branch + " (commit " + sha7(c.pushed_sha) + ") without a pull request.";
        }
        h += '<p class="ghr-state ' + sl[0] + '">' + TX(stTxt) +
          (c.decided_at ? " " + E(t12(c.decided_at)) + "." : "") +
          (c.error ? "<br>" + TX(c.error) : "") + "</p>";
      }
      if (R.result) h += resultHTML(R);
      var san = sanitizedNote(c.sanitized);
      if (san) h += '<p class="gh-note">' + E(san) + "</p>";
      h += checksHTML(c, R);
      h += filesHTML(c);
      h += '<div class="gh-h"><h2>Diff</h2><span>' + E(diffHead(c, R)) +
        "</span></div>" + diffHTML(c.diff, { all: R.diffAll, files: c.files });
      if (c.diff_truncated) {
        h += '<p class="gh-warnl">This diff is cut short at 1 MB. Everything listed under Files is in the commit; ' +
          "look at the rest in the sandbox before you approve.</p>";
      }
      h += '<div class="gh-h"><h2>Pull request</h2><span>' + E(c.state === "pending"
        ? "sent exactly as written here" : "as it was sent") + "</span></div>";
      if (c.state === "pending") {
        h += '<label class="gh-lab" for="gh-rv-title">Title</label>' +
          '<input class="gh-field" id="gh-rv-title" data-ghr-title type="text" maxlength="' + TITLE_MAX + '" ' +
          'autocomplete="off" aria-describedby="gh-rv-clean">' +
          '<label class="gh-lab" for="gh-rv-body">Description</label>' +
          '<textarea class="gh-field ghr-body" id="gh-rv-body" data-ghr-body rows="8" autocomplete="off" ' +
          'aria-describedby="gh-rv-clean gh-rv-count"></textarea>' +
          countHTML(c, R) +
          '<p class="gh-note" id="gh-rv-clean">' + E(CLEAN_NOTE) + "</p>";
      } else {
        h += '<p class="gh-p"><b>' + TX(c.title) + "</b></p>" +
          (c.body ? '<pre class="gh-pre wrap" tabindex="0">' + TX(c.body) + "</pre>" : "");
      }
      h += '<p class="gh-note">The pull request description ends with this line, added by Hermes:</p>' +
        '<pre class="gh-pre wrap" data-ghr-footer>' + TX(footerPreview(c, R.body)) + "</pre>";
      if (R.msg) h += '<p class="ghr-msg ' + E(R.msg.cls || "") + '" role="' + (R.msg.cls === "bad" ? "alert" : "status") +
        '">' + TX(R.msg.text) + "</p>";
    }
    h += "</div></div>";
    return bar + h + footHTML(R);
  }
  // what a decision did, as plain lines (resultHTML escapes them; outcomeToast
  // says them when the sheet is no longer on screen)
  function resultLines(d) {
    d = d || {};
    var pr = d.pr || {};
    var t = d.state === "rejected" ? "Rejected. Nothing was pushed."
      : d.state === "retracted" ? "Retracted. The branch is gone from GitHub" +
        (d.pr_closed === true ? " and the pull request is closed." : d.pr_closed === false
          ? ", but the pull request could not be closed — close it on GitHub." : ".")
      : d.state === "pr_open" ? (pr.existing ? "Pushed onto the open pull request #" + pr.number + "."
        : pr.draft === false ? "Pushed, and opened pull request #" + pr.number + ". GitHub could not open a " +
          "draft here, so it is ready for review and may notify reviewers."
        : "Pushed, and opened draft pull request #" + pr.number + ".")
      : d.state === "pushed" ? "Pushed. No pull request was opened." : "Done.";
    var out = [t];
    if (d.pr_error) out.push("The branch was pushed, but the pull request was not opened: " + d.pr_error);
    if (d.notified === false && (d.state === "pr_open" || d.state === "pushed")) {
      out.push("Telegram did not confirm the notice for this push.");
    }
    return out;
  }
  function resultHTML(R) {
    var d = R.result || {};
    var pr = d.pr || {};
    var url = ghUrl(pr.url);
    var h = '<div class="ghr-state ok" data-ghr-result>' + resultLines(d).map(TX).join("<br>");
    if (url) {
      h += '<div class="ghc-act"><button type="button" class="gh-b gh-go" data-ghr="open-pr" data-url="' + E(url) +
        '">' + svg(G.ext, 15) + "Open the pull request</button></div>";
    }
    return h + "</div>";
  }
  function footHTML(R) {
    var c = R.change;
    var h = '<div class="ghr-foot"><div class="ghr-foot-in">';
    if (c && c.state === "pending") {
      var g = gate(c, R);
      var dis = g.ok ? "" : " disabled";
      h += '<span class="ghr-why" data-ghr-why aria-live="polite">' + TX(g.reason) + "</span>" +
        '<button type="button" class="gh-b gh-bad" data-ghr="reject"' + (R.busy ? " disabled" : "") + ">" +
        (R.busy === "reject" ? "Rejecting…" : "Reject") + "</button>" +
        '<button type="button" class="gh-b" data-ghr="push_only"' + dis + ">" +
        (R.busy === "push_only" ? "Pushing…" : "Push branch only") + "</button>" +
        '<button type="button" class="gh-b gh-go" data-ghr="push_pr"' + dis + ">" +
        (R.busy === "push_pr" ? "Pushing…" : "Push and open draft PR") + "</button>";
    } else {
      h += '<span class="ghr-why"></span>';
      if (c && (c.state === "pushed" || c.state === "pr_open")) {
        var url = ghUrl((c.pr || {}).url);
        if (url && !(R.result && ghUrl(((R.result || {}).pr || {}).url))) {
          h += '<button type="button" class="gh-b" data-ghr="open-pr" data-url="' + E(url) + '">' + svg(G.ext, 15) +
            "Open the pull request</button>";
        }
        h += '<button type="button" class="gh-b gh-bad" data-ghr="retract"' + (R.busy ? " disabled" : "") + ">" +
          (R.busy === "retract" ? "Retracting…" : "Retract") + "</button>";
      }
      h += '<button type="button" class="gh-b" data-ghr="close-foot">Close</button>';
    }
    return h + "</div></div>";
  }

  function sheetEl() { var d = D(); return d ? d.getElementById(SHEET_ID) : null; }
  function paintSheet(first) {
    var d = D(); if (!d || !d.body) return;
    var R = S.review; if (!R) return;
    // a closed review never repaints itself back onto the screen: an answer that
    // arrives after Esc (a push still in flight) is reported by outcomeToast()
    if (!R.open) return;
    ensureCSS();
    var n = sheetEl();
    if (!n) {
      n = d.createElement("div");
      n.id = SHEET_ID;
      n.setAttribute("role", "dialog");
      n.setAttribute("aria-modal", "true");
      n.setAttribute("aria-labelledby", "gh-rv-h");
      n.hidden = true;
      d.body.appendChild(n);
    }
    syncEdits(R);
    var sc = n.querySelector ? n.querySelector(".ghr-scroll") : null;
    var top = sc ? sc.scrollTop : 0;
    // which control had focus, so a re-render puts it back (a removed node
    // would drop focus to <body>, outside the dialog)
    var fkey = null;
    try {
      var a = d.activeElement;
      if (a && a !== d.body && n.contains(a)) {
        fkey = a.getAttribute("data-ghr") ? 'button[data-ghr="' + a.getAttribute("data-ghr") + '"]'
          : a.getAttribute("data-ack") != null ? 'input[data-ack="' + String(a.getAttribute("data-ack")).replace(/["\\]/g, "") + '"]'
          : a.id ? "#" + String(a.id).replace(/[^A-Za-z0-9_-]/g, "") : "#gh-rv-h";
      }
    } catch (e) { fkey = null; }
    try { n.innerHTML = sheetHTML(R); } catch (e) { return; }
    wireSheet(n, R);
    var sc2 = n.querySelector(".ghr-scroll");
    if (sc2 && !first) sc2.scrollTop = top;
    var wasHidden = n.hidden;
    n.hidden = false;
    if (first && wasHidden) R.handle = anim().enter(n, "fade");
    var target = null;
    try { target = fkey ? n.querySelector(fkey) : null; } catch (e) { target = null; }
    if (target && target.disabled) target = null;
    if (first || fkey) {
      try { (target || n.querySelector("#gh-rv-h")).focus(); } catch (e) {}
    }
  }
  function syncEdits(R) {
    var n = sheetEl();
    if (!n || !n.querySelector || !R || !R.change) return;
    var t = n.querySelector("input[data-ghr-title]"), b = n.querySelector("textarea[data-ghr-body]");
    if (t && t.__ghWired) R.title = String(t.value);
    if (b && b.__ghWired) R.body = String(b.value);
  }
  function updateGate(n, R) {
    var c = R.change; if (!c || !n) return;
    var g = gate(c, R);
    var why = n.querySelector("[data-ghr-why]");
    if (why) why.innerHTML = TX(g.reason);
    ["push_only", "push_pr"].forEach(function (k) {
      var b = n.querySelector('button[data-ghr="' + k + '"]');
      if (b) b.disabled = !g.ok;
    });
  }
  function wireSheet(n, R) {
    var c = R.change;
    var t = n.querySelector("input[data-ghr-title]"), b = n.querySelector("textarea[data-ghr-body]");
    if (t) {
      t.value = R.title == null ? (c ? c.title || "" : "") : R.title;
      t.__ghWired = true;
      t.oninput = function () { R.title = String(t.value); updateGate(n, R); };
    }
    if (b) {
      b.value = R.body == null ? (c ? c.body || "" : "") : R.body;
      b.__ghWired = true;
      b.oninput = function () {
        R.body = String(b.value);
        var f = n.querySelector("[data-ghr-footer]");
        if (f && c) f.innerHTML = TX(footerPreview(c, R.body));
        var k = n.querySelector("[data-ghr-count]");
        if (k && c) {
          var ct = countText(c, R);
          k.textContent = ct.text;
          k.className = "ghr-count" + (ct.over ? " over" : "");
        }
        updateGate(n, R);
      };
    }
    Array.prototype.slice.call(n.querySelectorAll("input[data-ack]")).forEach(function (x) {
      x.onchange = function () {
        var a = x.getAttribute("data-ack");
        R.acks[a] = !!x.checked;
        R.missing = (R.missing || []).filter(function (m) { return m !== a; });
        updateGate(n, R);
      };
    });
    Array.prototype.slice.call(n.querySelectorAll("input[data-confirm-public]")).forEach(function (x) {
      x.onchange = function () { R.confirmPublic = !!x.checked; updateGate(n, R); };
    });
    Array.prototype.slice.call(n.querySelectorAll("button[data-ghr]")).forEach(function (x) {
      var a = x.getAttribute("data-ghr");
      x.onclick = function () {
        if (a === "close" || a === "close-foot") closeReview();
        else if (a === "push_pr" || a === "push_only" || a === "reject") decide(a);
        else if (a === "retract") retract();
        else if (a === "open-pr") { if (!openExternal(x.getAttribute("data-url"))) say("Only github.com links open from here."); }
        else if (a === "diff-all") { R.diffAll = true; paintSheet(false); }
      };
    });
  }
  var FOCUSABLE = 'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),' +
    'select:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';
  function focusables(n) {
    return Array.prototype.slice.call(n.querySelectorAll(FOCUSABLE)).filter(function (x) {
      try { return x.getClientRects().length > 0; } catch (e) { return true; }
    });
  }
  function sheetKey(ev, n) {
    if (!ev || !S.review || !S.review.open) return;
    if (ev.key === "Escape" || ev.key === "Esc") {
      ev.preventDefault();
      if (ev.stopPropagation) ev.stopPropagation();
      closeReview();
      return;
    }
    if (ev.key !== "Tab") return;
    var list = focusables(n);
    if (!list.length) { ev.preventDefault(); return; }
    var d = D(), cur = d ? d.activeElement : null;
    var first = list[0], last = list[list.length - 1];
    var inside = !!(cur && n.contains(cur)) && list.indexOf(cur) >= 0;
    if (ev.shiftKey) {
      if (!inside || cur === first) { ev.preventDefault(); try { last.focus(); } catch (e) {} }
    } else if (!inside || cur === last) {
      ev.preventDefault(); try { first.focus(); } catch (e) {}
    }
  }
  // a modal pop-out or the Widget Center would pull focus back out of this
  // sheet (index.html's overlay watcher): close them first — the pop-out AT
  // ONCE. closePop() animates its exit, and #wpop stays visible (an open modal,
  // to the watcher) until that exit's done() hides it, so the sheet's focus was
  // pulled back into the closing pop-out and then handed to the card behind
  // the sheet (motion-overlays-1). Finishing the exit runs its done() now; a
  // pop-out already closing (aux_needsyou closes it before calling us) is
  // finished the same way.
  function closeOtherModals() {
    var d = D(); if (!d) return;
    try {
      var pop = d.getElementById("wpop");
      if (pop && !pop.hidden && typeof closePop === "function") {
        closePop();
        try {
          if (typeof popExit !== "undefined" && popExit && popExit.forEach) {
            popExit.forEach(function (h) { try { if (h && h.finish) h.finish(); } catch (e) {} });
          }
        } catch (e) {}
        // without the motion runtime closePop() hides it on a timer instead
        if (!pop.hidden) {
          try { if (typeof popHideT !== "undefined") clearTimeout(popHideT); } catch (e) {}
          pop.hidden = true;
        }
      }
    } catch (e) {}
    try { var wc = d.getElementById("wcenter"); if (wc && !wc.hidden) wc.hidden = true; } catch (e) {}
    // the Agent page's Claude dialogues sheet is a modal in that registry too
    // (S2 seam): closed through its own button, so its focus hand-back runs
    try {
      var dm = d.getElementById("ag-deep");
      if (dm && dm.classList && dm.classList.contains("open")) {
        var dx = d.getElementById("ag-deep-x");
        if (dx && dx.click) dx.click();
        if (dm.classList.contains("open")) dm.classList.remove("open");
      }
    } catch (e) {}
  }

  // Where focus goes back when the sheet closes (motion-overlays-3). The
  // control that opened it is often REPLACED while the sheet is open — the
  // hub's 60 s refresh re-renders the Needs-you card, the Settings card's own
  // poll repaints itself — and an opener inside the pop-out or the Widget
  // Center is hidden with them, so focusing the saved node left focus on
  // <body>. The opener is therefore also remembered as selectors that find
  // its replacement: the same control (tag + its key attributes) under the
  // same [data-id] rows, inside its nearest id'd container, then anywhere on
  // the page (the Needs-you pop-out's row is also the hub card's row); then a
  // stable control near it (the hub card's title, else the container's first
  // control). Never <body>.
  var BACK_KEYS = ["data-id", "data-a", "data-gh", "data-act", "data-ghr"];
  function cssStr(v) { return String(v).replace(/["\\]/g, "\\$&"); }
  function backPlan(el) {
    var d = D();
    if (!el || el.nodeType !== 1 || !d || el === d.body || el === d.documentElement) return null;
    var plan = { node: el, sels: [], near: [], anchor: "" };
    try {
      var tag = String(el.tagName || "").toLowerCase(), own = tag;
      if (el.id) own += '[id="' + cssStr(el.id) + '"]';
      else BACK_KEYS.forEach(function (k) { var v = el.getAttribute(k); if (v != null) own += "[" + k + '="' + cssStr(v) + '"]'; });
      var keyed = [], p = el.parentElement;
      while (p && p !== d.body && p.nodeType === 1) {
        if (p.id) { plan.anchor = p.id; break; }
        var k = p.getAttribute("data-id");
        if (k != null) keyed.unshift('[data-id="' + cssStr(k) + '"]');
        p = p.parentElement;
      }
      var inner = keyed.concat([own]).join(" ");
      if (own !== tag || keyed.length) {            // a bare "button" would match any button
        if (plan.anchor) plan.sels.push('[id="' + cssStr(plan.anchor) + '"] ' + inner);
        plan.sels.push(inner);
      }
      var card = el.closest ? el.closest("#wgrid .w[data-id]") : null;
      if (card) plan.near.push('#wgrid .w[data-id="' + cssStr(card.getAttribute("data-id")) + '"] .w-title');
    } catch (e) {}
    return plan;
  }
  function canTake(x, n, strict) {
    try {
      if (!x || x.nodeType !== 1 || x.isConnected === false || (n && n.contains(x))) return false;
      if (x.disabled || (x.closest && x.closest("[hidden]"))) return false;
      if (x.getClientRects && !x.getClientRects().length) return false;
      return !strict || x.tabIndex >= 0;
    } catch (e) { return false; }
  }
  function focusBack(plan, n) {
    var d = D();
    if (!plan || !d) return;
    var all = function (sel) { try { return Array.prototype.slice.call(d.querySelectorAll(sel)); } catch (e) { return []; } };
    var cands = [plan.node];
    plan.sels.concat(plan.near).forEach(function (s) { cands = cands.concat(all(s)); });
    if (plan.anchor) {
      var box = d.getElementById(plan.anchor);
      if (box && box !== d.body) cands = cands.concat(focusables(box));
    }
    for (var i = 0; i < cands.length; i++) {
      if (!canTake(cands[i], n, i > 0)) continue;
      try { cands[i].focus({ preventScroll: true }); } catch (e) {}
      if (d.activeElement === cands[i]) return;
    }
  }

  // An Esc must not throw away PR text the owner typed: it is kept for this page
  // life and put back when the SAME content (same digest) is opened again. Ticks
  // are never kept: every warning is read again.
  function keepDraft(R) {
    var c = R && R.change;
    if (!c || c.state !== "pending" || !ID_RE.test(String(R.id))) return;
    if (R.title !== (c.title || "") || R.body !== (c.body || "")) {
      S.drafts[R.id] = { digest: c.digest, title: R.title, body: R.body };
    } else {
      delete S.drafts[R.id];
    }
  }
  function restoreDraft(R) {
    var d = S.drafts[R.id], c = R.change;
    if (!d || !c) return;
    if (c.state === "pending" && d.digest === c.digest && typeof d.title === "string" && typeof d.body === "string") {
      R.title = d.title;
      R.body = d.body;
      R.msg = { cls: "", text: "Your earlier edits to the title or description are back. Every warning needs its tick again." };
    } else {
      delete S.drafts[R.id];
    }
  }

  async function openReview(id) {
    id = String(id || "");
    if (!ID_RE.test(id)) return false;
    S.seen[id] = 1;
    var d = D();
    var prev = S.review;
    if (prev && prev.open && prev.id === id) { var n0 = sheetEl(); if (n0 && !n0.hidden) return true; }
    // the opener is read BEFORE anything closes around it (a pop-out's row)
    var opener = null;
    try { opener = (prev && prev.open && prev.lastFocus) || (d && d.activeElement) || null; } catch (e) {}
    closeOtherModals();
    if (prev && prev.open) { syncEdits(prev); keepDraft(prev); }
    try { if (prev && prev.handle && prev.handle.cancel) prev.handle.cancel(); } catch (e) {}
    var R = newReview(id);
    R.lastFocus = opener;
    try { R.back = (prev && prev.open && prev.lastFocus) ? prev.back : backPlan(opener); } catch (e) { R.back = null; }
    S.review = R;
    paintSheet(true);
    var r = await get("/api/github/changes?id=" + encodeURIComponent(id));
    if (S.review !== R || !R.open) return false;
    R.loading = false;
    if (r.ok && r.data && r.data.change && r.data.change.id === id) {
      setChange(R, r.data.change);
      restoreDraft(R);
    } else {
      R.err = r.status === 404 ? "This change no longer exists. It may have been finished and cleaned up."
        : r.net ? "Could not reach the dashboard." : "This change could not be loaded.";
    }
    paintSheet(false);
    var n = sheetEl();
    try { var hd = n && n.querySelector("#gh-rv-h"); if (hd && !(D().activeElement && n.contains(D().activeElement) && D().activeElement !== hd)) hd.focus(); } catch (e) {}
    return true;
  }
  async function reloadChange(R) {
    var r = await get("/api/github/changes?id=" + encodeURIComponent(R.id));
    if (S.review !== R) return;
    if (r.ok && r.data && r.data.change) {
      // the same content (same digest): the owner's ticks and edits stay; new
      // content is a new review, so every tick starts again
      var keep = { t: R.title, b: R.body, a: R.acks, p: R.confirmPublic };
      var same = !!(R.change && r.data.change.digest === R.change.digest);
      setChange(R, r.data.change);
      if (same) { R.title = keep.t; R.body = keep.b; R.acks = keep.a || {}; R.confirmPublic = keep.p === true; }
    }
  }
  function closeReview() {
    var R = S.review;
    var n = sheetEl();
    if (!R || !n) { if (n) n.hidden = true; return; }
    if (!R.open) { try { if (R.handle && R.handle.finish) R.handle.finish(); } catch (e) {} n.hidden = true; return; }
    syncEdits(R);
    keepDraft(R);
    R.open = false;
    var back = R.back;
    // the deep link is consumed: closing must not reopen on a reload
    try {
      var m = HASH_RE.exec((W.location && W.location.hash) || "");
      if (m && W.history && typeof W.history.replaceState === "function") {
        W.history.replaceState(W.history.state || null, "", (W.location.pathname || "/") + (W.location.search || ""));
      }
    } catch (e) {}
    try { if (R.handle && R.handle.cancel) R.handle.cancel(); } catch (e) {}
    R.handle = anim().exit(n, "fadeOut", function () {
      n.hidden = true;
      try { focusBack(back, n); } catch (e) {}
    });
  }

  async function decide(decision) {
    var R = S.review;
    if (!R || !R.change || R.busy) return;
    var c = R.change;
    syncEdits(R);
    if (decision !== "reject") {
      var g = gate(c, R);
      if (!g.ok) { R.msg = { cls: "warn", text: g.reason }; paintSheet(false); return; }
    }
    var body = decideBody(c, R, decision);
    R.busy = decision;
    R.msg = null;
    paintSheet(false);
    var r = await post("/api/github/changes/decide", body);
    // no early return when the sheet was closed or replaced meanwhile: the push
    // may well have happened, and the owner is told below
    R.busy = "";
    var d = r.data || {};
    var reload = false;
    if (r.ok && d.ok) {
      R.result = d;
      R.msg = null;
      reload = true;
    } else if (r.status === 409 && (d.stale || d.error === "stale" || d.error === "not_pending")) {
      R.msg = { cls: "warn", text: STALE_TEXT };
      reload = true;
    } else if (r.status === 422 && d.error === "needs_ack") {
      R.missing = Array.isArray(d.needs_ack) ? d.needs_ack.slice() : [];
      R.msg = { cls: "warn", text: "Tick every warning first — the highlighted ones are not ticked." };
    } else if (r.status === 422 && d.error === "confirm_public") {
      R.missing = ["confirm_public"];
      R.msg = { cls: "warn", text: "Confirm that this repository is public first." };
    } else if (r.status === 422 && d.error === "blocked") {
      R.msg = { cls: "bad", text: "A check blocks this change, so GitHub was not touched." };
      reload = true;
    } else if (r.status === 422 && d.error === "secret_in_text") {
      R.msg = { cls: "bad", text: "The title or description contains one of your saved secret values. " +
        "Remove it, then approve again." };
    } else if (r.status === 403 && d.error === "owner_only") {
      R.msg = { cls: "bad", text: OWNER_TEXT };
    } else if (r.status === 429) {
      R.msg = { cls: "warn", text: d.hint || "Hermes has used today’s pushes. Try again tomorrow." };
    } else if (r.status === 400 || r.status === 403 || r.status === 409) {
      R.msg = { cls: "bad", text: d.hint || "That did not work (" + (d.error || r.status) + ")." };
      if (r.status === 409) reload = true;
    } else if (r.status === 404) {
      R.msg = { cls: "bad", text: "This change no longer exists." };
    } else {
      R.msg = { cls: "bad", text: (d.hint ? d.hint + " " : r.net ? "Could not reach the dashboard, so it is not " +
        "known whether anything happened. " : "Hermes could not finish (" + (d.error || r.status) + "). ") +
        "The change has been reloaded to show where it stands." };
      reload = true;
    }
    if (reload) await reloadChange(R);
    if (S.review !== R || !R.open) { outcomeToast(R); return; }
    paintSheet(false);
    var sn = sheetEl();
    if (R.result && sn) {
      var res = sn.querySelector("[data-ghr-result]");
      if (res) anim().enter(res, "fade", { key: "gh:result:" + R.id + ":" + (R.result.state || "") });
    }
    loadChanges().then(function () { paintCard(false); });
  }

  async function retract() {
    var R = S.review;
    if (!R || !R.change || R.busy) return;
    var c = R.change;
    if (c.state !== "pushed" && c.state !== "pr_open") return;
    var pr = c.pr || {};
    if (!ask("Retract this change? Hermes " + (typeof pr.number === "number" ? "closes pull request #" + pr.number +
      " and " : "") + "deletes " + c.branch + " on " + c.repo + ", but only if nobody else has moved that branch " +
      "since. This cannot be undone.")) return;
    R.busy = "retract";
    R.msg = null;
    paintSheet(false);
    var r = await post("/api/github/changes/retract", { id: c.id });
    R.busy = "";
    var d = r.data || {};
    if (r.ok && d.ok) {
      R.result = { state: "retracted", pr_closed: d.pr_closed };
    } else if (r.status === 403 && d.error === "owner_only") {
      R.msg = { cls: "bad", text: OWNER_TEXT };
    } else {
      R.msg = { cls: "bad", text: d.hint || (d.error === "not_retractable" ? "This change can no longer be retracted."
        : "The retract did not finish (" + (d.error || r.status || "no answer") + ").") };
    }
    await reloadChange(R);
    if (S.review !== R || !R.open) { outcomeToast(R); return; }
    paintSheet(false);
    loadChanges().then(function () { paintCard(false); });
  }
  // The answer to a decision (or a retract) arrived after the owner closed the
  // sheet, or opened another change over it. Nobody is looking at it, and a
  // push may have happened: say so (a toast, or a line in the sheet that is open),
  // and let the card catch up.
  function outcomeToast(R) {
    var lines = R && R.result ? resultLines(R.result) : (R && R.msg && R.msg.text ? [R.msg.text] : []);
    var cur = S.review;
    if (lines.length && cur && cur !== R && cur.open) {
      // another change is on screen: the page's toast would sit UNDER the sheet, so the
      // answer is said in the sheet itself
      cur.msg = { cls: "", text: "Earlier decision on " + (R.change ? R.change.repo + " " + R.change.branch : R.id) +
        ": " + lines.join(" ") };
      paintSheet(false);
    } else if (lines.length) {
      say("GitHub change: " + lines.join(" "), 9000);
    }
    loadChanges().then(function () { paintCard(false); });
  }

  // ==========================================================================
  // (c) the Hub toast + the deep link
  // ==========================================================================
  async function pendingIds(st) {
    if (Array.isArray(st && st.pending_ids)) {
      return st.pending_ids.filter(function (x) { return ID_RE.test(String(x)); });
    }
    if (!st || !((st.pending | 0) > 0)) return [];
    var r = await get("/api/github/changes?state=pending");
    if (!r.ok || !r.data || !Array.isArray(r.data.changes)) return null;
    return r.data.changes.map(function (c) { return c && c.id; }).filter(function (x) { return ID_RE.test(String(x)); });
  }
  // the first read of a page life: what is already pending is not news
  async function seed() {
    var st = S.status;
    if (!st || st.connected !== true) return;
    var ids = await pendingIds(st);
    (ids || []).forEach(function (id) { S.seen[id] = 1; });
    S.seeded = true;
  }
  function visible() {
    var d = D();
    if (!d) return false;
    if (d.hidden === true) return false;
    return !d.visibilityState || d.visibilityState === "visible";
  }
  function pollStart() {
    if (S.pollId || typeof W.setInterval !== "function") return;
    if (!S.status || S.status.connected !== true) return;
    S.pollId = W.setInterval(function () { pollTick(false); }, POLL_MS);
  }
  function pollStop() {
    if (S.pollId && typeof W.clearInterval === "function") { try { W.clearInterval(S.pollId); } catch (e) {} }
    S.pollId = 0;
  }
  async function pollTick(force) {
    if (S.polling) return false;
    if (!visible()) return false;
    if (!S.status || S.status.connected !== true) return false;
    var now = Date.now();
    if (!force && S.lastPollAt && now - S.lastPollAt < POLL_MS - 1000) return false;
    S.polling = true;
    try {
      var st = await readStatus(true);
      if (!st) return false;
      if (st.connected !== true) { pollStop(); paintCard(false); return false; }
      var ids = await pendingIds(st);
      if (!ids) return false;
      if (!S.seeded) { ids.forEach(function (id) { S.seen[id] = 1; }); S.seeded = true; return true; }
      var fresh = ids.filter(function (id) { return !S.seen[id]; });
      ids.forEach(function (id) { S.seen[id] = 1; });
      var openId = S.review && S.review.open ? S.review.id : null;
      fresh = fresh.filter(function (id) { return id !== openId; });
      if (fresh.length) {
        notify(fresh[fresh.length - 1]);
        loadChanges().then(function () { paintCard(false); });
      } else if (S.changes && !sameIds(ids, pendingList().map(function (c) { return c.id; }))) {
        // one left the pending set (the agent withdrew it, it expired, the
        // owner decided it elsewhere): the card lists S.changes, not the count,
        // so re-read the list or it keeps offering Review on a dead request
        loadChanges().then(function () { paintCard(false); });
      } else {
        paintCard(false);
      }
      return true;
    } finally {
      S.polling = false;
    }
  }
  // toast() never takes a click (pointer-events:none). While OUR line is up
  // the toast opens the review; any other line, or after it hides, it goes
  // back to being inert.
  function notify(id) {
    if (typeof toast !== "function") return false;
    say(TOAST_MSG, TOAST_MS);
    var d = D(), t = d && d.getElementById("toast");
    S.toastId = id;
    if (!t) return true;
    var handler = function () {
      if (t.textContent !== TOAST_MSG || !S.toastId || (t.classList && !t.classList.contains("on"))) return;
      var x = S.toastId;
      S.toastId = null;
      try { t.classList.remove("on"); } catch (e) {}
      openReview(x);
    };
    try {
      t.onclick = handler;
      t.style.pointerEvents = "auto";
      t.style.cursor = "pointer";
      t.style.minHeight = "40px";
      t.setAttribute("title", "Open the review");
    } catch (e) {}
    // inert again the MOMENT our line is gone — replaced by another toast or
    // faded (no .on) — not only when our own timer runs out (E63): an
    // invisible toast must never swallow a click on what is under it
    var disarm = function () {
      if (S.toastObs && S.toastObs.h === handler) {
        try { S.toastObs.o.disconnect(); } catch (e) {}
        S.toastObs = null;
      }
      if (t.onclick !== handler) return;
      t.onclick = null;
      try {
        t.style.pointerEvents = ""; t.style.cursor = ""; t.style.minHeight = "";
        t.removeAttribute("title");
      } catch (e) {}
      if (S.toastId === id) S.toastId = null;
    };
    if (S.toastObs) { try { S.toastObs.o.disconnect(); } catch (e) {} S.toastObs = null; }
    if (typeof W.MutationObserver === "function") {
      try {
        var o = new W.MutationObserver(function () {
          if (t.textContent !== TOAST_MSG || (t.classList && !t.classList.contains("on"))) disarm();
        });
        o.observe(t, { attributes: true, attributeFilter: ["class"], childList: true, characterData: true, subtree: true });
        S.toastObs = { o: o, h: handler };
      } catch (e) { S.toastObs = null; }
    }
    if (typeof W.setTimeout === "function") W.setTimeout(disarm, TOAST_MS + 400);
    return true;
  }
  function hashId() {
    try { var m = HASH_RE.exec((W.location && W.location.hash) || ""); return m ? m[1] : null; } catch (e) { return null; }
  }
  function onHash() {
    var id = hashId();
    if (!id) return false;
    if (S.review && S.review.open && S.review.id === id) return true;
    openReview(id);
    return true;
  }

  // ==========================================================================
  // (d) the onboarding step (SHARED CONTRACT 1)
  // ==========================================================================
  function stepInner() {
    var cs = S.conn.step;
    var st = S.step.status;
    if (cs.result) {
      return connectHTML(cs, "ghstep", { intro: false, ambient: S.ambient || null }) +
        '<p class="gh-note">Continue to finish. Everything about GitHub stays in Settings › Connections.</p>';
    }
    if (st && st.connected) {
      return '<p class="gh-p">GitHub is already connected as <b>@' + TX(st.login || "?") + "</b>" +
        (typeof st.expires_at === "number" ? ", and the key expires " + E(dateLong(st.expires_at)) : "") +
        ". Nothing to do here: manage it in Settings › Connections.</p>";
    }
    return connectHTML(cs, "ghstep", { intro: true, skip: true });
  }
  function stepRender(ctx) {
    return '<div class="ghc-step" data-gh-step>' + stepInner(ctx) + "</div>";
  }
  function advance(node, ctx) {
    if (ctx && typeof ctx.next === "function") { try { ctx.next(); return; } catch (e) {} }
    var d = D();
    var sheet = null;
    try { sheet = (node && node.closest) ? node.closest("#onb-sheet") : null; } catch (e) { sheet = null; }
    if (!sheet && d) sheet = d.getElementById("onb-sheet");
    // the hook's own Skip (it never calls save()), else the primary action
    var sk = sheet && sheet.querySelector ? sheet.querySelector('.onb-foot [data-act="skip"]') : null;
    if (sk && !sk.disabled) { sk.click(); return; }
    var go = sheet && sheet.querySelector ? sheet.querySelector(".onb-go") : null;
    if (go && !go.disabled) { go.click(); return; }
    if (ctx && typeof ctx.repaint === "function") ctx.repaint();
  }
  function stepWire(node, ctx) {
    if (!node || !node.querySelector) return;
    ensureCSS();
    var root = node.querySelector("[data-gh-step]") || node;
    var rerender = function () {
      var r = node.querySelector("[data-gh-step]");
      if (!r) return;
      var ta = r.querySelector("textarea[data-gh-repos]");
      if (ta) S.conn.step.reposText = String(ta.value || "");
      try { r.innerHTML = stepInner(ctx); } catch (e) { return; }
      wireConnect(r, S.conn.step, opts);
    };
    var opts = {
      repaint: rerender,
      onSkip: function () { S.step.skipped = true; advance(node, ctx); },
      onConnected: function () { S.step.status = S.status; }
    };
    S.stepRepaint = rerender;
    wireConnect(root, S.conn.step, opts);
    // its own status read (a local file read; no GitHub request)
    if (!S.step.read && !S.step.reading) {
      S.step.reading = true;
      get("/api/github/status?scan=0").then(function (r) {
        S.step.reading = false;
        S.step.read = true;
        if (r.ok && r.data && typeof r.data.connected === "boolean") {
          S.step.status = r.data;
          if (!S.status) S.status = r.data;
          var r0 = node.querySelector("[data-gh-step]");
          var tok = r0 && r0.querySelector("input[data-gh-token]");
          if (r.data.connected && !(tok && tok.value) && !S.conn.step.result) rerender();
        }
      });
    }
  }
  function stepSave(ctx) {
    try {
      var d = D(), sheet = d && d.getElementById("onb-sheet");
      var tok = sheet && sheet.querySelector("#ghstep-token");
      if (tok && String(tok.value || "").trim()) { S.conn.step.unchecked = true; }
      if (tok) tok.value = "";
    } catch (e) {}
    var res = S.conn.step.result;
    if (res && res.login) return Promise.resolve({ applied: ["GitHub connected as @" + res.login] });
    return Promise.resolve({});
  }
  function stepSummary() {
    // connected in the step: the same line save() applies (the sheet drops the
    // duplicate after Continue), because the footer's Skip never calls save()
    var res = S.conn.step.result;
    if (res) return res.login ? ["GitHub connected as @" + res.login] : [];
    var st = S.step.status;
    if (st && st.connected) return ["GitHub: already connected as @" + (st.login || "?") + " — unchanged."];
    if (S.conn.step.unchecked) {
      return ["GitHub: not connected. The token you pasted was not checked, and Hermes did not keep it. " +
        "Connect it in Settings › Connections."];
    }
    return ["GitHub: not connected. You can connect it any time in Settings › Connections."];
  }
  function stepStatusRow() {
    var st = S.step.status || S.status;
    if (st && st.connected) {
      var held = st.writes_paused;
      return [held ? "wait" : "ok", "GitHub", "Connected as @" + (st.login || "?") +
        (held ? ". Writes are paused — see Settings › Connections." :
          ". Hermes pushes only changes you approve, to hermes/ branches.")];
    }
    if (st && st.connected === false) {
      return ["", "GitHub", "Not connected. Optional: Settings › Connections creates a token limited to the " +
        "repositories you pick."];
    }
    return ["", "GitHub", "Optional. Connect it in Settings › Connections whenever you like."];
  }
  var STEP = {
    key: "github",
    eyebrow: COPY.eyebrow,
    title: COPY.title,
    sub: COPY.sub,
    after: "prefs",
    optional: true,
    render: stepRender,
    wire: stepWire,
    save: stepSave,
    statusRow: stepStatusRow,
    summary: stepSummary
  };
  function registerStep() {
    try {
      var ob = W.hermesOnboarding;
      if (ob && typeof ob.registerStep === "function") { ob.registerStep(STEP); return "hook"; }
    } catch (e) {}
    try {
      var arr = W.hermesOnboardingSteps;
      if (!Array.isArray(arr)) { arr = []; W.hermesOnboardingSteps = arr; }
      for (var i = 0; i < arr.length; i++) {
        if (arr[i] && arr[i].key === STEP.key) { arr[i] = STEP; return "array"; }
      }
      arr.push(STEP);
      return "array";
    } catch (e) { return ""; }
  }

  // ==========================================================================
  // boot
  // ==========================================================================
  // chain window.mindExtras like the other Settings cards
  var prevExtras = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prevExtras === "function") { try { await prevExtras(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  async function boot() {
    try { onHash(); } catch (e) {}
    var st = await readStatus(true);
    if (st && st.connected) {
      await seed();
      pollStart();
    }
  }

  registerStep();
  try {
    if (W.addEventListener) W.addEventListener("hashchange", function () { onHash(); });
  } catch (e) {}
  // Esc and the Tab trap for the review sheet, wherever focus is (captured,
  // so the page's own Esc handler sees a handled event and stays out of it)
  var sheetTab = "";   // a Tab the trap let through whose focus move has not landed yet: "fwd" | "back"
  try {
    var dk = D();
    if (dk && dk.addEventListener) dk.addEventListener("keydown", function (ev) {
      var n = sheetEl();
      if (!n || n.hidden || !S.review || !S.review.open) return;
      sheetKey(ev, n);
      if (ev.key === "Tab" && !ev.defaultPrevented) {
        sheetTab = ev.shiftKey ? "back" : "fwd";
        setTimeout(function () { sheetTab = ""; }, 0);
      }
    }, true);
  } catch (e) {}
  // ...and keeps focus IN the sheet (motion-overlays-2). index.html's overlay
  // watcher pulls focus back only into its own modals, so a script's .focus()
  // elsewhere — sendMsg focuses the composer when a turn ends — moved the
  // keyboard to the page behind this aria-modal sheet: typing went into the
  // hidden composer and Enter sent a chat message. Focus that leaves the open
  // sheet goes back to the control that had it (the field being typed in),
  // else to the heading; a Tab that got past the trap (WebKit with keyboard
  // navigation off skips buttons) wraps to the sheet's other end instead, as
  // index.html's watcher does for its sheets (R2-35). Not while the sheet is
  // closing (R.open is already false then), so closeReview's own focus return
  // is never fought. A pop-out or the Widget Center that opened beneath the
  // sheet is closed first, so the two pull-backs can never hand focus to each
  // other.
  try {
    var df = D();
    if (df && df.addEventListener) df.addEventListener("focusin", function (ev) {
      var via = sheetTab;
      sheetTab = "";
      var n = sheetEl();
      if (!n || n.hidden || !S.review || !S.review.open) return;
      var t = ev.target;
      if (!t || t.nodeType !== 1 || n.contains(t)) return;
      try { if (t.closest && t.closest("#wpop,#wcenter,#ag-deep")) closeOtherModals(); } catch (e) {}
      var from = ev.relatedTarget, to = null;
      if (via) {
        var f = focusables(n);
        to = f.length ? f[via === "back" ? f.length - 1 : 0] : null;
      } else if (from && from.nodeType === 1 && from.isConnected !== false && n.contains(from) && !from.disabled) {
        to = from;
      }
      to = to || n.querySelector("#gh-rv-h");
      try { if (to) to.focus({ preventScroll: true }); } catch (e) {}
    }, true);
  } catch (e) {}
  try {
    var dd = D();
    if (dd && dd.addEventListener) {
      dd.addEventListener("visibilitychange", function () {
        if (!visible()) return;
        if (S.status && S.status.connected === true &&
            (!S.lastPollAt || Date.now() - S.lastPollAt >= POLL_MS)) pollTick(false);
        if (S.cardDirty) {
          var c = D() && D().getElementById(CARD_ID);
          if (c && !focusedField(c)) paintCard(false);
        }
      });
    }
  } catch (e) {}
  try {
    var d0 = D();
    if (d0 && d0.readyState === "loading" && d0.addEventListener) {
      d0.addEventListener("DOMContentLoaded", function () { boot(); }, { once: true });
    } else {
      boot();
    }
  } catch (e) {}

  // public + headless-harness surface
  W.hermesGitHub = {
    v: 1,
    openCard: openCard,
    openReview: openReview,
    closeReview: closeReview,
    connectHTML: connectHTML,
    cardHTML: cardHTML,
    sheetHTML: sheetHTML,
    diffHTML: diffHTML,
    checksHTML: checksHTML,
    filesHTML: filesHTML,
    gate: gate,
    decideBody: decideBody,
    warnAcks: warnAcks,
    markHidden: markHidden,
    ghUrl: ghUrl,
    fmt: { t12: t12, dateLong: dateLong, ago: ago, sha7: sha7, bytes: bytes },
    step: STEP,
    registerStep: registerStep,
    mount: mount,
    paintCard: paintCard,
    refresh: async function () { S.cardLoaded = false; await mount(); },
    poll: pollTick,
    pollStop: pollStop,
    boot: boot,
    onHash: onHash,
    COPY: COPY,
    TOAST_MSG: TOAST_MSG,
    POLL_MS: POLL_MS,
    state: S
  };
})();
