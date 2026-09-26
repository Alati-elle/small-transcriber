#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app="$project_root/dist/Расшифровать встречу DEV.app"
runtime="$project_root/dist/gemini_meeting_gui_dev"
build_tmp=$(mktemp -d /private/tmp/small-transcriber-dev.XXXXXX)
trap 'rm -rf "$build_tmp"' EXIT HUP INT TERM

if [ -e "$app" ]; then
    printf 'Refusing to overwrite existing build: %s\n' "$app" >&2
    exit 1
fi

xcrun swiftc -DDEBUG -DDEV -module-cache-path "$build_tmp/module-cache" \
    "$project_root/src/ManagedPipelineEvents.swift" \
    "$project_root/src/MeetingStatus.swift" \
    "$project_root/src/AppShell.swift" \
    "$project_root/src/SettingsWindow.swift" -o "$runtime"
file "$runtime" | grep -q 'x86_64'
mkdir -p "$app/Contents/MacOS"
cp "$runtime" "$app/Contents/MacOS/gemini_meeting_gui_dev"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleDevelopmentRegion</key><string>ru</string>
<key>CFBundleExecutable</key><string>gemini_meeting_gui_dev</string>
<key>CFBundleIdentifier</key><string>local.small-transcriber.dev</string>
<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
<key>CFBundleName</key><string>Расшифровать встречу DEV</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleDocumentTypes</key><array><dict>
<key>CFBundleTypeName</key><string>Audio</string>
<key>CFBundleTypeRole</key><string>Viewer</string>
<key>LSHandlerRank</key><string>Alternate</string>
<key>CFBundleTypeExtensions</key><array>
<string>m4a</string><string>mp3</string><string>wav</string><string>aac</string>
<string>flac</string><string>ogg</string><string>opus</string><string>mp4</string>
<string>mov</string><string>webm</string>
</array></dict></array>
</dict></plist>
PLIST
plutil -lint "$app/Contents/Info.plist"
printf 'Built: %s\n' "$app"
