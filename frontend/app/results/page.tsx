"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Timeline } from "@/components/Timeline";
import { formatTime } from "@/lib/api";
import type { AnalysisResult } from "@/lib/types";

export default function ResultsPage() {
  const router = useRouter();
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [activeTab, setActiveTab] = useState<"timeline" | "highlights" | "cuts">("timeline");

  useEffect(() => {
    const raw = sessionStorage.getItem("gc_result");
    if (!raw) {
      router.push("/");
      return;
    }
    try {
      setResult(JSON.parse(raw));
    } catch {
      router.push("/");
    }
  }, [router]);

  if (!result) {
    return (
      <div className="flex items-center justify-center h-64 text-white/40">
        Loading results...
      </div>
    );
  }

  const { edl, highlights, dead_zones, duration, filename, preset_name } = result;
  const { summary } = edl;

  const handleExport = (format: "json" | "csv" | "edl") => {
    let content = "";
    let mime = "text/plain";
    let ext = format;

    if (format === "json") {
      content = JSON.stringify(result.edl, null, 2);
      mime = "application/json";
    } else if (format === "csv") {
      const rows = [
        ["index", "start", "end", "duration", "type", "score"],
        ...edl.segments.map((s, i) => [
          i + 1,
          s.start,
          s.end,
          s.duration,
          s.type,
          s.score ?? "",
        ]),
      ];
      content = rows.map((r) => r.join(",")).join("\n");
      mime = "text/csv";
    } else if (format === "edl") {
      // Use server-exported EDL if available
      const exports = (result as unknown as { exports?: { edl?: string } }).exports;
      content = exports?.edl ?? "EDL export not available. Re-analyze with export_format=all.";
    }

    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gamecut_${filename?.replace(/\.[^.]+$/, "") ?? "export"}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Analysis Results</h1>
          <p className="text-white/40 text-sm mt-1">
            {filename} &middot; {preset_name} &middot; {formatTime(duration)}
          </p>
        </div>
        <button
          onClick={() => {
            sessionStorage.removeItem("gc_result");
            sessionStorage.removeItem("gc_status");
            router.push("/");
          }}
          className="text-sm text-white/40 hover:text-white/70 transition-colors"
        >
          ← New analysis
        </button>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
        <StatCard
          label="Cut savings"
          value={`${summary.cut_savings_pct}%`}
          sub={`${summary.dead_zone_count} dead zones`}
          color="text-red-400"
        />
        <StatCard
          label="Highlights"
          value={`${summary.highlight_pct}%`}
          sub={`${summary.highlight_count} moments`}
          color="text-green-400"
        />
        <StatCard
          label="Dead time"
          value={`${summary.dead_zone_duration.toFixed(0)}s`}
          sub="can be cut"
          color="text-red-300"
        />
        <StatCard
          label="Keep time"
          value={`${summary.keep_duration.toFixed(0)}s`}
          sub="neutral content"
          color="text-yellow-400"
        />
      </div>

      {/* Timeline */}
      <div className="bg-white/5 rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-4">
          Visual Timeline
        </h2>
        <Timeline segments={edl.segments} totalDuration={duration} />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 bg-white/5 rounded-lg p-1 w-fit">
        {(["timeline", "highlights", "cuts"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all
              ${activeTab === tab ? "bg-white/10 text-white" : "text-white/40 hover:text-white/70"}`}
          >
            {tab === "timeline"
              ? "All Segments"
              : tab === "highlights"
              ? `Highlights (${highlights.length})`
              : `Dead Zones (${dead_zones.length})`}
          </button>
        ))}
      </div>

      {/* Segment list */}
      <div className="space-y-2 mb-8">
        {activeTab === "timeline" &&
          edl.segments.map((seg, i) => (
            <SegmentRow key={i} index={i + 1} type={seg.type} start={seg.start} end={seg.end} duration={seg.duration} score={seg.score} />
          ))}

        {activeTab === "highlights" &&
          highlights.map((hl, i) => (
            <HighlightRow key={i} hl={hl} index={i + 1} />
          ))}

        {activeTab === "cuts" &&
          dead_zones.map((dz, i) => (
            <SegmentRow key={i} index={i + 1} type="dead_zone" start={dz.start} end={dz.end} duration={dz.duration} score={dz.confidence} />
          ))}
      </div>

      {/* Export buttons */}
      <div className="border-t border-white/10 pt-6">
        <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-4">
          Export
        </h2>
        <div className="flex flex-wrap gap-3">
          <ExportButton onClick={() => handleExport("json")} label="EDL (JSON)" icon="📄" />
          <ExportButton onClick={() => handleExport("csv")} label="CSV" icon="📊" />
          <ExportButton onClick={() => handleExport("edl")} label="DaVinci EDL" icon="🎬" />
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub: string;
  color: string;
}) {
  return (
    <div className="bg-white/5 rounded-lg p-4">
      <p className="text-xs text-white/40 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      <p className="text-xs text-white/30 mt-1">{sub}</p>
    </div>
  );
}

const SEG_COLORS: Record<string, string> = {
  dead_zone: "border-red-500/40 text-red-400",
  highlight: "border-green-500/40 text-green-400",
  keep: "border-yellow-500/40 text-yellow-400",
};

const SEG_LABELS: Record<string, string> = {
  dead_zone: "CUT",
  highlight: "HIGHLIGHT",
  keep: "KEEP",
};

function SegmentRow({
  index, type, start, end, duration, score
}: {
  index: number;
  type: string;
  start: number;
  end: number;
  duration: number;
  score: number | null;
}) {
  return (
    <div className={`flex items-center gap-3 p-3 rounded-lg border bg-white/[0.02] text-sm ${SEG_COLORS[type] || "border-white/10 text-white/60"}`}>
      <span className="text-white/20 w-6 text-right text-xs">{index}</span>
      <span className="font-semibold text-xs w-16">{SEG_LABELS[type] || type}</span>
      <span className="font-mono text-white/70">{formatTime(start)}</span>
      <span className="text-white/20">→</span>
      <span className="font-mono text-white/70">{formatTime(end)}</span>
      <span className="ml-auto text-white/30 text-xs">{duration.toFixed(2)}s</span>
      {score !== null && (
        <span className="text-xs font-mono w-10 text-right">{(score * 100).toFixed(0)}%</span>
      )}
    </div>
  );
}

function HighlightRow({ hl, index }: { hl: import("@/lib/types").Highlight; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="border border-green-500/30 bg-green-500/5 rounded-lg overflow-hidden cursor-pointer"
      onClick={() => setOpen((v) => !v)}
    >
      <div className="flex items-center gap-3 p-3 text-sm">
        <span className="text-white/20 w-6 text-right text-xs">{index}</span>
        <span className="font-semibold text-xs w-16 text-green-400">HIGHLIGHT</span>
        <span className="font-mono text-white/70">{formatTime(hl.start)}</span>
        <span className="text-white/20">→</span>
        <span className="font-mono text-white/70">{formatTime(hl.end)}</span>
        <span className="ml-auto font-bold text-green-400">
          {(hl.composite_score * 100).toFixed(0)}%
        </span>
        <span className="text-white/20 text-xs">{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div className="px-4 pb-3 grid grid-cols-3 gap-3 text-xs border-t border-white/5">
          <ScoreBar label="Volume" value={hl.volume_score} color="#6366f1" />
          <ScoreBar label="Pitch" value={hl.pitch_score} color="#8b5cf6" />
          <ScoreBar label="Speech Rate" value={hl.speech_rate_score} color="#a78bfa" />
        </div>
      )}
    </div>
  );
}

function ScoreBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="mt-2">
      <div className="flex justify-between mb-1">
        <span className="text-white/40">{label}</span>
        <span style={{ color }}>{(value * 100).toFixed(0)}%</span>
      </div>
      <div className="bg-white/10 rounded-full h-1">
        <div
          className="h-1 rounded-full"
          style={{ width: `${value * 100}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function ExportButton({
  onClick, label, icon
}: {
  onClick: () => void;
  label: string;
  icon: string;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-sm transition-all"
    >
      <span>{icon}</span>
      <span>{label}</span>
    </button>
  );
}
