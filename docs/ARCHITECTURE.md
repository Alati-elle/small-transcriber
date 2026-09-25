# Архитектура

Документ описывает baseline production на 23 сентября 2026 года. Он не описывает планируемые исправления как уже реализованные.

## Flow

```text
аудиофайл
  → ~/Desktop/Расшифровать встречу.app
  → main.scpt
  → ~/.local/bin/gemini_meeting_gui <audio>
  → Этап 1: gemini_transcribe_meeting.py <audio>
      → ffprobe
      → ffmpeg: chunks по 360 сек., overlap 30 сек.
      → Gemini gemini-3.5-transcribe
      → _service/*_gemini.json
      → Merge V2.1 и quality checks
      → *_ПОЛНАЯ_РАСШИФРОВКА.txt
      → _service/merge_diagnostics.json
  → Этап 2: gemini_make_protocol.py <transcript>
      → нормализация технических speaker ID
      → попытка определить имена
      → извлечение пунктов встречи
      → *_ПРОТОКОЛ.json
      → *_ПРОТОКОЛ.html
  → GUI показывает результат или ошибку
```

## Компоненты

### AppleScript launcher

`src/main.applescript` — декомпилированный исходник `main.scpt`. Обработчик `open` запускает отдельный Swift GUI для каждого перетащенного файла через `nohup`, после чего droplet завершается. Запуск без файла показывает подсказку.

### Swift GUI

`src/MeetingStatus.swift` создаёт output directory, последовательно запускает два Python-процесса через `/usr/bin/python3`, направляет stdout/stderr в `transcribe.log` и `protocol.log`, читает последнюю строку лога и переводит технические сообщения в пользовательские статусы.

При успехе GUI показывает кнопки «Открыть протокол», «Открыть папку», «Закрыть». При ошибке — «Открыть лог», «Открыть папку», «Закрыть».

### Транскрипция

`src/gemini_transcribe_meeting.py`:

- использует `/usr/local/bin/ffmpeg` и `/usr/local/bin/ffprobe`;
- режет аудио на chunks по 360 секунд с overlap 30 секунд;
- использует модель `gemini-3.5-transcribe`;
- отправляет запросы через `curl -4`;
- использует connect timeout 20 секунд и общий timeout 120 секунд;
- обрабатывает 429 до четырёх попыток, учитывая `RetryInfo` и дневную квоту;
- повторно использует непустой валидный Gemini JSON с `candidates`;
- выполняет Merge V2.1 и записывает диагностику качества;
- создаёт `*_ПОЛНАЯ_РАСШИФРОВКА.txt`.

Отдельной retry/fallback-логики для 503 в транскрипторе нет.

### Генератор протокола

`src/gemini_make_protocol.py` читает готовую полную расшифровку. Используемая цепочка моделей:

1. `gemini-3.6-flash`;
2. `gemini-3.8-flash`;
3. `gemini-3.5-flash`.

Текст делится на батчи максимум по 40 000 символов с overlap 8 реплик. Gemini используется в трёх местах: нормализация speaker ID, определение имён и извлечение пунктов протокола.

Для каждого места действуют одинаковые основные правила:

- `curl -4`, connect timeout 15 секунд, общий timeout 300 секунд;
- при 429 — один повтор с `RetryInfo`, не более 65 секунд ожидания, затем следующая модель;
- при 503 — повторы через 10 и 30 секунд, после третьего 503 следующая модель.

Ошибка нормализации или определения имён не прерывает весь протокол. Ошибка основного извлечения пунктов прерывает генерацию.

## Конфигурация

- API key: macOS Keychain, account текущего пользователя, service `Gemini Transcribe API Key`;
- пользовательский словарь: `~/.config/gemini-transcribe/vocabulary.txt`;
- в source tree хранится только `config/vocabulary.example.txt`.

## Имена и размещение результатов

Для `/path/Встреча.m4a` используется:

```text
/path/Встреча/
  transcribe.log
  protocol.log
  Встреча_ПОЛНАЯ_РАСШИФРОВКА.txt
  Встреча_ПРОТОКОЛ.json
  Встреча_ПРОТОКОЛ.html
  _service/
    Встреча_chunk360_ov30_001.m4a
    Встреча_chunk360_ov30_001_gemini.json
    Встреча_chunk360_ov30_001_transcript.txt
    merge_diagnostics.json
    speaker_normalization.json
```

При повторном запуске существующие chunks и Gemini JSON могут быть использованы повторно. Текущие ограничения этой проверки описаны в `KNOWN_ISSUES.md`.

## Зависимости

- macOS и AppKit;
- `/usr/bin/python3` (на проверенном Mac: Python 3.9.6);
- Swift toolchain/Xcode Command Line Tools;
- `/usr/local/bin/ffmpeg` и `/usr/local/bin/ffprobe`;
- `curl`;
- macOS Keychain;
- сетевой доступ к Gemini API.

Дополнительных Python-пакетов baseline не требует.

## Meeting storage (feature branch)

[MEETING_STORAGE.md](MEETING_STORAGE.md) описывает реализованный storage core для истории встреч и версий анализа. Он пока не подключён к production flow, описанному выше; текущий GUI и pipeline продолжают работать без SQLite.

Source-версия transcriber также поддерживает явный managed-output mode; production flow выше по-прежнему использует legacy-вызов.

В development source-версии `src/meeting_pipeline.py` соединяет `MeetingStore`, managed transcriber и managed protocol generator: создаёт записи SQLite, публикует проверенные run directories одним rename и выбирает active analysis. `retry-analysis` создаёт только новый analysis run из проверенного опубликованного TXT; несколько успешных transcription runs требуют явного выбора. Source Swift GUI запускает один orchestrator process, читает JSON Lines stdout и при analysis failure предлагает повторить создание протокола без транскрипции. Пять стадий видны сразу; дочерние managed scripts передают безопасный progress/request channel через orchestrator. Python записывает локальные API attempts в SQLite и отправляет snapshot после каждого результата; Swift только отображает. `gemini_overloaded` и подтверждённая дневная квота показываются без текста ответа Gemini. Swift не обращается к SQLite и не строит managed paths.

Кнопка «Показать в Finder» выделяет опубликованный HTML после успеха или TXT после ошибки анализа через Finder reveal/select. Исходные managed файлы остаются единственным source of truth; копии и отдельный export directory не создаются. В GUI отображается stem исходного файла, UUID остаётся внутренним ID. Отдельный DEV app использует этот flow; production launcher/GUI по-прежнему legacy, production installer orchestrator не разворачивает.
