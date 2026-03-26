"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";

export default function ProcessingPage() {
  const router = useRouter();
  const [progress, setProgress] = useState(5);
  const [stage, setStage] = useState("Uploading video…");
  const [chunkInfo, setChunkInfo] = useState<{ completed: number; total: number } | null>(null);
  const [fileName, setFileName] = useState("");
  const [preset, setPreset] = useState("");
  const statusPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const donePollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setFileName(sessionStorage.getItem("gc_file_name") || "video.mp4");
    setPreset(sessionStorage.getItem("gc_preset") || "fps");
  }, []);

  // Poll the /status/{jobId} endpoint for real chunk progress
  useEffect(() => {
    const jobId = sessionStorage.getItem("gc_job_id");
    if (!jobId) return;

    statusPollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/status/${jobId}`);
        if (!r.ok) return;
        const s = await r.json();
        const completed: number = s.completed_chunks ?? 0;
        const total: number = s.total_chunks ?? 1;
        setChunkInfo({ completed, total });
        const pct = Math.round(10 + (completed / total) * 85);
        setProgress(pct);
        setStage(s.current_label ?? `Analyzing chunk ${completed + 1} of ${total}…`);
      } catch {
        // ignore transient errors
      }
    }, 1000);

    return () => {
      if (statusPollRef.current) clearInterval(statusPollRef.current);
    };
  }, []);

  // Poll sessionStorage for completion signal from the upload page
  useEffect(() => {
    donePollRef.current = setInterval(() => {
      const status = sessionStorage.getItem("gc_status");
      if (status === "done") {
        if (statusPollRef.current) clearInterval(statusPollRef.current);
        clearInterval(donePollRef.current!);
        setProgress(100);
        setStage("Analysis complete!");
        setTimeout(() => router.push("/results"), 600);
      } else if (status === "error") {
        if (statusPollRef.current) clearInterval(statusPollRef.current);
        clearInterval(donePollRef.current!);
        const errMsg = sessionStorage.getItem("gc_error") || "Analysis failed";
        setStage(`Error: ${errMsg}`);
        setProgress(0);
      }
    }, 300);

    return () => {
      if (donePollRef.current) clearInterval(donePollRef.current);
    };
  }, [router]);

  const isError = stage.startsWith("Error:");

  return (
    <div className="max-w-xl mx-auto px-4 py-20 flex flex-col items-center text-center">
      {/* Animated icon */}
      <div className="relative w-20 h-20 mb-8">
        <div
          className={`absolute inset-0 rounded-full border-4 border-indigo-600/20 ${!isError ? "animate-spin" : ""}`}
          style={{ borderTopColor: isError ? "transparent" : "#6366f1" }}
        />
        <div className="absolute inset-0 flex items-center justify-center text-3xl">
          {isError ? "❌" : progress === 100 ? "✅" : "🎮"}
        </div>
      </div>

      <h1 className="text-2xl font-bold mb-2">
        {isError ? "Analysis Failed" : progress === 100 ? "Done!" : "Analyzing..."}
      </h1>

      {fileName && !isError && (
        <p className="text-white/40 text-sm mb-8">
          {fileName}
          {preset && (
            <span className="ml-2 px-2 py-0.5 rounded bg-indigo-600/20 text-indigo-400 text-xs uppercase">
              {preset}
            </span>
          )}
        </p>
      )}

      {/* Progress bar */}
      <div className="w-full bg-white/10 rounded-full h-2.5 mb-4 overflow-hidden">
        <div
          className={`h-2.5 rounded-full transition-all duration-700 ease-out
            ${isError ? "bg-red-500" : "bg-indigo-500"}`}
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Stage label */}
      <p className={`text-sm font-medium ${isError ? "text-red-400" : "text-white/70"}`}>
        {stage}
      </p>

      <p className="text-white/25 text-xs mt-2">
        {isError ? "" : `${progress}% complete`}
      </p>

      {/* Chunk progress dots */}
      {!isError && chunkInfo && chunkInfo.total > 1 && (
        <div className="mt-10 w-full">
          <p className="text-xs text-white/30 uppercase tracking-wide mb-3 text-left">
            Chunks
          </p>
          <div className="flex flex-wrap gap-2">
            {Array.from({ length: chunkInfo.total }, (_, i) => (
              <div
                key={i}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono transition-all
                  ${i < chunkInfo.completed
                    ? "bg-green-500/20 text-green-400 border border-green-500/30"
                    : i === chunkInfo.completed
                    ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 animate-pulse"
                    : "bg-white/5 text-white/20 border border-white/10"
                  }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full
                    ${i < chunkInfo.completed ? "bg-green-500" : i === chunkInfo.completed ? "bg-indigo-400" : "bg-white/20"}`}
                />
                {i < chunkInfo.completed ? "✓" : `#${i + 1}`}
              </div>
            ))}
          </div>
        </div>
      )}

      {isError && (
        <button
          onClick={() => {
            sessionStorage.removeItem("gc_status");
            router.push("/");
          }}
          className="mt-8 px-6 py-2 bg-white/10 hover:bg-white/15 rounded-lg text-sm transition-all"
        >
          Back to upload
        </button>
      )}
    </div>
  );
}
