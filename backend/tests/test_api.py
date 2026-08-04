from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app import main
from backend.app.models import (
    DetectedSpeakerTurn,
    Job,
    Project,
    ProjectWorkspaceState,
    Segment,
    TimestampClip,
    Word,
)


def test_desktop_app_serves_the_built_frontend(tmp_path):
    static_root = tmp_path / "dist"
    static_root.mkdir()
    (static_root / "index.html").write_text("<main>Subtitle Studio</main>")

    client = TestClient(create_app(tmp_path / "data", static_root=static_root))

    assert client.get("/").text == "<main>Subtitle Studio</main>"
    assert client.get("/api/health").json() == {"status": "ok"}


def test_transcription_rejects_a_duplicate_active_job(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post("/api/projects", json={"name": "Episode"}).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "status": "media_ready"}
    )
    app.state.store.save_project(project)
    app.state.store.save_speaker_turn(
        project.project_id,
        DetectedSpeakerTurn(
            turn_id="turn_000001",
            start_ms=0,
            end_ms=1_000,
            speaker_id="SPEAKER_01",
        ),
    )
    app.state.store.save_job(
        Job(job_id="job_active", project_id=project.project_id, stage="transcribing")
    )
    monkeypatch.setattr(main, "run_transcription", lambda *args: None)

    response = client.post(f"/api/projects/{project.project_id}/transcribe")

    assert response.status_code == 409
    assert response.json()["detail"] == "Transcription is already running"


def test_transcription_uses_large_v3_by_default(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post("/api/projects", json={"name": "Episode"}).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "status": "speakers_detected"}
    )
    app.state.store.save_project(project)
    app.state.store.save_speaker_turn(
        project.project_id,
        DetectedSpeakerTurn(
            turn_id="turn_000001",
            start_ms=0,
            end_ms=1_000,
            speaker_id="SPEAKER_01",
        ),
    )
    transcription_calls = []
    monkeypatch.setattr(
        main,
        "run_transcription",
        lambda *args: transcription_calls.append(args),
    )

    response = client.post(f"/api/projects/{project.project_id}/transcribe")

    assert response.status_code == 202
    assert transcription_calls[0][3] == "large-v3"


def test_active_project_job_can_be_restored_after_reopening(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Episode"}).json()
    app.state.store.save_job(
        Job(
            job_id="job_active",
            project_id=project["project_id"],
            stage="transcribing",
            progress=0.08,
        )
    )

    response = client.get(f"/api/projects/{project['project_id']}/jobs/active")

    assert response.status_code == 200
    assert response.json()["job_id"] == "job_active"
    assert response.json()["stage"] == "transcribing"


def test_active_job_can_be_paused_and_resumed(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Episode"}).json()
    app.state.store.save_job(
        Job(
            job_id="job_active",
            project_id=project["project_id"],
            stage="transcribing",
            progress=0.35,
        )
    )

    paused = client.post("/api/jobs/job_active/pause")
    active = client.get(
        f"/api/projects/{project['project_id']}/jobs/active"
    )
    resumed = client.post("/api/jobs/job_active/resume")

    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert active.json()["paused"] is True
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False
    assert resumed.json()["progress"] == 0.35


def test_full_english_pipeline_starts_as_one_job(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects",
        json={"name": "Episode", "expected_speaker_count": 3},
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "status": "media_ready"}
    )
    app.state.store.save_project(project)
    app.state.store.save_setting("HUGGINGFACE_TOKEN", "hf_test")
    app.state.store.save_setting("OPENROUTER_API_KEY", "sk-or-test")
    calls = []

    async def fake_pipeline(*args):
        calls.append(args)

    monkeypatch.setattr(main, "diarization_available", lambda: True)
    monkeypatch.setattr(main, "whisper_available", lambda: True)
    monkeypatch.setattr(main, "run_english_pipeline", fake_pipeline)

    response = client.post(
        f"/api/projects/{project.project_id}/pipeline/english"
    )

    assert response.status_code == 202
    job = response.json()
    assert job["pipeline"] is True
    assert job["pipeline_step"] == 1
    assert job["pipeline_total"] == 5
    assert job["overall_progress"] == 0
    assert calls[0][1] == project.project_id
    assert calls[0][4] == 3


def test_pipeline_job_remains_active_between_stages(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Episode"}
    ).json()
    app.state.store.save_job(
        Job(
            job_id="job_pipeline",
            project_id=project["project_id"],
            stage="transcribed",
            progress=1,
            overall_progress=0.4,
            pipeline=True,
            pipeline_step=2,
            pipeline_total=5,
        )
    )

    active = client.get(
        f"/api/projects/{project['project_id']}/jobs/active"
    )
    paused = client.post("/api/jobs/job_pipeline/pause")
    stopped = client.post("/api/jobs/job_pipeline/cancel")
    no_longer_active = client.get(
        f"/api/projects/{project['project_id']}/jobs/active"
    )

    assert active.status_code == 200
    assert active.json()["job_id"] == "job_pipeline"
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert stopped.status_code == 200
    assert stopped.json()["stage"] == "cancelled"
    assert no_longer_active.json() is None


def test_completed_job_cannot_be_paused(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Episode"}).json()
    app.state.store.save_job(
        Job(
            job_id="job_done",
            project_id=project["project_id"],
            stage="transcribed",
            progress=1,
        )
    )

    response = client.post("/api/jobs/job_done/pause")

    assert response.status_code == 409
    assert response.json()["detail"] == "Only an active job can be paused"


def test_timestamps_are_navigation_markers_and_clips_are_created_separately(
    tmp_path,
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post("/api/projects", json={"name": "Episode"}).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "duration_ms": 600_000}
    )
    app.state.store.save_project(project)

    imported = client.put(
        f"/api/projects/{project.project_id}/markers",
        json={"text": "00:00 Intro\n03:37 Main topic"},
    )
    created = client.post(
        f"/api/projects/{project.project_id}/clips",
        json={
            "navigation_marker_id": imported.json()[1]["marker_id"],
            "start_ms": 217_000,
            "end_ms": 300_000,
            "title": "Cut",
        },
    )
    duplicate = client.post(
        f"/api/projects/{project.project_id}/clips",
        json={
            "navigation_marker_id": imported.json()[1]["marker_id"],
            "start_ms": 217_000,
            "end_ms": 300_000,
            "title": "Duplicate",
        },
    )
    changed = client.patch(
        f"/api/projects/{project.project_id}/clips/{created.json()['clip_id']}",
        json={"selected": False},
    )

    assert imported.status_code == 200
    assert imported.json()[1]["timestamp_ms"] == 217_000
    assert created.status_code == 201
    assert (
        created.json()["navigation_marker_id"]
        == imported.json()[1]["marker_id"]
    )
    assert duplicate.status_code == 409
    assert created.json()["start_ms"] == 217_000
    assert len(client.get(f"/api/projects/{project.project_id}/clips").json()) == 1
    assert changed.json()["selected"] is False
    assert client.get(f"/api/projects/{project.project_id}/clips").json()[0][
        "selected"
    ] is False


def test_unchanged_navigation_markers_keep_clip_workspace_identity(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Episode"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "duration_ms": 600_000}
    )
    app.state.store.save_project(project)
    first = client.put(
        f"/api/projects/{project.project_id}/markers",
        json={"text": "00:00 Intro\n03:37 Main topic"},
    ).json()
    created = client.post(
        f"/api/projects/{project.project_id}/clips",
        json={
            "navigation_marker_id": first[1]["marker_id"],
            "start_ms": 217_000,
            "end_ms": 600_000,
            "title": "Main topic",
        },
    ).json()

    repeated = client.put(
        f"/api/projects/{project.project_id}/markers",
        json={"text": "00:00 Intro\n03:37 Main topic"},
    ).json()
    changed = client.patch(
        f"/api/projects/{project.project_id}/clips/{created['clip_id']}",
        json={"start_ms": 220_000},
    ).json()

    assert repeated[1]["marker_id"] == first[1]["marker_id"]
    assert changed["navigation_marker_id"] == first[1]["marker_id"]

    renamed = client.put(
        f"/api/projects/{project.project_id}/markers",
        json={"text": "00:00 Intro\n03:37 Renamed topic"},
    ).json()
    assert renamed[1]["marker_id"] == first[1]["marker_id"]

    client.put(
        f"/api/projects/{project.project_id}/markers",
        json={"text": "00:00 Intro"},
    )
    detached = client.get(
        f"/api/projects/{project.project_id}/clips"
    ).json()[0]
    assert detached["navigation_marker_id"] is None


def test_fractional_timeline_values_are_rounded_to_milliseconds(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Episode"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "duration_ms": 60_000}
    )
    app.state.store.save_project(project)
    app.state.store.save_clip(
        project.project_id,
        TimestampClip(
            clip_id="clip_a",
            start_ms=0,
            end_ms=30_000,
            title="Clip",
        ),
    )

    clip_response = client.patch(
        f"/api/projects/{project.project_id}/clips/clip_a",
        json={"end_ms": 25_000.6},
    )
    workspace_response = client.patch(
        f"/api/projects/{project.project_id}/workspace",
        json={"playhead_ms": 1_234.6},
    )

    assert clip_response.status_code == 200
    assert clip_response.json()["end_ms"] == 25_001
    assert workspace_response.status_code == 200
    assert workspace_response.json()["playhead_ms"] == 1_235


def test_timeline_info_and_waveform_use_project_media(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Episode"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={
            "media_name": "episode.mp4",
            "media_hash": "media-hash",
            "duration_ms": 60_000,
        }
    )
    app.state.store.save_project(project)
    project_dir = app.state.store.media_root / project.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "episode.mp4").write_bytes(b"video")
    (project_dir / "normalized.wav").write_bytes(b"audio")
    rendered = []

    monkeypatch.setattr(main, "media_frame_rate", lambda path: 29.97)

    def render(source, target, start_ms, end_ms, width, height):
        rendered.append(
            (source, start_ms, end_ms, width, height)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"waveform")

    monkeypatch.setattr(main, "render_waveform_image", render)

    info = client.get(
        f"/api/projects/{project.project_id}/timeline-info"
    )
    waveform = client.get(
        f"/api/projects/{project.project_id}/waveform.png",
        params={
            "start_ms": 10_000,
            "end_ms": 20_000,
            "width": 1024,
            "height": 96,
        },
    )

    assert info.status_code == 200
    assert info.json() == {
        "frame_rate": 29.97,
        "waveform_url": (
            f"/api/projects/{project.project_id}/waveform.png"
        ),
    }
    assert waveform.status_code == 200
    assert waveform.content == b"waveform"
    assert rendered == [
        (
            project_dir / "normalized.wav",
            10_000,
            20_000,
            1024,
            96,
        )
    ]


def test_navigation_marker_changes_preserve_audio_analysis_and_transcript(
    tmp_path,
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Episode"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={
            "media_name": "episode.mp4",
            "duration_ms": 600_000,
            "status": "transcribed",
        }
    )
    app.state.store.save_project(project)
    turn = DetectedSpeakerTurn(
        turn_id="turn_000001",
        start_ms=0,
        end_ms=600_000,
        speaker_id="SPEAKER_01",
    )
    app.state.store.save_speaker_turn(project.project_id, turn)
    app.state.store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_001",
            start_ms=1_000,
            end_ms=2_000,
            speaker_id="SPEAKER_01",
            raw_korean="test",
        ),
    )

    response = client.put(
        f"/api/projects/{project.project_id}/markers",
        json={"text": "00:00 Intro\n03:37 Main topic"},
    )

    assert response.status_code == 200
    assert app.state.store.list(
        "speaker_turn", project.project_id
    ) == [turn.model_dump()]
    assert len(app.state.store.list("segment", project.project_id)) == 1
    assert client.get(
        f"/api/projects/{project.project_id}"
    ).json()["status"] == "transcribed"


def test_boundary_change_invalidates_only_the_edited_clip(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Episode"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={
            "media_name": "episode.mp4",
            "duration_ms": 60_000,
            "status": "translated",
        }
    )
    app.state.store.save_project(project)
    app.state.store.save_speaker_turn(
        project.project_id,
        DetectedSpeakerTurn(
            turn_id="turn_001",
            start_ms=0,
            end_ms=60_000,
            speaker_id="SPEAKER_01",
        ),
    )
    for clip_id, start_ms, end_ms in (
        ("clip_a", 0, 30_000),
        ("clip_b", 30_000, 60_000),
    ):
        app.state.store.save_clip(
            project.project_id,
            TimestampClip(
                clip_id=clip_id,
                start_ms=start_ms,
                end_ms=end_ms,
                title=clip_id,
                status="translated",
                render_queued=True,
            ),
        )
        app.state.store.save_segment(
            project.project_id,
            Segment(
                segment_id=f"segment_{clip_id}",
                clip_id=clip_id,
                start_ms=start_ms,
                end_ms=end_ms,
                raw_korean=clip_id,
            ),
        )

    response = client.patch(
        f"/api/projects/{project.project_id}/clips/clip_a",
        json={"end_ms": 25_000},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "speakers_detected"
    assert response.json()["render_queued"] is False
    assert [
        item["segment_id"]
        for item in app.state.store.list("segment", project.project_id)
    ] == ["segment_clip_b"]
    assert len(app.state.store.list("speaker_turn", project.project_id)) == 1


def test_render_queue_can_be_cleared_in_one_action(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = Project.model_validate(
        client.post("/api/projects", json={"name": "Episode"}).json()
    )
    for index in range(3):
        app.state.store.save_clip(
            project.project_id,
            TimestampClip(
                clip_id=f"clip_{index}",
                start_ms=index * 10_000,
                end_ms=(index + 1) * 10_000,
                title=f"Clip {index}",
                render_queued=index < 2,
            ),
        )

    response = client.delete(
        f"/api/projects/{project.project_id}/render-queue"
    )

    assert response.status_code == 200
    assert all(not clip["render_queued"] for clip in response.json())
    assert all(
        not clip["render_queued"]
        for clip in app.state.store.list("clip", project.project_id)
    )


def test_existing_raw_transcript_is_migrated_to_clip_rows(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post("/api/projects", json={"name": "Episode"}).json()
    project = Project.model_validate(project_data).model_copy(
        update={"status": "transcribed", "duration_ms": 30_000}
    )
    app.state.store.save_project(project)
    for index, (start, end) in enumerate(
        [(0, 10_000), (10_000, 20_000), (20_000, 30_000)], start=1
    ):
        app.state.store.save_clip(
            project.project_id,
            TimestampClip(
                clip_id=f"clip_{index:03d}",
                start_ms=start,
                end_ms=end,
                title=f"Clip {index}",
            ),
        )
    app.state.store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_combined",
            start_ms=0,
            end_ms=30_000,
            raw_korean="one two three",
            words=[
                Word(text="one", start_ms=1_000, end_ms=2_000),
                Word(text="two", start_ms=11_000, end_ms=12_000),
                Word(text="three", start_ms=21_000, end_ms=22_000),
            ],
        ),
    )

    response = client.get(f"/api/projects/{project.project_id}/segments")

    assert response.status_code == 200
    assert [item["raw_korean"] for item in response.json()] == [
        "one",
        "two",
        "three",
    ]
    assert [item["clip_id"] for item in response.json()] == [
        "clip_001",
        "clip_002",
        "clip_003",
    ]


def test_create_project_persists_defaults(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/projects",
        json={
            "name": "Prediction Markets",
            "description": "A Korean crypto roundtable.",
            "speakers": ["민준", "서윤"],
            "translation_profile": "natural_conversation",
        },
    )

    assert response.status_code == 201
    project = response.json()
    assert project["name"] == "Prediction Markets"
    assert project["status"] == "draft"
    assert project["source_language"] == "ko"
    assert project["target_language"] == "en"
    assert client.get(f"/api/projects/{project['project_id']}").json()["speakers"] == [
        "민준",
        "서윤",
    ]


def test_editing_master_transcript_never_overwrites_raw_korean(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Episode 4"}).json()
    app.state.store.save_segment(
        project["project_id"],
        Segment(
            segment_id="seg_001",
            start_ms=1_000,
            end_ms=3_200,
            raw_korean="아니 그게 아니고",
            pass_2_korean="아니, 그게 아니고.",
        ),
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/segments/seg_001",
        json={"pass_2_korean": "아니, 내 말은 그게 아니고.", "locked": True},
    )

    assert response.status_code == 200
    assert response.json()["raw_korean"] == "아니 그게 아니고"
    assert response.json()["pass_2_korean"] == "아니, 내 말은 그게 아니고."
    assert response.json()["status"] == "user_edited"
    assert response.json()["locked"] is True


def test_openrouter_setup_persists_without_returning_the_api_key(tmp_path):
    client = TestClient(create_app(tmp_path))

    saved = client.put(
        "/api/settings/openrouter",
        json={"api_key": "sk-or-v1-private-key"},
    )

    assert saved.status_code == 200
    assert saved.json()["openrouter_configured"] is True
    assert "api_key" not in saved.json()
    assert client.get("/api/settings/openrouter").json()["openrouter_configured"] is True


def test_openrouter_key_update_preserves_existing_model_settings(tmp_path):
    client = TestClient(create_app(tmp_path))

    configured = client.put(
        "/api/settings/openrouter",
        json={
            "correction_model": "openai/gpt-5.4-mini",
            "translation_model": "anthropic/claude-sonnet-4.6",
        },
    )
    saved_key = client.put(
        "/api/settings/openrouter",
        json={"api_key": "sk-or-v1-private-key"},
    )

    assert configured.status_code == 200
    assert saved_key.json()["correction_model"] == "openai/gpt-5.4-mini"
    assert saved_key.json()["translation_model"] == "anthropic/claude-sonnet-4.6"


def test_speaker_can_be_renamed_without_changing_its_id(tmp_path):
    client = TestClient(create_app(tmp_path))
    project = client.post(
        "/api/projects",
        json={"name": "Interview", "speakers": ["Speaker 01"]},
    ).json()

    response = client.patch(
        f"/api/projects/{project['project_id']}/speakers/SPEAKER_01",
        json={"name": "Host"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Host"
    assert response.json()["speaker_id"] == "SPEAKER_01"


def test_interrupted_job_is_recovered_when_app_reopens(tmp_path):
    first_app = create_app(tmp_path)
    project = TestClient(first_app).post(
        "/api/projects", json={"name": "Interrupted"}
    ).json()
    first_app.state.store.save_job(
        Job(
            job_id="job_interrupted",
            project_id=project["project_id"],
            stage="transcribing",
            progress=0.4,
        )
    )

    reopened = create_app(tmp_path)
    recovered = reopened.state.store.get("job", "job_interrupted")

    assert recovered["stage"] == "failed"
    assert recovered["paused"] is False
    assert "interrupted" in recovered["error"].lower()
    assert (
        TestClient(reopened)
        .get(f"/api/projects/{project['project_id']}/jobs/active")
        .json()
        is None
    )


def test_segment_updates_are_project_scoped_and_keep_raw_timing_immutable(
    tmp_path,
):
    app = create_app(tmp_path)
    client = TestClient(app)
    first = client.post(
        "/api/projects", json={"name": "First"}
    ).json()
    second = client.post(
        "/api/projects", json={"name": "Second"}
    ).json()
    app.state.store.save_project(
        Project.model_validate(second).model_copy(
            update={"duration_ms": 10_000}
        )
    )
    app.state.store.save_segment(
        second["project_id"],
        Segment(
            segment_id="seg_owned",
            start_ms=1_000,
            end_ms=2_000,
            raw_korean="original",
        ),
    )

    wrong_project = client.patch(
        f"/api/projects/{first['project_id']}/segments/seg_owned",
        json={"pass_2_korean": "edited"},
    )
    timing_edit = client.patch(
        f"/api/projects/{second['project_id']}/segments/seg_owned",
        json={"start_ms": 1_001, "end_ms": 2_001},
    )
    invalid_speaker = client.patch(
        f"/api/projects/{second['project_id']}/segments/seg_owned",
        json={"speaker_id": "NOT_IN_PROJECT"},
    )
    assert wrong_project.status_code == 404
    assert timing_edit.status_code == 422
    assert invalid_speaker.status_code == 422
    saved = app.state.store.get("segment", "seg_owned")
    assert saved["start_ms"] == 1_000
    assert saved["end_ms"] == 2_000


def test_project_and_media_replacement_are_blocked_during_active_job(
    tmp_path,
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Busy"}
    ).json()
    app.state.store.save_job(
        Job(
            job_id="job_busy",
            project_id=project["project_id"],
            stage="transcribing",
        )
    )

    replaced = client.post(
        f"/api/projects/{project['project_id']}/media",
        files={"media": ("replacement.mp4", b"video", "video/mp4")},
    )
    deleted = client.delete(
        f"/api/projects/{project['project_id']}"
    )

    assert replaced.status_code == 409
    assert deleted.status_code == 409
    assert app.state.store.get("project", project["project_id"]) is not None


def test_reserved_media_filename_cannot_overwrite_derived_audio(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Reserved filename"}
    ).json()
    monkeypatch.setattr(main, "media_duration_ms", lambda _path: 1_000)
    monkeypatch.setattr(
        main,
        "normalize_audio",
        lambda _source, output: output.write_bytes(b"normalized"),
    )
    monkeypatch.setattr(
        main,
        "prepare_diarization_audio",
        lambda _source, output: output.write_bytes(b"diarization"),
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/media",
        files={"media": ("normalized.wav", b"source", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["media_name"] == "source-normalized.wav"
    project_dir = app.state.store.media_root / project["project_id"]
    assert (project_dir / "source-normalized.wav").read_bytes() == b"source"
    assert (project_dir / "normalized.wav").read_bytes() == b"normalized"


def test_media_replacement_resets_timeline_and_preserves_settings(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects",
        json={
            "name": "Keep this project",
            "custom_instructions": "Keep the hosts conversational.",
            "speakers": ["Original host"],
            "expected_speaker_count": 3,
            "subtitle_style": {"font_size": 66, "font_family": "Pretendard"},
        },
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={
            "media_name": "old.mp4",
            "media_hash": "old-hash",
            "media_url": f"/media/{project_data['project_id']}/old.mp4",
            "duration_ms": 60_000,
            "status": "translated",
        }
    )
    project_id = project.project_id
    app.state.store.save_project(project)
    app.state.store.save_clip(
        project_id,
        TimestampClip(
            clip_id="clip_old",
            navigation_marker_id="marker_old",
            start_ms=1_000,
            end_ms=10_000,
            title="Old clip",
            opened=True,
            render_queued=True,
        ),
    )
    app.state.store.save_segment(
        project_id,
        Segment(
            segment_id="segment_old",
            clip_id="clip_old",
            start_ms=1_000,
            end_ms=2_000,
            raw_korean="old transcript",
            english="old translation",
        ),
    )
    app.state.store.save_speaker_turn(
        project_id,
        DetectedSpeakerTurn(
            turn_id="turn_old",
            start_ms=0,
            end_ms=2_000,
            speaker_id="SPEAKER_01",
        ),
    )
    app.state.store.put(
        "speaker",
        project_id,
        f"{project_id}:SPEAKER_01",
        {"speaker_id": "SPEAKER_01", "name": "Renamed host"},
    )
    app.state.store.put(
        "marker",
        project_id,
        f"{project_id}:marker_old",
        {"marker_id": "marker_old", "timestamp_ms": 1_000, "title": "Old"},
    )
    app.state.store.put(
        "caption_track", project_id, f"{project_id}:en", {"old": True}
    )
    app.state.store.put(
        "post_copy", project_id, f"{project_id}:clip_old", {"old": True}
    )
    app.state.store.put(
        "job", project_id, "job_old", {"job_id": "job_old"}
    )
    app.state.store.put(
        "glossary", project_id, "term_keep", {"entry_id": "term_keep"}
    )
    app.state.store.save_workspace(
        project_id,
        ProjectWorkspaceState(
            active_clip_id="clip_old",
            selected_segment_id="segment_old",
            sidebar_tab="style",
            playhead_ms=5_000,
            playback_rate=1.5,
            transcript_query="old query",
            warning_only=True,
            video_quality="high",
            timeline_zoom=8,
        ),
    )
    project_dir = app.state.store.media_root / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "old.mp4").write_bytes(b"old video")
    for cache_name in ("waveforms", "whisper", ".video-export-work"):
        cache = project_dir / cache_name
        cache.mkdir()
        (cache / "old.cache").write_bytes(b"old")
    (project_dir / "enrolled-clips.wav").write_bytes(b"old voices")
    monkeypatch.setattr(main, "media_duration_ms", lambda _path: 90_000)
    monkeypatch.setattr(
        main,
        "normalize_audio",
        lambda _source, output: output.write_bytes(b"normalized"),
    )
    monkeypatch.setattr(
        main,
        "prepare_diarization_audio",
        lambda _source, output: output.write_bytes(b"diarization"),
    )

    response = client.post(
        f"/api/projects/{project_id}/media",
        files={"media": ("replacement.mp4", b"new video", "video/mp4")},
    )

    assert response.status_code == 200
    replaced = response.json()
    assert replaced["media_name"] == "replacement.mp4"
    assert replaced["duration_ms"] == 90_000
    assert replaced["custom_instructions"] == "Keep the hosts conversational."
    assert replaced["expected_speaker_count"] == 3
    assert replaced["subtitle_style"]["font_size"] == 66
    assert app.state.store.list("speaker", project_id)[0]["name"] == "Renamed host"
    assert app.state.store.list("glossary", project_id)[0]["entry_id"] == "term_keep"
    for kind in (
        "marker",
        "clip",
        "segment",
        "speaker_turn",
        "caption_track",
        "post_copy",
        "job",
    ):
        assert app.state.store.list(kind, project_id) == []
    workspace = client.get(f"/api/projects/{project_id}/workspace").json()
    assert workspace["active_clip_id"] is None
    assert workspace["sidebar_tab"] == "timestamps"
    assert workspace["playhead_ms"] == 0
    assert workspace["timeline_zoom"] == 1
    assert workspace["playback_rate"] == 1.5
    assert workspace["video_quality"] == "high"
    assert not (project_dir / "old.mp4").exists()
    assert (project_dir / "replacement.mp4").read_bytes() == b"new video"
    assert not (project_dir / "enrolled-clips.wav").exists()
    assert all(
        not (project_dir / cache_name).exists()
        for cache_name in ("waveforms", "whisper", ".video-export-work")
    )
