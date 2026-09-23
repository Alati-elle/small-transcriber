#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_file="$project_root/src/MeetingStatus.swift"
output_dir="$project_root/dist"
output_file="$output_dir/gemini_meeting_gui"
build_tmp=$(mktemp -d "/private/tmp/small-transcriber-swift.XXXXXX")

cleanup() {
    rm -rf "$build_tmp"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$output_dir" "$build_tmp/module-cache"

xcrun swiftc \
    -module-cache-path "$build_tmp/module-cache" \
    "$source_file" \
    -o "$output_file"

file "$output_file" | grep -q 'x86_64'
printf 'Built: %s\n' "$output_file"
