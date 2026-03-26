"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { analyzeVideo, fetchPresets } from "@/lib/api";
import type { GamePreset, AnalysisResult } from "@/lib/types";

const PRESET_LABELS: Record<string, string> = {
  fps: "FPS",
  open_world: "Open World",
  rpg: "RPG",
  fighting: "Fighting",
  platformer: "Platformer",
};

export default function UploadPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preset, setPreset] = useState<GamePreset>("fps");
  const [deadSens, setDeadSens] = useState(1.0);
  const [hypeSens, setHypeSens] = useState(1.0);
  const [presetDescriptions, setPresetDescriptions] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPresets()
      .then((data) => {
        const descs: Record<string, string> = {};
        Object.entries(data).forEach(([k, v]) => {
          descs[k] = v.description;
        });
        setPresetDescriptions(descs);
      })
      .catch(() => {});
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped && dropped.type.startsWith("video/")) {
      setFile(dropped);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback(() => setDragActive(false), []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  };

  const handleAnalyze = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);

    // Store analysis config in sessionStorage for processing page
    sessionStorage.setItem("gc_file_name", file.name);
    sessionStorage.setItem("gc_file_size", String(file.size));
    sessionStorage.setItem("gc_preset", preset);

    try {
      // We navigate to /processing and pass the file via a hidden form trick:
      // Store the actual analysis promise result in sessionStorage as JSON
      const stages = [
        [10, "Uploading video..."],
        [20, "Extracting audio track..."],
        [40, "Detecting silence intervals..."],
        [55, "Analyzing video frames..."],
        [65, "Merging dead zone signals..."],
        [70, "Extracting commentary track..."],
        [80, "Computing hype scores..."],
        [90, "Building edit decision list..."],
        [95, "Finalizing export..."],
      ] as const;

      // Kick off analysis in background; use sessionStorage to communicate
      sessionStorage.setItem("gc_status", "analyzing");
      sessionStorage.setItem("gc_stage", "Starting analysis...");
      sessionStorage.setItem("gc_progress", "5");

      router.push("/processing");

      // Delay slightly so navigation happens before heavy work
      await new Promise((r) => setTimeout(r, 100));

      const result = await analyzeVideo(
        file,
        preset,
        deadSens,
        hypeSens,
      );

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
        <input
          ref={fileRef}
          type="file"
          accept="video/*"
          className="hidden"
          onChange={handleFileChange}
        />
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

      {/* Game preset */}
      <div className="mt-8">
        <label className="block text-sm font-medium text-white/70 mb-3">
          Game Preset
        </label>
        <div className="grid grid-cols-5 gap-2">
          {Object.entries(PRESET_LABELS).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setPreset(key as GamePreset)}
              className={`py-2 px-1 rounded-lg text-sm font-medium transition-all border
                ${preset === key
                  ? "bg-indigo-600 border-indigo-500 text-white"
                  : "bg-white/5 border-white/10 text-white/60 hover:border-white/30"
                }`}
            >
              {label}
            </button>
          ))}
        </div>
        {presetDescriptions[preset] && (
          <p className="text-xs text-white/35 mt-2 pl-1">
            {presetDescriptions[preset]}
          </p>
        )}
      </div>

      {/* Sensitivity sliders */}
      <div className="mt-8 space-y-5">
        <div>
          <div className="flex justify-between mb-2">
            <label className="text-sm font-medium text-white/70">
              Dead Zone Sensitivity
            </label>
            <span className="text-sm text-indigo-400 font-mono">
              {deadSens.toFixed(1)}x
            </span>
          </div>
          <input
            type="range"
            min="0.5" max="2.0" step="0.1"
            value={deadSens}
            onChange={(e) => setDeadSens(Number(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <div className="flex justify-between text-xs text-white/25 mt-1">
            <span>More sensitive</span>
            <span>Less sensitive</span>
          </div>
        </div>

        <div>
          <div className="flex justify-between mb-2">
            <label className="text-sm font-medium text-white/70">
              Hype Detection Sensitivity
            </label>
            <span className="text-sm text-indigo-400 font-mono">
              {hypeSens.toFixed(1)}x
            </span>
          </div>
          <input
            type="range"
            min="0.5" max="2.0" step="0.1"
            value={hypeSens}
            onChange={(e) => setHypeSens(Number(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <div className="flex justify-between text-xs text-white/25 mt-1">
            <span>More highlights</span>
            <span>Fewer highlights</span>
          </div>
        </div>
      </div>

      {error && (
        <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Analyze button */}
      <button
        onClick={handleAnalyze}
        disabled={!file || loading}
        className={`mt-8 w-full py-3 rounded-xl font-semibold text-base transition-all
          ${file && !loading
            ? "bg-indigo-600 hover:bg-indigo-500 text-white"
            : "bg-white/5 text-white/30 cursor-not-allowed"
          }`}
      >
        {loading ? "Analyzing..." : "Analyze Video"}
      </button>
    </div>
  );
}
