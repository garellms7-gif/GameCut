"use client";

import { useState } from "react";
import type { Segment, StruggleZone } from "@/lib/types";
import { formatTime } from "@/lib/api";

interface TimelineProps {
  segments: Segment[];
  totalDuration: number;
  struggleZones?: StruggleZone[];
}

const TYPE_COLORS: Record<string, string> = {
  dead_zone:    "#ef4444",
  highlight:    "#22c55e",
  keep:         "#eab308",
  struggle_zone:"#f97316",   // orange
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

const LEGEND_ENTRIES = ["dead_zone", "highlight", "keep", "struggle_zone"] as const;

export function Timeline({ segments, totalDuration, struggleZones = [] }: TimelineProps) {
  const [active, setActive] = useState<Segment | null>(null);
  const [activeStruggle, setActiveStruggle] = useState<StruggleZone | null>(null);

  const pct = (seconds: number) =>
    totalDuration > 0 ? (seconds / totalDuration) * 100 : 0;

  return (
    <div>
      {/* Color legend */}
      <div className="flex flex-wrap items-center gap-4 mb-3 text-xs text-white/50">
        {LEGEND_ENTRIES.map((type) => (
          <div key={type} className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-sm" style={{ backgroundColor: TYPE_COLORS[type] }} />
            <span>{TYPE_LABELS[type]}</span>
          </div>
        ))}
      </div>

      {/* Main segment bar */}
      <div
        className="w-full h-10 rounded-lg overflow-hidden flex cursor-pointer relative"
        style={{ background: "#1a1a1a" }}
      >
        {segments.map((seg, i) => {
          const widthPct = pct(seg.duration);
          return (
            <div
              key={i}
              title={`${TYPE_LABELS[seg.type] ?? seg.type} — ${formatTime(seg.start)} → ${formatTime(seg.end)}`}
              onClick={() => {
                setActiveStruggle(null);
                setActive(active?.start === seg.start ? null : seg);
              }}
              style={{
                width: `${widthPct}%`,
                backgroundColor: TYPE_COLORS[seg.type] ?? "#555",
                opacity: active
                  ? active.start === seg.start ? 1 : 0.35
                  : seg.type === "keep" ? 0.6 : 0.85,
                minWidth: widthPct > 0 ? "2px" : 0,
                transition: "opacity 0.15s",
              }}
              className="h-full"
            />
          );
        })}
      </div>

      {/* Struggle zone overlay bar */}
      {struggleZones.length > 0 && (
        <div
          className="w-full h-3 mt-1 rounded relative"
          style={{ background: "#1a1a1a" }}
          title="Struggle zones (orange = 3+ dead zones in 10 min)"
        >
          {struggleZones.map((sz, i) => {
            const leftPct  = pct(sz.start);
            const widthPct = pct(sz.duration);
            return (
              <div
                key={i}
                title={`STRUGGLE ZONE — ${formatTime(sz.start)} → ${formatTime(sz.end)} (${sz.dead_zone_count} dead zones)`}
                onClick={() => {
                  setActive(null);
                  setActiveStruggle(activeStruggle?.start === sz.start ? null : sz);
                }}
                style={{
                  position: "absolute",
                  left:  `${leftPct}%`,
                  width: `${widthPct}%`,
                  height: "100%",
                  backgroundColor: TYPE_COLORS.struggle_zone,
                  opacity: activeStruggle
                    ? activeStruggle.start === sz.start ? 1 : 0.4
                    : 0.75,
                  minWidth: "3px",
                  cursor: "pointer",
                  borderRadius: "2px",
                  transition: "opacity 0.15s",
                }}
              />
            );
          })}
        </div>
      )}

      {/* Timestamps */}
      <div className="flex justify-between text-xs text-white/25 mt-1">
        <span>{formatTime(0)}</span>
        <span>{formatTime(totalDuration / 2)}</span>
        <span>{formatTime(totalDuration)}</span>
      </div>

      {/* Active segment popover */}
      {active && (
        <div className={`mt-4 p-3 rounded-lg border text-sm ${TYPE_BG[active.type] ?? "border-white/10 text-white/60"}`}>
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold uppercase text-xs tracking-wider">
              {TYPE_LABELS[active.type] ?? active.type}
            </span>
            <button onClick={() => setActive(null)} className="text-white/30 hover:text-white/60 text-xs">✕</button>
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
                      width: `${Math.round((active.score || 0) * 100)}%`,
                      backgroundColor: TYPE_COLORS[active.type],
                    }}
                  />
                </div>
                <span className="font-mono text-xs">{((active.score || 0) * 100).toFixed(0)}%</span>
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
            <button onClick={() => setActiveStruggle(null)} className="text-white/30 hover:text-white/60 text-xs">✕</button>
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
