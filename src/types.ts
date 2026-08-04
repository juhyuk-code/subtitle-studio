export type TranslationProfile =
  | "natural_conversation"
  | "clean_youtube"
  | "faithful_review"
  | "custom";

export interface SubtitleStyle {
  font_family: string;
  font_size: number;
  font_weight: "normal" | "bold";
  font_style: "normal" | "italic";
  text_color: string;
  letter_spacing: number;
  line_spacing: number;
  max_words_per_line: number;
  max_lines: number;
  alignment: "left" | "center" | "right";
  position: "top" | "middle" | "bottom";
  max_width_percent: number;
  margin_vertical: number;
  background_enabled: boolean;
  background_color: string;
  background_opacity: number;
  background_padding_x: number;
  background_padding_y: number;
  background_radius: number;
  outline_color: string;
  outline_size: number;
  shadow_size: number;
}

export interface SubtitleStylePreset {
  preset_id: string;
  name: string;
  style: SubtitleStyle;
}

export interface Project {
  project_id: string;
  name: string;
  description: string;
  speakers: string[];
  translation_profile: TranslationProfile;
  custom_instructions: string;
  expected_speaker_count: number | null;
  subtitle_style: SubtitleStyle;
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

export interface MediaTimelineInfo {
  frame_rate: number;
  waveform_url: string | null;
}

export type WorkspaceSidebarTab =
  | "stages"
  | "timestamps"
  | "speakers"
  | "glossary"
  | "style"
  | "post_copy";

export interface ProjectWorkspaceState {
  active_clip_id: string | null;
  selected_segment_id: string | null;
  sidebar_tab: WorkspaceSidebarTab;
  playhead_ms: number;
  playback_rate: number;
  transcript_query: string;
  warning_only: boolean;
  video_resolution: "1080p" | "source";
  video_quality: "high" | "maximum";
  video_encoder: "gpu" | "cpu";
  timeline_zoom: number;
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

export interface CaptionCue {
  cue_id: string;
  start_ms: number;
  end_ms: number;
  lines: string[];
  source_segment_ids: string[];
  clip_id: string | null;
  speaker_id: string | null;
}

export interface CaptionTrack {
  language: "ko" | "en";
  max_words_per_line: number;
  max_lines: number;
  generated_at: string;
  source_signature: string;
  stale: boolean;
  cues: CaptionCue[];
}

export interface PostCopy {
  clip_id: string;
  headline: string;
  body: string;
  generated_at: string;
  source_signature: string;
  stale: boolean;
}

export interface TimestampClip {
  clip_id: string;
  navigation_marker_id: string | null;
  start_ms: number;
  end_ms: number;
  title: string;
  selected: boolean;
  opened: boolean;
  status: string;
  render_queued: boolean;
  subtitle_style: SubtitleStyle | null;
}

export interface NavigationMarker {
  marker_id: string;
  timestamp_ms: number;
  title: string;
}

export interface Speaker {
  speaker_id: string;
  name: string;
}

export interface VoiceProfile {
  profile_id: string;
  name: string;
  sample_name: string;
  duration_ms: number;
  created_at: string;
}

export interface Job {
  job_id: string;
  project_id: string;
  clip_id: string | null;
  stage: string;
  progress: number;
  overall_progress: number | null;
  processed_duration_ms: number;
  warning_count: number;
  error: string | null;
  cancelled: boolean;
  paused: boolean;
  pipeline: boolean;
  pipeline_step: number;
  pipeline_total: number;
  pipeline_completed: boolean;
  encoder_name: string | null;
  output_url: string | null;
  output_name: string | null;
  output_folder: string | null;
  outputs: VideoExportOutput[];
}

export interface VideoExportOutput {
  clip_id: string;
  title: string;
  start_ms: number;
  end_ms: number;
  output_url: string;
  output_name: string;
  kind: "video" | "srt" | "ass";
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
  diarization: boolean;
  diarization_configured: boolean;
  diarization_model: string;
  llm_provider: "OpenRouter";
  openrouter_configured: boolean;
  correction_model: string;
  translation_model: string;
  post_copy_model: string;
  privacy: string;
}

export interface SpeakerDetectionSettings {
  configured: boolean;
  available: boolean;
  model: string;
}

export interface OpenRouterSettings {
  openrouter_configured: boolean;
  correction_model: string;
  translation_model: string;
  post_copy_model: string;
}

export interface VideoExportFolderSettings {
  path: string;
  default_path: string;
  is_default: boolean;
}

export interface AppPreferences {
  app_font_scale: number;
  sidebar_width: number;
  last_project_id: string | null;
  connection_dismissed: boolean;
}

export interface OpenRouterModel {
  model_id: string;
  name: string;
  provider: string;
  created: number;
  context_length: number;
  prompt_price: string;
  completion_price: string;
  request_price: string;
}
