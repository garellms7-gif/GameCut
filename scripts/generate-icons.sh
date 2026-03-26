#!/usr/bin/env bash
# generate-icons.sh — create all Tauri icon variants from a single source PNG.
#
# Usage:
#   ./scripts/generate-icons.sh [path-to-1024x1024-source.png]
#
# If no source image is provided, a placeholder indigo square is generated.
# Requires:  @tauri-apps/cli  (installed via npm in frontend/)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICONS_DIR="$REPO_ROOT/frontend/src-tauri/icons"
FRONTEND_DIR="$REPO_ROOT/frontend"

SOURCE_PNG="${1:-}"

# ── Generate placeholder source if none provided ─────────────────────────────
if [[ -z "$SOURCE_PNG" ]]; then
    TMP_PNG="$(mktemp /tmp/gamecut-icon-XXXX.png)"
    python3 - "$TMP_PNG" <<'PYEOF'
import struct, zlib, sys

def make_png(w, h, rgb=(99, 102, 241)):
    r, g, b = rgb
    def ck(t, d):
        raw = t + d
        return struct.pack('>I', len(d)) + raw + struct.pack('>I', zlib.crc32(raw) & 0xffffffff)
    IHDR = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    sl = b''.join(b'\x00' + bytes([r, g, b]) * w for _ in range(h))
    return b'\x89PNG\r\n\x1a\n' + ck(b'IHDR', IHDR) + ck(b'IDAT', zlib.compress(sl)) + ck(b'IEND', b'')

open(sys.argv[1], 'wb').write(make_png(1024, 1024))
print(f"Created placeholder icon: {sys.argv[1]}")
PYEOF
    SOURCE_PNG="$TMP_PNG"
fi

# ── Run tauri icon generator ──────────────────────────────────────────────────
cd "$FRONTEND_DIR"
npx @tauri-apps/cli icon "$SOURCE_PNG" --output src-tauri/icons

echo ""
echo "Icons written to $ICONS_DIR"
echo "Replace src-tauri/icons/ with your real brand icons before shipping."
