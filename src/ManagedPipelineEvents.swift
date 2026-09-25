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
    case stateChanged
}

enum ManagedPipelineCompletion {
    case success(ManagedPipelineResult)
    case failure(String)
}

final class ManagedPipelineEvents {
    static let stages = ["transcription", "speaker_normalization", "name_detection",
                         "protocol_generation", "finalization"]
    static let titles = ["Расшифровка аудио", "Нормализация спикеров", "Определение имён",
                         "Создание протокола", "Завершение"]
    private(set) var stageStates = [String: String]()
    private(set) var liveMessage = ""
    private var retryUntil: Date?
    private var retryReason = ""
    var displayLiveMessage: String {
        guard let until = retryUntil else { return liveMessage }
        let remaining = max(0, Int(ceil(until.timeIntervalSinceNow)))
        return "\(retryReason) · повтор через \(remaining) сек."
    }
    private(set) var usageModels = [String: Int]()
    private(set) var usageTotal = 0
    private(set) var usageUpload = 0
    private(set) var observedDailyLimits = [String: Double]()
    private(set) var quotaDate = ""
    private(set) var result: ManagedPipelineResult?
    private(set) var meetingDir: String?
    private(set) var currentLogPath: String?
    private(set) var failureMessage: String?
    private(set) var errorCode: String?
    private(set) var meetingID: String?
    private(set) var transcriptionRunID: String?
    private(set) var transcriptPath: String?
    private(set) var transcriptionSucceeded = false
    private(set) var canRetryAnalysis = false
    private(set) var operation = "full"
    private var terminalEventCount = 0
    private var lastProtocolQuotaKind: String?

    func consume(_ line: String) -> ManagedPipelineUpdate {
        guard let data = line.data(using: .utf8),
              let values = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let event = values["event"] as? String else {
            return .malformed
        }
        switch event {
        case "stage_started", "stage_succeeded", "stage_warning", "stage_failed", "stage_progress":
            guard let stage = values["stage"] as? String,
                  Self.stages.contains(stage) else { return .ignored }
            if event != "stage_progress" {
                stageStates[stage] = ["stage_started": "running", "stage_succeeded": "succeeded",
                                      "stage_warning": "warning", "stage_failed": "failed"][event]
            }
            if event == "stage_started" { retryUntil = nil }
            if let safe = values["safe_message"] as? String, safe.count <= 160 {
                liveMessage = safe
            } else if event == "stage_started" {
                liveMessage = values["message_code"] as? String == "validating_publication"
                    ? "Проверка и публикация результата…" : "Выполняется…"
            }
            return .stateChanged
        case "gemini_request_started", "gemini_request_retry", "gemini_request_failed":
            guard let stage = values["stage"] as? String, Self.stages.contains(stage) else {
                return .ignored
            }
            if let model = values["model"] as? String, model.count <= 80,
               let attempt = values["attempt"] as? Int, attempt > 0 {
                if event == "gemini_request_started" {
                    retryUntil = nil
                    liveMessage = "\(Self.modelTitle(model)) · запрос \(attempt)"
                } else if event == "gemini_request_failed" {
                    let kind = values["quota_kind"] as? String
                    if stage == "protocol_generation" { lastProtocolQuotaKind = kind }
                    switch kind {
                    case "rate_limit_rpm": liveMessage = "Слишком много запросов за короткое время."
                    case "rate_limit_tpm": liveMessage = "Превышен временный лимит объёма данных."
                    case "daily_quota_exhausted": liveMessage = "Дневной лимит Gemini исчерпан."
                    case "rate_limit_unknown": liveMessage = "Gemini временно ограничил запросы."
                    case "gemini_overloaded": liveMessage = "Gemini временно перегружен."
                    default: break
                    }
                } else if event == "gemini_request_retry",
                          let delay = values["retry_after_seconds"] as? Double, delay >= 0 {
                    retryUntil = Date().addingTimeInterval(delay)
                    switch values["quota_kind"] as? String {
                    case "rate_limit_rpm": retryReason = "429: слишком много запросов"
                    case "rate_limit_tpm": retryReason = "429: временный лимит объёма"
                    case "gemini_overloaded": retryReason = "503: модель перегружена"
                    default: retryReason = "Gemini временно ограничил запросы"
                    }
                }
            }
            return .stateChanged
        case "gemini_request_succeeded":
            if values["stage"] as? String == "protocol_generation" {
                lastProtocolQuotaKind = nil
            }
            return .stateChanged
        case "usage_updated":
            guard let models = values["models"] as? [String: Int],
                  let total = values["total"] as? Int, total >= 0 else { return .malformed }
            usageModels = models.filter { $0.key.count <= 80 && $0.value >= 0 }
            usageTotal = total
            usageUpload = values["upload"] as? Int ?? 0
            quotaDate = values["quota_date"] as? String ?? ""
            observedDailyLimits = values["observed_daily_limits"] as? [String: Double] ?? [:]
            return .stateChanged
        case "pipeline_started":
            operation = values["operation"] as? String ?? "full"
            rememberIdentity(values)
            if operation == "analysis_retry", transcriptPath != nil {
                transcriptionSucceeded = true
                stageStates["transcription"] = "succeeded"
            }
            rememberPaths(values)
            return .started
        case "transcription_started":
            rememberPaths(values)
            return .transcriptionStarted
        case "transcription_succeeded":
            rememberIdentity(values)
            transcriptionSucceeded = true
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
            transcriptPath = parsed.transcriptPath
            return .successReceived
        case "pipeline_failed":
            terminalEventCount += 1
            rememberPaths(values)
            rememberIdentity(values)
            let phase = values["phase"] as? String ?? "pipeline"
            errorCode = values["error_code"] as? String ?? lastProtocolQuotaKind
            canRetryAnalysis = transcriptionSucceeded && meetingID != nil &&
                transcriptionRunID != nil && transcriptPath != nil &&
                (phase == "analysis" || phase == "publication" || phase == "active") &&
                errorCode != "daily_quota_exhausted"
            let message: String
            switch (phase, errorCode) {
            case ("analysis", "gemini_overloaded"):
                message = "Расшифровка сохранена. Gemini сейчас перегружен. Повторите создание протокола позже — новая расшифровка не нужна."
            case ("analysis", "daily_quota_exhausted"):
                message = "Дневной лимит Gemini исчерпан. Расшифровка сохранена; повторная расшифровка не потребуется."
            case ("analysis", "rate_limit_rpm"):
                message = "Слишком много запросов за короткое время. Расшифровка сохранена."
            case ("analysis", "rate_limit_tpm"):
                message = "Превышен временный лимит объёма данных. Расшифровка сохранена."
            case ("analysis", "rate_limit_unknown"):
                message = "Gemini временно ограничил запросы. Расшифровка сохранена."
            case ("input", _): message = "Проверьте исходный аудиофайл."
            case ("transcription", _): message = "Не удалось создать расшифровку."
            case ("analysis", _): message = "Протокол создать не удалось."
            case ("publication", _): message = "Не удалось сохранить результат."
            default: message = "Обработка встречи не завершилась."
            }
            failureMessage = message
            return .failed(message)
        default:
            return .ignored
        }
    }

    func stageLine(_ index: Int) -> String {
        let key = Self.stages[index]
        let marks = ["running": "●", "succeeded": "✓", "warning": "⚠", "failed": "✕"]
        let mark = marks[stageStates[key] ?? "pending"] ?? "○"
        return "\(mark)  \(Self.titles[index])"
    }

    static func modelTitle(_ model: String) -> String {
        let parts = model.replacingOccurrences(of: "gemini-", with: "").split(separator: "-")
        if parts.contains("transcribe") { return "Transcribe" }
        return "Gemini " + parts.map { $0.capitalized }.joined(separator: " ")
    }

    var usageRows: [(String, String)] {
        var rows = usageModels.keys.sorted { left, right in
            let a = Self.modelTitle(left)
            let b = Self.modelTitle(right)
            if a == "Transcribe" { return false }
            if b == "Transcribe" { return true }
            return a > b
        }.map { key -> (String, String) in
            let count = usageModels[key] ?? 0
            let value = observedDailyLimits[key].map { "\(count) / \(Int($0))" } ?? "\(count) / ?"
            return (Self.modelTitle(key), value)
        }
        if usageUpload > 0 { rows.append(("Загрузки", "\(usageUpload)")) }
        rows.append(("Всего", "\(usageTotal)"))
        return rows
    }

    var usageText: String {
        (["Gemini сегодня"] + usageRows.map { "\($0.0)   \($0.1)" }).joined(separator: "\n")
    }

    private func rememberIdentity(_ values: [String: Any]) {
        if let id = values["meeting_id"] as? String, !id.isEmpty { meetingID = id }
        if let id = values["transcription_run_id"] as? String, !id.isEmpty {
            transcriptionRunID = id
        }
        if let path = values["transcript_path"] as? String, path.hasPrefix("/") {
            transcriptPath = path
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
