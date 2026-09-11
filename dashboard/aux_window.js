/* aux_window.js — tell the native shell where the title bar is.
 *
 * The app's main window is .fullSizeContentView with a hidden, transparent
 * title bar and a WKWebView as its entire contentView, and WebKit ignores
 * Electron's `-webkit-app-region: drag` (a Chromium property — it has never
 * done anything here), so without this file no pixel of the window starts a
 * drag. HermesWebView (app/main.swift) performs the drag; it only needs to know
 * which rectangles are the handle. Everything below is in CSS pixels with a
 * top-left origin — exactly what getBoundingClientRect() returns and what the
 * Swift side converts into view coordinates.
 *
 * In a browser (or the Quick Ask popover) the bridge is absent and this no-ops.
 */
(function () {
  var mh = window.webkit && window.webkit.messageHandlers
        && window.webkit.messageHandlers.hermesWindow;
  if (!mh) return;

  // The carve-outs the header's (inert) -webkit-app-region rules used to list,
  // plus an explicit opt-out. Widen THIS when a new chip lands in the header.
  var NODRAG = 'button,a,input,select,textarea,[role=button],[role=tab],' +
               '[contenteditable],.chip,.pill,.seg,.menu,.dropdown,' +
               '#model-pill,.ctx-chip,[data-nodrag]';

  var header = null, timer = 0, last = '';

  function hdr() {
    if (!header || !header.isConnected) header = document.querySelector('header');
    return header;
  }
  function r4(r) {
    return { x: Math.round(r.left), y: Math.round(r.top),
             w: Math.round(r.width), h: Math.round(r.height) };
  }
  function post() {
    var h = hdr(); if (!h) return;
    var b = h.getBoundingClientRect();
    if (b.width <= 0 || b.height <= 0) return;      // hidden — keep the last band
    var nodrag = [];
    h.querySelectorAll(NODRAG).forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) nodrag.push(r4(r));
    });
    var msg = { action: 'dragRegions', band: r4(b), nodrag: nodrag };
    var key = JSON.stringify(msg);
    if (key === last) return;                        // nothing moved
    last = key;
    try { mh.postMessage(msg); } catch (e) {}
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(post, 120); }

  window.addEventListener('resize', schedule);
  var h0 = hdr();
  if (h0) {
    new MutationObserver(schedule).observe(h0, { childList: true, subtree: true, attributes: true });
    if (window.ResizeObserver) new ResizeObserver(schedule).observe(h0);
  }
  // Fonts, the model pill and late chips all settle after load — re-measure a few times.
  if (document.readyState === 'complete') post();
  else window.addEventListener('load', post);
  [0, 300, 1200, 3000].forEach(function (t) { setTimeout(post, t); });
})();
