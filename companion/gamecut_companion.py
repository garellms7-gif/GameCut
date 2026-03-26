#!/usr/bin/env python3
"""
GameCut Companion
=================
Lightweight background hotkey logger for OBS recording sessions.

Press configured keys while recording to mark moments by type.
A timestamps.json file is written alongside the recording and can be
fed to the GameCut /analyze endpoint to pre-seed the edit decision
list before AI analysis runs.

Default hotkeys
---------------
  F8  — Mark recording start  (resets elapsed timer to 0.000 s)
  F9  — Hype moment
  F10 — Funny moment
  F11 — Rage / frustration
  F12 — Cut suggestion

Usage
-----
  python gamecut_companion.py
  python gamecut_companion.py --output "C:/OBS/recordings/session.json"
  python gamecut_companion.py --config gamecut_companion_config.json
  python gamecut_companion.py --hotkeys hype=F5 funny=F6 rage=F7 cut=F8 start=F4
  python gamecut_companion.py --window 40   # highlight window in seconds

Requirements
------------
  pip install keyboard
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Dependency check ──────────────────────────────────────────────────────────
try:
    import keyboard  # type: ignore
except ImportError:
    print(
        "ERROR: 'keyboard' package not found.\n"
        "Install it with:  pip install keyboard\n"
        "On Windows you may need to run this script as Administrator\n"
        "if OBS or the game is also running as Administrator."
    )
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
VERSION = "1.0.0"

DEFAULT_HOTKEYS: dict[str, str] = {
    "session_start": "f8",
    "hype":          "f9",
    "funny":         "f10",
    "rage":          "f11",
    "cut":           "f12",
}

EVENT_ICONS: dict[str, str] = {
    "hype":  "▶ HYPE ",
    "funny": "★ FUNNY",
    "rage":  "✖ RAGE ",
    "cut":   "✂ CUT  ",
}

# ── Global session state (written from keyboard thread, read from main) ───────
_lock             = threading.Lock()
_session_start_mono: float | None = None   # monotonic time of last F8 press
_session_start_wall: str | None   = None   # ISO wall-clock of session start
_events: list[dict[str, Any]]     = []
_total_markers = 0


def _now_wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _elapsed() -> float:
    """Seconds since last session-start press, or since script launch."""
    if _session_start_mono is None:
        return time.monotonic()   # fallback: from script launch
    return time.monotonic() - _session_start_mono


# ── Event handling ────────────────────────────────────────────────────────────
def _reset_session(verbose: bool) -> None:
    global _session_start_mono, _session_start_wall
    with _lock:
        _session_start_mono = time.monotonic()
        _session_start_wall = _now_wall()
    if verbose:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:12]
        print(f"[{ts}]  ⏱  SESSION START — elapsed timer reset to 0.000 s")


def _log_event(
    event_type: str,
    hotkey: str,
    output_path: Path,
    verbose: bool,
) -> None:
    global _total_markers
    elapsed = _elapsed()
    wall    = _now_wall()

    entry: dict[str, Any] = {
        "type":            event_type,
        "elapsed_seconds": round(elapsed, 3),
        "wall_clock":      wall,
        "hotkey":          hotkey,
    }

    with _lock:
        _events.append(entry)
        _total_markers += 1
        count = _total_markers

    _write_json(output_path, verbose=False)

    if verbose:
        ts    = datetime.now().strftime("%H:%M:%S.%f")[:12]
        icon  = EVENT_ICONS.get(event_type, "? " + event_type.upper()[:5])
        print(f"[{ts}]  {icon}  @ {elapsed:>8.3f}s  ({hotkey.upper()})  [#{count}]")


def _write_json(output_path: Path, verbose: bool = True) -> None:
    """Atomically write the timestamps JSON file."""
    with _lock:
        data = {
            "gamecut_companion_version": VERSION,
            "session_start_wall":        _session_start_wall,
            "offset_seconds":            0.0,
            "hotkey_config":             {},   # filled by caller
            "events":                    list(_events),
        }
    # Atomic write: write to temp file then rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=output_path.parent, prefix=".gc_tmp_", suffix=".json"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    if verbose:
        print(f"  → Saved {len(data['events'])} event(s) to {output_path}")


# ── Argument parsing ──────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="GameCut Companion — hotkey timestamp logger for OBS sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--output", "-o",
        default="timestamps.json",
        help="Path to the output JSON file (default: timestamps.json in CWD)",
    )
    p.add_argument(
        "--config", "-c",
        default=None,
        help="Path to a JSON config file (see gamecut_companion_config.json)",
    )
    p.add_argument(
        "--hotkeys",
        nargs="*",
        metavar="TYPE=KEY",
        help="Override hotkeys, e.g. --hotkeys hype=F5 funny=F6 start=F4",
    )
    p.add_argument(
        "--window", "-w",
        type=float,
        default=30.0,
        help="Highlight clip window in seconds written to JSON (default: 30)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=True,
        help="Print each keypress to the console (default: on)",
    )
    p.add_argument(
        "--quiet", "-q",
        action="store_true",
        default=False,
        help="Suppress console output",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p.parse_args()


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(f"WARNING: config file not found: {cfg_path}")
        return {}
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    args = _parse_args()

    verbose = args.verbose and not args.quiet

    # ── Build effective config ─────────────────────────────────────────────
    cfg = _load_config(args.config)
    hotkeys: dict[str, str] = {**DEFAULT_HOTKEYS, **cfg.get("hotkeys", {})}
    if args.hotkeys:
        for item in args.hotkeys:
            try:
                k, v = item.split("=", 1)
                hotkeys[k.strip().lower()] = v.strip().lower()
            except ValueError:
                print(f"WARNING: ignoring malformed hotkey spec: {item!r}")

    window: float = args.window or cfg.get("window_seconds", 30.0)
    output_path   = Path(args.output or cfg.get("output", "timestamps.json")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Print banner ───────────────────────────────────────────────────────
    print("=" * 60)
    print(f"  GameCut Companion  v{VERSION}")
    print("=" * 60)
    print(f"  Output : {output_path}")
    print(f"  Window : {window} s per highlight marker")
    print()
    print("  Hotkeys:")
    reverse = {v: k for k, v in hotkeys.items()}
    for action, key in hotkeys.items():
        icon = EVENT_ICONS.get(action, f"  {action.upper():<8}")
        label = "⏱  RESET TIMER (press when OBS starts recording)" \
                if action == "session_start" else icon
        print(f"    {key.upper():<6}  {label}")
    print()
    print("  Press Ctrl+C to stop and save.")
    print("=" * 60)
    print()

    # ── Register hotkeys ──────────────────────────────────────────────────
    start_key = hotkeys.get("session_start", "f8")
    keyboard.add_hotkey(
        start_key,
        lambda: _reset_session(verbose),
        suppress=False,
        trigger_on_release=False,
    )

    for action in ("hype", "funny", "rage", "cut"):
        key = hotkeys.get(action)
        if not key:
            continue
        # Capture action and key in the closure
        def _make_cb(a: str, k: str):
            return lambda: _log_event(a, k, output_path, verbose)
        keyboard.add_hotkey(
            key,
            _make_cb(action, key),
            suppress=False,
            trigger_on_release=False,
        )

    # Write initial (empty) file so the user can see it was created
    _write_json(output_path)
    if verbose:
        print(f"Listening… (press {start_key.upper()} when OBS starts recording)\n")

    # ── Block until Ctrl+C ────────────────────────────────────────────────
    stop = threading.Event()

    def _on_sigint(sig, frame):
        stop.set()

    signal.signal(signal.SIGINT, _on_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_sigint)

    stop.wait()

    # ── Shutdown ──────────────────────────────────────────────────────────
    print("\nStopping…")
    keyboard.unhook_all()

    # Final write with hotkey config embedded
    with _lock:
        final_data: dict[str, Any] = {
            "gamecut_companion_version": VERSION,
            "session_start_wall":        _session_start_wall,
            "offset_seconds":            0.0,
            "window_seconds":            window,
            "hotkey_config":             hotkeys,
            "events":                    list(_events),
        }
    output_path.write_text(
        json.dumps(final_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved {len(final_data['events'])} event(s) → {output_path}")


if __name__ == "__main__":
    main()
