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


def test_x_settings_update_marks_configured(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.xapi.verify_credentials", lambda settings: "testuser"
    )
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
    body = response.json()
    assert body["configured"] is True
    assert body["verified_username"] == "testuser"


def test_x_settings_update_rejects_masked_credentials(client):
    response = client.put(
        "/api/settings/x",
        json={
            "api_key": "**********",
            "api_secret": "s",
            "access_token": "t",
            "access_secret": "sec",
        },
    )
    assert response.status_code == 422
    assert "masked placeholder" in response.json()["detail"]


def test_x_settings_update_rejects_keys_that_fail_verification(
    client, monkeypatch
):
    from backend.app import xapi

    def fail(_settings):
        raise xapi.XApiError("X rejected these credentials (HTTP 401 Unauthorized).")

    monkeypatch.setattr("backend.app.xapi.verify_credentials", fail)
    response = client.put(
        "/api/settings/x",
        json={
            "api_key": "k",
            "api_secret": "s",
            "access_token": "t",
            "access_secret": "sec",
        },
    )
    assert response.status_code == 422
    assert "401" in response.json()["detail"]


def test_credentials_survive_save_load_roundtrip(tmp_path):
    """SecretStr.model_dump_json() masks secrets as '**********' by default;
    save_account_settings must store the real values so posting can use them."""
    from pydantic import SecretStr

    from backend.app.store import Store
    from backend.app.models import XAccountSettings

    store = Store(tmp_path)
    settings = XAccountSettings(
        method="api",
        api_key=SecretStr("real_key_abc"),
        api_secret=SecretStr("real_secret_def"),
        access_token=SecretStr("real_token_ghi"),
        access_secret=SecretStr("real_access_jkl"),
        verified_username="someuser",
    )
    xpost.save_account_settings(store, settings)

    loaded = xpost.load_account_settings(store)
    assert loaded.api_key.get_secret_value() == "real_key_abc"
    assert loaded.api_secret.get_secret_value() == "real_secret_def"
    assert loaded.access_token.get_secret_value() == "real_token_ghi"
    assert loaded.access_secret.get_secret_value() == "real_access_jkl"
    assert loaded.verified_username == "someuser"
    # and the raw stored payload must not contain the mask
    raw = store.get_setting("X_ACCOUNT_SETTINGS")
    assert "**********" not in raw


def test_account_is_configured_rejects_masked_values():
    from backend.app.models import XAccountSettings

    masked = XAccountSettings(
        method="api",
        api_key="**********",
        api_secret="**********",
        access_token="**********",
        access_secret="**********",
    )
    assert xpost.account_is_configured(masked) is False
    assert (
        xpost.account_is_configured(XAccountSettings(method="api")) is False
    )


def test_validate_credential_value_strips_whitespace_and_rejects_masks():
    assert xpost.validate_credential_value("api key", "  abc123  ") == "abc123"
    with pytest.raises(xpost.XAccountError):
        xpost.validate_credential_value("api key", "**********")
    with pytest.raises(xpost.XAccountError):
        xpost.validate_credential_value("api key", "")
    with pytest.raises(xpost.XAccountError):
        xpost.validate_credential_value("access token", "YOUR_TOKEN_HERE")


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


def test_scheduled_post_accepts_premium_length_text(client):
    # X Premium allows up to 25,000 characters; a 5,000-char post must be
    # accepted (the old 4,000 cap would have rejected it).
    long_text = "word " * 1000  # 5,000 characters
    created = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": "prj_1",
            "text": long_text,
            "scheduled_at": _future(120),
        },
    )
    assert created.status_code == 201
    assert len(created.json()["text"]) == len(long_text)


def test_scheduled_post_rejects_text_over_25k(client):
    too_long = "x" * 25_001
    created = client.post(
        "/api/scheduled-posts",
        json={
            "project_id": "prj_1",
            "text": too_long,
            "scheduled_at": _future(120),
        },
    )
    assert created.status_code == 422


def _configure_credentials(client) -> None:
    """Put usable-looking credentials straight into the store so scheduler
    tests exercise posting mechanics without hitting the credential gate."""
    from backend.app.models import XAccountSettings

    xpost.save_account_settings(
        client.app.state.store,
        XAccountSettings(
            method="api",
            api_key="test_key_value",
            api_secret="test_secret_value",
            access_token="test_token_value",
            access_secret="test_access_secret",
        ),
    )


def test_due_post_is_published_via_registered_poster(client, monkeypatch):
    published: list[str] = []

    def fake_poster(post: ScheduledPost, account) -> str:
        published.append(post.text)
        return "https://x.com/test/status/123"

    xpost.register_poster("api", fake_poster)
    try:
        _configure_credentials(client)
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


def test_post_waits_for_credentials_instead_of_burning_retries(client):
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
    # No usable credentials: the post stays pending with zero attempts and
    # publishes automatically once working keys are saved.
    assert post["status"] == "pending"
    assert post["attempts"] == 0
    assert post["error"] is None


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
        _configure_credentials(client)
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
