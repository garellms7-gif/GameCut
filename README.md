# GameCut

Auto-detect **dead zones** (boring, cut-worthy content) and **hype moments** (highlight-worthy clips) in gameplay footage. Exports a merged Edit Decision List (EDL) in JSON, CSV, and DaVinci Resolve-compatible `.edl` format.

Run as a **web app** (Next.js + FastAPI) or as a **fully-local desktop app** (Tauri `.msi` / `.dmg` — no server required).

---

## Architecture

```
GameCut/
├── backend/                  # FastAPI Python backend
│   ├── main.py               # /analyze endpoint
│   ├── modules/
│   │   ├── dead_zone.py      # Audio silence + static frame detection
│   │   ├── hype_moment.py    # Volume spikes, pitch elevation, speech rate
│   │   └── edl_export.py     # Merges results → JSON / CSV / EDL
│   ├── presets.json          # Per-game threshold configurations
│   ├── requirements.txt
│   └── test_detection.py     # CLI test script
└── frontend/                 # Next.js 14 + Tailwind frontend
    ├── app/
    │   ├── page.tsx           # Upload screen
    │   ├── processing/        # Live progress screen
    │   └── results/           # Color-coded timeline + export
    ├── components/
    │   └── Timeline.tsx       # Visual timeline bar
    └── lib/
        ├── api.ts             # Backend API calls
        └── types.ts           # Shared TypeScript types
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10+ |
| Node.js | 18+ |
| ffmpeg | Any recent version |

### Install ffmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (via Chocolatey)
choco install ffmpeg
```

---

## Desktop App (Tauri)

GameCut can be packaged as a native desktop application that ships the Python
backend as a sidecar binary — no Python installation required on the end-user
machine.

### Additional Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Rust | 1.77+ | Tauri core |
| Cargo | bundled with Rust | Tauri build |
| Node.js | 18+ | Frontend build |
| PyInstaller | 6+ | Backend binary |
| ffmpeg | any recent | Runtime (must be on PATH) |

Install Rust: https://rustup.rs

### 1. Generate app icons

```bash
./scripts/generate-icons.sh
# Optionally pass your own 1024×1024 PNG:
# ./scripts/generate-icons.sh path/to/icon.png
```

Replace `frontend/src-tauri/icons/` with real brand artwork before shipping.

### 2. Build the Python backend sidecar

```bash
# Make sure you are inside the backend virtual environment
cd backend
pip install -r requirements.txt
pip install pyinstaller>=6.0.0

cd ..
./scripts/build-sidecar.sh
```

This compiles `backend/main.py` with PyInstaller and copies the output to
`frontend/src-tauri/binaries/gamecut-backend-<target-triple>`.

> **ffmpeg** is NOT bundled — it must be present on the end-user's PATH.
> For a fully self-contained installer, consider adding an ffmpeg static
> binary to `src-tauri/binaries/` and adjusting `tauri.conf.json`
> `externalBin` accordingly.

### 3. Build the installer

```bash
cd frontend
npm install
npm run tauri:build
```

Tauri runs `npm run build:tauri` (which sets `TAURI_BUILD=1` and generates a
static Next.js export), then compiles the Rust shell, and finally bundles
everything.

Output artifacts:

| Platform | Installer | Location |
|----------|-----------|----------|
| Windows  | `.msi`    | `frontend/src-tauri/target/release/bundle/msi/` |
| macOS    | `.dmg`    | `frontend/src-tauri/target/release/bundle/dmg/` |
| macOS    | `.app`    | `frontend/src-tauri/target/release/bundle/macos/` |

### 4. Development mode (hot reload)

In Tauri dev mode the app loads the Next.js dev server and spawns the backend
sidecar automatically:

```bash
# Terminal 1 — backend (the sidecar is also spawned by Tauri, but running it
#              separately gives you cleaner logs during development)
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2 — Tauri dev
cd frontend && npm run tauri:dev
```

> The sidecar binary must exist in `src-tauri/binaries/` even during
> `tauri dev`.  Run `./scripts/build-sidecar.sh` at least once first.

### File dialog

In the desktop app the drag-and-drop upload area is replaced by the OS-native
file picker.  The selected file path is passed directly to the backend —
no upload round-trip for large video files.

---

## Backend Setup

```bash
cd GameCut/backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the API server (default: http://localhost:8000)
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Frontend Setup

```bash
cd GameCut/frontend

# Install dependencies
npm install

# Start dev server (default: http://localhost:3000)
npm run dev
```

Open `http://localhost:3000` in your browser.

> The Next.js dev server proxies `/api/*` requests to the FastAPI backend at `localhost:8000` automatically via `next.config.js`.

---

## Usage

### Web UI

1. Open `http://localhost:3000`
2. Drag & drop a gameplay video (MP4, MOV, MKV, AVI)
3. Select your **game preset** (FPS, RPG, Open World, Fighting, Platformer)
4. Adjust the **sensitivity sliders** if needed
5. Click **Analyze Video**
6. Review the color-coded timeline:
   - 🔴 **Red** = Dead zone (cut this)
   - 🟡 **Yellow** = Keep (neutral content)
   - 🟢 **Green** = Highlight moment
7. Export as **JSON**, **CSV**, or **DaVinci Resolve EDL**

### CLI Test Script

```bash
cd GameCut/backend
source venv/bin/activate

# Basic usage
python test_detection.py /path/to/gameplay.mp4

# With options
python test_detection.py /path/to/gameplay.mp4 --preset rpg --dead-sensitivity 0.8 --hype-sensitivity 1.2
```

Output files are written to the current directory:
- `<filename>_gamecut.json`
- `<filename>_gamecut.csv`
- `<filename>_gamecut.edl`

### Direct API

```bash
# Analyze a video
curl -X POST http://localhost:8000/analyze \
  -F "file=@gameplay.mp4" \
  -F "game_preset=fps" \
  -F "dead_zone_sensitivity=1.0" \
  -F "hype_sensitivity=1.0" \
  -F "export_format=all" \
  | python -m json.tool

# Get available presets
curl http://localhost:8000/presets
```

---

## Game Presets

| Preset | Silence Threshold | Dead Zone Min | Notes |
|--------|------------------|---------------|-------|
| `fps` | −35 dB | 1.5s | Aggressive cutting for fast-paced action |
| `open_world` | −45 dB | 4.0s | Tolerates quiet exploration |
| `rpg` | −50 dB | 6.0s | Preserves narrative silence |
| `fighting` | −30 dB | 1.0s | Very tight; loud reactions matter |
| `platformer` | −40 dB | 3.0s | Balanced general-purpose |

Presets are configurable in `backend/presets.json`.

---

## How It Works

### Dead Zone Detector (`modules/dead_zone.py`)

1. **Audio silence**: Extracts mono audio with ffmpeg, computes RMS energy via librosa. Frames below the dB threshold are flagged as silent.
2. **Static frames**: Samples video at 10 fps with OpenCV, computes mean absolute difference between consecutive frames. Low-diff frames are flagged as static.
3. **Intersection**: Only frames that are *both* silent and static are labelled as dead zones. Confidence = overlap ratio of the two signals.

### Hype Moment Detector (`modules/hype_moment.py`)

Three signals are computed and combined into a **composite hype score**:

| Signal | Weight | Method |
|--------|--------|--------|
| Volume spike | 45% | RMS above rolling 5s baseline |
| Pitch elevation | 30% | librosa `pyin` F0 above rolling baseline |
| Speech rate | 25% | Onset density in 2s sliding window |

Windows where composite score exceeds the 75th percentile are returned as highlights.

### EDL Exporter (`modules/edl_export.py`)

Merges dead zones and highlights into a flat timeline:
- Highlights **override** dead zones on overlap
- Gaps become **keep** segments
- Outputs CMX 3600-compatible `.edl` for DaVinci Resolve

---

## CapCut Export

The **Export for CapCut** button downloads a `draft_content.json` file containing
colored text markers placed at every detected HIGHLIGHT and CUT (dead zone)
timestamp on your video's timeline.

| Marker color | Meaning |
|---|---|
| Green `#00C853` | Highlight moment — keep and feature |
| Red `#FF3D3D` | Dead zone — consider cutting |
| Orange `#FF8C00` | Struggle zone — montage candidate |

Each marker is visible for **1.5 seconds** and labelled with the segment type
and timestamp (e.g. `▶ HIGHLIGHT  01:23`).

### How to use

The exported `draft_content.json` creates a **standalone CapCut project** that
contains only the marker track.  Open it in CapCut, then use it as a reference
while you edit your main project.

### Project folder locations

CapCut stores projects inside a folder named with a unique project ID.
Place `draft_content.json` inside a **new** project folder you create manually,
or replace the `draft_content.json` of an existing empty CapCut project.

**Windows**

```
%USERPROFILE%\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\
```

Full example path:

```
C:\Users\<YourName>\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\<project-id>\draft_content.json
```

Steps:
1. Open File Explorer and paste `%USERPROFILE%\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\` into the address bar.
2. Create a new folder (any name — CapCut uses the folder name as the project name initially).
3. Copy the downloaded `draft_content.json` into that folder.
4. Open CapCut — the project will appear in **My Projects**.

**macOS**

```
~/Movies/CapCut/User Data/Projects/com.lveditor.draft/
```

Full example path:

```
/Users/<YourName>/Movies/CapCut/User Data/Projects/com.lveditor.draft/<project-id>/draft_content.json
```

Steps:
1. Open Finder, press `⌘ Shift G`, and enter `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/`.
2. Create a new folder for the project.
3. Copy the downloaded `draft_content.json` into that folder.
4. Open CapCut — the project will appear in **My Projects**.

> **Note:** CapCut's internal format evolves between app versions.  The export
> targets CapCut **5.9** and later (desktop).  If markers do not appear, ensure
> you are running an up-to-date version of CapCut and that the `draft_content.json`
> is the only file in the project folder before opening it.

---

## GameCut Companion — Live Hotkey Logger (Windows / OBS)

The **GameCut Companion** is a tiny background script that listens for hotkeys while you record in OBS and writes a `timestamps.json` file that GameCut uses to pre-seed manual markers into the EDL before AI analysis runs.

### Setup (Windows)

1. **Install Python 3.10+** from python.org (check "Add Python to PATH" during install).

2. **Install the companion dependencies:**
   ```cmd
   cd companion
   pip install -r requirements.txt
   ```
   > On Windows you may need to run the terminal **as Administrator** if OBS is also running elevated.

3. **Start the companion before hitting Record in OBS:**
   ```cmd
   python gamecut_companion.py
   ```
   Or with a custom output path alongside the recording:
   ```cmd
   python gamecut_companion.py --output "D:\OBS\recordings\session.json"
   ```

4. **Press F8 the moment OBS starts recording.** This resets the elapsed timer to `0.000 s` so timestamps align with the video file.

### Default hotkeys

| Key | Action |
|-----|--------|
| F8  | Reset session timer (press when OBS starts recording) |
| F9  | Mark hype moment |
| F10 | Mark funny moment |
| F11 | Mark rage / frustration |
| F12 | Mark cut suggestion |

### Avoid OBS hotkey conflicts

OBS uses F-keys for its own hotkeys (Start/Stop Recording, etc.). Check **OBS → Settings → Hotkeys** and reassign any conflicting keys.  If you prefer not to move OBS hotkeys, pass custom keys to the companion:

```cmd
python gamecut_companion.py --hotkeys start=F4 hype=F5 funny=F6 rage=F7 cut=F8
```

Or store them permanently in a config file:

```json
{
  "output": "C:/OBS/recordings/session.json",
  "hotkeys": {
    "session_start": "f4",
    "hype":  "f5",
    "funny": "f6",
    "rage":  "f7",
    "cut":   "f8"
  },
  "window_seconds": 30.0
}
```

```cmd
python gamecut_companion.py --config my_config.json
```

### Using timestamps in GameCut

After the recording session, open GameCut, upload the video, then click **"Load timestamps.json from GameCut Companion…"** and select the JSON file the companion wrote.  Manual markers are merged with AI analysis before the EDL is built — `hype`/`funny`/`rage` events create 30-second highlight windows; `cut` events create 5-second dead zones.

---

## Troubleshooting

**`librosa` install fails on Apple Silicon**

```bash
pip install --no-binary :all: llvmlite
pip install librosa
```

**`opencv-python-headless` conflicts with `opencv-python`**

```bash
pip uninstall opencv-python
pip install opencv-python-headless
```

**`ffmpeg: command not found`**

Ensure `ffmpeg` is on your `PATH`. Test with `ffmpeg -version`.

**CORS errors in the browser**

Ensure the backend is running on port 8000 and the frontend on port 3000. The `next.config.js` proxy handles routing.
