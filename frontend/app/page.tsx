"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { analyzeVideo, detectPreset, fetchPresets } from "@/lib/api";
import { setUploadedFile } from "@/lib/fileStore";
import type { GamePreset, PresetDetectionResult } from "@/lib/types";

const PRESET_LABELS: Record<GamePreset, string> = {
  fps:        "FPS",
  open_world: "Open World",
  rpg:        "RPG",
  fighting:   "Fighting",
  platformer: "Platformer",
};

const SIGNAL_LABELS: Record<string, string> = {
  audio_energy_db:        "Loudness",
  spectral_centroid_norm: "Brightness",
  frame_change_rate:      "Camera motion",
  color_saturation_mean:  "Color vividness",
};

export default function UploadPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);

  const [dragActive, setDragActive]   = useState(false);
  const [file, setFile]               = useState<File | null>(null);
  const [preset, setPreset]           = useState<GamePreset>("fps");
  const [deadSens, setDeadSens]       = useState(1.0);
  const [hypeSens, setHypeSens]       = useState(1.0);
  const [presetDescs, setPresetDescs] = useState<Record<string, string>>({});
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState<string | null>(null);

  // Auto-detect state
  const [autoMode, setAutoMode]               = useState(true);
  const [detecting, setDetecting]             = useState(false);
  const [detection, setDetection]             = useState<PresetDetectionResult | null>(null);
  const [userOverride, setUserOverride]       = useState(false);
  const [detectError, setDetectError]         = useState<string | null>(null);
  const detectAbortRef                         = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchPresets()
      .then((data) => {
        const descs: Record<string, string> = {};
        Object.entries(data).forEach(([k, v]) => { descs[k] = v.description; });
        setPresetDescs(descs);
      })
      .catch(() => {});
  }, []);

  // Trigger auto-detection when a new file arrives and auto mode is on
  useEffect(() => {
    if (!file || !autoMode) return;

    // Cancel any in-flight detection for the previous file
    detectAbortRef.current?.abort();
    const abort = new AbortController();
    detectAbortRef.current = abort;

    setDetecting(true);
    setDetection(null);
    setDetectError(null);
    setUserOverride(false);

    detectPreset(file)
      .then((result) => {
        if (abort.signal.aborted) return;
        setDetection(result);
        setPreset(result.detected_preset);
        setDetecting(false);
      })
      .catch((err: Error) => {
        if (abort.signal.aborted) return;
        setDetectError(err.message);
        setDetecting(false);
      });

    return () => { abort.abort(); };
  }, [file, autoMode]);

  const handleFileSet = useCallback((f: File) => {
    setFile(f);
    setUploadedFile(f);
    setError(null);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files[0];
    if (f?.type.startsWith("video/")) handleFileSet(f);
  }, [handleFileSet]);

  const handleDragOver  = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragActive(true); }, []);
  const handleDragLeave = useCallback(() => setDragActive(false), []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleFileSet(f);
  };

  const handlePresetClick = (key: GamePreset) => {
    setPreset(key);
    if (detection && key !== detection.detected_preset) {
      setUserOverride(true);
    } else {
      setUserOverride(false);
    }
  };

  const handleAnalyze = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);

    sessionStorage.setItem("gc_file_name", file.name);
    sessionStorage.setItem("gc_file_size", String(file.size));
    sessionStorage.setItem("gc_preset", preset);
    sessionStorage.setItem("gc_status", "analyzing");

    router.push("/processing");
    await new Promise((r) => setTimeout(r, 100));

    try {
      const result = await analyzeVideo(file, preset, deadSens, hypeSens);
      sessionStorage.setItem("gc_result", JSON.stringify(result));
      sessionStorage.setItem("gc_status", "done");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Analysis failed";
      sessionStorage.setItem("gc_status", "error");
      sessionStorage.setItem("gc_error", msg);
      setError(msg);
      setLoading(false);
    }
  };

  const topScore = detection ? Math.max(...Object.values(detection.scores)) : 0;

  return (
    <div className="max-w-2xl mx-auto px-4 py-12">
      <h1 className="text-3xl font-bold mb-2">Upload Gameplay Footage</h1>
      <p className="text-white/50 mb-8">
        Drop a video to auto-detect dead zones and highlight moments.
      </p>

      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all
          ${dragActive ? "drag-active border-indigo-500 bg-indigo-500/5" : "border-white/20 hover:border-white/40"}
          ${file ? "border-green-500/60 bg-green-500/5" : ""}`}
      >
        <input ref={fileRef} type="file" accept="video/*" className="hidden" onChange={handleFileChange} />
        {file ? (
          <div>
            <div className="text-4xl mb-3">🎬</div>
            <p className="font-semibold text-green-400">{file.name}</p>
            <p className="text-sm text-white/40 mt-1">
              {(file.size / 1024 / 1024).toFixed(1)} MB — click to change
            </p>
          </div>
        ) : (
          <div>
            <div className="text-4xl mb-3 text-white/30">📁</div>
            <p className="text-white/60">Drag & drop a video file here</p>
            <p className="text-sm text-white/30 mt-1">or click to browse</p>
            <p className="text-xs text-white/20 mt-3">MP4, MOV, MKV, AVI supported</p>
          </div>
        )}
      </div>

      {/* ── Game preset ─────────────────────────────────────────────────── */}
      <div className="mt-8">
        {/* Header row with auto-detect toggle */}
        <div className="flex items-center justify-between mb-3">
          <label className="text-sm font-medium text-white/70">Game Preset</label>
          <button
            onClick={() => {
              const next = !autoMode;
              setAutoMode(next);
              if (!next) {
                setDetecting(false);
                detectAbortRef.current?.abort();
              } else if (file) {
                // Re-trigger detection
                setFile((f) => f ? new File([f], f.name, { type: f.type }) : f);
              }
            }}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-all border
              ${autoMode
                ? "bg-indigo-600/20 border-indigo-500/40 text-indigo-300"
                : "bg-white/5 border-white/10 text-white/40 hover:border-white/25"
              }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${autoMode ? "bg-indigo-400" : "bg-white/20"}`} />
            Auto-detect
          </button>
        </div>

        {/* Detection status banner */}
        {autoMode && (
          <div className="mb-3">
            {detecting && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-xs text-indigo-300">
                <span className="w-3 h-3 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                Analyzing first 90 seconds — extracting audio energy, frequency profile, frame rate, color palette…
              </div>
            )}

            {detection && !detecting && (
              <DetectionResult
                detection={detection}
                topScore={topScore}
                userOverride={userOverride}
                onRetry={() => {
                  if (file) {
                    setDetection(null);
                    setDetectError(null);
                    setUserOverride(false);
                    setFile((f) => f ? new File([f], f.name, { type: f.type }) : f);
                  }
                }}
              />
            )}

            {detectError && !detecting && (
              <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
                Detection failed: {detectError} — select a preset manually.
              </div>
            )}
          </div>
        )}

        {/* Preset buttons */}
        <div className="grid grid-cols-5 gap-2">
          {(Object.entries(PRESET_LABELS) as [GamePreset, string][]).map(([key, label]) => {
            const isSelected  = preset === key;
            const isDetected  = detection?.detected_preset === key;
            const score       = detection?.scores[key];
            const pct         = score !== undefined ? Math.round(score * 100) : null;

            return (
              <button
                key={key}
                onClick={() => handlePresetClick(key)}
                className={`relative py-2 px-1 rounded-lg text-sm font-medium transition-all border flex flex-col items-center gap-0.5
                  ${isSelected
                    ? "bg-indigo-600 border-indigo-500 text-white"
                    : "bg-white/5 border-white/10 text-white/60 hover:border-white/30"
                  }`}
              >
                {/* Detected badge */}
                {isDetected && !userOverride && (
                  <span className="absolute -top-1.5 -right-1.5 w-3.5 h-3.5 rounded-full bg-indigo-400 border-2 border-[#0f0f0f] flex items-center justify-center">
                    <span className="text-[6px] font-bold text-white">✓</span>
                  </span>
                )}
                <span>{label}</span>
                {pct !== null && (
                  <span
                    className={`text-[10px] font-mono leading-none ${
                      isSelected ? "text-indigo-200" : "text-white/25"
                    }`}
                  >
                    {pct}%
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Preset description */}
        {!detecting && presetDescs[preset] && (
          <p className="text-xs text-white/35 mt-2 pl-1">{presetDescs[preset]}</p>
        )}
      </div>

      {/* ── Sensitivity sliders ─────────────────────────────────────────── */}
      <div className="mt-8 space-y-5">
        <Slider
          label="Dead Zone Sensitivity"
          value={deadSens}
          onChange={setDeadSens}
          leftLabel="More sensitive"
          rightLabel="Less sensitive"
        />
        <Slider
          label="Hype Detection Sensitivity"
          value={hypeSens}
          onChange={setHypeSens}
          leftLabel="More highlights"
          rightLabel="Fewer highlights"
        />
      </div>

      {error && (
        <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Analyze button */}
      <button
        onClick={handleAnalyze}
        disabled={!file || loading || detecting}
        className={`mt-8 w-full py-3 rounded-xl font-semibold text-base transition-all
          ${file && !loading && !detecting
            ? "bg-indigo-600 hover:bg-indigo-500 text-white"
            : "bg-white/5 text-white/30 cursor-not-allowed"
          }`}
      >
        {loading ? "Analyzing…" : detecting ? "Detecting preset…" : "Analyze Video"}
      </button>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DetectionResult({
  detection,
  topScore,
  userOverride,
  onRetry,
}: {
  detection: PresetDetectionResult;
  topScore: number;
  userOverride: boolean;
  onRetry: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const pct = Math.round(detection.confidence * 100);

  return (
    <div className="rounded-lg border border-indigo-500/25 bg-indigo-500/5 overflow-hidden">
      {/* Summary row */}
      <div className="flex items-center gap-3 px-3 py-2 text-xs">
        {userOverride ? (
          <span className="text-white/40 italic">Preset manually overridden</span>
        ) : (
          <>
            <span className="text-indigo-300 font-semibold">
              Detected: {PRESET_LABELS[detection.detected_preset]}
            </span>
            <ConfidenceBar value={detection.confidence} />
            <span className="font-mono text-indigo-400">{pct}%</span>
          </>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onRetry}
            className="text-white/25 hover:text-white/60 transition-colors text-[10px] uppercase tracking-wide"
          >
            Retry
          </button>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-white/25 hover:text-white/60 transition-colors"
          >
            {expanded ? "▲" : "▼"}
          </button>
        </div>
      </div>

      {/* Expanded signal breakdown */}
      {expanded && (
        <div className="border-t border-white/5 px-3 py-3 grid grid-cols-2 gap-x-6 gap-y-2">
          <SignalGroup title="Audio signals">
            <SignalRow
              label="Loudness"
              value={`${detection.signals.audio_energy_db.toFixed(1)} dB`}
              hint="Higher = louder game audio"
            />
            <SignalRow
              label="Brightness"
              value={`${Math.round(detection.signals.spectral_centroid_norm * 100)}%`}
              hint="Higher = more treble / punchy SFX"
            />
          </SignalGroup>
          <SignalGroup title="Video signals">
            <SignalRow
              label="Camera motion"
              value={`${(detection.signals.frame_change_rate * 100).toFixed(1)}%`}
              hint="Higher = faster camera movement"
            />
            <SignalRow
              label="Color vividness"
              value={`${Math.round(detection.signals.color_saturation_mean * 100)}%`}
              hint="Higher = more vibrant art style"
            />
          </SignalGroup>
          <div className="col-span-2 mt-1">
            <p className="text-[10px] text-white/30 mb-1.5 uppercase tracking-wide">All preset scores</p>
            <div className="grid grid-cols-5 gap-1">
              {(Object.entries(detection.scores) as [GamePreset, number][])
                .sort(([, a], [, b]) => b - a)
                .map(([p, s]) => (
                  <div key={p} className="flex flex-col items-center gap-0.5">
                    <div className="w-full bg-white/10 rounded-full h-1 overflow-hidden">
                      <div
                        className="h-1 rounded-full bg-indigo-400"
                        style={{ width: `${s * 100}%` }}
                      />
                    </div>
                    <span className="text-[9px] text-white/40">{PRESET_LABELS[p]}</span>
                    <span className="text-[9px] font-mono text-indigo-400">{Math.round(s * 100)}%</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 0.6 ? "#818cf8" : value >= 0.4 ? "#a78bfa" : "#c4b5fd";
  return (
    <div className="flex-1 max-w-[80px] bg-white/10 rounded-full h-1 overflow-hidden">
      <div className="h-1 rounded-full transition-all" style={{ width: `${value * 100}%`, backgroundColor: color }} />
    </div>
  );
}

function SignalGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] text-white/30 mb-1 uppercase tracking-wide">{title}</p>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function SignalRow({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="flex items-center justify-between" title={hint}>
      <span className="text-white/40 text-[11px]">{label}</span>
      <span className="font-mono text-[11px] text-white/70">{value}</span>
    </div>
  );
}

function Slider({
  label, value, onChange, leftLabel, rightLabel,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <div>
      <div className="flex justify-between mb-2">
        <label className="text-sm font-medium text-white/70">{label}</label>
        <span className="text-sm text-indigo-400 font-mono">{value.toFixed(1)}x</span>
      </div>
      <input
        type="range" min="0.5" max="2.0" step="0.1"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-indigo-500"
      />
      <div className="flex justify-between text-xs text-white/25 mt-1">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
    </div>
  );
}
