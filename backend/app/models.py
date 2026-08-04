from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


TranslationProfile = Literal[
    "natural_conversation", "clean_youtube", "faithful_review", "custom"
]


def _round_milliseconds(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value)
    if isinstance(value, str):
        try:
            return round(float(value))
        except ValueError:
            return value
    return value


class SubtitleStyle(BaseModel):
    font_family: str = Field(
        default="Pretendard", min_length=1, max_length=80
    )
    font_size: int = Field(default=48, ge=20, le=96)
    font_weight: Literal["normal", "bold"] = "bold"
    font_style: Literal["normal", "italic"] = "normal"
    text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    letter_spacing: float = Field(default=0, ge=-2, le=12)
    line_spacing: float = Field(default=1.2, ge=0.8, le=2.5)
    max_words_per_line: int = Field(default=8, ge=2, le=40)
    max_lines: int = Field(default=1, ge=1, le=4)
    alignment: Literal["left", "center", "right"] = "center"
    position: Literal["top", "middle", "bottom"] = "bottom"
    max_width_percent: int = Field(default=72, ge=40, le=96)
    margin_vertical: int = Field(default=54, ge=0, le=300)
    background_enabled: bool = True
    background_color: str = Field(
        default="#20211F", pattern=r"^#[0-9A-Fa-f]{6}$"
    )
    background_opacity: float = Field(default=0.88, ge=0, le=1)
    background_padding_x: int = Field(default=20, ge=0, le=80)
    background_padding_y: int = Field(default=10, ge=0, le=50)
    background_radius: int = Field(default=4, ge=0, le=30)
    outline_color: str = Field(
        default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$"
    )
    outline_size: float = Field(default=0, ge=0, le=8)
    shadow_size: float = Field(default=0, ge=0, le=8)


class SubtitleStylePatch(BaseModel):
    font_family: str | None = Field(default=None, min_length=1, max_length=80)
    font_size: int | None = Field(default=None, ge=20, le=96)
    font_weight: Literal["normal", "bold"] | None = None
    font_style: Literal["normal", "italic"] | None = None
    text_color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}$"
    )
    letter_spacing: float | None = Field(default=None, ge=-2, le=12)
    line_spacing: float | None = Field(default=None, ge=0.8, le=2.5)
    max_words_per_line: int | None = Field(default=None, ge=2, le=40)
    max_lines: int | None = Field(default=None, ge=1, le=4)
    alignment: Literal["left", "center", "right"] | None = None
    position: Literal["top", "middle", "bottom"] | None = None
    max_width_percent: int | None = Field(default=None, ge=40, le=96)
    margin_vertical: int | None = Field(default=None, ge=0, le=300)
    background_enabled: bool | None = None
    background_color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}$"
    )
    background_opacity: float | None = Field(default=None, ge=0, le=1)
    background_padding_x: int | None = Field(default=None, ge=0, le=80)
    background_padding_y: int | None = Field(default=None, ge=0, le=50)
    background_radius: int | None = Field(default=None, ge=0, le=30)
    outline_color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}$"
    )
    outline_size: float | None = Field(default=None, ge=0, le=8)
    shadow_size: float | None = Field(default=None, ge=0, le=8)


class SubtitleStylePresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    style: SubtitleStyle


class SubtitleStylePreset(SubtitleStylePresetCreate):
    preset_id: str = Field(
        default_factory=lambda: f"style_{uuid4().hex[:10]}"
    )


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4_000)
    speakers: list[str] = Field(default_factory=list)
    translation_profile: TranslationProfile = "natural_conversation"
    custom_instructions: str = Field(default="", max_length=4_000)
    expected_speaker_count: int | None = Field(
        default=None, ge=1, le=12
    )
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)


class Project(ProjectCreate):
    project_id: str
    source_language: str = "ko"
    target_language: str = "en"
    status: str = "draft"
    media_name: str | None = None
    media_hash: str | None = None
    media_url: str | None = None
    duration_ms: int = 0
    created_at: str
    updated_at: str

    @classmethod
    def create(cls, request: ProjectCreate) -> "Project":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            **request.model_dump(),
            project_id=f"prj_{uuid4().hex[:12]}",
            created_at=now,
            updated_at=now,
        )


class MediaTimelineInfo(BaseModel):
    frame_rate: float = Field(default=30, gt=0, le=240)
    waveform_url: str | None = None


class ProjectSpeakerSettings(BaseModel):
    expected_speaker_count: int | None = Field(
        default=None, ge=1, le=12
    )


WorkspaceSidebarTab = Literal[
    "stages", "timestamps", "speakers", "glossary", "style", "post_copy"
]


class ProjectWorkspaceState(BaseModel):
    active_clip_id: str | None = None
    selected_segment_id: str | None = None
    sidebar_tab: WorkspaceSidebarTab = "timestamps"
    playhead_ms: int = Field(default=0, ge=0)
    playback_rate: float = Field(default=1, ge=0.5, le=2)
    transcript_query: str = Field(default="", max_length=500)
    warning_only: bool = False
    video_resolution: Literal["1080p", "source"] = "1080p"
    video_quality: Literal["high", "maximum"] = "maximum"
    video_encoder: Literal["gpu", "cpu"] = "gpu"
    timeline_zoom: float = Field(default=1, ge=1, le=100_000)

    @field_validator("playhead_ms", mode="before")
    @classmethod
    def round_playhead_ms(cls, value):
        return _round_milliseconds(value)


class ProjectWorkspacePatch(BaseModel):
    active_clip_id: str | None = None
    selected_segment_id: str | None = None
    sidebar_tab: WorkspaceSidebarTab | None = None
    playhead_ms: int | None = Field(default=None, ge=0)
    playback_rate: float | None = Field(default=None, ge=0.5, le=2)
    transcript_query: str | None = Field(default=None, max_length=500)
    warning_only: bool | None = None
    video_resolution: Literal["1080p", "source"] | None = None
    video_quality: Literal["high", "maximum"] | None = None
    video_encoder: Literal["gpu", "cpu"] | None = None
    timeline_zoom: float | None = Field(default=None, ge=1, le=100_000)

    @field_validator("playhead_ms", mode="before")
    @classmethod
    def round_playhead_ms(cls, value):
        return _round_milliseconds(value)


class Word(BaseModel):
    text: str
    start_ms: int
    end_ms: int
    probability: float | None = None


class Segment(BaseModel):
    segment_id: str
    start_ms: int
    end_ms: int
    clip_id: str | None = None
    speaker_id: str | None = None
    raw_korean: str
    pass_1_korean: str = ""
    pass_2_korean: str = ""
    english: str = ""
    words: list[Word] = Field(default_factory=list)
    confidence: float = 0.0
    no_speech_probability: float = 0.0
    change_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "raw"
    locked: bool = False
    approved: bool = False


class SegmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str | None = None
    pass_2_korean: str | None = None
    english: str | None = None
    locked: bool | None = None
    approved: bool | None = None


class CaptionCue(BaseModel):
    cue_id: str
    start_ms: int
    end_ms: int
    lines: list[str]
    source_segment_ids: list[str] = Field(default_factory=list)
    clip_id: str | None = None
    speaker_id: str | None = None


class CaptionTrack(BaseModel):
    language: Literal["ko", "en"]
    max_words_per_line: int = Field(ge=2, le=40)
    max_lines: int = Field(ge=1, le=4)
    generated_at: str
    source_signature: str
    stale: bool = False
    cues: list[CaptionCue] = Field(default_factory=list)


class PostCopy(BaseModel):
    clip_id: str
    headline: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20_000)
    generated_at: str
    source_signature: str
    stale: bool = False


class PostCopyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)


class CaptionGenerationRequest(BaseModel):
    language: Literal["ko", "en"] = "en"
    max_words_per_line: int = Field(ge=2, le=40)
    max_lines: int = Field(ge=1, le=4)
    clip_id: str | None = None


class TimestampClip(BaseModel):
    clip_id: str
    navigation_marker_id: str | None = None
    start_ms: int
    end_ms: int
    title: str
    selected: bool = True
    opened: bool = False
    status: str = "media_ready"
    render_queued: bool = False
    subtitle_style: SubtitleStyle | None = None

    @field_validator("start_ms", "end_ms", mode="before")
    @classmethod
    def round_boundaries(cls, value):
        return _round_milliseconds(value)


class TimestampClipCreate(BaseModel):
    navigation_marker_id: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("start_ms", "end_ms", mode="before")
    @classmethod
    def round_boundaries(cls, value):
        return _round_milliseconds(value)


class TimestampClipPatch(BaseModel):
    selected: bool | None = None
    opened: bool | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    render_queued: bool | None = None

    @field_validator("start_ms", "end_ms", mode="before")
    @classmethod
    def round_boundaries(cls, value):
        return _round_milliseconds(value)


class NavigationMarker(BaseModel):
    marker_id: str
    timestamp_ms: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)


class Speaker(BaseModel):
    speaker_id: str
    name: str


class VoiceProfile(BaseModel):
    profile_id: str
    name: str
    sample_name: str
    duration_ms: int
    created_at: str


class VoiceProfileRecord(VoiceProfile):
    embedding: list[float]


class DetectedSpeakerTurn(BaseModel):
    turn_id: str
    start_ms: int
    end_ms: int
    speaker_id: str


class SpeakerPatch(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TimestampImport(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


class GlossaryEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: f"gls_{uuid4().hex[:10]}")
    source_variants: list[str]
    canonical_korean: str
    canonical_english: str
    category: str = "term"
    case_sensitive: bool = True
    notes: str = ""


class VideoExportOutput(BaseModel):
    clip_id: str
    title: str
    start_ms: int
    end_ms: int
    output_url: str
    output_name: str
    kind: Literal["video", "srt", "ass"] = "video"


class Job(BaseModel):
    job_id: str
    project_id: str
    clip_id: str | None = None
    stage: str
    progress: float = 0
    overall_progress: float | None = None
    processed_duration_ms: int = 0
    warning_count: int = 0
    error: str | None = None
    cancelled: bool = False
    paused: bool = False
    pipeline: bool = False
    pipeline_step: int = 0
    pipeline_total: int = 0
    pipeline_completed: bool = False
    encoder_name: str | None = None
    output_url: str | None = None
    output_name: str | None = None
    output_folder: str | None = None
    outputs: list[VideoExportOutput] = Field(default_factory=list)


class VideoExportRequest(BaseModel):
    resolution: Literal["1080p", "source"] = "1080p"
    quality: Literal["high", "maximum"] = "maximum"
    encoder: Literal["gpu", "cpu"] = "gpu"
    clip_ids: list[str] = Field(default_factory=list, max_length=200)
    include_video: bool = True
    include_srt: bool = False
    include_ass: bool = False


class VideoExportFolderUpdate(BaseModel):
    path: str | None = Field(default=None, max_length=2048)


class VideoExportFolderStatus(BaseModel):
    path: str
    default_path: str
    is_default: bool


class AppPreferences(BaseModel):
    app_font_scale: float = Field(default=1, ge=0.75, le=2)
    sidebar_width: int = Field(default=245, ge=245, le=10_000)
    last_project_id: str | None = Field(default=None, max_length=80)
    connection_dismissed: bool = False


class AppPreferencesPatch(BaseModel):
    app_font_scale: float | None = Field(default=None, ge=0.75, le=2)
    sidebar_width: int | None = Field(default=None, ge=245, le=10_000)
    last_project_id: str | None = Field(default=None, max_length=80)
    connection_dismissed: bool | None = None


class OpenRouterSettingsUpdate(BaseModel):
    api_key: SecretStr | None = None
    correction_model: str | None = Field(
        default=None, min_length=3, max_length=160
    )
    translation_model: str | None = Field(
        default=None, min_length=3, max_length=160
    )
    post_copy_model: str | None = Field(
        default=None, min_length=3, max_length=160
    )


class OpenRouterSettingsStatus(BaseModel):
    openrouter_configured: bool
    correction_model: str
    translation_model: str
    post_copy_model: str


class SpeakerDetectionSettingsUpdate(BaseModel):
    huggingface_token: SecretStr | None = None


class SpeakerDetectionSettingsStatus(BaseModel):
    configured: bool
    available: bool
    model: str


class OpenRouterModel(BaseModel):
    model_id: str
    name: str
    provider: str
    created: int
    context_length: int
    prompt_price: str
    completion_price: str
    request_price: str
