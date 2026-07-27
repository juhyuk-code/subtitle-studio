from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app import main
from backend.app.models import Job, Project, Segment


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
    app.state.store.save_job(
        Job(job_id="job_active", project_id=project.project_id, stage="transcribing")
    )
    monkeypatch.setattr(main, "run_transcription", lambda *args: None)

    response = client.post(f"/api/projects/{project.project_id}/transcribe")

    assert response.status_code == 409
    assert response.json()["detail"] == "Transcription is already running"


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


def test_timestamp_clips_are_imported_and_selection_is_persisted(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post("/api/projects", json={"name": "Episode"}).json()
    project = Project.model_validate(project_data).model_copy(
        update={"media_name": "episode.mp4", "duration_ms": 600_000}
    )
    app.state.store.save_project(project)

    imported = client.put(
        f"/api/projects/{project.project_id}/clips",
        json={"text": "00:00 Intro\n03:37 Main topic"},
    )
    changed = client.patch(
        f"/api/projects/{project.project_id}/clips/clip_002",
        json={"selected": False},
    )

    assert imported.status_code == 200
    assert imported.json()[0]["end_ms"] == 217_000
    assert changed.json()["selected"] is False
    assert client.get(f"/api/projects/{project.project_id}/clips").json()[1][
        "selected"
    ] is False


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
