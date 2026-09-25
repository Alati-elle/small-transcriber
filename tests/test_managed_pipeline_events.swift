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
        assert(guiSource.contains("finalResult?.meetingDir ?? events.meetingDir"))
        let root = FileManager.default.temporaryDirectory
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
        print("Managed pipeline events: 7 synthetic scenarios OK")
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
