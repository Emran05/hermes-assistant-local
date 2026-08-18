// Hermes Assistant — native macOS shell for the local dashboard.
// A lightweight AppKit window with an embedded WKWebView pointed at the
// local hub (127.0.0.1:7788). On launch it makes sure the launchd services
// (model server + dashboard) are up, shows a splash until the backend
// responds, then loads the UI. No network beyond localhost.

import AppKit
import WebKit
import Carbon.HIToolbox        // RegisterEventHotKey — system-wide hotkey, no Accessibility TCC
import ServiceManagement       // SMAppService.mainApp — open-at-login
import SQLite3                 // P2.4 — read a snapshot of ~/Library/Messages/chat.db (FDA lives here)

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

    // ---- menu-bar info dropdown: weather · Claude usage · date · system ----
    var menuTopbar: [String: Any] = [:]   // /api/topbar cache (weather + claude)
    var menuSys: [String: Any] = [:]      // /api/sys cache (cpu/ram/disk/model)
    var menuInfoTimer: Timer?

    // ---- P2.4 Message Center: the FDA-holding app feeds the dashboard ------
    let messagesSync = MessagesSync()

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
        messagesSync.start()          // P2.4 — silent background chat.db → dashboard sync
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
    // Any direct navigation to a non-localhost http(s) URL also goes to the browser,
    // and any external scheme (e.g. x-apple.systempreferences: from the Message
    // Center's Full-Disk-Access grant card) is handed to the system.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url {
            let scheme = (url.scheme ?? "").lowercased()
            if scheme == "http" || scheme == "https" {
                if let host = url.host,
                   host != "127.0.0.1", host != "localhost", host != "::1" {
                    NSWorkspace.shared.open(url)
                    decisionHandler(.cancel)
                    return
                }
            } else if !scheme.isEmpty, !["about", "data", "blob", "file", "javascript"].contains(scheme) {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
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
            b.toolTip = "Hermes — weather · Claude usage · system (⌃⌥Space for Quick Ask)"
        }
        fetchMenuInfo()
        menuInfoTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            self?.fetchMenuInfo()
        }
    }

    @objc func statusItemClicked() {
        fetchMenuInfo()                    // refresh cache for next open; show cached now
        let menu = buildStatusMenu()
        if let b = statusItem.button {
            menu.popUp(positioning: nil, at: NSPoint(x: 0, y: b.bounds.height + 5), in: b)
        }
    }

    // Fetch weather+Claude (/api/topbar) and system meters (/api/sys) into the
    // menu caches. Best-effort, non-blocking; the dropdown reads whatever's cached.
    func fetchMenuInfo() {
        for (path, isTopbar) in [("api/topbar", true), ("api/sys", false)] {
            let url = DASH_URL.appendingPathComponent(path)
            URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
                guard let self = self, let data = data,
                      let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                else { return }
                DispatchQueue.main.async {
                    if isTopbar { self.menuTopbar = obj } else { self.menuSys = obj }
                }
            }.resume()
        }
    }

    private func numStr(_ v: Any?) -> String {
        if let i = v as? Int { return "\(i)" }
        if let d = v as? Double { return "\(Int(d.rounded()))" }
        return "?"
    }
    private func hm(_ secs: Double) -> String {
        let s = Int(secs), h = Int(secs) / 3600, m = (s % 3600) / 60
        return h > 0 ? "in \(h)h \(m)m" : "in \(m)m"
    }
    private func infoRow(_ title: String) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        it.isEnabled = false
        return it
    }

    func buildStatusMenu() -> NSMenu {
        let m = NSMenu()

        // date header — MM/DD/YYYY, bold
        let df = DateFormatter(); df.dateFormat = "MM/dd/yyyy"
        let today = df.string(from: Date())
        let hdr = NSMenuItem(title: today, action: nil, keyEquivalent: "")
        hdr.isEnabled = false
        hdr.attributedTitle = NSAttributedString(string: today,
            attributes: [.font: NSFont.systemFont(ofSize: 13, weight: .semibold)])
        m.addItem(hdr)
        m.addItem(.separator())

        // weather
        if let w = menuTopbar["weather"] as? [String: Any],
           (w["configured"] as? Bool) == true,
           (w["error"] as? String) == nil,
           let t = w["temp"], !(t is NSNull) {
            let city = (w["city"] as? String) ?? ""
            let desc = (w["desc"] as? String) ?? ""
            m.addItem(infoRow("Weather    \(numStr(t))°  \(city)" + (desc.isEmpty ? "" : "  ·  \(desc)")))
        }

        // Claude plan usage
        if let c = menuTopbar["claude"] as? [String: Any], (c["available"] as? Bool) == true {
            var parts: [String] = []
            if let pct = c["pct"] as? Int { parts.append("\(pct)%") }
            if let msgs = c["msgs"], !(msgs is NSNull) { parts.append("\(numStr(msgs)) msgs") }
            if let cost = c["cost"] as? Double { parts.append(String(format: "$%.2f", cost)) }
            if let r = c["reset_in"] as? Double { parts.append("resets \(hm(r))") }
            if !parts.isEmpty { m.addItem(infoRow("Claude     " + parts.joined(separator: "  ·  "))) }
        }

        // system hardware
        if !menuSys.isEmpty {
            m.addItem(infoRow("System     CPU \(numStr(menuSys["cpu_pct"]))%  ·  RAM \(numStr(menuSys["ram_pct"]))%  ·  Disk \(numStr(menuSys["disk_pct"]))% (\(numStr(menuSys["disk_free_gb"])) GB free)"))
            let online = (menuSys["model_online"] as? Bool) == true
            m.addItem(infoRow("Model      " + (online ? "online" : "sleeping / offline")))
        }

        m.addItem(.separator())
        m.addItem(withTitle: "Quick Ask", action: #selector(toggleQuickAsk), keyEquivalent: "")
        m.addItem(withTitle: "Open Dashboard", action: #selector(showMainWindowAction(_:)), keyEquivalent: "")
        let login = NSMenuItem(title: "Open at Login", action: #selector(toggleLoginItem), keyEquivalent: "")
        if #available(macOS 13.0, *) {
            login.state = (SMAppService.mainApp.status == .enabled) ? .on : .off
        }
        m.addItem(login)
        m.addItem(.separator())
        m.addItem(withTitle: "Quit Hermes Assistant", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")

        for it in m.items where it.action != nil { it.target = self }
        m.items.last?.target = NSApp     // terminate → NSApp
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

// ==========================================================================
// P2.4 — Message Center sync. The signed app is the only process that can
// hold Full Disk Access, so IT reads ~/Library/Messages/chat.db and POSTs
// decoded conversation previews to the dashboard's token-guarded loopback
// ingest (/api/messages/ingest). The launchd python never opens chat.db.
//
// Flow (every 60 s + once ~10 s after launch, all off the main thread):
//   1. FDA probe: open(2) chat.db read-only — TCC denies with EPERM/EACCES
//      → POST {fda:false} so the widget shows the grant steps.
//   2. Point-in-time snapshot via SQLite's online-backup API into the app's
//      temp dir (no WAL locks held on the live db; SQLITE_BUSY → skip tick).
//   3. Query the SNAPSHOT: last ~14 chats w/ last message, unread, today,
//      participants, service; decode attributedBody (byte-scan); convert
//      Apple-epoch dates (ns since 2001-01-01 → unix).
//   4. POST JSON (with the shared messages-token) and unlink the snapshot.
// Silent by design: no UI, failures just retry next tick.
// ==========================================================================

final class MessagesSync {
    private let queue = DispatchQueue(label: "local.hermes.messages-sync", qos: .utility)
    private var timer: Timer?
    private let chatDB = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Messages/chat.db")
    private let tokenPath = (NSHomeDirectory() as NSString).appendingPathComponent(".hermes/dashboard/messages-token")
    private let ingestURL = URL(string: "http://127.0.0.1:7788/api/messages/ingest")!
    private let maxChats = 14
    private let previewCap = 200

    func start() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { [weak self] in self?.kick() }
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.kick() }
        timer?.tolerance = 5
    }

    private func kick() { queue.async { [weak self] in self?.sync() } }

    // Re-read every tick so a re-minted token self-heals without a relaunch.
    private func token() -> String {
        (try? String(contentsOfFile: tokenPath, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    // Apple epoch: ns since 2001-01-01 on modern macOS, seconds on pre-High-Sierra rows.
    private func appleTS(_ d: Int64) -> Double {
        if d == 0 { return 0 }
        let v = Double(d)
        return 978307200.0 + (v > 1e11 ? v / 1e9 : v)
    }

    private func sync() {
        // 1. FDA probe — TCC denial surfaces as EPERM/EACCES on open(2).
        let fd = open(chatDB, O_RDONLY)
        if fd < 0 {
            postFDADenied()
            return
        }
        close(fd)

        // 2. Consistent snapshot in temp (backup API — no locks left on the live db).
        var src: OpaquePointer?
        guard sqlite3_open_v2(chatDB, &src, SQLITE_OPEN_READONLY, nil) == SQLITE_OK, src != nil else {
            sqlite3_close(src)
            postFDADenied()
            return
        }
        let tmp = NSTemporaryDirectory() + "hermes-msgsnap-\(getpid()).db"
        defer { for sfx in ["", "-wal", "-shm"] { unlink(tmp + sfx) } }   // snapshot never persists
        var dst: OpaquePointer?
        guard sqlite3_open(tmp, &dst) == SQLITE_OK, dst != nil else {
            sqlite3_close(dst); sqlite3_close(src)
            return
        }
        var copied = false
        if let bk = sqlite3_backup_init(dst, "main", src, "main") {
            copied = sqlite3_backup_step(bk, -1) == SQLITE_DONE
            sqlite3_backup_finish(bk)
        }
        sqlite3_close(src)
        if !copied {                  // SQLITE_BUSY etc. — keep last store, retry next tick
            sqlite3_close(dst)
            return
        }

        // 3+4. Query the snapshot, then POST.
        let payload = buildPayload(dst!)
        sqlite3_close(dst)
        post(payload)
    }

    // ---- SQL helpers (C API) ----------------------------------------------
    private func colText(_ st: OpaquePointer?, _ i: Int32) -> String? {
        guard let c = sqlite3_column_text(st, i) else { return nil }
        return String(cString: c)
    }

    private func colBlob(_ st: OpaquePointer?, _ i: Int32) -> Data? {
        guard let p = sqlite3_column_blob(st, i) else { return nil }
        let n = Int(sqlite3_column_bytes(st, i))
        return n > 0 ? Data(bytes: p, count: n) : nil
    }

    private func scalarInt(_ db: OpaquePointer, _ sql: String, _ binds: [Int64]) -> Int {
        var st: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &st, nil) == SQLITE_OK else { return 0 }
        defer { sqlite3_finalize(st) }
        for (i, v) in binds.enumerated() { sqlite3_bind_int64(st, Int32(i + 1), v) }
        return sqlite3_step(st) == SQLITE_ROW ? Int(sqlite3_column_int64(st, 0)) : 0
    }

    // attributedBody byte-scan (port of the dashboard's proven decoder): find
    // "NSString", skip to the next '+', read the length prefix (0x81 → u16 LE,
    // 0x82 → u32 LE, else one byte), slice as UTF-8; fallback = first
    // printable run after the marker.
    private func decodeBody(_ blob: Data) -> String {
        let raw = [UInt8](blob)
        let needle = Array("NSString".utf8)
        guard raw.count > needle.count else { return "" }
        var marker = -1
        if raw.count >= needle.count {
            outer: for i in 0...(raw.count - needle.count) {
                for j in 0..<needle.count where raw[i + j] != needle[j] { continue outer }
                marker = i
                break
            }
        }
        guard marker >= 0 else { return "" }

        var s = ""
        if let plus = raw[(marker + needle.count)...].firstIndex(of: UInt8(ascii: "+")) {
            var p = plus + 1
            if p < raw.count {
                var ln = Int(raw[p])
                if ln == 0x81, p + 2 < raw.count {
                    ln = Int(raw[p + 1]) | (Int(raw[p + 2]) << 8); p += 3
                } else if ln == 0x82, p + 4 < raw.count {
                    ln = Int(raw[p + 1]) | (Int(raw[p + 2]) << 8) |
                         (Int(raw[p + 3]) << 16) | (Int(raw[p + 4]) << 24); p += 5
                } else {
                    p += 1
                }
                let end = min(raw.count, p + max(0, min(ln, 4000)))
                if p < end {
                    s = String(decoding: raw[p..<end], as: UTF8.self)
                        .replacingOccurrences(of: "\0", with: "")
                        .replacingOccurrences(of: "\u{FFFD}", with: "")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                }
            }
        }
        if !s.isEmpty { return s }

        var out: [UInt8] = []
        var started = false
        for b in raw[min(marker + 8, raw.count)...].prefix(3000) {
            if (32..<127).contains(b) || b == 9 || b == 10 { out.append(b); started = true }
            else if started && out.count > 1 { break }
        }
        let t = String(decoding: out, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return t.count > 1 ? t : ""
    }

    // ---- build the ingest payload from the snapshot -------------------------
    private func buildPayload(_ db: OpaquePointer) -> [String: Any] {
        var convos: [[String: Any]] = []
        var totalUnread = 0
        var totalToday = 0

        // local midnight → Apple-epoch nanoseconds (for the per-chat today count)
        let midnight = Calendar.current.startOfDay(for: Date()).timeIntervalSince1970
        let todayAppleNS = Int64((midnight - 978307200.0) * 1e9)

        var chats: OpaquePointer?
        let chatSQL = """
            SELECT c.ROWID, c.chat_identifier, c.display_name, c.style, MAX(m.date) AS mx
            FROM chat c
            JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY c.ROWID ORDER BY mx DESC LIMIT \(maxChats)
            """
        guard sqlite3_prepare_v2(db, chatSQL, -1, &chats, nil) == SQLITE_OK else {
            return envelope(fda: true, convos: [], unread: 0, today: 0)
        }
        while sqlite3_step(chats) == SQLITE_ROW {
            let cid = sqlite3_column_int64(chats, 0)
            let ident = colText(chats, 1) ?? ""
            let dname = (colText(chats, 2) ?? "").trimmingCharacters(in: .whitespaces)
            let style = Int(sqlite3_column_int(chats, 3))

            // last message in this chat
            var last: OpaquePointer?
            let lastSQL = """
                SELECT m.text, m.attributedBody, m.is_from_me, m.date,
                       m.cache_has_attachments, m.associated_message_type, h.id, m.service
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id = ? ORDER BY m.date DESC LIMIT 1
                """
            guard sqlite3_prepare_v2(db, lastSQL, -1, &last, nil) == SQLITE_OK else { continue }
            sqlite3_bind_int64(last, 1, cid)
            guard sqlite3_step(last) == SQLITE_ROW else { sqlite3_finalize(last); continue }

            var body = colText(last, 0) ?? ""
            if body.isEmpty, let blob = colBlob(last, 1) { body = decodeBody(blob) }
            let fromMe = sqlite3_column_int(last, 2) != 0
            let date = sqlite3_column_int64(last, 3)
            let hasAtt = sqlite3_column_int(last, 4) != 0
            let isTapback = sqlite3_column_int(last, 5) != 0
            let handle = colText(last, 6)
            let service = colText(last, 7) ?? ""
            sqlite3_finalize(last)

            if body.isEmpty {
                body = hasAtt ? "Attachment" : (isTapback ? "Reaction" : "")
            }

            let unread = scalarInt(db, """
                SELECT COUNT(*) FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id = ? AND m.is_from_me = 0 AND m.is_read = 0
                """, [cid])
            totalUnread += unread

            let today = scalarInt(db, """
                SELECT COUNT(*) FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id = ? AND m.date >= ?
                """, [cid, todayAppleNS])
            totalToday += today

            // participants (for group flag + naming)
            var parts: [String] = []
            var pst: OpaquePointer?
            if sqlite3_prepare_v2(db, """
                SELECT h.id FROM chat_handle_join chj
                JOIN handle h ON h.ROWID = chj.handle_id WHERE chj.chat_id = ?
                """, -1, &pst, nil) == SQLITE_OK {
                sqlite3_bind_int64(pst, 1, cid)
                while sqlite3_step(pst) == SQLITE_ROW {
                    if let hid = colText(pst, 0), !hid.isEmpty { parts.append(hid) }
                }
            }
            sqlite3_finalize(pst)

            let isGroup = (style == 43) || parts.count > 1
            // raw name — the dashboard prettifies phone handles
            let name = !dname.isEmpty ? dname : (parts.first ?? (ident.isEmpty ? "Unknown" : ident))

            convos.append([
                "name": name,
                "ident": ident,
                "group": isGroup,
                "participants": max(parts.count, 1),
                "last": String(body.prefix(previewCap)),
                "from_me": fromMe,
                "sender": fromMe ? "You" : (handle ?? ident),
                "ts": appleTS(date),
                "unread": unread,
                "attachment": hasAtt,
                "reaction": isTapback,
                "today_count": today,
                "service": service,
            ])
        }
        sqlite3_finalize(chats)

        return envelope(fda: true, convos: convos, unread: totalUnread, today: totalToday)
    }

    private func envelope(fda: Bool, convos: [[String: Any]], unread: Int, today: Int,
                          reason: String? = nil) -> [String: Any] {
        var p: [String: Any] = [
            "v": 1,
            "generated_at": Date().timeIntervalSince1970,
            "fda": fda,
            "host": Host.current().localizedName ?? "Mac",
            "conversations": convos,
            "totals": ["unread": unread, "today": today],
            "token": token(),
        ]
        if let r = reason { p["reason"] = r }
        return p
    }

    private func postFDADenied() {
        post(envelope(fda: false, convos: [], unread: 0, today: 0,
                      reason: "Full Disk Access needed"))
    }

    private func post(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        var req = URLRequest(url: ingestURL)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 8
        req.httpBody = data
        // Fire and forget — a failed POST just means the widget keeps its last
        // state until the next tick. Never log message content.
        URLSession.shared.dataTask(with: req).resume()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
