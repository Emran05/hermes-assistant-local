// Hermes Assistant — native macOS shell for the local dashboard.
// A lightweight AppKit window with an embedded WKWebView pointed at the
// local hub (127.0.0.1:7788). On launch it makes sure the launchd services
// (model server + dashboard) are up, shows a splash until the backend
// responds, then loads the UI. No network beyond localhost.

import AppKit
import WebKit
import Carbon.HIToolbox        // RegisterEventHotKey — system-wide hotkey, no Accessibility TCC
import ServiceManagement       // SMAppService.mainApp — open-at-login

let DASH_URL = URL(string: "http://127.0.0.1:7788/")!
let HEALTH_URL = URL(string: "http://127.0.0.1:7788/api/health")!
let SERVICES = ["com.hermes.mlx-server", "com.hermes.dashboard"]

// Popover shell: a ~6-line HTML doc loaded with baseURL = the dashboard origin,
// so every fetch('/api/…'), /motion.min.js, /aux_quickask.js, /aux_clip.js is
// same-origin (NSAllowsLocalNetworking already true).  The two aux files carry
// the whole UX; this is only the bootstrap.
let QUICKASK_HTML = """
<!doctype html><meta charset="utf-8">
<script>window.__HERMES_QUICKASK__=1;</script>
<div id="qa" style="padding:16px;font:13px -apple-system,sans-serif;color:#64748B">starting your local assistant…</div>
<script src="/motion.min.js"></script>
<script src="/aux_quickask.js"></script>
<script src="/aux_clip.js"></script>
"""

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSWindowDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var retryTimer: Timer?
    var loaded = false

    // ---- P2.2 menu-bar quick-ask + P2.3 clipboard bridge ------------------
    var statusItem: NSStatusItem!
    var popover: NSPopover!
    var quickWebView: WKWebView!
    var hotKeyRef: EventHotKeyRef?
    var quickLoaded = false
    var pendingResumeJS: String?      // a hand-off to run once the main window has loaded

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        ensureServices()

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "Hermes Assistant"
        window.minSize = NSSize(width: 900, height: 600)
        // The app stays resident in the menu bar, so closing the main window must
        // not deallocate it — reopen it from the status item or the Dock.
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("HermesMainWindow")
        // Seamless glass window: the web UI paints edge-to-edge under a
        // transparent titlebar; traffic lights float over the glass header.
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.backgroundColor = NSColor(calibratedRed: 0.031, green: 0.039, blue: 0.067, alpha: 1) // #080A11, matches dark ground

        let cfg = WKWebViewConfiguration()
        // The main window's WebView also exposes the clipboard read/write bridge
        // so aux_clip.js runs no-prompt in the dashboard (falls back to
        // navigator.clipboard when these are absent).
        let ucc = WKUserContentController()
        ucc.add(self, name: "hermesClip")
        ucc.add(self, name: "hermesClipWrite")
        cfg.userContentController = ucc
        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground") // let the page's own bg show, no white flash
        window.delegate = self
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        installStatusItem()
        installPopover()
        installHotKey()

        showSplash()
        startRetry()
    }

    // Stay alive in the menu bar when the main window is closed (a menu-bar
    // app), so the global hotkey and quick-ask keep working.
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { false }

    // Dock-icon click with no visible window → bring the main window back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showMainWindow() }
        return true
    }

    // ---- fullscreen: tell the web UI so it drops the traffic-light inset ---
    private func setFullscreen(_ on: Bool) {
        webView?.evaluateJavaScript(
            "document.documentElement.setAttribute('data-fullscreen','\(on ? "1" : "0")')",
            completionHandler: nil)
    }
    func windowDidEnterFullScreen(_ n: Notification) { setFullscreen(true) }
    func windowDidExitFullScreen(_ n: Notification) { setFullscreen(false) }

    // ---- external links: open in the default browser, not the app shell ----
    // window.open('_blank') from widget links lands here.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // JS dialogs — WKWebView drops confirm()/alert()/prompt() to false/nil unless
    // the UI delegate implements these. Without them, the model switch/pause
    // buttons (which gate on confirm) silently do nothing.
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message; a.addButton(withTitle: "OK")
        if let w = window { a.beginSheetModal(for: w) { _ in completionHandler() } }
        else { a.runModal(); completionHandler() }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        let done: (NSApplication.ModalResponse) -> Void = { r in completionHandler(r == .alertFirstButtonReturn) }
        if let w = window { a.beginSheetModal(for: w, completionHandler: done) }
        else { done(a.runModal()) }
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = prompt
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        tf.stringValue = defaultText ?? ""
        a.accessoryView = tf
        let done: (NSApplication.ModalResponse) -> Void = { r in
            completionHandler(r == .alertFirstButtonReturn ? tf.stringValue : nil) }
        if let w = window { a.beginSheetModal(for: w, completionHandler: done) }
        else { done(a.runModal()) }
    }
    // Any direct navigation to a non-localhost http(s) URL also goes to the browser.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url, let host = url.host,
           (url.scheme == "http" || url.scheme == "https"),
           host != "127.0.0.1", host != "localhost", host != "::1" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // ---- backend bring-up -------------------------------------------------

    func ensureServices() {
        let uid = getuid()
        for label in SERVICES {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            p.arguments = ["kickstart", "gui/\(uid)/\(label)"]
            try? p.run()
        }
    }

    func startRetry() {
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.tryConnect()
        }
        tryConnect()
    }

    func tryConnect() {
        var req = URLRequest(url: HEALTH_URL)
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { [weak self] _, resp, _ in
            guard let self = self else { return }
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                DispatchQueue.main.async {
                    if !self.loaded {
                        self.loaded = true
                        self.retryTimer?.invalidate()
                        self.webView.load(URLRequest(url: DASH_URL))
                    }
                }
            }
        }.resume()
    }

    // if the backend restarts underneath us, fall back to splash + retry.
    // Only react for the MAIN webView — a popover load hiccup must not splash it.
    func webView(_ wv: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError error: Error) {
        guard wv === webView else { return }
        loaded = false
        showSplash()
        ensureServices()
        startRetry()
    }

    // Once the main window finishes loading the dashboard, run any pending
    // quick-ask hand-off (resume an approval / open the menubar session).
    func webView(_ wv: WKWebView, didFinish nav: WKNavigation!) {
        guard wv === webView, let js = pendingResumeJS else { return }
        pendingResumeJS = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.webView.evaluateJavaScript(js, completionHandler: nil)
        }
    }

    func showSplash() {
        let html = """
        <!doctype html><meta charset='utf-8'>
        <style>
          :root{color-scheme:light dark}
          body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
               gap:18px;background:#F4F4F6;color:#0F172A;
               font:15px/-apple-system -apple-system,BlinkMacSystemFont,sans-serif}
          @media(prefers-color-scheme:dark){body{background:#0B0E14;color:#F1F5F9}}
          .logo{width:64px;height:64px;border-radius:18px;background:linear-gradient(135deg,#6366F1,#8B5CF6);
                display:flex;align-items:center;justify-content:center}
          .logo svg{width:34px;height:34px;fill:#fff}
          .sub{color:#64748B;font-size:13px}
          .dots{display:inline-flex;gap:6px}
          .dots i{width:8px;height:8px;border-radius:50%;background:#6366F1;opacity:.35;animation:b 1.2s infinite}
          .dots i:nth-child(2){animation-delay:.18s}.dots i:nth-child(3){animation-delay:.36s}
          @keyframes b{0%,100%{opacity:.25}45%{opacity:.9}}
        </style>
        <div class='logo'><svg viewBox='0 0 24 24'><path d='M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9zM19 3.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z'/></svg></div>
        <div><b>Hermes Assistant</b></div>
        <div class='sub'>starting your local assistant&hellip; the model can take a minute after a reboot</div>
        <div class='dots'><i></i><i></i><i></i></div>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // ---- menu (needed for ⌘C/⌘V/⌘Q etc. to work) ---------------------------

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Hermes Assistant",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Hermes Assistant",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit Hermes Assistant",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload(_:)), keyEquivalent: "r")
        let qa = NSMenuItem(title: "Quick Ask", action: #selector(toggleQuickAsk), keyEquivalent: " ")
        qa.keyEquivalentModifierMask = [.control, .option]   // display only; global hook is Carbon
        qa.target = self
        view.addItem(qa)
        viewItem.submenu = view
        main.addItem(viewItem)

        let winItem = NSMenuItem()
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        winItem.submenu = win
        main.addItem(winItem)
        NSApp.windowsMenu = win

        NSApp.mainMenu = main
    }

    @objc func reload(_ sender: Any?) {
        if loaded { webView.reload() } else { ensureServices(); startRetry() }
    }

    func showMainWindow() {
        guard window != nil else { return }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
    @objc func showMainWindowAction(_ sender: Any?) { showMainWindow() }

    // ======================================================================
    // P2.2 — status item + popover + Carbon hotkey + SMAppService login item
    // ======================================================================

    // Bespoke monochrome template spark glyph (isTemplate ⇒ the system tints it
    // for light/dark menu bars).  Same concave 4-point star as the app icon.
    func sparklePath(_ cx: CGFloat, _ cy: CGFloat, _ r: CGFloat) -> CGPath {
        let p = CGMutablePath(); let k = r * 0.28
        p.move(to: CGPoint(x: cx, y: cy + r))
        p.addQuadCurve(to: CGPoint(x: cx + r, y: cy), control: CGPoint(x: cx + k, y: cy + k))
        p.addQuadCurve(to: CGPoint(x: cx, y: cy - r), control: CGPoint(x: cx + k, y: cy - k))
        p.addQuadCurve(to: CGPoint(x: cx - r, y: cy), control: CGPoint(x: cx - k, y: cy - k))
        p.addQuadCurve(to: CGPoint(x: cx, y: cy + r), control: CGPoint(x: cx - k, y: cy + k))
        p.closeSubpath()
        return p
    }
    func sparkTemplateImage() -> NSImage {
        let img = NSImage(size: NSSize(width: 18, height: 18))
        img.lockFocus()
        if let ctx = NSGraphicsContext.current?.cgContext {
            ctx.setFillColor(NSColor.black.cgColor)
            ctx.addPath(sparklePath(8.2, 9.0, 6.6)); ctx.fillPath()
            ctx.addPath(sparklePath(13.4, 13.8, 2.3)); ctx.fillPath()
        }
        img.unlockFocus()
        img.isTemplate = true
        return img
    }

    func installStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            b.image = sparkTemplateImage()
            b.target = self
            b.action = #selector(statusItemClicked)
            b.sendAction(on: [.leftMouseUp, .rightMouseUp])
            b.toolTip = "Hermes Quick Ask (⌃⌥Space)"
        }
    }

    @objc func statusItemClicked() {
        let ev = NSApp.currentEvent
        if let ev = ev, ev.type == .rightMouseUp || ev.modifierFlags.contains(.control) {
            let menu = buildStatusMenu()
            if let b = statusItem.button {
                menu.popUp(positioning: nil, at: NSPoint(x: 0, y: b.bounds.height + 5), in: b)
            }
        } else {
            toggleQuickAsk()
        }
    }

    func buildStatusMenu() -> NSMenu {
        let m = NSMenu()
        m.addItem(withTitle: "Quick Ask", action: #selector(toggleQuickAsk), keyEquivalent: "")
        m.addItem(withTitle: "Open Main Window", action: #selector(showMainWindowAction(_:)), keyEquivalent: "")
        m.addItem(.separator())
        let login = NSMenuItem(title: "Open at Login", action: #selector(toggleLoginItem), keyEquivalent: "")
        if #available(macOS 13.0, *) {
            login.state = (SMAppService.mainApp.status == .enabled) ? .on : .off
        }
        m.addItem(login)
        m.addItem(.separator())
        m.addItem(withTitle: "Quit Hermes Assistant", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        for it in m.items { it.target = self }
        // terminate: retarget to NSApp so it actually quits
        m.items.last?.target = NSApp
        return m
    }

    func installPopover() {
        let cfg = WKWebViewConfiguration()
        let ucc = WKUserContentController()
        ucc.add(self, name: "hermes")            // quick-ask bridge (openMain / openApproval / close / resize)
        ucc.add(self, name: "hermesClip")        // clipboard read  → NSPasteboard
        ucc.add(self, name: "hermesClipWrite")   // clipboard write → NSPasteboard
        cfg.userContentController = ucc
        quickWebView = WKWebView(frame: NSRect(x: 0, y: 0, width: 380, height: 460), configuration: cfg)
        quickWebView.navigationDelegate = self
        quickWebView.uiDelegate = self
        quickWebView.setValue(false, forKey: "drawsBackground")

        let vc = NSViewController()
        vc.view = quickWebView

        popover = NSPopover()
        popover.behavior = .transient
        popover.contentSize = NSSize(width: 380, height: 460)
        popover.contentViewController = vc
    }

    func loadQuickIfNeeded() {
        if quickLoaded { return }
        quickLoaded = true
        quickWebView.loadHTMLString(QUICKASK_HTML, baseURL: DASH_URL)
    }

    @objc func toggleQuickAsk() {
        guard let b = statusItem?.button else { return }
        if popover.isShown { popover.performClose(nil); return }
        NSApp.activate(ignoringOtherApps: true)
        loadQuickIfNeeded()
        popover.show(relativeTo: b.bounds, of: b, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            self?.quickWebView.evaluateJavaScript("window.__qaFocus&&window.__qaFocus()", completionHandler: nil)
        }
    }

    // Carbon global hotkey (default ⌃⌥Space).  System-wide hotkeys registered
    // this way do NOT need the Accessibility grant an NSEvent global monitor would.
    func installHotKey() {
        let d = UserDefaults(suiteName: "local.hermes.assistant") ?? .standard
        d.register(defaults: ["quickask.hotkey.keyCode": Int(kVK_Space),
                              "quickask.hotkey.modifiers": Int(controlKey | optionKey)])
        let keyCode = UInt32(d.integer(forKey: "quickask.hotkey.keyCode"))
        let mods = UInt32(d.integer(forKey: "quickask.hotkey.modifiers"))

        let hotID = EventHotKeyID(signature: OSType(0x484D4B59), id: 1)   // 'HMKY'
        let status = RegisterEventHotKey(keyCode, mods, hotID, GetEventDispatcherTarget(), 0, &hotKeyRef)
        if status != noErr {
            NSLog("Hermes: RegisterEventHotKey failed (%d) — status-item click still works.", status)
            return
        }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetEventDispatcherTarget(), { (_, _, userData) -> OSStatus in
            guard let userData = userData else { return noErr }
            let me = Unmanaged<AppDelegate>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { me.toggleQuickAsk() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), nil)
    }

    @objc func toggleLoginItem() {
        if #available(macOS 13.0, *) {
            do {
                if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
                else { try SMAppService.mainApp.register() }
            } catch {
                NSLog("Hermes: SMAppService toggle failed: %@", String(describing: error))
            }
        }
    }

    // JSON-encode a string for safe injection into an evaluateJavaScript expression.
    func jsString(_ s: String) -> String {
        if let d = try? JSONSerialization.data(withJSONObject: [s]),
           let str = String(data: d, encoding: .utf8) {
            return String(str.dropFirst().dropLast())   // strip the [ ] of the 1-element array
        }
        return "\"\""
    }

    // Activate the app, bring the main window forward, and resume the given job's
    // approval there (or just open the menubar session when job is nil).
    func openMainAndResume(job: String?) {
        popover.performClose(nil)
        showMainWindow()
        let arg = job != nil ? jsString(job!) : "null"
        let js = "window.hermesQuickAskResume&&window.hermesQuickAskResume(\(arg))"
        if loaded {
            webView.evaluateJavaScript(js, completionHandler: nil)
        } else {
            pendingResumeJS = js
            ensureServices(); startRetry()
        }
    }

    // ---- WKScriptMessageHandler: popover bridge + clipboard read/write -----
    func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage) {
        switch message.name {
        case "hermesClip":   // read tier 1: hand NSPasteboard text back to JS
            let s = NSPasteboard.general.string(forType: .string) ?? ""
            message.webView?.evaluateJavaScript("window.__clipDeliver&&window.__clipDeliver(\(jsString(s)))",
                                                completionHandler: nil)
        case "hermesClipWrite":   // copy-back tier 1: explicit write to NSPasteboard
            if let body = message.body as? [String: Any], let t = body["text"] as? String {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(t, forType: .string)
            }
        case "hermes":       // quick-ask popover bridge
            guard let body = message.body as? [String: Any],
                  let action = body["action"] as? String else { return }
            switch action {
            case "close": popover.performClose(nil)
            case "openMain": openMainAndResume(job: nil)
            case "openApproval": openMainAndResume(job: body["job"] as? String)
            case "resize":
                if let h = body["h"] as? Double {
                    popover.contentSize = NSSize(width: 380, height: min(max(h, 320), 620))
                }
            default: break
            }
        default: break
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
