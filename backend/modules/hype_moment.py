"""
Hype Moment Detector
Analyzes the microphone/commentary track for:
  - Volume spikes (RMS energy peaks)
  - Pitch elevation (fundamental frequency increases)
  - Speech rate increases (onset density)

Returns a ranked list of timestamped highlight moments with composite scores.

When `vocals_path` is supplied (an 80 Hz – 3 kHz bandpass WAV produced by
audio_splitter.split_audio), the internal ffmpeg extraction is skipped and
that file is analysed directly, giving cleaner pitch and speech-rate readings
by eliminating low-frequency game rumble and high-frequency SFX noise.
"""

import os
import subprocess
import tempfile
import logging
from typing import Optional, Callable

import numpy as np
import librosa

logger = logging.getLogger(__name__)

# Weights for composite hype score
WEIGHT_VOLUME = 0.45
WEIGHT_PITCH = 0.30
WEIGHT_SPEECH_RATE = 0.25


def detect_hype_moments(
    video_path: str,
    preset: dict,
    sensitivity: float = 1.0,
    progress_callback: Optional[Callable] = None,
    vocals_path: Optional[str] = None,
) -> list[dict]:
    """
    Detect hype moments from the mic/commentary audio track.

    Args:
        video_path: Path to input video file (only used when vocals_path is None).
        preset: Preset config dict from presets.json.
        sensitivity: 0.5 = more sensitive, 2.0 = less sensitive.
        progress_callback: Optional callable(pct: float, label: str).
        vocals_path: Optional path to a pre-split 80 Hz – 3 kHz bandpass WAV.
            When provided the internal ffmpeg extraction is skipped entirely.
            Using the bandpass-filtered vocals track removes low-frequency game
            rumble and high-frequency SFX that would otherwise pollute pitch and
            speech-rate measurements.

    Returns:
        List of dicts sorted by composite_score descending, each with:
        start, end, duration, type ("highlight"), composite_score,
        volume_score, pitch_score, speech_rate_score.
    """
    volume_spike_db = preset["hype_volume_spike_db"] / sensitivity
    pitch_rise_hz = preset["hype_pitch_rise_hz"] / sensitivity
    speech_rate_mult = 1.0 + (preset["speech_rate_multiplier"] - 1.0) / sensitivity
    min_duration = preset["min_hype_duration"]

    def progress(pct, label):
        if progress_callback:
            progress_callback(pct, label)

    if vocals_path:
        progress(0.0, "Using pre-split vocals track (80 Hz – 3 kHz bandpass)")
        try:
            y, sr = librosa.load(vocals_path, sr=22050, mono=True)
        except Exception as e:
            logger.error(f"Failed to load vocals WAV: {e}")
            return []
    else:
        progress(0.0, "Extracting commentary track")
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
            y, sr = librosa.load(tmp_audio, sr=22050, mono=True)
        except Exception as e:
            logger.error(f"Failed to extract audio: {e}")
            return []
        finally:
            if os.path.exists(tmp_audio):
                os.unlink(tmp_audio)

    progress(0.2, "Computing volume envelope")
    volume_scores, frame_times = _compute_volume_scores(y, sr, volume_spike_db)

    progress(0.45, "Estimating pitch (F0)")
    pitch_scores = _compute_pitch_scores(y, sr, frame_times, pitch_rise_hz)

    progress(0.65, "Measuring speech rate via onset density")
    speech_scores = _compute_speech_rate_scores(y, sr, frame_times, speech_rate_mult)

    progress(0.80, "Computing composite hype scores")
    composite = (
        WEIGHT_VOLUME * volume_scores
        + WEIGHT_PITCH * pitch_scores
        + WEIGHT_SPEECH_RATE * speech_scores
    )

    progress(0.90, "Extracting highlight windows")
    highlights = _extract_highlight_windows(
        composite, volume_scores, pitch_scores, speech_scores,
        frame_times, min_duration, threshold_percentile=75,
    )

    highlights.sort(key=lambda x: x["composite_score"], reverse=True)
    progress(1.0, "Hype detection complete")
    return highlights


def _compute_volume_scores(
    y: np.ndarray, sr: int, spike_db: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return per-frame normalised volume scores and their timestamps."""
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-9, ref=np.max)

    # Baseline = rolling 5s median
    window = max(1, int(5 * sr / hop))
    baseline = np.array(
        [np.median(rms_db[max(0, i - window):i + 1]) for i in range(len(rms_db))]
    )
    above_baseline = np.clip(rms_db - baseline, 0, None)
    scores = np.clip(above_baseline / spike_db, 0, 1)

    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    return scores.astype(np.float32), times.astype(np.float32)


def _compute_pitch_scores(
    y: np.ndarray, sr: int, frame_times: np.ndarray, pitch_rise_hz: float
) -> np.ndarray:
    """Return per-frame normalised pitch-elevation scores."""
    try:
        hop = 512
        f0, voiced_flag, _ = librosa.pyin(
            y, fmin=80, fmax=600, sr=sr, hop_length=hop,
            frame_length=2048,
        )

        # Interpolate NaNs
        valid = ~np.isnan(f0)
        if valid.sum() < 10:
            return np.zeros(len(frame_times), dtype=np.float32)

        f0_filled = np.copy(f0)
        indices = np.arange(len(f0))
        f0_filled = np.interp(indices, indices[valid], f0[valid])

        # Baseline = rolling 5s median
        n_frames = len(f0_filled)
        hop_sec = hop / sr
        window = max(1, int(5.0 / hop_sec))
        baseline = np.array(
            [np.median(f0_filled[max(0, i - window):i + 1]) for i in range(n_frames)]
        )
        above_baseline = np.clip(f0_filled - baseline, 0, None)
        scores = np.clip(above_baseline / pitch_rise_hz, 0, 1)

        # Resize to match frame_times length
        if len(scores) != len(frame_times):
            scores = np.interp(
                np.linspace(0, 1, len(frame_times)),
                np.linspace(0, 1, len(scores)),
                scores,
            )
        return scores.astype(np.float32)
    except Exception as e:
        logger.warning(f"Pitch analysis failed: {e}")
        return np.zeros(len(frame_times), dtype=np.float32)


def _compute_speech_rate_scores(
    y: np.ndarray, sr: int, frame_times: np.ndarray, rate_multiplier: float
) -> np.ndarray:
    """Return per-frame normalised speech-rate scores based on onset density."""
    hop = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop, backtrack=False
    )

    # Compute onset density in a 2-second sliding window
    window_sec = 2.0
    window_frames = int(window_sec * sr / hop)
    density = np.zeros(len(frame_times), dtype=np.float32)

    for i in range(len(frame_times)):
        lo = max(0, i - window_frames // 2)
        hi = min(len(frame_times), i + window_frames // 2)
        count = np.sum((onset_frames >= lo) & (onset_frames < hi))
        density[i] = float(count)

    if density.max() > 0:
        baseline = np.median(density[density > 0]) if (density > 0).any() else 1.0
        scores = np.clip((density / (baseline * rate_multiplier)), 0, 1)
    else:
        scores = density

    return scores


def _extract_highlight_windows(
    composite: np.ndarray,
    volume: np.ndarray,
    pitch: np.ndarray,
    speech: np.ndarray,
    times: np.ndarray,
    min_duration: float,
    threshold_percentile: float = 75,
) -> list[dict]:
    """Group high-score frames into highlight windows."""
    if len(composite) == 0:
        return []

    threshold = np.percentile(composite, threshold_percentile)
    high = composite >= threshold

    # Merge nearby segments (gap < min_duration/2)
    hop_sec = float(times[1] - times[0]) if len(times) > 1 else 0.023
    gap_frames = max(1, int((min_duration / 2) / hop_sec))

    # Close small gaps
    for i in range(1, len(high)):
        if not high[i] and i + gap_frames < len(high):
            if high[i - 1] and any(high[i:i + gap_frames]):
                high[i] = True

    results = []
    in_seg = False
    seg_start = None

    for i, (t, flag) in enumerate(zip(times, high)):
        if flag and not in_seg:
            in_seg = True
            seg_start = i
        elif not flag and in_seg:
            seg_end = i
            _add_segment(results, composite, volume, pitch, speech, times,
                         seg_start, seg_end, min_duration)
            in_seg = False

    if in_seg:
        _add_segment(results, composite, volume, pitch, speech, times,
                     seg_start, len(times), min_duration)

    return results


def _add_segment(results, composite, volume, pitch, speech, times,
                 start_idx, end_idx, min_duration):
    start_t = float(times[start_idx])
    end_t = float(times[min(end_idx, len(times) - 1)])
    duration = end_t - start_t
    if duration < min_duration:
        return

    seg_comp = composite[start_idx:end_idx]
    seg_vol = volume[start_idx:end_idx]
    seg_pit = pitch[start_idx:end_idx]
    seg_spe = speech[start_idx:end_idx]

    results.append({
        "start": round(start_t, 3),
        "end": round(end_t, 3),
        "duration": round(duration, 3),
        "type": "highlight",
        "composite_score": round(float(np.mean(seg_comp)), 4),
        "volume_score": round(float(np.mean(seg_vol)), 4),
        "pitch_score": round(float(np.mean(seg_pit)), 4),
        "speech_rate_score": round(float(np.mean(seg_spe)), 4),
    })
