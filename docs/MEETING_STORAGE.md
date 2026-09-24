# Meeting storage core

## Purpose

Этот слой закладывает основу для истории встреч, версий анализа и безопасного выбора активного протокола. Позже на него смогут опираться повторный анализ готовой расшифровки и интерфейс истории. Сейчас это отдельный storage core, не часть рабочего pipeline.

## Managed root

Root по умолчанию: `~/Library/Application Support/Small Transcriber/`. Это внутреннее служебное хранилище приложения; пользователю не требуется работать с ним вручную. `MeetingStore()` и import модуля ничего там не создают. Root и `index.sqlite3` появляются только при явном вызове `initialize()` (в будущем — из integration layer); сейчас реальная пользовательская DB не создаётся.

`initialize()` требует режим `0700` для root и `0600` для DB. Текущий слой не создаёт каталоги и файлы артефактов; их права нужно обеспечить при интеграции.

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

## What is not implemented yet

- Gemini pipeline и Swift GUI не подключены к storage core; текущий production flow не меняется.
- Реальная пользовательская DB пока не создаётся.
- Publication layer не реализован: существование файлов и совпадение их реальных SHA-256 с DB ещё не проверяются.
- Legacy importer, повторный analysis и history UI отсутствуют.
- Speaker overrides и user task state отсутствуют.

## Phase 1 roadmap

1. Storage core — реализован в feature branch; проверяется на синтетических данных во временной DB.
2. Интеграция с pipeline и публикация артефактов.
3. Версионированный повторный analysis.
4. Legacy importer для существующих результатов.
5. History GUI позднее.
