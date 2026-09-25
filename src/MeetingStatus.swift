import AppKit
import Foundation

final class MeetingApp: NSObject, NSApplicationDelegate, NSWindowDelegate {

    let inputPath: String

    var window: NSWindow!

    var phaseLabel: NSTextField!
    var statusLabel: NSTextField!
    var elapsedLabel: NSTextField!
    var fileLabel: NSTextField!
    var spinner: NSProgressIndicator!

    var openProtocolButton: NSButton!
    var openFolderButton: NSButton!
    var openLogButton: NSButton!
    var closeButton: NSButton!

    var startDate = Date()
    var elapsedTimer: Timer?

    var currentProcess: Process?
    var processExitCode: Int32?
    var stdoutFinished = false
    var stderrFinished = false
    var didFinish = false
    let stdoutLines = JSONLineBuffer()
    let events = ManagedPipelineEvents()
    var finalResult: ManagedPipelineResult?

    let fm = FileManager.default

    let home = FileManager.default.homeDirectoryForCurrentUser.path

    lazy var pipelineScript: String = {
        #if DEBUG
        if let override = ProcessInfo.processInfo.environment["SMALL_TRANSCRIBER_PIPELINE_SCRIPT"],
           !override.isEmpty { return override }
        #endif
        return home + "/.local/bin/meeting_pipeline.py"
    }()

    lazy var inputURL = URL(fileURLWithPath: inputPath)

    lazy var stem: String = {
        inputURL.deletingPathExtension().lastPathComponent
    }()


    init(inputPath: String) {
        self.inputPath = inputPath
        super.init()
    }


    func applicationDidFinishLaunching(
        _ notification: Notification
    ) {
        buildWindow()

        startDate = Date()

        elapsedTimer = Timer.scheduledTimer(
            withTimeInterval: 1.0,
            repeats: true
        ) { [weak self] _ in
            self?.updateElapsed()
        }

        startPipeline()
    }


    func applicationShouldTerminateAfterLastWindowClosed(
        _ sender: NSApplication
    ) -> Bool {
        return true
    }

    func applicationShouldTerminate(
        _ sender: NSApplication
    ) -> NSApplication.TerminateReply {
        return currentProcess == nil ? .terminateNow : .terminateCancel
    }


    func buildWindow() {

        window = NSWindow(
            contentRect: NSRect(
                x: 0,
                y: 0,
                width: 560,
                height: 330
            ),
            styleMask: [
                .titled,
                .closable,
                .miniaturizable
            ],
            backing: .buffered,
            defer: false
        )

        window.title = "Обработка встречи"
        window.center()
        window.delegate = self

        window.standardWindowButton(
            .closeButton
        )?.isEnabled = false

        let content = window.contentView!

        fileLabel = NSTextField(labelWithString: stem)
        fileLabel.font = NSFont.systemFont(
            ofSize: 15,
            weight: .semibold
        )
        fileLabel.lineBreakMode = .byTruncatingMiddle
        fileLabel.translatesAutoresizingMaskIntoConstraints = false

        phaseLabel = NSTextField(
            labelWithString: "Подготовка…"
        )
        phaseLabel.font = NSFont.systemFont(
            ofSize: 22,
            weight: .semibold
        )
        phaseLabel.translatesAutoresizingMaskIntoConstraints = false

        statusLabel = NSTextField(
            wrappingLabelWithString:
            "Подготавливаю обработку…"
        )
        statusLabel.font = NSFont.systemFont(ofSize: 14)
        statusLabel.maximumNumberOfLines = 3
        statusLabel.translatesAutoresizingMaskIntoConstraints = false

        elapsedLabel = NSTextField(
            labelWithString: "Прошло: 00:00"
        )
        elapsedLabel.textColor = .secondaryLabelColor
        elapsedLabel.translatesAutoresizingMaskIntoConstraints = false

        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.isIndeterminate = true
        spinner.translatesAutoresizingMaskIntoConstraints = false
        spinner.startAnimation(nil)

        openProtocolButton = NSButton(
            title: "Открыть протокол",
            target: self,
            action: #selector(openProtocol)
        )

        openFolderButton = NSButton(
            title: "Открыть папку",
            target: self,
            action: #selector(openFolder)
        )

        openLogButton = NSButton(
            title: "Открыть лог",
            target: self,
            action: #selector(openLog)
        )

        closeButton = NSButton(
            title: "Закрыть",
            target: self,
            action: #selector(closeApp)
        )

        for button in [
            openProtocolButton!,
            openFolderButton!,
            openLogButton!,
            closeButton!
        ] {
            button.translatesAutoresizingMaskIntoConstraints = false
            button.isHidden = true
        }

        let buttons = NSStackView(
            views: [
                openProtocolButton,
                openLogButton,
                openFolderButton,
                closeButton
            ]
        )

        buttons.orientation = .horizontal
        buttons.spacing = 10
        buttons.alignment = .centerY
        buttons.translatesAutoresizingMaskIntoConstraints = false

        content.addSubview(fileLabel)
        content.addSubview(phaseLabel)
        content.addSubview(statusLabel)
        content.addSubview(elapsedLabel)
        content.addSubview(spinner)
        content.addSubview(buttons)

        NSLayoutConstraint.activate([

            fileLabel.topAnchor.constraint(
                equalTo: content.topAnchor,
                constant: 28
            ),

            fileLabel.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 30
            ),

            fileLabel.trailingAnchor.constraint(
                equalTo: content.trailingAnchor,
                constant: -30
            ),

            spinner.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 30
            ),

            spinner.topAnchor.constraint(
                equalTo: fileLabel.bottomAnchor,
                constant: 30
            ),

            phaseLabel.leadingAnchor.constraint(
                equalTo: spinner.trailingAnchor,
                constant: 14
            ),

            phaseLabel.centerYAnchor.constraint(
                equalTo: spinner.centerYAnchor
            ),

            phaseLabel.trailingAnchor.constraint(
                lessThanOrEqualTo: content.trailingAnchor,
                constant: -30
            ),

            statusLabel.topAnchor.constraint(
                equalTo: phaseLabel.bottomAnchor,
                constant: 24
            ),

            statusLabel.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 30
            ),

            statusLabel.trailingAnchor.constraint(
                equalTo: content.trailingAnchor,
                constant: -30
            ),

            elapsedLabel.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 30
            ),

            elapsedLabel.bottomAnchor.constraint(
                equalTo: buttons.topAnchor,
                constant: -22
            ),

            buttons.trailingAnchor.constraint(
                equalTo: content.trailingAnchor,
                constant: -30
            ),

            buttons.bottomAnchor.constraint(
                equalTo: content.bottomAnchor,
                constant: -25
            )
        ])

        window.makeKeyAndOrderFront(nil)

        NSApp.activate(
            ignoringOtherApps: true
        )
    }


    func updateElapsed() {

        let seconds = Int(
            Date().timeIntervalSince(startDate)
        )

        let hours = seconds / 3600
        let minutes = (seconds % 3600) / 60
        let secs = seconds % 60

        if hours > 0 {
            elapsedLabel.stringValue = String(
                format: "Прошло: %02d:%02d:%02d",
                hours,
                minutes,
                secs
            )
        } else {
            elapsedLabel.stringValue = String(
                format: "Прошло: %02d:%02d",
                minutes,
                secs
            )
        }
    }


    func startPipeline() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        var arguments = [pipelineScript, "run", inputPath]
        #if DEBUG
        if let root = ProcessInfo.processInfo.environment["SMALL_TRANSCRIBER_STORAGE_ROOT"],
           !root.isEmpty {
            arguments += ["--storage-root", root]
        }
        #endif
        process.arguments = arguments

        let stdout = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdout
        process.standardError = stderrPipe
        currentProcess = process

        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                self?.processExitCode = finished.terminationReason == .exit
                    ? finished.terminationStatus : -1
                self?.finishIfReady()
            }
        }

        do {
            try process.run()
        } catch {
            currentProcess = nil
            try? stdout.fileHandleForWriting.close()
            try? stderrPipe.fileHandleForWriting.close()
            finishError("Не удалось запустить обработку встречи.", logPath: nil)
            return
        }
        try? stdout.fileHandleForWriting.close()
        try? stderrPipe.fileHandleForWriting.close()

        DispatchQueue.global(qos: .utility).async { [weak self] in
            while true {
                let chunk = stdout.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                DispatchQueue.main.async { self?.receiveStdout(chunk) }
            }
            DispatchQueue.main.async {
                self?.flushStdout()
                self?.stdoutFinished = true
                self?.finishIfReady()
            }
        }

        // Drain diagnostics separately; child logs are provided by the orchestrator.
        DispatchQueue.global(qos: .utility).async {
            while !stderrPipe.fileHandleForReading.availableData.isEmpty {}
            DispatchQueue.main.async { [weak self] in
                self?.stderrFinished = true
                self?.finishIfReady()
            }
        }
    }

    func receiveStdout(_ chunk: Data) {
        for line in stdoutLines.append(chunk) {
            applyEvent(line)
        }
    }

    func flushStdout() {
        for line in stdoutLines.finish() {
            applyEvent(line)
        }
    }

    func applyEvent(_ line: String) {
        switch events.consume(line) {
        case .started:
            statusLabel.stringValue = "Подготавливаю обработку…"
        case .transcriptionStarted:
            phaseLabel.stringValue = "Этап 1 из 2 — Расшифровка"
            statusLabel.stringValue = "Подготавливаю аудио…"
        case .transcriptionSucceeded:
            statusLabel.stringValue = "Расшифровка создана."
        case .analysisStarted:
            phaseLabel.stringValue = "Этап 2 из 2 — Создание протокола"
            statusLabel.stringValue = "Подготавливаю протокол…"
        case .analysisSucceeded:
            statusLabel.stringValue = "Протокол создан."
        case .failed(let message):
            statusLabel.stringValue = message
        case .successReceived, .ignored:
            break
        case .malformed:
            fputs("Ignored malformed pipeline event\n", stderr)
        }
    }

    func finishIfReady() {
        guard !didFinish, let exitCode = processExitCode, stdoutFinished, stderrFinished else { return }
        didFinish = true
        processExitCode = nil
        currentProcess = nil
        switch events.complete(exitCode: exitCode, files: fm) {
        case .success(let result):
            finalResult = result
            finishSuccess()
        case .failure(let message):
            finishError(message, logPath: events.currentLogPath)
        }
    }


    func finishSuccess() {

        elapsedTimer?.invalidate()

        spinner.stopAnimation(nil)
        spinner.isHidden = true

        phaseLabel.stringValue = "Готово"

        statusLabel.stringValue =
            "Расшифровка и протокол успешно созданы."

        openProtocolButton.isHidden = false
        openFolderButton.isHidden = false
        closeButton.isHidden = false
        openLogButton.isHidden = true

        window.standardWindowButton(
            .closeButton
        )?.isEnabled = true

        NSApp.requestUserAttention(
            .informationalRequest
        )

        NSApp.activate(
            ignoringOtherApps: true
        )
    }


    func finishError(
        _ message: String,
        logPath: String?
    ) {

        elapsedTimer?.invalidate()

        spinner.stopAnimation(nil)
        spinner.isHidden = true

        phaseLabel.stringValue = "Не удалось завершить"

        statusLabel.stringValue = message

        openProtocolButton.isHidden = true
        var isDirectory: ObjCBool = false
        openFolderButton.isHidden = !(events.meetingDir.map {
            fm.fileExists(atPath: $0, isDirectory: &isDirectory) && isDirectory.boolValue
        } ?? false)
        closeButton.isHidden = false

        openLogButton.isHidden =
            logPath.map { !fm.fileExists(atPath: $0) } ?? true

        window.standardWindowButton(
            .closeButton
        )?.isEnabled = true

        NSApp.requestUserAttention(
            .criticalRequest
        )

        NSApp.activate(
            ignoringOtherApps: true
        )
    }


    @objc func openProtocol() {
        guard let path = finalResult?.activeHTMLPath,
              let attributes = try? fm.attributesOfItem(atPath: path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              let size = attributes[.size] as? NSNumber,
              size.intValue > 0 else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }


    @objc func openFolder() {
        guard let path = finalResult?.meetingDir ?? events.meetingDir else { return }
        var isDirectory: ObjCBool = false
        guard fm.fileExists(atPath: path, isDirectory: &isDirectory),
              isDirectory.boolValue else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }


    @objc func openLog() {

        guard
            let path = events.currentLogPath
        else {
            return
        }
        guard fm.fileExists(atPath: path) else { return }

        NSWorkspace.shared.open(
            URL(fileURLWithPath: path)
        )
    }


    @objc func closeApp() {
        NSApp.terminate(nil)
    }


    func windowWillClose(
        _ notification: Notification
    ) {
        if currentProcess == nil {
            NSApp.terminate(nil)
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        return currentProcess == nil
    }
}


@main
struct MeetingMain {
    static func main() {
        guard CommandLine.arguments.count >= 2 else {
            fputs("Usage: gemini_meeting_gui <audio-file>\n", stderr)
            exit(2)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let controller = MeetingApp(inputPath: CommandLine.arguments[1])
        app.delegate = controller
        app.run()
    }
}
