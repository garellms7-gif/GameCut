"""
Audio Splitter
Uses a single ffmpeg pass to produce two separated audio tracks from a video:

  - vocals_path   : 80 Hz – 3 kHz bandpass  → isolates mic / commentary
  - game_audio_path: full spectrum           → captures game sound events

The bandpass is implemented as a chained highpass + lowpass filter, which
ffmpeg applies before PCM encoding so no extra decoding step is needed.

Usage (context manager — files are cleaned up automatically):

    with split_audio(video_path) as tracks:
        y_game, sr = librosa.load(tracks.game_audio_path, ...)
        y_voc, sr  = librosa.load(tracks.vocals_path, ...)
"""

import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Voice fundamental frequency range used for the bandpass filter
VOCALS_LOWCUT_HZ = 80
VOCALS_HIGHCUT_HZ = 3000

# Sample rate used for all extracted WAVs
SAMPLE_RATE = 22050


@dataclass
class AudioTracks:
    """Paths to the two extracted audio WAV files."""
    game_audio_path: str   # full-spectrum game sound
    vocals_path: str       # 80 Hz – 3 kHz bandpass (mic / commentary)
    sample_rate: int = SAMPLE_RATE
    # Metadata surfaced in the API response
    vocals_filter: str = f"highpass=f={VOCALS_LOWCUT_HZ},lowpass=f={VOCALS_HIGHCUT_HZ}"
    game_audio_filter: str = "none (full spectrum)"


@contextmanager
def split_audio(video_path: str):
    """
    Context manager that splits a video's audio into two WAV files.

    Yields an AudioTracks instance. Both files are deleted on exit regardless
    of whether an exception occurred.

    Raises:
        RuntimeError: if ffmpeg fails and no fallback is possible.
    """
    game_tmp = tempfile.NamedTemporaryFile(suffix="_game.wav", delete=False)
    voc_tmp = tempfile.NamedTemporaryFile(suffix="_vocals.wav", delete=False)
    game_path = game_tmp.name
    voc_path = voc_tmp.name
    game_tmp.close()
    voc_tmp.close()

    try:
        _run_split(video_path, game_path, voc_path)
        yield AudioTracks(game_audio_path=game_path, vocals_path=voc_path)
    finally:
        for p in (game_path, voc_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def _run_split(video_path: str, game_path: str, voc_path: str) -> None:
    """
    Run a single ffmpeg invocation that writes both output files in parallel.

    Command breakdown:
      -map 0:a:0  — use first audio stream for both outputs
      First output  : game audio — no filter, full spectrum
      Second output : vocals — highpass(80 Hz) → lowpass(3 kHz) bandpass chain
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        # ── Output 1: game audio (full spectrum) ──────────────────────────
        "-map", "0:a:0",
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        game_path,
        # ── Output 2: vocals (80 Hz – 3 kHz bandpass) ────────────────────
        "-map", "0:a:0",
        "-vn",
        "-af", f"highpass=f={VOCALS_LOWCUT_HZ},lowpass=f={VOCALS_HIGHCUT_HZ}",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        voc_path,
    ]

    logger.info(
        "Splitting audio: game_audio=%s  vocals=%s  filter=%s–%s Hz",
        os.path.basename(game_path),
        os.path.basename(voc_path),
        VOCALS_LOWCUT_HZ,
        VOCALS_HIGHCUT_HZ,
    )

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        # Surface the ffmpeg stderr for easier debugging
        stderr_tail = result.stderr[-800:] if result.stderr else "(no output)"
        raise RuntimeError(
            f"ffmpeg audio split failed (exit {result.returncode}):\n{stderr_tail}"
        )

    # Verify both outputs were actually written
    for label, path in (("game audio", game_path), ("vocals", voc_path)):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError(
                f"ffmpeg produced an empty {label} file: {path}\n"
                "The video may have no audio stream."
            )

    logger.info(
        "Audio split complete — game: %.1f KB  vocals: %.1f KB",
        os.path.getsize(game_path) / 1024,
        os.path.getsize(voc_path) / 1024,
    )
