"""
GameCut FastAPI Backend
Single /analyze endpoint that accepts a video file and game_preset,
runs dead zone + hype moment detection, then returns a merged EDL.
"""

import asyncio
import functools
import glob as _glob
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from modules.audio_splitter import split_audio, VOCALS_LOWCUT_HZ, VOCALS_HIGHCUT_HZ
from modules.corrections import (
    apply_corrections,
    correction_summary,
    save_correction,
    segment_hash,
)
from modules.dead_zone import detect_dead_zones, detect_struggle_zones
from modules.hype_moment import detect_hype_moments
from modules.edl_export import build_edl, to_json, to_csv, to_edl, to_fcpxml, to_capcut
from modules.preset_detector import detect_preset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Load presets ───────────────────────────────────────────────────────────────
# When packaged with PyInstaller, __file__ is inside the bundle.  Use sys._MEIPASS
# as the base directory so presets.json is found from the extracted bundle root.
import sys as _sys
_BASE_DIR = Path(getattr(_sys, "_MEIPASS", None) or Path(__file__).parent)
PRESETS_PATH = _BASE_DIR / "presets.json"
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


def _get_video_dimensions(path: str) -> tuple[int, int]:
    """Use ffprobe to get video width and height."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            path,
        ],
        capture_output=True, text=True,
    )
    try:
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1920, 1080


# ── Chunking constants & job-progress store ────────────────────────────────────

CHUNK_DURATION_SEC = 900  # 15 minutes per chunk

_job_progress: dict[str, dict] = {}
_job_lock = threading.Lock()


def _init_job(job_id: str, total_chunks: int) -> None:
    with _job_lock:
        _job_progress[job_id] = {
            "total_chunks": total_chunks,
            "completed_chunks": 0,
            "chunk_labels": ["Queued"] * total_chunks,
            "status": "analyzing",
        }


def _update_chunk_label(job_id: str, chunk_idx: int, label: str) -> None:
    with _job_lock:
        if job_id in _job_progress:
            _job_progress[job_id]["chunk_labels"][chunk_idx] = label


def _complete_chunk(job_id: str, chunk_idx: int) -> None:
    with _job_lock:
        if job_id in _job_progress:
            _job_progress[job_id]["completed_chunks"] += 1
            _job_progress[job_id]["chunk_labels"][chunk_idx] = "Done"


def _finish_job(job_id: str) -> None:
    with _job_lock:
        if job_id in _job_progress:
            _job_progress[job_id]["status"] = "done"


def _split_into_chunks(video_path: str) -> tuple[list[tuple[str, float]], Optional[str]]:
    """Split video into ~15-min chunks using the ffmpeg segment muxer.

    Returns ([(chunk_path, start_offset_seconds), ...], tmp_dir).
    tmp_dir is None when splitting failed and the original path is returned.
    """
    tmp_dir = tempfile.mkdtemp(prefix="gamecut_chunks_")
    pattern = os.path.join(tmp_dir, "chunk_%03d.mp4")

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-f", "segment",
            "-segment_time", str(CHUNK_DURATION_SEC),
            "-reset_timestamps", "1",
            "-c", "copy",
            pattern,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    chunk_files = sorted(_glob.glob(os.path.join(tmp_dir, "chunk_*.mp4")))

    if not chunk_files:
        logger.warning(
            "ffmpeg segment muxer produced no chunks: %s",
            result.stderr.decode()[-300:],
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return [(video_path, 0.0)], None

    # Compute start offsets from cumulative chunk durations
    chunks: list[tuple[str, float]] = []
    offset = 0.0
    for chunk_path in chunk_files:
        chunks.append((chunk_path, offset))
        offset += _get_video_duration(chunk_path)

    return chunks, tmp_dir


def _process_chunk(
    chunk_path: str,
    chunk_offset: float,
    chunk_idx: int,
    job_id: Optional[str],
    preset: dict,
    dz_sensitivity: float,
    hype_sensitivity: float,
) -> tuple[list[dict], list[dict]]:
    """Detect dead zones and hype moments in one chunk; adjust timestamps by offset."""
    total = _job_progress.get(job_id, {}).get("total_chunks", 1) if job_id else 1
    logger.info("Processing chunk %d/%d (offset=%.1fs)", chunk_idx + 1, total, chunk_offset)

    if job_id:
        _update_chunk_label(job_id, chunk_idx, f"Analyzing chunk {chunk_idx + 1} of {total}…")

    with split_audio(chunk_path) as tracks:
        dead_zones = detect_dead_zones(
            chunk_path, preset,
            sensitivity=dz_sensitivity,
            game_audio_path=tracks.game_audio_path,
        )
        highlights = detect_hype_moments(
            chunk_path, preset,
            sensitivity=hype_sensitivity,
            vocals_path=tracks.vocals_path,
        )

    # Shift all timestamps by this chunk's start offset
    for seg in dead_zones:
        seg["start"] += chunk_offset
        seg["end"] += chunk_offset
    for seg in highlights:
        seg["start"] += chunk_offset
        seg["end"] += chunk_offset

    if job_id:
        _complete_chunk(job_id, chunk_idx)

    return dead_zones, highlights


# ── Companion timestamps helpers ───────────────────────────────────────────────

def _companion_to_segments(
    timestamps_data: dict,
    video_duration: float,
    *,
    window_before: float = 10.0,
    window_after: float = 20.0,
    cut_half_window: float = 2.5,
) -> tuple[list[dict], list[dict]]:
    """Convert gamecut_companion.py events to dead_zone / highlight dicts.

    Args:
        timestamps_data: Parsed contents of timestamps.json.
        video_duration:  Duration of the source video in seconds.
        window_before:   Seconds before the marked point to start a highlight clip.
        window_after:    Seconds after the marked point to end a highlight clip.
        cut_half_window: Half-width of the dead_zone window for "cut" markers.

    Returns:
        (manual_dead_zones, manual_highlights) — ready to merge with AI results.
    """
    offset   = float(timestamps_data.get("offset_seconds", 0.0))
    win      = float(timestamps_data.get("window_seconds", window_before + window_after))
    wb       = window_before
    wa       = win - wb  # keep proportions if window_seconds was overridden

    dead_zones: list[dict] = []
    highlights: list[dict] = []

    for ev in timestamps_data.get("events", []):
        t     = float(ev.get("elapsed_seconds", 0.0)) + offset
        etype = str(ev.get("type", "hype")).lower()
        hkey  = ev.get("hotkey", "")

        if t < 0 or t > video_duration:
            logger.warning(
                "Companion event at %.1fs is outside video duration (%.1fs) — skipping",
                t, video_duration,
            )
            continue

        if etype == "cut":
            start = max(0.0, t - cut_half_window)
            end   = min(video_duration, t + cut_half_window)
            dead_zones.append({
                "start":       start,
                "end":         end,
                "duration":    end - start,
                "type":        "dead_zone",
                "source":      "manual",
                "manual_type": "cut",
                "hotkey":      hkey,
                "confidence":  1.0,
            })
        else:
            # hype / funny / rage → highlight window
            start = max(0.0, t - wb)
            end   = min(video_duration, t + wa)
            highlights.append({
                "start":             start,
                "end":               end,
                "duration":          end - start,
                "type":              "highlight",
                "source":            "manual",
                "manual_type":       etype,
                "hotkey":            hkey,
                "composite_score":   1.0,
                "volume_score":      0.0,
                "pitch_score":       0.0,
                "speech_rate_score": 0.0,
            })

    return dead_zones, highlights


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "GameCut API is running", "version": "1.0.0"}


@app.get("/presets")
def get_presets():
    """Return available game presets."""
    return PRESETS


@app.post("/detect-preset")
async def detect_preset_endpoint(
    file: Optional[UploadFile] = File(default=None, description="Gameplay video file"),
    file_path: Optional[str] = Form(default=None, description="Local filesystem path (Tauri desktop mode)"),
):
    """
    Analyse the first 90 seconds of a video and classify it into a game preset.

    Examines four audio-visual signals:
      - audio_energy_db       : mean programme loudness
      - spectral_centroid_norm: audio brightness (bass vs. treble balance)
      - frame_change_rate     : camera / action dynamics
      - color_saturation_mean : art style vibrancy

    Returns the best-matching preset plus a full confidence distribution so the
    UI can show scores for all presets and let the user override the suggestion.
    """
    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Provide either 'file' or 'file_path'.")

    tmp_path = None
    own_tmp = False
    try:
        if file_path:
            if not os.path.isabs(file_path) or not os.path.exists(file_path):
                raise HTTPException(status_code=400, detail=f"file_path not found: {file_path}")
            tmp_path = file_path
            display_name = Path(file_path).name
        else:
            suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(await file.read())
            own_tmp = True
            display_name = file.filename or "upload.mp4"

        logger.info("Detecting preset for %r", display_name)
        result = detect_preset(tmp_path)
        logger.info(
            "Detected preset=%s confidence=%.2f signals=%s",
            result["detected_preset"], result["confidence"], result["signals"],
        )
        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Preset detection failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if own_tmp and tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/analyze")
async def analyze(
    file: Optional[UploadFile] = File(default=None, description="Gameplay video file"),
    file_path: Optional[str] = Form(default=None, description="Local filesystem path (Tauri desktop mode)"),
    game_preset: str = Form("fps", description="Game preset key"),
    dead_zone_sensitivity: float = Form(1.0, description="Dead zone sensitivity multiplier (0.5–2.0)"),
    hype_sensitivity: float = Form(1.0, description="Hype detection sensitivity multiplier (0.5–2.0)"),
    export_format: str = Form("json", description="Export format: json | csv | edl | all"),
    job_id: Optional[str] = Form(None, description="Client-generated job ID for /status polling"),
    companion_timestamps: Optional[str] = Form(
        None,
        description="JSON string from gamecut_companion.py — pre-seeds the EDL with manual markers",
    ),
):
    """
    Analyze a gameplay video for dead zones and hype moments.

    Returns a merged EDL with segments tagged as dead_zone, highlight, or keep.
    """
    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Provide either 'file' or 'file_path'.")

    if game_preset not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{game_preset}'. Valid: {list(PRESETS.keys())}",
        )

    preset = PRESETS[game_preset]

    # Clamp sensitivities
    dead_zone_sensitivity = max(0.25, min(4.0, dead_zone_sensitivity))
    hype_sensitivity = max(0.25, min(4.0, hype_sensitivity))

    # Apply any accumulated user corrections to the preset thresholds
    preset, threshold_adjustments = apply_corrections(game_preset, preset)

    # Resolve video path — either from upload or from the Tauri local path.
    tmp_path = None
    own_tmp = False
    orig_filename: str
    try:
        if file_path:
            if not os.path.isabs(file_path) or not os.path.exists(file_path):
                raise HTTPException(status_code=400, detail=f"file_path not found: {file_path}")
            tmp_path = file_path
            own_tmp = False
            orig_filename = Path(file_path).name
        else:
            suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(await file.read())
            own_tmp = True
            orig_filename = file.filename or "upload.mp4"

        logger.info(f"Analyzing {orig_filename!r} with preset={game_preset}")

        duration = _get_video_duration(tmp_path)
        fps = _get_video_fps(tmp_path)

        if duration <= 0:
            raise HTTPException(status_code=422, detail="Could not determine video duration.")

        # ── Split into chunks and process in parallel ─────────────────────
        progress_log: list[str] = []
        audio_track_meta: dict = {
            "game_audio": {
                "description": "Full-spectrum audio — dead zone detection",
                "lowcut_hz": 0,
                "highcut_hz": "full",
            },
            "vocals": {
                "description": "Bandpass-filtered commentary — hype detection",
                "lowcut_hz": VOCALS_LOWCUT_HZ,
                "highcut_hz": VOCALS_HIGHCUT_HZ,
            },
        }

        chunks, chunk_dir = _split_into_chunks(tmp_path)
        num_chunks = len(chunks)
        progress_log.append(
            f"[chunks] Video split into {num_chunks} chunk(s) "
            f"of up to {CHUNK_DURATION_SEC}s each"
        )
        logger.info("Processing %d chunk(s) in parallel", num_chunks)

        if job_id:
            _init_job(job_id, num_chunks)

        try:
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(
                max_workers=min(num_chunks, os.cpu_count() or 4)
            ) as pool:
                futures = [
                    loop.run_in_executor(
                        pool,
                        functools.partial(
                            _process_chunk,
                            chunk_path, offset, idx, job_id,
                            preset, dead_zone_sensitivity, hype_sensitivity,
                        ),
                    )
                    for idx, (chunk_path, offset) in enumerate(chunks)
                ]
                chunk_results = await asyncio.gather(*futures)
        finally:
            if chunk_dir and os.path.exists(chunk_dir):
                shutil.rmtree(chunk_dir, ignore_errors=True)

        dead_zones: list[dict] = []
        highlights: list[dict] = []
        for dz_list, hl_list in chunk_results:
            dead_zones.extend(dz_list)
            highlights.extend(hl_list)

        dead_zones.sort(key=lambda x: x["start"])
        highlights.sort(key=lambda x: x["start"])
        progress_log.append(
            f"[chunks] Merged {num_chunks} chunk(s): "
            f"{len(dead_zones)} dead zones, {len(highlights)} highlights"
        )

        if job_id:
            _finish_job(job_id)

        # ── Merge companion manual markers (if supplied) ───────────────────
        manual_markers: list[dict] = []
        if companion_timestamps:
            try:
                cts_data = json.loads(companion_timestamps)
                manual_dz, manual_hl = _companion_to_segments(cts_data, duration)
                logger.info(
                    "Companion timestamps: %d manual dead_zones, %d manual highlights",
                    len(manual_dz), len(manual_hl),
                )
                # Manual markers take priority: prepend so highlights override AI dead_zones
                dead_zones  = manual_dz  + dead_zones
                highlights  = manual_hl + highlights
                dead_zones.sort(key=lambda x: x["start"])
                highlights.sort(key=lambda x: x["start"])
                manual_markers = manual_dz + manual_hl
                progress_log.append(
                    f"[companion] Merged {len(manual_dz)} manual cuts "
                    f"and {len(manual_hl)} manual highlights from timestamps.json"
                )
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning("Could not parse companion_timestamps: %s", e)
                progress_log.append(f"[companion] WARNING: could not parse timestamps — {e}")

        # Attach segment_hash to AI-detected segments only (manual ones have source="manual")
        for dz in dead_zones:
            if dz.get("source") != "manual":
                dz["segment_hash"] = segment_hash(
                    game_preset, "dead_zone", dz.get("confidence"), dz["duration"]
                )
        for hl in highlights:
            if hl.get("source") != "manual":
                hl["segment_hash"] = segment_hash(
                    game_preset, "highlight", hl.get("composite_score"), hl["duration"]
                )

        # Post-process: group clustered dead zones into struggle zones
        struggle_zones = detect_struggle_zones(dead_zones)

        edl = build_edl(
            dead_zones=dead_zones,
            highlights=highlights,
            video_duration=duration,
            source_name=Path(orig_filename).stem[:8].upper(),
            struggle_zones=struggle_zones,
        )

        response_data = {
            "preset": game_preset,
            "preset_name": preset["name"],
            "filename": orig_filename,
            "duration": duration,
            "fps": fps,
            "audio_tracks": audio_track_meta,
            "threshold_adjustments": threshold_adjustments,
            "dead_zones": dead_zones,
            "highlights": highlights,
            "struggle_zones": struggle_zones,
            "manual_markers": manual_markers,
            "edl": edl,
            "progress_log": progress_log,
        }

        if export_format == "csv":
            return PlainTextResponse(to_csv(edl), media_type="text/csv")
        elif export_format == "edl":
            return PlainTextResponse(to_edl(edl, fps=fps), media_type="text/plain")
        elif export_format == "fcpxml":
            return PlainTextResponse(
                to_fcpxml(edl, fps=fps, source_filename=file.filename or "source.mp4"),
                media_type="application/xml",
                headers={"Content-Disposition": 'attachment; filename="gamecut_timeline.fcpxml"'},
            )
        elif export_format == "capcut":
            return PlainTextResponse(
                to_capcut(edl, fps=fps, source_filename=orig_filename),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="draft_content.json"'},
            )
        elif export_format == "all":
            response_data["exports"] = {
                "json": json.loads(to_json(edl)),
                "csv": to_csv(edl),
                "edl": to_edl(edl, fps=fps),
                "fcpxml": to_fcpxml(edl, fps=fps, source_filename=orig_filename),
                "capcut": to_capcut(edl, fps=fps, source_filename=orig_filename),
            }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if own_tmp and tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.get("/status/{job_id}")
def get_job_status(job_id: str):
    """Return chunk-level progress for an in-flight /analyze job."""
    with _job_lock:
        job = _job_progress.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    completed = job["completed_chunks"]
    total = job["total_chunks"]
    if job["status"] == "analyzing":
        current_label = f"Analyzing chunk {completed + 1} of {total}…"
    else:
        current_label = "Done"
    return {
        "total_chunks": total,
        "completed_chunks": completed,
        "chunk_labels": job["chunk_labels"],
        "status": job["status"],
        "current_label": current_label,
    }


@app.post("/feedback")
async def submit_feedback(
    preset: str = Form(..., description="Game preset key"),
    segment_type: str = Form(..., description="'dead_zone' or 'highlight'"),
    vote: str = Form(..., description="'up' or 'down'"),
    score: float = Form(None, description="Detection score (0–1); used for bucketing"),
    duration: float = Form(..., description="Segment duration in seconds"),
):
    """
    Record a thumbs-up or thumbs-down vote on a detected segment.

    Votes are aggregated by segment class (preset × type × score bucket ×
    duration bucket) and persisted to corrections.json.  Future /analyze calls
    for the same preset will have their thresholds adjusted accordingly.
    """
    if preset not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{preset}'. Valid: {list(PRESETS.keys())}",
        )
    if segment_type not in ("dead_zone", "highlight"):
        raise HTTPException(
            status_code=400,
            detail="segment_type must be 'dead_zone' or 'highlight'",
        )
    if vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="vote must be 'up' or 'down'")
    if duration <= 0:
        raise HTTPException(status_code=400, detail="duration must be > 0")

    try:
        entry = save_correction(
            preset=preset,
            segment_type=segment_type,
            vote=vote,
            score=score,
            duration=duration,
        )
    except Exception as e:
        logger.exception("Failed to save correction")
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse(
        content={
            "saved": True,
            "correction": entry,
            "message": (
                f"Vote recorded. "
                f"{entry['votes_up']} up / {entry['votes_down']} down "
                f"for this segment class."
            ),
        }
    )


@app.get("/corrections/{preset_key}")
def get_corrections(preset_key: str):
    """Return a summary of all stored corrections for a preset."""
    if preset_key not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{preset_key}'. Valid: {list(PRESETS.keys())}",
        )
    return correction_summary(preset_key)


@app.delete("/corrections/{preset_key}")
def clear_corrections(preset_key: str):
    """Delete all stored corrections for a preset (reset thresholds to defaults)."""
    from modules.corrections import load_corrections, CORRECTIONS_PATH
    import json

    if preset_key not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{preset_key}'.",
        )

    corrections = load_corrections()
    before = len(corrections)
    corrections = {h: e for h, e in corrections.items() if e.get("preset") != preset_key}
    removed = before - len(corrections)

    try:
        with open(CORRECTIONS_PATH, "w") as f:
            json.dump(corrections, f, indent=2)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"preset": preset_key, "removed": removed, "remaining": len(corrections)}


@app.post("/analyze/export/{format}")
async def export_edl(
    format: str,
    file: UploadFile = File(...),
    game_preset: str = Form("fps"),
    dead_zone_sensitivity: float = Form(1.0),
    hype_sensitivity: float = Form(1.0),
):
    """Convenience endpoint: analyze and return a specific export format directly. Formats: json | csv | edl | fcpxml | all"""
    return await analyze(
        file=file,
        game_preset=game_preset,
        dead_zone_sensitivity=dead_zone_sensitivity,
        hype_sensitivity=hype_sensitivity,
        export_format=format,
    )


@app.post("/assemble")
async def assemble_highlight_reel(
    file: Optional[UploadFile] = File(default=None, description="Original gameplay video"),
    file_path: Optional[str] = Form(default=None, description="Local filesystem path (Tauri desktop mode)"),
    highlights_json: str = Form(..., description="JSON array of highlight segments from /analyze"),
):
    """
    Assemble a highlight reel from HIGHLIGHT segments.

    Accepts the original video and the JSON array of highlight objects returned
    by /analyze.  Segments are sorted by composite_score descending, then
    concatenated with a 0.5-second black-frame transition between each clip.

    Returns a downloadable MP4 named ``<source>_highlights.mp4``.
    """
    try:
        highlights: list[dict] = json.loads(highlights_json)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid highlights_json: {exc}")

    if not highlights:
        raise HTTPException(status_code=400, detail="No highlights provided.")

    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Provide either 'file' or 'file_path'.")

    # Sort best-first
    highlights = sorted(highlights, key=lambda h: h.get("composite_score", 0.0), reverse=True)

    tmp_video = None
    own_tmp_video = False
    tmp_dir   = None

    try:
        # ── Resolve source video ───────────────────────────────────────────────
        if file_path:
            if not os.path.isabs(file_path) or not os.path.exists(file_path):
                raise HTTPException(status_code=400, detail=f"file_path not found: {file_path}")
            tmp_video = file_path
            display_src = Path(file_path).name
        else:
            suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_video = tmp.name
                tmp.write(await file.read())
            own_tmp_video = True
            display_src = file.filename or "upload.mp4"

        logger.info(
            "Assembling %d highlights from %r",
            len(highlights), display_src,
        )

        fps   = _get_video_fps(tmp_video)
        fps_int = max(1, round(fps))
        width, height = _get_video_dimensions(tmp_video)

        tmp_dir = tempfile.mkdtemp()

        # ── Extract each highlight clip ────────────────────────────────────────
        clip_paths: list[str] = []
        for i, hl in enumerate(highlights):
            clip_path = os.path.join(tmp_dir, f"clip_{i:03d}.mp4")
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", tmp_video,
                    "-ss", str(hl["start"]),
                    "-t",  str(hl["duration"]),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    clip_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                clip_paths.append(clip_path)
            else:
                logger.warning("Clip %d extraction failed or empty — skipping", i)

        if not clip_paths:
            raise HTTPException(status_code=500, detail="All clip extractions failed.")

        # ── Create 0.5 s black transition clip ────────────────────────────────
        black_path = os.path.join(tmp_dir, "black.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                    f"color=black:size={width}x{height}:rate={fps_int}",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "0.5",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "192k",
                black_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # ── Write concat list ──────────────────────────────────────────────────
        list_path = os.path.join(tmp_dir, "filelist.txt")
        with open(list_path, "w") as flist:
            for i, clip in enumerate(clip_paths):
                flist.write(f"file '{clip}'\n")
                if i < len(clip_paths) - 1 and os.path.exists(black_path):
                    flist.write(f"file '{black_path}'\n")

        # ── Concatenate ────────────────────────────────────────────────────────
        output_path = os.path.join(tmp_dir, "highlight_reel.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-movflags", "+faststart",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg concat produced no output.",
            )

        with open(output_path, "rb") as fout:
            video_bytes = fout.read()

        stem = Path(display_src).stem
        logger.info(
            "Assembled reel: %d clips, %.1f MB",
            len(clip_paths), len(video_bytes) / 1_048_576,
        )

        return Response(
            content=video_bytes,
            media_type="video/mp4",
            headers={
                "Content-Disposition":
                    f'attachment; filename="{stem}_highlights.mp4"',
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Assemble failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if own_tmp_video and tmp_video and os.path.exists(tmp_video):
            os.unlink(tmp_video)
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/slice-shorts")
async def slice_shorts(
    file: Optional[UploadFile] = File(default=None, description="Original gameplay video"),
    file_path: Optional[str] = Form(default=None, description="Local filesystem path (Tauri desktop mode)"),
    segments_json: str = Form(..., description="JSON array of highlight segments to export"),
    crop_center_pct: float = Form(default=50.0, description="Horizontal crop center 0–100 (50 = center)"),
):
    """
    Export highlight segments as individual vertical 9:16 clips, returned as a .zip archive.

    Each clip is center-cropped (or offset-cropped) from the landscape source and
    scaled to 1080×1920, ready to upload as a Short, Reel, or TikTok.
    """
    import zipfile as _zipfile

    try:
        segments: list[dict] = json.loads(segments_json)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid segments_json: {exc}")

    if not segments:
        raise HTTPException(status_code=400, detail="No segments provided.")
    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Provide either 'file' or 'file_path'.")

    crop_center_pct = max(0.0, min(100.0, crop_center_pct))

    tmp_video = None
    own_tmp_video = False
    tmp_dir = None

    try:
        if file_path:
            if not os.path.isabs(file_path) or not os.path.exists(file_path):
                raise HTTPException(status_code=400, detail=f"file_path not found: {file_path}")
            tmp_video = file_path
            stem = Path(file_path).stem
        else:
            suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_video = tmp.name
                tmp.write(await file.read())
            own_tmp_video = True
            stem = Path(file.filename or "upload").stem

        tmp_dir = tempfile.mkdtemp()

        # crop_w must be divisible by 2 (libx264 requirement)
        # x offset scales from 0 (left) to 1 (right) within the available horizontal range
        w_expr = "trunc(ih*9/16/2)*2"
        x_pct  = crop_center_pct / 100.0
        x_expr = f"(iw-{w_expr})*{x_pct:.6f}"
        vf     = f"crop={w_expr}:ih:{x_expr}:0,scale=1080:1920:flags=lanczos"

        clip_paths: list[tuple[str, str]] = []  # (filesystem path, zip entry name)
        for i, seg in enumerate(segments):
            label     = seg.get("manual_type") or f"hl{i + 1:02d}"
            clip_name = f"{stem}_short_{i + 1:02d}_{label}.mp4"
            clip_path = os.path.join(tmp_dir, clip_name)

            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(seg["start"]),
                    "-i",  tmp_video,
                    "-t",  str(seg["duration"]),
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    clip_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                clip_paths.append((clip_path, clip_name))
            else:
                logger.warning("Shorts clip %d failed or produced no output — skipping", i)

        if not clip_paths:
            raise HTTPException(status_code=500, detail="All clip extractions failed.")

        zip_path = os.path.join(tmp_dir, f"{stem}_shorts.zip")
        with _zipfile.ZipFile(zip_path, "w", _zipfile.ZIP_DEFLATED) as zf:
            for clip_path, clip_name in clip_paths:
                zf.write(clip_path, clip_name)

        with open(zip_path, "rb") as fzip:
            zip_bytes = fzip.read()

        logger.info(
            "Sliced %d shorts (%.1f MB zip) from %r",
            len(clip_paths), len(zip_bytes) / 1_048_576, stem,
        )

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{stem}_shorts.zip"'},
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("slice-shorts failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if own_tmp_video and tmp_video and os.path.exists(tmp_video):
            os.unlink(tmp_video)
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Sidecar entry point (used by PyInstaller / Tauri) ─────────────────────────
if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="GameCut FastAPI backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
