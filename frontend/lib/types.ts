export type GamePreset = "fps" | "open_world" | "rpg" | "fighting" | "platformer";

export type SegmentType = "dead_zone" | "highlight" | "keep";

export interface Segment {
  start: number;
  end: number;
  duration: number;
  type: SegmentType;
  score: number | null;
}

export interface EdlSummary {
  total_duration: number;
  dead_zone_duration: number;
  highlight_duration: number;
  keep_duration: number;
  dead_zone_count: number;
  highlight_count: number;
  keep_count: number;
  cut_savings_pct: number;
  highlight_pct: number;
}

export interface Edl {
  segments: Segment[];
  summary: EdlSummary;
  source_name: string;
}

export interface DeadZone {
  start: number;
  end: number;
  duration: number;
  type: "dead_zone";
  confidence: number;
}

export interface Highlight {
  start: number;
  end: number;
  duration: number;
  type: "highlight";
  composite_score: number;
  volume_score: number;
  pitch_score: number;
  speech_rate_score: number;
}

export interface AnalysisResult {
  preset: GamePreset;
  preset_name: string;
  filename: string;
  duration: number;
  fps: number;
  dead_zones: DeadZone[];
  highlights: Highlight[];
  edl: Edl;
  progress_log: string[];
}

export interface AnalysisState {
  status: "idle" | "uploading" | "analyzing" | "done" | "error";
  progress: number;
  stage: string;
  result: AnalysisResult | null;
  error: string | null;
}
