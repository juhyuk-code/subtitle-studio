import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .clips import parse_timestamp_clips
from .desktop_paths import bundled_binary, user_data_root
from .models import (
    GlossaryEntry,
    Job,
    OpenRouterSettingsStatus,
    OpenRouterSettingsUpdate,
    Project,
    ProjectCreate,
    Segment,
    SegmentPatch,
    TimestampClip,
    TimestampClipPatch,
    TimestampImport,
)
from .services import (
    configured_value,
    export_subtitles,
    media_duration_ms,
    new_job,
    normalize_audio,
    run_language_stage,
    run_transcription,
    safe_filename,
    save_upload,
    whisper_available,
)
from .store import Store

load_dotenv()

ACTIVE_JOB_STAGES = {
    "queued",
    "preparing_model",
    "transcribing",
    "correcting_pass_1",
    "correcting_pass_2",
    "translating",
}


def create_app(
    data_root: Path | None = None, static_root: Path | None = None
) -> FastAPI:
    root = data_root or Path(os.environ.get("SUBTITLE_STUDIO_DATA", "./data"))
    store = Store(root)
    app = FastAPI(title="Subtitle Studio API", version="0.1.0")
    app.state.store = store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/projects", response_model=Project, status_code=201)
    def create_project(request: ProjectCreate):
        project = Project.create(request)
        store.save_project(project)
        return project

    @app.get("/api/projects", response_model=list[Project])
    def list_projects():
        return [Project.model_validate(item) for item in store.list("project")]

    @app.get("/api/projects/{project_id}", response_model=Project)
    def get_project(project_id: str):
        item = store.get("project", project_id)
        if not item:
            raise HTTPException(404, "Project not found")
        return Project.model_validate(item)

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        store.delete_project(project_id)
        project_dir = store.media_root / project_id
        if project_dir.exists():
            shutil.rmtree(project_dir)

    @app.get("/api/projects/{project_id}/segments", response_model=list[Segment])
    def list_segments(project_id: str):
        return [Segment.model_validate(item) for item in store.list("segment", project_id)]

    @app.get(
        "/api/projects/{project_id}/clips", response_model=list[TimestampClip]
    )
    def list_clips(project_id: str):
        return [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]

    @app.put(
        "/api/projects/{project_id}/clips", response_model=list[TimestampClip]
    )
    def import_clips(project_id: str, request: TimestampImport):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(project_data)
        try:
            clips = parse_timestamp_clips(request.text, project.duration_ms)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        store.delete_kind("clip", project_id)
        for clip in clips:
            store.save_clip(project_id, clip)
        return clips

    @app.patch(
        "/api/projects/{project_id}/clips/{clip_id}",
        response_model=TimestampClip,
    )
    def update_clip_selection(
        project_id: str, clip_id: str, patch: TimestampClipPatch
    ):
        item = next(
            (
                candidate
                for candidate in store.list("clip", project_id)
                if candidate.get("clip_id") == clip_id
            ),
            None,
        )
        if not item:
            raise HTTPException(404, "Timestamp clip not found")
        clip = TimestampClip.model_validate(item).model_copy(
            update={"selected": patch.selected}
        )
        store.save_clip(project_id, clip)
        return clip

    @app.patch(
        "/api/projects/{project_id}/segments/{segment_id}",
        response_model=Segment,
    )
    def update_segment(project_id: str, segment_id: str, patch: SegmentPatch):
        item = store.get("segment", segment_id)
        if not item:
            raise HTTPException(404, "Segment not found")
        segment = Segment.model_validate(item)
        updates = patch.model_dump(exclude_unset=True)
        if (
            "start_ms" in updates
            and "end_ms" in updates
            and updates["end_ms"] <= updates["start_ms"]
        ):
            raise HTTPException(422, "End time must follow start time")
        updated = segment.model_copy(update={**updates, "status": "user_edited"})
        store.save_segment(project_id, updated)
        return updated

    @app.post("/api/projects/{project_id}/media", response_model=Project)
    async def upload_media(project_id: str, media: UploadFile = File(...)):
        data = store.get("project", project_id)
        if not data:
            raise HTTPException(404, "Project not found")
        filename = safe_filename(media.filename or "source")
        project_dir = store.media_root / project_id
        source = project_dir / filename
        media_hash = await save_upload(media, source)
        duration = media_duration_ms(source)
        try:
            normalize_audio(source, project_dir / "normalized.wav")
        except Exception as exc:
            source.unlink(missing_ok=True)
            raise HTTPException(422, f"Audio extraction failed: {exc}") from exc
        project = Project.model_validate(data).model_copy(
            update={
                "media_name": filename,
                "media_hash": media_hash,
                "media_url": f"/media/{project_id}/{filename}",
                "duration_ms": duration,
                "status": "media_ready",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.save_project(project)
        return project

    @app.get("/media/{project_id}/{filename}")
    def project_media(project_id: str, filename: str):
        safe = safe_filename(filename)
        path = store.media_root / project_id / safe
        if not path.is_file():
            raise HTTPException(404, "Media not found")
        return FileResponse(path)

    @app.post("/api/projects/{project_id}/transcribe", response_model=Job, status_code=202)
    def transcribe(
        project_id: str, background: BackgroundTasks, model: str = "small"
    ):
        project = store.get("project", project_id)
        if not project or not project.get("media_name"):
            raise HTTPException(409, "Upload media before transcription")
        if any(
            item.get("stage") in {"queued", "preparing_model", "transcribing"}
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Transcription is already running")
        all_clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        selected_clips = [clip for clip in all_clips if clip.selected]
        if all_clips and not selected_clips:
            raise HTTPException(409, "Select at least one timestamp clip")
        job = new_job(project_id, "queued")
        store.save_job(job)
        background.add_task(
            run_transcription,
            store,
            project_id,
            job.job_id,
            model,
            selected_clips,
        )
        return job

    @app.get("/api/projects/{project_id}/jobs/active", response_model=Job | None)
    def active_project_job(project_id: str):
        return next(
            (
                Job.model_validate(item)
                for item in reversed(store.list("job", project_id))
                if item.get("stage") in ACTIVE_JOB_STAGES
                and not item.get("cancelled", False)
            ),
            None,
        )

    def start_language_job(
        project_id: str, stage: str, background: BackgroundTasks
    ) -> Job:
        if not store.list("segment", project_id):
            raise HTTPException(409, "Transcribe the project first")
        if not configured_value("OPENROUTER_API_KEY", store):
            raise HTTPException(
                409, "Connect OpenRouter from the app settings before running AI stages"
            )
        all_clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        selected_clips = [clip for clip in all_clips if clip.selected]
        if all_clips and not selected_clips:
            raise HTTPException(409, "Select at least one timestamp clip")
        job = new_job(project_id, "queued")
        store.save_job(job)
        background.add_task(
            lambda: asyncio.run(
                run_language_stage(
                    store, project_id, job.job_id, stage, selected_clips
                )
            )
        )
        return job

    @app.post("/api/projects/{project_id}/correct/pass-1", response_model=Job, status_code=202)
    def correct_pass_1(project_id: str, background: BackgroundTasks):
        return start_language_job(project_id, "correcting_pass_1", background)

    @app.post("/api/projects/{project_id}/correct/pass-2", response_model=Job, status_code=202)
    def correct_pass_2(project_id: str, background: BackgroundTasks):
        return start_language_job(project_id, "correcting_pass_2", background)

    @app.post("/api/projects/{project_id}/translate", response_model=Job, status_code=202)
    def translate(project_id: str, background: BackgroundTasks):
        return start_language_job(project_id, "translating", background)

    @app.get("/api/jobs/{job_id}", response_model=Job)
    def get_job(job_id: str):
        data = store.get("job", job_id)
        if not data:
            raise HTTPException(404, "Job not found")
        return Job.model_validate(data)

    @app.post("/api/jobs/{job_id}/cancel", response_model=Job)
    def cancel_job(job_id: str):
        data = store.get("job", job_id)
        if not data:
            raise HTTPException(404, "Job not found")
        job = Job.model_validate(data).model_copy(
            update={"cancelled": True, "stage": "cancelled"}
        )
        store.save_job(job)
        return job

    @app.get("/api/projects/{project_id}/glossary", response_model=list[GlossaryEntry])
    def list_glossary(project_id: str):
        return [
            GlossaryEntry.model_validate(item)
            for item in store.list("glossary", project_id)
        ]

    @app.post(
        "/api/projects/{project_id}/glossary",
        response_model=GlossaryEntry,
        status_code=201,
    )
    def add_glossary(project_id: str, entry: GlossaryEntry):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        store.save_glossary(project_id, entry)
        return entry

    @app.get("/api/projects/{project_id}/export/{format_name}")
    def export(project_id: str, format_name: str, language: str = "en", bilingual: bool = False):
        if format_name not in {"srt", "vtt", "txt", "json"}:
            raise HTTPException(404, "Unknown export format")
        segments = store.list("segment", project_id)
        if format_name == "json":
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {
                    "project": store.get("project", project_id),
                    "segments": segments,
                    "glossary": store.list("glossary", project_id),
                },
                headers={"Content-Disposition": f'attachment; filename="{project_id}.json"'},
            )
        if format_name == "txt":
            values = []
            for item in segments:
                segment = Segment.model_validate(item)
                values.append(
                    segment.english
                    if language == "en"
                    else segment.pass_2_korean or segment.pass_1_korean or segment.raw_korean
                )
            content = "\n\n".join(values) + "\n"
        else:
            content = export_subtitles(segments, language, format_name, bilingual)
        suffix = format_name
        return PlainTextResponse(
            content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{project_id}-{language}.{suffix}"'
            },
        )

    @app.get("/api/runtime")
    def runtime_status():
        default_model = configured_value(
            "OPENROUTER_MODEL", store, "google/gemini-3.1-flash-lite"
        )
        return {
            "ffmpeg": Path(bundled_binary("ffmpeg")).is_file()
            or bool(shutil.which("ffmpeg")),
            "whisper": whisper_available(),
            "llm_provider": "OpenRouter",
            "openrouter_configured": bool(configured_value("OPENROUTER_API_KEY", store)),
            "correction_model": configured_value(
                "OPENROUTER_CORRECTION_MODEL", store, default_model
            ),
            "translation_model": configured_value(
                "OPENROUTER_TRANSLATION_MODEL", store, default_model
            ),
            "privacy": "Media and Whisper stay local; transcript text is sent to OpenRouter.",
        }

    def openrouter_status() -> OpenRouterSettingsStatus:
        default_model = configured_value(
            "OPENROUTER_MODEL", store, "google/gemini-3.1-flash-lite"
        )
        return OpenRouterSettingsStatus(
            openrouter_configured=bool(
                configured_value("OPENROUTER_API_KEY", store)
            ),
            correction_model=configured_value(
                "OPENROUTER_CORRECTION_MODEL", store, default_model
            ),
            translation_model=configured_value(
                "OPENROUTER_TRANSLATION_MODEL", store, default_model
            ),
        )

    @app.get(
        "/api/settings/openrouter", response_model=OpenRouterSettingsStatus
    )
    def get_openrouter_settings():
        return openrouter_status()

    @app.put(
        "/api/settings/openrouter", response_model=OpenRouterSettingsStatus
    )
    def update_openrouter_settings(settings: OpenRouterSettingsUpdate):
        if settings.api_key is not None:
            store.save_setting(
                "OPENROUTER_API_KEY", settings.api_key.get_secret_value().strip()
            )
        store.save_setting(
            "OPENROUTER_CORRECTION_MODEL", settings.correction_model.strip()
        )
        store.save_setting(
            "OPENROUTER_TRANSLATION_MODEL", settings.translation_model.strip()
        )
        return openrouter_status()

    if static_root and static_root.is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="desktop")

    return app


app = create_app(
    user_data_root() if getattr(sys, "frozen", False) else None
)
