#!/usr/bin/env python3
"""
GameCut test script — run dead zone + hype detection on a sample video.

Usage:
    python test_detection.py /path/to/test.mp4 [--preset fps]
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Test GameCut detection on a video file.")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--preset", default="fps",
                        choices=["fps", "open_world", "rpg", "fighting", "platformer"],
                        help="Game preset (default: fps)")
    parser.add_argument("--dead-sensitivity", type=float, default=1.0,
                        help="Dead zone sensitivity multiplier (default: 1.0)")
    parser.add_argument("--hype-sensitivity", type=float, default=1.0,
                        help="Hype detection sensitivity multiplier (default: 1.0)")
    args = parser.parse_args()

    video_path = args.video
    if not Path(video_path).exists():
        print(f"ERROR: File not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # Load presets
    presets_path = Path(__file__).parent / "presets.json"
    with open(presets_path) as f:
        presets = json.load(f)

    preset = presets[args.preset]
    print(f"\n{'='*60}")
    print(f"GameCut Detection Test")
    print(f"{'='*60}")
    print(f"Video  : {video_path}")
    print(f"Preset : {args.preset} ({preset['name']})")
    print(f"Dead sensitivity  : {args.dead_sensitivity}x")
    print(f"Hype sensitivity  : {args.hype_sensitivity}x")
    print(f"{'='*60}\n")

    # ── Dead Zone Detection ────────────────────────────────────────
    print("[ 1/3 ] Running Dead Zone Detector...")
    from modules.dead_zone import detect_dead_zones

    def dz_progress(pct, label):
        bar = "█" * int(pct * 20)
        print(f"  [{bar:<20}] {int(pct*100):3d}%  {label}")

    dead_zones = detect_dead_zones(
        video_path,
        preset,
        sensitivity=args.dead_sensitivity,
        progress_callback=dz_progress,
    )

    print(f"\n  Found {len(dead_zones)} dead zone(s):\n")
    if dead_zones:
        print(f"  {'#':>3}  {'Start':>10}  {'End':>10}  {'Duration':>10}  {'Confidence':>12}")
        print(f"  {'-'*55}")
        for i, dz in enumerate(dead_zones, 1):
            print(f"  {i:>3}  {_fmt(dz['start']):>10}  {_fmt(dz['end']):>10}  "
                  f"{dz['duration']:>9.2f}s  {dz['confidence']:>11.1%}")
    else:
        print("  (none detected)")

    # ── Hype Moment Detection ──────────────────────────────────────
    print(f"\n[ 2/3 ] Running Hype Moment Detector...")
    from modules.hype_moment import detect_hype_moments

    def hype_progress(pct, label):
        bar = "█" * int(pct * 20)
        print(f"  [{bar:<20}] {int(pct*100):3d}%  {label}")

    highlights = detect_hype_moments(
        video_path,
        preset,
        sensitivity=args.hype_sensitivity,
        progress_callback=hype_progress,
    )

    print(f"\n  Found {len(highlights)} highlight moment(s) (ranked by composite score):\n")
    if highlights:
        print(f"  {'#':>3}  {'Start':>10}  {'End':>10}  {'Score':>7}  "
              f"{'Vol':>6}  {'Pitch':>6}  {'Rate':>6}")
        print(f"  {'-'*60}")
        for i, hl in enumerate(highlights[:20], 1):  # top 20
            print(f"  {i:>3}  {_fmt(hl['start']):>10}  {_fmt(hl['end']):>10}  "
                  f"{hl['composite_score']:>6.1%}  "
                  f"{hl['volume_score']:>5.1%}  "
                  f"{hl['pitch_score']:>5.1%}  "
                  f"{hl['speech_rate_score']:>5.1%}")
        if len(highlights) > 20:
            print(f"  ... and {len(highlights) - 20} more")
    else:
        print("  (none detected)")

    # ── EDL Merge ─────────────────────────────────────────────────
    print(f"\n[ 3/3 ] Building EDL...")
    from modules.edl_export import build_edl, to_csv, to_edl
    import subprocess

    # Get duration
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    duration = float(result.stdout.strip() or "0")

    edl = build_edl(dead_zones, highlights, duration,
                    source_name=Path(video_path).stem[:8].upper())

    summary = edl["summary"]
    segs = edl["segments"]

    print(f"\n  EDL Summary:")
    print(f"  Total duration  : {_fmt(summary['total_duration'])}")
    print(f"  Dead zone time  : {_fmt(summary['dead_zone_duration'])}  ({summary['cut_savings_pct']}% can be cut)")
    print(f"  Highlight time  : {_fmt(summary['highlight_duration'])}  ({summary['highlight_pct']}% is highlight)")
    print(f"  Keep time       : {_fmt(summary['keep_duration'])}")
    print(f"  Segments        : {len(segs)} total  "
          f"({summary['dead_zone_count']} cut, "
          f"{summary['highlight_count']} highlight, "
          f"{summary['keep_count']} keep)")

    # ── Visual Timeline ────────────────────────────────────────────
    ICONS = {"dead_zone": "🔴", "highlight": "🟢", "keep": "🟡"}
    print(f"\n  Visual Timeline:")
    print(f"  ", end="")
    bar_width = 60
    for seg in segs:
        w = max(1, int(seg["duration"] / duration * bar_width))
        icon = ICONS.get(seg["type"], "⬜")
        print(icon * w, end="")
    print()
    print(f"  🔴 = CUT  🟡 = KEEP  🟢 = HIGHLIGHT")

    # ── Write output files ─────────────────────────────────────────
    base = Path(video_path).stem
    out_json = Path(f"{base}_gamecut.json")
    out_csv = Path(f"{base}_gamecut.csv")
    out_edl = Path(f"{base}_gamecut.edl")

    out_json.write_text(json.dumps(edl, indent=2))
    out_csv.write_text(to_csv(edl))
    out_edl.write_text(to_edl(edl))

    print(f"\n  Output files written:")
    print(f"    {out_json}")
    print(f"    {out_csv}")
    print(f"    {out_edl}")
    print(f"\n{'='*60}\n")


def _fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:05.2f}"
    return f"{m}:{s:05.2f}"


if __name__ == "__main__":
    main()
