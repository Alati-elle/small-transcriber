#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
dev_runtime="$HOME/.local/share/gemini-meeting-pipeline/dev-managed"
dev_app="$HOME/Desktop/Расшифровать встречу DEV.app"
built_app="$project_root/dist/Расшифровать встречу DEV.app"
files="meeting_config.py meeting_pipeline.py meeting_store.py gemini_telemetry.py gemini_transcribe_meeting.py gemini_make_protocol.py"

printf 'DEV runtime: %s\nDEV app: %s\n' "$dev_runtime" "$dev_app"
printf '%s\n' 'Production paths and DEV storage/index.sqlite3 are not installation targets.'
if [ "${1:-}" != '--apply' ]; then
    printf '%s\n' 'Dry run only.'
    exit 0
fi

test -d "$dev_runtime"
test -d "$dev_app"
test -d "$built_app"
test -f "$project_root/dist/gemini_meeting_gui_dev"
for name in $files; do test -f "$project_root/src/$name"; done

stamp=$(date '+%Y%m%d_%H%M%S')
backup="$dev_runtime/backups/app-shell_$stamp"
stage="$dev_runtime/.app-shell-staging_$stamp"
staged_app="$HOME/Desktop/.Расшифровать встречу DEV.app.staging_$stamp"
mkdir -m 0700 -p "$backup" "$stage"
for name in $files; do
    if [ -f "$dev_runtime/$name" ]; then cp -p "$dev_runtime/$name" "$backup/$name"; fi
    install -m 0644 "$project_root/src/$name" "$stage/$name"
done
if [ -f "$dev_runtime/gemini_meeting_gui_dev" ]; then
    cp -p "$dev_runtime/gemini_meeting_gui_dev" "$backup/gemini_meeting_gui_dev"
fi
install -m 0755 "$project_root/dist/gemini_meeting_gui_dev" "$stage/gemini_meeting_gui_dev"
ditto "$built_app" "$staged_app"
for name in $files gemini_meeting_gui_dev; do mv -f "$stage/$name" "$dev_runtime/$name"; done
rmdir "$stage"
mv "$dev_app" "$backup/old-app-original"
if ! mv "$staged_app" "$dev_app"; then
    mv "$backup/old-app-original" "$dev_app"
    for name in $files gemini_meeting_gui_dev; do
        if [ -f "$backup/$name" ]; then cp -p "$backup/$name" "$dev_runtime/$name"; fi
    done
    if [ ! -f "$backup/meeting_config.py" ]; then rm -f "$dev_runtime/meeting_config.py"; fi
    printf '%s\n' 'DEV app replacement failed; original app restored.' >&2
    exit 1
fi
printf 'DEV installed. Backup: %s\n' "$backup"
