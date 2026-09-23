# Backup manifest

Backup-файлы остаются в исходных местах. Этот каталог содержит только manifest, созданный 23 сентября 2026 года.

Описание изменений основано на имени файла и подтверждённых diff. Формулировка «точное изменение не установлено» намеренна: история не выдумывается.

Для отдельных файлов указан обычный SHA-256 содержимого. Для `.app` указан deterministic tree SHA-256: файлы сортируются по относительному POSIX-пути, затем хешируется последовательность `relative_path + NUL + binary_file_sha256 + NUL`. Метаданные каталогов, mtime и extended attributes в tree digest не входят.

## Python и AppleScript backups

| Путь | Дата изменения | Примерное назначение | SHA-256 | Статус |
|---|---:|---|---|---|
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_143522` | 2026-09-15 14:14:33 +0300 | Исторический снимок; точное изменение не установлено | `9c95de15b512ebeaa77149d21e187fb2f5846fb6d74abbd1a38bb00499246101` | Оставить на месте |
| `~/Desktop/Расшифровать встречу.app/Contents/Resources/Scripts/main.scpt.backup_before_full_pipeline_20260916_154313` | 2026-09-15 14:24:02 +0300 | AppleScript до запуска полного transcript → protocol pipeline | `2b05a4b577969c3140c63f34985d2e4d79cb0730311c9926a54b0c425f096d58` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_143640` | 2026-09-15 14:35:22 +0300 | Исторический снимок; точное изменение не установлено | `8504bf0e3a193b17e704a4134f9d61cb8b06c163e8618386a67efd90901e30a3` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_144347` | 2026-09-15 14:36:40 +0300 | Исторический снимок; точное изменение не установлено | `4804d3fa62dea54175131fd5f181d12919443be691632dd6bffcb9c2ded673c7` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_144736` | 2026-09-15 14:43:47 +0300 | Исторический снимок; точное изменение не установлено | `b446451bb3807542f5aca704acf20a1fa24e3502f7f06ece6659df4654f09ad4` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_150903` | 2026-09-15 15:09:03 +0300 | Исторический снимок; точное изменение не установлено | `9f68f54452d8872da49543ebca96b3c42f601f8fbd93d3319655755f6160302a` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_152422` | 2026-09-15 15:24:22 +0300 | Исторический снимок; точное изменение не установлено | `7d2624a1bc05c2df6ebb1c186d7696ee3af24b2a2d7cb812f83e239894820eaa` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_163352` | 2026-09-15 16:33:52 +0300 | Исторический снимок; точное изменение не установлено | `3a8bfcf8e42607d006608b141e56e343d1abc38944b94ef615cafd46abf3a3d1` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_165503` | 2026-09-15 16:55:03 +0300 | Исторический снимок; точное изменение не установлено | `6dc021e109dfc209b3c86a0962624d4d08b1230fa37fdfb7e29001b92421b24f` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_165503` | 2026-09-15 16:55:03 +0300 | Исторический снимок; точное изменение не установлено | `1f96ba9b5cc85b6f15d07c6051dbffbe921d259ae935c9f5fbc57c9f80f82354` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_171809` | 2026-09-15 17:18:09 +0300 | Исторический снимок; точное изменение не установлено | `68e8762451f6995f104962441e9767b3daa92fb20d7787c6a4f699ab5f0dbdab` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_20260915_171809` | 2026-09-15 17:18:09 +0300 | Исторический снимок; точное изменение не установлено | `affb475d3867fd5c062d9aa17732d9e9a3ace0f8c1f0c2b2189d5ffc7382ba85` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.backup_before_parser_fix_20260916_153221` | 2026-09-15 17:18:09 +0300 | Снимок перед исправлением parser | `ca9dc0eadbe9187323af468f1fe95556943aca32308f065fe75876a9d4ede4ae` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_172855` | 2026-09-15 17:28:55 +0300 | Исторический снимок; точное изменение не установлено | `a359152c51398cd38932f922cc278f8e741a204f594ea8105ebaaea470eea9b8` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_173041` | 2026-09-15 17:30:41 +0300 | Дубликат предыдущего содержимого | `a359152c51398cd38932f922cc278f8e741a204f594ea8105ebaaea470eea9b8` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_181349` | 2026-09-15 18:13:49 +0300 | Исторический снимок; точное изменение не установлено | `64a0f1d5c893bf20b9a5e7df62b31b111cf7eb047f900b8135aff3262440837d` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260915_182949` | 2026-09-15 18:29:49 +0300 | Исторический снимок; точное изменение не установлено | `b91c99be3d93725a358dac30d331ba0b0347e1706a2aeb75caba5b17249ab161` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260916_141834` | 2026-09-15 18:29:49 +0300 | Исторический снимок | `ebbdfe13f943838d0bfcb2b2d32c6062e33940bcabcb63af427f9e3e8103ce0b` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260916_141632` | 2026-09-16 14:16:32 +0300 | Дубликат предыдущего содержимого | `ebbdfe13f943838d0bfcb2b2d32c6062e33940bcabcb63af427f9e3e8103ce0b` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260916_141935` | 2026-09-16 14:18:34 +0300 | Исторический снимок; точное изменение не установлено | `1410fb5fadca53f950b157aa49a0d128a0db431fd99a38253d6640eb3389f92f` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260916_142329` | 2026-09-16 14:23:29 +0300 | Исторический снимок; точное изменение не установлено | `706a972f6aef8a95df1d5a5b08f626fc5cf279181326e518a2dac3383f14759a` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_20260916_142447` | 2026-09-16 14:24:47 +0300 | Исторический снимок; точное изменение не установлено | `209ce21517bc1cfba8a9bca483b3721a227a1a73b9b7cc728251196281f2717b` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_findcut_20260916_145145` | 2026-09-16 14:24:47 +0300 | Этап изменения поиска границы merge | `9184c9ad6bbe5b4e55939f3dbcbe6feeccaeac16309b2c92c148d6a20924d760` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_before_v21_final_20260916_152114` | 2026-09-16 14:51:45 +0300 | Снимок перед финализацией Merge V2.1 | `9a9c0729b13db187ef4e679adbebc7a0efa41da1cf21793cdc32750454230065` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_before_verified_v21_20260916_150739` | 2026-09-16 14:51:45 +0300 | То же содержимое, сохранённое перед verified V2.1 | `9a9c0729b13db187ef4e679adbebc7a0efa41da1cf21793cdc32750454230065` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.backup_merge_v21_20260916_150424` | 2026-09-16 14:51:45 +0300 | То же содержимое этапа Merge V2.1 | `9a9c0729b13db187ef4e679adbebc7a0efa41da1cf21793cdc32750454230065` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.failed_merge_v21_20260916_150424` | 2026-09-16 15:04:24 +0300 | Неуспешный экспериментальный вариант; точная причина требует отдельного diff | `034b939f7914d2a51015dd0d8a3ea6eca7e2902319f02cdfb838fc0e8044f8ae` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.failed_verified_v21_20260916_150739` | 2026-09-16 15:07:39 +0300 | Неуспешный экспериментальный вариант; точная причина требует отдельного diff | `bb5e73fa1b423e500fab386954b5306c768a15e7601f53839e0025eea87ec23a` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.v21_overlap_param_20260916_151339.py` | 2026-09-16 15:13:39 +0300 | Merge V2.1 с явным overlap-параметром | `7e10688bba73ffc73ed0c2dbfc612965706d104c92f36b1a5b87ab22dc690ae0` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.v21_full_param_20260916_151659.py` | 2026-09-16 15:19:38 +0300 | Merge V2.1 с полной параметризацией | `0cf19103aed28b8d73ad83c3d42c03f1dcd0d63eb17f4a235720d2d75dccfe82` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.bak_ipv4_20260923_183519` | 2026-09-23 18:35:19 +0300 | Снимок перед добавлением принудительного curl IPv4 | `0cf19103aed28b8d73ad83c3d42c03f1dcd0d63eb17f4a235720d2d75dccfe82` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.bak_ipv4_20260923_183519` | 2026-09-23 18:35:19 +0300 | Снимок перед добавлением принудительного curl IPv4 | `4701eb16a4c9766e20572bfed918311b26364c5e3d9b075a77ef1e86d09257b5` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.bak_timeout_20260923_184310` | 2026-09-23 18:43:10 +0300 | Снимок перед добавлением curl timeout | `cfff06b6655fe46c4ddac28ce8f2ba23fec9242699d77caa0801405c72a0fd31` | Оставить на месте |
| `~/.local/bin/gemini_transcribe_meeting.py.bak_timestamps_20260923_184538` | 2026-09-23 18:45:38 +0300 | Снимок перед добавлением timestamp в логи | `eeb002bab564b75fbb5e893e463f39adc65ee518b670d3e3cc05acf89ad6abb8` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.bak_timestamps_20260923_184538` | 2026-09-23 18:45:38 +0300 | Снимок перед добавлением timestamp в логи | `62f65ff4860e7127f9c5c32bd3dd9ae917379da9fb0b5efc17b9c865c6815799` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.bak_retry_20260923_191631` | 2026-09-23 19:16:31 +0300 | Снимок перед изменением retry 503 | `e2a9e2672bea00ef36db87226fe097fbf87d939d1890637e7023641797814e09` | Оставить на месте |
| `~/.local/bin/gemini_make_protocol.py.bak_retry2_20260923_191718` | 2026-09-23 19:17:19 +0300 | То же содержимое перед вторым изменением retry 503 | `e2a9e2672bea00ef36db87226fe097fbf87d939d1890637e7023641797814e09` | Оставить на месте |

## App bundle backups

| Путь | Дата изменения | Примерное назначение | Tree SHA-256 | Статус |
|---|---:|---|---|---|
| `~/.local/share/gemini-meeting-pipeline/backups/Расшифровать встречу.app.bak_20260923_184538` | 2026-09-23 18:45:38 +0300 | Снимок перед последующими изменениями pipeline 23 сентября | `653a50ca4e8425ee88f9f7816d131078e3ab57b0e970b7a3b68e0ff136eba2b3` | Оставить на месте |
| `~/.local/share/gemini-meeting-pipeline/backups/Расшифровать встречу.app.bak_quit_20260923_190827` | 2026-09-23 19:08:27 +0300 | Снимок перед изменением quit-поведения | `5d3fcdad15187c93783b32c858d492f1ce746538501c61303297079557f405b5` | Оставить на месте |
| `~/.local/share/gemini-meeting-pipeline/backups/Расшифровать встречу.app.bak_nomodal_20260923_191501` | 2026-09-23 19:15:01 +0300 | Снимок перед изменением modal-поведения | `78613107cabfc60e515b96367b3e315a03325be37488d26e53cd5e934c34fa56` | Оставить на месте |
| `~/.local/share/gemini-meeting-pipeline/backups/Расшифровать встречу.app.bak_cleanup_20260923_191631` | 2026-09-23 19:16:31 +0300 | Снимок перед корректировкой завершения/cleanup | `6d57f1a6d42920aa6fcec6b7ca2fdb0abdc661ef8e223fba6a286f70bc190746` | Оставить на месте |
| `~/.local/share/gemini-meeting-pipeline/backups/Расшифровать встречу.app.bak_cleanup2_20260923_191718` | 2026-09-23 19:17:19 +0300 | Дубликат предыдущего bundle перед второй корректировкой cleanup | `6d57f1a6d42920aa6fcec6b7ca2fdb0abdc661ef8e223fba6a286f70bc190746` | Оставить на месте |
| `~/.local/share/gemini-meeting-pipeline/backups/Расшифровать встречу.app.bak_gui_20260923_192352` | 2026-09-23 19:23:52 +0300 | Снимок приложения перед переходом launcher на Swift GUI | `296717c9589b894b719e39afe3b9456b91bf2c3a7cf393c2c0ef30e94cc7fe77` | Оставить на месте |
