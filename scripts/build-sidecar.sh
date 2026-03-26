#!/usr/bin/env bash
# build-sidecar.sh — compile the Python FastAPI backend into a standalone
# binary and place it where Tauri expects it.
#
# Prerequisites
#   pip install pyinstaller   (inside your backend venv)
#   Run from the repo root:   ./scripts/build-sidecar.sh
#
# The output binary is placed in:
#   frontend/src-tauri/binaries/gamecut-backend-<target-triple>[.exe]
# which matches Tauri's externalBin naming convention.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
BINARIES_DIR="$REPO_ROOT/frontend/src-tauri/binaries"

# ── Detect Rust target triple (same as the Tauri build target) ────────────────
TARGET_TRIPLE="$(rustc -vV | awk '/host:/ { print $2 }')"
echo "Target triple: $TARGET_TRIPLE"

# ── Build with PyInstaller ────────────────────────────────────────────────────
cd "$BACKEND_DIR"

if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller>=6.0.0
fi

python -m PyInstaller gamecut.spec --distpath dist --workpath build --noconfirm

# ── Copy binary to Tauri's binaries directory ─────────────────────────────────
mkdir -p "$BINARIES_DIR"

if [[ "$OSTYPE" == "msys"* || "$OSTYPE" == "cygwin"* || "$OS" == "Windows_NT" ]]; then
    SRC="$BACKEND_DIR/dist/gamecut-backend.exe"
    DEST="$BINARIES_DIR/gamecut-backend-${TARGET_TRIPLE}.exe"
else
    SRC="$BACKEND_DIR/dist/gamecut-backend"
    DEST="$BINARIES_DIR/gamecut-backend-${TARGET_TRIPLE}"
fi

cp "$SRC" "$DEST"
chmod +x "$DEST"

echo ""
echo "Sidecar binary ready: $DEST"
echo "You can now run:  cd frontend && npm run tauri:build"
