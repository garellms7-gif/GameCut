import type {
  AnalysisResult,
  FeedbackPayload,
  FeedbackResponse,
  Highlight,
  PresetDetectionResult,
} from "./types";

// In a Tauri desktop build the Next.js proxy rewrites are not available —
// call the FastAPI backend directly.  In a browser context keep the /api proxy.
function apiBase(): string {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    return "http://localhost:8000";
  }
  return "/api";
}

/** Append either a File (browser upload) or a local path string (Tauri). */
function appendFile(form: FormData, fileOrPath: File | string): void {
  if (typeof fileOrPath === "string") {
    form.append("file_path", fileOrPath);
  } else {
    form.append("file", fileOrPath);
  }
}

export async function analyzeVideo(
  fileOrPath: File | string,
  gamePreset: string,
  deadZoneSensitivity: number,
  hypeSensitivity: number,
  onProgress?: (pct: number, label: string) => void,
  jobId?: string,
  companionTimestamps?: string,
): Promise<AnalysisResult> {
  const id = jobId ?? crypto.randomUUID();
  const base = apiBase();

  const form = new FormData();
  appendFile(form, fileOrPath);
  form.append("game_preset", gamePreset);
  form.append("dead_zone_sensitivity", String(deadZoneSensitivity));
  form.append("hype_sensitivity", String(hypeSensitivity));
  form.append("export_format", "all");
  form.append("job_id", id);
  if (companionTimestamps) {
    form.append("companion_timestamps", companionTimestamps);
  }

  onProgress?.(5, "Uploading video…");

  // Poll /status while the blocking fetch runs
  let pollHandle: ReturnType<typeof setInterval> | null = setInterval(async () => {
    try {
      const r = await fetch(`${base}/status/${id}`);
      if (!r.ok) return;
      const s = await r.json();
      const completed: number = s.completed_chunks ?? 0;
      const total: number = s.total_chunks ?? 1;
      const pct = Math.round(10 + (completed / total) * 85);
      onProgress?.(pct, s.current_label ?? `Analyzing chunk ${completed + 1} of ${total}…`);
    } catch {
      // ignore transient poll errors
    }
  }, 1000);

  try {
    const res = await fetch(`${base}/analyze`, {
      method: "POST",
      body: form,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Analysis failed");
    }

    onProgress?.(100, "Complete");
    return res.json();
  } finally {
    if (pollHandle !== null) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }
}

export async function fetchPresets(): Promise<Record<string, { name: string; description: string }>> {
  const res = await fetch(`${apiBase()}/presets`);
  if (!res.ok) throw new Error("Failed to load presets");
  return res.json();
}

export async function detectPreset(fileOrPath: File | string): Promise<PresetDetectionResult> {
  const form = new FormData();
  appendFile(form, fileOrPath);

  const res = await fetch(`${apiBase()}/detect-preset`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Preset detection failed");
  }

  return res.json();
}

export async function submitFeedback(
  payload: FeedbackPayload,
): Promise<FeedbackResponse> {
  const form = new FormData();
  form.append("preset", payload.preset);
  form.append("segment_type", payload.segment_type);
  form.append("vote", payload.vote);
  form.append("duration", String(payload.duration));
  if (payload.score !== null) {
    form.append("score", String(payload.score));
  }

  const res = await fetch(`${apiBase()}/feedback`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Feedback submission failed");
  }

  return res.json();
}

export async function assembleHighlights(
  fileOrPath: File | string,
  highlights: Highlight[],
  onProgress?: (pct: number, label: string) => void,
): Promise<Blob> {
  const form = new FormData();
  appendFile(form, fileOrPath);
  form.append("highlights_json", JSON.stringify(highlights));

  onProgress?.(5, "Uploading video...");

  const res = await fetch(`${apiBase()}/assemble`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Assembly failed");
  }

  onProgress?.(100, "Complete");
  return res.blob();
}

export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.round((seconds % 1) * 1000);

  const parts = [];
  if (h > 0) parts.push(String(h).padStart(2, "0"));
  parts.push(String(m).padStart(2, "0"));
  parts.push(String(s).padStart(2, "0"));
  return `${parts.join(":")}:${String(ms).padStart(3, "0")}`;
}
