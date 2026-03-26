export type GamePreset = "fps" | "open_world" | "rpg" | "fighting" | "platformer";

export type SegmentType = "dead_zone" | "highlight" | "keep" | "struggle_zone";

export interface Segment {
  start: number;
  end: number;
  duration: number;
  type: SegmentType;
  score: number | null;
}

export interface StruggleZone {
  start: number;
  end: number;
  duration: number;
  type: "struggle_zone";
  action: "MONTAGE_CANDIDATE";
  dead_zone_count: number;
}

export interface EdlSummary {
  total_duration: number;
  dead_zone_duration: number;
  highlight_duration: number;
  keep_duration: number;
  struggle_zone_duration: number;
  dead_zone_count: number;
  highlight_count: number;
  keep_count: number;
  struggle_zone_count: number;
  cut_savings_pct: number;
  highlight_pct: number;
}

export interface Edl {
  segments: Segment[];
  struggle_zones: StruggleZone[];
  summary: EdlSummary;
  source_name: string;
}

export interface DeadZone {
  start: number;
  end: number;
  duration: number;
  type: "dead_zone";
  confidence: number;
  segment_hash?: string;
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
  segment_hash?: string;
}

export type FeedbackVote = "up" | "down";

export interface FeedbackPayload {
  preset: string;
  segment_type: "dead_zone" | "highlight";
  vote: FeedbackVote;
  score: number | null;
  duration: number;
}

export interface FeedbackResponse {
  saved: boolean;
  correction: {
    hash: string;
    preset: string;
    segment_type: string;
    score_bucket: string;
    duration_bucket: string;
    votes_up: number;
    votes_down: number;
    last_updated: string;
  };
  message: string;
}

export interface ThresholdAdjustment {
  original: number;
  adjusted: number;
  delta: number;
}

export interface AnalysisResult {
  preset: GamePreset;
  preset_name: string;
  filename: string;
  duration: number;
  fps: number;
  dead_zones: DeadZone[];
  highlights: Highlight[];
  struggle_zones: StruggleZone[];
  edl: Edl;
  progress_log: string[];
  threshold_adjustments?: Record<string, ThresholdAdjustment>;
}

export interface AnalysisState {
  status: "idle" | "uploading" | "analyzing" | "done" | "error";
  progress: number;
  stage: string;
  result: AnalysisResult | null;
  error: string | null;
}
