"""
GameCut FastAPI Backend
Single /analyze endpoint that accepts a video file and game_preset,
runs dead zone + hype moment detection, then returns a merged EDL.
"""

import json
import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from modules.audio_splitter import split_audio, VOCALS_LOWCUT_HZ, VOCALS_HIGHCUT_HZ
from modules.dead_zone import detect_dead_zones
from modules.hype_moment import detect_hype_moments
from modules.edl_export import build_edl, to_json, to_csv, to_edl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Load presets ───────────────────────────────────────────────────────────────
PRESETS_PATH = Path(__file__).parent / "presets.json"
with open(PRESETS_PATH) as f:
    PRESETS: dict = json.load(f)

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="GameCut API",
    description="Detect dead zones and hype moments in gameplay footage.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_video_duration(path: str) -> float:
    """Use ffprobe to get video duration in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _get_video_fps(path: str) -> float:
    """Use ffprobe to get video FPS."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True,
    )
    try:
        num, den = result.stdout.strip().split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "GameCut API is running", "version": "1.0.0"}


@app.get("/presets")
def get_presets():
    """Return available game presets."""
    return PRESETS


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(..., description="Gameplay video file"),
    game_preset: str = Form("fps", description="Game preset key"),
    dead_zone_sensitivity: float = Form(1.0, description="Dead zone sensitivity multiplier (0.5–2.0)"),
    hype_sensitivity: float = Form(1.0, description="Hype detection sensitivity multiplier (0.5–2.0)"),
    export_format: str = Form("json", description="Export format: json | csv | edl | all"),
):
    """
    Analyze a gameplay video for dead zones and hype moments.

    Returns a merged EDL with segments tagged as dead_zone, highlight, or keep.
    """
    if game_preset not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{game_preset}'. Valid: {list(PRESETS.keys())}",
        )

    preset = PRESETS[game_preset]

    # Clamp sensitivities
    dead_zone_sensitivity = max(0.25, min(4.0, dead_zone_sensitivity))
    hype_sensitivity = max(0.25, min(4.0, hype_sensitivity))

    # Save upload to temp file
    suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            content = await file.read()
            tmp.write(content)

        logger.info(f"Analyzing {file.filename!r} with preset={game_preset}")

        duration = _get_video_duration(tmp_path)
        fps = _get_video_fps(tmp_path)

        if duration <= 0:
            raise HTTPException(status_code=422, detail="Could not determine video duration.")

        # ── Split audio into two tracks in a single ffmpeg pass ───────────
        progress_log: list[str] = []
        progress_log.append("[split] Splitting audio into game + vocals tracks")

        def dz_progress(pct, label):
            progress_log.append(f"[dead_zone {pct:.0%}] {label}")

        def hype_progress(pct, label):
            progress_log.append(f"[hype {pct:.0%}] {label}")

        audio_track_meta: dict = {}
        with split_audio(tmp_path) as tracks:
            logger.info(
                "Audio split: game_audio=%s  vocals=%s",
                tracks.game_audio_path,
                tracks.vocals_path,
            )
            progress_log.append(
                f"[split] game audio → full spectrum | "
                f"vocals → {VOCALS_LOWCUT_HZ} Hz – {VOCALS_HIGHCUT_HZ} Hz bandpass"
            )
            # Capture metadata before the context manager cleans up the files
            audio_track_meta = {
                "game_audio": {
                    "description": "Full-spectrum audio — used for dead zone detection",
                    "filter": tracks.game_audio_filter,
                },
                "vocals": {
                    "description": "Bandpass-filtered commentary — used for hype detection",
                    "filter": tracks.vocals_filter,
                    "lowcut_hz": VOCALS_LOWCUT_HZ,
                    "highcut_hz": VOCALS_HIGHCUT_HZ,
                },
            }

            # Dead zones use the full-spectrum game audio track
            dead_zones = detect_dead_zones(
                tmp_path, preset,
                sensitivity=dead_zone_sensitivity,
                progress_callback=dz_progress,
                game_audio_path=tracks.game_audio_path,
            )

            # Hype moments use the bandpass-filtered vocals track
            highlights = detect_hype_moments(
                tmp_path, preset,
                sensitivity=hype_sensitivity,
                progress_callback=hype_progress,
                vocals_path=tracks.vocals_path,
            )

        edl = build_edl(
            dead_zones=dead_zones,
            highlights=highlights,
            video_duration=duration,
            source_name=Path(file.filename or "CLIP").stem[:8].upper(),
        )

        response_data = {
            "preset": game_preset,
            "preset_name": preset["name"],
            "filename": file.filename,
            "duration": duration,
            "fps": fps,
            "audio_tracks": audio_track_meta,
            "dead_zones": dead_zones,
            "highlights": highlights,
            "edl": edl,
            "progress_log": progress_log,
        }

        if export_format == "csv":
            return PlainTextResponse(to_csv(edl), media_type="text/csv")
        elif export_format == "edl":
            return PlainTextResponse(to_edl(edl, fps=fps), media_type="text/plain")
        elif export_format == "all":
            response_data["exports"] = {
                "json": json.loads(to_json(edl)),
                "csv": to_csv(edl),
                "edl": to_edl(edl, fps=fps),
            }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/analyze/export/{format}")
async def export_edl(
    format: str,
    file: UploadFile = File(...),
    game_preset: str = Form("fps"),
    dead_zone_sensitivity: float = Form(1.0),
    hype_sensitivity: float = Form(1.0),
):
    """Convenience endpoint: analyze and return a specific export format directly."""
    return await analyze(
        file=file,
        game_preset=game_preset,
        dead_zone_sensitivity=dead_zone_sensitivity,
        hype_sensitivity=hype_sensitivity,
        export_format=format,
    )
