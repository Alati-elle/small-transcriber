#!/bin/sh
set -u

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
home_dir=${HOME:?HOME is not set}

source_transcribe="$project_root/src/gemini_transcribe_meeting.py"
source_protocol="$project_root/src/gemini_make_protocol.py"
source_swift="$project_root/src/MeetingStatus.swift"
source_applescript="$project_root/src/main.applescript"

installed_transcribe="$home_dir/.local/bin/gemini_transcribe_meeting.py"
installed_protocol="$home_dir/.local/bin/gemini_make_protocol.py"
installed_gui="$home_dir/.local/bin/gemini_meeting_gui"
installed_swift="$home_dir/.local/share/gemini-meeting-pipeline/MeetingStatus.swift"
installed_app="$home_dir/Desktop/Расшифровать встречу.app"
installed_scpt="$installed_app/Contents/Resources/Scripts/main.scpt"

check_tmp=$(mktemp -d "/private/tmp/small-transcriber-verify.XXXXXX")
passed=0
failed=0

cleanup() {
    rm -rf "$check_tmp"
}
trap cleanup EXIT HUP INT TERM

pass() {
    passed=$((passed + 1))
    printf 'OK   %s\n' "$1"
}

fail() {
    failed=$((failed + 1))
    printf 'FAIL %s\n' "$1" >&2
}

for path in \
    "$source_transcribe" \
    "$source_protocol" \
    "$source_swift" \
    "$source_applescript" \
    "$installed_transcribe" \
    "$installed_protocol" \
    "$installed_gui" \
    "$installed_swift" \
    "$installed_app" \
    "$installed_scpt"
do
    if [ -e "$path" ]; then
        pass "exists: $path"
    else
        fail "missing: $path"
    fi
done

if /usr/bin/python3 - "$source_transcribe" "$source_protocol" "$installed_transcribe" "$installed_protocol" <<'PY'
import ast
import sys
from pathlib import Path

for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
then
    pass 'Python syntax: source and installed'
else
    fail 'Python syntax: source and installed'
fi

for pair in \
    "$source_transcribe|$installed_transcribe|transcription source hash" \
    "$source_protocol|$installed_protocol|protocol source hash" \
    "$source_swift|$installed_swift|Swift source hash"
do
    left=${pair%%|*}
    rest=${pair#*|}
    right=${rest%%|*}
    label=${rest#*|}

    if cmp -s "$left" "$right"; then
        pass "$label"
    else
        fail "$label"
    fi
done

if osadecompile "$installed_scpt" >"$check_tmp/installed.applescript"; then
    pass 'installed AppleScript decompiles'
else
    fail 'installed AppleScript decompiles'
fi

if osacompile -o "$check_tmp/source-main.scpt" "$source_applescript"; then
    pass 'source AppleScript compiles'
else
    fail 'source AppleScript compiles'
fi

if diff -bB "$source_applescript" "$check_tmp/installed.applescript" >/dev/null 2>&1; then
    pass 'AppleScript source matches installed decompilation'
else
    fail 'AppleScript source matches installed decompilation'
    diff -u "$source_applescript" "$check_tmp/installed.applescript" >&2 || true
fi

mkdir -p "$check_tmp/module-cache"
if xcrun swiftc \
    -module-cache-path "$check_tmp/module-cache" \
    "$source_swift" \
    -o "$check_tmp/gemini_meeting_gui"
then
    pass 'Swift source builds'
else
    fail 'Swift source builds'
fi

if file "$check_tmp/gemini_meeting_gui" 2>/dev/null | grep -q 'x86_64'; then
    pass 'built GUI contains x86_64'
else
    fail 'built GUI contains x86_64'
fi

if file "$installed_gui" 2>/dev/null | grep -q 'x86_64'; then
    pass 'installed GUI contains x86_64'
else
    fail 'installed GUI contains x86_64'
fi

if [ -f "$project_root/dist/gemini_meeting_gui" ]; then
    if cmp -s "$project_root/dist/gemini_meeting_gui" "$installed_gui"; then
        pass 'built GUI hash matches installed binary'
    else
        printf 'INFO built GUI differs from installed binary; expected before installation\n'
    fi
else
    printf 'INFO dist/gemini_meeting_gui is absent; binary hash comparison skipped\n'
fi

printf '\nSHA-256 source and installed files:\n'
shasum -a 256 \
    "$source_transcribe" "$installed_transcribe" \
    "$source_protocol" "$installed_protocol" \
    "$source_swift" "$installed_swift" \
    "$source_applescript" "$installed_scpt" \
    "$installed_gui"

printf '\nResult: %s passed, %s failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
