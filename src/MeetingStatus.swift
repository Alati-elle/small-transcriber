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
    var logTimer: Timer?

    var currentProcess: Process?
    var currentLogHandle: FileHandle?
    var currentLogPath: String?

    let fm = FileManager.default

    let home = FileManager.default.homeDirectoryForCurrentUser.path

    lazy var transcribeScript =
        home + "/.local/bin/gemini_transcribe_meeting.py"

    lazy var protocolScript =
        home + "/.local/bin/gemini_make_protocol.py"

    lazy var inputURL = URL(fileURLWithPath: inputPath)

    lazy var stem: String = {
        inputURL.deletingPathExtension().lastPathComponent
    }()

    lazy var inputDir: String = {
        inputURL.deletingLastPathComponent().path
    }()

    lazy var outputDir =
        inputDir + "/" + stem

    lazy var transcribeLog =
        outputDir + "/transcribe.log"

    lazy var protocolLog =
        outputDir + "/protocol.log"

    lazy var transcriptPath =
        outputDir + "/" + stem + "_ПОЛНАЯ_РАСШИФРОВКА.txt"

    lazy var protocolJSON =
        outputDir + "/" + stem + "_ПРОТОКОЛ.json"

    lazy var protocolHTML =
        outputDir + "/" + stem + "_ПРОТОКОЛ.html"


    init(inputPath: String) {
        self.inputPath = inputPath
        super.init()
    }


    func applicationDidFinishLaunching(
        _ notification: Notification
    ) {
        buildWindow()

        do {
            try fm.createDirectory(
                atPath: outputDir,
                withIntermediateDirectories: true
            )
        } catch {
            finishError(
                "Не удалось создать папку результатов.",
                logPath: nil
            )
            return
        }

        startDate = Date()

        elapsedTimer = Timer.scheduledTimer(
            withTimeInterval: 1.0,
            repeats: true
        ) { [weak self] _ in
            self?.updateElapsed()
        }

        startTranscription()
    }


    func applicationShouldTerminateAfterLastWindowClosed(
        _ sender: NSApplication
    ) -> Bool {
        return true
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


    func startTranscription() {

        phaseLabel.stringValue =
            "Этап 1 из 2 — Расшифровка"

        statusLabel.stringValue =
            "Подготавливаю аудио…"

        runPython(
            script: transcribeScript,
            arguments: [inputPath],
            logPath: transcribeLog
        ) { [weak self] exitCode in

            guard let self = self else { return }

            if exitCode != 0 {
                self.finishError(
                    "Не удалось создать расшифровку.",
                    logPath: self.transcribeLog
                )
                return
            }

            guard self.fileExistsAndNotEmpty(
                self.transcriptPath
            ) else {
                self.finishError(
                    "Скрипт завершился, но итоговая расшифровка не найдена.",
                    logPath: self.transcribeLog
                )
                return
            }

            self.startProtocol()
        }
    }


    func startProtocol() {

        phaseLabel.stringValue =
            "Этап 2 из 2 — Создание протокола"

        statusLabel.stringValue =
            "Подготавливаю расшифровку для Gemini…"

        try? fm.removeItem(atPath: protocolJSON)
        try? fm.removeItem(atPath: protocolHTML)

        runPython(
            script: protocolScript,
            arguments: [transcriptPath],
            logPath: protocolLog
        ) { [weak self] exitCode in

            guard let self = self else { return }

            if exitCode != 0 {
                self.finishError(
                    "Протокол создать не удалось.",
                    logPath: self.protocolLog
                )
                return
            }

            guard
                self.fileExistsAndNotEmpty(
                    self.protocolJSON
                ),
                self.fileExistsAndNotEmpty(
                    self.protocolHTML
                )
            else {
                self.finishError(
                    "Генератор завершился, но файлы протокола не найдены.",
                    logPath: self.protocolLog
                )
                return
            }

            self.finishSuccess()
        }
    }


    func runPython(
        script: String,
        arguments: [String],
        logPath: String,
        completion: @escaping (Int32) -> Void
    ) {

        currentLogPath = logPath

        fm.createFile(
            atPath: logPath,
            contents: Data()
        )

        guard let handle =
            FileHandle(forWritingAtPath: logPath)
        else {
            finishError(
                "Не удалось открыть лог для записи.",
                logPath: nil
            )
            return
        }

        currentLogHandle = handle

        let process = Process()
        process.executableURL =
            URL(fileURLWithPath: "/usr/bin/python3")

        process.arguments =
            [script] + arguments

        process.standardOutput = handle
        process.standardError = handle

        currentProcess = process

        startWatchingLog(logPath)

        process.terminationHandler = {
            [weak self] process in

            DispatchQueue.main.async {

                guard let self = self else { return }

                self.logTimer?.invalidate()
                self.logTimer = nil

                try? self.currentLogHandle?.close()
                self.currentLogHandle = nil
                self.currentProcess = nil

                self.updateStatusFromLog(logPath)

                completion(
                    process.terminationStatus
                )
            }
        }

        do {
            try process.run()
        } catch {
            try? handle.close()

            finishError(
                "Не удалось запустить Python: \(error.localizedDescription)",
                logPath: logPath
            )
        }
    }


    func startWatchingLog(_ path: String) {

        logTimer?.invalidate()

        logTimer = Timer.scheduledTimer(
            withTimeInterval: 0.7,
            repeats: true
        ) { [weak self] _ in
            self?.updateStatusFromLog(path)
        }
    }


    func updateStatusFromLog(_ path: String) {

        guard
            let data = fm.contents(atPath: path),
            let text = String(
                data: data,
                encoding: .utf8
            )
        else {
            return
        }

        let lines = text
            .components(separatedBy: .newlines)
            .filter {
                !$0.trimmingCharacters(
                    in: .whitespacesAndNewlines
                ).isEmpty
            }

        guard let raw = lines.last else {
            return
        }

        statusLabel.stringValue =
            humanStatus(raw)
    }


    func humanStatus(_ rawLine: String) -> String {

        var line = rawLine

        if line.hasPrefix("["),
           let end = line.firstIndex(of: "]") {
            line = String(
                line[line.index(after: end)...]
            )
            .trimmingCharacters(in: .whitespaces)
        }

        let lower = line.lowercased()

        if lower.contains("rate limit") ||
           lower.contains("429") {

            if let range =
                line.range(
                    of: #"через \d+ сек"#,
                    options: .regularExpression
                ) {

                return
                    "Достигнут временный лимит Gemini. Повтор " +
                    line[range]
            }

            return
                "Достигнут временный лимит Gemini. Жду повторную попытку…"
        }

        if lower.contains("503") {

            if let range =
                line.range(
                    of: #"через \d+ сек"#,
                    options: .regularExpression
                ) {

                return
                    "Gemini сейчас перегружен. Повтор " +
                    line[range]
            }

            if lower.contains("перехожу") ||
               lower.contains("fallback") {

                return
                    "Gemini перегружен. Переключаюсь на резервную модель…"
            }

            return
                "Gemini сейчас перегружен. Повторяю запрос…"
        }

        if lower.contains(
            "нормализация спикеров"
        ) {
            return
                "Проверяю, какие номера спикеров относятся к одним и тем же людям…"
        }

        if lower.contains(
            "определение имён"
        ) {
            return
                "Пытаюсь определить имена участников по тексту встречи…"
        }

        if lower.contains("gemini: батч") {
            return
                "Создаю структурированный протокол встречи…"
        }

        if lower.contains(
            "склеиваю части"
        ) {
            return
                "Склеиваю части расшифровки…"
        }

        if lower.contains(
            "загрузка в gemini"
        ) {
            return
                "Отправляю аудио в Gemini…"
        }

        if lower.contains(
            "json уже существует"
        ) {
            return
                "Использую сохранённый результат Gemini…"
        }

        if lower.contains(
            "протокол готов"
        ) {
            return
                "Протокол создан."
        }

        if lower.contains("готово") {
            return
                "Расшифровка создана."
        }

        if line.count > 180 {
            return String(line.prefix(177)) + "…"
        }

        return line
    }


    func finishSuccess() {

        elapsedTimer?.invalidate()
        logTimer?.invalidate()

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
        logTimer?.invalidate()

        currentLogPath = logPath

        spinner.stopAnimation(nil)
        spinner.isHidden = true

        phaseLabel.stringValue = "Не удалось завершить"

        var detail = message

        if
            let logPath = logPath,
            let lastError =
                extractLastError(logPath) {

            detail += "\n\n" + lastError
        }

        statusLabel.stringValue = detail

        openProtocolButton.isHidden = true
        openFolderButton.isHidden = false
        closeButton.isHidden = false

        openLogButton.isHidden =
            (logPath == nil)

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


    func extractLastError(
        _ path: String
    ) -> String? {

        guard
            let text = try? String(
                contentsOfFile: path,
                encoding: .utf8
            )
        else {
            return nil
        }

        let lines = text
            .components(separatedBy: .newlines)
            .filter {
                !$0.trimmingCharacters(
                    in: .whitespacesAndNewlines
                ).isEmpty
            }

        guard let last = lines.last else {
            return nil
        }

        if last.count > 240 {
            return String(last.prefix(237)) + "…"
        }

        return last
    }


    func fileExistsAndNotEmpty(
        _ path: String
    ) -> Bool {

        guard
            let attrs =
                try? fm.attributesOfItem(
                    atPath: path
                ),
            let size = attrs[.size] as? NSNumber
        else {
            return false
        }

        return size.intValue > 0
    }


    @objc func openProtocol() {
        NSWorkspace.shared.open(
            URL(fileURLWithPath: protocolHTML)
        )
    }


    @objc func openFolder() {
        NSWorkspace.shared.open(
            URL(fileURLWithPath: outputDir)
        )
    }


    @objc func openLog() {

        guard
            let path = currentLogPath
        else {
            return
        }

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
}


guard CommandLine.arguments.count >= 2 else {
    fputs(
        "Usage: gemini_meeting_gui <audio-file>\n",
        stderr
    )
    exit(2)
}

let inputPath = CommandLine.arguments[1]

let app = NSApplication.shared
app.setActivationPolicy(.regular)

let controller =
    MeetingApp(inputPath: inputPath)

app.delegate = controller
app.run()
