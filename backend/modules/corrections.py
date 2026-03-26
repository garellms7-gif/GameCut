"""
Corrections Engine
Stores user thumbs-up / thumbs-down feedback on detected segments and uses
it to nudge detection thresholds on future analyses.

Storage: corrections.json sits next to this module's package root.
Schema per entry (keyed by segment class hash):
  {
    "preset":          "fps",
    "segment_type":    "dead_zone" | "highlight",
    "score_bucket":    "0-25" | "25-50" | "50-75" | "75-100",
    "duration_bucket": "short" | "medium" | "long",
    "votes_up":        int,
    "votes_down":      int,
    "last_updated":    "ISO-8601 timestamp"
  }

Hash inputs: (preset, segment_type, score_bucket, duration_bucket)
Using these four buckets means feedback on one segment generalises to all
segments of the same class, so a handful of corrections meaningfully shifts
the thresholds rather than requiring one vote per exact timestamp.

Threshold adjustment logic
──────────────────────────
net_signal = (votes_down - votes_up) / total_votes   ∈ [-1, +1]

dead_zone  net > 0  →  too many false dead-zones flagged
           silence_threshold_db:   lower by up to MAX_DB_SHIFT (more negative
                                   = only flag truly silent audio)
           static_frame_threshold: lower by up to MAX_FRAME_SHIFT
           min_dead_zone_duration: raise by up to MAX_DUR_SHIFT

highlight  net > 0  →  too many false highlights flagged
           hype_volume_spike_db:  raise by up to MAX_DB_SHIFT (require louder spike)
           hype_pitch_rise_hz:    raise by up to MAX_PITCH_SHIFT

Adjustments are clamped to prevent runaway drift.  The raw preset values are
never modified; adjusted copies are returned and used only for one analysis run.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CORRECTIONS_PATH = Path(__file__).parent.parent / "corrections.json"

# Maximum threshold shifts applied by corrections
MAX_DB_SHIFT       = 8.0   # dB — silence_threshold_db / hype_volume_spike_db
MAX_FRAME_SHIFT    = 0.012  # static_frame_threshold units
MAX_DUR_SHIFT      = 3.0   # seconds — min duration fields
MAX_PITCH_SHIFT    = 40.0  # Hz — hype_pitch_rise_hz

# How aggressively each net vote translates to a shift (scale factor)
CORRECTION_SCALE   = 0.5   # net_signal * CORRECTION_SCALE → fraction of MAX_*_SHIFT applied


# ── Hashing ───────────────────────────────────────────────────────────────────

def _score_bucket(score: Optional[float]) -> str:
    """Bin a 0–1 detection score into one of four named ranges."""
    if score is None:
        return "unknown"
    if score < 0.25:
        return "0-25"
    if score < 0.50:
        return "25-50"
    if score < 0.75:
        return "50-75"
    return "75-100"


def _duration_bucket(duration: float) -> str:
    """Bin a duration (seconds) into short / medium / long."""
    if duration < 3.0:
        return "short"
    if duration < 10.0:
        return "medium"
    return "long"


def segment_hash(
    preset: str,
    segment_type: str,
    score: Optional[float],
    duration: float,
) -> str:
    """
    Return a deterministic 12-character hex hash that identifies a *class* of
    segment.  Two segments with the same preset, type, score bucket, and
    duration bucket produce the same hash and share feedback.
    """
    sb = _score_bucket(score)
    db = _duration_bucket(duration)
    key = f"{preset}|{segment_type}|{sb}|{db}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ── Persistence ───────────────────────────────────────────────────────────────

def load_corrections() -> dict:
    """Return the full corrections dict (empty dict if the file doesn't exist)."""
    if not CORRECTIONS_PATH.exists():
        return {}
    try:
        with open(CORRECTIONS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load corrections.json: {e}")
        return {}


def save_correction(
    preset: str,
    segment_type: str,
    vote: str,          # "up" | "down"
    score: Optional[float],
    duration: float,
) -> dict:
    """
    Record one vote for the matching segment class.

    Returns the updated entry for that hash (useful for the API response).
    Raises ValueError on invalid vote string.
    """
    if vote not in ("up", "down"):
        raise ValueError(f"vote must be 'up' or 'down', got {vote!r}")

    h = segment_hash(preset, segment_type, score, duration)
    corrections = load_corrections()

    entry = corrections.get(h) or {
        "preset": preset,
        "segment_type": segment_type,
        "score_bucket": _score_bucket(score),
        "duration_bucket": _duration_bucket(duration),
        "votes_up": 0,
        "votes_down": 0,
    }

    if vote == "up":
        entry["votes_up"] = entry.get("votes_up", 0) + 1
    else:
        entry["votes_down"] = entry.get("votes_down", 0) + 1

    entry["last_updated"] = datetime.now(timezone.utc).isoformat()
    corrections[h] = entry

    try:
        CORRECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CORRECTIONS_PATH, "w") as f:
            json.dump(corrections, f, indent=2)
        logger.info(
            "Saved correction %s: preset=%s type=%s vote=%s  up=%d down=%d",
            h, preset, segment_type, vote,
            entry["votes_up"], entry["votes_down"],
        )
    except OSError as e:
        logger.error(f"Could not write corrections.json: {e}")

    return {"hash": h, **entry}


# ── Threshold adjustment ───────────────────────────────────────────────────────

def _net_signal(votes_up: int, votes_down: int) -> float:
    """
    Net correction signal in [-1, +1].
    Positive → more thumbs-down → detector is too aggressive.
    Negative → more thumbs-up  → detector is well-calibrated (or too lenient).
    """
    total = votes_up + votes_down
    if total == 0:
        return 0.0
    return (votes_down - votes_up) / total


def apply_corrections(preset_key: str, preset: dict) -> tuple[dict, dict]:
    """
    Return a *copy* of the preset config with thresholds nudged based on
    accumulated corrections for this preset.

    Also returns a ``adjustments`` dict describing what changed (for API transparency).

    The raw preset dict is never mutated.
    """
    corrections = load_corrections()
    if not corrections:
        return dict(preset), {}

    # Aggregate net signals per segment type for this preset
    dz_signals: list[float] = []
    hl_signals: list[float] = []

    for entry in corrections.values():
        if entry.get("preset") != preset_key:
            continue
        net = _net_signal(entry.get("votes_up", 0), entry.get("votes_down", 0))
        if net == 0.0:
            continue
        stype = entry.get("segment_type")
        if stype == "dead_zone":
            dz_signals.append(net)
        elif stype == "highlight":
            hl_signals.append(net)

    adjusted = dict(preset)
    adjustments: dict = {}

    # ── Dead zone adjustments ─────────────────────────────────────────────────
    if dz_signals:
        avg_dz = sum(dz_signals) / len(dz_signals)
        shift_fraction = avg_dz * CORRECTION_SCALE   # fraction of max shift to apply

        if shift_fraction > 0.01 or shift_fraction < -0.01:
            # silence_threshold_db: lower (more negative) to reduce false positives
            # or raise (less negative) to catch more silence when under-detecting
            db_delta = -shift_fraction * MAX_DB_SHIFT
            old_db = preset["silence_threshold_db"]
            new_db = round(old_db + db_delta, 2)
            adjusted["silence_threshold_db"] = new_db
            adjustments["silence_threshold_db"] = {
                "original": old_db, "adjusted": new_db, "delta": round(db_delta, 2)
            }

            # static_frame_threshold: lower to reduce false positives
            frame_delta = -shift_fraction * MAX_FRAME_SHIFT
            old_frame = preset["static_frame_threshold"]
            new_frame = round(max(0.002, old_frame + frame_delta), 4)
            adjusted["static_frame_threshold"] = new_frame
            adjustments["static_frame_threshold"] = {
                "original": old_frame, "adjusted": new_frame, "delta": round(frame_delta, 4)
            }

            # min_dead_zone_duration: increase to require longer zones when over-detecting
            dur_delta = shift_fraction * MAX_DUR_SHIFT
            old_dur = preset["min_dead_zone_duration"]
            new_dur = round(max(0.5, old_dur + dur_delta), 2)
            adjusted["min_dead_zone_duration"] = new_dur
            adjustments["min_dead_zone_duration"] = {
                "original": old_dur, "adjusted": new_dur, "delta": round(dur_delta, 2)
            }

    # ── Highlight adjustments ─────────────────────────────────────────────────
    if hl_signals:
        avg_hl = sum(hl_signals) / len(hl_signals)
        shift_fraction = avg_hl * CORRECTION_SCALE

        if shift_fraction > 0.01 or shift_fraction < -0.01:
            # hype_volume_spike_db: raise to demand louder spikes when over-detecting
            spike_delta = shift_fraction * MAX_DB_SHIFT
            old_spike = preset["hype_volume_spike_db"]
            new_spike = round(max(3.0, old_spike + spike_delta), 2)
            adjusted["hype_volume_spike_db"] = new_spike
            adjustments["hype_volume_spike_db"] = {
                "original": old_spike, "adjusted": new_spike, "delta": round(spike_delta, 2)
            }

            # hype_pitch_rise_hz: raise to demand bigger pitch jumps
            pitch_delta = shift_fraction * MAX_PITCH_SHIFT
            old_pitch = preset["hype_pitch_rise_hz"]
            new_pitch = round(max(10.0, old_pitch + pitch_delta), 2)
            adjusted["hype_pitch_rise_hz"] = new_pitch
            adjustments["hype_pitch_rise_hz"] = {
                "original": old_pitch, "adjusted": new_pitch, "delta": round(pitch_delta, 2)
            }

    if adjustments:
        logger.info(
            "Applied %d correction(s) to preset '%s': %s",
            len(dz_signals) + len(hl_signals),
            preset_key,
            {k: v["delta"] for k, v in adjustments.items()},
        )

    return adjusted, adjustments


def correction_summary(preset_key: str) -> dict:
    """
    Return a summary of all corrections recorded for a preset (for the API).
    """
    corrections = load_corrections()
    entries = {h: e for h, e in corrections.items() if e.get("preset") == preset_key}
    total_up = sum(e.get("votes_up", 0) for e in entries.values())
    total_down = sum(e.get("votes_down", 0) for e in entries.values())
    return {
        "preset": preset_key,
        "total_corrections": len(entries),
        "total_votes_up": total_up,
        "total_votes_down": total_down,
        "entries": entries,
    }
