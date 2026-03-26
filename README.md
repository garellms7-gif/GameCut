# GameCut

Auto-detect **dead zones** (boring, cut-worthy content) and **hype moments** (highlight-worthy clips) in gameplay footage. Exports a merged Edit Decision List (EDL) in JSON, CSV, and DaVinci Resolve-compatible `.edl` format.

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
