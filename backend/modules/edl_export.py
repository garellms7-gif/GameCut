"""
EDL Exporter
Merges dead zone and hype moment results into a unified Edit Decision List.
Outputs JSON, CSV, and DaVinci Resolve-compatible .edl formats.
"""

import csv
import io
import json
import math
from typing import Union


def build_edl(
    dead_zones: list[dict],
    highlights: list[dict],
    video_duration: float,
    source_name: str = "CLIP_001",
    struggle_zones: list[dict] | None = None,
) -> dict:
    """
    Merge dead zones, highlights, and struggle zones into a unified EDL.

    Segments that are neither dead zones nor highlights are labelled 'keep'.
    Segments that are both highlights and overlap a dead zone give priority to highlight.

    Struggle zones are stored as a separate top-level list and do NOT replace
    individual dead zone segments in the flat timeline — they are an annotation
    layer that editors can use to identify montage-candidate windows.

    Returns a dict with keys:
        segments:       flat timeline list (dead_zone / highlight / keep)
        struggle_zones: list of struggle zone windows (may be empty)
        summary:        overall stats
    """
    # Merge all known intervals
    all_events: list[dict] = []

    for dz in dead_zones:
        all_events.append({
            "start": dz["start"],
            "end": dz["end"],
            "duration": dz["duration"],
            "type": "dead_zone",
            "score": dz.get("confidence", 1.0),
        })

    for hl in highlights:
        all_events.append({
            "start": hl["start"],
            "end": hl["end"],
            "duration": hl["duration"],
            "type": "highlight",
            "score": hl.get("composite_score", 1.0),
        })

    # Sort all events by start time
    all_events.sort(key=lambda x: x["start"])

    # Build a flat timeline of all covered intervals, resolving overlaps
    # Highlights win over dead_zones; keep fills gaps
    covered: list[dict] = _resolve_overlaps(all_events)

    # Fill gaps with 'keep' segments
    segments: list[dict] = []
    cursor = 0.0

    for seg in covered:
        if seg["start"] > cursor + 0.001:
            gap_duration = seg["start"] - cursor
            segments.append({
                "start": round(cursor, 3),
                "end": round(seg["start"], 3),
                "duration": round(gap_duration, 3),
                "type": "keep",
                "score": None,
            })
        segments.append(seg)
        cursor = seg["end"]

    # Final trailing keep segment
    if cursor < video_duration - 0.001:
        segments.append({
            "start": round(cursor, 3),
            "end": round(video_duration, 3),
            "duration": round(video_duration - cursor, 3),
            "type": "keep",
            "score": None,
        })

    summary = _build_summary(segments, video_duration, struggle_zones or [])

    return {
        "segments": segments,
        "struggle_zones": struggle_zones or [],
        "summary": summary,
        "source_name": source_name,
    }


def _resolve_overlaps(events: list[dict]) -> list[dict]:
    """Flatten overlapping intervals; highlights beat dead_zones."""
    if not events:
        return []

    merged: list[dict] = []
    current = events[0].copy()

    for next_ev in events[1:]:
        if next_ev["start"] < current["end"]:
            # Overlap — pick winner
            if next_ev["type"] == "highlight" or current["type"] != "highlight":
                winner_type = next_ev["type"] if next_ev["type"] == "highlight" else current["type"]
                winner_score = next_ev["score"] if winner_type == next_ev["type"] else current["score"]
                current = {
                    "start": min(current["start"], next_ev["start"]),
                    "end": max(current["end"], next_ev["end"]),
                    "type": winner_type,
                    "score": winner_score,
                }
                current["duration"] = round(current["end"] - current["start"], 3)
            else:
                # Current is highlight and dominates
                current["end"] = max(current["end"], next_ev["end"])
                current["duration"] = round(current["end"] - current["start"], 3)
        else:
            merged.append({k: round(v, 3) if isinstance(v, float) else v
                           for k, v in current.items()})
            current = next_ev.copy()

    merged.append({k: round(v, 3) if isinstance(v, float) else v
                   for k, v in current.items()})
    return merged


def _build_summary(
    segments: list[dict],
    total_duration: float,
    struggle_zones: list[dict],
) -> dict:
    dead_duration = sum(s["duration"] for s in segments if s["type"] == "dead_zone")
    highlight_duration = sum(s["duration"] for s in segments if s["type"] == "highlight")
    keep_duration = sum(s["duration"] for s in segments if s["type"] == "keep")
    struggle_duration = sum(sz["duration"] for sz in struggle_zones)

    return {
        "total_duration": round(total_duration, 3),
        "dead_zone_duration": round(dead_duration, 3),
        "highlight_duration": round(highlight_duration, 3),
        "keep_duration": round(keep_duration, 3),
        "struggle_zone_duration": round(struggle_duration, 3),
        "dead_zone_count": sum(1 for s in segments if s["type"] == "dead_zone"),
        "highlight_count": sum(1 for s in segments if s["type"] == "highlight"),
        "keep_count": sum(1 for s in segments if s["type"] == "keep"),
        "struggle_zone_count": len(struggle_zones),
        "cut_savings_pct": round(dead_duration / total_duration * 100, 1) if total_duration > 0 else 0,
        "highlight_pct": round(highlight_duration / total_duration * 100, 1) if total_duration > 0 else 0,
    }


# ─── Export helpers ────────────────────────────────────────────────────────────

def to_json(edl: dict) -> str:
    return json.dumps(edl, indent=2)


def to_csv(edl: dict) -> str:
    output = io.StringIO()
    fieldnames = ["index", "start", "end", "duration", "type", "score", "action"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    # Build a lookup: segment start → struggle zone membership
    struggle_starts = {sz["start"]: sz for sz in edl.get("struggle_zones", [])}

    for i, seg in enumerate(edl["segments"], 1):
        writer.writerow({
            "index": i,
            "start": seg["start"],
            "end": seg["end"],
            "duration": seg["duration"],
            "type": seg["type"],
            "score": seg.get("score") or "",
            "action": seg.get("action") or "",
        })

    # Append struggle zone rows after the flat segment list
    for i, sz in enumerate(edl.get("struggle_zones", []), 1):
        writer.writerow({
            "index": f"SZ{i}",
            "start": sz["start"],
            "end": sz["end"],
            "duration": sz["duration"],
            "type": sz["type"],
            "score": "",
            "action": sz.get("action", "MONTAGE_CANDIDATE"),
        })

    return output.getvalue()


def to_edl(edl: dict, fps: float = 30.0) -> str:
    """
    Generate a DaVinci Resolve / CMX 3600-compatible EDL.
    Dead zones are omitted from the edit; highlights and keeps are included.
    Struggle zones are appended as comment blocks with MONTAGE_CANDIDATE markers.
    """
    source = edl.get("source_name", "CLIP_001").upper().replace(" ", "_")[:8]
    lines = [
        "TITLE: GameCut EDL Export",
        "FCM: NON-DROP FRAME",
        "",
    ]

    edit_number = 1
    for seg in edl["segments"]:
        if seg["type"] == "dead_zone":
            continue

        src_tc_in  = _seconds_to_timecode(seg["start"], fps)
        src_tc_out = _seconds_to_timecode(seg["end"],   fps)
        rec_tc_in  = _seconds_to_timecode(seg["start"], fps)
        rec_tc_out = _seconds_to_timecode(seg["end"],   fps)

        lines.append(
            f"{edit_number:03d}  {source:<8} V     C        "
            f"{src_tc_in} {src_tc_out} {rec_tc_in} {rec_tc_out}"
        )
        if seg["type"] == "highlight":
            lines.append(f"* GAMECUT HIGHLIGHT score={seg.get('score', '')}")

        lines.append("")
        edit_number += 1

    # Append struggle zone annotations
    struggle_zones = edl.get("struggle_zones", [])
    if struggle_zones:
        lines.append("* ── STRUGGLE ZONES (MONTAGE CANDIDATES) ──────────────────")
        for i, sz in enumerate(struggle_zones, 1):
            tc_in  = _seconds_to_timecode(sz["start"], fps)
            tc_out = _seconds_to_timecode(sz["end"],   fps)
            lines.append(
                f"* SZ{i:02d} MONTAGE_CANDIDATE  {tc_in} - {tc_out}  "
                f"({sz['dead_zone_count']} dead zones, {sz['duration']:.1f}s)"
            )
        lines.append("")

    return "\n".join(lines)


def _seconds_to_timecode(seconds: float, fps: float) -> str:
    total_frames = int(round(seconds * fps))
    frames = total_frames % int(fps)
    total_seconds = total_frames // int(fps)
    secs = total_seconds % 60
    mins = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"
