#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$FRONTEND_DIR/build"
QT_CMAKE="${QT_CMAKE:-/path/to/Qt/bin/qt-cmake}"

if command -v cmake >/dev/null 2>&1; then
  CMAKE_BIN="$(command -v cmake)"
elif [[ -x /opt/homebrew/bin/cmake ]]; then
  CMAKE_BIN="/opt/homebrew/bin/cmake"
elif [[ -x "$QT_CMAKE" ]]; then
  CMAKE_BIN="$QT_CMAKE"
else
  echo "CMake was not found. Set QT_CMAKE to your Qt cmake wrapper, for example:" >&2
  echo "  QT_CMAKE=/path/to/Qt/bin/qt-cmake ./scripts/run_frontend.sh" >&2
  exit 1
fi

if [[ -x "$QT_CMAKE" ]]; then
  "$QT_CMAKE" -S "$FRONTEND_DIR" -B "$BUILD_DIR"
else
  "$CMAKE_BIN" -S "$FRONTEND_DIR" -B "$BUILD_DIR"
fi
"$CMAKE_BIN" --build "$BUILD_DIR"

APP="$BUILD_DIR/SearchClient.app/Contents/MacOS/SearchClient"
BIN="$BUILD_DIR/SearchClient"

if [[ -x "$APP" ]]; then
  exec "$APP"
elif [[ -x "$BIN" ]]; then
  exec "$BIN"
else
  echo "Qt frontend built, but SearchClient executable was not found under $BUILD_DIR." >&2
  exit 1
fi
