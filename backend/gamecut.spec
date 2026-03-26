# PyInstaller spec for the GameCut backend sidecar binary.
# Usage:  cd backend && pyinstaller gamecut.spec
# Output: dist/gamecut-backend  (or gamecut-backend.exe on Windows)

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle the presets config alongside the binary
        ("presets.json", "."),
        # Bundle the modules package (Python source is needed at runtime
        # because PyInstaller's analysis may miss dynamic imports inside
        # librosa / soundfile).
        ("modules", "modules"),
    ],
    hiddenimports=[
        # uvicorn internals not always auto-detected
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # FastAPI / Starlette internals
        "fastapi",
        "starlette",
        "multipart",
        # Audio / video analysis stack
        "librosa",
        "soundfile",
        "scipy.signal",
        "scipy.fft",
        "numpy",
        "cv2",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="gamecut-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Keep console=True so Tauri can capture stdout/stderr for debugging.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
