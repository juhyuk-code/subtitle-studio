"""Tests for the viral-potential scoring step + 10-minute scheduling guard."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import viral_score
from backend.app.main import create_app
from backend.app.models import PostCopy, Project, ProjectCreate, TimestampClip
from backend.app.store import Store


# --- viral scoring -----------------------------------------------------------


class FakeClip:
    def __init__(self, clip_id, title="", start_ms=0, end_ms=60000):
        self.clip_id = clip_id
        self.title = title
        self.start_ms = start_ms
        self.end_ms = end_ms


def test_score_one_clip_honors_llm_result(tmp_path, monkeypatch):
    store = Store(tmp_path)

    async def fake_openrouter(_store, stage, _prompt, payload):
        assert stage == "post_captioning"
        return {"viral_score": 88, "rationale": "Quotable hot take about markets"}

    monkeypatch.setattr("backend.app.viral_score.call_openrouter", fake_openrouter)

    clip = FakeClip("clip_1")
    result = asyncio.run(
        viral_score.score_one_clip(store, clip, [{"english": "text"}], None)
    )
    assert result.clip_id == "clip_1"
    assert result.score == 88
    assert "hot take" in result.rationale


def test_score_one_clip_clamps_out_of_range(tmp_path, monkeypatch):
    store = Store(tmp_path)

    async def fake_openrouter(_store, stage, _prompt, payload):
        return {"viral_score": 999, "rationale": ""}

    monkeypatch.setattr("backend.app.viral_score.call_openrouter", fake_openrouter)

    result = asyncio.run(viral_score.score_one_clip(store, FakeClip("c"), [], None))
    assert 0 <= result.score <= 100


def test_score_one_clip_falls_back_on_llm_error(tmp_path, monkeypatch):
    store = Store(tmp_path)

    async def broken(_store, stage, _prompt, payload):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("backend.app.viral_score.call_openrouter", broken)

    result = asyncio.run(viral_score.score_one_clip(store, FakeClip("c"), [], None))
    assert result.score == 50
    assert "error" in result.rationale.lower()


def test_persist_scores_writes_back_to_post_copy(tmp_path):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="P"))
    store.save_project(project)
    clip = TimestampClip(clip_id="clip_1", title="C1", start_ms=0, end_ms=10000)
    store.save_clip(project.project_id, clip)
    post_copy = PostCopy(
        clip_id="clip_1",
        headline="H",
        body="B",
        generated_at="2026-08-26T00:00:00+00:00",
        source_signature="sig",
    )
    store.save_post_copy(project.project_id, post_copy)

    batch = viral_score.ViralScoreBatch(
        results=[viral_score.ViralScoreResult(clip_id="clip_1", score=91, rationale="why")]
    )
    viral_score.persist_scores(store, project.project_id, batch)

    saved = PostCopy.model_validate(
        store.get("post_copy", f"{project.project_id}:clip_1")
    )
    assert saved.viral_score == 91
    assert saved.viral_rationale == "why"


def test_viral_score_endpoint_returns_ranked_rows(tmp_path):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="P"))
    store.save_project(project)
    for clip_id, start, end in [("c_high", 0, 5000), ("c_low", 5000, 10000)]:
        store.save_clip(project.project_id, TimestampClip(clip_id=clip_id, title=clip_id, start_ms=start, end_ms=end))
        store.save_post_copy(
            project.project_id,
            PostCopy(
                clip_id=clip_id,
                headline=f"{clip_id} headline",
                body="body",
                generated_at="2026-08-26T00:00:00+00:00",
                source_signature="sig",
                viral_score=90 if clip_id == "c_high" else 30,
                viral_rationale="r",
            ),
        )

    client = TestClient(create_app(tmp_path))
    response = client.get(f"/api/projects/{project.project_id}/viral-scores")
    assert response.status_code == 200
    data = response.json()
    assert data["scored"] == 2
    # Most viral first.
    assert data["clips"][0]["clip_id"] == "c_high"
    assert data["clips"][0]["viral_score"] == 90


# --- 10-minute guard ---------------------------------------------------------


def test_create_post_rejects_video_over_10_minutes(tmp_path, monkeypatch):
    from backend.app.main import media_duration_ms as real_duration

    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="P"))
    store.save_project(project)
    client = TestClient(create_app(tmp_path))

    fake_video = tmp_path / "long.mp4"
    fake_video.write_bytes(b"fake")

    # Stub the duration probe so the test doesn't need ffprobe on the runner.
    monkeypatch.setattr(
        "backend.app.main.media_duration_ms",
        lambda _path: 11 * 60 * 1000,  # 11 minutes -> over the 10-min limit
    )

    response = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": project.project_id,
            "clip_id": None,
            "text": "hello",
            "scheduled_at": "2026-12-31T12:00:00+00:00",
            "video_path": str(fake_video),
        },
    )
    assert response.status_code == 422
    assert "10-minute" in response.json()["detail"]


def test_create_post_allows_short_video(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="P"))
    store.save_project(project)
    client = TestClient(create_app(tmp_path))

    fake_video = tmp_path / "short.mp4"
    fake_video.write_bytes(b"fake")

    monkeypatch.setattr(
        "backend.app.main.media_duration_ms",
        lambda _path: 3 * 60 * 1000,  # 3 minutes
    )

    response = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": project.project_id,
            "clip_id": None,
            "text": "hello",
            "scheduled_at": "2026-12-31T12:00:00+00:00",
            "video_path": str(fake_video),
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "pending"


def test_create_post_tolerates_missing_ffprobe(tmp_path, monkeypatch):
    """If ffprobe is unavailable, do not crash the endpoint; allow the post."""
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="P"))
    store.save_project(project)
    client = TestClient(create_app(tmp_path))

    fake_video = tmp_path / "unknown.mp4"
    fake_video.write_bytes(b"fake")

    monkeypatch.setattr(
        "backend.app.main.media_duration_ms",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("ffprobe")),
    )

    response = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": project.project_id,
            "clip_id": None,
            "text": "hello",
            "scheduled_at": "2026-12-31T12:00:00+00:00",
            "video_path": str(fake_video),
        },
    )
    assert response.status_code == 201
