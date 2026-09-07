// aux_md.js — the ONE Markdown renderer for every surface that shows model text:
// the chat (index.html renderMd), deep cards (aux_agent.js), the menu-bar Quick
// Ask popover (aux_quickask.js loads this file itself, because the popover's
// HTML shell is frozen inside main.swift) and Needs-you drafts (aux_needsyou.js).
//
// Escape-FIRST: the whole source is HTML-escaped before any Markdown is
// recognised, so model output can never inject markup. Links are http(s) only;
// images render as links (a local-first app must not fetch remote images on a
// model's say-so).
//
// Supported: fenced code (language class), headings 1–6, paragraphs with line
// breaks, bullet / ordered / task lists incl. nesting and loose items,
// blockquotes (rendered recursively), horizontal rules, GFM tables with
// alignment, inline code / bold / italic / strikethrough / links / bare URLs,
// and <think>…</think> reasoning blocks (collapsed once closed).
(function (root) {
  "use strict";
  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ESC[c]; });
  }

  // ---- inline ---------------------------------------------------------------
  // Operates on ALREADY-ESCAPED text. Code spans and links are lifted out first
  // so emphasis rules never touch their contents (snake_case URLs, `**` in code).
  var URL_RE = "https?:\\/\\/[^\\s<)]*[^\\s<).,;:!?]";
  var BARE_URL = new RegExp("(^|[\\s(])(" + URL_RE + ")", "g");
  var TOK = "\u0000", PIPE = "\u0001";
  function inline(s) {
    var stash = [];
    function keep(html) { stash.push(html); return TOK + (stash.length - 1) + TOK; }
    function link(u, t) { return keep('<a href="' + u + '" target="_blank" rel="noopener">' + (t || u) + "</a>"); }
    s = s.replace(/`([^`\n]+)`/g, function (_, c) { return keep("<code>" + c + "</code>"); });
    s = s.replace(/!?\[([^\]\n]*)\]\((https?:[^)\s]+)\)/g, function (_, t, u) { return link(u, t); });
    s = s.replace(BARE_URL, function (_, pre, u) { return pre + link(u); });
    s = s.replace(/\*\*\*(\S(?:[^*\n]*?\S)?)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*(\S(?:[^*\n]*?\S)?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^\w*])\*(\S(?:[^*\n]*?\S)?)\*(?![\w*])/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])__(\S(?:[^_\n]*?\S)?)__(?!\w)/g, "$1<strong>$2</strong>");
    s = s.replace(/(^|[^\w])_(\S(?:[^_\n]*?\S)?)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~(\S(?:[^~\n]*?\S)?)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return stash[+i]; });
  }

  // ---- blocks ---------------------------------------------------------------
  var RE = {
    fence: /^\s*(`{3,}|~{3,})\s*([^\s`]*)/,
    heading: /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/,
    hr: /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/,
    ul: /^(\s*)[-*+]\s+(.*)$/,
    ol: /^(\s*)(\d{1,9})[.)]\s+(.*)$/,
    quote: /^\s*&gt;\s?(.*)$/,
    tableSep: /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/
  };
  function cells(line) {
    var t = line.replace(/\\\|/g, PIPE).trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.replace(/\u0001/g, "|").trim(); });
  }
  function align(c) {
    var l = c.charAt(0) === ":", r = c.charAt(c.length - 1) === ":";
    return l && r ? "center" : r ? "right" : l ? "left" : "";
  }
  function cell(tag, a, html) { return "<" + tag + (a ? ' style="text-align:' + a + '"' : "") + ">" + html + "</" + tag + ">"; }

  // `lines` are already escaped. Returns HTML.
  function renderLines(lines) {
    var out = [], para = [], lst = [], i = 0, n = lines.length, blank = false, m;
    function top() { return lst[lst.length - 1]; }
    function flushPara() { if (para.length) { out.push("<p>" + para.join("<br>") + "</p>"); para = []; } }
    function popList() { var t = lst.pop(); if (t.open) out.push("</li>"); out.push("</" + t.type + ">"); }
    function closeLists() { while (lst.length) popList(); }
    function openTag(type, start) { return type === "ol" && start && start !== 1 ? '<ol start="' + start + '">' : "<" + type + ">"; }
    function taskify(text) {
      var t = text.match(/^\[([ xX])\]\s+([\s\S]*)$/);
      if (!t) return inline(text);
      return '<label class="md-task"><input type="checkbox" disabled' + (t[1] === " " ? "" : " checked") + "> " + inline(t[2]) + "</label>";
    }
    function item(type, indent, text, start) {
      flushPara();
      while (lst.length && indent <= top().indent - 2) popList();
      var t = top();
      if (!t) { lst.push({ type: type, indent: indent, open: false }); out.push(openTag(type, start)); }
      else if (indent >= t.indent + 2 && t.open) { lst.push({ type: type, indent: indent, open: false }); out.push(openTag(type, start)); }
      else if (t.type !== type) { popList(); lst.push({ type: type, indent: indent, open: false }); out.push(openTag(type, start)); }
      t = top();
      if (t.open) out.push("</li>");
      out.push("<li>" + taskify(text)); t.open = true;
    }
    while (i < n) {
      var raw = lines[i];
      if ((m = raw.match(RE.fence))) {
        flushPara(); closeLists();
        var fence = m[1], lang = (m[2] || "").replace(/[^\w+#.-]/g, "").slice(0, 24), buf = [];
        var closeRe = new RegExp("^\\s*" + fence.charAt(0) + "{" + fence.length + ",}\\s*$");
        i++;
        while (i < n && !closeRe.test(lines[i])) { buf.push(lines[i]); i++; }
        out.push("<pre><code" + (lang ? ' class="lang-' + lang + '" data-lang="' + lang + '"' : "") + ">" + buf.join("\n") + "</code></pre>");
        i++; blank = false; continue;
      }
      if (raw.trim() === "") { flushPara(); blank = true; i++; continue; }
      if (RE.quote.test(raw)) {
        flushPara(); closeLists();
        var q = [];
        while (i < n && (m = lines[i].match(RE.quote))) { q.push(m[1]); i++; }
        out.push("<blockquote>" + renderLines(q) + "</blockquote>"); blank = false; continue;
      }
      if (RE.hr.test(raw)) { flushPara(); closeLists(); out.push("<hr>"); i++; blank = false; continue; }
      if ((m = raw.match(RE.heading))) {
        flushPara(); closeLists();
        var lvl = m[1].length;
        out.push("<h" + lvl + ">" + inline(m[2]) + "</h" + lvl + ">"); i++; blank = false; continue;
      }
      if (raw.indexOf("|") >= 0 && i + 1 < n && lines[i + 1].indexOf("|") >= 0 && RE.tableSep.test(lines[i + 1])) {
        flushPara(); closeLists();
        var head = cells(raw), al = cells(lines[i + 1]).map(align), t = '<div class="md-table"><table><thead><tr>';
        head.forEach(function (c, k) { t += cell("th", al[k], inline(c)); });
        t += "</tr></thead><tbody>"; i += 2;
        while (i < n && lines[i].trim() !== "" && lines[i].indexOf("|") >= 0) {
          var row = cells(lines[i]); t += "<tr>";
          for (var k = 0; k < head.length; k++) t += cell("td", al[k], inline(row[k] || ""));
          t += "</tr>"; i++;
        }
        out.push(t + "</tbody></table></div>"); blank = false; continue;
      }
      if ((m = raw.match(RE.ul))) { item("ul", m[1].length, m[2]); i++; blank = false; continue; }
      // an ordered item may only interrupt a paragraph when it starts at 1 (CommonMark),
      // so "2024. It was…" mid-paragraph stays prose.
      if ((m = raw.match(RE.ol)) && (!para.length || m[2] === "1" || lst.length)) {
        item("ol", m[1].length, m[3], parseInt(m[2], 10)); i++; blank = false; continue;
      }
      if (lst.length && top().open && /^\s{2,}\S/.test(raw)) {   // indented text under an item
        out.push("<br>" + inline(raw.trim())); i++; blank = false; continue;
      }
      closeLists();
      para.push(inline(raw.replace(/\s+$/, "").replace(/\\$/, "")));
      i++; blank = false;
    }
    flushPara(); closeLists();
    return out.join("");
  }

  function block(text) { return renderLines(esc(text).split("\n")); }

  // <think>…</think> (Qwen-style reasoning) → collapsed <details>; a block that
  // is still open (streaming) stays expanded until its closing tag arrives.
  var THINK = /<think>\s*([\s\S]*?)\s*(?:<\/think>|$)/g;
  function render(src) {
    src = String(src == null ? "" : src).replace(/\r\n?/g, "\n");
    if (src.indexOf("<think>") < 0) return block(src);
    var html = "", last = 0, m;
    THINK.lastIndex = 0;
    while ((m = THINK.exec(src))) {
      html += block(src.slice(last, m.index));
      var closed = /<\/think>$/.test(m[0]);
      if (m[1]) {
        html += '<details class="md-think"' + (closed ? "" : " open") + "><summary>" +
          (closed ? "Reasoning" : "Reasoning…") + "</summary>" + block(m[1]) + "</details>";
      }
      last = m.index + m[0].length;
    }
    return html + block(src.slice(last));
  }

  var api = { esc: esc, inline: inline, render: render, version: 1 };
  root.hermesMd = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
