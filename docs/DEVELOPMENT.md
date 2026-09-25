# Разработка и установка

## Source of truth

Редактировать следует только:

- `src/gemini_transcribe_meeting.py`;
- `src/gemini_make_protocol.py`;
- `src/MeetingStatus.swift`;
- `src/main.applescript`.

Файлы в `~/.local/bin`, `~/.local/share/gemini-meeting-pipeline` и `.app` на Desktop являются установленными копиями. Рабочий `vocabulary.txt`, Keychain, cache и результаты встреч не являются частью source tree.

## Запуск transcriber из source

```bash
python3 src/gemini_transcribe_meeting.py AUDIO
python3 src/gemini_transcribe_meeting.py AUDIO \
  --output-dir DIR \
  --cache-dir DIR \
  --expected-source-sha256 HEX
```

Первый вызов сохраняет legacy layout рядом с source и auto-open. Во втором вызове `--output-dir` включает managed mode, `--cache-dir` задаёт каталог chunks/cache, а `--expected-source-sha256` передаёт 64-символьный hex SHA-256 source. Все три опции обязательны вместе. Managed-вызов пока является низкоуровневым building block для будущего orchestrator, а не пользовательским production workflow. Для тестов используйте только синтетические файлы; эти команды без mock вызывают ffmpeg и Gemini.

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
xcrun swiftc src/MeetingStatus.swift -o dist/gemini_meeting_gui
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
