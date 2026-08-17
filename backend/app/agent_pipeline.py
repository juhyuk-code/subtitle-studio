"""One-shot agent orchestration: video + timestamps -> clips (+ captions).

This drives the existing pipeline services in-process so an agent can hand the
app a video file and a list of timestamps and receive finished, captioned clip
files back — without choreographing the individual REST endpoints.

The heavy lifting is reused verbatim:
  * ``run_english_pipeline``  -> diarize, transcribe, correct, translate
  * ``generate_project_caption_track`` -> build the burn-in caption track
  * ``run_video_export``      -> cut + burn + encode each clip with ffmpeg
"""

from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from .models import (
    AgentClipRequest,
    AgentClipResult,
    CaptionGenerationRequest,
    Job,
    NavigationMarker,
    Project,
    ProjectCreate,
    TimestampClip,
)
from .services import (
    generate_project_caption_track,
    media_duration_ms,
    new_job,
    normalize_audio,
    prepare_diarization_audio,
    run_english_pipeline,
    run_video_export,
    safe_filename,
    save_upload,
)
from .store import Store


def _wait_for_job(
    store: Store, job_id: str, timeout_s: float = 3600, poll_s: float = 1.0
) -> Job:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = Job.model_validate(store.get("job", job_id))
        if job.stage in {"video_exported"} or job.pipeline_completed:
            return job
        if job.stage in {"failed", "cancelled"}:
            raise RuntimeError(job.error or f"Job {job.stage}")
        time.sleep(poll_s)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout_s:.0f}s")


def _ingest_media(store: Store, project: Project, media_path: Path) -> Project:
    """Copy a local video file into the project and extract analysis audio."""
    project_dir = store.media_root / project.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(media_path.name)
    source = project_dir / filename
    shutil.copyfile(media_path, source)

    duration = media_duration_ms(source)
    normalize_audio(source, project_dir / "normalized.wav")
    prepare_diarization_audio(source, project_dir / "diarization.wav")

    updated = project.model_copy(
        update={
            "media_name": filename,
            "media_url": f"/media/{project.project_id}/{filename}",
            "duration_ms": duration,
            "status": "media_ready",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    store.save_project(updated)
    return updated


def _create_clips(
    store: Store, project: Project, request: AgentClipRequest
) -> list[TimestampClip]:
    clips: list[TimestampClip] = []
    for index, spec in enumerate(request.clips):
        if spec.end_ms <= spec.start_ms:
            raise ValueError(
                f"Clip {index + 1}: end must be after start "
                f"({spec.start_ms} >= {spec.end_ms})"
            )
        if spec.end_ms > project.duration_ms:
            raise ValueError(
                f"Clip {index + 1}: end {spec.end_ms}ms exceeds media "
                f"duration {project.duration_ms}ms"
            )
        clip = TimestampClip(
            clip_id=f"clip_{uuid4().hex[:12]}",
            start_ms=spec.start_ms,
            end_ms=spec.end_ms,
            title=spec.title or f"Clip {index + 1}",
            selected=True,
            render_queued=True,
            subtitle_style=project.subtitle_style,
        )
        store.save_clip(project.project_id, clip)
        clips.append(clip)
    return clips


async def clip_from_timestamps(
    store: Store,
    media_path: Path,
    request: AgentClipRequest,
    *,
    expected_speaker_count: int | None = None,
    job_timeout_s: float = 7200,
) -> tuple[Project, list[AgentClipResult]]:
    """Run the full video -> captioned clips pipeline. Returns output paths."""
    project = Project.create(ProjectCreate(name=request.project_name))
    store.save_project(project)
    project = _ingest_media(store, project, media_path)
    clips = _create_clips(store, project, request)

    # 1) diarize -> transcribe -> correct -> translate
    pipeline_job = new_job(project.project_id, "queued").model_copy(
        update={
            "pipeline": True,
            "pipeline_step": 1,
            "pipeline_total": 5,
            "pipeline_completed": False,
            "overall_progress": 0,
        }
    )
    store.save_job(pipeline_job)
    await run_english_pipeline(
        store,
        project.project_id,
        pipeline_job.job_id,
        clips,
        expected_speaker_count,
    )
    finished = Job.model_validate(store.get("job", pipeline_job.job_id))
    if finished.stage == "failed" or not finished.pipeline_completed:
        raise RuntimeError(finished.error or "English pipeline did not complete")

    # 2) build the caption track used for burn-in
    project = Project.model_validate(store.get("project", project.project_id))
    clips = [TimestampClip.model_validate(c) for c in store.list("clip", project.project_id)]
    track = generate_project_caption_track(
        store.list("segment", project.project_id),
        "en",
        clips,
        project.subtitle_style,
    )
    store.save_caption_track(project.project_id, track)

    # 3) cut + burn + encode
    export_job = new_job(project.project_id, "exporting_video")
    store.save_job(export_job)
    run_video_export(
        store,
        project.project_id,
        export_job.job_id,
        request.resolution,
        request.quality,
        request.encoder,
        [c.clip_id for c in clips],
        True,
        False,
        False,
    )
    export_job = Job.model_validate(store.get("job", export_job.job_id))
    if export_job.stage != "video_exported":
        raise RuntimeError(export_job.error or "Video export failed")

    outputs_by_clip = {o.clip_id: o for o in export_job.outputs if o.kind == "video"}
    results: list[AgentClipResult] = []
    for clip in clips:
        output = outputs_by_clip.get(clip.clip_id)
        output_path = (
            str(store.video_export_root / output.output_name) if output else None
        )
        results.append(
            AgentClipResult(
                clip_id=clip.clip_id,
                title=clip.title,
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
                output_name=output.output_name if output else None,
                output_url=output.output_url if output else None,
                output_path=output_path,
            )
        )
    return project, results


def _derive_clips_from_markers(
    store: Store, project: Project
) -> list[TimestampClip]:
    """Build one clip per timestamp: marker[i] -> marker[i+1] (last -> end)."""
    markers = sorted(
        (
            NavigationMarker.model_validate(item)
            for item in store.list("marker", project.project_id)
        ),
        key=lambda marker: marker.timestamp_ms,
    )
    if not markers:
        raise ValueError(
            "This project has no timestamps or clips. Import timestamps first."
        )
    clips: list[TimestampClip] = []
    for index, marker in enumerate(markers):
        start_ms = marker.timestamp_ms
        end_ms = (
            markers[index + 1].timestamp_ms
            if index + 1 < len(markers)
            else project.duration_ms
        )
        if end_ms <= start_ms:
            continue
        clip = TimestampClip(
            clip_id=f"clip_{uuid4().hex[:12]}",
            navigation_marker_id=marker.marker_id,
            start_ms=start_ms,
            end_ms=end_ms,
            title=marker.title,
            selected=True,
            render_queued=True,
            subtitle_style=project.subtitle_style,
        )
        store.save_clip(project.project_id, clip)
        clips.append(clip)
    return clips


async def clip_everything(
    store: Store,
    project_id: str,
    *,
    resolution: str = "1080p",
    quality: str = "maximum",
    encoder: str = "gpu",
    expected_speaker_count: int | None = None,
) -> tuple[Project, list[AgentClipResult]]:
    """Clip every timestamp in an existing project: full pipeline + export.

    Reuses the same in-process services as ``clip_from_timestamps`` but operates
    on an already-created project (the one the user is currently inside).
    """
    project_data = store.get("project", project_id)
    if not project_data:
        raise ValueError("Project not found")
    project = Project.model_validate(project_data)
    if not project.media_name:
        raise ValueError("Upload media to this project before clipping.")

    clips = [
        TimestampClip.model_validate(item)
        for item in store.list("clip", project_id)
    ]
    if not clips:
        clips = _derive_clips_from_markers(store, project)

    # Queue every clip for rendering.
    clips = []
    for item in store.list("clip", project_id):
        clip = TimestampClip.model_validate(item)
        if not clip.render_queued:
            clip = clip.model_copy(update={"render_queued": True})
            store.save_clip(project_id, clip)
        clips.append(clip)
    if not clips:
        raise ValueError("No clips to render in this project.")

    # 1) diarize -> transcribe -> correct -> translate (skips completed stages)
    pipeline_job = new_job(project_id, "queued").model_copy(
        update={
            "pipeline": True,
            "pipeline_step": 1,
            "pipeline_total": 5,
            "pipeline_completed": False,
            "overall_progress": 0,
        }
    )
    store.save_job(pipeline_job)
    await run_english_pipeline(
        store,
        project_id,
        pipeline_job.job_id,
        clips,
        expected_speaker_count,
    )
    finished = Job.model_validate(store.get("job", pipeline_job.job_id))
    if finished.stage == "failed" or not finished.pipeline_completed:
        raise RuntimeError(finished.error or "English pipeline did not complete")

    # 2) regenerate the burn-in caption track
    project = Project.model_validate(store.get("project", project_id))
    track = generate_project_caption_track(
        store.list("segment", project_id),
        "en",
        clips,
        project.subtitle_style,
    )
    store.save_caption_track(project_id, track)

    # 3) cut + burn + encode every clip
    export_job = new_job(project_id, "exporting_video")
    store.save_job(export_job)
    run_video_export(
        store,
        project_id,
        export_job.job_id,
        resolution,
        quality,
        encoder,
        [c.clip_id for c in clips],
        True,
        False,
        False,
    )
    export_job = Job.model_validate(store.get("job", export_job.job_id))
    if export_job.stage != "video_exported":
        raise RuntimeError(export_job.error or "Video export failed")

    outputs_by_clip = {o.clip_id: o for o in export_job.outputs if o.kind == "video"}
    results: list[AgentClipResult] = []
    for clip in clips:
        output = outputs_by_clip.get(clip.clip_id)
        output_path = (
            str(store.video_export_root / output.output_name) if output else None
        )
        results.append(
            AgentClipResult(
                clip_id=clip.clip_id,
                title=clip.title,
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
                output_name=output.output_name if output else None,
                output_url=output.output_url if output else None,
                output_path=output_path,
            )
        )
    return project, results
