"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";

const STAGES = [
  { pct: 5, label: "Uploading video..." },
  { pct: 15, label: "Extracting audio track..." },
  { pct: 30, label: "Detecting silence intervals..." },
  { pct: 50, label: "Analyzing video frames for static content..." },
  { pct: 65, label: "Merging dead zone signals..." },
  { pct: 72, label: "Extracting commentary audio..." },
  { pct: 80, label: "Computing volume & pitch hype scores..." },
  { pct: 88, label: "Analyzing speech rate..." },
  { pct: 94, label: "Building edit decision list..." },
  { pct: 98, label: "Finalizing exports..." },
];

export default function ProcessingPage() {
  const router = useRouter();
  const [progress, setProgress] = useState(5);
  const [stage, setStage] = useState("Uploading video...");
  const [stageIdx, setStageIdx] = useState(0);
  const [fileName, setFileName] = useState("");
  const [preset, setPreset] = useState("");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setFileName(sessionStorage.getItem("gc_file_name") || "video.mp4");
    setPreset(sessionStorage.getItem("gc_preset") || "fps");
  }, []);

  // Animate through stages at a realistic pace
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setStageIdx((prev) => {
        const next = prev + 1;
        if (next < STAGES.length) {
          setProgress(STAGES[next].pct);
          setStage(STAGES[next].label);
          return next;
        }
        return prev;
      });
    }, 2200);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  // Poll sessionStorage for completion signal from the upload page
  useEffect(() => {
    pollRef.current = setInterval(() => {
      const status = sessionStorage.getItem("gc_status");
      if (status === "done") {
        if (intervalRef.current) clearInterval(intervalRef.current);
        clearInterval(pollRef.current!);
        setProgress(100);
        setStage("Analysis complete!");
        setTimeout(() => router.push("/results"), 600);
      } else if (status === "error") {
        if (intervalRef.current) clearInterval(intervalRef.current);
        clearInterval(pollRef.current!);
        const errMsg = sessionStorage.getItem("gc_error") || "Analysis failed";
        setStage(`Error: ${errMsg}`);
        setProgress(0);
      }
    }, 300);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
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

      {/* Stage list */}
      {!isError && (
        <div className="mt-10 w-full text-left space-y-2">
          {STAGES.map((s, i) => (
            <div key={i} className="flex items-center gap-3 text-sm">
              <div
                className={`w-2 h-2 rounded-full flex-shrink-0 transition-all
                  ${i < stageIdx ? "bg-green-500" : i === stageIdx ? "bg-indigo-400 animate-pulse" : "bg-white/10"}`}
              />
              <span
                className={
                  i < stageIdx
                    ? "text-green-400/70 line-through"
                    : i === stageIdx
                    ? "text-white"
                    : "text-white/25"
                }
              >
                {s.label}
              </span>
            </div>
          ))}
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
