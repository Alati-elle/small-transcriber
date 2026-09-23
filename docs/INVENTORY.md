# Инвентаризация

Дата baseline: 23 сентября 2026 года, часовой пояс Europe/Moscow.

## Source of truth

| Source-файл | Production-источник | Production SHA-256 | Source SHA-256 | Проверка |
|---|---|---|---|---|
| `src/gemini_transcribe_meeting.py` | `~/.local/bin/gemini_transcribe_meeting.py` | `9a66edf83f45486b108abb26868b8f900f4d58a2fa0471ff58d2b0497b3cd117` | `9a66edf83f45486b108abb26868b8f900f4d58a2fa0471ff58d2b0497b3cd117` | `cmp`: совпадает |
| `src/gemini_make_protocol.py` | `~/.local/bin/gemini_make_protocol.py` | `74f30fb07b0278d395d82d2d2d83a796ab2da63b4b1a05f7394ff6b38e3bb5b7` | `4b2aef81bf45fbaa27fc73b1d121d87bb13399740bea806a4624f8d8117aad6e` | Отличается только обезличенными примерами/fixture-like text; функциональная логика не менялась; production не изменялся |
| `src/MeetingStatus.swift` | `~/.local/share/gemini-meeting-pipeline/MeetingStatus.swift` | `94e4e492063bbc17f11a0736ce73d20c57d72ba1f2b3bbbbe4628413db99b151` | `94e4e492063bbc17f11a0736ce73d20c57d72ba1f2b3bbbbe4628413db99b151` | `cmp`: совпадает |
| `src/main.applescript` | `~/Desktop/Расшифровать встречу.app/.../main.scpt` | compiled: `baf4bc39c47124197cd71044cc1d332c48af00081580198ee881289c8c3a31a7` | readable source: `5ebab97835cd9649c6e3cc007a7ecf34224e947e6ffaa7b29fe5afcf69c4be00` | installed decompilation отличается только whitespace в пустых строках |

Хеши compiled AppleScript и текстового исходника по определению не должны совпадать. Для них применяется semantic roundtrip через `osacompile`/`osadecompile`.

Для публикации source-копии в `src/gemini_make_protocol.py` имена и связанные реплики в примерах заменены полностью синтетическим текстом. Структура prompt, функциональная логика, алгоритмы и поведение кода не менялись. Установленный production-файл не изменялся.

## Установленные компоненты

| Путь | Тип | Назначение | Статус |
|---|---|---|---|
| `~/Desktop/Расшифровать встречу.app` | app bundle | Drag-and-drop launcher | Production; не изменён |
| `~/.local/bin/gemini_transcribe_meeting.py` | Python | Транскрипция | Production; source baseline создан |
| `~/.local/bin/gemini_make_protocol.py` | Python | Протокол | Production; source baseline создан |
| `~/.local/bin/gemini_meeting_gui` | Mach-O x86_64 | Нативный GUI | Production; SHA-256 `346968168cb3990eadd20476e7a5723be4c29f318689ad40f340449c9c4de528` |
| `~/.local/share/gemini-meeting-pipeline/MeetingStatus.swift` | Swift | GUI source | Production source; baseline создан |
| `~/.config/gemini-transcribe/vocabulary.txt` | Text | Пользовательский словарь | Production config; не скопирован |
| macOS Keychain service `Gemini Transcribe API Key` | Secret | Gemini API key | Существует; не читался и не копировался |

Текущий app bundle имеет tree SHA-256 `6d0d1868a97571b6ba926bb34d56b6f306c2f4c22d713bcc17920ee290ff7adb`. Метод tree digest описан в `archive/README.md`.

## Документация до консолидации

Две найденные копии исходного README побайтно совпадают, SHA-256 `9d9d71d997e35f1aeb02350d71a942dcab980446beff81037f656a6d0aa5d803`:

- `~/.local/bin/README_gemini_meeting_pipeline.md`;
- `~/Downloads/README_Gemini_Meeting_Pipeline.md`.

README описывает состояние до Swift GUI и содержит несколько расхождений с production. Обе копии оставлены на месте как исторические документы.

## Backups

- 22 версии транскриптора и 14 версий генератора протокола в `~/.local/bin`;
- 6 полных `.app` bundle в `~/.local/share/gemini-meeting-pipeline/backups`;
- один AppleScript backup внутри рабочей `.app`;
- четыре Python bytecode-файла в `~/.local/bin/__pycache__`.

Полный manifest с хешами находится в `archive/README.md`. Файлы физически не переносились.

## Найденные результаты и test data

| Путь | Размер на момент аудита | Содержимое | Классификация |
|---|---:|---|---|
| `~/Desktop/Gemini_AB_test` | 6,0 МБ | Успешный короткий transcript/protocol и cache | Test data с реальным содержимым; не копировать |
| `~/Desktop/<legacy-meeting-result-1>` | 102 МБ | Старые chunks и Gemini JSON | Legacy/test data; не копировать |
| `~/Desktop/<legacy-meeting-result-2>` | 52 МБ | TXT, JSON и варианты HTML | Legacy/test data; не копировать |
| `~/Downloads/<successful-end-to-end-sample>` | 18 МБ | Успешный end-to-end результат 17 сентября | Возможный внешний cache-only fixture; не копировать |
| `~/Downloads/<recent-failed-protocol-sample>` | 13 МБ | Успешный cached transcript, неуспешный protocol | Diagnostic data; не копировать |
| iCloud Drive `<legacy-icloud-result>` | 1,0 МБ | Старый формат transcript/cache | Legacy data; не копировать |
| `~/Desktop/gemini_transcribe_last.log` | 1,8 КБ | Старый общий лог | Legacy log; не копировать |

## Смежные, но не production-файлы

- `~/Downloads/st_protocol_template_full.html` и связанные PDF/XLSX — reference material, кодом не читаются;
- `~/Downloads/transcribe-kit-v1.zip` — будущий Content-Kit, вне текущего проекта.

## Границы аудита

Проверены известные пути, Desktop, Documents, Downloads, `.local`, `.config`, `codex`, локально доступная часть iCloud Drive, `/Applications`, `/Library`, `/usr/local` и `/opt`. Remote-only файлы облачных провайдеров, не выгруженные на Mac, не могут считаться полностью проаудированными.

## Проверка baseline-консолидации

`scripts/verify-install.sh` выполнен после создания source tree:

- 20 проверок успешно, 0 ошибок;
- Python syntax source и installed copies — OK;
- SHA-256/`cmp` для `gemini_transcribe_meeting.py` и Swift source — совпадают;
- `gemini_make_protocol.py` отличается от установленной production-копии только обезличенными примерами/fixture-like text; функциональная логика не менялась, production не изменялся;
- Swift source собран во временный x86_64 binary — OK;
- installed GUI имеет архитектуру x86_64 — OK;
- installed AppleScript декомпилируется — OK;
- readable `src/main.applescript` компилируется и совпадает с installed decompilation, кроме завершающей пустой строки — OK;
- отдельная временная сборка droplet `.app` принимает wildcard file type — OK;
- install script запускался только в dry-run режиме.

Gemini API не вызывался. Production-файлы, Keychain, `vocabulary.txt`, cache и результаты встреч не изменялись.
