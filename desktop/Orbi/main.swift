// Orbi.app (Phase 19.6) — Orbi on the Mac desktop, no browser.
//
// A frameless, transparent, always-on-top panel showing ORBIT's /ui/orbi.html (the
// avatar, its speech bubble and voice), plus a ◎ menu-bar item. If the ORBIT server
// isn't running, the app starts it (scripts/run_demo.sh). Built by
// scripts/build_orbi_app.sh; the ORBIT folder is baked in at build time.
import AppKit
import Carbon.HIToolbox
import ServiceManagement
import WebKit

let orbitURL = URL(string: "http://localhost:8765/ui/orbi.html")!
let dashboardURL = URL(string: "http://localhost:8765/ui/")!
let orbitRoot = Bundle.main.object(forInfoDictionaryKey: "ORBITRoot") as? String ?? ""

/// A strip under Orbi that moves the window when dragged.
final class DragHandle: NSView {
    override var mouseDownCanMoveWindow: Bool { true }
    override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
    override func draw(_ dirtyRect: NSRect) {
        NSColor(white: 1, alpha: 0.55).setFill()
        let w: CGFloat = 46, h: CGFloat = 5
        NSBezierPath(roundedRect: NSRect(x: (bounds.width - w) / 2, y: (bounds.height - h) / 2, width: w, height: h),
                     xRadius: 2.5, yRadius: 2.5).fill()
    }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
}

final class OrbiPanel: NSPanel {
    override var canBecomeKey: Bool { true }  // clicks reach the page
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var panel: OrbiPanel!
    var web: WKWebView!
    var status: NSStatusItem!
    var server: Process?
    var showItem: NSMenuItem!
    var hideItem: NSMenuItem!

    func applicationDidFinishLaunching(_ note: Notification) {
        buildPanel()
        buildMenu()
        registerHotKey()
        if UserDefaults.standard.bool(forKey: "orbiHidden") { panel.orderOut(nil) }
        updateShownState()
        ensureServer { [weak self] in self?.web.load(URLRequest(url: orbitURL)) }
    }

    // MARK: window
    func buildPanel() {
        let size = NSSize(width: 270, height: 380)
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let saved = UserDefaults.standard.string(forKey: "orbiFrame").map(NSRectFromString)
        let frame = saved ?? NSRect(x: screen.maxX - size.width - 24, y: screen.minY + 24, width: size.width, height: size.height)
        panel = OrbiPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false

        let config = WKWebViewConfiguration()
        config.mediaTypesRequiringUserActionForPlayback = []  // Orbi may speak and sing
        config.userContentController.add(self, name: "orbi")
        web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: config)
        web.setValue(false, forKey: "drawsBackground")  // transparent: only Orbi is visible
        web.uiDelegate = self
        web.navigationDelegate = self
        web.autoresizingMask = [.width, .height]

        let root = NSView(frame: NSRect(origin: .zero, size: size))
        root.addSubview(web)
        let handle = DragHandle(frame: NSRect(x: 0, y: 0, width: size.width, height: 18))
        handle.autoresizingMask = [.width]
        root.addSubview(handle)
        web.frame = NSRect(x: 0, y: 18, width: size.width, height: size.height - 18)
        panel.contentView = root
        panel.orderFrontRegardless()
        NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: panel, queue: .main) { [weak self] _ in
            if let f = self?.panel.frame { UserDefaults.standard.set(NSStringFromRect(f), forKey: "orbiFrame") }
        }
    }

    // Microphone for ORBIT's own page only.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType, decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(origin.host == "localhost" && origin.port == 8765 ? .grant : .deny)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.web.load(URLRequest(url: orbitURL)) }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        if type == "server-down" { ensureServer {} }
        if type == "hide" { hideOrbi() }
        if type == "state", let s = body["state"] as? String {
            status.button?.title = ["listening": "◉", "thinking": "◍", "speaking": "◎"][s] ?? "◎"
        }
    }

    // MARK: menu bar
    func buildMenu() {
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        status.button?.title = "◎"
        let menu = NSMenu()
        menu.addItem(withTitle: "Talk to Orbi", action: #selector(talk), keyEquivalent: "t").target = self
        showItem = NSMenuItem(title: "Show Orbi", action: #selector(showOrbi), keyEquivalent: "")
        showItem.target = self
        hideItem = NSMenuItem(title: "Hide Orbi", action: #selector(hideOrbi), keyEquivalent: "")
        hideItem.target = self
        menu.addItem(showItem)
        menu.addItem(hideItem)
        let hint = NSMenuItem(title: "Show/Hide from anywhere: ⌥⌘O", action: nil, keyEquivalent: "")
        hint.isEnabled = false
        menu.addItem(hint)
        menu.addItem(withTitle: "Open ORBIT dashboard", action: #selector(openDashboard), keyEquivalent: "d").target = self
        menu.addItem(withTitle: "Open Orbi in the Quest headset", action: #selector(openInQuest), keyEquivalent: "q").target = self
        menu.addItem(.separator())
        let login = NSMenuItem(title: "Open at Login", action: #selector(toggleLogin), keyEquivalent: "")
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(withTitle: "Restart ORBIT server", action: #selector(restartServer), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Orbi", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        status.menu = menu
    }

    @objc func talk() { panel.orderFrontRegardless(); web.evaluateJavaScript("window.orbiToggle && window.orbiToggle()") }
    @objc func toggleShown() { panel.isVisible ? hideOrbi() : showOrbi() }
    @objc func showOrbi() {
        panel.orderFrontRegardless()
        UserDefaults.standard.set(false, forKey: "orbiHidden")
        updateShownState()
    }
    @objc func hideOrbi() {
        panel.orderOut(nil)
        UserDefaults.standard.set(true, forKey: "orbiHidden")
        updateShownState()
    }
    func updateShownState() {
        showItem?.state = panel.isVisible ? .on : .off
        hideItem?.state = panel.isVisible ? .off : .on
    }

    /// ⌥⌘O from any app (a Carbon hot key: no extra permission needed).
    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, ctx in
            let me = Unmanaged<AppDelegate>.fromOpaque(ctx!).takeUnretainedValue()
            DispatchQueue.main.async { me.toggleShown() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), nil)
        var ref: EventHotKeyRef?
        RegisterEventHotKey(UInt32(kVK_ANSI_O), UInt32(cmdKey | optionKey), EventHotKeyID(signature: OSType(0x4F524249), id: 1),
                            GetApplicationEventTarget(), 0, &ref)
    }
    @objc func openDashboard() { NSWorkspace.shared.open(dashboardURL) }
    /// Opens ORBIT in the Quest browser, starts AR and listening (USB cable, developer mode).
    @objc func openInQuest() {
        guard !orbitRoot.isEmpty else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-lc", "PATH=/opt/homebrew/bin:$PATH \"\(orbitRoot)/scripts/open_orbi_quest.sh\""]
        p.currentDirectoryURL = URL(fileURLWithPath: orbitRoot)
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        p.terminationHandler = { proc in
            let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            DispatchQueue.main.async {
                let alert = NSAlert()
                alert.messageText = proc.terminationStatus == 0 ? "Orbi is open in the headset" : "Couldn't open Orbi in the headset"
                alert.informativeText = text.trimmingCharacters(in: .whitespacesAndNewlines)
                alert.runModal()
            }
        }
        try? p.run()
    }
    @objc func toggleLogin(_ item: NSMenuItem) {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() }
        } catch {
            let alert = NSAlert()
            alert.messageText = "Couldn't change Open at Login"
            alert.informativeText = "Add Orbi in System Settings → General → Login Items instead.\n\(error.localizedDescription)"
            alert.runModal()
        }
        item.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }
    @objc func restartServer() { runServerScript { [weak self] in self?.web.reload() } }

    // MARK: ORBIT server
    func serverUp() -> Bool {
        var req = URLRequest(url: URL(string: "http://localhost:8765/voice/status")!)
        req.timeoutInterval = 1.5
        let done = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, res, _ in ok = (res as? HTTPURLResponse)?.statusCode == 200; done.signal() }.resume()
        _ = done.wait(timeout: .now() + 2)
        return ok
    }

    func ensureServer(then: @escaping () -> Void) {
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            if self.serverUp() { DispatchQueue.main.async(execute: then); return }
            self.runServerScript(then: then)
        }
    }

    func runServerScript(then: @escaping () -> Void) {
        guard !orbitRoot.isEmpty else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["\(orbitRoot)/scripts/run_demo.sh", "--lan"]
        p.currentDirectoryURL = URL(fileURLWithPath: orbitRoot)
        p.terminationHandler = { _ in DispatchQueue.main.async(execute: then) }
        try? p.run()
        server = p
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // lives in the menu bar, not the Dock
app.run()
