import AppKit
import Foundation
import UniformTypeIdentifiers

private let audioExtensions: Set<String> = ["m4a", "mp3", "wav", "aac", "flac", "ogg", "opus", "mp4", "mov", "webm"]

final class AudioDropView: NSView {
    var accept: (([URL]) -> Void)?
    var highlighted = false { didSet { needsDisplay = true } }

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }

    override func draw(_ dirtyRect: NSRect) {
        let box = bounds.insetBy(dx: 2, dy: 2)
        let path = NSBezierPath(roundedRect: box, xRadius: 12, yRadius: 12)
        (highlighted ? NSColor.controlAccentColor.withAlphaComponent(0.12) : NSColor.controlBackgroundColor).setFill()
        path.fill()
        (highlighted ? NSColor.controlAccentColor : NSColor.separatorColor).setStroke()
        path.lineWidth = highlighted ? 2 : 1
        path.stroke()
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        highlighted = true
        return .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { highlighted = false }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        highlighted = false
        guard let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] else { return false }
        accept?(urls)
        return true
    }
}

final class AppShell: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let initialPath: String?
    private var pendingFiles: [String] = []
    private var window: NSWindow!
    private var usage = NSTextField(wrappingLabelWithString: "Загружаю…")
    private var recent = NSStackView()
    private var warning = NSTextField(wrappingLabelWithString: "")
    private var processing: MeetingApp?
    private var settings: SettingsWindow?
    private var recentItems: [String: [String: Any]] = [:]
    private let home = FileManager.default.homeDirectoryForCurrentUser.path

    init(initialPath: String?) {
        self.initialPath = initialPath
        super.init()
    }

    var script: String {
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["SMALL_TRANSCRIBER_PIPELINE_SCRIPT"], !value.isEmpty { return value }
        #endif
        #if DEV
        return home + "/.local/share/gemini-meeting-pipeline/dev-managed/meeting_pipeline.py"
        #else
        return home + "/.local/bin/meeting_pipeline.py"
        #endif
    }

    var storageRoot: String {
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["SMALL_TRANSCRIBER_STORAGE_ROOT"], !value.isEmpty { return value }
        #endif
        #if DEV
        return home + "/Library/Application Support/Small Transcriber DEV"
        #else
        return home + "/Library/Application Support/Small Transcriber"
        #endif
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildWindow()
        installMenu()
        refresh()
        if let initialPath = initialPath, !pendingFiles.contains(initialPath) { pendingFiles.append(initialPath) }
        if !pendingFiles.isEmpty {
            let files = pendingFiles
            pendingFiles.removeAll()
            openFiles(files)
        }
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        guard !filenames.isEmpty else { sender.reply(toOpenOrPrint: .success); return }
        if window == nil { pendingFiles += filenames; sender.reply(toOpenOrPrint: .success); return }
        openFiles(filenames)
        sender.reply(toOpenOrPrint: .success)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        processing?.currentProcess == nil ? .terminateNow : .terminateCancel
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 520),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Small Transcriber"
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 480, height: 480)
        window.center()
        window.delegate = self
        let body = NSStackView()
        body.orientation = .vertical
        body.alignment = .leading
        body.spacing = 16
        body.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(body)
        NSLayoutConstraint.activate([
            body.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 24),
            body.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -24),
            body.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 22),
            body.bottomAnchor.constraint(lessThanOrEqualTo: window.contentView!.bottomAnchor, constant: -20)
        ])

        let heading = NSTextField(labelWithString: "Новая встреча")
        heading.font = .systemFont(ofSize: 22, weight: .semibold)
        body.addArrangedSubview(heading)

        let drop = AudioDropView(frame: .zero)
        drop.translatesAutoresizingMaskIntoConstraints = false
        drop.accept = { [weak self] urls in self?.openFiles(urls.map(\.path)) }
        let dropStack = NSStackView()
        dropStack.orientation = .vertical
        dropStack.alignment = .centerX
        dropStack.spacing = 14
        dropStack.translatesAutoresizingMaskIntoConstraints = false
        let prompt = NSTextField(labelWithString: "Перетащите аудиофайл сюда")
        prompt.font = .systemFont(ofSize: 16)
        dropStack.addArrangedSubview(prompt)
        let picker = NSButton(title: "Выбрать файл", target: self, action: #selector(chooseFile))
        dropStack.addArrangedSubview(picker)
        drop.addSubview(dropStack)
        NSLayoutConstraint.activate([
            dropStack.centerXAnchor.constraint(equalTo: drop.centerXAnchor),
            dropStack.centerYAnchor.constraint(equalTo: drop.centerYAnchor)
        ])
        body.addArrangedSubview(drop)
        drop.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true
        drop.heightAnchor.constraint(equalToConstant: 150).isActive = true

        let usageHeading = NSTextField(labelWithString: "Gemini сегодня")
        usageHeading.font = .systemFont(ofSize: 16, weight: .semibold)
        body.addArrangedSubview(usageHeading)
        usage.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
        body.addArrangedSubview(usage)
        usage.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true

        let recentHeading = NSTextField(labelWithString: "Недавние встречи")
        recentHeading.font = .systemFont(ofSize: 16, weight: .semibold)
        body.addArrangedSubview(recentHeading)
        recent.orientation = .vertical
        recent.alignment = .leading
        recent.spacing = 6
        body.addArrangedSubview(recent)
        recent.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true

        warning.textColor = .systemOrange
        warning.font = .systemFont(ofSize: 12)
        body.addArrangedSubview(warning)
        let settingsButton = NSButton(title: "Настройки…", target: self, action: #selector(openSettings))
        body.addArrangedSubview(settingsButton)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func installMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        let settingsItem = appMenu.addItem(withTitle: "Настройки…", action: #selector(openSettings), keyEquivalent: ",")
        settingsItem.target = self
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Выйти", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
        NSApp.mainMenu = main
    }

    @objc private func chooseFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = audioExtensions.sorted().compactMap { UTType(filenameExtension: $0) }
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url { openFiles([url.path]) }
    }

    private func openFiles(_ files: [String]) {
        guard files.count == 1 else { alert("Выберите один аудиофайл."); return }
        guard processing == nil else { alert("Дождитесь завершения текущей встречи и закройте окно обработки."); return }
        let url = URL(fileURLWithPath: files[0])
        guard audioExtensions.contains(url.pathExtension.lowercased()),
              (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true else {
            alert("Файл не найден или формат аудио не поддерживается.")
            return
        }
        launchProcessing(path: url.path)
    }

    private func launchProcessing(path: String,
                                  retry: (meetingID: String, transcriptionRunID: String)? = nil) {
        let controller = MeetingApp(inputPath: path, initialRetry: retry)
        controller.onUpdate = { [weak self] in self?.refresh() }
        controller.onClose = { [weak self] in
            DispatchQueue.main.async { [weak self] in
                self?.processing = nil
                self?.refresh()
                self?.window.makeKeyAndOrderFront(nil)
            }
        }
        processing = controller
        controller.start()
    }

    private func alert(_ message: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
    }

    func refresh() {
        runCommand(["status", "--limit", "5"]) { [weak self] result in
            guard let self = self else { return }
            guard let status = result as? [String: Any],
                  let snapshot = status["usage"] as? [String: Any],
                  let meetings = status["recent_meetings"] as? [[String: Any]] else {
                self.warning.stringValue = "Не удалось загрузить состояние встреч."
                return
            }
            self.warning.stringValue = status["config_warning"] as? String ?? ""
            let counts = snapshot["models"] as? [String: Int] ?? [:]
            let api = snapshot["observed_daily_limits"] as? [String: NSNumber] ?? [:]
            let manual = status["manual_limits"] as? [String: [String: Any]] ?? [:]
            var lines = counts.keys.sorted().map { model -> String in
                let observed = api[model]?.stringValue
                let configured = manual[model]?["rpd"] as? Int
                if let observed = observed, let configured = configured, observed != String(configured) {
                    return "\(model)  \(counts[model] ?? 0) использовано · API: \(observed) · настройка: \(configured)"
                }
                if let observed = observed { return "\(model)  \(counts[model] ?? 0) / \(observed) · лимит API" }
                if let configured = configured { return "\(model)  \(counts[model] ?? 0) / \(configured) · ручной лимит" }
                return "\(model)  \(counts[model] ?? 0) / ?"
            }
            if lines.isEmpty { lines = ["Запросов пока нет"] }
            lines += ["Загрузки  \(snapshot["upload"] as? Int ?? 0)", "Всего  \(snapshot["total"] as? Int ?? 0)"]
            self.usage.stringValue = lines.joined(separator: "\n")
            for view in self.recent.arrangedSubviews { self.recent.removeArrangedSubview(view); view.removeFromSuperview() }
            self.recentItems.removeAll()
            if meetings.isEmpty { self.recent.addArrangedSubview(NSTextField(labelWithString: "Встреч пока нет")) }
            for item in meetings {
                let title = item["title"] as? String ?? "Встреча"
                let date = String((item["created_at"] as? String ?? "").prefix(16)).replacingOccurrences(of: "T", with: " ")
                let status = item["status"] as? String ?? ""
                let label = status == "protocol_ready" ? "Протокол готов" : status == "transcript_ready" ? "Можно повторить протокол" : "Обработка не завершена"
                let button = NSButton(title: "\(title)  ·  \(date)  ·  \(label)", target: self, action: #selector(self.openRecent(_:)))
                button.bezelStyle = .rounded
                button.identifier = NSUserInterfaceItemIdentifier(item["meeting_id"] as? String ?? "")
                button.toolTip = item["protocol_path"] as? String
                if let id = item["meeting_id"] as? String { self.recentItems[id] = item }
                self.recent.addArrangedSubview(button)
            }
        }
    }

    @objc private func openRecent(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue, let item = recentItems[id] else { return }
        if let path = item["protocol_path"] as? String, FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
        } else if item["has_transcript"] as? Bool == true,
                  let meetingID = item["meeting_id"] as? String,
                  let transcriptionRunID = item["transcription_run_id"] as? String {
            guard processing == nil else { alert("Завершите текущую обработку."); return }
            launchProcessing(path: item["title"] as? String ?? "Встреча",
                             retry: (meetingID, transcriptionRunID))
        } else {
            alert("Для этой встречи протокол ещё не создан.")
        }
    }

    @objc private func openSettings() {
        if settings == nil { settings = SettingsWindow(shell: self) }
        settings?.show()
    }

    func runCommand(_ args: [String], input: Data? = nil, completion: @escaping (Any?) -> Void) {
        let script = self.script, root = storageRoot
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [script] + args + ["--storage-root", root]
            let output = Pipe(), error = Pipe()
            process.standardOutput = output
            process.standardError = error
            if let input = input {
                let stdin = Pipe()
                process.standardInput = stdin
                do { try process.run(); stdin.fileHandleForWriting.write(input); try? stdin.fileHandleForWriting.close() }
                catch { DispatchQueue.main.async { completion(nil) }; return }
            } else {
                do { try process.run() } catch { DispatchQueue.main.async { completion(nil) }; return }
            }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let value = process.terminationStatus == 0 ? try? JSONSerialization.jsonObject(with: data) : nil
            DispatchQueue.main.async { completion(value) }
        }
    }
}
