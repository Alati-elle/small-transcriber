#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_file="$project_root/src/main.applescript"
output_dir="$project_root/dist"
output_app="$output_dir/Расшифровать встречу.app"

mkdir -p "$output_dir"

if [ -e "$output_app" ]; then
    printf 'Refusing to overwrite existing build: %s\n' "$output_app" >&2
    exit 1
fi

osacompile -o "$output_app" "$source_file"
osadecompile "$output_app/Contents/Resources/Scripts/main.scpt" >/dev/null
printf 'Built: %s\n' "$output_app"
