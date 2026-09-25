# Changelog

История восстановлена только по существующим файлам, backup-именам, diff и логам. Не подтверждённые детали не добавлены.

## Unreleased

- В transcriber добавлен explicit managed-output mode с каталогами output/cache от вызывающего слоя и проверкой SHA-256 source до и после обработки. Legacy behavior сохранён; managed mode ещё не подключён к GUI и storage orchestration.
- Добавлен начальный meeting storage core: SQLite schema v1, UUIDv4 для встреч и runs, выбор активной версии анализа и проверка managed paths.
- Storage core пока не подключён к транскрипции и GUI; production flow не изменён.

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
