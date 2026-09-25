import Foundation

@main
struct ManagedPipelineEventTests {
    static func main() throws {
        let fake = CommandLine.arguments[1]
        let guiSource = try String(contentsOfFile: CommandLine.arguments[2], encoding: .utf8)
        assert(guiSource.components(separatedBy: "Process()").count - 1 == 1)
        assert(!guiSource.contains("gemini_transcribe_meeting.py"))
        assert(!guiSource.contains("gemini_make_protocol.py"))
        assert(guiSource.contains("finalResult?.activeHTMLPath"))
        assert(guiSource.contains("activateFileViewerSelecting"))
        assert(guiSource.contains("finalResult?.activeHTMLPath ?? events.transcriptPath"))
        assert(!guiSource.contains("fm.fileExists(atPath: $0, isDirectory:"))
        assert(guiSource.contains("retry-analysis"))
        assert(guiSource.contains("--transcription-run-id"))
        assert(guiSource.contains("guard currentProcess == nil else { return }"))
        assert(guiSource.contains("retryButton.isEnabled = false"))
        assert(guiSource.contains("events = ManagedPipelineEvents()"))
        assert(guiSource.contains("retryButton.isHidden = !events.canRetryAnalysis"))
        assert(guiSource.contains("openFolderButton.isHidden = !isRevealable(events.transcriptPath)"))
        assert(guiSource.contains("for index in ManagedPipelineEvents.stages.indices"))
        assert(guiSource.contains("stageDetails[index].stringValue"))
        let root = URL(fileURLWithPath: "/private/tmp")
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let state = ManagedPipelineEvents()
        assert(isStarted(state.consume(#"{"event":"pipeline_started"}"#)))
        assert(isStageOne(state.consume(#"{"event":"transcription_started"}"#)))
        assert(isStageTwo(state.consume(#"{"event":"analysis_started"}"#)))
        assert(isIgnored(state.consume(#"{"event":"future_event","meeting_dir":"/unexpected"}"#)))
        assert(state.meetingDir == nil)
        assert(isMalformed(state.consume("{broken")))
        let visual = ManagedPipelineEvents()
        assert(ManagedPipelineEvents.stages.count == 5)
        assert(visual.stageLine(0).hasPrefix("○"))
        assert(isChanged(visual.consume(#"{"event":"stage_started","stage":"transcription"}"#)))
        assert(visual.stageLine(0).hasPrefix("●"))
        _ = visual.consume(#"{"event":"stage_progress","stage":"transcription","message_code":"chunk","safe_message":"Фрагмент 1 из 3"}"#)
        assert(visual.liveMessage == "Фрагмент 1 из 3")
        _ = visual.consume(#"{"event":"stage_succeeded","stage":"transcription"}"#)
        assert(visual.stageLine(0).hasPrefix("✓"))
        _ = visual.consume(#"{"event":"stage_warning","stage":"speaker_normalization"}"#)
        assert(visual.stageLine(1).hasPrefix("⚠"))
        _ = visual.consume(#"{"event":"stage_failed","stage":"protocol_generation"}"#)
        assert(visual.stageLine(3).hasPrefix("✕"))
        _ = visual.consume(#"{"event":"usage_updated","quota_date":"2026-09-25","models":{"gemini-2.5-flash":2,"gemini-2.0-flash":1,"gemini-3.5-transcribe":4},"total":7,"upload":1}"#)
        assert(visual.usageText.contains("Gemini 2.5 Flash   2 / ?"))
        assert(visual.usageText.contains("Gemini 2.0 Flash   1 / ?"))
        assert(visual.usageText.contains("Transcribe   4 / ?"))
        assert(visual.usageText.contains("Загрузки   1"))
        assert(visual.usageText.contains("Всего   7"))
        assert(!visual.usageText.contains("gemini-2.5-flash"))
        assert(!visual.usageText.contains("Upload"))
        assert(!visual.usageText.contains("generate-content"))
        _ = visual.consume(#"{"event":"usage_updated","models":{"gemini-2.5-flash":20},"total":20,"observed_daily_limits":{"gemini-2.5-flash":20}}"#)
        assert(visual.usageText.contains("Gemini 2.5 Flash   20 / 20"))
        assert(!visual.usageText.contains("20 / ?"))

        let split = JSONLineBuffer()
        assert(split.append(Data(#"{"event":"pipeline_"#.utf8)).isEmpty)
        assert(split.append(Data("started\"}\n".utf8)).count == 1)

        let multiple = JSONLineBuffer()
        let joined = #"{"event":"pipeline_started"}"# + "\n" +
                     #"{"event":"analysis_started"}"# + "\n"
        assert(multiple.append(Data(joined.utf8)).count == 2)
        assert(multiple.finish().isEmpty)

        let utf8 = JSONLineBuffer()
        let unicode = Data((#"{"event":"pipeline_started","note":"тест"}"# + "\n").utf8)
        let boundary = unicode.firstIndex(of: 0xD1)!
        assert(utf8.append(Data(unicode[..<(boundary + 1)])).isEmpty)
        assert(utf8.append(Data(unicode[(boundary + 1)...])).count == 1)

        let noNewline = JSONLineBuffer()
        assert(noNewline.append(Data(#"{"event":"pipeline_started"}"#.utf8)).isEmpty)
        assert(noNewline.finish().count == 1)

        let terminalRoot = root.appendingPathComponent("terminal")
        try FileManager.default.createDirectory(at: terminalRoot, withIntermediateDirectories: true)
        let html = terminalRoot.appendingPathComponent("result.html")
        try "synthetic".write(to: html, atomically: true, encoding: .utf8)
        let finalValues: [String: Any] = [
            "event": "pipeline_succeeded", "meeting_id": "m", "transcription_run_id": "t",
            "analysis_run_id": "a", "transcript_path": terminalRoot.path + "/transcript.txt",
            "active_json_path": terminalRoot.path + "/result.json",
            "active_html_path": html.path, "meeting_dir": terminalRoot.path
        ]
        let finalLine = String(data: try JSONSerialization.data(withJSONObject: finalValues), encoding: .utf8)!
        let failedLine = #"{"event":"pipeline_failed","phase":"analysis"}"#
        let early = ManagedPipelineEvents()
        assert(isFailure(early.complete(exitCode: 0)))
        _ = early.consume(finalLine)
        assert(isSuccess(early.complete(exitCode: 0)))
        let duplicate = ManagedPipelineEvents()
        _ = duplicate.consume(finalLine)
        _ = duplicate.consume(finalLine)
        assert(isFailure(duplicate.complete(exitCode: 0)))
        let successThenFailure = ManagedPipelineEvents()
        _ = successThenFailure.consume(finalLine)
        _ = successThenFailure.consume(failedLine)
        assert(isFailure(successThenFailure.complete(exitCode: 0)))
        let retryEligible = ManagedPipelineEvents()
        _ = retryEligible.consume(#"{"event":"pipeline_started","meeting_id":"m"}"#)
        _ = retryEligible.consume(#"{"event":"transcription_succeeded","transcription_run_id":"t","transcript_path":"/tmp/synthetic.txt"}"#)
        _ = retryEligible.consume(#"{"event":"pipeline_failed","phase":"analysis","error_code":"gemini_overloaded","raw_body":"secret"}"#)
        assert(retryEligible.canRetryAnalysis)
        assert(retryEligible.failureMessage?.contains("Gemini") == true)
        assert(retryEligible.failureMessage?.contains("secret") == false)
        let daily = ManagedPipelineEvents()
        _ = daily.consume(#"{"event":"pipeline_started","meeting_id":"m","transcription_run_id":"t"}"#)
        _ = daily.consume(#"{"event":"transcription_succeeded","transcript_path":"/tmp/synthetic.txt"}"#)
        _ = daily.consume(#"{"event":"pipeline_failed","phase":"analysis","error_code":"daily_quota_exhausted"}"#)
        assert(!daily.canRetryAnalysis)
        assert(daily.failureMessage?.contains("Дневной лимит") == true)
        let noRetry = ManagedPipelineEvents()
        _ = noRetry.consume(#"{"event":"pipeline_started","meeting_id":"m","transcription_run_id":"t"}"#)
        _ = noRetry.consume(#"{"event":"pipeline_failed","phase":"transcription"}"#)
        assert(!noRetry.canRetryAnalysis)
        let storageFailure = ManagedPipelineEvents()
        _ = storageFailure.consume(#"{"event":"pipeline_failed","phase":"storage","meeting_id":"m"}"#)
        assert(!storageFailure.canRetryAnalysis)
        let genericAnalysis = ManagedPipelineEvents()
        _ = genericAnalysis.consume(#"{"event":"pipeline_started","meeting_id":"m","transcription_run_id":"t"}"#)
        _ = genericAnalysis.consume(#"{"event":"transcription_succeeded","transcript_path":"/tmp/synthetic.txt"}"#)
        _ = genericAnalysis.consume(#"{"event":"pipeline_failed","phase":"analysis"}"#)
        assert(genericAnalysis.canRetryAnalysis)
        assert(genericAnalysis.failureMessage == "Протокол создать не удалось.")
        let failureThenSuccess = ManagedPipelineEvents()
        _ = failureThenSuccess.consume(failedLine)
        _ = failureThenSuccess.consume(finalLine)
        assert(isFailure(failureThenSuccess.complete(exitCode: 0)))
        let malformedBetween = ManagedPipelineEvents()
        _ = malformedBetween.consume(#"{"event":"pipeline_started"}"#)
        _ = malformedBetween.consume("{broken")
        _ = malformedBetween.consume(finalLine)
        assert(isSuccess(malformedBetween.complete(exitCode: 0)))

        for scenario in ["success", "stage_failure", "malformed", "missing_final",
                         "nonzero_after_success", "missing_html", "missing_meeting"] {
            let scenarioRoot = root.appendingPathComponent(scenario)
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [fake, "run", "synthetic.m4a", "--storage-root", scenarioRoot.path]
            var environment = ProcessInfo.processInfo.environment
            environment["FAKE_PIPELINE_SCENARIO"] = scenario
            process.environment = environment
            let output = Pipe()
            process.standardOutput = output
            process.standardError = Pipe()
            try process.run()
            let text = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)!
            process.waitUntilExit()
            let parsed = ManagedPipelineEvents()
            var sawMalformed = false
            for line in text.split(separator: "\n") {
                if isMalformed(parsed.consume(String(line))) { sawMalformed = true }
            }
            if scenario == "malformed" { assert(sawMalformed) }
            if ["nonzero_after_success", "missing_html", "missing_meeting"].contains(scenario) {
                assert(parsed.result != nil)
            }
            let completion = parsed.complete(exitCode: process.terminationStatus)
            if scenario == "success" || scenario == "malformed" {
                guard case .success(let result) = completion else { fatalError(scenario) }
                assert(result.meetingID == "meeting-synthetic")
                assert(result.transcriptionRunID == "transcription-synthetic")
                assert(result.analysisRunID == "analysis-synthetic")
                assert(result.activeHTMLPath == scenarioRoot.path + "/synthetic-meeting/synthetic.html")
                assert(result.meetingDir == scenarioRoot.path + "/synthetic-meeting")
                assert(parsed.currentLogPath == scenarioRoot.path + "/synthetic-meeting/protocol.log")
            } else {
                guard case .failure = completion else { fatalError(scenario) }
                if scenario == "stage_failure" {
                    assert(parsed.failureMessage == "Протокол создать не удалось.")
                    assert(FileManager.default.fileExists(atPath: parsed.currentLogPath!))
                }
            }
        }
        for scenario in ["retry_success", "retry_overloaded", "retry_generic"] {
            let scenarioRoot = root.appendingPathComponent(scenario)
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [fake, "retry-analysis", "--meeting-id", "meeting-synthetic",
                                 "--transcription-run-id", "transcription-synthetic",
                                 "--storage-root", scenarioRoot.path]
            var environment = ProcessInfo.processInfo.environment
            environment["FAKE_PIPELINE_SCENARIO"] = scenario
            process.environment = environment
            let output = Pipe()
            process.standardOutput = output
            process.standardError = Pipe()
            try process.run()
            let text = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)!
            process.waitUntilExit()
            let parsed = ManagedPipelineEvents()
            var stages: [String] = []
            for line in text.split(separator: "\n") {
                let update = parsed.consume(String(line))
                if isStageOne(update) { stages.append("transcription") }
                if isStageTwo(update) { stages.append("analysis") }
            }
            assert(stages == ["analysis"])
            assert(parsed.operation == "analysis_retry")
            if scenario == "retry_success" {
                guard case .success(let result) = parsed.complete(exitCode: process.terminationStatus)
                    else { fatalError(scenario) }
                assert(result.activeHTMLPath == scenarioRoot.path + "/synthetic-meeting/synthetic.html")
            } else {
                assert(parsed.canRetryAnalysis)
                assert(isFailure(parsed.complete(exitCode: process.terminationStatus)))
                if scenario == "retry_overloaded" {
                    assert(parsed.errorCode == "gemini_overloaded")
                } else {
                    assert(parsed.failureMessage == "Протокол создать не удалось.")
                }
            }
        }
        print("Managed pipeline events: 10 synthetic scenarios OK")
    }

    static func isStarted(_ update: ManagedPipelineUpdate) -> Bool {
        if case .started = update { return true }; return false
    }
    static func isStageOne(_ update: ManagedPipelineUpdate) -> Bool {
        if case .transcriptionStarted = update { return true }; return false
    }
    static func isStageTwo(_ update: ManagedPipelineUpdate) -> Bool {
        if case .analysisStarted = update { return true }; return false
    }
    static func isIgnored(_ update: ManagedPipelineUpdate) -> Bool {
        if case .ignored = update { return true }; return false
    }
    static func isChanged(_ update: ManagedPipelineUpdate) -> Bool {
        if case .stateChanged = update { return true }; return false
    }
    static func isMalformed(_ update: ManagedPipelineUpdate) -> Bool {
        if case .malformed = update { return true }; return false
    }
    static func isSuccess(_ completion: ManagedPipelineCompletion) -> Bool {
        if case .success = completion { return true }; return false
    }
    static func isFailure(_ completion: ManagedPipelineCompletion) -> Bool {
        if case .failure = completion { return true }; return false
    }
}
