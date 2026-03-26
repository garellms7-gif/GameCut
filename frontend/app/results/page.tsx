"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Timeline } from "@/components/Timeline";
import { assembleHighlights, formatTime, submitFeedback } from "@/lib/api";
import { getUploadedFile } from "@/lib/fileStore";
import type {
  AnalysisResult,
  FeedbackVote,
  Highlight,
  Segment,
  StruggleZone,
  ThresholdAdjustment,
} from "@/lib/types";

export default function ResultsPage() {
  const router = useRouter();
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [activeTab, setActiveTab] = useState<"timeline" | "highlights" | "cuts" | "struggle">("timeline");
  // Map of segmentKey → "up" | "down" to track which segments have been voted on
  const [votes, setVotes] = useState<Map<string, FeedbackVote>>(new Map());
  // Toast message
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  // Highlight reel assembly
  const [assembling, setAssembling] = useState(false);
  const [assembleError, setAssembleError] = useState<string | null>(null);
  // Video preview panel
  const [previewSegment, setPreviewSegment] = useState<Segment | null>(null);
  const [videoObjectUrl, setVideoObjectUrl] = useState<string | null>(null);
  // Per-segment approve / reject actions (key → action)
  const [segmentActions, setSegmentActions] = useState<Map<string, "approved" | "rejected">>(new Map());

  useEffect(() => {
    const file = getUploadedFile();
    if (!file) return;
    const url = URL.createObjectURL(file);
    setVideoObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, []);

  const segKey = (seg: { type: string; start: number; end: number }) =>
    `${seg.type}:${seg.start}:${seg.end}`;

  const handleSegmentAction = useCallback(
    (seg: Segment, action: "approved" | "rejected") => {
      setSegmentActions((prev) => {
        const next = new Map(prev);
        const k = segKey(seg);
        // Toggle off if same action clicked twice
        if (next.get(k) === action) next.delete(k);
        else next.set(k, action);
        return next;
      });
    },
    [],
  );

  useEffect(() => {
    const raw = sessionStorage.getItem("gc_result");
    if (!raw) { router.push("/"); return; }
    try { setResult(JSON.parse(raw)); }
    catch { router.push("/"); }
  }, [router]);

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 2800);
  };

  const handleVote = useCallback(
    async (
      segmentKey: string,
      vote: FeedbackVote,
      segmentType: "dead_zone" | "highlight",
      score: number | null,
      duration: number,
    ) => {
      if (!result) return;
      // Optimistically mark as voted
      setVotes((prev) => new Map(prev).set(segmentKey, vote));
      try {
        const res = await submitFeedback({
          preset: result.preset,
          segment_type: segmentType,
          vote,
          score,
          duration,
        });
        showToast(res.message, true);
      } catch (e: unknown) {
        // Roll back on error
        setVotes((prev) => {
          const next = new Map(prev);
          next.delete(segmentKey);
          return next;
        });
        showToast(e instanceof Error ? e.message : "Feedback failed", false);
      }
    },
    [result],
  );

  if (!result) {
    return (
      <div className="flex items-center justify-center h-64 text-white/40">
        Loading results...
      </div>
    );
  }

  const { edl, highlights, dead_zones, struggle_zones, duration, filename, preset_name, threshold_adjustments } = result;
  const { summary } = edl;
  const hasAdjustments = threshold_adjustments && Object.keys(threshold_adjustments).length > 0;
  const hasStruggleZones = (struggle_zones ?? []).length > 0;

  const handleExport = (format: "json" | "csv" | "edl" | "fcpxml" | "capcut") => {
    let content = "";
    let mime = "text/plain";
    const exports = (result as unknown as {
      exports?: { edl?: string; fcpxml?: string; capcut?: string };
    }).exports;

    if (format === "json") {
      // Apply any approve/reject overrides to the EDL before export
      const editedEdl = {
        ...result.edl,
        segments: result.edl.segments.map((s) => {
          const k = segKey(s);
          const action = segmentActions.get(k);
          if (!action) return s;
          return {
            ...s,
            type: action === "approved" ? "keep" : "dead_zone",
            action,
          };
        }),
      };
      content = JSON.stringify(editedEdl, null, 2);
      mime = "application/json";
    } else if (format === "csv") {
      const rows = [
        ["index", "start", "end", "duration", "type", "score"],
        ...edl.segments.map((s, i) => [i + 1, s.start, s.end, s.duration, s.type, s.score ?? ""]),
      ];
      content = rows.map((r) => r.join(",")).join("\n");
      mime = "text/csv";
    } else if (format === "edl") {
      content = exports?.edl ?? "EDL export not available. Re-analyze with export_format=all.";
    } else if (format === "fcpxml") {
      content = exports?.fcpxml ?? "FCPXML export not available. Re-analyze with export_format=all.";
      mime = "application/xml";
    } else if (format === "capcut") {
      content = exports?.capcut ?? "CapCut export not available. Re-analyze with export_format=all.";
      mime = "application/json";
    }

    const downloadName =
      format === "fcpxml" ? "gamecut_timeline.fcpxml" :
      format === "capcut" ? "draft_content.json" :
      `gamecut_${filename?.replace(/\.[^.]+$/, "") ?? "export"}.${format}`;

    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = downloadName;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleAssemble = async () => {
    if (!result) return;
    const sourceFile = getUploadedFile();
    if (!sourceFile) {
      setAssembleError("Original video not available. Please re-upload and analyze first.");
      return;
    }
    if (!result.highlights || result.highlights.length === 0) {
      setAssembleError("No highlight segments detected to assemble.");
      return;
    }
    setAssembling(true);
    setAssembleError(null);
    try {
      const blob = await assembleHighlights(sourceFile, result.highlights);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const stem = result.filename?.replace(/\.[^.]+$/, "") ?? "clip";
      a.download = `${stem}_highlights.mp4`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setAssembleError(err instanceof Error ? err.message : "Assembly failed");
    } finally {
      setAssembling(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      {/* Toast */}
      {toast && (
        <div
          className={`fixed bottom-6 left-1/2 -translate-x-1/2 px-5 py-2.5 rounded-full text-sm font-medium shadow-xl transition-all z-50
            ${toast.ok ? "bg-green-600 text-white" : "bg-red-600 text-white"}`}
        >
          {toast.msg}
        </div>
      )}

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

      {/* Threshold adjustment banner */}
      {hasAdjustments && (
        <div className="mb-6 p-4 rounded-xl border border-indigo-500/30 bg-indigo-500/5 text-sm">
          <p className="font-semibold text-indigo-300 mb-2">
            Thresholds adjusted from past feedback
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-1 text-xs text-white/50">
            {Object.entries(threshold_adjustments!).map(([key, adj]) => (
              <AdjustmentBadge key={key} name={key} adj={adj} />
            ))}
          </div>
        </div>
      )}

      {/* Summary stats */}
      <div className={`grid gap-3 mb-8 ${hasStruggleZones ? "grid-cols-2 sm:grid-cols-5" : "grid-cols-2 sm:grid-cols-4"}`}>
        <StatCard label="Cut savings" value={`${summary.cut_savings_pct}%`} sub={`${summary.dead_zone_count} dead zones`} color="text-red-400" />
        <StatCard label="Highlights" value={`${summary.highlight_pct}%`} sub={`${summary.highlight_count} moments`} color="text-green-400" />
        <StatCard label="Dead time" value={`${summary.dead_zone_duration.toFixed(0)}s`} sub="can be cut" color="text-red-300" />
        <StatCard label="Keep time" value={`${summary.keep_duration.toFixed(0)}s`} sub="neutral content" color="text-yellow-400" />
        {hasStruggleZones && (
          <StatCard
            label="Struggle zones"
            value={String(summary.struggle_zone_count ?? (struggle_zones ?? []).length)}
            sub={`${(summary.struggle_zone_duration ?? 0).toFixed(0)}s montage`}
            color="text-orange-400"
          />
        )}
      </div>

      {/* Timeline */}
      <div className="bg-white/5 rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-4">
          Visual Timeline
        </h2>
        <Timeline
          segments={edl.segments}
          totalDuration={duration}
          struggleZones={struggle_zones ?? []}
          videoFile={getUploadedFile()}
          onSegmentSelect={setPreviewSegment}
        />
      </div>

      {/* Video preview panel */}
      {previewSegment && videoObjectUrl && (
        <VideoPreview
          segment={previewSegment}
          videoUrl={videoObjectUrl}
          action={segmentActions.get(segKey(previewSegment)) ?? null}
          onApprove={() => handleSegmentAction(previewSegment, "approved")}
          onReject={() => handleSegmentAction(previewSegment, "rejected")}
          onClose={() => setPreviewSegment(null)}
        />
      )}
      {previewSegment && !videoObjectUrl && (
        <div className="bg-white/5 rounded-xl p-4 mb-6 border border-white/10 text-sm text-white/40">
          Video preview unavailable — re-upload your file to enable playback.
        </div>
      )}

      {/* Feedback hint */}
      <p className="text-xs text-white/30 mb-3 pl-1">
        Use thumbs up / down on segments to teach GameCut — corrections improve future analyses for the <span className="text-indigo-400">{preset_name}</span> preset.
      </p>

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 mb-4 bg-white/5 rounded-lg p-1 w-fit">
        {(["timeline", "highlights", "cuts", ...(hasStruggleZones ? ["struggle"] : [])] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab as typeof activeTab)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all
              ${activeTab === tab ? "bg-white/10 text-white" : "text-white/40 hover:text-white/70"}`}
          >
            {tab === "timeline"
              ? "All Segments"
              : tab === "highlights"
              ? `Highlights (${highlights.length})`
              : tab === "struggle"
              ? `Struggle Zones (${(struggle_zones ?? []).length})`
              : `Dead Zones (${dead_zones.length})`}
          </button>
        ))}
      </div>

      {/* Segment list */}
      <div className="space-y-2 mb-8">
        {activeTab === "timeline" &&
          edl.segments.map((seg, i) => (
            <SegmentRow
              key={i} index={i + 1}
              type={seg.type} start={seg.start} end={seg.end}
              duration={seg.duration} score={seg.score}
              currentVote={votes.get(`${seg.type}-${seg.start}-${seg.end}`) ?? null}
              action={segmentActions.get(segKey(seg)) ?? null}
              onVote={(vote) =>
                seg.type !== "keep" &&
                handleVote(
                  `${seg.type}-${seg.start}-${seg.end}`,
                  vote, seg.type as "dead_zone" | "highlight",
                  seg.score, seg.duration,
                )
              }
              onPreview={() => setPreviewSegment(
                previewSegment?.start === seg.start && previewSegment.end === seg.end
                  ? null : seg
              )}
            />
          ))}

        {activeTab === "highlights" &&
          highlights.map((hl, i) => (
            <HighlightRow
              key={i} hl={hl} index={i + 1}
              currentVote={votes.get(`highlight-${hl.start}-${hl.end}`) ?? null}
              onVote={(vote) =>
                handleVote(
                  `highlight-${hl.start}-${hl.end}`,
                  vote, "highlight",
                  hl.composite_score, hl.duration,
                )
              }
            />
          ))}

        {activeTab === "cuts" &&
          dead_zones.map((dz, i) => (
            <SegmentRow
              key={i} index={i + 1}
              type="dead_zone" start={dz.start} end={dz.end}
              duration={dz.duration} score={dz.confidence}
              currentVote={votes.get(`dead_zone-${dz.start}-${dz.end}`) ?? null}
              onVote={(vote) =>
                handleVote(
                  `dead_zone-${dz.start}-${dz.end}`,
                  vote, "dead_zone",
                  dz.confidence, dz.duration,
                )
              }
            />
          ))}

        {activeTab === "struggle" &&
          (struggle_zones ?? []).map((sz, i) => (
            <StruggleZoneRow key={i} sz={sz} index={i + 1} />
          ))}
      </div>

      {/* Export */}
      <div className="border-t border-white/10 pt-6">
        <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-4">Export</h2>
        <div className="flex flex-wrap gap-3">
          <ExportButton onClick={() => handleExport("json")} label="EDL (JSON)" icon="📄" />
          <ExportButton onClick={() => handleExport("csv")} label="CSV" icon="📊" />
          <ExportButton onClick={() => handleExport("edl")} label="DaVinci EDL" icon="🎬" />
          <ExportButton onClick={() => handleExport("fcpxml")} label="FCPXML (Resolve)" icon="🎞️" />
          <ExportButton onClick={() => handleExport("capcut")} label="Export for CapCut" icon="✂️" />
        </div>
      </div>

      {/* Assemble highlight reel */}
      <div className="border-t border-white/10 pt-6">
        <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-2">Highlight Reel</h2>
        <p className="text-xs text-white/40 mb-4">
          Extracts all highlight segments, sorts by score, and concatenates them with
          0.5 s black transitions into a single downloadable MP4.
        </p>
        <button
          onClick={handleAssemble}
          disabled={assembling}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500
            disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold transition-colors"
        >
          {assembling ? (
            <>
              <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Assembling…
            </>
          ) : (
            <>
              <span>🎬</span>
              Assemble Highlight Reel
            </>
          )}
        </button>
        {assembleError && (
          <p className="mt-2 text-xs text-red-400">{assembleError}</p>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function AdjustmentBadge({ name, adj }: { name: string; adj: ThresholdAdjustment }) {
  const positive = adj.delta > 0;
  return (
    <div className="flex items-center gap-1.5">
      <span className={`text-xs font-mono ${positive ? "text-orange-400" : "text-sky-400"}`}>
        {positive ? "+" : ""}{adj.delta.toFixed(2)}
      </span>
      <span className="text-white/40">{name.replace(/_/g, " ")}</span>
    </div>
  );
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
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

function FeedbackButtons({
  currentVote,
  onVote,
}: {
  currentVote: FeedbackVote | null;
  onVote: (v: FeedbackVote) => void;
}) {
  return (
    <div className="flex items-center gap-1 ml-2">
      <button
        title="Good detection"
        onClick={(e) => { e.stopPropagation(); onVote("up"); }}
        className={`w-6 h-6 rounded flex items-center justify-center text-xs transition-all
          ${currentVote === "up"
            ? "bg-green-500/30 text-green-400"
            : "text-white/20 hover:text-green-400 hover:bg-green-500/10"
          }`}
      >
        ▲
      </button>
      <button
        title="Wrong detection"
        onClick={(e) => { e.stopPropagation(); onVote("down"); }}
        className={`w-6 h-6 rounded flex items-center justify-center text-xs transition-all
          ${currentVote === "down"
            ? "bg-red-500/30 text-red-400"
            : "text-white/20 hover:text-red-400 hover:bg-red-500/10"
          }`}
      >
        ▼
      </button>
    </div>
  );
}

function SegmentRow({
  index, type, start, end, duration, score,
  currentVote, action, onVote, onPreview,
}: {
  index: number;
  type: string;
  start: number;
  end: number;
  duration: number;
  score: number | null;
  currentVote: FeedbackVote | null;
  action: "approved" | "rejected" | null;
  onVote: (v: FeedbackVote) => void;
  onPreview?: () => void;
}) {
  const showFeedback = type !== "keep";
  const actionBadge =
    action === "approved" ? "bg-green-500/20 text-green-400" :
    action === "rejected" ? "bg-red-500/20 text-red-400" : null;

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
      {actionBadge && (
        <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${actionBadge}`}>
          {action === "approved" ? "✓ keep" : "✕ cut"}
        </span>
      )}
      {onPreview && (
        <button
          onClick={onPreview}
          title="Preview segment"
          className="w-6 h-6 rounded flex items-center justify-center text-xs text-white/20 hover:text-white/60 hover:bg-white/10 transition-all"
        >
          ▶
        </button>
      )}
      {showFeedback && (
        <FeedbackButtons currentVote={currentVote} onVote={onVote} />
      )}
    </div>
  );
}

function HighlightRow({
  hl, index, currentVote, onVote,
}: {
  hl: Highlight;
  index: number;
  currentVote: FeedbackVote | null;
  onVote: (v: FeedbackVote) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-green-500/30 bg-green-500/5 rounded-lg overflow-hidden">
      <div
        className="flex items-center gap-3 p-3 text-sm cursor-pointer"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-white/20 w-6 text-right text-xs">{index}</span>
        <span className="font-semibold text-xs w-16 text-green-400">HIGHLIGHT</span>
        <span className="font-mono text-white/70">{formatTime(hl.start)}</span>
        <span className="text-white/20">→</span>
        <span className="font-mono text-white/70">{formatTime(hl.end)}</span>
        <span className="ml-auto font-bold text-green-400">
          {(hl.composite_score * 100).toFixed(0)}%
        </span>
        <FeedbackButtons currentVote={currentVote} onVote={onVote} />
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
        <div className="h-1 rounded-full" style={{ width: `${value * 100}%`, backgroundColor: color }} />
      </div>
    </div>
  );
}

function StruggleZoneRow({ sz, index }: { sz: StruggleZone; index: number }) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border border-orange-500/30 bg-orange-500/5 text-sm">
      <span className="text-white/20 w-6 text-right text-xs">{index}</span>
      <span className="font-semibold text-xs w-20 text-orange-400">STRUGGLE</span>
      <span className="font-mono text-white/70">{formatTime(sz.start)}</span>
      <span className="text-white/20">→</span>
      <span className="font-mono text-white/70">{formatTime(sz.end)}</span>
      <span className="ml-auto text-white/30 text-xs">{sz.duration.toFixed(1)}s</span>
      <span className="text-xs text-orange-400/70">{sz.dead_zone_count} dead zones</span>
      <span className="text-xs px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-300 font-mono">
        MONTAGE
      </span>
    </div>
  );
}

// ── VideoPreview ──────────────────────────────────────────────────────────────

const PREVIEW_TYPE_COLOR: Record<string, string> = {
  dead_zone: "text-red-400",
  highlight: "text-green-400",
  keep:      "text-yellow-400",
};
const PREVIEW_TYPE_BORDER: Record<string, string> = {
  dead_zone: "border-red-500/30 bg-red-500/5",
  highlight: "border-green-500/30 bg-green-500/5",
  keep:      "border-yellow-500/30 bg-yellow-500/5",
};

function VideoPreview({
  segment, videoUrl, action, onApprove, onReject, onClose,
}: {
  segment: Segment;
  videoUrl: string;
  action: "approved" | "rejected" | null;
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  // Seek and play whenever the segment changes
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    let done = false;
    video.currentTime = segment.start;
    video.play().catch(() => {});

    const onTimeUpdate = () => {
      if (!done && video.currentTime >= segment.end) {
        done = true;
        video.pause();
      }
    };
    // Reset done flag when seeking back manually
    const onSeeked = () => { done = false; };

    video.addEventListener("timeupdate", onTimeUpdate);
    video.addEventListener("seeked", onSeeked);
    return () => {
      video.removeEventListener("timeupdate", onTimeUpdate);
      video.removeEventListener("seeked", onSeeked);
    };
  }, [segment]);

  const typeColor  = PREVIEW_TYPE_COLOR[segment.type]  ?? "text-white/60";
  const typeBorder = PREVIEW_TYPE_BORDER[segment.type] ?? "border-white/10 bg-white/5";

  return (
    <div className={`rounded-xl p-4 border mb-6 ${typeBorder}`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <span className={`text-xs font-bold uppercase tracking-wider ${typeColor}`}>
          {SEG_LABELS[segment.type] ?? segment.type}
        </span>
        <span className="font-mono text-xs text-white/50">
          {formatTime(segment.start)} → {formatTime(segment.end)}
        </span>
        <span className="text-xs text-white/30">{segment.duration.toFixed(2)} s</span>
        {segment.score !== null && (
          <span className={`text-xs font-mono ${typeColor}`}>
            score {((segment.score ?? 0) * 100).toFixed(0)}%
          </span>
        )}
        <button
          onClick={onClose}
          className="ml-auto text-white/25 hover:text-white/60 text-sm"
        >✕</button>
      </div>

      {/* Player */}
      <video
        ref={videoRef}
        src={videoUrl}
        controls
        className="w-full rounded-lg bg-black"
        style={{ maxHeight: "360px" }}
      />

      {/* Actions */}
      <div className="flex items-center gap-3 mt-3">
        <button
          onClick={onApprove}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold transition-all
            ${action === "approved"
              ? "bg-green-500 text-white shadow-lg shadow-green-500/30"
              : "bg-green-500/15 text-green-400 hover:bg-green-500/25"}`}
        >
          ✓ Approve
        </button>
        <button
          onClick={onReject}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold transition-all
            ${action === "rejected"
              ? "bg-red-500 text-white shadow-lg shadow-red-500/30"
              : "bg-red-500/15 text-red-400 hover:bg-red-500/25"}`}
        >
          ✕ Reject
        </button>
        <button
          onClick={() => {
            const v = videoRef.current;
            if (v) { v.currentTime = segment.start; v.play().catch(() => {}); }
          }}
          className="ml-auto text-xs text-white/30 hover:text-white/60 flex items-center gap-1 transition-colors"
        >
          ↺ Replay
        </button>
      </div>

      {action && (
        <p className={`text-xs mt-2 font-medium
          ${action === "approved" ? "text-green-400" : "text-red-400"}`}>
          {action === "approved"
            ? "✓ Marked as keep — will appear in EDL export as keep"
            : "✕ Marked for removal — will appear in EDL export as dead_zone"}
        </p>
      )}
    </div>
  );
}

function ExportButton({ onClick, label, icon }: { onClick: () => void; label: string; icon: string }) {
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
