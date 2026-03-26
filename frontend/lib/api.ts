import type {
  AnalysisResult,
  FeedbackPayload,
  FeedbackResponse,
  Highlight,
  PresetDetectionResult,
} from "./types";

const API_BASE = "/api";

export async function analyzeVideo(
  file: File,
  gamePreset: string,
  deadZoneSensitivity: number,
  hypeSensitivity: number,
  onProgress?: (pct: number, label: string) => void,
  jobId?: string,
): Promise<AnalysisResult> {
  const id = jobId ?? crypto.randomUUID();

  const form = new FormData();
  form.append("file", file);
  form.append("game_preset", gamePreset);
  form.append("dead_zone_sensitivity", String(deadZoneSensitivity));
  form.append("hype_sensitivity", String(hypeSensitivity));
  form.append("export_format", "all");
  form.append("job_id", id);

  onProgress?.(5, "Uploading video…");

  // Poll /status while the blocking fetch runs
  let pollHandle: ReturnType<typeof setInterval> | null = setInterval(async () => {
    try {
      const r = await fetch(`${API_BASE}/status/${id}`);
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
    const res = await fetch(`${API_BASE}/analyze`, {
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
  const res = await fetch(`${API_BASE}/presets`);
  if (!res.ok) throw new Error("Failed to load presets");
  return res.json();
}

export async function detectPreset(file: File): Promise<PresetDetectionResult> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/detect-preset`, {
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

  const res = await fetch(`${API_BASE}/feedback`, {
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
  file: File,
  highlights: Highlight[],
  onProgress?: (pct: number, label: string) => void,
): Promise<Blob> {
  const form = new FormData();
  form.append("file", file);
  form.append("highlights_json", JSON.stringify(highlights));

  onProgress?.(5, "Uploading video...");

  const res = await fetch(`${API_BASE}/assemble`, {
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
