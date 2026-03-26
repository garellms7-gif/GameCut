"""
Preset Detector
Analyzes the first 90 seconds of a gameplay video and classifies it into one
of the five preset categories using four audio-visual signals:

  1. audio_energy_db       — mean RMS energy; loud vs. quiet game audio
  2. spectral_centroid_norm— normalized spectral centroid; bright/punchy vs. bass-heavy
  3. frame_change_rate     — mean absolute frame difference; dynamic vs. static camera
  4. color_saturation_mean — mean HSV saturation; vibrant vs. muted art style

Each signal is measured against per-preset reference profiles.  Distances are
converted to a confidence distribution via softmax so all scores sum to 1.0.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import librosa
import numpy as np

logger = logging.getLogger(__name__)

# ── Sampling parameters ───────────────────────────────────────────────────────
SAMPLE_DURATION_SEC = 90     # analyse first N seconds
VIDEO_SAMPLE_FPS    = 1      # 1 frame/second for frame change & colour analysis
AUDIO_SR            = 22050  # librosa sample rate

# ── Per-preset reference profiles ────────────────────────────────────────────
# Values are empirically chosen target centroids for each signal × preset.
# The classifier finds the nearest preset in this 4D feature space.
#
#  audio_energy_db       : mean programme loudness (dBFS-ish, negative values)
#  spectral_centroid_norm: spectral centroid as fraction of Nyquist [0, 1]
#  frame_change_rate     : mean inter-frame MAD (0 = static, 1 = fully random)
#  color_saturation_mean : mean HSV saturation [0, 1]
PRESET_PROFILES: dict[str, dict[str, float]] = {
    "fps": {
        "audio_energy_db":        -28.0,
        "spectral_centroid_norm":   0.38,
        "frame_change_rate":        0.055,
        "color_saturation_mean":    0.22,
    },
    "fighting": {
        "audio_energy_db":        -26.0,
        "spectral_centroid_norm":   0.46,
        "frame_change_rate":        0.062,
        "color_saturation_mean":    0.56,
    },
    "platformer": {
        "audio_energy_db":        -34.0,
        "spectral_centroid_norm":   0.42,
        "frame_change_rate":        0.034,
        "color_saturation_mean":    0.52,
    },
    "open_world": {
        "audio_energy_db":        -40.0,
        "spectral_centroid_norm":   0.28,
        "frame_change_rate":        0.018,
        "color_saturation_mean":    0.30,
    },
    "rpg": {
        "audio_energy_db":        -45.0,
        "spectral_centroid_norm":   0.22,
        "frame_change_rate":        0.010,
        "color_saturation_mean":    0.38,
    },
}

FEATURE_KEYS = list(next(iter(PRESET_PROFILES.values())).keys())

# Per-feature scale factors (≈ range across profiles) used for normalisation
# so every dimension contributes equally regardless of magnitude.
_SCALES: dict[str, float] = {
    key: max(p[key] for p in PRESET_PROFILES.values())
         - min(p[key] for p in PRESET_PROFILES.values())
    for key in FEATURE_KEYS
}


# ── Public API ────────────────────────────────────────────────────────────────

def detect_preset(video_path: str) -> dict:
    """
    Analyse a video and return the best-matching preset with confidence scores.

    Returns:
        {
          "detected_preset": str,
          "confidence": float,          # top-1 softmax score
          "scores": {preset: float, …}, # full softmax distribution
          "signals": {feature: value, …},
        }
    """
    signals = extract_signals(video_path)
    scores  = classify_preset(signals)
    best    = max(scores, key=lambda k: scores[k])

    return {
        "detected_preset": best,
        "confidence": round(scores[best], 4),
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "signals": {k: round(v, 4) for k, v in signals.items()},
    }


def extract_signals(video_path: str, sample_duration: float = SAMPLE_DURATION_SEC) -> dict[str, float]:
    """
    Extract the four classification signals from the first `sample_duration`
    seconds of the video.  Falls back gracefully if audio or video is missing.
    """
    duration = _probe_duration(video_path)
    clip_dur = min(sample_duration, duration) if duration > 0 else sample_duration

    audio_energy_db, spectral_centroid_norm = _audio_signals(video_path, clip_dur)
    frame_change_rate, color_saturation_mean = _video_signals(video_path, clip_dur)

    return {
        "audio_energy_db":        audio_energy_db,
        "spectral_centroid_norm":  spectral_centroid_norm,
        "frame_change_rate":       frame_change_rate,
        "color_saturation_mean":   color_saturation_mean,
    }


def classify_preset(signals: dict[str, float]) -> dict[str, float]:
    """
    Compute a softmax confidence distribution over all presets given the
    observed signals.  Uses normalised Euclidean distance from each preset
    profile, then applies softmax with a sharpening temperature.

    Returns a dict mapping preset name → confidence score (sums to 1.0).
    """
    distances: dict[str, float] = {}

    for preset, profile in PRESET_PROFILES.items():
        sq_sum = 0.0
        for key in FEATURE_KEYS:
            scale = _SCALES[key] if _SCALES[key] > 0 else 1.0
            diff  = (signals[key] - profile[key]) / scale
            sq_sum += diff * diff
        distances[preset] = sq_sum ** 0.5

    # Softmax over negative distances (temperature=4 sharpens low-confidence cases)
    temperature = 4.0
    neg_dists   = np.array([-distances[p] * temperature for p in PRESET_PROFILES])
    exp_vals    = np.exp(neg_dists - neg_dists.max())   # numerically stable
    softmax     = exp_vals / exp_vals.sum()

    return {p: float(softmax[i]) for i, p in enumerate(PRESET_PROFILES)}


# ── Signal extraction helpers ─────────────────────────────────────────────────

def _probe_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         video_path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _audio_signals(video_path: str, clip_dur: float) -> tuple[float, float]:
    """
    Return (audio_energy_db, spectral_centroid_norm) from the first clip_dur
    seconds.  Defaults to -60 dB / 0.25 if extraction fails.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "0", "-t", str(clip_dur),
                "-i", video_path,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", str(AUDIO_SR), "-ac", "1",
                tmp_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return -60.0, 0.25

        y, sr = librosa.load(tmp_path, sr=AUDIO_SR, mono=True)
        if len(y) < 1024:
            return -60.0, 0.25

        # RMS energy in dB
        rms     = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        mean_db = float(librosa.amplitude_to_db(np.mean(rms) + 1e-9))

        # Spectral centroid normalised to [0, 1] relative to Nyquist
        centroid     = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=512)[0]
        nyquist      = sr / 2.0
        centroid_norm = float(np.mean(centroid) / nyquist)

        return float(mean_db), float(np.clip(centroid_norm, 0.0, 1.0))

    except Exception as e:
        logger.warning(f"Audio signal extraction failed: {e}")
        return -60.0, 0.25
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def _video_signals(video_path: str, clip_dur: float) -> tuple[float, float]:
    """
    Return (frame_change_rate, color_saturation_mean) by sampling at
    VIDEO_SAMPLE_FPS from the first clip_dur seconds.

    Frames are resized to 160×90 for speed.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0.02, 0.35

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    max_frames   = int(min(clip_dur * fps, total_frames))
    sample_every = max(1, int(fps / VIDEO_SAMPLE_FPS))

    frame_diffs:  list[float] = []
    saturations:  list[float] = []
    prev_gray = None

    try:
        frame_idx = 0
        while frame_idx < max_frames:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_every == 0:
                small = cv2.resize(frame, (160, 90))

                # Frame change rate
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    frame_diffs.append(float(diff.mean()) / 255.0)
                prev_gray = gray

                # Colour saturation
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                sat = hsv[:, :, 1].astype(np.float32) / 255.0
                saturations.append(float(sat.mean()))

            frame_idx += 1
    finally:
        cap.release()

    fcr = float(np.mean(frame_diffs))  if frame_diffs  else 0.02
    csat = float(np.mean(saturations)) if saturations else 0.35
    return fcr, csat
