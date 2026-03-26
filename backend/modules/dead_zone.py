"""
Dead Zone Detector
Detects simultaneous audio silence and static video frames in gameplay footage.
Uses ffmpeg for frame/audio extraction, librosa for audio analysis, and
opencv-python for frame comparison.

When `game_audio_path` is supplied (a pre-split full-spectrum WAV produced by
audio_splitter.split_audio), the internal ffmpeg extraction step is skipped and
that file is used directly — avoiding a redundant decode pass.
"""

import os
import json
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import librosa
import cv2

logger = logging.getLogger(__name__)


def detect_dead_zones(
    video_path: str,
    preset: dict,
    sensitivity: float = 1.0,
    progress_callback=None,
    game_audio_path: Optional[str] = None,
) -> list[dict]:
    """
    Detect dead zones (simultaneous audio silence + static frames).

    Args:
        video_path: Path to input video file (used for frame analysis).
        preset: Preset config dict from presets.json.
        sensitivity: Multiplier applied to thresholds (0.5 = more sensitive, 2.0 = less).
        progress_callback: Optional callable(pct: float, label: str) for progress updates.
        game_audio_path: Optional path to a pre-split full-spectrum WAV file.
            When provided the internal ffmpeg extraction is skipped entirely and
            this file is analysed directly (full-spectrum game audio gives more
            accurate silence detection than a vocal-filtered mix).

    Returns:
        List of dicts with keys: start, end, duration, type ("dead_zone"), confidence.
    """
    silence_threshold_db = preset["silence_threshold_db"] * sensitivity
    static_threshold = preset["static_frame_threshold"] * sensitivity
    min_duration = preset["min_dead_zone_duration"]

    def progress(pct, label):
        if progress_callback:
            progress_callback(pct, label)

    if game_audio_path:
        progress(0.0, "Using pre-split game audio track")
        audio_silence_intervals = _detect_audio_silence_from_wav(
            game_audio_path, silence_threshold_db, min_duration
        )
    else:
        progress(0.0, "Extracting audio track")
        audio_silence_intervals = _detect_audio_silence(
            video_path, silence_threshold_db, min_duration
        )

    progress(0.4, "Analyzing video frames")
    video_static_intervals = _detect_static_frames(
        video_path, static_threshold, min_duration
    )

    progress(0.8, "Merging silence + static intervals")
    dead_zones = _intersect_intervals(audio_silence_intervals, video_static_intervals)

    filtered = [dz for dz in dead_zones if dz["duration"] >= min_duration]
    filtered.sort(key=lambda x: x["start"])

    progress(1.0, "Dead zone detection complete")
    return filtered


STRUGGLE_WINDOW_SEC = 600   # 10-minute sliding window
STRUGGLE_MIN_COUNT  = 3     # minimum dead zones to form a struggle zone


def detect_struggle_zones(
    dead_zones: list[dict],
    window_sec: float = STRUGGLE_WINDOW_SEC,
    min_count: int = STRUGGLE_MIN_COUNT,
) -> list[dict]:
    """
    Post-processing pass over detected dead zones.

    A *struggle zone* is declared when ``min_count`` or more dead zones have
    their start times within any ``window_sec``-wide sliding window.  The
    resulting zone spans from the earliest dead zone's start to the latest
    dead zone's end inside that cluster.

    Overlapping struggle windows are merged into one zone so the output list
    is non-overlapping and sorted by start time.

    Args:
        dead_zones: Output of detect_dead_zones(), sorted by start.
        window_sec: Width of the sliding detection window (default: 600 s = 10 min).
        min_count: Minimum dead zones in the window to declare a struggle zone.

    Returns:
        List of dicts with keys:
            start, end, duration, type ("struggle_zone"),
            action ("MONTAGE_CANDIDATE"), dead_zone_count, dead_zone_indices
    """
    if len(dead_zones) < min_count:
        return []

    # Sorted by start (caller should already have done this, but be defensive)
    dzs = sorted(dead_zones, key=lambda x: x["start"])

    raw_zones: list[dict] = []

    for i, anchor in enumerate(dzs):
        window_end = anchor["start"] + window_sec
        # Collect all dead zones whose start falls within the window
        cluster_indices = [
            j for j, dz in enumerate(dzs)
            if anchor["start"] <= dz["start"] <= window_end
        ]
        if len(cluster_indices) >= min_count:
            zone_start = dzs[cluster_indices[0]]["start"]
            zone_end   = max(dzs[j]["end"] for j in cluster_indices)
            raw_zones.append({
                "start": zone_start,
                "end":   zone_end,
                "dead_zone_count": len(cluster_indices),
                "dead_zone_indices": cluster_indices,
            })

    if not raw_zones:
        return []

    # Merge overlapping raw zones
    raw_zones.sort(key=lambda x: x["start"])
    merged: list[dict] = []
    cur = raw_zones[0].copy()

    for nxt in raw_zones[1:]:
        if nxt["start"] <= cur["end"]:
            cur["end"]   = max(cur["end"], nxt["end"])
            cur["dead_zone_count"] = max(cur["dead_zone_count"], nxt["dead_zone_count"])
            # Union of participating indices
            seen = set(cur["dead_zone_indices"]) | set(nxt["dead_zone_indices"])
            cur["dead_zone_indices"] = sorted(seen)
        else:
            merged.append(cur)
            cur = nxt.copy()
    merged.append(cur)

    result = []
    for zone in merged:
        dur = round(zone["end"] - zone["start"], 3)
        result.append({
            "start":             round(zone["start"], 3),
            "end":               round(zone["end"], 3),
            "duration":          dur,
            "type":              "struggle_zone",
            "action":            "MONTAGE_CANDIDATE",
            "dead_zone_count":   zone["dead_zone_count"],
        })

    logger.info(
        "Struggle zone detection: %d zone(s) from %d dead zones (window=%.0fs, min=%d)",
        len(result), len(dzs), window_sec, min_count,
    )
    return result


def _detect_audio_silence(
    video_path: str, threshold_db: float, min_duration: float
) -> list[dict]:
    """Extract full-spectrum audio from a video file, then find silent intervals."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_audio = tmp.name

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "22050", "-ac", "1",
                tmp_audio,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _detect_audio_silence_from_wav(tmp_audio, threshold_db, min_duration)
    except Exception as e:
        logger.warning(f"Audio extraction failed: {e}")
        return []
    finally:
        if os.path.exists(tmp_audio):
            os.unlink(tmp_audio)


def _detect_audio_silence_from_wav(
    wav_path: str, threshold_db: float, min_duration: float
) -> list[dict]:
    """
    Analyse an already-extracted WAV file for silent intervals.

    Accepts the full-spectrum game audio WAV produced by audio_splitter so that
    dead zone detection reacts to *game* sounds (explosions, music, UI) rather
    than the streamer's commentary — which lives in the bandpass-filtered vocals
    track used by hype detection.
    """
    try:
        y, sr = librosa.load(wav_path, sr=22050, mono=True)

        hop_length = 512
        frame_length = 2048
        rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
        rms_db = librosa.amplitude_to_db(rms, ref=np.max)

        frame_times = librosa.frames_to_time(
            np.arange(len(rms)), sr=sr, hop_length=hop_length
        )

        silent_frames = rms_db < threshold_db

        return _frames_to_intervals(frame_times, silent_frames, min_duration, "silence")
    except Exception as e:
        logger.warning(f"Audio silence analysis failed: {e}")
        return []


def _detect_static_frames(
    video_path: str, threshold: float, min_duration: float
) -> list[dict]:
    """Use OpenCV to find intervals where frames are nearly identical (static)."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning(f"Could not open video: {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    # Sample at most 10 fps to keep analysis fast
    sample_every = max(1, int(fps / 10))

    timestamps = []
    is_static = []

    prev_gray = None
    frame_idx = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_every == 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_small = cv2.resize(gray, (160, 90))
                timestamp = frame_idx / fps

                if prev_gray is not None:
                    diff = cv2.absdiff(gray_small, prev_gray)
                    mean_diff = diff.mean() / 255.0
                    is_static_frame = mean_diff < threshold
                else:
                    is_static_frame = False

                timestamps.append(timestamp)
                is_static.append(is_static_frame)
                prev_gray = gray_small

            frame_idx += 1
    finally:
        cap.release()

    return _frames_to_intervals(
        np.array(timestamps), np.array(is_static), min_duration, "static"
    )


def _frames_to_intervals(
    times: np.ndarray,
    flags: np.ndarray,
    min_duration: float,
    label: str,
) -> list[dict]:
    """Convert boolean flag arrays into contiguous intervals."""
    if len(times) == 0:
        return []

    intervals = []
    in_interval = False
    start = None

    for i, (t, flag) in enumerate(zip(times, flags)):
        if flag and not in_interval:
            in_interval = True
            start = float(t)
        elif not flag and in_interval:
            end = float(t)
            duration = end - start
            if duration >= min_duration:
                intervals.append({"start": start, "end": end, "duration": duration})
            in_interval = False

    if in_interval and start is not None:
        end = float(times[-1])
        duration = end - start
        if duration >= min_duration:
            intervals.append({"start": start, "end": end, "duration": duration})

    return intervals


def _intersect_intervals(
    intervals_a: list[dict], intervals_b: list[dict]
) -> list[dict]:
    """Return overlapping portions of two interval lists as dead zones."""
    if not intervals_a or not intervals_b:
        # If either detector found nothing (e.g., audio-only / video processing failed),
        # return whichever list has results as a fallback.
        return [
            {
                "start": iv["start"],
                "end": iv["end"],
                "duration": iv["duration"],
                "type": "dead_zone",
                "confidence": 0.5,
            }
            for iv in (intervals_a or intervals_b)
        ]

    results = []
    for a in intervals_a:
        for b in intervals_b:
            start = max(a["start"], b["start"])
            end = min(a["end"], b["end"])
            if end > start:
                duration = end - start
                # Confidence based on overlap ratio
                union = max(a["end"], b["end"]) - min(a["start"], b["start"])
                confidence = round(duration / union, 3) if union > 0 else 1.0
                results.append(
                    {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "duration": round(duration, 3),
                        "type": "dead_zone",
                        "confidence": confidence,
                    }
                )
    return results
