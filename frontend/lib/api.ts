import type { AnalysisResult, FeedbackPayload, FeedbackResponse } from "./types";

const API_BASE = "/api";

export async function analyzeVideo(
  file: File,
  gamePreset: string,
  deadZoneSensitivity: number,
  hypeSensitivity: number,
  onProgress?: (pct: number, label: string) => void,
): Promise<AnalysisResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("game_preset", gamePreset);
  form.append("dead_zone_sensitivity", String(deadZoneSensitivity));
  form.append("hype_sensitivity", String(hypeSensitivity));
  form.append("export_format", "all");

  // Simulate upload progress
  onProgress?.(5, "Uploading video...");

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
}

export async function fetchPresets(): Promise<Record<string, { name: string; description: string }>> {
  const res = await fetch(`${API_BASE}/presets`);
  if (!res.ok) throw new Error("Failed to load presets");
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
