// Hermes Assistant — native macOS shell for the local dashboard.
// A lightweight AppKit window with an embedded WKWebView pointed at the
// local hub (127.0.0.1:7788). On launch it makes sure the launchd services
// (model server + dashboard) are up, shows a splash until the backend
// responds, then loads the UI. No network beyond localhost.

import AppKit
import WebKit

let DASH_URL = URL(string: "http://127.0.0.1:7788/")!
let HEALTH_URL = URL(string: "http://127.0.0.1:7788/api/health")!
let SERVICES = ["com.hermes.mlx-server", "com.hermes.dashboard"]

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSWindowDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var retryTimer: Timer?
    var loaded = false

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        ensureServices()

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "Hermes Assistant"
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.setFrameAutosaveName("HermesMainWindow")
        // Seamless glass window: the web UI paints edge-to-edge under a
        // transparent titlebar; traffic lights float over the glass header.
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.backgroundColor = NSColor(calibratedRed: 0.031, green: 0.039, blue: 0.067, alpha: 1) // #080A11, matches dark ground

        let cfg = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground") // let the page's own bg show, no white flash
        window.delegate = self
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showSplash()
        startRetry()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

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

    // if the backend restarts underneath us, fall back to splash + retry
    func webView(_ wv: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError error: Error) {
        loaded = false
        showSplash()
        ensureServices()
        startRetry()
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
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
