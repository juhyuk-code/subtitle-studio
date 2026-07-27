from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, SecretStr


TranslationProfile = Literal[
    "natural_conversation", "clean_youtube", "faithful_review", "custom"
]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4_000)
    speakers: list[str] = Field(default_factory=list)
    translation_profile: TranslationProfile = "natural_conversation"
    custom_instructions: str = Field(default="", max_length=4_000)


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
    speaker_id: str | None = None
    pass_2_korean: str | None = None
    english: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    locked: bool | None = None
    approved: bool | None = None


class TimestampClip(BaseModel):
    clip_id: str
    start_ms: int
    end_ms: int
    title: str
    selected: bool = True


class TimestampClipPatch(BaseModel):
    selected: bool


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


class Job(BaseModel):
    job_id: str
    project_id: str
    stage: str
    progress: float = 0
    processed_duration_ms: int = 0
    warning_count: int = 0
    error: str | None = None
    cancelled: bool = False


class OpenRouterSettingsUpdate(BaseModel):
    api_key: SecretStr | None = None
    correction_model: str = Field(
        default="google/gemini-3.1-flash-lite", min_length=3, max_length=160
    )
    translation_model: str = Field(
        default="anthropic/claude-sonnet-4.6", min_length=3, max_length=160
    )


class OpenRouterSettingsStatus(BaseModel):
    openrouter_configured: bool
    correction_model: str
    translation_model: str
