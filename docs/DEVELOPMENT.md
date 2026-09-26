# Разработка и установка

## Source of truth

Редактировать следует только:

- `src/gemini_transcribe_meeting.py`;
- `src/gemini_make_protocol.py`;
- `src/meeting_store.py` и `src/meeting_pipeline.py` для managed source flow;
- `src/ManagedPipelineEvents.swift` для разбора JSON Lines в GUI;
- `src/MeetingStatus.swift`;
- `src/AppShell.swift`, `src/SettingsWindow.swift` и `src/meeting_config.py` для DEV shell/config;
- `src/main.applescript`.

Файлы в `~/.local/bin`, `~/.local/share/gemini-meeting-pipeline` и `.app` на Desktop являются установленными копиями. Рабочий `vocabulary.txt`, Keychain, cache и результаты встреч не являются частью source tree.

## DEV app shell и config

`./scripts/build-gui.sh` собирает release binary, `./scripts/build-dev-app.sh` — отдельный DEV `.app` с `-DDEBUG -DDEV`; оба используют четыре Swift source-файла. DEV bundle содержит Swift executable напрямую, поэтому обычный запуск открывает главное окно, а Finder передаёт аудиофайл через `application(_:openFiles:)`. DEV runtime ищет Python scripts в `~/.local/share/gemini-meeting-pipeline/dev-managed/` и DB/config в `~/Library/Application Support/Small Transcriber DEV/`. Production installer и `src/main.applescript` этим milestone не меняются.

`./scripts/install-dev-shell.sh` сначала показывает dry run. `--apply` устанавливает только DEV Python/runtime и DEV Desktop app, сохраняя прежние файлы в `dev-managed/backups/app-shell_*`. DEV `index.sqlite3`, встречи, usage и пользовательский config не входят в список целей установки.

Read-only preflight без создания root:

```bash
/usr/bin/python3 src/meeting_pipeline.py status --storage-root "$HOME/Library/Application Support/Small Transcriber DEV" --limit 5
/usr/bin/python3 src/meeting_pipeline.py config-get --storage-root "$HOME/Library/Application Support/Small Transcriber DEV"
```

`config-save` принимает полный JSON через stdin; UI вызывает его после редактирования. Отсутствующий config не создаётся, пока пользователь не сохранит настройки. Повреждённый config оставляется на месте; UI отдельно подтверждает замену. `known_models` расширяется вручную без запроса к Gemini. Выбор model/fallback выполняется Python child scripts в managed mode через проверенный config и `SMALL_TRANSCRIBER_STORAGE_ROOT`. При отсутствии config текущий model order сохранён. Manual RPD/RPM/TPM служат подсказкой в GUI и не останавливают pipeline.

Для fake smoke используйте `SMALL_TRANSCRIBER_PIPELINE_SCRIPT=$PWD/tests/fake_meeting_pipeline.py` и отдельный временный `SMALL_TRANSCRIBER_STORAGE_ROOT` при прямом запуске DEV binary. Fake script не делает сетевых вызовов Gemini. Не сбрасывайте и не копируйте пользовательскую DEV DB ради тестов.

## Запуск transcriber из source

```bash
python3 src/gemini_transcribe_meeting.py AUDIO
python3 src/gemini_transcribe_meeting.py AUDIO \
  --output-dir DIR \
  --cache-dir DIR \
  --expected-source-sha256 HEX
```

Первый вызов сохраняет legacy layout рядом с source и auto-open. Во втором вызове `--output-dir` включает managed mode, `--cache-dir` задаёт каталог chunks/cache, а `--expected-source-sha256` передаёт 64-символьный hex SHA-256 source. Все три опции обязательны вместе. Managed-вызов является низкоуровневым building block для source orchestrator, а не пользовательским production workflow. Для тестов используйте только синтетические файлы; эти команды без mock вызывают ffmpeg и Gemini.

Managed cache использует `DIR/manifest.json` и `DIR/generations/<id>/`. Manifest записывается атомарно и должен точно совпасть с текущим source, параметрами обработки, моделью, шаблоном запроса и словарём для reuse. Отсутствующий или невалидный manifest создаёт новую generation без удаления старых файлов. Legacy-вызов manifest не создаёт.

Протокол из готовой расшифровки запускается аналогично:

```bash
python3 src/gemini_make_protocol.py TRANSCRIPT
python3 src/gemini_make_protocol.py TRANSCRIPT --output-dir EMPTY_STAGING_DIR
```

Первый вызов сохраняет legacy output рядом с transcript. `--output-dir` включает managed mode: каталог может отсутствовать или быть пустым, но непустой каталог отклоняется до обработки. Это низкоуровневый building block source orchestrator; production GUI пока вызывает legacy mode.

## Managed pipeline из source

```bash
python3 src/meeting_pipeline.py run AUDIO
python3 src/meeting_pipeline.py run SYNTHETIC_AUDIO --storage-root TEMP_PRIVATE_DIR
python3 src/meeting_pipeline.py retry-analysis --meeting-id UUID --storage-root TEMP_PRIVATE_DIR
python3 src/meeting_pipeline.py retry-analysis --meeting-id UUID --transcription-run-id UUID --storage-root TEMP_PRIVATE_DIR
```

Без `--storage-root` используется `MeetingStore.DEFAULT_ROOT`. Вызов реальных child scripts использует Gemini; для синтетических integration tests предусмотрены `--transcriber-script` и `--protocol-script`. Orchestrator ищет обычные child scripts и `gemini_telemetry.py` рядом со своим файлом и запускает их через тот же `sys.executable`; DEV runtime содержит их вместе, production installer пока нет. `retry-analysis` не принимает audio или `--transcriber-script`, требует существующую DB и не создаёт default root при её отсутствии. Ровно одна successful transcription выбирается автоматически; при нескольких требуется `--transcription-run-id`. Этот CLI без synthetic `--protocol-script` вызывает реальный Gemini, поэтому его нельзя запускать как preflight на пользовательской встрече. stdout — только JSON Lines progress/result с `operation: full|analysis_retry`, stderr — короткая диагностика, подробности child process — в приватных run logs. Exit `75` protocol generator превращается в `error_code: gemini_overloaded`, `76` — в `daily_quota_exhausted`.

Новый structured protocol содержит `stage_started/succeeded/warning/failed/progress` для пяти стадий и `gemini_request_started/retry/succeeded/failed`, `usage_updated` для API. Initial `usage_updated` приходит до первого нового запроса; он показывает локальные generate-content counts и отдельный upload count за `America/Los_Angeles` day. Код 429 классифицируется по `QuotaFailure.quotaId/quotaMetric`: `rate_limit_rpm`, `rate_limit_tpm`, `daily_quota_exhausted` или `rate_limit_unknown`; 503 — `gemini_overloaded`. Один 429 без metadata не объявляется дневной квотой. Optional speaker/name stages используют primary с одним коротким retry и один fallback без retry; main protocol сохраняет ограниченный 503 fallback. `daily_quota_exhausted` останавливает дальнейшие попытки данного action. Никакие prompts/transcript/raw API bodies в stdout protocol или usage DB не пишутся.

Source GUI запускает один `meeting_pipeline.py run AUDIO`; после analysis failure с успешным TXT кнопка retry запускает `retry-analysis --meeting-id UUID --transcription-run-id UUID` в том же окне без Stage 1. Обычная сборка всегда ищет `~/.local/bin/meeting_pipeline.py` и игнорирует test overrides. Только сборка с `-DDEBUG` читает `SMALL_TRANSCRIBER_PIPELINE_SCRIPT` и `SMALL_TRANSCRIBER_STORAGE_ROOT` (второй передаётся как `--storage-root`); отдельный DEV app задаёт оба пути. Без fake script DEV GUI вызовет реальный Gemini, поэтому в тестах используйте `tests/fake_meeting_pipeline.py` и временный root. Foundation-only parser и fake subprocess проверяются отдельным Swift harness:

```bash
test_dir=$(mktemp -d /private/tmp/swift-events.XXXXXX)
xcrun swiftc src/ManagedPipelineEvents.swift tests/test_managed_pipeline_events.swift -o "$test_dir/events-test"
"$test_dir/events-test" "$PWD/tests/fake_meeting_pipeline.py" "$PWD/src/MeetingStatus.swift"
```

`scripts/build-gui.sh` и `scripts/verify-install.sh` собирают оба Swift source-файла. Installer в этом milestone не менялся; будущая установка должна доставить orchestrator, MeetingStore и helper вместе с GUI. Source GUI не установлен в production.

## Безопасный порядок изменения

1. Выполнить `./scripts/verify-install.sh` и сохранить результат.
2. Изменить минимально необходимый source-файл.
3. Проверить Python syntax или Swift build.
4. Использовать только синтетический fixture либо копию реальных данных вне проекта.
5. Если возможно, провести cache-only regression без вызова Gemini.
6. Просмотреть diff и обновить документацию.
7. Получить подтверждение на установку.
8. Запустить installer сначала без `--apply`.
9. После одобрения запустить `./scripts/install-local.sh --apply`.
10. Повторить `./scripts/verify-install.sh` и только затем выполнять полный пользовательский тест.

## Сборка Swift GUI

```bash
./scripts/build-gui.sh
```

Результат: `dist/gemini_meeting_gui`. Скрипт использует установленный Swift toolchain, собирает во временном module cache и проверяет, что результат содержит архитектуру `x86_64`.

Эквивалентная основная команда:

```bash
xcrun swiftc src/ManagedPipelineEvents.swift src/MeetingStatus.swift -o dist/gemini_meeting_gui
```

## Сборка droplet .app

```bash
./scripts/build-app.sh
```

Результат: `dist/Расшифровать встречу.app`. В `src/main.applescript` есть обработчик `open`, поэтому `osacompile` создаёт приложение, принимающее перетаскиваемые файлы.

Скрипт не перезаписывает уже существующий bundle в `dist`: его нужно предварительно переместить или собрать в чистом tree. Это защищает от случайной потери артефакта.

Сборка не исправляет codesign автоматически. Подписание вынесено за пределы baseline и должно рассматриваться отдельным изменением.

## Read-only проверка установленной версии

```bash
./scripts/verify-install.sh
```

Проверяется:

- наличие production-компонентов;
- Python syntax source и installed copies без создания `__pycache__`;
- совпадение Python/Swift source-файлов через `cmp` и SHA-256;
- buildability Swift GUI во временном каталоге;
- архитектура собранного и установленного GUI;
- декомпиляция installed `main.scpt`;
- компиляция source AppleScript;
- совпадение декомпилированного AppleScript с source-файлом с игнорированием пустой строки в конце;
- совпадение installed GUI с `dist/gemini_meeting_gui`, если такой build уже существует.

Скрипт не вызывает Gemini, не читает API key, не открывает записи и не изменяет production.

До установки изменённой source-ветки три проверки совпадения Python/Swift source с установленными копиями ожидаемо показывают hash mismatch. Это фиксирует различие версий, а не ошибку сборки; после согласованной установки хеши должны совпасть.

## Установка production-копий

Предварительный просмотр, ничего не меняет:

```bash
./scripts/install-local.sh
```

Реальная установка разрешается только после отдельного подтверждения:

```bash
./scripts/install-local.sh --apply
```

Перед `--apply` должны существовать оба build-артефакта в `dist/`. Installer создаёт timestamped backup в:

```text
~/.local/share/gemini-meeting-pipeline/backups/source-install_YYYYMMDD_HHMMSS/
```

Затем он устанавливает Python source, Swift source, GUI binary и atomically заменяет Desktop `.app` через staging bundle. Пользовательский словарь, Keychain и cache не затрагиваются.

## Проверка source ↔ installed

После установки ожидаются прямые совпадения:

```text
src/gemini_transcribe_meeting.py
  = ~/.local/bin/gemini_transcribe_meeting.py

src/gemini_make_protocol.py
  = ~/.local/bin/gemini_make_protocol.py

src/MeetingStatus.swift
  = ~/.local/share/gemini-meeting-pipeline/MeetingStatus.swift
```

Для AppleScript сравнивается декомпилированный installed `main.scpt` с `src/main.applescript`; игнорируется только пустая строка, которую `osadecompile` добавляет в конце. Для GUI binary сравнение возможно с `dist/gemini_meeting_gui`, собранным в той же среде; различие до установки нового build показывается как информация, а не ошибка baseline.

## Rollback

1. Закрыть все GUI-процессы обработки встреч.
2. Найти последний `source-install_*` backup.
3. Проверить его содержимое и хеши.
4. Вернуть из него Python-файлы, `MeetingStatus.swift`, GUI binary и `.app` в исходные пути.
5. Запустить `./scripts/verify-install.sh` с checkout исходников, соответствующим возвращаемой версии.

Rollback не должен удалять или изменять пользовательский `vocabulary.txt`, Keychain, cache и каталоги результатов.

Текущие исторические backups, перечисленные в `archive/README.md`, нельзя массово удалять: не каждый из них является полным согласованным release snapshot.
