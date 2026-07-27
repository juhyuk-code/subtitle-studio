export type TranslationProfile =
  | "natural_conversation"
  | "clean_youtube"
  | "faithful_review"
  | "custom";

export interface Project {
  project_id: string;
  name: string;
  description: string;
  speakers: string[];
  translation_profile: TranslationProfile;
  custom_instructions: string;
  source_language: "ko";
  target_language: "en";
  status: string;
  media_name: string | null;
  media_hash: string | null;
  media_url: string | null;
  duration_ms: number;
  created_at: string;
  updated_at: string;
}

export interface Segment {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  clip_id: string | null;
  speaker_id: string | null;
  raw_korean: string;
  pass_1_korean: string;
  pass_2_korean: string;
  english: string;
  confidence: number;
  no_speech_probability: number;
  change_reasons: string[];
  warnings: string[];
  status: string;
  locked: boolean;
  approved: boolean;
}

export interface TimestampClip {
  clip_id: string;
  start_ms: number;
  end_ms: number;
  title: string;
  selected: boolean;
}

export interface Job {
  job_id: string;
  project_id: string;
  stage: string;
  progress: number;
  processed_duration_ms: number;
  warning_count: number;
  error: string | null;
  cancelled: boolean;
}

export interface GlossaryEntry {
  entry_id: string;
  source_variants: string[];
  canonical_korean: string;
  canonical_english: string;
  category: string;
  case_sensitive: boolean;
  notes: string;
}

export interface RuntimeStatus {
  ffmpeg: boolean;
  whisper: boolean;
  llm_provider: "OpenRouter";
  openrouter_configured: boolean;
  correction_model: string;
  translation_model: string;
  privacy: string;
}

export interface OpenRouterSettings {
  openrouter_configured: boolean;
  correction_model: string;
  translation_model: string;
}
