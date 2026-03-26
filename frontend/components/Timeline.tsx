"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Segment, StruggleZone } from "@/lib/types";
import { formatTime } from "@/lib/api";

// ── Constants ────────────────────────────────────────────────────────────────

const CANVAS_H = 88;          // canvas height in px
const MAX_W    = 20_000;      // hard cap to keep the DOM reasonable
const MAX_DECODE_BYTES = 200 * 1024 * 1024; // skip full decode above 200 MB

// Segment overlay colours (RGBA strings)
const OVERLAY: Record<string, string> = {
  dead_zone: "rgba(239,68,68,0.22)",
  highlight: "rgba(34,197,94,0.18)",
  keep:      "rgba(0,0,0,0)",
};

// Waveform bar colours per segment type
const BAR_COLOR: Record<string, string> = {
  dead_zone: "rgba(255,255,255,0.15)",
  highlight: "rgba(80,255,120,0.90)",
  keep:      "rgba(255,255,255,0.65)",
};

const TYPE_COLORS: Record<string, string> = {
  dead_zone:    "#ef4444",
  highlight:    "#22c55e",
  keep:         "#eab308",
  struggle_zone:"#f97316",
};
const TYPE_LABELS: Record<string, string> = {
  dead_zone:    "CUT",
  highlight:    "HIGHLIGHT",
  keep:         "KEEP",
  struggle_zone:"STRUGGLE",
};
const TYPE_BG: Record<string, string> = {
  dead_zone:    "bg-red-500/10 border-red-500/30 text-red-400",
  highlight:    "bg-green-500/10 border-green-500/30 text-green-400",
  keep:         "bg-yellow-500/10 border-yellow-500/30 text-yellow-400",
  struggle_zone:"bg-orange-500/10 border-orange-500/30 text-orange-400",
};

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Pixels-per-second zoom level, scaled for long sessions. */
function pxPerSec(dur: number): number {
  if (dur > 7200) return 1;
  if (dur > 3600) return 2;
  if (dur > 1800) return 3;
  return 4;
}

/** Compute canvas pixel width for a session of `dur` seconds. */
function computeWidth(dur: number, containerW: number): number {
  return Math.min(MAX_W, Math.max(containerW, Math.ceil(dur * pxPerSec(dur))));
}

/** Find the segment that covers time `t`. */
function segAt(t: number, segs: Segment[]): Segment | null {
  for (const s of segs) if (t >= s.start && t <= s.end) return s;
  return null;
}

/** Simple deterministic LCG for synthetic waveform generation. */
function lcg(seed: number) {
  let s = seed | 0;
  return () => {
    s = Math.imul(s, 1664525) + 1013904223;
    return ((s >>> 0) / 0xffffffff);
  };
}

/**
 * Generate a synthetic Float32Array of `n` amplitude values (0-1) based
 * purely on the EDL segments — no real audio required.
 *
 * dead_zone   → very low amplitude  (silence)
 * highlight   → high amplitude      (loud/energetic)
 * keep        → medium amplitude    (normal gameplay)
 */
function syntheticPeaks(segs: Segment[], n: number, dur: number): Float32Array {
  const peaks = new Float32Array(n);
  const rand  = lcg(42);
  // Per-pixel smoothing state
  let smoothed = 0.3;

  for (let i = 0; i < n; i++) {
    const t   = (i / n) * dur;
    const seg = segAt(t, segs);
    let envelope: number;
    switch (seg?.type) {
      case "dead_zone": envelope = 0.06 + rand() * 0.06; break;
      case "highlight": envelope = 0.55 + rand() * 0.40; break;
      default:          envelope = 0.25 + rand() * 0.30; break;
    }
    // Light smoothing to avoid salt-and-pepper look
    smoothed = smoothed * 0.7 + envelope * 0.3;
    peaks[i] = smoothed;
  }
  return peaks;
}

// ── Main component ───────────────────────────────────────────────────────────

export type ConfirmationState = "confirmed_cut" | "confirmed_highlight";

interface TimelineProps {
  segments: Segment[];
  totalDuration: number;
  struggleZones?: StruggleZone[];
  videoFile?: File | null;
  onSegmentSelect?: (seg: Segment | null) => void;
  /** Key → confirmation state, keyed as "type:start:end" */
  confirmations?: ReadonlyMap<string, ConfirmationState>;
}

export function Timeline({
  segments,
  totalDuration,
  struggleZones = [],
  videoFile,
  onSegmentSelect,
  confirmations,
}: TimelineProps) {
  const canvasRef    = useRef<HTMLCanvasElement>(null);
  const scrollRef    = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [containerW, setContainerW] = useState(800);
  const [peaks,      setPeaks]      = useState<Float32Array | null>(null);
  const [decoding,   setDecoding]   = useState(false);

  const [active,         setActive]         = useState<Segment | null>(null);
  const [activeStruggle, setActiveStruggle] = useState<StruggleZone | null>(null);

  const canvasW = computeWidth(totalDuration, containerW);

  // ── Measure container ────────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setContainerW(Math.floor(entry.contentRect.width) || 800);
    });
    ro.observe(el);
    setContainerW(el.getBoundingClientRect().width || 800);
    return () => ro.disconnect();
  }, []);

  // ── Decode audio waveform ─────────────────────────────────────────────────
  useEffect(() => {
    if (!videoFile) return;

    // Skip full decode for very large files — use synthetic instead.
    if (videoFile.size > MAX_DECODE_BYTES) return;

    let cancelled = false;
    setDecoding(true);

    (async () => {
      try {
        const buf     = await videoFile.arrayBuffer();
        if (cancelled) return;

        const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const actx     = new AudioCtx();
        const audio    = await actx.decodeAudioData(buf);
        if (cancelled) return;

        const raw   = audio.getChannelData(0);           // mono / left channel
        const nPx   = canvasW;
        const step  = Math.max(1, Math.floor(raw.length / nPx));
        const out   = new Float32Array(nPx);

        for (let i = 0; i < nPx; i++) {
          const start = i * step;
          let max = 0;
          for (let j = start; j < start + step; j++) {
            const v = Math.abs(raw[j] ?? 0);
            if (v > max) max = v;
          }
          out[i] = max;
        }

        actx.close();
        if (!cancelled) setPeaks(out);
      } catch {
        // Decode failed — synthetic fallback is rendered automatically
      } finally {
        if (!cancelled) setDecoding(false);
      }
    })();

    return () => { cancelled = true; };
  }, [videoFile, canvasW]);

  // ── Render canvas ─────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width  = canvasW;
    canvas.height = CANVAS_H;

    // Background
    ctx.fillStyle = "#0c0c0c";
    ctx.fillRect(0, 0, canvasW, CANVAS_H);

    const midY = CANVAS_H / 2;

    // Effective peaks: real audio if decoded, else synthetic
    const p = peaks ?? syntheticPeaks(segments, canvasW, totalDuration);

    // Helper: resolve confirmation state for a segment
    const conf = (seg: Segment | null): ConfirmationState | undefined =>
      seg ? confirmations?.get(`${seg.type}:${seg.start}:${seg.end}`) : undefined;

    // ── 1 · Draw waveform bars ──────────────────────────────────────────
    for (let i = 0; i < canvasW; i++) {
      const t    = (i / canvasW) * totalDuration;
      const seg  = segAt(t, segments);
      const c    = conf(seg);
      const amp  = p[i] ?? 0;

      let barH = Math.max(1, amp * CANVAS_H * 0.88);
      let barColor: string;

      if (c === "confirmed_cut") {
        // Confirmed cut: nearly invisible — the audio is definitely gone
        barColor = "rgba(255,255,255,0.07)";
        barH *= 0.25;
        ctx.shadowBlur = 0;
      } else if (c === "confirmed_highlight") {
        // Confirmed highlight: vivid and bright
        barColor = "rgba(60,255,100,1.0)";
        ctx.shadowColor = "#00ff55";
        ctx.shadowBlur  = 10;
      } else if (seg?.type === "highlight") {
        barColor = BAR_COLOR.highlight;
        ctx.shadowColor = "#22c55e";
        ctx.shadowBlur  = 6;
      } else {
        barColor = BAR_COLOR[seg?.type ?? "keep"] ?? BAR_COLOR.keep;
        ctx.shadowBlur = 0;
      }

      ctx.fillStyle = barColor;
      ctx.fillRect(i, midY - barH / 2, 1, barH);
    }
    ctx.shadowBlur = 0;

    // ── 2 · Segment colour overlays ─────────────────────────────────────
    for (const seg of segments) {
      const x = (seg.start / totalDuration) * canvasW;
      const w = (seg.duration / totalDuration) * canvasW;
      const c = conf(seg);

      let overlayColor: string | null = null;
      if (c === "confirmed_cut") {
        overlayColor = "rgba(80,80,80,0.45)";    // gray — confirmed gone
      } else if (c === "confirmed_highlight") {
        overlayColor = "rgba(34,197,94,0.32)";   // strong green — locked in
      } else {
        overlayColor = OVERLAY[seg.type] ?? null;
      }

      if (overlayColor) {
        ctx.fillStyle = overlayColor;
        ctx.fillRect(x, 0, Math.max(w, 1), CANVAS_H);
      }
    }

    // ── 3 · Highlight glow border (confirmed = solid, unconfirmed = dashed) ──
    for (const seg of segments) {
      if (seg.type !== "highlight") continue;
      const x = (seg.start / totalDuration) * canvasW;
      const w = (seg.duration / totalDuration) * canvasW;
      const c = conf(seg);

      if (c === "confirmed_highlight") {
        ctx.strokeStyle = "rgba(34,197,94,0.90)";
        ctx.lineWidth   = 2;
      } else {
        ctx.strokeStyle = "rgba(34,197,94,0.55)";
        ctx.lineWidth   = 1;
      }
      ctx.strokeRect(x + 0.5, 0.5, Math.max(w - 1, 1), CANVAS_H - 1);
    }

    // ── 3b · Confirmed-cut strikethrough line ────────────────────────────
    for (const seg of segments) {
      if (conf(seg) !== "confirmed_cut") continue;
      const x = (seg.start / totalDuration) * canvasW;
      const w = (seg.duration / totalDuration) * canvasW;
      ctx.strokeStyle = "rgba(180,50,50,0.35)";
      ctx.lineWidth   = 1;
      ctx.beginPath();
      ctx.moveTo(x, midY);
      ctx.lineTo(x + Math.max(w, 1), midY);
      ctx.stroke();
    }

    // ── 4 · Struggle zone strip (top 5 px) ───────────────────────────────
    for (const sz of struggleZones) {
      const x = (sz.start / totalDuration) * canvasW;
      const w = (sz.duration / totalDuration) * canvasW;
      ctx.fillStyle = "rgba(249,115,22,0.7)";
      ctx.fillRect(x, 0, Math.max(w, 3), 5);
    }

    // ── 5 · Time grid ────────────────────────────────────────────────────
    const gridInterval =
      totalDuration > 7200 ? 600 :
      totalDuration > 3600 ? 300 :
      totalDuration > 600  ?  60 : 30;

    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth   = 1;
    ctx.fillStyle   = "rgba(255,255,255,0.2)";
    ctx.font        = "9px 'SF Mono', monospace";

    for (let t = gridInterval; t < totalDuration; t += gridInterval) {
      const x = (t / totalDuration) * canvasW;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, CANVAS_H);
      ctx.stroke();

      // Only draw label if there is enough horizontal room
      if ((canvasW / totalDuration) * gridInterval > 40) {
        ctx.fillText(_shortTime(t), x + 3, CANVAS_H - 4);
      }
    }
  }, [peaks, segments, struggleZones, totalDuration, canvasW, confirmations]);

  // ── Click on canvas ───────────────────────────────────────────────────────
  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const scrollLeft = scrollRef.current?.scrollLeft ?? 0;
      const x = e.clientX - rect.left + scrollLeft;
      const t = (x / canvasW) * totalDuration;

      // Check struggle zones first
      const sz = struggleZones.find(z => t >= z.start && t <= z.end);
      if (sz) {
        setActive(null);
        onSegmentSelect?.(null);
        setActiveStruggle(activeStruggle?.start === sz.start ? null : sz);
        return;
      }
      const seg = segAt(t, segments);
      const next = seg && active?.start === seg.start ? null : seg ?? null;
      setActiveStruggle(null);
      setActive(next);
      onSegmentSelect?.(next);
    },
    [canvasW, totalDuration, segments, struggleZones, active, activeStruggle],
  );

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div ref={containerRef} className="w-full">
      {/* Legend */}
      <div className="flex flex-wrap items-center gap-4 mb-3 text-xs text-white/50">
        {(["dead_zone", "highlight", "keep", "struggle_zone"] as const).map((t) => (
          <div key={t} className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-sm" style={{ backgroundColor: TYPE_COLORS[t] }} />
            <span>{TYPE_LABELS[t]}</span>
          </div>
        ))}
        {decoding && (
          <span className="ml-auto text-white/30 flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 border border-white/20 border-t-white/60 rounded-full animate-spin" />
            Decoding audio…
          </span>
        )}
        {!videoFile && !peaks && (
          <span className="ml-auto text-white/25 italic text-xs">synthetic waveform</span>
        )}
      </div>

      {/* Scrollable waveform */}
      <div
        ref={scrollRef}
        className="overflow-x-auto rounded-lg cursor-crosshair"
        style={{ WebkitOverflowScrolling: "touch" }}
      >
        <canvas
          ref={canvasRef}
          width={canvasW}
          height={CANVAS_H}
          style={{ display: "block", minWidth: canvasW }}
          onClick={handleClick}
          title="Click a segment to inspect"
        />
      </div>

      {/* Timestamps below scroll area */}
      <div className="flex justify-between text-xs text-white/25 mt-1 px-0.5">
        <span>{formatTime(0)}</span>
        <span>{formatTime(totalDuration / 4)}</span>
        <span>{formatTime(totalDuration / 2)}</span>
        <span>{formatTime((totalDuration * 3) / 4)}</span>
        <span>{formatTime(totalDuration)}</span>
      </div>

      {/* Active segment popover */}
      {active && (
        <div
          className={`mt-4 p-3 rounded-lg border text-sm
            ${TYPE_BG[active.type] ?? "border-white/10 text-white/60"}`}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold uppercase text-xs tracking-wider">
              {TYPE_LABELS[active.type] ?? active.type}
            </span>
            <button
              onClick={() => setActive(null)}
              className="text-white/30 hover:text-white/60 text-xs"
            >✕</button>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs mt-2">
            <div><p className="text-white/40">Start</p><p className="font-mono">{formatTime(active.start)}</p></div>
            <div><p className="text-white/40">End</p><p className="font-mono">{formatTime(active.end)}</p></div>
            <div><p className="text-white/40">Duration</p><p className="font-mono">{active.duration.toFixed(2)}s</p></div>
          </div>
          {active.score !== null && (
            <div className="mt-2">
              <p className="text-white/40 text-xs">Score</p>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex-1 bg-white/10 rounded-full h-1.5">
                  <div
                    className="h-1.5 rounded-full"
                    style={{
                      width: `${Math.round((active.score ?? 0) * 100)}%`,
                      backgroundColor: TYPE_COLORS[active.type],
                    }}
                  />
                </div>
                <span className="font-mono text-xs">
                  {((active.score ?? 0) * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Active struggle zone popover */}
      {activeStruggle && (
        <div className="mt-4 p-3 rounded-lg border bg-orange-500/10 border-orange-500/30 text-orange-400 text-sm">
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold uppercase text-xs tracking-wider">
              Struggle Zone — Montage Candidate
            </span>
            <button
              onClick={() => setActiveStruggle(null)}
              className="text-white/30 hover:text-white/60 text-xs"
            >✕</button>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs mt-2">
            <div><p className="text-white/40">Start</p><p className="font-mono">{formatTime(activeStruggle.start)}</p></div>
            <div><p className="text-white/40">End</p><p className="font-mono">{formatTime(activeStruggle.end)}</p></div>
            <div><p className="text-white/40">Duration</p><p className="font-mono">{activeStruggle.duration.toFixed(1)}s</p></div>
          </div>
          <p className="text-xs text-orange-300/70 mt-2">
            {activeStruggle.dead_zone_count} dead zones clustered here — consider a speed-ramp or montage cut.
          </p>
        </div>
      )}
    </div>
  );
}

function _shortTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return s === 0 ? `${m}m` : `${m}:${String(s).padStart(2, "0")}`;
}
