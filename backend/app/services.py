import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile

from .clips import selected_clip_ranges
from .desktop_paths import (
    bundle_root,
    bundled_binary,
    hidden_subprocess_kwargs,
    model_cache_root,
)
from .models import (
    CaptionCue,
    CaptionTrack,
    DetectedSpeakerTurn,
    GlossaryEntry,
    Job,
    OpenRouterModel,
    PostCopy,
    Project,
    Segment,
    SubtitleStyle,
    Speaker,
    TimestampClip,
    VideoExportOutput,
    VoiceProfileRecord,
    Word,
)
from .store import Store

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".aac"}
DEFAULT_WHISPER_MODEL = "large-v3"
COMMUNITY_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
INTEL_MAC_DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"


def default_diarization_model(
    system_name: str | None = None,
    machine: str | None = None,
) -> str:
    configured = os.environ.get("DIARIZATION_MODEL", "").strip()
    if configured:
        return configured
    current_system = system_name or sys.platform
    current_machine = machine or platform.machine()
    if current_system == "darwin" and current_machine == "x86_64":
        return INTEL_MAC_DIARIZATION_MODEL
    return COMMUNITY_DIARIZATION_MODEL


DEFAULT_DIARIZATION_MODEL = default_diarization_model()
DEFAULT_CORRECTION_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_TRANSLATION_MODEL = "anthropic/claude-sonnet-4.6"

logger = logging.getLogger(__name__)

WORKFLOW_RANK = {
    "draft": 0,
    "media_ready": 1,
    "speakers_detected": 2,
    "transcribed": 3,
    "corrected_pass_1": 4,
    "corrected": 5,
    "translated": 6,
}

CORRECTION_PROMPT = """You are correcting Korean automatic speech recognition output.
Recover what was actually spoken; never rewrite, summarize, sanitize, or translate.
Preserve casual speech, slang, profanity, repetition, and unfinished sentences.
Use the glossary, speaker labels, and nearby dialogue. Do not add facts. Keep uncertain text and flag it.
Never move words between speakers or infer a person's identity from a generic speaker label.
Return only JSON: {"corrected_segments":[{"segment_id":"...","corrected_korean":"...",
"change_reason":["spacing"],"confidence":0.9,"uncertain_phrases":[]}]}"""

CONSISTENCY_PROMPT = """Review this Korean podcast transcript episode-wide.
Standardize names and terminology using the glossary and later context without rewriting speech.
Never change locked segments. Return only JSON: {"corrected_segments":[{"segment_id":"...",
"corrected_korean":"...","change_reason":["terminology"],"confidence":0.9,
"uncertain_phrases":[]}]}"""

TRANSLATION_PROMPT = """Translate corrected Korean podcast dialogue into natural conversational English.
Preserve meaning, intention, emotion, sarcasm, uncertainty, interruptions, terminology, and profanity.
Use speaker labels to preserve each voice and turn-taking. Never invent a speaker identity.
Use contractions naturally. Do not add explanations or create subtitle line breaks.
Return only JSON: {"translations":[{"segment_id":"...","english":"...","warnings":[]}]}"""

POST_COPY_PROMPT = """# Project: Twitter/X Clip Captions

You are helping create short Twitter/X posts from interview and podcast transcripts.

## Default objective

Identify the clip's central argument and turn it into a concise, provocative post that makes people want to watch the video.

The post should emphasize the clip's most important idea—not merely the most sensational sentence.

## Speaker attribution (important)

Never name or identify a speaker. The audio cannot reliably distinguish who is speaking, so any name you assign is likely wrong. Do not open with a name, do not use "Alex argues that...", and do not label quotes with a speaker. Write the argument itself, with no attribution.

## Default output format

Always use this structure:

One sentence explaining the clip's central argument (no speaker name).

"Supporting quote or paragraph."

"Supporting quote or paragraph."

"Strong concluding quote or paragraph."

Do not add a title, introduction, explanation, hashtags, emojis, timestamps, or commentary unless requested.

## Opening sentence

Start the post with one crisp opening sentence that summarizes the point of the clip — the single takeaway a viewer should walk away with. No speaker name or attribution.

The opening sentence must:

* State the clip's point (the takeaway), not merely describe the topic.
* Stand alone: a reader who sees only this sentence should get the gist.
* Be assertive and interesting without misrepresenting what was said.
* Stay short — ideally under 20 words, never over 30.

Example:

Open-source AI is essential to keeping research accessible and preventing a handful of labs from controlling the field.

## Quote selection: choose the right length

After the opening statement, include 3–7 quotes that build the argument. Judge each clip and decide whether short punchy quotes or longer paragraph quotes serve it better. Do not default to one length.

Use short single-sentence quotes (roughly under 12 words each) when the clip is meant to be provocative: a bold claim, a sharp rebuke, a hot take. Short quotes land harder and read as confident.

Use longer paragraph quotes (multiple sentences, preserving the speaker's full reasoning) when the clip needs nuance: a careful explanation, a tradeoff, a chain of reasoning, or any argument that falls apart when cut to a fragment. Give these quotes enough context to stand on their own.

Whichever length you choose, every quote must:

* Follow the logical progression of the original argument.
* Preserve the speaker's meaning, tone, and level of certainty.
* Prioritize concrete, provocative language over generic statements.
* Work independently as a readable excerpt.

Arrange the quotes so they create a narrative:

1. Establish what would be lost or what is at stake.
2. Explain the practical consequence or the reasoning behind it.
3. Identify the danger, tension, or concentration of power.
4. End with the strongest conclusion.

## Transcript fidelity

Stay loyal to the original transcript.

Light editing is allowed to:

* Remove filler words and repetition.
* Correct obvious transcription errors.
* Shorten a sentence without changing its meaning.
* Replace unclear pronouns with the subject being discussed.
* Make spoken grammar readable.

Do not:

* Invent arguments the speaker did not make.
* Turn an implication into a direct claim.
* Make the speaker sound more certain than they were.
* Combine unrelated statements into a fabricated quote.
* Add fashionable language such as "accountability," "democratization," or "counterweight" unless the speaker expressed that idea.
* Present a loose paraphrase inside quotation marks.

If a line cannot remain faithful while being shortened, exclude it or keep it at full length rather than distort it.

## Style

The writing should feel:

* Intelligent
* Direct
* Provocative
* Minimal
* Human
* Native to Twitter/X

Avoid corporate language, vague summaries, exaggerated clickbait, repetitive quotes, and any speaker naming.

## Punctuation

* Never use em dashes (—). Not in the headline, not in the body, not anywhere. Replace any em dash with a comma, period, colon, or parentheses. This is a hard rule with no exceptions.
* Do not use en dashes (–) either.

## Topic emphasis

Determine the actual subject of the clip before choosing quotes. If the clip is primarily about open-source models, the post must explain why open-source models matter. Do not let a provocative side comment overshadow the central argument.

## Final quality check

Before answering, verify:

* Does the opening sentence crisply summarize the clip's point?
* Is there no speaker name or attribution anywhere in the post?
* Can every quoted line be traced to something actually said in the transcript?
* Did shortening preserve the original meaning, and does the chosen quote length fit the clip's nature?
* Do the quotes collectively explain why the argument matters?
* Is the strongest quote placed near the end?
* Are there no em dashes (—) or en dashes (–) anywhere in the headline or body?
* Can the post be understood without additional context?

## Output format (required by the app)

Return exactly one JSON object, nothing else:

- "headline": the opening sentence (a crisp summary of the clip's point, no speaker name).
- "body": the 3–7 supporting quotes (short or paragraph length as appropriate), each wrapped in double quotes ("like this"), each on its own line, separated by blank lines, in narrative order.

Return only JSON: {"headline":"...","body":"..."}"""


def configured_value(
    key: str, store: Store | None = None, default: str = ""
) -> str:
    environment_value = os.environ.get(key, "").strip()
    if environment_value:
        return environment_value
    if store:
        stored_value = store.get_setting(key)
        if stored_value:
            return stored_value.strip()
    return default


def openrouter_headers(store: Store | None = None) -> dict[str, str]:
    api_key = configured_value("OPENROUTER_API_KEY", store)
    if not api_key:
        raise RuntimeError(
            "OpenRouter is not connected. Add your API key in app settings."
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.environ.get(
            "OPENROUTER_APP_URL", "http://localhost:3000"
        ),
        "X-OpenRouter-Title": "Subtitle Studio",
    }


def openrouter_model_for_stage(stage: str, store: Store | None = None) -> str:
    legacy_default = configured_value("OPENROUTER_MODEL", store)
    if stage == "post_captioning":
        translation_model = configured_value(
            "OPENROUTER_TRANSLATION_MODEL",
            store,
            legacy_default or DEFAULT_TRANSLATION_MODEL,
        )
        return configured_value(
            "OPENROUTER_POST_COPY_MODEL", store, translation_model
        )
    default_model = legacy_default or (
        DEFAULT_TRANSLATION_MODEL
        if stage == "translating"
        else DEFAULT_CORRECTION_MODEL
    )
    variable = (
        "OPENROUTER_TRANSLATION_MODEL"
        if stage == "translating"
        else "OPENROUTER_CORRECTION_MODEL"
    )
    return configured_value(variable, store, default_model)


def _normalize_openrouter_models(payload: Any) -> list[OpenRouterModel]:
    models = []
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if not model_id or "/" not in model_id:
            continue
        architecture = item.get("architecture") or {}
        output_modalities = architecture.get("output_modalities") or []
        if output_modalities and "text" not in output_modalities:
            continue
        pricing = item.get("pricing") or {}
        models.append(
            OpenRouterModel(
                model_id=model_id,
                name=str(item.get("name") or model_id.split("/", 1)[1]),
                provider=model_id.split("/", 1)[0],
                created=max(0, int(item.get("created") or 0)),
                context_length=max(0, int(item.get("context_length") or 0)),
                prompt_price=str(pricing.get("prompt") or ""),
                completion_price=str(pricing.get("completion") or ""),
                request_price=str(pricing.get("request") or ""),
            )
        )
    return sorted(models, key=lambda model: (-model.created, model.name.lower()))


async def fetch_openrouter_models(
    store: Store | None = None,
) -> list[OpenRouterModel]:
    base_url = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    ).rstrip("/")
    api_key = configured_value("OPENROUTER_API_KEY", store)
    headers = {
        "HTTP-Referer": os.environ.get(
            "OPENROUTER_APP_URL", "http://localhost:3000"
        ),
        "X-OpenRouter-Title": "Subtitle Studio",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    paths = ["/models/user", "/models"] if api_key else ["/models"]
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=20) as client:
        for path in paths:
            try:
                response = await client.get(
                    f"{base_url}{path}",
                    headers=headers,
                    params={"sort": "newest"},
                )
                response.raise_for_status()
                models = _normalize_openrouter_models(response.json())
                if models:
                    return models
            except (httpx.HTTPError, ValueError, TypeError) as error:
                last_error = error

    raise HTTPException(
        502,
        "OpenRouter's model list is unavailable. Check the connection and try again.",
    ) from last_error


def whisper_command() -> list[str]:
    if importlib.util.find_spec("whisper") is not None:
        return [sys.executable, "-m", "whisper"]
    executable = shutil.which("whisper")
    if executable:
        return [executable]
    raise RuntimeError(
        "Whisper is not installed. Install openai-whisper in the project environment."
    )


def whisper_available() -> bool:
    if importlib.util.find_spec("faster_whisper") is not None:
        return True
    try:
        whisper_command()
        return True
    except RuntimeError:
        return False


def diarization_available() -> bool:
    return importlib.util.find_spec("pyannote.audio") is not None


@dataclass(frozen=True)
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker_id: str


@dataclass(frozen=True)
class DiarizationResult:
    turns: list[SpeakerTurn]
    embeddings: dict[str, list[float]]


@dataclass(frozen=True)
class ClipAudioMapping:
    combined_start_ms: int
    combined_end_ms: int
    source_start_ms: int


_DIARIZATION_PIPELINES: dict[str, Any] = {}
_DIARIZATION_MODEL_LOCK = threading.RLock()
_WHISPER_MODELS: dict[tuple[str, str, str], Any] = {}
_WHISPER_MODEL_LOCK = threading.RLock()
_WAVEFORM_RENDER_LOCK = threading.Lock()
_CUDA_DLL_DIRECTORIES: list[Any] = []


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    stem = re.sub(r"[^A-Za-z0-9가-힣._ -]+", "_", Path(name).stem).strip(" ._")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported media format: {suffix or 'unknown'}")
    return f"{stem[:100] or 'source'}{suffix}"


async def save_upload(upload: UploadFile, target: Path) -> str:
    digest = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    return digest.hexdigest()


def media_duration_ms(path: Path) -> int:
    try:
        result = subprocess.run(
            [
                bundled_binary("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            **hidden_subprocess_kwargs(),
        )
        return round(float(result.stdout.strip()) * 1000)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise HTTPException(422, "The media file is corrupted or unreadable.") from exc


def media_frame_rate(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                bundled_binary("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            **hidden_subprocess_kwargs(),
        )
        streams = json.loads(result.stdout).get("streams", [])
        stream = streams[0] if streams else {}
        for key in ("avg_frame_rate", "r_frame_rate"):
            value = str(stream.get(key) or "")
            if "/" in value:
                numerator, denominator = value.split("/", 1)
                rate = float(numerator) / float(denominator)
            elif value:
                rate = float(value)
            else:
                continue
            if 0 < rate <= 240:
                return rate
    except (
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        ZeroDivisionError,
    ):
        pass
    return 30.0


def render_waveform_image(
    audio_path: Path,
    target: Path,
    start_ms: int,
    end_ms: int,
    width: int,
    height: int,
) -> None:
    with _WAVEFORM_RENDER_LOCK:
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(
            f".{target.stem}-{uuid4().hex}.tmp.png"
        )
        duration_seconds = max(0.001, (end_ms - start_ms) / 1000)
        try:
            result = subprocess.run(
                [
                    bundled_binary("ffmpeg"),
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{start_ms / 1000:.6f}",
                    "-t",
                    f"{duration_seconds:.6f}",
                    "-i",
                    str(audio_path),
                    "-filter_complex",
                    (
                        "aformat=channel_layouts=mono,"
                        f"showwavespic=s={width}x{height}:"
                        "colors=white:draw=full:scale=cbrt"
                    ),
                    "-frames:v",
                    "1",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode != 0 or not temporary.is_file():
                detail = (
                    result.stderr.strip()
                    or "FFmpeg did not create a waveform."
                )
                raise RuntimeError(detail)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def normalize_audio(source: Path, output: Path) -> None:
    ffmpeg = bundled_binary("ffmpeg")
    if ffmpeg == "ffmpeg" and not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed or is not on PATH.")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        **hidden_subprocess_kwargs(),
    )


def prepare_transcription_clip(
    source: Path,
    output: Path,
    start_ms: int,
    end_ms: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source), "rb") as reader:
        channels = reader.getnchannels()
        sample_rate = reader.getframerate()
        sample_width = reader.getsampwidth()
        if channels != 1 or sample_width != 2 or sample_rate != 16_000:
            raise RuntimeError(
                "Transcription audio must be mono 16-bit PCM at 16 kHz."
            )
        total_frames = reader.getnframes()
        start_frame = min(
            total_frames,
            max(0, round(start_ms * sample_rate / 1000)),
        )
        end_frame = min(
            total_frames,
            max(start_frame, round(end_ms * sample_rate / 1000)),
        )
        if end_frame <= start_frame:
            raise RuntimeError("The selected clip does not contain any audio.")
        reader.setpos(start_frame)
        frames = reader.readframes(end_frame - start_frame)

    with wave.open(str(output), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(frames)


def new_job(project_id: str, stage: str) -> Job:
    return Job(job_id=f"job_{uuid4().hex[:12]}", project_id=project_id, stage=stage)


class JobCancelled(Exception):
    pass


def _checkpoint_job(store: Store, job: Job) -> None:
    while True:
        with store.lock:
            current_data = store.get("job", job.job_id)
            if not current_data:
                raise JobCancelled
            current = Job.model_validate(current_data)
            if current.cancelled:
                raise JobCancelled
            if not current.paused:
                job.paused = False
                if (
                    job.pipeline
                    and not job.pipeline_completed
                    and job.pipeline_total > 0
                ):
                    job.overall_progress = min(
                        1,
                        max(
                            0,
                            (
                                max(0, job.pipeline_step - 1)
                                + min(1, max(0, job.progress))
                            )
                            / job.pipeline_total,
                        ),
                    )
                store.save_job(job)
                return
        time.sleep(0.25)


def _processed_audio_ms(
    timeline_ms: int,
    clips: list[TimestampClip] | None,
    total_duration_ms: int,
) -> int:
    if not clips:
        return min(max(0, timeline_ms), total_duration_ms)
    return sum(
        max(0, min(timeline_ms, clip.end_ms) - clip.start_ms)
        for clip in clips
        if timeline_ms > clip.start_ms
    )


def prepare_diarization_audio(source: Path, output: Path) -> None:
    ffmpeg = bundled_binary("ffmpeg")
    if ffmpeg == "ffmpeg" and not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed or is not on PATH.")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        **hidden_subprocess_kwargs(),
    )


def prepare_selected_diarization_audio(
    source: Path,
    clips: list[TimestampClip],
    output: Path,
    silence_ms: int = 750,
) -> list[ClipAudioMapping]:
    mappings: list[ClipAudioMapping] = []
    with wave.open(str(source), "rb") as reader:
        channels = reader.getnchannels()
        sample_rate = reader.getframerate()
        sample_width = reader.getsampwidth()
        if channels != 1 or sample_width != 2:
            raise RuntimeError(
                "Fast host recognition requires mono 16-bit PCM audio."
            )
        total_frames = reader.getnframes()
        silence_frames = round(sample_rate * silence_ms / 1000)
        combined_cursor_ms = 0
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(sample_width)
            writer.setframerate(sample_rate)
            selected = sorted(
                (clip for clip in clips if clip.selected),
                key=lambda clip: clip.start_ms,
            )
            for index, clip in enumerate(selected):
                start_frame = min(
                    total_frames,
                    max(0, round(clip.start_ms * sample_rate / 1000)),
                )
                end_frame = min(
                    total_frames,
                    max(start_frame, round(clip.end_ms * sample_rate / 1000)),
                )
                frame_count = end_frame - start_frame
                if frame_count == 0:
                    continue
                reader.setpos(start_frame)
                writer.writeframes(reader.readframes(frame_count))
                duration_ms = round(frame_count * 1000 / sample_rate)
                mappings.append(
                    ClipAudioMapping(
                        combined_start_ms=combined_cursor_ms,
                        combined_end_ms=combined_cursor_ms + duration_ms,
                        source_start_ms=clip.start_ms,
                    )
                )
                combined_cursor_ms += duration_ms
                if index < len(selected) - 1:
                    writer.writeframes(
                        b"\0" * silence_frames * channels * sample_width
                    )
                    combined_cursor_ms += silence_ms
    return mappings


def remap_selected_clip_turns(
    turns: list[SpeakerTurn],
    mappings: list[ClipAudioMapping],
) -> list[SpeakerTurn]:
    remapped: list[SpeakerTurn] = []
    for turn in turns:
        for mapping in mappings:
            start_ms = max(turn.start_ms, mapping.combined_start_ms)
            end_ms = min(turn.end_ms, mapping.combined_end_ms)
            if end_ms <= start_ms:
                continue
            remapped.append(
                SpeakerTurn(
                    start_ms=(
                        mapping.source_start_ms
                        + start_ms
                        - mapping.combined_start_ms
                    ),
                    end_ms=(
                        mapping.source_start_ms
                        + end_ms
                        - mapping.combined_start_ms
                    ),
                    speaker_id=turn.speaker_id,
                )
            )
    return remapped


def _cue_from_words(
    segment: Segment,
    words: list[Word],
    clip_id: str | None,
) -> Segment:
    probabilities = [
        word.probability for word in words if word.probability is not None
    ]
    return segment.model_copy(
        update={
            "start_ms": words[0].start_ms,
            "end_ms": words[-1].end_ms,
            "clip_id": clip_id,
            "raw_korean": " ".join(
                word.text for word in words if word.text
            ).strip(),
            "pass_1_korean": "",
            "pass_2_korean": "",
            "english": "",
            "words": words,
            "confidence": (
                sum(probabilities) / len(probabilities)
                if probabilities
                else segment.confidence
            ),
            "change_reasons": [],
            "warnings": [],
            "status": "raw",
            "locked": False,
            "approved": False,
        }
    )


def _word_cues(words: list[Word]) -> list[list[Word]]:
    cues: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current and word.start_ms - current[-1].end_ms >= 1_500:
            cues.append(current)
            current = []
        current.append(word)
        duration_ms = current[-1].end_ms - current[0].start_ms
        character_count = sum(len(item.text) for item in current)
        sentence_end = word.text.rstrip().endswith(
            (".", "?", "!", "。", "？", "！")
        )
        if (
            duration_ms >= 8_000
            or character_count >= 80
            or (sentence_end and duration_ms >= 2_500)
        ):
            cues.append(current)
            current = []
    if current:
        cues.append(current)
    return cues


def prepare_segments_for_clips(
    segments: list[Segment], clips: list[TimestampClip]
) -> list[Segment]:
    aligned: list[Segment] = []
    for segment in segments:
        overlapping = [
            clip
            for clip in clips
            if segment.start_ms < clip.end_ms
            and segment.end_ms > clip.start_ms
        ]
        if not overlapping:
            aligned.append(segment)
            continue
        if len(overlapping) == 1:
            aligned.append(
                segment.model_copy(
                    update={"clip_id": overlapping[0].clip_id}
                )
            )
            continue
        if not segment.words:
            clip = next(
                (
                    item
                    for item in overlapping
                    if item.start_ms <= segment.start_ms < item.end_ms
                ),
                overlapping[0],
            )
            aligned.append(segment.model_copy(update={"clip_id": clip.clip_id}))
            continue
        for clip in overlapping:
            clip_words = [
                word
                for word in segment.words
                if clip.start_ms <= word.start_ms < clip.end_ms
            ]
            if clip_words:
                aligned.append(
                    _cue_from_words(segment, clip_words, clip.clip_id)
                )

    cues: list[Segment] = []
    for segment in aligned:
        oversized = (
            segment.end_ms - segment.start_ms > 15_000
            or len(segment.raw_korean) > 160
        )
        if not oversized or len(segment.words) < 2:
            cues.append(segment)
            continue
        for words in _word_cues(segment.words):
            cues.append(_cue_from_words(segment, words, segment.clip_id))

    return [
        segment.model_copy(update={"segment_id": f"seg_{index + 1:06d}"})
        for index, segment in enumerate(cues)
    ]


def _turn_overlap(start_ms: int, end_ms: int, turn: SpeakerTurn) -> int:
    return max(0, min(end_ms, turn.end_ms) - max(start_ms, turn.start_ms))


def _speaker_for_range(
    start_ms: int, end_ms: int, turns: list[SpeakerTurn]
) -> str | None:
    if not turns:
        return None
    overlapping = max(
        turns,
        key=lambda turn: _turn_overlap(start_ms, end_ms, turn),
    )
    if _turn_overlap(start_ms, end_ms, overlapping) > 0:
        return overlapping.speaker_id
    midpoint = (start_ms + end_ms) / 2
    nearest = min(
        turns,
        key=lambda turn: min(
            abs(midpoint - turn.start_ms), abs(midpoint - turn.end_ms)
        ),
    )
    return nearest.speaker_id


def align_segments_to_speakers(
    segments: list[Segment], turns: list[SpeakerTurn]
) -> list[Segment]:
    aligned: list[Segment] = []
    for segment in segments:
        if not segment.words:
            aligned.append(
                segment.model_copy(
                    update={
                        "speaker_id": _speaker_for_range(
                            segment.start_ms, segment.end_ms, turns
                        )
                    }
                )
            )
            continue

        groups: list[tuple[str | None, list[Word]]] = []
        for word in segment.words:
            speaker_id = _speaker_for_range(
                word.start_ms, word.end_ms, turns
            )
            if groups and groups[-1][0] == speaker_id:
                groups[-1][1].append(word)
            else:
                groups.append((speaker_id, [word]))
        for speaker_id, words in groups:
            for cue_words in _word_cues(words):
                aligned.append(
                    _cue_from_words(
                        segment, cue_words, segment.clip_id
                    ).model_copy(update={"speaker_id": speaker_id})
                )

    return [
        segment.model_copy(update={"segment_id": f"seg_{index + 1:06d}"})
        for index, segment in enumerate(aligned)
    ]


def _annotation_turns(annotation: Any) -> list[SpeakerTurn]:
    rows = (
        annotation.itertracks(yield_label=True)
        if hasattr(annotation, "itertracks")
        else iter(annotation)
    )
    turns: list[SpeakerTurn] = []
    for row in rows:
        if len(row) == 3:
            interval, _, speaker = row
        else:
            interval, speaker = row
        turns.append(
            SpeakerTurn(
                start_ms=round(float(interval.start) * 1000),
                end_ms=round(float(interval.end) * 1000),
                speaker_id=str(speaker),
            )
        )
    return turns


def _load_diarization_pipeline(token: str):
    cache_root = model_cache_root()
    os.environ.setdefault("HF_HOME", str(cache_root / "huggingface"))
    os.environ.setdefault("MPLCONFIGDIR", str(cache_root / "matplotlib"))
    from pyannote.audio import Pipeline
    import torch

    with _DIARIZATION_MODEL_LOCK:
        pipeline = _DIARIZATION_PIPELINES.get(token)
        if pipeline is not None:
            return pipeline
        try:
            pipeline = Pipeline.from_pretrained(
                DEFAULT_DIARIZATION_MODEL,
                token=token,
                cache_dir=cache_root / "huggingface",
            )
        except TypeError:
            pipeline = Pipeline.from_pretrained(
                DEFAULT_DIARIZATION_MODEL, use_auth_token=token
            )
        if pipeline is None:
            raise RuntimeError(
                "Speaker model access was denied. Accept the selected Pyannote "
                "model terms on Hugging Face, then check your token."
            )
        if torch.cuda.is_available():
            pipeline.to(torch.device("cuda"))
        _DIARIZATION_PIPELINES[token] = pipeline
        return pipeline


def _pcm_waveform(audio_path: Path):
    import torch

    with wave.open(str(audio_path), "rb") as reader:
        if reader.getsampwidth() != 2:
            raise RuntimeError("Speaker detection requires 16-bit PCM audio.")
        channels = reader.getnchannels()
        sample_rate = reader.getframerate()
        samples = torch.frombuffer(
            bytearray(reader.readframes(reader.getnframes())),
            dtype=torch.int16,
        )
        waveform = (
            samples.reshape(-1, channels).transpose(0, 1).float() / 32768.0
        )
    return waveform, sample_rate


def extract_voice_embedding(audio_path: Path, token: str) -> list[float]:
    import numpy as np
    import torch

    pipeline = _load_diarization_pipeline(token)
    waveform, sample_rate = _pcm_waveform(audio_path)
    if sample_rate != 16_000:
        raise RuntimeError("Voice samples must be normalized to 16 kHz.")
    mono = waveform.mean(dim=0, keepdim=True)
    chunk_samples = sample_rate * 10
    minimum_samples = sample_rate * 2
    vectors = []
    for start in range(0, mono.shape[1], chunk_samples):
        chunk = mono[:, start : start + chunk_samples]
        if chunk.shape[1] < minimum_samples:
            continue
        if float(torch.sqrt(torch.mean(chunk.square()))) < 0.003:
            continue
        with _DIARIZATION_MODEL_LOCK:
            vector = pipeline._embedding(chunk.unsqueeze(0))[0]
        if np.all(np.isfinite(vector)):
            norm = np.linalg.norm(vector)
            if norm > 0:
                vectors.append(vector / norm)
    if not vectors:
        raise RuntimeError(
            "The voice sample does not contain enough clear speech."
        )
    embedding = np.mean(np.stack(vectors), axis=0)
    norm = np.linalg.norm(embedding)
    if not np.isfinite(norm) or norm == 0:
        raise RuntimeError("A usable voice profile could not be created.")
    return (embedding / norm).astype(float).tolist()


def _run_pyannote(
    audio_path: Path,
    token: str,
    num_speakers: int | None = None,
    progress: Callable[[float], None] | None = None,
) -> DiarizationResult:
    pipeline = _load_diarization_pipeline(token)
    waveform, sample_rate = _pcm_waveform(audio_path)
    pipeline_options = (
        {"num_speakers": num_speakers} if num_speakers else {}
    )

    def progress_hook(
        step_name: str,
        _artifact: Any,
        file: Any = None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        del file
        if progress is None:
            return
        ranges = {
            "segmentation": (0.12, 0.55),
            "speaker_counting": (0.55, 0.58),
            "embeddings": (0.58, 0.84),
            "discrete_diarization": (0.84, 0.87),
        }
        start, end = ranges.get(step_name, (0.12, 0.87))
        if completed is not None and total:
            fraction = min(1.0, max(0.0, completed / total))
            progress(start + (end - start) * fraction)
        elif _artifact is not None:
            progress(end)

    with _DIARIZATION_MODEL_LOCK:
        output = pipeline(
            {"waveform": waveform, "sample_rate": sample_rate},
            hook=progress_hook,
            **pipeline_options,
        )
    annotation = getattr(
        output, "exclusive_speaker_diarization", None
    ) or getattr(output, "speaker_diarization", output)
    embeddings: dict[str, list[float]] = {}
    values = getattr(output, "speaker_embeddings", None)
    regular = getattr(output, "speaker_diarization", annotation)
    if values is not None and hasattr(regular, "labels"):
        embeddings = {
            str(label): values[index].astype(float).tolist()
            for index, label in enumerate(regular.labels())
            if index < len(values)
        }
    return DiarizationResult(
        turns=_annotation_turns(annotation),
        embeddings=embeddings,
    )


def _normalize_speaker_turns(
    turns: list[SpeakerTurn],
    known_speakers: dict[str, str] | None = None,
    reserved_speaker_ids: set[str] | None = None,
) -> tuple[list[SpeakerTurn], list[str]]:
    ids: dict[str, str] = {}
    used_ids = set(reserved_speaker_ids or ())
    used_ids.update((known_speakers or {}).values())
    normalized: list[SpeakerTurn] = []
    for turn in sorted(turns, key=lambda item: (item.start_ms, item.end_ms)):
        if turn.speaker_id not in ids:
            speaker_id = (
                known_speakers.get(turn.speaker_id)
                if known_speakers
                else None
            )
            if not speaker_id:
                index = 1
                while f"SPEAKER_{index:02d}" in used_ids:
                    index += 1
                speaker_id = f"SPEAKER_{index:02d}"
            ids[turn.speaker_id] = speaker_id
            used_ids.add(speaker_id)
        normalized.append(
            SpeakerTurn(
                turn.start_ms, turn.end_ms, ids[turn.speaker_id]
            )
        )
    return normalized, list(dict.fromkeys(ids.values()))


def match_voice_profiles(
    embeddings: dict[str, list[float]],
    profiles: list[VoiceProfileRecord],
    minimum_similarity: float = 0.2,
) -> dict[str, str]:
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    labels = list(embeddings)
    if not labels or not profiles:
        return {}
    scores = np.full((len(labels), len(profiles)), -1.0, dtype=float)
    for label_index, label in enumerate(labels):
        raw_embedding = embeddings[label]
        embedding = np.asarray(raw_embedding, dtype=float)
        embedding_norm = np.linalg.norm(embedding)
        if not np.isfinite(embedding_norm) or embedding_norm == 0:
            continue
        for profile_index, profile in enumerate(profiles):
            enrolled = np.asarray(profile.embedding, dtype=float)
            enrolled_norm = np.linalg.norm(enrolled)
            if (
                enrolled.shape != embedding.shape
                or not np.isfinite(enrolled_norm)
                or enrolled_norm == 0
            ):
                continue
            similarity = float(
                np.dot(embedding, enrolled) / (embedding_norm * enrolled_norm)
            )
            if np.isfinite(similarity):
                scores[label_index, profile_index] = similarity

    row_indices, column_indices = linear_sum_assignment(scores, maximize=True)
    assignments: dict[str, str] = {}
    for row_index, column_index in zip(row_indices, column_indices):
        if scores[row_index, column_index] >= minimum_similarity:
            assignments[labels[row_index]] = profiles[
                column_index
            ].profile_id
    return assignments


def run_diarization(
    store: Store,
    project_id: str,
    job_id: str,
    clips: list[TimestampClip] | None = None,
    expected_speaker_count: int | None = None,
) -> None:
    job = Job.model_validate(store.get("job", job_id))
    project_dir = store.media_root / project_id
    source = project_dir / "diarization.wav"
    try:
        token = configured_value("HUGGINGFACE_TOKEN", store)
        if not token:
            raise RuntimeError(
                "Add a Hugging Face token in Settings before detecting speakers."
            )
        job.stage, job.progress = "preparing_diarization", 0.05
        _checkpoint_job(store, job)
        if not source.is_file():
            project = Project.model_validate(
                store.get("project", project_id)
            )
            original = project_dir / (project.media_name or "")
            if original.is_file():
                prepare_diarization_audio(original, source)
            else:
                source = project_dir / "normalized.wav"
        if not source.is_file():
            raise RuntimeError("The speaker-analysis audio is missing.")

        profiles = [
            VoiceProfileRecord.model_validate(item)
            for item in store.list("voice_profile", "__app__")
        ]
        clip_mappings: list[ClipAudioMapping] = []
        if profiles and clips:
            selected_source = project_dir / "enrolled-clips.wav"
            clip_mappings = prepare_selected_diarization_audio(
                source, clips, selected_source
            )
            if clip_mappings:
                source = selected_source
        job.stage, job.progress = "diarizing", 0.12
        _checkpoint_job(store, job)

        def save_progress(value: float) -> None:
            job.progress = value
            _checkpoint_job(store, job)

        result = _run_pyannote(
            source, token, expected_speaker_count, save_progress
        )
        if isinstance(result, list):
            result = DiarizationResult(turns=result, embeddings={})
        known_speakers = match_voice_profiles(
            result.embeddings, profiles
        )
        existing_names = {
            Speaker.model_validate(item).speaker_id: (
                Speaker.model_validate(item).name
            )
            for item in store.list("speaker", project_id)
        }
        turns = (
            remap_selected_clip_turns(result.turns, clip_mappings)
            if clip_mappings
            else result.turns
        )
        turns, speaker_ids = _normalize_speaker_turns(
            turns,
            known_speakers,
            set(existing_names) if clip_mappings else None,
        )
        if not turns:
            raise RuntimeError(
                "No speaker activity was detected in the analyzed audio."
            )

        job.stage, job.progress = "saving_speaker_turns", 0.88
        _checkpoint_job(store, job)
        profile_names = {
            profile.profile_id: profile.name for profile in profiles
        }
        scoped = bool(clip_mappings)
        if scoped:
            target_clip_ids = {clip.clip_id for clip in clips or []}
            target_ranges = [
                (clip.start_ms, clip.end_ms) for clip in clips or []
            ]
            for item in store.list("segment", project_id):
                overlaps_target = any(
                    item.get("start_ms", -1) < end_ms
                    and item.get("end_ms", -1) > start_ms
                    for start_ms, end_ms in target_ranges
                )
                if (
                    item.get("clip_id") in target_clip_ids
                    or overlaps_target
                ):
                    store.delete("segment", item["segment_id"])
            preserved_turns = [
                DetectedSpeakerTurn.model_validate(item)
                for item in store.list("speaker_turn", project_id)
                if not any(
                    item.get("start_ms", -1) < end_ms
                    and item.get("end_ms", -1) > start_ms
                    for start_ms, end_ms in target_ranges
                )
            ]
        else:
            store.delete_kind("segment", project_id)
            preserved_turns = []
        store.delete_kind("speaker_turn", project_id)
        all_turns = sorted(
            [
                *preserved_turns,
                *(
                    DetectedSpeakerTurn(
                        turn_id="pending",
                        start_ms=turn.start_ms,
                        end_ms=turn.end_ms,
                        speaker_id=turn.speaker_id,
                    )
                    for turn in turns
                ),
            ],
            key=lambda turn: (turn.start_ms, turn.end_ms),
        )
        for index, turn in enumerate(all_turns):
            store.save_speaker_turn(
                project_id,
                turn.model_copy(update={"turn_id": f"turn_{index + 1:06d}"}),
            )
            if index % 50 == 0:
                _checkpoint_job(store, job)
        all_speaker_ids = list(
            dict.fromkeys(
                [
                    *(turn.speaker_id for turn in preserved_turns),
                    *speaker_ids,
                ]
            )
        )
        if not scoped:
            store.delete_kind("speaker", project_id)
        for index, speaker_id in enumerate(all_speaker_ids):
            store.save_speaker(
                project_id,
                Speaker(
                    speaker_id=speaker_id,
                    name=(
                        profile_names.get(speaker_id)
                        or existing_names.get(speaker_id)
                        or f"Speaker {index + 1}"
                    ),
                ),
            )
        job.stage, job.progress = "speakers_detected", 1
        _update_workflow_status(
            store,
            project_id,
            "speakers_detected",
            clips,
            all_clips=not scoped,
        )
        _checkpoint_job(store, job)
    except JobCancelled:
        return
    except Exception as exc:
        message = str(exc)
        if "401" in message or "gated" in message.lower():
            message = (
                "Speaker model access was denied. Accept the Community-1 model "
                "terms on Hugging Face, then check your token."
            )
        job.stage, job.error = "failed", message
        job.paused = False
        store.save_job(job)


def _update_project(store: Store, project_id: str, **updates: Any) -> Project:
    data = store.get("project", project_id)
    if not data:
        raise RuntimeError("Project not found")
    project = Project.model_validate(data).model_copy(
        update={**updates, "updated_at": datetime.now(timezone.utc).isoformat()}
    )
    store.save_project(project)
    return project


def _update_workflow_status(
    store: Store,
    project_id: str,
    status: str,
    clips: list[TimestampClip] | None = None,
    all_clips: bool = False,
) -> None:
    project = Project.model_validate(store.get("project", project_id))
    if WORKFLOW_RANK.get(status, 0) >= WORKFLOW_RANK.get(project.status, 0):
        _update_project(store, project_id, status=status)
    target_ids = {clip.clip_id for clip in clips or []}
    for item in store.list("clip", project_id):
        clip = TimestampClip.model_validate(item)
        if not all_clips and clip.clip_id not in target_ids:
            continue
        if WORKFLOW_RANK.get(status, 0) < WORKFLOW_RANK.get(clip.status, 0):
            continue
        store.save_clip(
            project_id, clip.model_copy(update={"status": status})
        )


def run_transcription(
    store: Store,
    project_id: str,
    job_id: str,
    model: str,
    clips: list[TimestampClip] | None = None,
) -> None:
    job = Job.model_validate(store.get("job", job_id))
    project_dir = store.media_root / project_id
    output_dir = project_dir / "whisper"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = project_dir / "normalized.wav"
    project = Project.model_validate(store.get("project", project_id))
    total_duration_ms = (
        sum(clip.end_ms - clip.start_ms for clip in clips)
        if clips
        else project.duration_ms
    )
    try:
        faster_whisper_available = (
            importlib.util.find_spec("faster_whisper") is not None
        )
        job.stage = "preparing_model" if faster_whisper_available else "transcribing"
        job.progress = 0.03 if faster_whisper_available else 0.08
        _checkpoint_job(store, job)
        prompt = ", ".join(
            entry["canonical_korean"]
            for entry in store.list("glossary", project_id)
            if entry.get("canonical_korean")
        )
        if faster_whisper_available:
            def model_ready() -> None:
                job.stage, job.progress = "transcribing", 0.08
                _checkpoint_job(store, job)

            def segment_ready(end_seconds: float) -> None:
                processed_ms = _processed_audio_ms(
                    round(end_seconds * 1000),
                    clips,
                    total_duration_ms,
                )
                job.processed_duration_ms = processed_ms
                if total_duration_ms:
                    job.progress = 0.08 + 0.88 * min(
                        1, processed_ms / total_duration_ms
                    )
                _checkpoint_job(store, job)

            payload_segments: list[dict[str, Any]] = []
            if clips:
                for clip in sorted(clips, key=lambda item: item.start_ms):
                    clip_audio_path = (
                        output_dir / f"{job.job_id}_{clip.clip_id}.wav"
                    )
                    try:
                        prepare_transcription_clip(
                            audio_path,
                            clip_audio_path,
                            clip.start_ms,
                            clip.end_ms,
                        )
                        clip_payload = _transcribe_with_faster_whisper(
                            clip_audio_path,
                            model,
                            prompt,
                            model_ready,
                            segment_ready,
                            timestamp_offset_seconds=clip.start_ms / 1000,
                        )
                        payload_segments.extend(
                            clip_payload.get("segments", [])
                        )
                    finally:
                        clip_audio_path.unlink(missing_ok=True)
                payload = {"segments": payload_segments}
            else:
                payload = _transcribe_with_faster_whisper(
                    audio_path,
                    model,
                    prompt,
                    model_ready,
                    segment_ready,
                )
            (output_dir / "normalized.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        else:
            command = [
                *whisper_command(),
                str(audio_path),
                "--model",
                model,
                "--language",
                "ko",
                "--task",
                "transcribe",
                "--word_timestamps",
                "True",
                "--condition_on_previous_text",
                "True",
                "--output_format",
                "json",
                "--output_dir",
                str(output_dir),
                "--verbose",
                "False",
            ]
            if prompt:
                command.extend(["--initial_prompt", prompt])
            if clips:
                command.extend(
                    [
                        "--clip_timestamps",
                        ",".join(
                            str(value) for value in selected_clip_ranges(clips)
                        ),
                    ]
                )
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                **hidden_subprocess_kwargs(),
            )
            _checkpoint_job(store, job)
            payload = json.loads(
                (output_dir / "normalized.json").read_text(encoding="utf-8")
            )
        payload_segments = payload.get("segments", [])
        job.progress = 0.98
        job.processed_duration_ms = total_duration_ms
        _checkpoint_job(store, job)
        transcribed_segments = []
        for index, item in enumerate(payload_segments):
            words = [
                Word(
                    text=word.get("word", "").strip(),
                    start_ms=round(word.get("start", item["start"]) * 1000),
                    end_ms=round(word.get("end", item["end"]) * 1000),
                    probability=word.get("probability"),
                )
                for word in item.get("words", [])
            ]
            transcribed_segments.append(Segment(
                segment_id=f"seg_{uuid4().hex[:12]}",
                start_ms=round(item["start"] * 1000),
                end_ms=round(item["end"] * 1000),
                clip_id=next(
                    (
                        clip.clip_id
                        for clip in clips or []
                        if clip.start_ms
                        <= round(item["start"] * 1000)
                        < clip.end_ms
                    ),
                    None,
                ),
                raw_korean=item["text"].strip(),
                words=words,
                confidence=max(0, min(1, 1 + float(item.get("avg_logprob", -1)))),
                no_speech_probability=float(item.get("no_speech_prob", 0)),
            ))
        if clips:
            transcribed_segments = prepare_segments_for_clips(
                transcribed_segments, clips
            )
        detected_turns = [
            SpeakerTurn(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                speaker_id=item.speaker_id,
            )
            for item in (
                DetectedSpeakerTurn.model_validate(data)
                for data in store.list("speaker_turn", project_id)
            )
        ]
        if not detected_turns:
            raise RuntimeError(
                "Detect speakers before transcribing the audio."
            )
        transcribed_segments = align_segments_to_speakers(
            transcribed_segments, detected_turns
        )
        existing_speakers = [
            Speaker.model_validate(item)
            for item in store.list("speaker", project_id)
        ]
        store.delete_kind("speaker", project_id)
        for speaker in existing_speakers:
            store.save_speaker(project_id, speaker)
        if clips:
            target_clip_ids = {clip.clip_id for clip in clips}
            for item in store.list("segment", project_id):
                segment = Segment.model_validate(item)
                if segment.clip_id in target_clip_ids or any(
                    clip.start_ms <= segment.start_ms < clip.end_ms
                    for clip in clips
                ):
                    store.delete("segment", segment.segment_id)
        else:
            store.delete_kind("segment", project_id)
        for index, segment in enumerate(transcribed_segments):
            store.save_segment(project_id, segment)
            if index % 20 == 0:
                _checkpoint_job(store, job)
        job.stage, job.progress = "transcribed", 1
        _update_workflow_status(
            store, project_id, "transcribed", clips, all_clips=not clips
        )
        _checkpoint_job(store, job)
    except JobCancelled:
        return
    except Exception as exc:
        job.stage, job.error = "failed", str(exc)
        job.paused = False
        store.save_job(job)


def _transcribe_with_faster_whisper(
    audio_path: Path,
    model_name: str,
    prompt: str,
    on_model_ready: Callable[[], None] | None = None,
    on_segment_ready: Callable[[float], None] | None = None,
    timestamp_offset_seconds: float = 0,
) -> dict[str, Any]:
    preferred_device, preferred_compute_type = _preferred_whisper_backend()
    backends = [(preferred_device, preferred_compute_type)]
    if preferred_device == "cuda":
        backends.append(("cpu", "int8"))

    for index, (device, compute_type) in enumerate(backends):
        try:
            model = _load_whisper_model(model_name, device, compute_type)
            if on_model_ready:
                on_model_ready()
            return _collect_whisper_segments(
                model,
                audio_path,
                prompt,
                on_segment_ready,
                timestamp_offset_seconds,
            )
        except JobCancelled:
            raise
        except Exception as exc:
            can_fallback = (
                device == "cuda"
                and index + 1 < len(backends)
                and _is_cuda_runtime_error(exc)
            )
            if not can_fallback:
                raise
            logger.warning(
                "Whisper CUDA failed; retrying on CPU: %s",
                exc,
            )
            with _WHISPER_MODEL_LOCK:
                _WHISPER_MODELS.pop(
                    (model_name, device, compute_type),
                    None,
                )
    raise RuntimeError("No Whisper execution backend is available.")


def _preferred_whisper_backend() -> tuple[str, str]:
    configured = os.environ.get(
        "SUBTITLE_STUDIO_WHISPER_DEVICE", ""
    ).strip().lower()
    if configured == "cpu":
        return "cpu", "int8"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except (ImportError, RuntimeError):
        pass
    return "cpu", "int8"


def _configure_cuda_dll_directories() -> None:
    if sys.platform != "win32":
        return
    candidates: list[Path] = []
    try:
        import nvidia.cublas.lib

        candidates.append(Path(nvidia.cublas.lib.__file__).parent)
    except ImportError:
        pass
    candidates.extend(
        (
            Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
            Path(sys.executable).parent / "_internal" / "nvidia" / "cublas" / "bin",
        )
    )
    existing_path = os.environ.get("PATH", "").split(os.pathsep)
    for directory in candidates:
        if not (directory / "cublas64_12.dll").is_file():
            continue
        value = str(directory)
        if value not in existing_path:
            os.environ["PATH"] = value + os.pathsep + os.environ.get("PATH", "")
            existing_path.insert(0, value)
        if hasattr(os, "add_dll_directory"):
            _CUDA_DLL_DIRECTORIES.append(os.add_dll_directory(value))


def _load_whisper_model(
    model_name: str,
    device: str,
    compute_type: str,
) -> Any:
    from faster_whisper import WhisperModel

    if device == "cuda":
        _configure_cuda_dll_directories()
    key = (model_name, device, compute_type)
    with _WHISPER_MODEL_LOCK:
        cached = _WHISPER_MODELS.get(key)
        if cached is not None:
            return cached
        cache = model_cache_root()
        cache.mkdir(parents=True, exist_ok=True)
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(cache),
        )
        _WHISPER_MODELS[key] = model
        return model


def _collect_whisper_segments(
    model: Any,
    audio_path: Path,
    prompt: str,
    on_segment_ready: Callable[[float], None] | None,
    timestamp_offset_seconds: float,
) -> dict[str, Any]:
    results, _ = model.transcribe(
        str(audio_path),
        language="ko",
        task="transcribe",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=True,
        initial_prompt=prompt or None,
    )
    segments = []
    for result in results:
        start = result.start + timestamp_offset_seconds
        end = result.end + timestamp_offset_seconds
        segments.append(
            {
                "start": start,
                "end": end,
                "text": result.text,
                "avg_logprob": result.avg_logprob,
                "no_speech_prob": result.no_speech_prob,
                "words": [
                    {
                        "word": word.word,
                        "start": (
                            word.start + timestamp_offset_seconds
                            if word.start is not None
                            else start
                        ),
                        "end": (
                            word.end + timestamp_offset_seconds
                            if word.end is not None
                            else end
                        ),
                        "probability": word.probability,
                    }
                    for word in (result.words or [])
                ],
            }
        )
        if on_segment_ready:
            on_segment_ready(end)
    return {"segments": segments}


def _is_cuda_runtime_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "cuda",
            "cudnn",
            "cublas",
            "out of memory",
            "driver version",
        )
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The language model did not return JSON.")
    return json.loads(text[start : end + 1])


async def call_openrouter(
    store: Store, stage: str, system: str, payload: dict[str, Any]
) -> dict[str, Any]:
    base_url = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    ).rstrip("/")
    model = openrouter_model_for_stage(stage, store)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ]
            for attempt in range(2):
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=openrouter_headers(store),
                    json={
                        "model": model,
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                        "provider": {
                            "data_collection": "deny",
                            "require_parameters": True,
                        },
                        "messages": messages,
                    },
                )
                response.raise_for_status()
                content = ""
                try:
                    content = response.json()["choices"][0]["message"]["content"]
                    return _extract_json(content)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "OpenRouter returned invalid JSON for %s on attempt %s",
                        stage,
                        attempt + 1,
                    )
                    if attempt == 1:
                        raise RuntimeError(
                            "The language model returned an unreadable response. Try again."
                        ) from exc
                    messages = [
                        *messages,
                        {"role": "assistant", "content": str(content)},
                        {
                            "role": "user",
                            "content": (
                                "Return the same answer again as one valid JSON object. "
                                "Do not use Markdown or add any text outside the JSON."
                            ),
                        },
                    ]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        messages = {
            401: "OpenRouter rejected the API key.",
            402: (
                "OpenRouter has insufficient credits for this model. "
                "Add credits or choose a free model in Settings."
            ),
            429: "OpenRouter rate limit reached. Retry after a short pause.",
        }
        raise RuntimeError(
            messages.get(status, f"OpenRouter request failed with status {status}.")
        ) from exc
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise RuntimeError(
            "OpenRouter is unavailable. Check the internet connection and retry."
        ) from exc


def _post_copy_source(
    store: Store, project_id: str, clip_id: str
) -> tuple[TimestampClip, list[dict[str, Any]], str]:
    clip_record = store.get("clip", clip_id)
    if not clip_record or clip_record.get("project_id") not in {None, project_id}:
        raise RuntimeError("The selected clip no longer exists.")
    clip = TimestampClip.model_validate(clip_record)
    speaker_names = {
        item["speaker_id"]: item.get("name") or item["speaker_id"]
        for item in store.list("speaker", project_id)
    }
    transcript = []
    for item in store.list("segment", project_id):
        segment_clip_id = item.get("clip_id")
        belongs_to_clip = segment_clip_id == clip_id or (
            not segment_clip_id
            and item.get("start_ms", -1) < clip.end_ms
            and item.get("end_ms", -1) > clip.start_ms
        )
        english = str(item.get("english") or "").strip()
        if not belongs_to_clip or not english:
            continue
        speaker_id = item.get("speaker_id")
        transcript.append(
            {
                "segment_id": item["segment_id"],
                "speaker": speaker_names.get(speaker_id, speaker_id),
                "start_ms": item.get("start_ms", 0),
                "text": english,
            }
        )
    transcript.sort(key=lambda item: (item["start_ms"], item["segment_id"]))
    signature_payload = json.dumps(
        {
            "clip_id": clip.clip_id,
            "title": clip.title,
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
            "transcript": transcript,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    signature = hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()
    return clip, transcript, signature


def post_copy_source_signature(
    store: Store, project_id: str, clip_id: str
) -> str:
    try:
        return _post_copy_source(store, project_id, clip_id)[2]
    except RuntimeError:
        return ""


def format_post_copy_quote_blocks(body: str) -> str:
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n", body.strip())
        if block.strip()
    ]
    formatted: list[str] = []
    quote_pattern = re.compile(
        r'^(?:(?P<speaker>[^:\n]{1,80}):\s*)?["\u201c](?P<quote>.*)["\u201d]$',
        re.DOTALL,
    )
    for block in blocks:
        match = quote_pattern.match(block)
        if not match:
            formatted.append(block)
            continue
        quote = " ".join(match.group("quote").split())
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", quote)
            if sentence.strip()
        ]
        if len(sentences) < 3:
            formatted.append(block)
            continue
        speaker = match.group("speaker")
        for index, sentence in enumerate(sentences):
            prefix = f"{speaker}: " if speaker and index == 0 else ""
            formatted.append(f'{prefix}"{sentence}"')
    return "\n\n".join(formatted)


async def generate_post_copy(
    store: Store, project_id: str, clip_id: str
) -> PostCopy:
    clip, transcript, signature = _post_copy_source(
        store, project_id, clip_id
    )
    if not transcript:
        raise RuntimeError(
            "Translate this clip before generating its post copy."
        )
    payload = {
        "clip": {
            "clip_id": clip.clip_id,
            "working_title": clip.title,
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
        },
        "transcript": transcript,
    }
    headline = ""
    body = ""
    for attempt in range(2):
        result = await call_openrouter(
            store,
            "post_captioning",
            POST_COPY_PROMPT,
            payload
            if attempt == 0
            else {
                **payload,
                "retry_instruction": (
                    "The previous answer was incomplete. Return non-empty "
                    "headline and body string fields."
                ),
            },
        )
        headline = str(result.get("headline") or "").strip()
        body = str(result.get("body") or "").strip()
        if headline and body:
            break
    if not headline or not body:
        raise RuntimeError(
            "The language model returned incomplete post copy. Try again."
        )
    body = format_post_copy_quote_blocks(body)
    post_copy = PostCopy(
        clip_id=clip_id,
        headline=headline,
        body=body,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_signature=signature,
    )
    store.save_post_copy(project_id, post_copy)
    return post_copy


def _dialogue_payload(
    store: Store,
    project_id: str,
    clips: list[TimestampClip] | None = None,
) -> dict[str, Any]:
    segments = store.list("segment", project_id)
    if clips:
        selected_ids = {clip.clip_id for clip in clips}
        segments = [
            item
            for item in segments
            if item.get("clip_id") in selected_ids
            or any(
                clip.start_ms <= item.get("start_ms", -1) < clip.end_ms
                for clip in clips
            )
        ]
    return {
        "project": store.get("project", project_id),
        "glossary": store.list("glossary", project_id),
        "speakers": store.list("speaker", project_id),
        "segments": segments,
    }


def _dialogue_batches(
    segments: list[dict[str, Any]],
    max_duration_ms: int = 90_000,
    max_segments: int = 60,
    max_characters: int = 12_000,
) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        segments,
        key=lambda item: (
            item.get("start_ms", 0),
            item.get("segment_id", ""),
        ),
    )
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_characters = 0
    for segment in ordered:
        segment_characters = len(
            json.dumps(segment, ensure_ascii=False)
        )
        duration_ms = (
            segment.get("end_ms", 0) - current[0].get("start_ms", 0)
            if current
            else 0
        )
        if current and (
            len(current) >= max_segments
            or current_characters + segment_characters > max_characters
            or duration_ms > max_duration_ms
        ):
            batches.append(current)
            current = []
            current_characters = 0
        current.append(segment)
        current_characters += segment_characters
    if current:
        batches.append(current)
    return batches


def _language_rows(
    result: dict[str, Any], stage: str
) -> list[dict[str, Any]]:
    key = "translations" if stage == "translating" else "corrected_segments"
    rows = result.get(key)
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) for row in rows
    ):
        raise RuntimeError(
            "The language model returned an invalid transcript response."
        )
    return rows


async def run_language_stage(
    store: Store,
    project_id: str,
    job_id: str,
    stage: str,
    clips: list[TimestampClip] | None = None,
) -> None:
    job = Job.model_validate(store.get("job", job_id))
    prompts = {
        "correcting_pass_1": CORRECTION_PROMPT,
        "correcting_pass_2": CONSISTENCY_PROMPT,
        "translating": TRANSLATION_PROMPT,
    }
    try:
        job.stage, job.progress = stage, 0.1
        _checkpoint_job(store, job)
        payload = _dialogue_payload(store, project_id, clips)
        segments = {
            item["segment_id"]: Segment.model_validate(item)
            for item in store.list("segment", project_id)
        }
        batches = _dialogue_batches(payload["segments"])
        target_count = sum(
            not segments[item["segment_id"]].locked
            for batch in batches
            for item in batch
            if item.get("segment_id") in segments
        )
        completed_count = 0
        for batch in batches:
            _checkpoint_job(store, job)
            required_ids = [
                item["segment_id"]
                for item in batch
                if item.get("segment_id") in segments
                and not segments[item["segment_id"]].locked
            ]
            if not required_ids:
                continue
            batch_payload = {
                **payload,
                "segments": batch,
                "required_segment_ids": required_ids,
            }
            result = await call_openrouter(
                store, stage, prompts[stage], batch_payload
            )
            rows = _language_rows(result, stage)
            row_by_id = {
                row.get("segment_id"): row
                for row in rows
                if row.get("segment_id") in required_ids
            }
            missing_ids = [
                segment_id
                for segment_id in required_ids
                if segment_id not in row_by_id
            ]
            if missing_ids:
                retry_payload = {
                    **payload,
                    "segments": [
                        item
                        for item in batch
                        if item.get("segment_id") in missing_ids
                    ],
                    "required_segment_ids": missing_ids,
                    "repair_instruction": (
                        "Return exactly one valid row for every required "
                        "segment ID. Do not omit any ID."
                    ),
                }
                retry_result = await call_openrouter(
                    store, stage, prompts[stage], retry_payload
                )
                row_by_id.update(
                    {
                        row.get("segment_id"): row
                        for row in _language_rows(retry_result, stage)
                        if row.get("segment_id") in missing_ids
                    }
                )
                missing_ids = [
                    segment_id
                    for segment_id in required_ids
                    if segment_id not in row_by_id
                ]
            if missing_ids:
                raise RuntimeError(
                    "The language model omitted "
                    f"{len(missing_ids)} transcript segment(s) after retry. "
                    "No incomplete stage was marked as finished."
                )
            for segment_id in required_ids:
                _checkpoint_job(store, job)
                row = row_by_id[segment_id]
                segment = segments[segment_id]
                if stage == "translating":
                    segment.english = row.get("english", segment.english)
                    segment.warnings = row.get("warnings", [])
                    segment.status = (
                        "warning" if segment.warnings else "translated"
                    )
                else:
                    field = (
                        "pass_1_korean"
                        if stage == "correcting_pass_1"
                        else "pass_2_korean"
                    )
                    setattr(
                        segment,
                        field,
                        row.get("corrected_korean", segment.raw_korean),
                    )
                    if (
                        stage == "correcting_pass_2"
                        and not segment.pass_2_korean
                    ):
                        segment.pass_2_korean = segment.pass_1_korean
                    segment.change_reasons = row.get("change_reason", [])
                    segment.warnings = [
                        f"Uncertain: {phrase}"
                        for phrase in row.get("uncertain_phrases", [])
                    ]
                    segment.confidence = row.get(
                        "confidence", segment.confidence
                    )
                    segment.status = (
                        "warning" if segment.warnings else "corrected"
                    )
                store.save_segment(project_id, segment)
                completed_count += 1
                job.progress = 0.1 + 0.9 * completed_count / max(
                    1, target_count
                )
                _checkpoint_job(store, job)
        status = {
            "correcting_pass_1": "corrected_pass_1",
            "correcting_pass_2": "corrected",
            "translating": "translated",
        }[stage]
        job.stage, job.progress = status, 1
        job.warning_count = sum(
            len(item.get("warnings", [])) for item in store.list("segment", project_id)
        )
        _update_workflow_status(
            store, project_id, status, clips, all_clips=not clips
        )
        _checkpoint_job(store, job)
    except JobCancelled:
        return
    except Exception as exc:
        job.stage, job.error = "failed", str(exc)
        job.paused = False
        store.save_job(job)


async def run_english_pipeline(
    store: Store,
    project_id: str,
    job_id: str,
    clips: list[TimestampClip] | None = None,
    expected_speaker_count: int | None = None,
) -> None:
    project = Project.model_validate(store.get("project", project_id))
    rank = WORKFLOW_RANK.get(
        clips[0].status if clips and len(clips) == 1 else project.status,
        0,
    )
    steps = [
        (
            1,
            "speakers_detected",
            lambda: run_diarization(
                store,
                project_id,
                job_id,
                clips,
                expected_speaker_count,
            ),
        ),
        (
            2,
            "transcribed",
            lambda: run_transcription(
                store,
                project_id,
                job_id,
                DEFAULT_WHISPER_MODEL,
                clips,
            ),
        ),
        (
            3,
            "corrected_pass_1",
            lambda: run_language_stage(
                store,
                project_id,
                job_id,
                "correcting_pass_1",
                clips,
            ),
        ),
        (
            4,
            "corrected",
            lambda: run_language_stage(
                store,
                project_id,
                job_id,
                "correcting_pass_2",
                clips,
            ),
        ),
        (
            5,
            "translated",
            lambda: run_language_stage(
                store,
                project_id,
                job_id,
                "translating",
                clips,
            ),
        ),
    ]
    try:
        for step_number, completed_stage, run_step in steps:
            if rank > step_number:
                continue
            job = Job.model_validate(store.get("job", job_id))
            job.pipeline_step = step_number
            job.progress = 0
            job.overall_progress = (step_number - 1) / job.pipeline_total
            store.save_job(job)
            _checkpoint_job(store, job)

            result = run_step()
            if inspect.isawaitable(result):
                await result

            current = Job.model_validate(store.get("job", job_id))
            if current.cancelled or current.stage in {"cancelled", "failed"}:
                return
            if current.stage != completed_stage:
                raise RuntimeError(
                    f"The pipeline stopped before {completed_stage.replace('_', ' ')}."
                )

        completed = Job.model_validate(store.get("job", job_id))
        completed.pipeline_completed = True
        completed.pipeline_step = completed.pipeline_total
        completed.overall_progress = 1
        completed.progress = 1
        store.save_job(completed)
    except JobCancelled:
        return
    except Exception as exc:
        failed = Job.model_validate(store.get("job", job_id))
        failed.stage = "failed"
        failed.error = str(exc)
        failed.paused = False
        store.save_job(failed)


def format_timestamp(ms: int, separator: str = ",") -> str:
    hours, remainder = divmod(max(0, ms), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def format_ass_timestamp(ms: int) -> str:
    centiseconds = max(0, ms) // 10
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def ass_color(hex_color: str, opacity: float = 1) -> str:
    value = hex_color.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha = round((1 - opacity) * 255)
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


@dataclass(frozen=True)
class CaptionWord:
    text: str
    start_ms: int
    end_ms: int
    segment_id: str
    clip_id: str | None
    speaker_id: str | None


CAPTION_HARD_PAUSE_MS = 2_000


def balance_caption_words(
    words: list[str], max_lines: int
) -> list[str]:
    if not words:
        return [""]
    line_count = min(len(words), max(1, max_lines))
    base_line_size, longer_line_count = divmod(
        len(words), line_count
    )
    lines = []
    offset = 0
    for line_index in range(line_count):
        line_size = base_line_size + (
            1 if line_index < longer_line_count else 0
        )
        lines.append(" ".join(words[offset : offset + line_size]))
        offset += line_size
    return lines


def caption_source_signature(
    segments: list[dict[str, Any]],
    language: str,
    max_words_per_line: int,
    max_lines: int,
) -> str:
    source = []
    for data in segments:
        segment = Segment.model_validate(data)
        korean = (
            segment.pass_2_korean
            or segment.pass_1_korean
            or segment.raw_korean
        )
        source.append(
            {
                "id": segment.segment_id,
                "start": segment.start_ms,
                "end": segment.end_ms,
                "clip": segment.clip_id,
                "speaker": segment.speaker_id,
                "text": segment.english or korean
                if language == "en"
                else korean,
            }
        )
    payload = {
        "language": language,
        "max_words_per_line": max_words_per_line,
        "max_lines": max_lines,
        "segments": source,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def project_caption_source_signature(
    segments: list[dict[str, Any]],
    language: str,
    clips: list[TimestampClip],
    default_style: SubtitleStyle,
) -> str:
    layouts = [
        {
            "clip_id": clip.clip_id,
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
            "max_words_per_line": (
                clip.subtitle_style or default_style
            ).max_words_per_line,
            "max_lines": (clip.subtitle_style or default_style).max_lines,
        }
        for clip in sorted(clips, key=lambda item: item.start_ms)
    ]
    payload = {
        "transcript": caption_source_signature(
            segments,
            language,
            default_style.max_words_per_line,
            default_style.max_lines,
        ),
        "layouts": layouts,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_caption_track(
    segments: list[dict[str, Any]],
    language: str,
    max_words_per_line: int,
    max_lines: int,
) -> CaptionTrack:
    capacity = max_words_per_line * max_lines
    words: list[CaptionWord] = []
    for data in sorted(segments, key=lambda item: item.get("start_ms", 0)):
        segment = Segment.model_validate(data)
        korean = (
            segment.pass_2_korean
            or segment.pass_1_korean
            or segment.raw_korean
        )
        text = segment.english or korean if language == "en" else korean
        text_words = text.split()
        if not text_words:
            continue
        duration = max(len(text_words), segment.end_ms - segment.start_ms)
        weights = [
            max(1, len(re.sub(r"\W", "", word, flags=re.UNICODE)))
            for word in text_words
        ]
        total_weight = sum(weights)
        elapsed_weight = 0
        for word, weight in zip(text_words, weights, strict=True):
            word_start = segment.start_ms + round(
                duration * elapsed_weight / total_weight
            )
            elapsed_weight += weight
            word_end = segment.start_ms + round(
                duration * elapsed_weight / total_weight
            )
            words.append(
                CaptionWord(
                    text=word,
                    start_ms=word_start,
                    end_ms=max(word_start + 1, word_end),
                    segment_id=segment.segment_id,
                    clip_id=segment.clip_id,
                    speaker_id=segment.speaker_id,
                )
            )

    groups: list[list[CaptionWord]] = []
    current: list[CaptionWord] = []
    for word in words:
        previous = current[-1] if current else None
        boundary = bool(
            previous
            and (
                len(current) >= capacity
                or word.clip_id != previous.clip_id
                or (
                    word.speaker_id
                    and previous.speaker_id
                    and word.speaker_id != previous.speaker_id
                )
                or word.start_ms - previous.end_ms
                > CAPTION_HARD_PAUSE_MS
            )
        )
        if boundary:
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)

    cues: list[CaptionCue] = []
    previous_end = 0
    for index, group in enumerate(groups, start=1):
        lines = balance_caption_words(
            [word.text for word in group], max_lines
        )
        start_ms = max(previous_end, group[0].start_ms)
        end_ms = max(start_ms + 80, group[-1].end_ms)
        source_segment_ids = list(
            dict.fromkeys(word.segment_id for word in group)
        )
        cues.append(
            CaptionCue(
                cue_id=f"cue_{index:06d}",
                start_ms=start_ms,
                end_ms=end_ms,
                lines=lines,
                source_segment_ids=source_segment_ids,
                clip_id=group[0].clip_id,
                speaker_id=group[0].speaker_id,
            )
        )
        previous_end = end_ms

    return CaptionTrack(
        language=language,
        max_words_per_line=max_words_per_line,
        max_lines=max_lines,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_signature=caption_source_signature(
            segments,
            language,
            max_words_per_line,
            max_lines,
        ),
        cues=cues,
    )


def generate_project_caption_track(
    segments: list[dict[str, Any]],
    language: str,
    clips: list[TimestampClip],
    default_style: SubtitleStyle,
) -> CaptionTrack:
    clip_map = {clip.clip_id: clip for clip in clips}
    grouped: dict[str | None, list[dict[str, Any]]] = {}
    for segment in segments:
        grouped.setdefault(segment.get("clip_id"), []).append(segment)

    cues: list[CaptionCue] = []
    ordered_keys = [
        clip.clip_id for clip in sorted(clips, key=lambda item: item.start_ms)
        if clip.clip_id in grouped
    ]
    ordered_keys.extend(
        key for key in grouped
        if key not in clip_map
    )
    for key in ordered_keys:
        clip = clip_map.get(key) if key is not None else None
        style = clip.subtitle_style if clip and clip.subtitle_style else default_style
        generated = generate_caption_track(
            grouped[key],
            language,
            style.max_words_per_line,
            style.max_lines,
        )
        cues.extend(generated.cues)
    cues.sort(key=lambda cue: cue.start_ms)
    cues = [
        cue.model_copy(update={"cue_id": f"cue_{index:06d}"})
        for index, cue in enumerate(cues, start=1)
    ]
    return CaptionTrack(
        language=language,
        max_words_per_line=default_style.max_words_per_line,
        max_lines=default_style.max_lines,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_signature=project_caption_source_signature(
            segments, language, clips, default_style
        ),
        cues=cues,
    )


def _ass_style_line(name: str, style: SubtitleStyle) -> str:
    alignment = {
        ("bottom", "left"): 1,
        ("bottom", "center"): 2,
        ("bottom", "right"): 3,
        ("middle", "left"): 4,
        ("middle", "center"): 5,
        ("middle", "right"): 6,
        ("top", "left"): 7,
        ("top", "center"): 8,
        ("top", "right"): 9,
    }[(style.position, style.alignment)]
    primary = ass_color(style.text_color)
    if style.background_enabled:
        # ASS opaque boxes use OutlineColour as their fill. A non-zero
        # fallback border is required even when one padding axis is zero.
        outline = ass_color(
            style.background_color, style.background_opacity
        )
        background = outline
        border_style = 3
        outline_size = 1
    else:
        outline = ass_color(style.outline_color)
        background = ass_color(style.background_color, 0)
        border_style = 1
        outline_size = style.outline_size
    return (
        f"Style: {name},"
        f"{style.font_family},{style.font_size},{primary},{primary},"
        f"{outline},{background},{-1 if style.font_weight == 'bold' else 0},"
        f"{-1 if style.font_style == 'italic' else 0},0,0,100,100,"
        f"{style.letter_spacing:g},0,{border_style},{outline_size:g},"
        f"{style.shadow_size:g},{alignment},60,60,{style.margin_vertical},1"
    )


def _ass_caption_text(text: str, style: SubtitleStyle) -> str:
    escaped = escape_ass_text(text)
    if not style.background_enabled:
        return escaped
    padding_x = style.background_padding_x
    padding_y = style.background_padding_y
    if padding_x == 0 and padding_y == 0:
        padding_x = padding_y = 1
    return (
        rf"{{\xbord{padding_x}\ybord{padding_y}}}"
        + escaped
    )


def ass_subtitle_header(
    style: SubtitleStyle,
    named_styles: dict[str, SubtitleStyle] | None = None,
) -> str:
    style_lines = [_ass_style_line("Default", style)]
    style_lines.extend(
        _ass_style_line(name, named_style)
        for name, named_style in (named_styles or {}).items()
    )
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "\n".join(style_lines)
        + "\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )


def export_ass_subtitles(
    segments: list[dict[str, Any]],
    language: str,
    style: SubtitleStyle,
    bilingual: bool = False,
) -> str:
    header = ass_subtitle_header(style)
    events: list[str] = []
    previous_end = 0
    for data in segments:
        segment = Segment.model_validate(data)
        start = max(previous_end, segment.start_ms)
        end = max(start + 80, segment.end_ms)
        korean = (
            segment.pass_2_korean
            or segment.pass_1_korean
            or segment.raw_korean
        )
        english = segment.english or korean
        if bilingual:
            lines = [
                *wrap_subtitle(korean, style.max_words_per_line),
                *wrap_subtitle(english, style.max_words_per_line),
            ]
            pages = paginate_subtitle_lines(lines, style.max_lines)
        else:
            pages = paginate_subtitle_text(
                english if language == "en" else korean,
                style.max_words_per_line,
                style.max_lines,
            )
        for page_index, page in enumerate(pages):
            page_start, page_end = subtitle_page_times(
                start, end, page_index, len(pages)
            )
            text = "\n".join(page)
            events.append(
                "Dialogue: 0,"
                f"{format_ass_timestamp(page_start)},"
                f"{format_ass_timestamp(page_end)},"
                f"Default,,0,0,0,,{_ass_caption_text(text, style)}"
            )
        previous_end = end
    return header + "\n".join(events) + "\n"


def export_ass_caption_cues(
    cues: list[CaptionCue],
    style: SubtitleStyle,
    clip_styles: dict[str, SubtitleStyle] | None = None,
) -> str:
    style_names = {
        clip_id: f"Clip{index:03d}"
        for index, clip_id in enumerate(
            sorted(clip_styles or {}), start=1
        )
    }
    named_styles = {
        style_names[clip_id]: clip_style
        for clip_id, clip_style in (clip_styles or {}).items()
    }
    events = []
    for cue in cues:
        cue_style = (clip_styles or {}).get(cue.clip_id or "", style)
        events.append(
            "Dialogue: 0,"
            f"{format_ass_timestamp(cue.start_ms)},"
            f"{format_ass_timestamp(cue.end_ms)},"
            f"{style_names.get(cue.clip_id or '', 'Default')},,0,0,0,,"
            f"{_ass_caption_text(chr(10).join(cue.lines), cue_style)}"
        )
    return (
        ass_subtitle_header(style, named_styles)
        + "\n".join(events)
        + "\n"
    )


def wrap_subtitle(text: str, max_words_per_line: int = 8) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        lines.extend(
            " ".join(words[index : index + max_words_per_line])
            for index in range(0, len(words), max_words_per_line)
        )
    return lines


def paginate_subtitle_lines(
    lines: list[str], max_lines: int = 1
) -> list[list[str]]:
    safe_max_lines = max(1, max_lines)
    return [
        lines[index : index + safe_max_lines]
        for index in range(0, len(lines), safe_max_lines)
    ] or [[""]]


def paginate_subtitle_text(
    text: str,
    max_words_per_line: int,
    max_lines: int,
) -> list[list[str]]:
    pages: list[list[str]] = []
    capacity = max(1, max_words_per_line) * max(1, max_lines)
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            pages.append([""])
            continue
        for index in range(0, len(words), capacity):
            pages.append(
                balance_caption_words(
                    words[index : index + capacity], max_lines
                )
            )
    return pages or [[""]]


def subtitle_page_times(
    start_ms: int, end_ms: int, page_index: int, page_count: int
) -> tuple[int, int]:
    duration = max(page_count, end_ms - start_ms)
    page_start = start_ms + round(duration * page_index / page_count)
    page_end = start_ms + round(duration * (page_index + 1) / page_count)
    return page_start, max(page_start + 1, page_end)


def export_subtitles(
    segments: list[dict[str, Any]],
    language: str,
    format_name: str,
    bilingual: bool = False,
    max_words_per_line: int = 8,
    max_lines: int = 1,
) -> str:
    output: list[str] = []
    previous_end = 0
    cue_index = 1
    for data in segments:
        segment = Segment.model_validate(data)
        start = max(previous_end, segment.start_ms)
        end = max(start + 80, segment.end_ms)
        korean = segment.pass_2_korean or segment.pass_1_korean or segment.raw_korean
        english = segment.english or korean
        if bilingual:
            lines = [
                *wrap_subtitle(korean, max_words_per_line),
                *wrap_subtitle(english, max_words_per_line),
            ]
            pages = paginate_subtitle_lines(lines, max_lines)
        else:
            pages = paginate_subtitle_text(
                english if language == "en" else korean,
                max_words_per_line,
                max_lines,
            )
        for page_index, page in enumerate(pages):
            page_start, page_end = subtitle_page_times(
                start, end, page_index, len(pages)
            )
            if format_name == "vtt":
                output.append(
                    f"{format_timestamp(page_start, '.')} --> "
                    f"{format_timestamp(page_end, '.')}\n"
                    + "\n".join(page)
                )
            else:
                output.append(
                    f"{cue_index}\n{format_timestamp(page_start)} --> "
                    f"{format_timestamp(page_end)}\n"
                    + "\n".join(page)
                )
            cue_index += 1
        previous_end = end
    prefix = "WEBVTT\n\n" if format_name == "vtt" else ""
    return prefix + "\n\n".join(output) + "\n"


def export_caption_cues(
    cues: list[CaptionCue], format_name: str
) -> str:
    output = []
    for index, cue in enumerate(cues, start=1):
        separator = "." if format_name == "vtt" else ","
        timing = (
            f"{format_timestamp(cue.start_ms, separator)} --> "
            f"{format_timestamp(cue.end_ms, separator)}"
        )
        body = "\n".join(cue.lines)
        output.append(
            f"{timing}\n{body}"
            if format_name == "vtt"
            else f"{index}\n{timing}\n{body}"
        )
    prefix = "WEBVTT\n\n" if format_name == "vtt" else ""
    return prefix + "\n\n".join(output) + "\n"


def video_export_filter(resolution: str) -> str:
    caption_filter = "subtitles=chunk.ass:fontsdir=."
    if resolution == "source":
        return caption_filter
    return (
        "scale=1920:1080:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,{caption_filter}"
    )


def available_gpu_video_encoder(
    ffmpeg: str | None = None,
) -> tuple[str, str] | None:
    executable = ffmpeg or bundled_binary("ffmpeg")
    candidates = (
        [("h264_videotoolbox", "Apple VideoToolbox")]
        if sys.platform == "darwin"
        else [
            ("h264_nvenc", "NVIDIA NVENC"),
            ("h264_qsv", "Intel Quick Sync"),
            ("h264_amf", "AMD AMF"),
        ]
    )
    for codec, label in candidates:
        try:
            subprocess.run(
                [
                    executable,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=640x360:r=30:d=0.1",
                    "-frames:v",
                    "1",
                    "-an",
                    *video_encoder_args(codec, "high"),
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
                **hidden_subprocess_kwargs(),
            )
            return codec, label
        except (subprocess.SubprocessError, OSError):
            continue
    return None


def video_encoder_args(codec: str, quality: str) -> list[str]:
    value = "16" if quality == "maximum" else "18"
    if codec == "h264_videotoolbox":
        bitrate = "20M" if quality == "maximum" else "12M"
        maxrate = "28M" if quality == "maximum" else "18M"
        bufsize = "40M" if quality == "maximum" else "24M"
        return [
            "-c:v",
            codec,
            "-profile:v",
            "high",
            "-b:v",
            bitrate,
            "-maxrate",
            maxrate,
            "-bufsize",
            bufsize,
            "-tag:v",
            "avc1",
        ]
    if codec == "h264_nvenc":
        return [
            "-c:v",
            codec,
            "-preset",
            "p7" if quality == "maximum" else "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            value,
            "-b:v",
            "0",
        ]
    if codec == "h264_qsv":
        return [
            "-c:v",
            codec,
            "-preset",
            "veryslow" if quality == "maximum" else "slow",
            "-global_quality",
            value,
        ]
    if codec == "h264_amf":
        return [
            "-c:v",
            codec,
            "-quality",
            "quality",
            "-rc",
            "cqp",
            "-qp_i",
            value,
            "-qp_p",
            str(int(value) + 2),
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "slow" if quality == "maximum" else "medium",
        "-crf",
        value,
    ]


def _run_video_chunk(
    store: Store,
    job: Job,
    command: list[str],
    work_dir: Path,
    completed_ms: int,
    chunk_duration_ms: int,
    total_duration_ms: int,
) -> None:
    while True:
        process = subprocess.Popen(
            command,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        restart = False
        assert process.stdout is not None
        while True:
            current_data = store.get("job", job.job_id)
            if not current_data:
                process.terminate()
                raise JobCancelled
            current = Job.model_validate(current_data)
            if current.cancelled:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise JobCancelled
            if current.paused:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                job.paused = True
                store.save_job(job)
                _checkpoint_job(store, job)
                restart = True
                break
            line = process.stdout.readline()
            if line:
                key, _, value = line.strip().partition("=")
                if key in {"out_time_us", "out_time_ms"}:
                    try:
                        encoded_ms = min(
                            chunk_duration_ms,
                            max(0, int(value) // 1000),
                        )
                        job.processed_duration_ms = (
                            completed_ms + encoded_ms
                        )
                        job.progress = min(
                            0.98,
                            job.processed_duration_ms
                            / max(1, total_duration_ms),
                        )
                        store.save_job(job)
                    except ValueError:
                        pass
            if process.poll() is not None:
                break
            if not line:
                time.sleep(0.05)
        if restart:
            continue
        return_code = process.wait()
        if return_code != 0:
            assert process.stderr is not None
            error = process.stderr.read().strip()
            raise RuntimeError(error or "FFmpeg could not encode the video")
        return


def run_video_export(
    store: Store,
    project_id: str,
    job_id: str,
    resolution: str = "1080p",
    quality: str = "maximum",
    encoder: str = "gpu",
    clip_ids: list[str] | None = None,
    include_video: bool = True,
    include_srt: bool = False,
    include_ass: bool = False,
) -> None:
    job = Job.model_validate(store.get("job", job_id))
    project = Project.model_validate(store.get("project", project_id))
    project_dir = store.media_root / project_id
    source = project_dir / (project.media_name or "")
    track_data = store.get("caption_track", f"{project_id}:en")
    if not track_data:
        job.stage = "failed"
        job.error = "Generate captions before exporting"
        store.save_job(job)
        return
    track = CaptionTrack.model_validate(track_data)
    clips = [
        TimestampClip.model_validate(item)
        for item in store.list("clip", project_id)
    ]
    clip_lookup = {clip.clip_id: clip for clip in clips}
    if clip_ids:
        missing = [clip_id for clip_id in clip_ids if clip_id not in clip_lookup]
        if missing:
            job.stage = "failed"
            job.error = "One or more video segments were not found"
            store.save_job(job)
            return
        export_clips = [clip_lookup[clip_id] for clip_id in clip_ids]
    elif clips:
        export_clips = clips
    else:
        export_clips = [
            TimestampClip(
                clip_id="full_video",
                start_ms=0,
                end_ms=project.duration_ms,
                title=project.name,
            )
        ]
    export_clips = sorted(export_clips, key=lambda clip: clip.start_ms)
    clip_styles = {
        clip.clip_id: clip.subtitle_style
        for clip in clips
        if clip.subtitle_style
    }
    export_dir = store.video_export_dir(project_id, project.name)
    work_dir = project_dir / ".video-export-work" / job_id
    chunk_ms = 15_000
    ffmpeg = bundled_binary("ffmpeg")
    created_outputs: list[Path] = []
    using_gpu = False

    def local_caption_cues(clip: TimestampClip) -> list[CaptionCue]:
        local_cues = []
        for cue in track.cues:
            overlap_start = max(clip.start_ms, cue.start_ms)
            overlap_end = min(clip.end_ms, cue.end_ms)
            if overlap_end <= overlap_start:
                continue
            local_cues.append(
                cue.model_copy(
                    update={
                        "start_ms": overlap_start - clip.start_ms,
                        "end_ms": overlap_end - clip.start_ms,
                    }
                )
            )
        return local_cues

    def safe_clip_title(clip: TimestampClip) -> str:
        value = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]+',
            "_",
            clip.title,
        ).strip(" ._")
        return re.sub(r"\s+", " ", value)[:80] or clip.clip_id

    def write_subtitle_outputs(
        clip: TimestampClip,
        base_name: str,
    ) -> list[VideoExportOutput]:
        cues = local_caption_cues(clip)
        requested = []
        if include_srt:
            requested.append(
                (
                    "srt",
                    f"{base_name}.en.srt",
                    export_caption_cues(cues, "srt"),
                )
            )
        if include_ass:
            requested.append(
                (
                    "ass",
                    f"{base_name}.styled.ass",
                    export_ass_caption_cues(
                        cues,
                        project.subtitle_style,
                        clip_styles,
                    ),
                )
            )
        subtitle_outputs = []
        for kind, output_name, content in requested:
            output = export_dir / output_name
            output.unlink(missing_ok=True)
            created_outputs.append(output)
            output.write_text(content, encoding="utf-8")
            subtitle_outputs.append(
                VideoExportOutput(
                    clip_id=clip.clip_id,
                    title=clip.title,
                    start_ms=clip.start_ms,
                    end_ms=clip.end_ms,
                    output_name=output_name,
                    output_url=(
                        f"/api/projects/{project_id}/video-exports/"
                        f"{quote(output_name)}"
                    ),
                    kind=kind,
                )
            )
        return subtitle_outputs

    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)

        gpu_encoder = (
            available_gpu_video_encoder(ffmpeg)
            if include_video and encoder == "gpu"
            else None
        )
        codec = gpu_encoder[0] if gpu_encoder else "libx264"
        using_gpu = gpu_encoder is not None
        job.encoder_name = (
            gpu_encoder[1]
            if gpu_encoder
            else "CPU (libx264)"
            if include_video
            else "Subtitle files only"
        )
        job.stage = "exporting_video"
        job.progress = 0
        job.processed_duration_ms = 0
        job.clip_id = (
            export_clips[0].clip_id if len(export_clips) == 1 else None
        )
        job.output_folder = str(export_dir.resolve())
        job.outputs = []
        _checkpoint_job(store, job)
        total_duration_ms = max(
            1,
            sum(clip.end_ms - clip.start_ms for clip in export_clips),
        )
        completed_ms = 0
        outputs: list[VideoExportOutput] = []
        if not include_video:
            for clip_index, clip in enumerate(export_clips, start=1):
                _checkpoint_job(store, job)
                base_name = (
                    f"{clip_index:02d}-{safe_clip_title(clip)}-"
                    f"{job.job_id[-6:]}"
                )
                outputs.extend(write_subtitle_outputs(clip, base_name))
                completed_ms += max(1, clip.end_ms - clip.start_ms)
                job.outputs = outputs.copy()
                job.processed_duration_ms = completed_ms
                job.progress = min(0.98, completed_ms / total_duration_ms)
                _checkpoint_job(store, job)

            for clip in export_clips:
                if clip.render_queued:
                    store.save_clip(
                        project_id,
                        clip.model_copy(update={"render_queued": False}),
                    )
            job.stage = "video_exported"
            job.progress = 1
            job.processed_duration_ms = total_duration_ms
            job.outputs = outputs
            job.output_name = outputs[0].output_name if outputs else None
            job.output_url = outputs[0].output_url if outputs else None
            store.save_job(job)
            return

        for clip_index, clip in enumerate(export_clips, start=1):
            _checkpoint_job(store, job)
            clip_duration_ms = max(1, clip.end_ms - clip.start_ms)
            clip_work_dir = work_dir / f"clip-{clip_index:03d}"
            clip_work_dir.mkdir(parents=True)
            for font in (bundle_root() / "dist" / "assets").glob(
                "*Pretendard*.woff2"
            ):
                shutil.copy2(font, clip_work_dir / font.name)

            safe_title = safe_clip_title(clip)
            output_name = (
                f"{clip_index:02d}-{safe_title}-"
                f"{resolution}-{job.job_id[-6:]}.mp4"
            )
            output = export_dir / output_name
            output.unlink(missing_ok=True)
            created_outputs.append(output)

            part_names: list[str] = []
            for part_index, local_start_ms in enumerate(
                range(0, clip_duration_ms, chunk_ms)
            ):
                _checkpoint_job(store, job)
                source_start_ms = clip.start_ms + local_start_ms
                duration_ms = min(
                    chunk_ms,
                    clip_duration_ms - local_start_ms,
                )
                part_name = f"part-{part_index:05d}.mp4"
                part_names.append(part_name)
                part_path = clip_work_dir / part_name
                local_cues = []
                for cue in track.cues:
                    overlap_start = max(source_start_ms, cue.start_ms)
                    overlap_end = min(
                        source_start_ms + duration_ms,
                        cue.end_ms,
                    )
                    if overlap_end <= overlap_start:
                        continue
                    local_cues.append(
                        cue.model_copy(
                            update={
                                "start_ms": overlap_start - source_start_ms,
                                "end_ms": overlap_end - source_start_ms,
                            }
                        )
                    )
                (clip_work_dir / "chunk.ass").write_text(
                    export_ass_caption_cues(
                        local_cues,
                        project.subtitle_style,
                        clip_styles,
                    ),
                    encoding="utf-8",
                )
                command = [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-stats_period",
                    "0.25",
                    "-ss",
                    f"{source_start_ms / 1000:.3f}",
                    "-t",
                    f"{duration_ms / 1000:.3f}",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-vf",
                    video_export_filter(resolution),
                    *video_encoder_args(codec, quality),
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "320k",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    part_name,
                ]
                _run_video_chunk(
                    store,
                    job,
                    command,
                    clip_work_dir,
                    completed_ms + local_start_ms,
                    duration_ms,
                    total_duration_ms,
                )
                if not part_path.is_file():
                    raise RuntimeError(
                        "FFmpeg did not create an export segment"
                    )

            concat_file = clip_work_dir / "parts.txt"
            concat_file.write_text(
                "".join(f"file '{name}'\n" for name in part_names),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                cwd=clip_work_dir,
                capture_output=True,
                text=True,
                check=True,
                **hidden_subprocess_kwargs(),
            )
            outputs.append(
                VideoExportOutput(
                    clip_id=clip.clip_id,
                    title=clip.title,
                    start_ms=clip.start_ms,
                    end_ms=clip.end_ms,
                    output_name=output_name,
                    output_url=(
                        f"/api/projects/{project_id}/video-exports/"
                        f"{quote(output_name)}"
                    ),
                    kind="video",
                )
            )
            outputs.extend(
                write_subtitle_outputs(
                    clip,
                    Path(output_name).stem,
                )
            )
            completed_ms += clip_duration_ms
            job.outputs = outputs.copy()
            job.processed_duration_ms = completed_ms
            job.progress = min(0.98, completed_ms / total_duration_ms)
            _checkpoint_job(store, job)

        _checkpoint_job(store, job)
        for clip in export_clips:
            if clip.render_queued:
                store.save_clip(
                    project_id,
                    clip.model_copy(update={"render_queued": False}),
                )
        job.stage = "video_exported"
        job.progress = 1
        job.processed_duration_ms = total_duration_ms
        job.outputs = outputs
        job.output_name = outputs[0].output_name if outputs else None
        job.output_url = outputs[0].output_url if outputs else None
        store.save_job(job)
    except JobCancelled:
        for output in created_outputs:
            output.unlink(missing_ok=True)
    except Exception as exc:
        for output in created_outputs:
            output.unlink(missing_ok=True)
        if using_gpu:
            shutil.rmtree(work_dir, ignore_errors=True)
            job.stage = "queued"
            job.progress = 0
            job.processed_duration_ms = 0
            job.error = None
            job.encoder_name = "CPU (libx264 fallback)"
            job.outputs = []
            store.save_job(job)
            run_video_export(
                store,
                project_id,
                job_id,
                resolution,
                quality,
                "cpu",
                clip_ids,
                include_video,
                include_srt,
                include_ass,
            )
            recovered = Job.model_validate(store.get("job", job_id))
            if recovered.stage == "video_exported":
                recovered.encoder_name = "CPU (libx264 fallback)"
                store.save_job(recovered)
            return
        job.stage = "failed"
        job.error = f"Video export failed: {exc}"
        job.paused = False
        store.save_job(job)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
