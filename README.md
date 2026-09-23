# Small Transcriber

Локальный macOS-инструмент для расшифровки записей встреч и создания структурированных протоколов через Gemini API.

## Текущий статус

23 сентября 2026 года сюда скопированы без изменения поведения актуальные production-исходники. Этот каталог теперь является source of truth для дальнейшей разработки, но обратная установка в production ещё не выполнялась.

Production продолжает работать из прежних путей:

- `~/Desktop/Расшифровать встречу.app`;
- `~/.local/bin/gemini_transcribe_meeting.py`;
- `~/.local/bin/gemini_make_protocol.py`;
- `~/.local/bin/gemini_meeting_gui`;
- `~/.local/share/gemini-meeting-pipeline/MeetingStatus.swift`.

## Использование установленной версии

1. Записать встречу и перенести аудиофайл на Mac.
2. Перетащить файл на `~/Desktop/Расшифровать встречу.app`.
3. Дождаться окончания двух этапов в окне состояния.
4. Открыть HTML-протокол или папку результата кнопкой GUI.

Рядом с исходным аудио создаётся папка с тем же именем без расширения. В ней находятся полная расшифровка, JSON/HTML-протокол, логи и каталог `_service` с cache и диагностикой.

## Source of truth

- `src/gemini_transcribe_meeting.py` — транскрипция, chunks, cache и merge;
- `src/gemini_make_protocol.py` — нормализация спикеров и генерация протокола;
- `src/MeetingStatus.swift` — нативный GUI;
- `src/main.applescript` — launcher droplet-приложения.

Скомпилированные `.app` и `gemini_meeting_gui` являются устанавливаемыми артефактами, а не исходниками.

## Конфигурация и приватность

API key остаётся в macOS Keychain. Рабочий словарь остаётся в `~/.config/gemini-transcribe/vocabulary.txt`; в проекте хранится только безопасный пример.

Реальные записи, расшифровки, протоколы и cache не входят в проект. Известные проблемы безопасности зафиксированы в `docs/KNOWN_ISSUES.md`, но в baseline-коде пока не исправлены.

## Документация

- `docs/INVENTORY.md` — production-файлы, baseline-хеши и найденные данные;
- `docs/ARCHITECTURE.md` — фактический flow и ответственность компонентов;
- `docs/DEVELOPMENT.md` — сборка, проверка, установка и rollback;
- `docs/KNOWN_ISSUES.md` — подтверждённые ограничения и риски;
- `CHANGELOG.md` — достоверно восстановленная история;
- `archive/README.md` — manifest backup-файлов без их физического переноса.

## Проверка

```bash
./scripts/verify-install.sh
```

Скрипт ничего не устанавливает и не изменяет в production.
