import Foundation

final class JSONLineBuffer {
    private var pending = Data()
    private var droppingOversizedLine = false
    private let maximumLineBytes = 65_536

    func append(_ chunk: Data) -> [String] {
        pending.append(chunk)
        var lines: [String] = []
        while let newline = pending.firstIndex(of: 10) {
            let record = Data(pending[..<newline])
            pending.removeSubrange(...newline)
            if !droppingOversizedLine, record.count <= maximumLineBytes,
               let line = String(data: record, encoding: .utf8) {
                lines.append(line)
            }
            droppingOversizedLine = false
        }
        if pending.count > maximumLineBytes {
            pending.removeAll()
            droppingOversizedLine = true
        }
        return lines
    }

    func finish() -> [String] {
        defer {
            pending.removeAll()
            droppingOversizedLine = false
        }
        guard !droppingOversizedLine, !pending.isEmpty,
              let line = String(data: pending, encoding: .utf8) else { return [] }
        return [line]
    }
}

struct ManagedPipelineResult {
    let meetingID: String
    let transcriptionRunID: String
    let analysisRunID: String
    let transcriptPath: String
    let activeJSONPath: String
    let activeHTMLPath: String
    let meetingDir: String
    let transcribeLogPath: String?
    let protocolLogPath: String?

    init?(_ values: [String: Any]) {
        guard let meetingID = values["meeting_id"] as? String, !meetingID.isEmpty,
              let transcriptionRunID = values["transcription_run_id"] as? String, !transcriptionRunID.isEmpty,
              let analysisRunID = values["analysis_run_id"] as? String, !analysisRunID.isEmpty,
              let transcriptPath = values["transcript_path"] as? String, transcriptPath.hasPrefix("/"),
              let activeJSONPath = values["active_json_path"] as? String, activeJSONPath.hasPrefix("/"),
              let activeHTMLPath = values["active_html_path"] as? String, activeHTMLPath.hasPrefix("/"),
              let meetingDir = values["meeting_dir"] as? String, meetingDir.hasPrefix("/") else {
            return nil
        }
        self.meetingID = meetingID
        self.transcriptionRunID = transcriptionRunID
        self.analysisRunID = analysisRunID
        self.transcriptPath = transcriptPath
        self.activeJSONPath = activeJSONPath
        self.activeHTMLPath = activeHTMLPath
        self.meetingDir = meetingDir
        self.transcribeLogPath = values["transcribe_log_path"] as? String
        self.protocolLogPath = values["protocol_log_path"] as? String
    }
}

enum ManagedPipelineUpdate {
    case started
    case transcriptionStarted
    case transcriptionSucceeded
    case analysisStarted
    case analysisSucceeded
    case successReceived
    case failed(String)
    case ignored
    case malformed
}

enum ManagedPipelineCompletion {
    case success(ManagedPipelineResult)
    case failure(String)
}

final class ManagedPipelineEvents {
    private(set) var result: ManagedPipelineResult?
    private(set) var meetingDir: String?
    private(set) var currentLogPath: String?
    private(set) var failureMessage: String?
    private var terminalEventCount = 0

    func consume(_ line: String) -> ManagedPipelineUpdate {
        guard let data = line.data(using: .utf8),
              let values = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let event = values["event"] as? String else {
            return .malformed
        }
        switch event {
        case "pipeline_started":
            rememberPaths(values)
            return .started
        case "transcription_started":
            rememberPaths(values)
            return .transcriptionStarted
        case "transcription_succeeded":
            return .transcriptionSucceeded
        case "analysis_started":
            rememberPaths(values)
            return .analysisStarted
        case "analysis_succeeded":
            return .analysisSucceeded
        case "pipeline_succeeded":
            terminalEventCount += 1
            guard let parsed = ManagedPipelineResult(values) else { return .malformed }
            result = parsed
            meetingDir = parsed.meetingDir
            return .successReceived
        case "pipeline_failed":
            terminalEventCount += 1
            rememberPaths(values)
            let phase = values["phase"] as? String ?? "pipeline"
            let message: String
            switch phase {
            case "input": message = "Проверьте исходный аудиофайл."
            case "transcription": message = "Не удалось создать расшифровку."
            case "analysis": message = "Протокол создать не удалось."
            case "publication": message = "Не удалось сохранить результат."
            default: message = "Обработка встречи не завершилась."
            }
            failureMessage = message
            return .failed(message)
        default:
            return .ignored
        }
    }

    private func rememberPaths(_ values: [String: Any]) {
        if let path = values["meeting_dir"] as? String, path.hasPrefix("/") {
            meetingDir = path
        }
        if let path = values["current_log_path"] as? String, path.hasPrefix("/") {
            currentLogPath = path
        }
    }

    func complete(exitCode: Int32, files: FileManager = .default) -> ManagedPipelineCompletion {
        if let message = failureMessage { return .failure(message) }
        guard exitCode == 0, terminalEventCount == 1, let result = result else {
            return .failure("Обработка встречи не завершилась.")
        }
        var isDirectory: ObjCBool = false
        guard files.fileExists(atPath: result.meetingDir, isDirectory: &isDirectory),
              isDirectory.boolValue,
              let attributes = try? files.attributesOfItem(atPath: result.activeHTMLPath),
              attributes[.type] as? FileAttributeType == .typeRegular,
              let size = attributes[.size] as? NSNumber,
              size.intValue > 0 else {
            return .failure("Итоговый протокол или папка встречи не найдены.")
        }
        return .success(result)
    }
}
