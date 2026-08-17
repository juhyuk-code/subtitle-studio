import asyncio
import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .clips import parse_timestamp_markers
from .desktop_paths import bundled_binary, open_folder, user_data_root
from . import xpost
from . import xapi
from .agent_pipeline import clip_from_timestamps, clip_everything
from .models import (
    AppPreferences,
    AppPreferencesPatch,
    CaptionGenerationRequest,
    CaptionTrack,
    GlossaryEntry,
    Job,
    MediaTimelineInfo,
    NavigationMarker,
    OpenRouterModel,
    OpenRouterSettingsStatus,
    OpenRouterSettingsUpdate,
    PostCopy,
    PostCopyPatch,
    Project,
    ProjectCreate,
    ProjectSpeakerSettings,
    ProjectWorkspacePatch,
    ProjectWorkspaceState,
    Segment,
    SegmentPatch,
    Speaker,
    SpeakerDetectionSettingsStatus,
    SpeakerDetectionSettingsUpdate,
    SpeakerPatch,
    SubtitleStyle,
    SubtitleStylePatch,
    SubtitleStylePreset,
    SubtitleStylePresetCreate,
    TimestampClip,
    TimestampClipCreate,
    TimestampClipPatch,
    TimestampImport,
    VideoExportFolderStatus,
    VideoExportFolderUpdate,
    VideoExportRequest,
    VoiceProfile,
    VoiceProfileRecord,
    AgentClipRequest,
    AgentClipResult,
    ScheduledPost,
    ScheduledPostCreate,
    ScheduledPostPatch,
    XAccountSettings,
    XAccountSettingsStatus,
    XAccountSettingsUpdate,
)
from .services import (
    DEFAULT_DIARIZATION_MODEL,
    DEFAULT_WHISPER_MODEL,
    caption_source_signature,
    configured_value,
    diarization_available,
    export_ass_caption_cues,
    export_ass_subtitles,
    export_caption_cues,
    export_subtitles,
    extract_voice_embedding,
    fetch_openrouter_models,
    format_post_copy_quote_blocks,
    generate_caption_track,
    generate_project_caption_track,
    generate_post_copy,
    media_duration_ms,
    media_frame_rate,
    prepare_segments_for_clips,
    post_copy_source_signature,
    project_caption_source_signature,
    new_job,
    normalize_audio,
    openrouter_model_for_stage,
    prepare_diarization_audio,
    run_english_pipeline,
    run_language_stage,
    run_diarization,
    run_transcription,
    run_video_export,
    render_waveform_image,
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
    "preparing_diarization",
    "diarizing",
    "saving_speaker_turns",
    "correcting_pass_1",
    "correcting_pass_2",
    "translating",
    "exporting_video",
}


def job_is_active(item: dict | Job) -> bool:
    data = item.model_dump() if isinstance(item, Job) else item
    if data.get("cancelled", False):
        return False
    if (
        data.get("pipeline", False)
        and not data.get("pipeline_completed", False)
        and data.get("stage") not in {"failed", "cancelled"}
    ):
        return True
    return data.get("stage") in ACTIVE_JOB_STAGES


def _resolve_clip_video_path(
    store: "Store", project_id: str, clip_id: str | None
) -> str | None:
    """Find the exported MP4 for a clip so a scheduled post can attach it.

    Scans the project's completed export jobs (newest first) for the matching
    video output and returns its absolute on-disk path, or None when the clip
    has not been exported yet.
    """
    for item in reversed(store.list("job", project_id)):
        job = Job.model_validate(item)
        if job.stage != "video_exported":
            continue
        candidates = job.outputs
        if clip_id:
            candidates = [o for o in candidates if o.clip_id == clip_id]
        for output in candidates:
            if output.kind != "video":
                continue
            base = (
                Path(job.output_folder)
                if job.output_folder
                else store.video_export_root
            )
            path = base / output.output_name
            if path.is_file():
                return str(path)
    return None


PRETENDARD_DEFAULT_MIGRATION = "pretendard_default_migration_v1"


def create_app(
    data_root: Path | None = None,
    static_root: Path | None = None,
    video_export_root: Path | None = None,
) -> FastAPI:
    root = data_root or Path(os.environ.get("SUBTITLE_STUDIO_DATA", "./data"))
    store = Store(root, video_export_root=video_export_root)
    default_video_export_root = store.video_export_root
    saved_video_export_root = store.get_setting("VIDEO_EXPORT_ROOT")
    if saved_video_export_root:
        saved_path = Path(saved_video_export_root).expanduser()
        if saved_path.is_absolute():
            store.video_export_root = saved_path
    for item in store.list("job"):
        if not job_is_active(item):
            continue
        interrupted = Job.model_validate(item).model_copy(
            update={
                "stage": "failed",
                "paused": False,
                "error": (
                    "This task was interrupted when Subtitle Studio closed. "
                    "Start it again to continue from the last completed stage."
                ),
            }
        )
        store.save_job(interrupted)
    migrate_arial_default = (
        store.get_setting(PRETENDARD_DEFAULT_MIGRATION) != "complete"
    )
    for item in store.list("project"):
        updates = {}
        project = Project.model_validate(item)
        if (
            migrate_arial_default
            and project.subtitle_style.font_family == "Arial"
        ):
            updates["subtitle_style"] = project.subtitle_style.model_copy(
                update={"font_family": "Pretendard"}
            )
        if (
            item.get("media_name")
            and item.get("status") not in {"draft", "media_ready"}
            and not store.list("speaker_turn", item["project_id"])
        ):
            updates["status"] = "media_ready"
        if updates:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            legacy_project = project.model_copy(update=updates)
            store.save_project(legacy_project)
            project = legacy_project
        segment_clip_ids = {
            segment.get("clip_id")
            for segment in store.list("segment", project.project_id)
        }
        has_speaker_turns = bool(
            store.list("speaker_turn", project.project_id)
        )
        for clip_item in store.list("clip", project.project_id):
            clip_updates = {}
            clip = TimestampClip.model_validate(clip_item)
            if "opened" not in clip_item:
                clip_updates["opened"] = clip.clip_id in segment_clip_ids
            if "status" not in clip_item:
                clip_updates["status"] = (
                    project.status
                    if clip.clip_id in segment_clip_ids
                    else "speakers_detected"
                    if has_speaker_turns
                    else "media_ready"
                )
            if not clip_item.get("subtitle_style"):
                clip_updates["subtitle_style"] = project.subtitle_style
            if clip_updates:
                store.save_clip(
                    project.project_id,
                    clip.model_copy(update=clip_updates),
                )
        if not store.list("marker", project.project_id):
            for index, clip_item in enumerate(
                store.list("clip", project.project_id), start=1
            ):
                clip = TimestampClip.model_validate(clip_item)
                store.save_marker(
                    project.project_id,
                    NavigationMarker(
                        marker_id=f"marker_{index:03d}",
                        timestamp_ms=clip.start_ms,
                        title=clip.title,
                    ),
                )
        markers = [
            NavigationMarker.model_validate(marker)
            for marker in store.list("marker", project.project_id)
        ]
        claimed_marker_ids = {
            clip.get("navigation_marker_id")
            for clip in store.list("clip", project.project_id)
            if clip.get("navigation_marker_id")
        }
        for clip_item in store.list("clip", project.project_id):
            if clip_item.get("navigation_marker_id"):
                continue
            clip = TimestampClip.model_validate(clip_item)
            marker = next(
                (
                    candidate
                    for candidate in markers
                    if candidate.marker_id not in claimed_marker_ids
                    and candidate.title == clip.title
                ),
                None,
            ) or next(
                (
                    candidate
                    for candidate in markers
                    if candidate.marker_id not in claimed_marker_ids
                    and candidate.timestamp_ms == clip.start_ms
                ),
                None,
            )
            if marker:
                claimed_marker_ids.add(marker.marker_id)
                store.save_clip(
                    project.project_id,
                    clip.model_copy(
                        update={"navigation_marker_id": marker.marker_id}
                    ),
                )
    if migrate_arial_default:
        store.save_setting(PRETENDARD_DEFAULT_MIGRATION, "complete")
    app = FastAPI(title="Subtitle Studio API", version="0.1.0")
    app.state.store = store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def invalidate_audio_analysis(
        project_id: str,
        force: bool = False,
        preserve_speakers: bool = False,
    ) -> None:
        data = store.get("project", project_id)
        if not data or (
            not force and data.get("status") in {"draft", "media_ready"}
        ):
            return
        project = Project.model_validate(data).model_copy(
            update={
                "status": "media_ready",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.save_project(project)
        store.delete_kind("speaker_turn", project_id)
        store.delete_kind("segment", project_id)
        store.delete_kind("caption_track", project_id)
        if not preserve_speakers:
            store.delete_kind("speaker", project_id)
            for index, name in enumerate(project.speakers, start=1):
                store.save_speaker(
                    project_id,
                    Speaker(
                        speaker_id=f"SPEAKER_{index:02d}",
                        name=name,
                    ),
                )

    def reset_replaced_media_timeline(project_id: str) -> None:
        for kind in ("marker", "clip", "post_copy", "job"):
            store.delete_kind(kind, project_id)
        current_data = store.get("workspace", project_id)
        current = (
            ProjectWorkspaceState.model_validate(current_data)
            if current_data
            else ProjectWorkspaceState()
        )
        store.save_workspace(
            project_id,
            current.model_copy(
                update={
                    "active_clip_id": None,
                    "selected_segment_id": None,
                    "sidebar_tab": "timestamps",
                    "playhead_ms": 0,
                    "transcript_query": "",
                    "warning_only": False,
                    "timeline_zoom": 1,
                }
            ),
        )

    def invalidate_clip_transcript(project_id: str, clip_id: str) -> str:
        for segment in store.list("segment", project_id):
            if segment.get("clip_id") == clip_id:
                store.delete("segment", segment["segment_id"])
        store.delete_kind("caption_track", project_id)
        return (
            "speakers_detected"
            if store.list("speaker_turn", project_id)
            else "media_ready"
        )

    def processing_clips(
        project_id: str, clip_id: str | None
    ) -> list[TimestampClip]:
        clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        if clip_id:
            clip = next(
                (item for item in clips if item.clip_id == clip_id), None
            )
            if not clip:
                raise HTTPException(404, "Timestamp clip not found")
            return [clip]
        selected = [clip for clip in clips if clip.selected]
        if clips and not selected:
            raise HTTPException(409, "Select at least one timestamp clip")
        return selected

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/projects", response_model=Project, status_code=201)
    def create_project(request: ProjectCreate):
        project = Project.create(request)
        store.save_project(project)
        for index, name in enumerate(project.speakers, start=1):
            store.save_speaker(
                project.project_id,
                Speaker(
                    speaker_id=f"SPEAKER_{index:02d}",
                    name=name,
                ),
            )
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

    @app.get(
        "/api/projects/{project_id}/workspace",
        response_model=ProjectWorkspaceState,
    )
    def get_project_workspace(project_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        item = store.get("workspace", project_id)
        return (
            ProjectWorkspaceState.model_validate(item)
            if item
            else ProjectWorkspaceState()
        )

    @app.patch(
        "/api/projects/{project_id}/workspace",
        response_model=ProjectWorkspaceState,
    )
    def update_project_workspace(
        project_id: str, patch: ProjectWorkspacePatch
    ):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        current_data = store.get("workspace", project_id)
        current = (
            ProjectWorkspaceState.model_validate(current_data)
            if current_data
            else ProjectWorkspaceState()
        )
        updates = patch.model_dump(exclude_unset=True)
        project = Project.model_validate(project_data)
        if "playhead_ms" in updates and updates["playhead_ms"] is not None:
            updates["playhead_ms"] = min(
                updates["playhead_ms"], project.duration_ms
            )
        if updates.get("active_clip_id"):
            opened_ids = {
                item["clip_id"]
                for item in store.list("clip", project_id)
                if item.get("opened")
            }
            if updates["active_clip_id"] not in opened_ids:
                updates["active_clip_id"] = None
        if updates.get("selected_segment_id"):
            segment_ids = {
                item["segment_id"]
                for item in store.list("segment", project_id)
            }
            if updates["selected_segment_id"] not in segment_ids:
                updates["selected_segment_id"] = None
        workspace = current.model_copy(update=updates)
        store.save_workspace(project_id, workspace)
        return workspace

    @app.patch(
        "/api/projects/{project_id}/speaker-settings",
        response_model=Project,
    )
    def update_project_speaker_settings(
        project_id: str, settings: ProjectSpeakerSettings
    ):
        item = store.get("project", project_id)
        if not item:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(item).model_copy(
            update={
                "expected_speaker_count": (
                    settings.expected_speaker_count
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.save_project(project)
        if store.list("speaker_turn", project_id):
            invalidate_audio_analysis(project_id)
            project = Project.model_validate(
                store.get("project", project_id)
            )
        return project

    @app.patch(
        "/api/projects/{project_id}/subtitle-style",
        response_model=SubtitleStyle,
    )
    def update_project_subtitle_style(
        project_id: str, patch: SubtitleStylePatch
    ):
        item = store.get("project", project_id)
        if not item:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(item)
        style = project.subtitle_style.model_copy(
            update=patch.model_dump(exclude_none=True)
        )
        project = project.model_copy(
            update={
                "subtitle_style": style,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.save_project(project)
        return style

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(
                409, "Stop the current task before deleting this project"
            )
        store.delete_project(project_id)
        project_dir = store.media_root / project_id
        if project_dir.exists():
            shutil.rmtree(project_dir)

    @app.get("/api/projects/{project_id}/segments", response_model=list[Segment])
    def list_segments(project_id: str):
        segments = [
            Segment.model_validate(item)
            for item in store.list("segment", project_id)
        ]
        clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        if clips and any(segment.clip_id is None for segment in segments):
            prepared = prepare_segments_for_clips(segments, clips)
            if prepared != segments:
                store.delete_kind("segment", project_id)
                for segment in prepared:
                    store.save_segment(project_id, segment)
                segments = prepared
        return segments

    @app.get(
        "/api/projects/{project_id}/captions",
        response_model=CaptionTrack | None,
    )
    def get_caption_track(project_id: str, language: str = "en"):
        if language not in {"ko", "en"}:
            raise HTTPException(422, "Caption language must be ko or en")
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        track_data = store.get(
            "caption_track", f"{project_id}:{language}"
        )
        if not track_data:
            return None
        track = CaptionTrack.model_validate(track_data)
        segments = store.list("segment", project_id)
        project = Project.model_validate(project_data)
        clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        current_signature = project_caption_source_signature(
            segments,
            track.language,
            clips,
            project.subtitle_style,
        )
        return track.model_copy(
            update={
                "stale": track.source_signature != current_signature
            }
        )

    @app.post(
        "/api/projects/{project_id}/captions/regenerate",
        response_model=CaptionTrack,
    )
    def regenerate_captions(
        project_id: str, request: CaptionGenerationRequest
    ):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        segments = store.list("segment", project_id)
        if not segments:
            raise HTTPException(
                409, "Transcribe the media before generating captions"
            )
        project = Project.model_validate(project_data)
        clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        style = project.subtitle_style.model_copy(
            update={
                "max_words_per_line": request.max_words_per_line,
                "max_lines": request.max_lines,
            }
        )
        if request.clip_id:
            clip = next(
                (item for item in clips if item.clip_id == request.clip_id),
                None,
            )
            if not clip:
                raise HTTPException(404, "Timestamp clip not found")
            store.save_clip(
                project_id,
                clip.model_copy(
                    update={
                        "subtitle_style": (
                            clip.subtitle_style or project.subtitle_style
                        ).model_copy(
                            update={
                                "max_words_per_line": request.max_words_per_line,
                                "max_lines": request.max_lines,
                            }
                        )
                    }
                ),
            )
            clips = [
                TimestampClip.model_validate(item)
                for item in store.list("clip", project_id)
            ]
        else:
            project = project.model_copy(
                update={
                    "subtitle_style": style,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            store.save_project(project)
            for clip in clips:
                store.save_clip(
                    project_id,
                    clip.model_copy(
                        update={
                            "subtitle_style": (
                                clip.subtitle_style or style
                            ).model_copy(
                                update={
                                    "max_words_per_line": request.max_words_per_line,
                                    "max_lines": request.max_lines,
                                }
                            )
                        }
                    ),
                )
            clips = [
                TimestampClip.model_validate(item)
                for item in store.list("clip", project_id)
            ]
        track = generate_project_caption_track(
            segments, request.language, clips, project.subtitle_style
        )
        store.save_caption_track(project_id, track)
        return track

    @app.get(
        "/api/projects/{project_id}/clips", response_model=list[TimestampClip]
    )
    def list_clips(project_id: str):
        return [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]

    @app.get(
        "/api/projects/{project_id}/post-copies",
        response_model=list[PostCopy],
    )
    def list_post_copies(project_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        copies = []
        for item in store.list("post_copy", project_id):
            post_copy = PostCopy.model_validate(item)
            post_copy = post_copy.model_copy(
                update={"body": format_post_copy_quote_blocks(post_copy.body)}
            )
            current_signature = post_copy_source_signature(
                store, project_id, post_copy.clip_id
            )
            copies.append(
                post_copy.model_copy(
                    update={
                        "stale": (
                            not current_signature
                            or current_signature != post_copy.source_signature
                        )
                    }
                )
            )
        return copies

    @app.post(
        "/api/projects/{project_id}/post-copies/{clip_id}/generate",
        response_model=PostCopy,
    )
    async def create_post_copy(project_id: str, clip_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        if not any(
            item.get("clip_id") == clip_id
            for item in store.list("clip", project_id)
        ):
            raise HTTPException(404, "Clip not found")
        try:
            return await generate_post_copy(store, project_id, clip_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.patch(
        "/api/projects/{project_id}/post-copies/{clip_id}",
        response_model=PostCopy,
    )
    def update_post_copy(
        project_id: str, clip_id: str, patch: PostCopyPatch
    ):
        item = store.get("post_copy", f"{project_id}:{clip_id}")
        if not item:
            raise HTTPException(404, "Post copy not found")
        updates = patch.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(422, "No post copy changes were provided")
        post_copy = PostCopy.model_validate(item).model_copy(update=updates)
        store.save_post_copy(project_id, post_copy)
        current_signature = post_copy_source_signature(
            store, project_id, clip_id
        )
        return post_copy.model_copy(
            update={
                "stale": (
                    not current_signature
                    or current_signature != post_copy.source_signature
                )
            }
        )

    @app.post(
        "/api/projects/{project_id}/clips",
        response_model=TimestampClip,
        status_code=201,
    )
    def create_clip(project_id: str, request: TimestampClipCreate):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(project_data)
        if request.end_ms <= request.start_ms:
            raise HTTPException(422, "Clip end must be after clip start")
        if request.end_ms > project.duration_ms:
            raise HTTPException(422, "Clip end must be within the media")
        existing = store.list("clip", project_id)
        if request.navigation_marker_id:
            marker_ids = {
                item["marker_id"]
                for item in store.list("marker", project_id)
            }
            if request.navigation_marker_id not in marker_ids:
                raise HTTPException(404, "Navigation timestamp not found")
            if any(
                item.get("navigation_marker_id")
                == request.navigation_marker_id
                for item in existing
            ):
                raise HTTPException(
                    409, "This timestamp already has a transcript tab"
                )
        clip = TimestampClip(
            clip_id=f"clip_{uuid4().hex[:12]}",
            navigation_marker_id=request.navigation_marker_id,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            title=request.title or f"Clip {len(existing) + 1}",
            status=(
                "speakers_detected"
                if store.list("speaker_turn", project_id)
                else "media_ready"
            ),
            subtitle_style=project.subtitle_style,
        )
        store.save_clip(project_id, clip)
        return clip

    @app.get(
        "/api/projects/{project_id}/markers",
        response_model=list[NavigationMarker],
    )
    def list_markers(project_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        return [
            NavigationMarker.model_validate(item)
            for item in store.list("marker", project_id)
        ]

    @app.put(
        "/api/projects/{project_id}/markers",
        response_model=list[NavigationMarker],
    )
    def import_markers(project_id: str, request: TimestampImport):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(project_data)
        try:
            parsed_markers = parse_timestamp_markers(
                request.text, project.duration_ms
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        existing_markers = [
            NavigationMarker.model_validate(item)
            for item in store.list("marker", project_id)
        ]
        markers = []
        for parsed in parsed_markers:
            existing = next(
                (
                    marker
                    for marker in existing_markers
                    if marker.timestamp_ms == parsed.timestamp_ms
                    and marker.title == parsed.title
                ),
                None,
            ) or next(
                (
                    marker
                    for marker in existing_markers
                    if marker.timestamp_ms == parsed.timestamp_ms
                ),
                None,
            )
            markers.append(
                parsed.model_copy(
                    update={
                        "marker_id": (
                            existing.marker_id
                            if existing
                            else f"marker_{uuid4().hex[:12]}"
                        )
                    }
                )
            )
        store.delete_kind("marker", project_id)
        for marker in markers:
            store.save_marker(project_id, marker)
        marker_ids = {marker.marker_id for marker in markers}
        for clip_data in store.list("clip", project_id):
            marker_id = clip_data.get("navigation_marker_id")
            if marker_id and marker_id not in marker_ids:
                clip = TimestampClip.model_validate(clip_data)
                store.save_clip(
                    project_id,
                    clip.model_copy(
                        update={"navigation_marker_id": None}
                    ),
                )
        return markers

    @app.patch(
        "/api/projects/{project_id}/clips/{clip_id}",
        response_model=TimestampClip,
    )
    def update_clip(
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
            raise HTTPException(404, "Clip not found")
        updates = patch.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(422, "No clip changes were provided")
        current = TimestampClip.model_validate(item)
        start_ms = updates.get("start_ms", current.start_ms)
        end_ms = updates.get("end_ms", current.end_ms)
        project = Project.model_validate(store.get("project", project_id))
        if end_ms <= start_ms:
            raise HTTPException(422, "Clip end must be after clip start")
        if project.duration_ms > 0 and end_ms > project.duration_ms:
            raise HTTPException(422, "Clip end must be within the media")
        boundaries_changed = (
            start_ms != current.start_ms or end_ms != current.end_ms
        )
        if boundaries_changed and any(
            job_is_active(candidate)
            for candidate in store.list("job", project_id)
        ):
            raise HTTPException(
                409, "Stop the current task before changing clip boundaries"
            )
        if boundaries_changed:
            updates.update(
                {
                    "status": invalidate_clip_transcript(
                        project_id, clip_id
                    ),
                    "render_queued": False,
                }
            )
        clip = current.model_copy(update=updates)
        store.save_clip(project_id, clip)
        return clip

    @app.delete(
        "/api/projects/{project_id}/render-queue",
        response_model=list[TimestampClip],
    )
    def clear_render_queue(project_id: str):
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        clips = []
        for item in store.list("clip", project_id):
            clip = TimestampClip.model_validate(item)
            if clip.render_queued:
                clip = clip.model_copy(update={"render_queued": False})
                store.save_clip(project_id, clip)
            clips.append(clip)
        return clips

    @app.delete(
        "/api/projects/{project_id}/clips/{clip_id}",
        status_code=204,
    )
    def delete_clip(project_id: str, clip_id: str):
        item = next(
            (
                candidate
                for candidate in store.list("clip", project_id)
                if candidate.get("clip_id") == clip_id
            ),
            None,
        )
        if not item:
            raise HTTPException(404, "Clip not found")
        if any(
            job_is_active(candidate)
            for candidate in store.list("job", project_id)
        ):
            raise HTTPException(
                409, "Stop the current task before deleting a clip"
            )
        invalidate_clip_transcript(project_id, clip_id)
        store.delete("post_copy", f"{project_id}:{clip_id}")
        store.delete("clip", clip_id)

    @app.patch(
        "/api/projects/{project_id}/clips/{clip_id}/subtitle-style",
        response_model=TimestampClip,
    )
    def update_clip_subtitle_style(
        project_id: str, clip_id: str, patch: SubtitleStylePatch
    ):
        project_data = store.get("project", project_id)
        item = next(
            (
                candidate
                for candidate in store.list("clip", project_id)
                if candidate.get("clip_id") == clip_id
            ),
            None,
        )
        if not project_data or not item:
            raise HTTPException(404, "Timestamp clip not found")
        project = Project.model_validate(project_data)
        clip = TimestampClip.model_validate(item)
        style = (clip.subtitle_style or project.subtitle_style).model_copy(
            update=patch.model_dump(exclude_none=True)
        )
        updated = clip.model_copy(update={"subtitle_style": style})
        store.save_clip(project_id, updated)
        return updated

    @app.post(
        "/api/projects/{project_id}/clips/{clip_id}/subtitle-style/apply-all",
        response_model=list[TimestampClip],
    )
    def apply_clip_subtitle_style_to_all(project_id: str, clip_id: str):
        project_data = store.get("project", project_id)
        source_data = next(
            (
                candidate
                for candidate in store.list("clip", project_id)
                if candidate.get("clip_id") == clip_id
            ),
            None,
        )
        if not project_data or not source_data:
            raise HTTPException(404, "Timestamp clip not found")
        project = Project.model_validate(project_data)
        source = TimestampClip.model_validate(source_data)
        style = source.subtitle_style or project.subtitle_style
        project = project.model_copy(
            update={
                "subtitle_style": style,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.save_project(project)
        updated_clips = []
        for item in store.list("clip", project_id):
            updated = TimestampClip.model_validate(item).model_copy(
                update={"subtitle_style": style}
            )
            store.save_clip(project_id, updated)
            updated_clips.append(updated)
        return updated_clips

    @app.patch(
        "/api/projects/{project_id}/segments/{segment_id}",
        response_model=Segment,
    )
    def update_segment(project_id: str, segment_id: str, patch: SegmentPatch):
        item = next(
            (
                candidate
                for candidate in store.list("segment", project_id)
                if candidate.get("segment_id") == segment_id
            ),
            None,
        )
        if not item:
            raise HTTPException(404, "Segment not found")
        segment = Segment.model_validate(item)
        updates = patch.model_dump(exclude_unset=True)
        if updates.get("speaker_id") is not None:
            speaker_ids = {
                candidate.get("speaker_id")
                for candidate in store.list("speaker", project_id)
            }
            if updates["speaker_id"] not in speaker_ids:
                raise HTTPException(422, "Speaker does not belong to this project")
        updated = segment.model_copy(update={**updates, "status": "user_edited"})
        store.save_segment(project_id, updated)
        return updated

    @app.get(
        "/api/projects/{project_id}/speakers", response_model=list[Speaker]
    )
    def list_speakers(project_id: str):
        return [
            Speaker.model_validate(item)
            for item in store.list("speaker", project_id)
        ]

    @app.patch(
        "/api/projects/{project_id}/speakers/{speaker_id}",
        response_model=Speaker,
    )
    def update_speaker(
        project_id: str, speaker_id: str, patch: SpeakerPatch
    ):
        item = next(
            (
                candidate
                for candidate in store.list("speaker", project_id)
                if candidate.get("speaker_id") == speaker_id
            ),
            None,
        )
        if not item:
            raise HTTPException(404, "Speaker not found")
        speaker = Speaker.model_validate(item).model_copy(
            update={"name": patch.name.strip()}
        )
        store.save_speaker(project_id, speaker)
        return speaker

    @app.get("/api/voice-profiles", response_model=list[VoiceProfile])
    def list_voice_profiles():
        return [
            VoiceProfile.model_validate(item)
            for item in store.list("voice_profile", "__app__")
        ]

    @app.post(
        "/api/voice-profiles",
        response_model=VoiceProfile,
        status_code=201,
    )
    async def create_voice_profile(
        name: str = Form(...),
        sample: UploadFile = File(...),
    ):
        profile_name = name.strip()
        if not profile_name:
            raise HTTPException(422, "Host name cannot be blank")
        if len(profile_name) > 80:
            raise HTTPException(422, "Host name is too long")
        token = configured_value("HUGGINGFACE_TOKEN", store)
        if not token:
            raise HTTPException(
                409,
                "Add a Hugging Face token before enrolling a host",
            )
        profile_id = f"HOST_{uuid4().hex[:10].upper()}"
        profile_dir = root / "voice-profiles" / profile_id
        filename = safe_filename(sample.filename or "voice-sample.wav")
        source = profile_dir / f"source-{filename}"
        normalized = profile_dir / "sample.wav"
        try:
            await save_upload(sample, source)
            duration_ms = media_duration_ms(source)
            if duration_ms < 5_000:
                raise ValueError(
                    "Use at least 5 seconds of clean solo speech."
                )
            if duration_ms > 600_000:
                raise ValueError(
                    "Voice samples can be up to 10 minutes long."
                )
            prepare_diarization_audio(source, normalized)
            embedding = await asyncio.to_thread(
                extract_voice_embedding, normalized, token
            )
            profile = VoiceProfileRecord(
                profile_id=profile_id,
                name=profile_name,
                sample_name=filename,
                duration_ms=duration_ms,
                created_at=datetime.now(timezone.utc).isoformat(),
                embedding=embedding,
            )
            store.save_voice_profile(profile)
            return VoiceProfile.model_validate(profile)
        except HTTPException:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise HTTPException(
                422, f"Could not create voice profile: {exc}"
            ) from exc

    @app.delete("/api/voice-profiles/{profile_id}", status_code=204)
    def delete_voice_profile(profile_id: str):
        if not store.get("voice_profile", profile_id):
            raise HTTPException(404, "Voice profile not found")
        store.delete_voice_profile(profile_id)
        shutil.rmtree(
            root / "voice-profiles" / profile_id,
            ignore_errors=True,
        )

    @app.get(
        "/api/style-presets",
        response_model=list[SubtitleStylePreset],
    )
    def list_style_presets():
        return [
            SubtitleStylePreset.model_validate(item)
            for item in store.list("style_preset", "__app__")
        ]

    @app.post(
        "/api/style-presets",
        response_model=SubtitleStylePreset,
        status_code=201,
    )
    def create_style_preset(payload: SubtitleStylePresetCreate):
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "Preset name cannot be blank")
        if any(
            item.get("name", "").casefold() == name.casefold()
            for item in store.list("style_preset", "__app__")
        ):
            raise HTTPException(409, "A preset with that name already exists")
        preset = SubtitleStylePreset(name=name, style=payload.style)
        store.save_style_preset(preset)
        return preset

    @app.put(
        "/api/style-presets/{preset_id}",
        response_model=SubtitleStylePreset,
    )
    def update_style_preset(
        preset_id: str, payload: SubtitleStylePresetCreate
    ):
        if not store.get("style_preset", preset_id):
            raise HTTPException(404, "Style preset not found")
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "Preset name cannot be blank")
        if any(
            item.get("preset_id") != preset_id
            and item.get("name", "").casefold() == name.casefold()
            for item in store.list("style_preset", "__app__")
        ):
            raise HTTPException(409, "A preset with that name already exists")
        preset = SubtitleStylePreset(
            preset_id=preset_id,
            name=name,
            style=payload.style,
        )
        store.save_style_preset(preset)
        return preset

    @app.delete("/api/style-presets/{preset_id}", status_code=204)
    def delete_style_preset(preset_id: str):
        if not store.get("style_preset", preset_id):
            raise HTTPException(404, "Style preset not found")
        store.delete_style_preset(preset_id)

    @app.post("/api/projects/{project_id}/media", response_model=Project)
    async def upload_media(project_id: str, media: UploadFile = File(...)):
        data = store.get("project", project_id)
        if not data:
            raise HTTPException(404, "Project not found")
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(
                409, "Stop the current task before replacing the media"
            )
        replacing_media = bool(data.get("media_name"))
        filename = safe_filename(media.filename or "source")
        if filename.lower() in {
            "normalized.wav",
            "diarization.wav",
            "enrolled-clips.wav",
        }:
            filename = f"source-{filename}"
        project_dir = store.media_root / project_id
        upload_id = uuid4().hex
        incoming = project_dir / (
            f".incoming-{upload_id}{Path(filename).suffix}"
        )
        normalized = project_dir / f".normalized-{upload_id}.wav"
        diarization = project_dir / f".diarization-{upload_id}.wav"
        try:
            media_hash = await save_upload(media, incoming)
            duration = media_duration_ms(incoming)
            normalize_audio(incoming, normalized)
            prepare_diarization_audio(incoming, diarization)
        except HTTPException:
            incoming.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)
            diarization.unlink(missing_ok=True)
            raise
        except Exception as exc:
            incoming.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)
            diarization.unlink(missing_ok=True)
            raise HTTPException(422, f"Audio extraction failed: {exc}") from exc
        source = project_dir / filename
        previous_source = (
            project_dir / data["media_name"]
            if data.get("media_name")
            else None
        )
        incoming.replace(source)
        normalized.replace(project_dir / "normalized.wav")
        diarization.replace(project_dir / "diarization.wav")
        if previous_source and previous_source != source:
            previous_source.unlink(missing_ok=True)
        for cache_name in ("waveforms", "whisper", ".video-export-work"):
            shutil.rmtree(project_dir / cache_name, ignore_errors=True)
        (project_dir / "enrolled-clips.wav").unlink(missing_ok=True)
        invalidate_audio_analysis(
            project_id,
            force=True,
            preserve_speakers=replacing_media,
        )
        if replacing_media:
            reset_replaced_media_timeline(project_id)
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

    @app.get(
        "/api/projects/{project_id}/timeline-info",
        response_model=MediaTimelineInfo,
    )
    def project_timeline_info(project_id: str):
        data = store.get("project", project_id)
        if not data:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(data)
        if not project.media_name:
            return MediaTimelineInfo()
        source = store.media_root / project_id / project.media_name
        if not source.is_file():
            return MediaTimelineInfo()
        return MediaTimelineInfo(
            frame_rate=media_frame_rate(source),
            waveform_url=f"/api/projects/{project_id}/waveform.png",
        )

    @app.get("/api/projects/{project_id}/waveform.png")
    def project_waveform(
        project_id: str,
        start_ms: int = Query(default=0, ge=0),
        end_ms: int | None = Query(default=None, gt=0),
        width: int = Query(default=2048, ge=256, le=4096),
        height: int = Query(default=128, ge=32, le=256),
    ):
        data = store.get("project", project_id)
        if not data:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(data)
        if not project.media_name:
            raise HTTPException(409, "Upload media before loading its waveform")
        project_dir = store.media_root / project_id
        source = project_dir / "normalized.wav"
        if not source.is_file():
            source = project_dir / project.media_name
        if not source.is_file():
            raise HTTPException(404, "Project media not found")
        bounded_end_ms = min(
            project.duration_ms,
            end_ms if end_ms is not None else project.duration_ms,
        )
        bounded_start_ms = min(start_ms, project.duration_ms)
        if bounded_end_ms <= bounded_start_ms:
            raise HTTPException(422, "Waveform end must be after its start")
        cache_key = (
            f"waveform-cbrt-v1:{project.media_hash or project.media_name}:"
            f"{bounded_start_ms}:{bounded_end_ms}:{width}:{height}"
        )
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:20]
        target = project_dir / "waveforms" / f"{digest}.png"
        if not target.is_file():
            try:
                render_waveform_image(
                    source,
                    target,
                    bounded_start_ms,
                    bounded_end_ms,
                    width,
                    height,
                )
            except Exception as exc:
                raise HTTPException(
                    422, f"Could not create the audio waveform: {exc}"
                ) from exc
        return FileResponse(
            target,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable"
            },
        )

    @app.post("/api/projects/{project_id}/transcribe", response_model=Job, status_code=202)
    def transcribe(
        project_id: str,
        background: BackgroundTasks,
        model: str = DEFAULT_WHISPER_MODEL,
        clip_id: str | None = None,
    ):
        project = store.get("project", project_id)
        if not project or not project.get("media_name"):
            raise HTTPException(409, "Upload media before transcription")
        if not store.list("speaker_turn", project_id):
            raise HTTPException(
                409, "Detect speakers before transcribing the audio"
            )
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Transcription is already running")
        selected_clips = processing_clips(project_id, clip_id)
        job = new_job(project_id, "queued").model_copy(
            update={"clip_id": clip_id}
        )
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

    @app.post(
        "/api/projects/{project_id}/pipeline/english",
        response_model=Job,
        status_code=202,
    )
    def start_english_pipeline(
        project_id: str,
        background: BackgroundTasks,
        clip_id: str | None = None,
    ):
        project = store.get("project", project_id)
        if not project or not project.get("media_name"):
            raise HTTPException(
                409, "Upload media before creating an English transcript"
            )
        status_rank = {
            "media_ready": 1,
            "speakers_detected": 2,
            "transcribed": 3,
            "corrected_pass_1": 4,
            "corrected": 5,
            "translated": 6,
        }
        selected_clips = processing_clips(project_id, clip_id)
        workflow_status = (
            selected_clips[0].status
            if clip_id and selected_clips
            else project.get("status", "")
        )
        rank = status_rank.get(workflow_status, 0)
        if rank >= 6:
            raise HTTPException(409, "The English transcript is already ready")
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Another task is already running")
        if rank < 2:
            if not diarization_available():
                raise HTTPException(
                    409, "Speaker detection is not installed"
                )
            if not configured_value("HUGGINGFACE_TOKEN", store):
                raise HTTPException(
                    409,
                    "Add a Hugging Face token in Settings before detecting speakers",
                )
        if rank < 3 and not whisper_available():
            raise HTTPException(409, "Whisper is not installed")
        if not configured_value("OPENROUTER_API_KEY", store):
            raise HTTPException(
                409,
                "Connect OpenRouter from the app settings before creating an English transcript",
            )
        job = new_job(project_id, "queued").model_copy(
            update={
                "clip_id": clip_id,
                "pipeline": True,
                "pipeline_step": max(1, rank),
                "pipeline_total": 5,
                "pipeline_completed": False,
                "overall_progress": max(0, rank - 1) / 5,
            }
        )
        store.save_job(job)
        background.add_task(
            lambda: asyncio.run(
                run_english_pipeline(
                    store,
                    project_id,
                    job.job_id,
                    selected_clips,
                    project.get("expected_speaker_count")
                    or len(store.list("voice_profile", "__app__"))
                    or len(project.get("speakers", []))
                    or None,
                )
            )
        )
        return job

    @app.post(
        "/api/projects/{project_id}/diarize",
        response_model=Job,
        status_code=202,
    )
    def diarize(
        project_id: str,
        background: BackgroundTasks,
        clip_id: str | None = None,
    ):
        project = store.get("project", project_id)
        if not project or not project.get("media_name"):
            raise HTTPException(
                409, "Upload media before detecting speakers"
            )
        if not diarization_available():
            raise HTTPException(409, "Speaker detection is not installed")
        if not configured_value("HUGGINGFACE_TOKEN", store):
            raise HTTPException(
                409,
                "Add a Hugging Face token in Settings before detecting speakers",
            )
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Another task is already running")
        selected_clips = processing_clips(project_id, clip_id)
        enrolled_profiles = store.list("voice_profile", "__app__")
        job = new_job(project_id, "queued").model_copy(
            update={"clip_id": clip_id}
        )
        store.save_job(job)
        background.add_task(
            run_diarization,
            store,
            project_id,
            job.job_id,
            selected_clips if enrolled_profiles else None,
            project.get("expected_speaker_count")
            or len(enrolled_profiles)
            or len(project.get("speakers", []))
            or None,
        )
        return job

    @app.get("/api/projects/{project_id}/jobs/active", response_model=Job | None)
    def active_project_job(project_id: str):
        return next(
            (
                Job.model_validate(item)
                for item in reversed(store.list("job", project_id))
                if job_is_active(item)
            ),
            None,
        )

    def start_language_job(
        project_id: str,
        stage: str,
        background: BackgroundTasks,
        clip_id: str | None = None,
    ) -> Job:
        project = store.get("project", project_id)
        selected_clips = processing_clips(project_id, clip_id)
        selected_ids = {clip.clip_id for clip in selected_clips}
        selected_segments = [
            item
            for item in store.list("segment", project_id)
            if not selected_clips or item.get("clip_id") in selected_ids
        ]
        if not project or not selected_segments:
            raise HTTPException(409, "Transcribe the project first")
        status_rank = {
            "speakers_detected": 2,
            "transcribed": 3,
            "corrected_pass_1": 4,
            "corrected": 5,
            "translated": 6,
        }
        workflow_status = (
            selected_clips[0].status
            if clip_id and selected_clips
            else project.get("status", "")
        )
        if status_rank.get(workflow_status, 0) < 3:
            raise HTTPException(
                409, "Transcribe the speaker-attributed audio first"
            )
        if not configured_value("OPENROUTER_API_KEY", store):
            raise HTTPException(
                409, "Connect OpenRouter from the app settings before running AI stages"
            )
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Another task is already running")
        selected_clips = processing_clips(project_id, clip_id)
        job = new_job(project_id, "queued").model_copy(
            update={"clip_id": clip_id}
        )
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
    def correct_pass_1(
        project_id: str,
        background: BackgroundTasks,
        clip_id: str | None = None,
    ):
        return start_language_job(
            project_id, "correcting_pass_1", background, clip_id
        )

    @app.post("/api/projects/{project_id}/correct/pass-2", response_model=Job, status_code=202)
    def correct_pass_2(
        project_id: str,
        background: BackgroundTasks,
        clip_id: str | None = None,
    ):
        return start_language_job(
            project_id, "correcting_pass_2", background, clip_id
        )

    @app.post("/api/projects/{project_id}/translate", response_model=Job, status_code=202)
    def translate(
        project_id: str,
        background: BackgroundTasks,
        clip_id: str | None = None,
    ):
        return start_language_job(
            project_id, "translating", background, clip_id
        )

    @app.post(
        "/api/projects/{project_id}/export/video",
        response_model=Job,
        status_code=202,
    )
    def export_video(
        project_id: str,
        request: VideoExportRequest,
        background: BackgroundTasks,
    ):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        if not any(
            (request.include_video, request.include_srt, request.include_ass)
        ):
            raise HTTPException(409, "Select at least one export format")
        if request.include_video:
            if not project_data.get("media_name"):
                raise HTTPException(409, "Upload a video before exporting")
            if Path(project_data["media_name"]).suffix.lower() not in {
                ".mp4",
                ".mov",
                ".mkv",
            }:
                raise HTTPException(
                    409, "Video export requires a video source"
                )
        track_data = store.get(
            "caption_track", f"{project_id}:en"
        )
        if not track_data:
            raise HTTPException(
                409, "Generate captions before exporting"
            )
        track = CaptionTrack.model_validate(track_data)
        project = Project.model_validate(project_data)
        clips = [
            TimestampClip.model_validate(item)
            for item in store.list("clip", project_id)
        ]
        clip_lookup = {clip.clip_id: clip for clip in clips}
        requested_clip_ids = list(dict.fromkeys(request.clip_ids))
        queued_clip_ids = [
            clip.clip_id for clip in clips if clip.render_queued
        ]
        if clips and not requested_clip_ids:
            requested_clip_ids = queued_clip_ids
        unknown_clip_ids = [
            clip_id
            for clip_id in requested_clip_ids
            if clip_id not in clip_lookup
        ]
        if unknown_clip_ids:
            raise HTTPException(
                404, "One or more video segments were not found"
            )
        if clips and not requested_clip_ids:
            raise HTTPException(
                409, "Add at least one clip to the rendering queue"
            )
        if any(
            clip_id not in queued_clip_ids
            for clip_id in requested_clip_ids
        ):
            raise HTTPException(
                409,
                "Every selected clip must be in the rendering queue",
            )
        current_signature = project_caption_source_signature(
            store.list("segment", project_id),
            track.language,
            clips,
            project.subtitle_style,
        )
        if track.source_signature != current_signature:
            raise HTTPException(
                409, "Regenerate captions before exporting"
            )
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Another task is already running")
        job = new_job(project_id, "exporting_video")
        store.save_job(job)
        background.add_task(
            run_video_export,
            store,
            project_id,
            job.job_id,
            request.resolution,
            request.quality,
            request.encoder,
            requested_clip_ids,
            request.include_video,
            request.include_srt,
            request.include_ass,
        )
        return job

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
            update={"cancelled": True, "paused": False, "stage": "cancelled"}
        )
        store.save_job(job)
        return job

    @app.post("/api/jobs/{job_id}/pause", response_model=Job)
    def pause_job(job_id: str):
        data = store.get("job", job_id)
        if not data:
            raise HTTPException(404, "Job not found")
        job = Job.model_validate(data)
        if not job_is_active(job):
            raise HTTPException(409, "Only an active job can be paused")
        job = job.model_copy(update={"paused": True})
        store.save_job(job)
        return job

    @app.post("/api/jobs/{job_id}/resume", response_model=Job)
    def resume_job(job_id: str):
        data = store.get("job", job_id)
        if not data:
            raise HTTPException(404, "Job not found")
        job = Job.model_validate(data)
        if not job_is_active(job):
            raise HTTPException(409, "Only an active job can be resumed")
        job = job.model_copy(update={"paused": False})
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

    @app.get(
        "/api/projects/{project_id}/video-exports",
        response_model=list[Job],
    )
    def list_video_exports(project_id: str):
        def existing_output(job: Job, output_name: str) -> Path | None:
            if Path(output_name).name != output_name:
                return None
            candidates: list[tuple[Path, Path]] = []
            if job.output_folder:
                candidates.append((Path(job.output_folder), Path()))
            candidates.append(
                (
                    store.media_root / project_id / "exports",
                    store.media_root,
                )
            )
            for folder, allowed_root in candidates:
                resolved_folder = folder.resolve()
                if allowed_root != Path():
                    try:
                        resolved_folder.relative_to(allowed_root.resolve())
                    except ValueError:
                        continue
                path = resolved_folder / output_name
                if path.is_file():
                    return path
            return None

        exports = []
        for item in reversed(store.list("job", project_id)):
            job = Job.model_validate(item)
            output_names = [
                output.output_name for output in job.outputs
            ] or ([job.output_name] if job.output_name else [])
            if not output_names:
                continue
            if any(
                existing_output(job, output_name) is not None
                for output_name in output_names
            ):
                exports.append(job)
        return exports

    @app.post("/api/projects/{project_id}/video-exports/open-folder")
    def open_video_export_folder(project_id: str):
        project_data = store.get("project", project_id)
        if not project_data:
            raise HTTPException(404, "Project not found")
        project = Project.model_validate(project_data)
        folder = store.video_export_dir(project_id, project.name)
        for item in reversed(store.list("job", project_id)):
            job = Job.model_validate(item)
            if job.output_folder:
                candidate = Path(job.output_folder).resolve()
                folder = candidate
                break
        try:
            open_folder(folder)
        except OSError as exc:
            raise HTTPException(
                500, "Could not open the video export folder"
            ) from exc
        return {"path": str(folder.resolve())}

    @app.get(
        "/api/projects/{project_id}/video-exports/{filename}"
    )
    def download_video_export(project_id: str, filename: str):
        if Path(filename).name != filename:
            raise HTTPException(404, "Video export not found")
        path = None
        for item in reversed(store.list("job", project_id)):
            job = Job.model_validate(item)
            output_names = {
                output.output_name for output in job.outputs
            }
            if job.output_name:
                output_names.add(job.output_name)
            if filename not in output_names:
                continue
            folders: list[tuple[Path, Path]] = []
            if job.output_folder:
                folders.append((Path(job.output_folder), Path()))
            folders.append(
                (
                    store.media_root / project_id / "exports",
                    store.media_root,
                )
            )
            for folder, allowed_root in folders:
                resolved_folder = folder.resolve()
                if allowed_root != Path():
                    try:
                        resolved_folder.relative_to(allowed_root.resolve())
                    except ValueError:
                        continue
                candidate = resolved_folder / filename
                if candidate.is_file():
                    path = candidate
                    break
            if path:
                break
        if path is None:
            raise HTTPException(404, "Video export not found")
        return FileResponse(
            path,
            media_type={
                ".mp4": "video/mp4",
                ".srt": "application/x-subrip",
                ".ass": "text/x-ssa",
            }.get(path.suffix.lower(), "application/octet-stream"),
            filename=filename,
        )

    @app.get("/api/projects/{project_id}/export/{format_name}")
    def export(project_id: str, format_name: str, language: str = "en", bilingual: bool = False):
        if format_name not in {"srt", "vtt", "ass", "txt", "json"}:
            raise HTTPException(404, "Unknown export format")
        segments = store.list("segment", project_id)
        if format_name == "json":
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {
                    "project": store.get("project", project_id),
                    "segments": segments,
                    "captions": store.list("caption_track", project_id),
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
        elif format_name == "ass":
            project = Project.model_validate(store.get("project", project_id))
            clip_styles = {
                clip.clip_id: clip.subtitle_style
                for clip in (
                    TimestampClip.model_validate(item)
                    for item in store.list("clip", project_id)
                )
                if clip.subtitle_style
            }
            track_data = store.get(
                "caption_track", f"{project_id}:{language}"
            )
            content = (
                export_ass_caption_cues(
                    CaptionTrack.model_validate(track_data).cues,
                    project.subtitle_style,
                    clip_styles,
                )
                if track_data and not bilingual
                else export_ass_subtitles(
                    segments,
                    language,
                    project.subtitle_style,
                    bilingual,
                )
            )
        else:
            project = Project.model_validate(store.get("project", project_id))
            track_data = store.get(
                "caption_track", f"{project_id}:{language}"
            )
            content = (
                export_caption_cues(
                    CaptionTrack.model_validate(track_data).cues,
                    format_name,
                )
                if track_data and not bilingual
                else export_subtitles(
                    segments,
                    language,
                    format_name,
                    bilingual,
                    project.subtitle_style.max_words_per_line,
                    project.subtitle_style.max_lines,
                )
            )
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
        return {
            "ffmpeg": Path(bundled_binary("ffmpeg")).is_file()
            or bool(shutil.which("ffmpeg")),
            "whisper": whisper_available(),
            "diarization": diarization_available(),
            "diarization_configured": bool(
                configured_value("HUGGINGFACE_TOKEN", store)
            ),
            "diarization_model": DEFAULT_DIARIZATION_MODEL,
            "llm_provider": "OpenRouter",
            "openrouter_configured": bool(configured_value("OPENROUTER_API_KEY", store)),
            "correction_model": openrouter_model_for_stage(
                "correcting_pass_1", store
            ),
            "translation_model": openrouter_model_for_stage("translating", store),
            "post_copy_model": openrouter_model_for_stage(
                "post_captioning", store
            ),
            "privacy": "Media and Whisper stay local; transcript text is sent to OpenRouter.",
        }

    def speaker_detection_status() -> SpeakerDetectionSettingsStatus:
        return SpeakerDetectionSettingsStatus(
            configured=bool(configured_value("HUGGINGFACE_TOKEN", store)),
            available=diarization_available(),
            model=DEFAULT_DIARIZATION_MODEL,
        )

    def video_export_folder_status() -> VideoExportFolderStatus:
        current = store.video_export_root.resolve()
        default = default_video_export_root.resolve()
        return VideoExportFolderStatus(
            path=str(current),
            default_path=str(default),
            is_default=current == default,
        )

    def app_preferences_status() -> AppPreferences:
        record = store.get("app_preferences", "app")
        return AppPreferences.model_validate(record or {})

    @app.get(
        "/api/settings/app-preferences",
        response_model=AppPreferences,
    )
    def get_app_preferences():
        return app_preferences_status()

    @app.patch(
        "/api/settings/app-preferences",
        response_model=AppPreferences,
    )
    def update_app_preferences(patch: AppPreferencesPatch):
        with store.lock:
            current = app_preferences_status()
            updates = patch.model_dump(exclude_unset=True)
            updates = {
                key: value
                for key, value in updates.items()
                if value is not None or key == "last_project_id"
            }
            preferences = current.model_copy(update=updates)
            store.put(
                "app_preferences",
                "__app__",
                "app",
                preferences,
            )
        return preferences

    @app.get(
        "/api/settings/video-export-folder",
        response_model=VideoExportFolderStatus,
    )
    def get_video_export_folder():
        return video_export_folder_status()

    @app.put(
        "/api/settings/video-export-folder",
        response_model=VideoExportFolderStatus,
    )
    def update_video_export_folder(settings: VideoExportFolderUpdate):
        if settings.path is None:
            store.video_export_root = default_video_export_root
            store.delete_setting("VIDEO_EXPORT_ROOT")
            return video_export_folder_status()
        target = Path(settings.path.strip()).expanduser()
        if not target.is_absolute():
            raise HTTPException(422, "Choose an absolute export folder")
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / f".subtitle-studio-{uuid4().hex}.tmp"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise HTTPException(
                422, "Subtitle Studio cannot write to that folder"
            ) from exc
        resolved = target.resolve()
        store.video_export_root = resolved
        store.save_setting("VIDEO_EXPORT_ROOT", str(resolved))
        return video_export_folder_status()

    def openrouter_status() -> OpenRouterSettingsStatus:
        return OpenRouterSettingsStatus(
            openrouter_configured=bool(
                configured_value("OPENROUTER_API_KEY", store)
            ),
            correction_model=openrouter_model_for_stage(
                "correcting_pass_1", store
            ),
            translation_model=openrouter_model_for_stage("translating", store),
            post_copy_model=openrouter_model_for_stage(
                "post_captioning", store
            ),
        )

    @app.get(
        "/api/settings/openrouter", response_model=OpenRouterSettingsStatus
    )
    def get_openrouter_settings():
        return openrouter_status()

    @app.get("/api/openrouter/models", response_model=list[OpenRouterModel])
    async def get_openrouter_models():
        return await fetch_openrouter_models(store)

    @app.put(
        "/api/settings/openrouter", response_model=OpenRouterSettingsStatus
    )
    def update_openrouter_settings(settings: OpenRouterSettingsUpdate):
        if settings.api_key is not None:
            api_key = settings.api_key.get_secret_value().strip()
            if api_key:
                store.save_setting("OPENROUTER_API_KEY", api_key)
        if settings.correction_model is not None:
            correction_model = settings.correction_model.strip()
            if not correction_model:
                raise HTTPException(422, "Correction model cannot be blank")
            store.save_setting("OPENROUTER_CORRECTION_MODEL", correction_model)
        if settings.translation_model is not None:
            translation_model = settings.translation_model.strip()
            if not translation_model:
                raise HTTPException(422, "Translation model cannot be blank")
            store.save_setting("OPENROUTER_TRANSLATION_MODEL", translation_model)
        if settings.post_copy_model is not None:
            post_copy_model = settings.post_copy_model.strip()
            if not post_copy_model:
                raise HTTPException(422, "Post copy model cannot be blank")
            store.save_setting("OPENROUTER_POST_COPY_MODEL", post_copy_model)
        return openrouter_status()

    @app.get(
        "/api/settings/speaker-detection",
        response_model=SpeakerDetectionSettingsStatus,
    )
    def get_speaker_detection_settings():
        return speaker_detection_status()

    @app.put(
        "/api/settings/speaker-detection",
        response_model=SpeakerDetectionSettingsStatus,
    )
    def update_speaker_detection_settings(
        settings: SpeakerDetectionSettingsUpdate,
    ):
        if settings.huggingface_token is not None:
            token = settings.huggingface_token.get_secret_value().strip()
            if token:
                store.save_setting("HUGGINGFACE_TOKEN", token)
        return speaker_detection_status()

    # --- Agent orchestration + X scheduling -------------------------------

    @app.post(
        "/api/agent/clip-from-timestamps",
        response_model=dict,
    )
    async def agent_clip_from_timestamps(
        background: BackgroundTasks,
        media: UploadFile = File(...),
        payload: str = Form(...),
    ):
        """One-shot: upload a video + JSON clip spec, get captioned clips back.

        `payload` is a JSON string matching AgentClipRequest. The heavy work
        runs as a background task; the response carries a job_id to poll via
        GET /api/agent/jobs/{job_id}.
        """
        try:
            request = AgentClipRequest.model_validate_json(payload)
        except Exception as exc:
            raise HTTPException(422, f"Invalid payload: {exc}") from exc

        suffix = Path(media.filename or "video.mp4").suffix or ".mp4"
        temp_dir = root / ".agent-uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"{uuid4().hex}{suffix}"
        temp_path.write_bytes(await media.read())

        job = new_job("agent", "agent_clip_from_timestamps")
        store.save_job(job)

        async def _run():
            try:
                project, results = await clip_from_timestamps(
                    store, temp_path, request
                )
                finished = Job.model_validate(store.get("job", job.job_id))
                finished.stage = "agent_completed"
                finished.progress = 1
                finished.outputs = []
                finished.error = None
                store.save_job(finished)
                store.put(
                    "agent_result",
                    "agent",
                    job.job_id,
                    {
                        "project_id": project.project_id,
                        "results": [r.model_dump() for r in results],
                    },
                )
            except Exception as exc:  # noqa: BLE001
                failed = Job.model_validate(store.get("job", job.job_id))
                failed.stage = "failed"
                failed.error = str(exc)
                store.save_job(failed)
            finally:
                temp_path.unlink(missing_ok=True)

        background.add_task(_run)
        return {"job_id": job.job_id, "status": "started"}

    @app.get("/api/agent/jobs/{job_id}", response_model=dict)
    def agent_job_status(job_id: str):
        data = store.get("job", job_id)
        if not data:
            raise HTTPException(404, "Job not found")
        job = Job.model_validate(data)
        result = store.get("agent_result", job_id)
        return {
            "job_id": job.job_id,
            "stage": job.stage,
            "progress": job.progress,
            "error": job.error,
            "result": result,
        }

    @app.post(
        "/api/agent/projects/{project_id}/clip-everything",
        response_model=dict,
    )
    async def agent_clip_everything(
        project_id: str,
        background: BackgroundTasks,
    ):
        """Clip every timestamp in an existing project in one shot.

        Runs the full pipeline (diarize -> transcribe -> correct -> translate),
        regenerates the burn-in captions, and exports every clip. Poll
        GET /api/agent/jobs/{job_id} for completion.
        """
        if not store.get("project", project_id):
            raise HTTPException(404, "Project not found")
        if any(
            job_is_active(item)
            for item in store.list("job", project_id)
        ):
            raise HTTPException(409, "Another task is already running")

        job = new_job(project_id, "agent_clip_everything")
        store.save_job(job)

        async def _run():
            try:
                project, results = await clip_everything(store, project_id)
                finished = Job.model_validate(store.get("job", job.job_id))
                finished.stage = "agent_completed"
                finished.progress = 1
                finished.error = None
                store.save_job(finished)
                store.put(
                    "agent_result",
                    "agent",
                    job.job_id,
                    {
                        "project_id": project.project_id,
                        "results": [r.model_dump() for r in results],
                    },
                )
            except Exception as exc:  # noqa: BLE001
                failed = Job.model_validate(store.get("job", job.job_id))
                failed.stage = "failed"
                failed.error = str(exc)
                store.save_job(failed)

        background.add_task(_run)
        return {"job_id": job.job_id, "status": "started"}

    # --- X account settings ------------------------------------------------

    @app.get("/api/settings/x", response_model=XAccountSettingsStatus)
    def x_settings_status():
        settings = xpost.load_account_settings(store)
        return XAccountSettingsStatus(
            method=settings.method,
            configured=xpost.account_is_configured(settings),
        )

    @app.put("/api/settings/x", response_model=XAccountSettingsStatus)
    def update_x_settings(update: XAccountSettingsUpdate):
        settings = xpost.load_account_settings(store)
        data = update.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(settings, key, value)
        xpost.save_account_settings(store, settings)
        return XAccountSettingsStatus(
            method=settings.method,
            configured=xpost.account_is_configured(settings),
        )

    # --- Scheduled posts ----------------------------------------------------

    @app.get("/api/scheduled-posts", response_model=list[ScheduledPost])
    def list_posts(project_id: str | None = None, status: str | None = None):
        return xpost.list_scheduled_posts(store, project_id or "*", status)

    @app.post(
        "/api/scheduled-posts",
        response_model=ScheduledPost,
        status_code=201,
    )
    def create_post(request: ScheduledPostCreate):
        settings = xpost.load_account_settings(store)
        post = ScheduledPost(
            project_id=request.project_id,
            clip_id=request.clip_id,
            text=request.text,
            scheduled_at=request.scheduled_at,
            video_path=request.video_path or _resolve_clip_video_path(
                store, request.project_id, request.clip_id
            ),
            method=request.method or settings.method,
        )
        xpost.save_scheduled_post(store, post)
        return post

    @app.get("/api/scheduled-posts/{post_id}", response_model=ScheduledPost)
    def get_post(post_id: str):
        post = xpost.get_scheduled_post(store, post_id)
        if not post:
            raise HTTPException(404, "Scheduled post not found")
        return post

    @app.patch("/api/scheduled-posts/{post_id}", response_model=ScheduledPost)
    def update_post(post_id: str, patch: ScheduledPostPatch):
        post = xpost.get_scheduled_post(store, post_id)
        if not post:
            raise HTTPException(404, "Scheduled post not found")
        if post.status == "posted":
            raise HTTPException(409, "Cannot edit a post that is already posted")
        updates = patch.model_dump(exclude_unset=True)
        post = post.model_copy(update=updates)
        xpost.save_scheduled_post(store, post)
        return post

    @app.delete("/api/scheduled-posts/{post_id}", status_code=204)
    def delete_post(post_id: str):
        post = xpost.get_scheduled_post(store, post_id)
        if not post:
            raise HTTPException(404, "Scheduled post not found")
        if post.status == "posted":
            raise HTTPException(409, "Cannot delete a post that is already posted")
        xpost.delete_scheduled_post(store, post_id)

    @app.post("/api/scheduled-posts/{post_id}/cancel", response_model=ScheduledPost)
    def cancel_post(post_id: str):
        post = xpost.get_scheduled_post(store, post_id)
        if not post:
            raise HTTPException(404, "Scheduled post not found")
        post.status = "cancelled"
        xpost.save_scheduled_post(store, post)
        return post

    @app.post("/api/scheduled-posts/publish-due", response_model=dict)
    def publish_due_now():
        return {"posted": xpost.publish_due_posts(store)}

    xpost.register_poster("api", xapi.post_to_x)
    xpost.start_scheduler(store)

    if static_root and static_root.is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="desktop")

    return app


app = create_app(
    user_data_root() if getattr(sys, "frozen", False) else None
)
