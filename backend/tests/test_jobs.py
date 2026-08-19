import asyncio

from backend.app import services
from backend.app.models import Job, Project, ProjectCreate, TimestampClip
from backend.app.services import (
    _checkpoint_job,
    _processed_audio_ms,
    run_english_pipeline,
)
from backend.app.store import Store


def test_worker_checkpoint_waits_for_resume(tmp_path, monkeypatch):
    store = Store(tmp_path)
    worker_job = Job(
        job_id="job_active",
        project_id="project_1",
        stage="transcribing",
        progress=0.4,
    )
    store.save_job(worker_job.model_copy(update={"paused": True}))
    waits = []

    def resume_after_wait(seconds):
        waits.append(seconds)
        paused_job = Job.model_validate(store.get("job", worker_job.job_id))
        store.save_job(paused_job.model_copy(update={"paused": False}))

    monkeypatch.setattr(services.time, "sleep", resume_after_wait)

    _checkpoint_job(store, worker_job)

    saved = Job.model_validate(store.get("job", worker_job.job_id))
    assert waits == [0.25]
    assert saved.paused is False
    assert saved.progress == 0.4


def test_clip_progress_counts_only_selected_audio():
    clips = [
        TimestampClip(
            clip_id="clip_1",
            start_ms=10_000,
            end_ms=20_000,
            title="First",
        ),
        TimestampClip(
            clip_id="clip_2",
            start_ms=40_000,
            end_ms=60_000,
            title="Second",
        ),
    ]

    assert _processed_audio_ms(45_000, clips, 30_000) == 15_000


def test_english_pipeline_runs_every_stage_in_order(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Pipeline")).model_copy(
        update={"status": "media_ready", "media_name": "episode.mp4"}
    )
    store.save_project(project)
    job = Job(
        job_id="job_pipeline",
        project_id=project.project_id,
        stage="queued",
        pipeline=True,
        pipeline_step=1,
        pipeline_total=6,
    )
    store.save_job(job)
    calls = []

    def finish(stage, label):
        calls.append(label)
        current = Job.model_validate(store.get("job", job.job_id))
        current.stage = stage
        current.progress = 1
        store.save_job(current)

    monkeypatch.setattr(
        services,
        "run_diarization",
        lambda *args: finish("speakers_detected", "diarize"),
    )
    monkeypatch.setattr(
        services,
        "run_transcription",
        lambda *args: finish("transcribed", "transcribe"),
    )

    async def finish_language(*args):
        stages = {
            "correcting_pass_1": ("corrected_pass_1", "pass-1"),
            "correcting_pass_2": ("corrected", "pass-2"),
            "translating": ("translated", "translate"),
        }
        finish(*stages[args[3]])

    monkeypatch.setattr(services, "run_language_stage", finish_language)

    async def finish_shortform(*args):
        finish("shortform_ideas", "shortform")

    monkeypatch.setattr(services, "run_shortform_ideas_stage", finish_shortform)

    asyncio.run(
        run_english_pipeline(
            store,
            project.project_id,
            job.job_id,
            expected_speaker_count=3,
        )
    )

    completed = Job.model_validate(store.get("job", job.job_id))
    assert calls == [
        "diarize",
        "transcribe",
        "pass-1",
        "pass-2",
        "translate",
        "shortform",
    ]
    assert completed.stage == "shortform_ideas"
    assert completed.pipeline_completed is True
    assert completed.overall_progress == 1


def test_english_pipeline_skips_completed_stages(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Resume")).model_copy(
        update={"status": "transcribed", "media_name": "episode.mp4"}
    )
    store.save_project(project)
    job = Job(
        job_id="job_pipeline",
        project_id=project.project_id,
        stage="queued",
        pipeline=True,
        pipeline_step=3,
        pipeline_total=6,
    )
    store.save_job(job)
    calls = []
    monkeypatch.setattr(
        services,
        "run_diarization",
        lambda *args: calls.append("diarize"),
    )
    monkeypatch.setattr(
        services,
        "run_transcription",
        lambda *args: calls.append("transcribe"),
    )

    async def finish_language(*args):
        stages = {
            "correcting_pass_1": "corrected_pass_1",
            "correcting_pass_2": "corrected",
            "translating": "translated",
        }
        calls.append(args[3])
        current = Job.model_validate(store.get("job", job.job_id))
        current.stage = stages[args[3]]
        current.progress = 1
        store.save_job(current)

    monkeypatch.setattr(services, "run_language_stage", finish_language)

    async def finish_shortform(*args):
        current = Job.model_validate(store.get("job", job.job_id))
        current.stage = "shortform_ideas"
        current.progress = 1
        store.save_job(current)
        calls.append("shortform")

    monkeypatch.setattr(services, "run_shortform_ideas_stage", finish_shortform)

    asyncio.run(
        run_english_pipeline(store, project.project_id, job.job_id)
    )

    assert calls == [
        "correcting_pass_1",
        "correcting_pass_2",
        "translating",
        "shortform",
    ]
