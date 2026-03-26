"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { sliceShorts, formatTime } from "@/lib/api";
import { getUploadedFile, getUploadedFilePath, getUploadedName } from "@/lib/fileStore";
import { isTauri, localFileUrl } from "@/lib/tauri";
import type { Highlight } from "@/lib/types";

export default function ShortsPage() {
  const router = useRouter();
  const [highlights, setHighlights]   = useState<Highlight[]>([]);
  const [selected, setSelected]       = useState<Set<number>>(new Set());
  const [cropPct, setCropPct]         = useState(50);
  const [videoUrl, setVideoUrl]       = useState<string | null>(null);
  const [activeIdx, setActiveIdx]     = useState<number | null>(null);
  const [slicing, setSlicing]         = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const previewVideoRef               = useRef<HTMLVideoElement>(null);

  // Load highlights from sessionStorage
  useEffect(() => {
    const raw = sessionStorage.getItem("gc_shorts_highlights");
    if (!raw) { router.push("/"); return; }
    try {
      const hls: Highlight[] = JSON.parse(raw);
      if (!hls.length) { router.push("/"); return; }
      setHighlights(hls);
      setSelected(new Set(hls.map((_, i) => i)));
      setActiveIdx(0);
    } catch { router.push("/"); }
  }, [router]);

  // Load video URL (same pattern as results page)
  useEffect(() => {
    let objectUrl: string | null = null;
    (async () => {
      if (isTauri()) {
        const path = getUploadedFilePath();
        if (!path) return;
        const url = await localFileUrl(path);
        if (url) setVideoUrl(url);
      } else {
        const file = getUploadedFile();
        if (!file) return;
        objectUrl = URL.createObjectURL(file);
        setVideoUrl(objectUrl);
      }
    })();
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, []);

  // Seek the preview panel video when active clip changes
  useEffect(() => {
    const video = previewVideoRef.current;
    if (!video || activeIdx === null || !highlights[activeIdx]) return;
    const seg    = highlights[activeIdx];
    const seekTo = seg.start + Math.min(2, seg.duration / 2);
    const doSeek = () => { video.currentTime = seekTo; };
    if (video.readyState >= 1) doSeek();
    else video.addEventListener("loadedmetadata", doSeek, { once: true });
  }, [activeIdx, videoUrl, highlights]);

  const toggleSelect = useCallback((i: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  }, []);

  const handleSlice = async () => {
    const source: File | string | null = isTauri() ? getUploadedFilePath() : getUploadedFile();
    if (!source) {
      setError("Original video not available — re-upload and analyze first.");
      return;
    }
    const clips = highlights.filter((_, i) => selected.has(i));
    if (!clips.length) { setError("Select at least one clip."); return; }

    setSlicing(true);
    setError(null);
    try {
      const blob = await sliceShorts(source, clips, cropPct);
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      const stem = (getUploadedName() ?? sessionStorage.getItem("gc_file_name") ?? "shorts")
        .replace(/\.[^.]+$/, "");
      a.download = `${stem}_shorts.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Slicing failed");
    } finally {
      setSlicing(false);
    }
  };

  const filename   = getUploadedName() ?? sessionStorage.getItem("gc_file_name") ?? "video";
  const activeClip = activeIdx !== null ? highlights[activeIdx] : null;

  if (!highlights.length) {
    return (
      <div className="flex items-center justify-center h-64 text-white/40">Loading…</div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Shorts Slicer</h1>
          <p className="text-white/40 text-sm mt-1">
            {filename} &middot; {highlights.length} highlight{highlights.length !== 1 ? "s" : ""}
          </p>
        </div>
        <button
          onClick={() => router.back()}
          className="text-sm text-white/40 hover:text-white/70 transition-colors"
        >
          ← Back to results
        </button>
      </div>

      <div className="flex flex-col lg:flex-row gap-8">
        {/* ── Left: clip grid ─────────────────────────────────────────── */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs text-white/40">
              {selected.size} of {highlights.length} selected
            </p>
            <div className="flex gap-4">
              <button
                onClick={() => setSelected(new Set(highlights.map((_, i) => i)))}
                className="text-xs text-white/40 hover:text-white/70 transition-colors"
              >
                Select all
              </button>
              <button
                onClick={() => setSelected(new Set())}
                className="text-xs text-white/40 hover:text-white/70 transition-colors"
              >
                Deselect all
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {highlights.map((hl, i) => (
              <ClipCard
                key={i}
                index={i}
                hl={hl}
                videoUrl={videoUrl}
                cropPct={cropPct}
                selected={selected.has(i)}
                active={activeIdx === i}
                onToggle={() => toggleSelect(i)}
                onPreview={() => setActiveIdx(activeIdx === i ? null : i)}
              />
            ))}
          </div>
        </div>

        {/* ── Right: preview panel + controls ─────────────────────────── */}
        <div className="w-full lg:w-64 flex-shrink-0">
          <div className="sticky top-6 space-y-4">
            {/* Preview panel */}
            <div className="bg-white/5 rounded-2xl p-3">
              <p className="text-xs text-white/40 mb-2 uppercase tracking-wide font-medium">
                {activeClip ? `Clip ${(activeIdx ?? 0) + 1} preview` : "Select a clip to preview"}
              </p>

              {/* Phone-shaped preview window */}
              <div
                className="relative mx-auto overflow-hidden rounded-xl bg-black/60 border border-white/10"
                style={{ maxWidth: "180px", aspectRatio: "9/16" }}
              >
                {videoUrl && activeClip ? (
                  <video
                    ref={previewVideoRef}
                    key={videoUrl}
                    src={videoUrl}
                    preload="metadata"
                    muted
                    playsInline
                    controls
                    className="w-full h-full"
                    style={{ objectFit: "cover", objectPosition: `${cropPct}% 50%` }}
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <span className="text-white/15 text-4xl">↕</span>
                  </div>
                )}
              </div>

              {activeClip && (
                <p className="text-[11px] text-white/40 text-center mt-2">
                  {formatTime(activeClip.start)} — {formatTime(activeClip.end)}
                  <span className="ml-1.5 text-white/25">{activeClip.duration.toFixed(1)}s</span>
                </p>
              )}
            </div>

            {/* Crop position slider */}
            <div className="bg-white/5 rounded-xl p-4">
              <div className="flex justify-between mb-2">
                <label className="text-xs font-medium text-white/60">Crop position</label>
                <span className="text-xs font-mono text-violet-400">{cropPct}%</span>
              </div>
              <input
                type="range" min="0" max="100" step="1"
                value={cropPct}
                onChange={(e) => setCropPct(Number(e.target.value))}
                className="w-full accent-violet-500"
              />
              <div className="flex justify-between text-[10px] text-white/25 mt-1">
                <span>Left</span>
                <span>Center</span>
                <span>Right</span>
              </div>
            </div>

            {/* Slice CTA */}
            <button
              onClick={handleSlice}
              disabled={slicing || selected.size === 0}
              className="w-full py-3 rounded-xl bg-violet-600 hover:bg-violet-500
                disabled:opacity-40 disabled:cursor-not-allowed
                text-white font-semibold text-sm transition-colors
                flex items-center justify-center gap-2"
            >
              {slicing ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Slicing…
                </>
              ) : (
                <>↓ Slice {selected.size} clip{selected.size !== 1 ? "s" : ""} as Shorts</>
              )}
            </button>

            {error && <p className="text-xs text-red-400">{error}</p>}

            <p className="text-[11px] text-white/25 text-center leading-relaxed">
              Downloads a .zip of 1080×1920 MP4s ready to upload.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── ClipCard ──────────────────────────────────────────────────────────────────

function ClipCard({
  index, hl, videoUrl, cropPct, selected, active, onToggle, onPreview,
}: {
  index: number;
  hl: Highlight;
  videoUrl: string | null;
  cropPct: number;
  selected: boolean;
  active: boolean;
  onToggle: () => void;
  onPreview: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoUrl) return;
    const seekTo = hl.start + Math.min(2, hl.duration / 2);
    const doSeek = () => { video.currentTime = seekTo; };
    if (video.readyState >= 1) doSeek();
    else video.addEventListener("loadedmetadata", doSeek, { once: true });
  }, [videoUrl, hl.start, hl.duration]);

  const scoreColor =
    hl.composite_score >= 0.7 ? "text-green-400" :
    hl.composite_score >= 0.4 ? "text-yellow-400" :
    "text-white/40";

  return (
    <div
      className={`relative rounded-xl overflow-hidden cursor-pointer transition-all ring-2
        ${active ? "ring-violet-500" : selected ? "ring-indigo-500/40" : "ring-white/10 hover:ring-white/25"}`}
      onClick={onPreview}
    >
      {/* 9:16 vertical crop preview */}
      <div className="relative overflow-hidden bg-black" style={{ aspectRatio: "9/16" }}>
        {videoUrl ? (
          <video
            ref={videoRef}
            src={videoUrl}
            preload="metadata"
            muted
            playsInline
            className="w-full h-full"
            style={{ objectFit: "cover", objectPosition: `${cropPct}% 50%` }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-white/5">
            <span className="text-white/20 text-2xl">↕</span>
          </div>
        )}

        {/* Bottom gradient overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/20 p-2 flex flex-col justify-between pointer-events-none">
          {/* Top badges */}
          <div className="flex justify-between items-start">
            <span className="text-[10px] font-mono text-white/70 bg-black/50 px-1.5 py-0.5 rounded">
              #{index + 1}
            </span>
            {hl.source === "manual" && (
              <span className="text-[10px] bg-violet-600/90 text-white px-1.5 py-0.5 rounded capitalize leading-tight">
                {hl.manual_type ?? "manual"}
              </span>
            )}
          </div>
          {/* Bottom info */}
          <div>
            <p className="text-white text-[10px] font-mono leading-tight">
              {formatTime(hl.start)}
            </p>
            <div className="flex items-center gap-1.5">
              <p className="text-white/60 text-[10px]">{hl.duration.toFixed(1)}s</p>
              {hl.source !== "manual" && (
                <p className={`text-[10px] font-mono ${scoreColor}`}>
                  {Math.round(hl.composite_score * 100)}%
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Select checkbox */}
      <button
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        className={`absolute top-2 right-2 w-5 h-5 rounded-full border-2 flex items-center justify-center
          text-[9px] font-bold transition-all z-10
          ${selected
            ? "bg-indigo-500 border-indigo-400 text-white"
            : "bg-black/40 border-white/30 text-transparent hover:border-white/60"
          }`}
        aria-label={selected ? "Deselect clip" : "Select clip"}
      >
        ✓
      </button>
    </div>
  );
}
