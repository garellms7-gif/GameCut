"use client";

import { useState } from "react";
import type { Segment } from "@/lib/types";
import { formatTime } from "@/lib/api";

interface TimelineProps {
  segments: Segment[];
  totalDuration: number;
}

const TYPE_COLORS: Record<string, string> = {
  dead_zone: "#ef4444",
  highlight: "#22c55e",
  keep: "#eab308",
};

const TYPE_LABELS: Record<string, string> = {
  dead_zone: "CUT",
  highlight: "HIGHLIGHT",
  keep: "KEEP",
};

const TYPE_BG: Record<string, string> = {
  dead_zone: "bg-red-500/10 border-red-500/30 text-red-400",
  highlight: "bg-green-500/10 border-green-500/30 text-green-400",
  keep: "bg-yellow-500/10 border-yellow-500/30 text-yellow-400",
};

export function Timeline({ segments, totalDuration }: TimelineProps) {
  const [active, setActive] = useState<Segment | null>(null);

  return (
    <div>
      {/* Color legend */}
      <div className="flex items-center gap-4 mb-3 text-xs text-white/50">
        {Object.entries(TYPE_LABELS).map(([type, label]) => (
          <div key={type} className="flex items-center gap-1.5">
            <div
              className="w-3 h-3 rounded-sm"
              style={{ backgroundColor: TYPE_COLORS[type] }}
            />
            <span>{label}</span>
          </div>
        ))}
      </div>

      {/* Timeline bar */}
      <div
        className="w-full h-10 rounded-lg overflow-hidden flex cursor-pointer relative"
        style={{ background: "#1a1a1a" }}
      >
        {segments.map((seg, i) => {
          const widthPct = (seg.duration / totalDuration) * 100;
          return (
            <div
              key={i}
              title={`${TYPE_LABELS[seg.type]} — ${formatTime(seg.start)} → ${formatTime(seg.end)}`}
              onClick={() => setActive(active?.start === seg.start ? null : seg)}
              style={{
                width: `${widthPct}%`,
                backgroundColor: TYPE_COLORS[seg.type],
                opacity: active
                  ? active.start === seg.start
                    ? 1
                    : 0.35
                  : seg.type === "keep"
                  ? 0.6
                  : 0.85,
                minWidth: widthPct > 0 ? "2px" : 0,
                transition: "opacity 0.15s",
              }}
              className="h-full"
            />
          );
        })}
      </div>

      {/* Timestamps below */}
      <div className="flex justify-between text-xs text-white/25 mt-1">
        <span>{formatTime(0)}</span>
        <span>{formatTime(totalDuration / 2)}</span>
        <span>{formatTime(totalDuration)}</span>
      </div>

      {/* Active segment detail */}
      {active && (
        <div
          className={`mt-4 p-3 rounded-lg border text-sm ${TYPE_BG[active.type]}`}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold uppercase text-xs tracking-wider">
              {TYPE_LABELS[active.type]}
            </span>
            <button
              onClick={() => setActive(null)}
              className="text-white/30 hover:text-white/60 text-xs"
            >
              ✕
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs mt-2">
            <div>
              <p className="text-white/40">Start</p>
              <p className="font-mono">{formatTime(active.start)}</p>
            </div>
            <div>
              <p className="text-white/40">End</p>
              <p className="font-mono">{formatTime(active.end)}</p>
            </div>
            <div>
              <p className="text-white/40">Duration</p>
              <p className="font-mono">{active.duration.toFixed(2)}s</p>
            </div>
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
                <span className="font-mono text-xs">
                  {((active.score || 0) * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
