# Meeting storage core

## Purpose

Этот слой хранит историю встреч, версии анализа и выбор активного протокола. Source-версия `meeting_pipeline.py` теперь связывает SQLite с managed transcriber и protocol generator. Production GUI пока использует legacy flow.

## Managed root

Root по умолчанию: `~/Library/Application Support/Small Transcriber/`. Это внутреннее служебное хранилище приложения; пользователю не требуется работать с ним вручную. `MeetingStore()` и import модуля ничего там не создают. Root и `index.sqlite3` появляются только при явном вызове `initialize()`; source orchestrator вызывает его после проверки и SHA-256 исходника. Тесты используют отдельный `--storage-root`, поэтому реальная пользовательская DB не создаётся.

`initialize()` требует режим `0700` для root и `0600` для DB. Orchestrator создаёт каталоги run с приватным umask `077`; stage logs имеют режим `0600`.

## Data model и SQLite schema v1

SQLite `user_version = 1`, три таблицы:

| Таблица | Смысл и важные поля |
|---|---|
| `meetings` | Устойчивая идентичность встречи (`id` — UUIDv4), время создания, название, вид и метаданные источника, `active_analysis_run_id`. Идентичность не определяется именем файла. |
| `transcription_runs` | Отдельные попытки транскрипции одной встречи: статус, backend/model, параметры, hash источника, относительный путь и hash TXT. Успешный run требует путь и SHA-256 расшифровки. |
| `analysis_runs` | Отдельные версии анализа успешной транскрипции: статус, параметры модели, hash входного TXT, относительные пути JSON/HTML и их hashes. Успешный run требует метаданные обоих outputs. |

Все ID — строки UUIDv4; для связей включён `PRAGMA foreign_keys = ON` на каждом соединении. `transcription_runs.meeting_id` ссылается на встречу. Составной FK `(analysis_runs.meeting_id, transcription_run_id)` требует транскрипцию той же встречи. Составной FK `(meetings.id, active_analysis_run_id)` ссылается на `(analysis_runs.meeting_id, id)` и не допускает active run чужой встречи. Для этого в таблицах runs есть `UNIQUE(meeting_id, id)`. Статусы ограничены `running`, `succeeded`, `failed`, `interrupted`, `cancelled`, `incomplete`.

## Source of truth

| Данные | Source of truth |
|---|---|
| Идентичность встречи и связи runs | SQLite |
| Содержимое расшифровки | TXT-файл; SQLite хранит путь и SHA-256 |
| Структурированные данные анализа | JSON-файл; SQLite хранит путь и SHA-256 |
| HTML | Представление результата, не канонические данные анализа |
| Активная версия анализа | `meetings.active_analysis_run_id` в SQLite |
| Cache и logs | Вспомогательные данные, не source of truth |

## Active analysis

`set_active_analysis()` работает в транзакции. Он проверяет существование встречи, принадлежность run этой встрече, статус `succeeded` и наличие путей и hashes JSON/HTML до изменения указателя. `failed`, `incomplete`, `interrupted`, `cancelled` и ещё выполняющийся run не могут стать active через API. Смена active не удаляет старые runs. Составной FK дополнительно защищает принадлежность на уровне SQLite.

## Managed paths

Пути TXT, JSON и HTML внутри managed root хранятся относительно root. Resolver отклоняет absolute path, компонент `..` и выход через symlink. Если допустимый symlink ведёт внутри root, в SQLite сохраняется уже разрешённый canonical relative path. Конечный файл пока может не существовать: storage core сохраняет метаданные, а не публикует файлы.

Проверка пути действительна в момент разрешения. При будущем чтении и записи integration layer должен повторно проверять путь и учитывать замену symlink между проверкой и открытием файла. `source_original_path` — метаданные внешнего источника, а не managed artifact path.

## Phase 1B: managed transcriber

Первый отдельный building block реализован в source-версии transcriber:

```text
python3 src/gemini_transcribe_meeting.py AUDIO --output-dir DIR --cache-dir DIR --expected-source-sha256 HEX
```

Все три опции обязательны вместе; `--output-dir` включает managed mode. Вызывающий слой задаёт оба каталога: final TXT и `_service/merge_diagnostics.json` находятся в output-dir, chunks и cache — в cache-dir. Каталоги могут уже существовать, совпадать или быть вложенными друг в друга. Существующие symlink-пути отдельных артефактов отклоняются; принадлежность каталогов managed root должен обеспечить будущий orchestrator вместе с MeetingStore. Сам transcriber не записывает SQLite.

SHA-256 source проверяется до обработки и после неё. Новый final TXT сначала готовится во временном файле внутри output-dir и заменяет окончательный файл только после повторной проверки. При ошибке ранее существовавший final TXT сохраняется; cache и диагностика могут остаться как непубликованные материалы. Legacy-вызов `python3 src/gemini_transcribe_meeting.py AUDIO` сохраняет прежние пути и auto-open.

Managed cache provenance реализован отдельно от storage core. `--cache-dir/manifest.json` содержит versioned identity: SHA-256 и размер source, backend/model/API, параметры chunk/overlap/step, SHA-256 ffmpeg/ffprobe и параметры audio preprocessing, SHA-256 фактического шаблона Gemini-запроса и нормализованного словаря, а также cache format version. Полные prompt и словарь в manifest не сохраняются.

Manifest атомарно указывает на `--cache-dir/generations/<id>/`. Только точное совпадение identity разрешает reuse файлов этой generation. При отсутствии, повреждении или несовпадении manifest создаётся новая generation; старые файлы не удаляются и не используются. Частичный cache с совпадающим manifest может дозаполняться. Legacy cache работает без manifest и не менялся.

Protocol generator также поддерживает отдельный managed-вызов:

```text
python3 src/gemini_make_protocol.py TRANSCRIPT --output-dir DIR
```

`DIR` задаёт вызывающий слой как новый или уже существующий **пустой** staging-каталог. Generator пишет `*_ПРОТОКОЛ.json`, `*_ПРОТОКОЛ.html` и `_service/speaker_normalization.json` только внутрь него. Непустой каталог, включая прежнюю пару или один файл, отклоняется до обработки; предыдущая хорошая пара не изменяется. Оба новых файла проверяются до публикации; при обычной ошибке публикации частичная пара убирается. Внешнюю публикацию всего analysis run directory выполняет source orchestrator.

Generator не записывает SQLite, не переключает active analysis и не создаёт run IDs. Manifest хранит только provenance cache и не заменяет SQLite.

## Phase 1B: managed orchestrator (source only)

`python3 src/meeting_pipeline.py run AUDIO [--storage-root DIR]` проверяет и хеширует source до создания meeting. Затем он создаёт transcription run, запускает managed transcriber, проверяет TXT и source hash, публикует `.staging` в `published` одним rename и сохраняет относительный TXT path/hash в SQLite. После этого создаёт analysis run, запускает managed generator, проверяет JSON/HTML и hashes, публикует analysis directory, повторно проверяет outputs, сохраняет относительные paths/hashes, переключает active и читает его обратно. Exit code `0` возможен только после read-back.

```text
meetings/<meeting-id>/
  transcriptions/<transcription-run-id>/
    transcribe.log
    cache/
    published/*_ПОЛНАЯ_РАСШИФРОВКА.txt
  analyses/<analysis-run-id>/
    protocol.log
    published/*_ПРОТОКОЛ.json
    published/*_ПРОТОКОЛ.html
```

До публикации вместо `published/` используется `.staging/`. Run IDs, связи, статусы, hashes и active pointer принадлежат SQLite; содержимое — файлам. `published/` не перезаписывается. Проверка/rename каталога и обновление SQLite происходят последовательно, общей filesystem+DB транзакции нет. При сбое child process run получает `failed`, при отсутствии/невалидности outputs, публикации или финализации — `incomplete` (если run ещё `running`). Если active switch падает после успешной записи analysis, analysis остаётся `succeeded`, но не становится active. Staging, cache и logs сохраняются для диагностики.

stdout orchestrator — JSON Lines (`event`, `timestamp`, безопасные IDs/phase/status); финальный `pipeline_succeeded` содержит пути к опубликованным outputs и логам. Ошибки дают `pipeline_failed` и ненулевой exit. Transcript, prompt и API key в события не включаются.

Автоматического recovery running runs пока нет: без ownership lock он мог бы ошибочно прервать параллельный процесс. `find_running_runs()` и `mark_interrupted()` остаются для отдельного milestone. Разные встречи имеют отдельные UUID-каталоги и cache; SQLite уже имеет bounded busy timeout. Установленный GUI и installer пока не подключены к orchestrator.

## What is not implemented yet

- Swift GUI не подключён к source orchestrator; текущий production flow не меняется.
- Реальная пользовательская DB пока не создаётся.
- Recovery running runs, повторный analysis существующей встречи, legacy importer и history UI пока отсутствуют.
- Speaker overrides и user task state отсутствуют.

## Phase 1 roadmap

1. Storage core — реализован в feature branch; проверяется на синтетических данных во временной DB.
2. Source-интеграция с pipeline и публикация артефактов — реализована; GUI не подключён.
3. Версионированный повторный analysis.
4. Legacy importer для существующих результатов.
5. History GUI позднее.
