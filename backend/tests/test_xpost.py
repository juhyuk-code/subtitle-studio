"""Tests for the X posting scheduler and settings endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import xpost
from backend.app.main import create_app
from backend.app.models import ScheduledPost


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTITLE_STUDIO_DATA", str(tmp_path / "data"))
    app = create_app(data_root=tmp_path / "data")
    return TestClient(app)


def _future(minutes: int = 60) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat()


def _past(minutes: int = 1) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes)
    ).isoformat()


def test_x_settings_defaults_to_unconfigured(client):
    status = client.get("/api/settings/x").json()
    assert status["method"] == "api"
    assert status["configured"] is False


def test_x_settings_update_marks_configured(client):
    response = client.put(
        "/api/settings/x",
        json={
            "api_key": "k",
            "api_secret": "s",
            "access_token": "t",
            "access_secret": "sec",
        },
    )
    assert response.status_code == 200
    assert response.json()["configured"] is True


def test_create_and_list_scheduled_post(client):
    created = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": "prj_1",
            "clip_id": "clip_1",
            "text": "hello world",
            "scheduled_at": _future(120),
        },
    )
    assert created.status_code == 201
    post = created.json()
    assert post["status"] == "pending"
    assert post["method"] == "api"

    listed = client.get("/api/scheduled-posts").json()
    assert len(listed) == 1
    assert listed[0]["post_id"] == post["post_id"]


def test_due_post_is_published_via_registered_poster(client, monkeypatch):
    published: list[str] = []

    def fake_poster(post: ScheduledPost, account) -> str:
        published.append(post.text)
        return "https://x.com/test/status/123"

    xpost.register_poster("api", fake_poster)
    try:
        created = client.post(
            "/api/scheduled-posts",
            json={
                "project_id": "prj_1",
                "text": "due now",
                "scheduled_at": _past(1),
            },
        ).json()

        result = client.post("/api/scheduled-posts/publish-due").json()
        assert result["posted"] == 1
        assert published == ["due now"]

        post = client.get(f"/api/scheduled-posts/{created['post_id']}").json()
        assert post["status"] == "posted"
        assert post["result_url"] == "https://x.com/test/status/123"
        assert post["posted_at"] is not None
    finally:
        xpost._POSTERS.pop("api", None)


def test_future_post_is_not_published(client):
    client.post(
        "/api/scheduled-posts",
        json={
            "project_id": "prj_1",
            "text": "later",
            "scheduled_at": _future(120),
        },
    )
    result = client.post("/api/scheduled-posts/publish-due").json()
    assert result["posted"] == 0


def test_post_without_credentials_retries_then_pending(client):
    created = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": "prj_1",
            "text": "no backend",
            "scheduled_at": _past(1),
        },
    ).json()
    result = client.post("/api/scheduled-posts/publish-due").json()
    assert result["posted"] == 0
    post = client.get(f"/api/scheduled-posts/{created['post_id']}").json()
    assert post["status"] == "pending"  # retried, not yet failed
    assert "credentials are incomplete" in post["error"]


def test_cancel_post(client):
    created = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": "prj_1",
            "text": "cancel me",
            "scheduled_at": _future(60),
        },
    ).json()
    cancelled = client.post(
        f"/api/scheduled-posts/{created['post_id']}/cancel"
    ).json()
    assert cancelled["status"] == "cancelled"


def test_cannot_edit_posted_post(client, monkeypatch):
    xpost.register_poster("api", lambda post, account: "https://x.com/x/1")
    try:
        created = client.post(
            "/api/scheduled-posts",
            json={
                "project_id": "prj_1",
                "text": "already posted",
                "scheduled_at": _past(1),
            },
        ).json()
        client.post("/api/scheduled-posts/publish-due")
        response = client.patch(
            f"/api/scheduled-posts/{created['post_id']}",
            json={"text": "new text"},
        )
        assert response.status_code == 409
    finally:
        xpost._POSTERS.pop("api", None)
