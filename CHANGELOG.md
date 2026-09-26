# Changelog

История восстановлена только по существующим файлам, backup-именам, diff и логам. Не подтверждённые детали не добавлены.

## Unreleased

- DEV app shell: обычный запуск без файла, drag в окно/на иконку, `NSOpenPanel`, отдельное переиспользуемое окно обработки, настройки и пять последних встреч. Production droplet не менялся.
- Versioned приватный `config.json` с ручными RPD/RPM/TPM, списком model ID и выбором primary/fallback по этапам; managed Python runtime читает выбор, отсутствие/ошибка config безопасно возвращают прежние модели.
- Read-only `status` для usage и истории; ручной лимит отдельно от подтверждённого quotaValue API и не влияет на retry.

- Managed source: пять видимых этапов GUI с live progress, локальный счётчик Gemini по моделям, отдельный upload count и условный показ подтверждённого дневного лимита.
- SQLite schema v2 добавляет `gemini_request_usage` и `gemini_quota_observations` с миграцией v1 без пересоздания встреч или runs; сутки считаются по `America/Los_Angeles`.
- Structured Gemini request events и классификация 429 по metadata; подтверждённая дневная квота прекращает retry, optional speaker/name stages ограничены короткой политикой, а основной protocol failure остаётся критическим.
- Добавлен `retry-analysis` для нового protocol analysis run из проверенной успешной расшифровки без повторной транскрипции; failed runs сохраняются, active переключается только после успешной публикации.
- Исчерпание 503 по всем fallback-моделям получает стабильный `gemini_overloaded` и понятное сообщение в DEV GUI; сырой ответ Gemini не показывается.
- GUI предлагает повторить только создание протокола и показывает опубликованный HTML или TXT через Finder reveal вместо навигации по UUID-каталогам. Production app не изменён.
- Source Swift GUI подключён к одному managed orchestrator process и его JSON Lines progress; итоговые пути берутся из события. Изменение ещё не установлено в production.
- Добавлен source managed meeting pipeline orchestrator: SQLite lifecycle, изолированные run directories, проверка и публикация transcript/JSON/HTML, JSON Lines progress. К production Swift GUI ещё не подключён.
- В protocol generator добавлен explicit managed-output mode с caller-provided пустым staging-каталогом и проверкой пары JSON/HTML перед публикацией. Legacy behavior сохранён; к production GUI режим пока не подключён.
- Добавлена проверка provenance для managed transcription cache: versioned `manifest.json`, отдельные generations и атомарная запись cache-файлов. Legacy cache не изменён.
- В transcriber добавлен explicit managed-output mode с каталогами output/cache от вызывающего слоя и проверкой SHA-256 source до и после обработки. Legacy behavior сохранён; managed mode ещё не подключён к GUI.
- Добавлен начальный meeting storage core: SQLite schema v1, UUIDv4 для встреч и runs, выбор активной версии анализа и проверка managed paths.
- Storage core подключён только к source orchestrator; production GUI и flow не изменены.

## 2026-09-23 — baseline consolidation

- Текущие production-исходники скопированы в `src/` без изменения содержимого.
- `main.scpt` декомпилирован в `src/main.applescript`.
- Зафиксированы SHA-256 production и source-копий.
- Созданы документация и read-only `verify-install.sh`.
- Production, cache, пользовательский словарь и реальные данные встреч не изменялись и не копировались.

## 2026-09-23 — network, logging, retry и GUI

Подтверждено backup-файлами и diff:

- во все актуальные curl-вызовы добавлен принудительный IPv4 (`-4`);
- добавлены curl timeout;
- логи получили timestamp;
- протокол получил retry 503 через 10 и 30 секунд с переходом после третьего 503;
- создан `MeetingStatus.swift` и x86_64 `gemini_meeting_gui`;
- AppleScript переключён с прямого запуска Python pipeline на запуск Swift GUI.

## 2026-09-16 — Merge V2.1 и полный pipeline

Подтверждено именованными вариантами и README:

- разрабатывалась и проверялась Merge V2.1 для overlap соседних chunks;
- overlap стал явным параметром merge-логики;
- добавлена обработка короткого fragment replacement;
- parser протокола адаптирован к `MM:SS`, `HH:MM:SS`, `spk:0` и `Спикер 0`;
- droplet был расширен от одной транскрипции до последовательного transcript → protocol pipeline;
- создан исходный README Gemini Meeting Pipeline.

## 2026-09-15 — первоначальная итеративная версия

Сохранились последовательные snapshots транскриптора и генератора протокола. По ним достоверно видно развитие двух отдельных Python-этапов, но назначение каждого timestamp-only backup без дополнительного исторического контекста не установлено.
