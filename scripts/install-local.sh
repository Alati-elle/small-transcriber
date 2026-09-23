#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
home_dir=${HOME:?HOME is not set}

source_transcribe="$project_root/src/gemini_transcribe_meeting.py"
source_protocol="$project_root/src/gemini_make_protocol.py"
source_swift="$project_root/src/MeetingStatus.swift"
built_gui="$project_root/dist/gemini_meeting_gui"
built_app="$project_root/dist/Расшифровать встречу.app"

installed_bin="$home_dir/.local/bin"
installed_share="$home_dir/.local/share/gemini-meeting-pipeline"
installed_app="$home_dir/Desktop/Расшифровать встречу.app"
backup_root="$installed_share/backups"

printf '%s\n' 'Planned installation:'
printf '  %s -> %s\n' "$source_transcribe" "$installed_bin/gemini_transcribe_meeting.py"
printf '  %s -> %s\n' "$source_protocol" "$installed_bin/gemini_make_protocol.py"
printf '  %s -> %s\n' "$source_swift" "$installed_share/MeetingStatus.swift"
printf '  %s -> %s\n' "$built_gui" "$installed_bin/gemini_meeting_gui"
printf '  %s -> %s\n' "$built_app" "$installed_app"

if [ "${1:-}" != '--apply' ]; then
    printf '%s\n' 'Dry run only. Pass --apply after explicit approval.'
    exit 0
fi

for path in \
    "$source_transcribe" \
    "$source_protocol" \
    "$source_swift" \
    "$built_gui" \
    "$built_app"
do
    if [ ! -e "$path" ]; then
        printf 'Missing required source/build: %s\n' "$path" >&2
        exit 1
    fi
done

timestamp=$(date '+%Y%m%d_%H%M%S')
backup_dir="$backup_root/source-install_$timestamp"
staged_app="$home_dir/Desktop/.Расшифровать встречу.app.installing_$timestamp"

mkdir -p "$backup_dir" "$installed_bin" "$installed_share"

cp -p "$installed_bin/gemini_transcribe_meeting.py" "$backup_dir/"
cp -p "$installed_bin/gemini_make_protocol.py" "$backup_dir/"
cp -p "$installed_bin/gemini_meeting_gui" "$backup_dir/"
cp -p "$installed_share/MeetingStatus.swift" "$backup_dir/"
ditto "$built_app" "$staged_app"

install -m 0644 "$source_transcribe" "$installed_bin/gemini_transcribe_meeting.py"
install -m 0755 "$source_protocol" "$installed_bin/gemini_make_protocol.py"
install -m 0644 "$source_swift" "$installed_share/MeetingStatus.swift"
install -m 0755 "$built_gui" "$installed_bin/gemini_meeting_gui"

mv "$installed_app" "$backup_dir/Расшифровать встречу.app"
if ! mv "$staged_app" "$installed_app"; then
    mv "$backup_dir/Расшифровать встречу.app" "$installed_app"
    printf '%s\n' 'App installation failed; original app restored.' >&2
    exit 1
fi

printf 'Installed. Backup: %s\n' "$backup_dir"
printf '%s\n' 'Run scripts/verify-install.sh before processing audio.'
