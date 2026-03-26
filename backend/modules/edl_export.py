"""
EDL Exporter
Merges dead zone and hype moment results into a unified Edit Decision List.
Outputs JSON, CSV, DaVinci Resolve-compatible .edl, and FCPXML 1.10 formats.
"""

import csv
import io
import json
import math
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
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


# ─── FCPXML 1.10 export ────────────────────────────────────────────────────────

def _to_rational_time(seconds: float, fps: int) -> str:
    """Convert seconds to FCPXML rational time string, e.g. '900/30s'."""
    if seconds <= 0:
        return "0s"
    frames = round(seconds * fps)
    return f"{frames}/{fps}s"


def to_fcpxml(
    edl: dict,
    fps: float = 30.0,
    source_filename: str = "source.mp4",
) -> str:
    """
    Generate a DaVinci Resolve-compatible FCPXML 1.10 document.

    Mapping:
      dead_zone     → <gap>  element (empty/black region in the timeline)
      keep          → <clip> element referencing the source asset
      highlight     → <clip> element with a green <marker> at the clip in-point
      struggle_zone → orange <marker> on the keep/highlight clip where the zone begins

    The returned string is a complete XML document ready to save as
    ``gamecut_timeline.fcpxml`` and import into DaVinci Resolve.
    """
    fps_int = max(1, round(fps))
    frame_dur = f"1/{fps_int}s"

    total_dur = edl["summary"]["total_duration"]
    total_dur_str = _to_rational_time(total_dur, fps_int)

    source_stem = Path(source_filename).stem if source_filename else "source"

    # ── Resources ─────────────────────────────────────────────────────────────
    fcpxml_el = ET.Element("fcpxml", version="1.10")

    resources = ET.SubElement(fcpxml_el, "resources")
    ET.SubElement(
        resources, "format",
        id="r1",
        name=f"FFVideoFormat{fps_int}",
        frameDuration=frame_dur,
        width="1920",
        height="1080",
    )
    asset = ET.SubElement(
        resources, "asset",
        id="r2",
        name=source_stem,
        start="0s",
        duration=total_dur_str,
        hasVideo="1",
        hasAudio="1",
        audioSources="1",
        audioChannels="2",
        audioRate="48000",
    )
    ET.SubElement(
        asset, "media-rep",
        kind="original-media",
        src=f"file:///{source_filename}",
    )

    # ── Timeline ──────────────────────────────────────────────────────────────
    library  = ET.SubElement(fcpxml_el, "library")
    event    = ET.SubElement(library, "event",    name="GameCut Export")
    project  = ET.SubElement(event,   "project",  name="GameCut Timeline")
    sequence = ET.SubElement(
        project, "sequence",
        duration=total_dur_str,
        format="r1",
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    struggle_zones: list[dict] = edl.get("struggle_zones", [])

    for seg in edl["segments"]:
        seg_start  = seg["start"]
        seg_end    = seg["end"]
        seg_dur    = seg["duration"]
        seg_type   = seg["type"]

        offset_str = _to_rational_time(seg_start, fps_int)
        dur_str    = _to_rational_time(seg_dur,   fps_int)
        src_str    = _to_rational_time(seg_start, fps_int)

        if seg_type == "dead_zone":
            ET.SubElement(
                spine, "gap",
                name="Dead Zone",
                offset=offset_str,
                duration=dur_str,
                start="0s",
            )
        else:
            label = "Highlight" if seg_type == "highlight" else "Keep"
            clip = ET.SubElement(
                spine, "clip",
                name=label,
                ref="r2",
                offset=offset_str,
                duration=dur_str,
                start=src_str,
            )

            # Highlight: green marker at the clip's in-point
            if seg_type == "highlight":
                score = seg.get("score") or 0.0
                note  = f"GameCut Highlight score={score:.2f}" if score else "GameCut Highlight"
                ET.SubElement(
                    clip, "marker",
                    start=src_str,
                    duration=frame_dur,
                    value="HIGHLIGHT",
                    note=note,
                    **{"completed": "0"},
                )

            # Struggle zones: orange marker at the zone's source in-point
            for sz in struggle_zones:
                if seg_start <= sz["start"] < seg_end:
                    sz_src_str = _to_rational_time(sz["start"], fps_int)
                    ET.SubElement(
                        clip, "marker",
                        start=sz_src_str,
                        duration=frame_dur,
                        value="MONTAGE_CANDIDATE",
                        note=(
                            f"Struggle Zone — {sz['dead_zone_count']} dead zones, "
                            f"{sz['duration']:.1f}s window"
                        ),
                        **{"completed": "0"},
                    )

    # Pretty-print (Python ≥ 3.9)
    try:
        ET.indent(fcpxml_el, space="  ")
    except AttributeError:
        pass  # Python < 3.9 — output will be on one line, still valid XML

    xml_body = ET.tostring(fcpxml_el, encoding="unicode", xml_declaration=False)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        + xml_body
        + "\n"
    )


# ─── CapCut draft_content.json export ──────────────────────────────────────────

_CAPCUT_VERSION = "5.9.0"
_CAPCUT_NEW_VERSION = "119.0.0"

# Marker config: type → (hex color, [R,G,B] floats, y-translation, label prefix)
# y-translation uses CapCut's normalised screen space (-0.5 top → +0.5 bottom).
_MARKER_STYLE: dict[str, tuple[str, list[float], float, str]] = {
    "highlight":    ("#00C853", [0.0,  0.784, 0.325], -0.35, "▶ HIGHLIGHT"),
    "dead_zone":    ("#FF3D3D", [1.0,  0.239, 0.239],  0.35, "✕ CUT"),
    "struggle_zone":("#FF8C00", [1.0,  0.549, 0.0],    0.0,  "⚡ STRUGGLE"),
}
# Marker visibility: 1.5 seconds in microseconds
_MARKER_DUR_US = 1_500_000


def _uid() -> str:
    """Return an uppercase UUID string as used by CapCut."""
    return str(uuid.uuid4()).upper()


def _capcut_text_content(label: str, color_rgb: list[float]) -> str:
    """
    Encode a text label into CapCut's inner JSON-string format.
    The `content` field of a text material is itself a JSON-encoded string.
    """
    inner = {
        "styles": [{
            "fill": {"alpha": 1, "color": color_rgb},
            "strokes": [],
            "range": [0, len(label)],
            "useLetterColor": False,
        }],
        "text": label,
    }
    return json.dumps(inner, ensure_ascii=False, separators=(",", ":"))


def _capcut_text_material(
    mat_id: str,
    label: str,
    hex_color: str,
    color_rgb: list[float],
) -> dict:
    """Build a CapCut text material object."""
    return {
        "add_type": 0,
        "alignment": 1,
        "background_alpha": 1.0,
        "background_color": "",
        "background_height": 0.14,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.0,
        "background_style": 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.82,
        "base_content": "",
        "bold_width": 0.0,
        "border_alpha": 1.0,
        "border_color": "",
        "border_width": 0.08,
        "content": _capcut_text_content(label, color_rgb),
        "fixed_height": -1.0,
        "fixed_width": -1.0,
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": "",
        "font_resource_id": "",
        "font_size": 8.0,
        "font_source_platform": 0,
        "font_title": "none",
        "font_url": "",
        "fonts": [],
        "force_apply_line_max_width": False,
        "global_alpha": 1.0,
        "group_id": "",
        "has_shadow": True,
        "id": mat_id,
        "initial_scale": 1.0,
        "inner_padding": -1.0,
        "is_rich_text": False,
        "italic": False,
        "italic_degree": 0,
        "ktv_color": "",
        "language": "",
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": 0.82,
        "line_spacing": 0.02,
        "multi_language_current": "none",
        "name": "",
        "original_size": [],
        "preset_id": "",
        "recognize_task_id": "",
        "recognize_type": 0,
        "relevance_segment": [],
        "shadow_alpha": 0.9,
        "shadow_angle": -45.0,
        "shadow_color": "",
        "shadow_distance": 8.0,
        "shadow_point": {"x": 0.6363961030678928, "y": -0.6363961030678928},
        "shadow_smoothing": 1.0,
        "shape_clip_type": "none",
        "style_name": "",
        "sub_type": "none",
        "text_alpha": 1.0,
        "text_color": hex_color,
        "text_curve": None,
        "text_preset_resource_id": "",
        "text_size": 30,
        "text_to_audio_ids": [],
        "tts_auto_update": False,
        "type": "text",
        "typesetting": "horizontal",
        "underline": False,
        "underline_offset": 0.22,
        "underline_width": 0.05,
        "use_effect_default_color": False,
        "words": {"end_time": [], "start_time": [], "text": []},
    }


def _capcut_text_segment(
    seg_id: str,
    mat_id: str,
    start_us: int,
    dur_us: int,
    render_index: int,
    y_translation: float,
) -> dict:
    """Build a CapCut track-segment object referencing a text material."""
    return {
        "caption_info": None,
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "translation": {"x": 0.0, "y": y_translation},
        },
        "common_keyframes": [],
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": None,
        "id": seg_id,
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": mat_id,
        "render_index": render_index,
        "responsive_layout": {
            "enable": False,
            "horizontal_pos_layout": 0,
            "size_layout": 0,
            "target_follow": "",
            "vertical_pos_layout": 0,
        },
        "reverse": False,
        "source_timerange": {"duration": dur_us, "start": 0},
        "speed": 1.0,
        "target_timerange": {"duration": dur_us, "start": start_us},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": 1.0,
    }


def to_capcut(edl: dict, fps: float = 30.0, source_filename: str = "") -> str:
    """
    Generate a CapCut-compatible ``draft_content.json`` markers file.

    Places colored text markers on the timeline at every HIGHLIGHT and CUT
    (dead_zone) timestamp, plus STRUGGLE_ZONE annotations:

      HIGHLIGHT    → green  (#00C853), top of frame, "▶ HIGHLIGHT"
      dead_zone    → red    (#FF3D3D), bottom of frame, "✕ CUT"
      struggle_zone→ orange (#FF8C00), centre of frame, "⚡ STRUGGLE"

    Each marker is displayed for 1.5 seconds.  Times are stored in
    **microseconds** as required by CapCut's internal format.

    The returned string is a complete JSON document.  Save it as
    ``draft_content.json`` and place it in your CapCut project folder
    (see README for exact paths on Windows / macOS).
    """
    total_us = max(1, int(edl["summary"]["total_duration"] * 1_000_000))
    now_ts   = int(time.time())

    text_materials: list[dict] = []
    track_segments: list[dict] = []
    render_idx = 0

    # ── Flat-timeline markers (highlights + dead zones) ────────────────────
    for seg in edl["segments"]:
        seg_type = seg["type"]
        if seg_type not in _MARKER_STYLE:
            continue

        hex_color, color_rgb, y_trans, prefix = _MARKER_STYLE[seg_type]

        score = seg.get("score")
        if score is not None and seg_type == "highlight":
            label = f"{prefix}  {score:.2f}"
        else:
            label = f"{prefix}  {_fmt_ms(seg['start'])}"

        mat_id  = _uid()
        seg_id  = _uid()
        start_us = int(seg["start"] * 1_000_000)
        dur_us   = min(_MARKER_DUR_US, total_us - start_us)
        if dur_us <= 0:
            continue

        text_materials.append(
            _capcut_text_material(mat_id, label, hex_color, color_rgb)
        )
        track_segments.append(
            _capcut_text_segment(seg_id, mat_id, start_us, dur_us, render_idx, y_trans)
        )
        render_idx += 1

    # ── Struggle zone markers ──────────────────────────────────────────────
    hex_color, color_rgb, y_trans, prefix = _MARKER_STYLE["struggle_zone"]
    for sz in edl.get("struggle_zones", []):
        dz_count = sz.get("dead_zone_count", 0)
        label    = f"{prefix}  ×{dz_count}  {_fmt_ms(sz['start'])}"

        mat_id   = _uid()
        seg_id   = _uid()
        start_us = int(sz["start"] * 1_000_000)
        dur_us   = min(_MARKER_DUR_US, total_us - start_us)
        if dur_us <= 0:
            continue

        text_materials.append(
            _capcut_text_material(mat_id, label, hex_color, color_rgb)
        )
        track_segments.append(
            _capcut_text_segment(seg_id, mat_id, start_us, dur_us, render_idx, y_trans)
        )
        render_idx += 1

    # ── Assemble draft_content ─────────────────────────────────────────────
    platform_info = {
        "app_version": _CAPCUT_VERSION,
        "device_id": "",
        "hard_disk_id": "",
        "mac_address": "",
        "os": "windows",
        "os_version": "",
    }

    draft: dict = {
        "canvas_config": {"height": 1080, "ratio": "original", "width": 1920},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": False,
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": "",
        "create_time": now_ts,
        "duration": total_us,
        "extra_info": source_filename,
        "fps": round(fps, 6),
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": _uid(),
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [], "audios": [], "filters": [],
            "handwrites": [], "texts": [], "videos": [],
        },
        "last_modified_platform": platform_info,
        "lyrics_recognition_id": "",
        "lyrics_taskinfo": [],
        "materials": {
            "audios": [], "beats": [], "canvases": [], "color_curves": [],
            "digital_humans": [], "drafts": [], "effects": [], "flowers": [],
            "green_screens": [], "handwrites": [], "hsl": [], "images": [],
            "log_color_wheels": [], "loudnesses": [], "manual_deformations": [],
            "masks": [], "material_animations": [], "material_colors": [],
            "place_holders": [], "plugin_effects": [], "primary_color_wheels": [],
            "retouch_adjusts": [], "retouch_face_beauties": [],
            "retouch_filters": [], "retouch_hair_beauties": [],
            "retouch_teeth_beauties": [], "speeds": [], "stickers": [],
            "tail_leaders": [], "text_templates": [],
            "texts": text_materials,
            "time_marks": [], "transitions": [], "video_effects": [],
            "video_trackings": [], "videos": [], "vocal_beauties": [],
            "vocaloids": [],
        },
        "mutable_config": None,
        "name": "GameCut Markers",
        "new_version": _CAPCUT_NEW_VERSION,
        "platform": platform_info,
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source_info": {"platform": "", "task_id": "", "task_type": ""},
        "static_cover_image_path": "",
        "time_marks": {"in": -1, "out": -1, "transition_time": 0},
        "tracks": [
            {
                "attribute": 0,
                "flag": 0,
                "id": _uid(),
                "is_default_name": True,
                "name": "",
                "segments": track_segments,
                "type": "text",
            }
        ],
        "update_time": now_ts,
        "version": _CAPCUT_VERSION,
        "video_mute": False,
    }

    return json.dumps(draft, ensure_ascii=False, indent=2)


def _fmt_ms(seconds: float) -> str:
    """Format seconds as MM:SS for use in short marker labels."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"
