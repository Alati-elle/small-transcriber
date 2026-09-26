import AppKit
import Foundation

final class SettingsWindow: NSObject {
    private unowned let shell: AppShell
    private let window: NSWindow
    private let tabs = NSTabView()
    private let warning = NSTextField(wrappingLabelWithString: "")
    private var config: [String: Any] = [:]
    private var hadMalformedConfig = false
    private var limitFields: [String: [String: NSTextField]] = [:]
    private var stageFields: [String: (NSComboBox, NSTextField)] = [:]
    private var newModel = NSTextField()
    private let stages = [
        ("transcription", "Расшифровка"),
        ("speaker_normalization", "Нормализация спикеров"),
        ("name_detection", "Определение имён"),
        ("protocol_generation", "Создание протокола")
    ]

    init(shell: AppShell) {
        self.shell = shell
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 560),
                          styleMask: [.titled, .closable, .miniaturizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "Настройки Small Transcriber"
        window.isReleasedWhenClosed = false
        window.center()
        let body = NSStackView()
        body.orientation = .vertical
        body.alignment = .leading
        body.spacing = 10
        body.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(body)
        NSLayoutConstraint.activate([
            body.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 20),
            body.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -20),
            body.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 20),
            body.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor, constant: -20)
        ])
        tabs.addTabViewItem(NSTabViewItem(identifier: "limits"))
        tabs.tabViewItem(at: 0).label = "Лимиты"
        tabs.addTabViewItem(NSTabViewItem(identifier: "models"))
        tabs.tabViewItem(at: 1).label = "Модели"
        tabs.addTabViewItem(NSTabViewItem(identifier: "general"))
        tabs.tabViewItem(at: 2).label = "Общие"
        body.addArrangedSubview(tabs)
        tabs.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true
        tabs.heightAnchor.constraint(greaterThanOrEqualToConstant: 430).isActive = true
        warning.textColor = .systemOrange
        warning.font = .systemFont(ofSize: 12)
        body.addArrangedSubview(warning)
        let save = NSButton(title: "Сохранить", target: self, action: #selector(saveSettings))
        body.addArrangedSubview(save)
        buildGeneral()
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        load()
    }

    private func load() {
        shell.runCommand(["config-get"]) { [weak self] value in
            guard let self = self,
                  let response = value as? [String: Any],
                  let config = response["config"] as? [String: Any] else {
                self?.warning.stringValue = "Не удалось загрузить настройки."
                return
            }
            self.config = config
            self.hadMalformedConfig = response["warning"] as? String != nil
            self.warning.stringValue = response["warning"] as? String ?? ""
            self.buildLimits()
            self.buildModels()
        }
    }

    private func stack() -> NSStackView {
        let view = NSStackView()
        view.orientation = .vertical
        view.alignment = .leading
        view.spacing = 12
        view.edgeInsets = NSEdgeInsets(top: 18, left: 14, bottom: 12, right: 14)
        return view
    }

    private func setTab(_ index: Int, view: NSView) {
        tabs.tabViewItem(at: index).view = view
    }

    private func buildLimits() {
        let page = stack()
        let help = NSTextField(wrappingLabelWithString: "Ручные лимиты используются только для отображения. Пустое поле означает неизвестный лимит; остановка по квоте зависит от ответа API.")
        help.font = .systemFont(ofSize: 12)
        help.textColor = .secondaryLabelColor
        page.addArrangedSubview(help)
        let models = ((config["gemini"] as? [String: Any])?["known_models"] as? [String]) ?? []
        let stored = ((config["gemini"] as? [String: Any])?["models"] as? [String: [String: Any]]) ?? [:]
        limitFields.removeAll()
        for model in models {
            let title = NSTextField(labelWithString: model)
            title.font = .systemFont(ofSize: 13, weight: .medium)
            page.addArrangedSubview(title)
            var fields: [String: NSTextField] = [:]
            let row = NSStackView()
            row.orientation = .horizontal
            row.spacing = 10
            for key in ["rpd", "rpm", "tpm"] {
                let field = NSTextField()
                field.placeholderString = key.uppercased() + " — ?"
                field.stringValue = (stored[model]?[key] as? Int).map(String.init) ?? ""
                field.alignment = .right
                field.widthAnchor.constraint(equalToConstant: 120).isActive = true
                row.addArrangedSubview(field)
                fields[key] = field
            }
            limitFields[model] = fields
            page.addArrangedSubview(row)
        }
        page.addArrangedSubview(NSButton(title: "Сбросить ручные лимиты", target: self,
                                         action: #selector(resetLimits)))
        setTab(0, view: page)
    }

    private func buildModels() {
        let page = stack()
        page.addArrangedSubview(NSTextField(wrappingLabelWithString:
            "Укажите model ID и порядок fallback через запятую. Используются только модели из списка ниже."))
        let known = ((config["gemini"] as? [String: Any])?["known_models"] as? [String]) ?? []
        let stagesConfig = ((config["gemini"] as? [String: Any])?["stages"] as? [String: [String: Any]]) ?? [:]
        stageFields.removeAll()
        for (key, title) in stages {
            let label = NSTextField(labelWithString: title)
            label.font = .systemFont(ofSize: 13, weight: .medium)
            page.addArrangedSubview(label)
            let primary = NSComboBox()
            primary.addItems(withObjectValues: known)
            primary.placeholderString = "Основная модель"
            primary.stringValue = stagesConfig[key]?["primary_model"] as? String ?? ""
            let fallback = NSTextField()
            fallback.placeholderString = "Fallback: model-1, model-2"
            fallback.stringValue = (stagesConfig[key]?["fallback_models"] as? [String] ?? []).joined(separator: ", ")
            let row = NSStackView(views: [primary, fallback])
            row.orientation = .horizontal
            row.spacing = 10
            primary.widthAnchor.constraint(equalToConstant: 210).isActive = true
            fallback.widthAnchor.constraint(equalToConstant: 360).isActive = true
            page.addArrangedSubview(row)
            stageFields[key] = (primary, fallback)
        }
        page.addArrangedSubview(NSTextField(labelWithString: "Известные модели: " + known.joined(separator: ", ")))
        newModel.placeholderString = "Добавить model ID"
        newModel.widthAnchor.constraint(equalToConstant: 280).isActive = true
        let add = NSButton(title: "Добавить", target: self, action: #selector(addModel))
        let row = NSStackView(views: [newModel, add])
        row.orientation = .horizontal
        page.addArrangedSubview(row)
        setTab(1, view: page)
    }

    private func buildGeneral() {
        let page = stack()
        page.addArrangedSubview(NSTextField(labelWithString: "День квоты: America/Los_Angeles"))
        page.addArrangedSubview(NSTextField(labelWithString: "API key хранится только в Keychain."))
        page.addArrangedSubview(NSTextField(labelWithString: shell.storageRoot + "/config.json"))
        setTab(2, view: page)
    }

    @objc private func addModel() {
        guard collectEdits() else { return }
        let model = newModel.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard model.range(of: "^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$", options: .regularExpression) != nil else {
            warning.stringValue = "Некорректный model ID."
            return
        }
        var gemini = config["gemini"] as? [String: Any] ?? [:]
        var known = gemini["known_models"] as? [String] ?? []
        if !known.contains(model) { known.append(model) }
        gemini["known_models"] = known
        var models = gemini["models"] as? [String: [String: Any]] ?? [:]
        models[model] = models[model] ?? ["rpd": NSNull(), "rpm": NSNull(), "tpm": NSNull()]
        gemini["models"] = models
        config["gemini"] = gemini
        newModel.stringValue = ""
        buildLimits()
        buildModels()
    }

    @objc private func resetLimits() {
        for fields in limitFields.values { for field in fields.values { field.stringValue = "" } }
        saveSettings()
    }

    @objc private func saveSettings() {
        guard !config.isEmpty else { return }
        if hadMalformedConfig {
            let alert = NSAlert()
            alert.messageText = "Заменить повреждённый config.json?"
            alert.informativeText = "Текущий файл останется без изменений до вашего подтверждения."
            alert.addButton(withTitle: "Заменить")
            alert.addButton(withTitle: "Отмена")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
        }
        guard collectEdits() else { return }
        guard let data = try? JSONSerialization.data(withJSONObject: config) else { return }
        shell.runCommand(["config-save"], input: data) { [weak self] value in
            guard let self = self else { return }
            if value == nil { self.warning.stringValue = "Настройки не сохранены: проверьте model ID и порядок fallback."; return }
            self.hadMalformedConfig = false
            self.warning.stringValue = "Настройки сохранены."
            self.shell.refresh()
        }
    }

    private func collectEdits() -> Bool {
        var gemini = config["gemini"] as? [String: Any] ?? [:]
        var models = gemini["models"] as? [String: [String: Any]] ?? [:]
        for (model, fields) in limitFields {
            var limits = models[model] ?? [:]
            for (key, field) in fields {
                let raw = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
                if raw.isEmpty { limits[key] = NSNull() }
                else if let value = Int(raw), value > 0 { limits[key] = value }
                else { warning.stringValue = "Лимиты должны быть положительными целыми числами."; return false }
            }
            models[model] = limits
        }
        gemini["models"] = models
        var stagesConfig = gemini["stages"] as? [String: [String: Any]] ?? [:]
        for (key, fields) in stageFields {
            var selected = stagesConfig[key] ?? [:]
            selected["primary_model"] = fields.0.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            selected["fallback_models"] = fields.1.stringValue.split(separator: ",", omittingEmptySubsequences: true)
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            stagesConfig[key] = selected
        }
        gemini["stages"] = stagesConfig
        config["gemini"] = gemini
        return true
    }
}
