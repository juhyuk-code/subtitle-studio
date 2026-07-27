import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile

from .clips import selected_clip_ranges
from .desktop_paths import bundled_binary, model_cache_root
from .models import GlossaryEntry, Job, Project, Segment, TimestampClip, Word
from .store import Store

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".aac"}

CORRECTION_PROMPT = """You are correcting Korean automatic speech recognition output.
Recover what was actually spoken; never rewrite, summarize, sanitize, or translate.
Preserve casual speech, slang, profanity, repetition, and unfinished sentences.
Use the glossary and nearby dialogue. Do not add facts. Keep uncertain text and flag it.
Return only JSON: {"corrected_segments":[{"segment_id":"...","corrected_korean":"...",
"change_reason":["spacing"],"confidence":0.9,"uncertain_phrases":[]}]}"""

CONSISTENCY_PROMPT = """Review this Korean podcast transcript episode-wide.
Standardize names and terminology using the glossary and later context without rewriting speech.
Never change locked segments. Return only JSON: {"corrected_segments":[{"segment_id":"...",
"corrected_korean":"...","change_reason":["terminology"],"confidence":0.9,
"uncertain_phrases":[]}]}"""

TRANSLATION_PROMPT = """Translate corrected Korean podcast dialogue into natural conversational English.
Preserve meaning, intention, emotion, sarcasm, uncertainty, interruptions, terminology, and profanity.
Use contractions naturally. Do not add explanations or create subtitle line breaks.
Return only JSON: {"translations":[{"segment_id":"...","english":"...","warnings":[]}]}"""


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
    default_model = configured_value(
        "OPENROUTER_MODEL", store, "google/gemini-3.1-flash-lite"
    )
    variable = (
        "OPENROUTER_TRANSLATION_MODEL"
        if stage == "translating"
        else "OPENROUTER_CORRECTION_MODEL"
    )
    return configured_value(variable, store, default_model)


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
        )
        return round(float(result.stdout.strip()) * 1000)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise HTTPException(422, "The media file is corrupted or unreadable.") from exc


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
    )


def new_job(project_id: str, stage: str) -> Job:
    return Job(job_id=f"job_{uuid4().hex[:12]}", project_id=project_id, stage=stage)


def _update_project(store: Store, project_id: str, **updates: Any) -> Project:
    data = store.get("project", project_id)
    if not data:
        raise RuntimeError("Project not found")
    project = Project.model_validate(data).model_copy(
        update={**updates, "updated_at": datetime.now(timezone.utc).isoformat()}
    )
    store.save_project(project)
    return project


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
    output_dir.mkdir(exist_ok=True)
    audio_path = project_dir / "normalized.wav"
    try:
        faster_whisper_available = (
            importlib.util.find_spec("faster_whisper") is not None
        )
        job.stage = "preparing_model" if faster_whisper_available else "transcribing"
        job.progress = 0.03 if faster_whisper_available else 0.08
        store.save_job(job)
        prompt = ", ".join(
            entry["canonical_korean"]
            for entry in store.list("glossary", project_id)
            if entry.get("canonical_korean")
        )
        if faster_whisper_available:
            def model_ready() -> None:
                job.stage, job.progress = "transcribing", 0.08
                store.save_job(job)

            payload = _transcribe_with_faster_whisper(
                audio_path,
                model,
                prompt,
                model_ready,
                selected_clip_ranges(clips or []) or None,
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
            subprocess.run(command, check=True, capture_output=True, text=True)
            payload = json.loads(
                (output_dir / "normalized.json").read_text(encoding="utf-8")
            )
        payload_segments = payload.get("segments", [])
        store.delete_kind("segment", project_id)
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
            segment = Segment(
                segment_id=f"seg_{index + 1:06d}",
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
            )
            store.save_segment(project_id, segment)
            job.progress = 0.1 + (
                0.88 * (index + 1) / max(1, len(payload_segments))
            )
            job.processed_duration_ms = segment.end_ms
            store.save_job(job)
        job.stage, job.progress = "transcribed", 1
        _update_project(store, project_id, status="transcribed")
    except Exception as exc:
        job.stage, job.error = "failed", str(exc)
    store.save_job(job)


def _transcribe_with_faster_whisper(
    audio_path: Path,
    model_name: str,
    prompt: str,
    on_model_ready: Callable[[], None] | None = None,
    clip_timestamps: list[float] | None = None,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    cache = model_cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=str(cache),
    )
    if on_model_ready:
        on_model_ready()
    results, _ = model.transcribe(
        str(audio_path),
        language="ko",
        task="transcribe",
        word_timestamps=True,
        condition_on_previous_text=True,
        initial_prompt=prompt or None,
        clip_timestamps=clip_timestamps or "0",
    )
    segments = []
    for result in results:
        segments.append(
            {
                "start": result.start,
                "end": result.end,
                "text": result.text,
                "avg_logprob": result.avg_logprob,
                "no_speech_prob": result.no_speech_prob,
                "words": [
                    {
                        "word": word.word,
                        "start": word.start,
                        "end": word.end,
                        "probability": word.probability,
                    }
                    for word in (result.words or [])
                ],
            }
        )
    return {"segments": segments}


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
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            return _extract_json(response.json()["choices"][0]["message"]["content"])
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        messages = {
            401: "OpenRouter rejected the API key.",
            402: "OpenRouter credits are insufficient.",
            429: "OpenRouter rate limit reached. Retry after a short pause.",
        }
        raise RuntimeError(
            messages.get(status, f"OpenRouter request failed with status {status}.")
        ) from exc
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise RuntimeError(
            "OpenRouter is unavailable. Check the internet connection and retry."
        ) from exc


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
        "segments": segments,
    }


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
        store.save_job(job)
        result = await call_openrouter(
            store,
            stage,
            prompts[stage],
            _dialogue_payload(store, project_id, clips),
        )
        segments = {
            item["segment_id"]: Segment.model_validate(item)
            for item in store.list("segment", project_id)
        }
        rows = result.get(
            "translations" if stage == "translating" else "corrected_segments", []
        )
        for index, row in enumerate(rows):
            segment = segments.get(row.get("segment_id"))
            if not segment or segment.locked:
                continue
            if stage == "translating":
                segment.english = row.get("english", segment.english)
                segment.warnings = row.get("warnings", [])
                segment.status = "warning" if segment.warnings else "translated"
            else:
                field = "pass_1_korean" if stage == "correcting_pass_1" else "pass_2_korean"
                setattr(segment, field, row.get("corrected_korean", segment.raw_korean))
                if stage == "correcting_pass_2" and not segment.pass_2_korean:
                    segment.pass_2_korean = segment.pass_1_korean
                segment.change_reasons = row.get("change_reason", [])
                segment.warnings = [
                    f"Uncertain: {phrase}" for phrase in row.get("uncertain_phrases", [])
                ]
                segment.confidence = row.get("confidence", segment.confidence)
                segment.status = "warning" if segment.warnings else "corrected"
            store.save_segment(project_id, segment)
            job.progress = 0.1 + 0.9 * (index + 1) / max(1, len(rows))
            store.save_job(job)
        status = {
            "correcting_pass_1": "corrected_pass_1",
            "correcting_pass_2": "corrected",
            "translating": "translated",
        }[stage]
        job.stage, job.progress = status, 1
        job.warning_count = sum(
            len(item.get("warnings", [])) for item in store.list("segment", project_id)
        )
        _update_project(store, project_id, status=status)
    except Exception as exc:
        job.stage, job.error = "failed", str(exc)
    store.save_job(job)


def format_timestamp(ms: int, separator: str = ",") -> str:
    hours, remainder = divmod(max(0, ms), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def wrap_subtitle(text: str, width: int = 42) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines = [words[0]]
    for word in words[1:]:
        if len(lines[-1]) + len(word) + 1 <= width or len(lines) == 2:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return lines


def export_subtitles(
    segments: list[dict[str, Any]], language: str, format_name: str, bilingual: bool = False
) -> str:
    output: list[str] = []
    previous_end = 0
    for index, data in enumerate(segments, 1):
        segment = Segment.model_validate(data)
        start = max(previous_end, segment.start_ms)
        end = max(start + 80, segment.end_ms)
        korean = segment.pass_2_korean or segment.pass_1_korean or segment.raw_korean
        english = segment.english or korean
        if bilingual:
            lines = [korean, english]
        else:
            lines = wrap_subtitle(english if language == "en" else korean)
        if format_name == "vtt":
            output.append(
                f"{format_timestamp(start, '.')} --> {format_timestamp(end, '.')}\n"
                + "\n".join(lines)
            )
        else:
            output.append(
                f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n"
                + "\n".join(lines)
            )
        previous_end = end
    prefix = "WEBVTT\n\n" if format_name == "vtt" else ""
    return prefix + "\n\n".join(output) + "\n"
