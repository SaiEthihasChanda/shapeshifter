#!/bin/bash
# Builds dist/ShapeShifter-<version>-macOS.dmg (Apple Silicon) on a Mac.
#
#   packaging/build_mac.sh [--skip-tests]
#
# Environment:
#   PYTHON             the Python 3.10+ to build with (default: python3)
#   FFMPEG_DIR         a folder with ffmpeg (and ffprobe) to bundle; downloaded when not set
#   MAC_SIGN_IDENTITY  codesign identity (default "-": an ad-hoc signature, which Apple Silicon requires)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/.build-mac"
DIST="$ROOT/dist"
PY="${PYTHON:-python3}"
IDENTITY="${MAC_SIGN_IDENTITY:--}"
step() { printf '\n==> %s\n' "$*"; }

step "Build environment"
mkdir -p "$BUILD"
rm -rf "$BUILD/venv"
"$PY" -m venv "$BUILD/venv"
VPY="$BUILD/venv/bin/python"
"$VPY" -m pip install -q --disable-pip-version-check --upgrade pip
"$VPY" -m pip install -q --disable-pip-version-check "$ROOT" pyinstaller pytest
VERSION="$("$VPY" -c 'import shapeshifter; print(shapeshifter.__version__)')"
echo "    ShapeShifter $VERSION on $("$VPY" -c 'import platform; print(platform.machine(), platform.mac_ver()[0])')"

if [ "${1:-}" != "--skip-tests" ]; then
    step "Tests"
    (cd "$ROOT" && "$VPY" -m pytest -q tests)
fi

step "Freezing ShapeShifter.app"
"$VPY" -c "from pathlib import Path; from shapeshifter.icon import make_icns; make_icns(Path('$BUILD/ShapeShifter.icns'))"
rm -rf "$BUILD/dist" "$BUILD/work"
"$VPY" -m PyInstaller --noconfirm --clean --windowed --name ShapeShifter --icon "$BUILD/ShapeShifter.icns" \
    --osx-bundle-identifier io.github.saiethihaschanda.shapeshifter --target-arch arm64 \
    --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
    --collect-submodules shapeshifter --collect-all pymupdf --collect-all pillow_heif --collect-all py7zr \
    --hidden-import AppKit --hidden-import Foundation --hidden-import Quartz --hidden-import Vision \
    --hidden-import ApplicationServices \
    --exclude-module tkinter --exclude-module pytest --exclude-module pymupdf.layout \
    --exclude-module onnxruntime --exclude-module numpy \
    "$ROOT/packaging/launcher.py"
APP="$BUILD/dist/ShapeShifter.app"
[ -d "$APP" ] || { echo "PyInstaller made no app bundle"; exit 1; }

step "ffmpeg"
FF="$BUILD/ffmpeg"
rm -rf "$FF"
mkdir -p "$FF"
if [ -n "${FFMPEG_DIR:-}" ] && [ -x "$FFMPEG_DIR/ffmpeg" ]; then
    cp "$FFMPEG_DIR/ffmpeg" "$FF/"
    [ -x "$FFMPEG_DIR/ffprobe" ] && cp "$FFMPEG_DIR/ffprobe" "$FF/"
else
    for tool in ffmpeg ffprobe; do
        if curl -fsSL -o "$FF/$tool.zip" "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$tool.zip"; then
            (cd "$FF" && unzip -oq "$tool.zip" && rm -f "$tool.zip")
        else
            echo "    couldn't download $tool"
        fi
    done
fi
FFARG=()
if [ -x "$FF/ffmpeg" ] && file "$FF/ffmpeg" | grep -q arm64; then
    mkdir -p "$APP/Contents/MacOS/ffmpeg" "$APP/Contents/Resources/ffmpeg"
    cp "$FF"/ff* "$APP/Contents/MacOS/ffmpeg/"
    chmod +x "$APP/Contents/MacOS/ffmpeg/"*
    "$APP/Contents/MacOS/ffmpeg/ffmpeg" -hide_banner -version | head -1
    cat > "$APP/Contents/Resources/ffmpeg/SOURCE.txt" <<'TXT'
ShapeShifter.app/Contents/MacOS/ffmpeg contains an unmodified static FFmpeg build for Apple Silicon.
FFmpeg is a separate program that ShapeShifter runs; it is not part of ShapeShifter itself.
Licence: GPL v3 (the build enables GPL components). https://ffmpeg.org/legal.html
Source code: https://ffmpeg.org/download.html#get-sources
Build used: https://ffmpeg.martin-riedl.de (the matching source is linked from that page)
You may replace the ffmpeg program with any other ffmpeg build.
TXT
    FFARG=(--ffmpeg-dir "$APP/Contents/MacOS/ffmpeg")
else
    echo "    no Apple Silicon ffmpeg: building without it (audio and video need ffmpeg from Homebrew)"
fi

step "Licences and Info.plist"
if ! "$VPY" "$ROOT/packaging/collect_licenses.py" --exe "$APP/Contents/MacOS/ShapeShifter" --out "$BUILD/licenses" \
        --version "$VERSION" "${FFARG[@]+"${FFARG[@]}"}"; then
    echo "    warning: couldn't list the bundled packages' licences"
fi
"$VPY" "$ROOT/packaging/mac_bundle.py" "$APP" "$VERSION" "$BUILD/licenses"

step "Signing ($IDENTITY)"
codesign --force --deep --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"
echo "    signature ok"

step "Self test of the built app"
"$APP/Contents/MacOS/ShapeShifter" selftest

step "Disk image"
mkdir -p "$DIST"
STAGE="$BUILD/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/ShapeShifter.app"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/Read me first.txt" <<'TXT'
Installing ShapeShifter
=======================

1. Drag ShapeShifter onto Applications.
2. Open ShapeShifter from Applications.

The first time, macOS says it can't check ShapeShifter for malicious software (the app is not
notarised by Apple). To open it anyway:

  * Open System Settings > Privacy & Security, scroll down, and click "Open Anyway" next to
    ShapeShifter. Confirm with your password. You only need to do this once.

  * Or, in Terminal:   xattr -dr com.apple.quarantine /Applications/ShapeShifter.app

ShapeShifter then lives in the menu bar. It adds "Convert with ShapeShifter" to Finder's
right-click menu (Quick Actions) and asks for two permissions when you use the hotkeys:
Accessibility (to see the hotkey) and Automation > Finder (to see which files are selected).

Updates install themselves from the menu bar: ShapeShifter > Check for updates.
TXT
DMG="$DIST/ShapeShifter-$VERSION-macOS.dmg"
rm -f "$DMG"
hdiutil create -volname "ShapeShifter $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
cp "$DMG" "$DIST/ShapeShifter-macOS.dmg"
SIZE=$(du -m "$DMG" | cut -f1)
echo
echo "Built $DMG ($SIZE MB)"
shasum -a 256 "$DMG" | sed 's/^/    /'
