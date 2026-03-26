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
    file: UploadFile = File(..., description="Gameplay video file"),
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
    suffix   = Path(file.filename or "upload.mp4").suffix or ".mp4"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(await file.read())

        logger.info("Detecting preset for %r", file.filename)
        result = detect_preset(tmp_path)
        logger.info(
            "Detected preset=%s confidence=%.2f signals=%s",
            result["detected_preset"], result["confidence"], result["signals"],
        )
        return JSONResponse(content=result)

    except Exception as e:
        logger.exception("Preset detection failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(..., description="Gameplay video file"),
    game_preset: str = Form("fps", description="Game preset key"),
    dead_zone_sensitivity: float = Form(1.0, description="Dead zone sensitivity multiplier (0.5–2.0)"),
    hype_sensitivity: float = Form(1.0, description="Hype detection sensitivity multiplier (0.5–2.0)"),
    export_format: str = Form("json", description="Export format: json | csv | edl | all"),
    job_id: Optional[str] = Form(None, description="Client-generated job ID for /status polling"),
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

    # Apply any accumulated user corrections to the preset thresholds
    preset, threshold_adjustments = apply_corrections(game_preset, preset)

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

        # Attach segment_hash to each detected segment so the frontend can
        # send it back as part of a /feedback call.
        for dz in dead_zones:
            dz["segment_hash"] = segment_hash(
                game_preset, "dead_zone", dz.get("confidence"), dz["duration"]
            )
        for hl in highlights:
            hl["segment_hash"] = segment_hash(
                game_preset, "highlight", hl.get("composite_score"), hl["duration"]
            )

        # Post-process: group clustered dead zones into struggle zones
        struggle_zones = detect_struggle_zones(dead_zones)

        edl = build_edl(
            dead_zones=dead_zones,
            highlights=highlights,
            video_duration=duration,
            source_name=Path(file.filename or "CLIP").stem[:8].upper(),
            struggle_zones=struggle_zones,
        )

        response_data = {
            "preset": game_preset,
            "preset_name": preset["name"],
            "filename": file.filename,
            "duration": duration,
            "fps": fps,
            "audio_tracks": audio_track_meta,
            "threshold_adjustments": threshold_adjustments,
            "dead_zones": dead_zones,
            "highlights": highlights,
            "struggle_zones": struggle_zones,
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
                to_capcut(edl, fps=fps, source_filename=file.filename or ""),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="draft_content.json"'},
            )
        elif export_format == "all":
            response_data["exports"] = {
                "json": json.loads(to_json(edl)),
                "csv": to_csv(edl),
                "edl": to_edl(edl, fps=fps),
                "fcpxml": to_fcpxml(edl, fps=fps, source_filename=file.filename or "source.mp4"),
                "capcut": to_capcut(edl, fps=fps, source_filename=file.filename or ""),
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
    file: UploadFile = File(..., description="Original gameplay video"),
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

    # Sort best-first
    highlights = sorted(highlights, key=lambda h: h.get("composite_score", 0.0), reverse=True)

    suffix   = Path(file.filename or "upload.mp4").suffix or ".mp4"
    tmp_video = None
    tmp_dir   = None

    try:
        # ── Save upload ────────────────────────────────────────────────────────
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_video = tmp.name
            tmp.write(await file.read())

        logger.info(
            "Assembling %d highlights from %r",
            len(highlights), file.filename,
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

        stem = Path(file.filename or "clip").stem
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
        if tmp_video and os.path.exists(tmp_video):
            os.unlink(tmp_video)
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
