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
    var stageLabels = [NSTextField]()
    var stageDetails = [NSTextField]()
    var usageStack: NSStackView!
    var renderedUsage = ""

    var openProtocolButton: NSButton!
    var openFolderButton: NSButton!
    var openLogButton: NSButton!
    var retryButton: NSButton!
    var closeButton: NSButton!

    var startDate = Date()
    var elapsedTimer: Timer?

    var currentProcess: Process?
    var processExitCode: Int32?
    var stdoutFinished = false
    var stderrFinished = false
    var didFinish = false
    var stdoutLines = JSONLineBuffer()
    var events = ManagedPipelineEvents()
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
                width: 780,
                height: 390
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
            labelWithString: "Этапы"
        )
        phaseLabel.font = NSFont.systemFont(
            ofSize: 17,
            weight: .semibold
        )
        phaseLabel.translatesAutoresizingMaskIntoConstraints = false

        statusLabel = NSTextField(
            wrappingLabelWithString:
            "Подготовка…"
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
            title: "Показать в Finder",
            target: self,
            action: #selector(openFolder)
        )

        retryButton = NSButton(
            title: "Повторить протокол",
            target: self,
            action: #selector(retryAnalysis)
        )
        retryButton.controlSize = .small

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
            retryButton!,
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

        let stageStack = NSStackView()
        stageStack.orientation = .vertical
        stageStack.alignment = .leading
        stageStack.spacing = 8
        stageStack.translatesAutoresizingMaskIntoConstraints = false
        for index in ManagedPipelineEvents.stages.indices {
            let label = NSTextField(labelWithString: events.stageLine(index))
            label.font = NSFont.systemFont(ofSize: 14, weight: .medium)
            let detail = NSTextField(wrappingLabelWithString: "")
            detail.font = NSFont.systemFont(ofSize: 12)
            detail.textColor = .secondaryLabelColor
            detail.isHidden = true
            stageLabels.append(label)
            stageDetails.append(detail)
            let row = NSStackView(views: index == 3 ? [label, retryButton] : [label])
            row.orientation = .horizontal
            row.alignment = .centerY
            row.translatesAutoresizingMaskIntoConstraints = false
            let group = NSStackView(views: [row, detail])
            group.orientation = .vertical
            group.alignment = .leading
            group.spacing = 2
            group.translatesAutoresizingMaskIntoConstraints = false
            stageStack.addArrangedSubview(group)
            group.widthAnchor.constraint(equalTo: stageStack.widthAnchor).isActive = true
            row.widthAnchor.constraint(equalTo: group.widthAnchor).isActive = true
            detail.widthAnchor.constraint(equalTo: group.widthAnchor, constant: -20).isActive = true
            if index == 3 {
                retryButton.setContentHuggingPriority(.required, for: .horizontal)
            }
        }

        let usageCard = NSView()
        usageCard.wantsLayer = true
        usageCard.layer?.cornerRadius = 10
        usageCard.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        usageCard.layer?.borderColor = NSColor.separatorColor.cgColor
        usageCard.layer?.borderWidth = 1
        usageCard.translatesAutoresizingMaskIntoConstraints = false
        usageStack = NSStackView()
        usageStack.orientation = .vertical
        usageStack.alignment = .leading
        usageStack.spacing = 8
        usageStack.translatesAutoresizingMaskIntoConstraints = false
        usageCard.addSubview(usageStack)

        content.addSubview(fileLabel)
        content.addSubview(phaseLabel)
        content.addSubview(statusLabel)
        content.addSubview(elapsedLabel)
        content.addSubview(spinner)
        content.addSubview(buttons)
        content.addSubview(stageStack)
        content.addSubview(usageCard)

        NSLayoutConstraint.activate([

            fileLabel.topAnchor.constraint(
                equalTo: content.topAnchor,
                constant: 22
            ),

            fileLabel.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 24
            ),

            fileLabel.trailingAnchor.constraint(
                equalTo: content.trailingAnchor,
                constant: -24
            ),

            spinner.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 24
            ),

            spinner.topAnchor.constraint(
                equalTo: fileLabel.bottomAnchor,
                constant: 15
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
                constant: -24
            ),

            stageStack.topAnchor.constraint(
                equalTo: phaseLabel.bottomAnchor,
                constant: 13
            ),
            stageStack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stageStack.trailingAnchor.constraint(equalTo: usageCard.leadingAnchor, constant: -18),

            statusLabel.topAnchor.constraint(
                equalTo: stageStack.bottomAnchor,
                constant: 11
            ),

            statusLabel.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 24
            ),

            statusLabel.trailingAnchor.constraint(equalTo: stageStack.trailingAnchor),

            usageCard.topAnchor.constraint(equalTo: phaseLabel.topAnchor),
            usageCard.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            usageCard.widthAnchor.constraint(equalToConstant: 265),
            usageCard.bottomAnchor.constraint(lessThanOrEqualTo: elapsedLabel.topAnchor, constant: -14),
            usageStack.topAnchor.constraint(equalTo: usageCard.topAnchor, constant: 14),
            usageStack.bottomAnchor.constraint(equalTo: usageCard.bottomAnchor, constant: -14),
            usageStack.leadingAnchor.constraint(equalTo: usageCard.leadingAnchor, constant: 14),
            usageStack.trailingAnchor.constraint(equalTo: usageCard.trailingAnchor, constant: -14),

            elapsedLabel.leadingAnchor.constraint(
                equalTo: content.leadingAnchor,
                constant: 24
            ),
            elapsedLabel.topAnchor.constraint(greaterThanOrEqualTo: statusLabel.bottomAnchor, constant: 14),

            elapsedLabel.bottomAnchor.constraint(
                equalTo: buttons.topAnchor,
                constant: -5
            ),

            buttons.trailingAnchor.constraint(
                equalTo: content.trailingAnchor,
                constant: -24
            ),

            buttons.bottomAnchor.constraint(
                equalTo: content.bottomAnchor,
                constant: -18
            )
        ])

        renderUsage()

        window.makeKeyAndOrderFront(nil)

        NSApp.activate(
            ignoringOtherApps: true
        )
    }


    func updateElapsed() {
        renderStages()

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


    func startPipeline(retry: (meetingID: String, transcriptionRunID: String)? = nil) {
        guard currentProcess == nil else { return }
        if retry != nil {
            events = ManagedPipelineEvents()
            stdoutLines = JSONLineBuffer()
            processExitCode = nil
            stdoutFinished = false
            stderrFinished = false
            didFinish = false
            finalResult = nil
            retryButton.isEnabled = false
            retryButton.isHidden = true
            openFolderButton.isHidden = true
            openLogButton.isHidden = true
            closeButton.isHidden = true
            spinner.isHidden = false
            spinner.startAnimation(nil)
            phaseLabel.stringValue = "Создание протокола"
            statusLabel.stringValue = "Повторяю создание протокола…"
            startDate = Date()
            elapsedTimer?.invalidate()
            elapsedTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) {
                [weak self] _ in self?.updateElapsed()
            }
        }
        renderStages()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        var arguments = retry.map {
            [pipelineScript, "retry-analysis", "--meeting-id", $0.meetingID,
             "--transcription-run-id", $0.transcriptionRunID]
        } ?? [pipelineScript, "run", inputPath]
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
            if events.operation == "analysis_retry" {
                phaseLabel.stringValue = "Создание протокола"
                statusLabel.stringValue = "Подготавливаю повторный анализ…"
            } else {
                statusLabel.stringValue = "Подготавливаю обработку…"
            }
        case .transcriptionStarted:
            phaseLabel.stringValue = "Этап 1 из 2 — Расшифровка"
            statusLabel.stringValue = "Подготавливаю аудио…"
        case .transcriptionSucceeded:
            statusLabel.stringValue = "Расшифровка создана."
        case .analysisStarted:
            phaseLabel.stringValue = events.operation == "analysis_retry"
                ? "Создание протокола" : "Этап 2 из 2 — Создание протокола"
            statusLabel.stringValue = "Подготавливаю протокол…"
        case .analysisSucceeded:
            statusLabel.stringValue = "Протокол создан."
        case .failed(let message):
            statusLabel.stringValue = message
        case .successReceived, .ignored:
            break
        case .stateChanged:
            break
        case .malformed:
            fputs("Ignored malformed pipeline event\n", stderr)
        }
        renderStages()
    }

    func renderStages() {
        guard !stageLabels.isEmpty else { return }
        phaseLabel.stringValue = "Этапы"
        for index in ManagedPipelineEvents.stages.indices {
            let key = ManagedPipelineEvents.stages[index]
            stageLabels[index].stringValue = events.stageLine(index)
            let state = events.stageStates[key] ?? "pending"
            stageLabels[index].textColor = state == "running" ? .controlAccentColor : .labelColor
            let daily = state == "failed" && key == "protocol_generation" &&
                events.errorCode == "daily_quota_exhausted"
            stageDetails[index].isHidden = state != "running" && !daily
            stageDetails[index].stringValue = daily
                ? "Дневной лимит Gemini для этой модели исчерпан."
                : state == "running" ? events.displayLiveMessage : ""
        }
        renderUsage()
    }

    func renderUsage() {
        let snapshot = events.usageText
        guard snapshot != renderedUsage else { return }
        renderedUsage = snapshot
        for view in usageStack.arrangedSubviews {
            usageStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        let title = NSTextField(labelWithString: "Gemini сегодня")
        title.font = NSFont.systemFont(ofSize: 14, weight: .semibold)
        usageStack.addArrangedSubview(title)
        for (index, entry) in events.usageRows.enumerated() {
            if entry.0 == "Загрузки" {
                let gap = NSView()
                usageStack.addArrangedSubview(gap)
                gap.heightAnchor.constraint(equalToConstant: 4).isActive = true
            }
            if index == events.usageRows.count - 1 {
                let rule = NSView()
                rule.wantsLayer = true
                rule.layer?.backgroundColor = NSColor.separatorColor.cgColor
                usageStack.addArrangedSubview(rule)
                rule.widthAnchor.constraint(equalTo: usageStack.widthAnchor).isActive = true
                rule.heightAnchor.constraint(equalToConstant: 1).isActive = true
            }
            let name = NSTextField(labelWithString: entry.0)
            let weight: NSFont.Weight = index == events.usageRows.count - 1 ? .semibold : .regular
            name.font = NSFont.systemFont(ofSize: 12, weight: weight)
            let value: NSView
            if entry.1.hasSuffix(" / ?") {
                let amount = NSTextField(labelWithString: String(entry.1.dropLast()).trimmingCharacters(in: .whitespaces))
                let unknown = NSTextField(labelWithString: "?")
                amount.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: weight)
                unknown.font = amount.font
                unknown.toolTip = "Официальный лимит для этой модели пока не известен приложению."
                let pair = NSStackView(views: [amount, unknown])
                pair.orientation = .horizontal
                pair.spacing = 3
                value = pair
            } else {
                let known = NSTextField(labelWithString: entry.1)
                known.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: weight)
                value = known
            }
            value.setContentHuggingPriority(.required, for: .horizontal)
            let spacer = NSView()
            let row = NSStackView(views: [name, spacer, value])
            row.orientation = .horizontal
            row.spacing = 4
            usageStack.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: usageStack.widthAnchor).isActive = true
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
        renderStages()

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

        phaseLabel.stringValue = events.errorCode == "gemini_overloaded"
            ? "Gemini временно перегружен" : "Не удалось завершить"

        statusLabel.stringValue = message
        renderStages()

        openProtocolButton.isHidden = true
        openFolderButton.isHidden = !isRevealable(events.transcriptPath)
        retryButton.isHidden = !events.canRetryAnalysis
        retryButton.isEnabled = events.canRetryAnalysis
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
        let path = finalResult?.activeHTMLPath ?? events.transcriptPath
        guard isRevealable(path), let path = path else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    func isRevealable(_ path: String?) -> Bool {
        guard let path = path,
              let attributes = try? fm.attributesOfItem(atPath: path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              let size = attributes[.size] as? NSNumber else { return false }
        return size.intValue > 0
    }

    @objc func retryAnalysis() {
        guard currentProcess == nil, events.canRetryAnalysis,
              let meetingID = events.meetingID,
              let transcriptionRunID = events.transcriptionRunID else { return }
        retryButton.isEnabled = false
        startPipeline(retry: (meetingID, transcriptionRunID))
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
