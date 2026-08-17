"""Tests for the official X API posting backend (xapi)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from backend.app import xapi
from backend.app.models import ScheduledPost, XAccountSettings


def _account() -> XAccountSettings:
    return XAccountSettings(
        method="api",
        api_key="ck",
        api_secret="cs",
        access_token="at",
        access_secret="as",
    )


def _post(video_path: str | None = None) -> ScheduledPost:
    return ScheduledPost(
        project_id="prj_1",
        text="hello world",
        scheduled_at="2030-01-01T00:00:00+00:00",
        video_path=video_path,
        method="api",
    )


def test_oauth1_header_contains_signature_and_credentials():
    header = xapi._oauth1_header("POST", "https://api.x.com/2/tweets", _account())
    assert header.startswith("OAuth ")
    assert 'oauth_consumer_key="ck"' in header
    assert 'oauth_token="at"' in header
    assert "oauth_signature=" in header
    assert 'oauth_signature_method="HMAC-SHA1"' in header


def test_require_credentials_rejects_incomplete():
    incomplete = XAccountSettings(method="api", api_key="ck")
    with pytest.raises(xapi.XApiError, match="incomplete"):
        xapi._require_credentials(incomplete)


def _ok(payload: dict | None = None, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json=payload if payload is not None else {}, request=httpx.Request("POST", "https://api.x.com")
    )


def test_post_to_x_text_only(monkeypatch):
    client = MagicMock()
    client.post.return_value = _ok({"data": {"id": "999"}})
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(xapi.httpx, "Client", lambda **kw: client)

    url = xapi.post_to_x(_post(video_path=None), _account())
    assert url == "https://x.com/i/web/status/999"
    # only one call: tweet create (no media upload)
    assert client.post.call_count == 1
    body = client.post.call_args.kwargs["json"]
    assert body["text"] == "hello world"
    assert "media" not in body


def test_post_to_x_with_video_runs_full_chunked_flow(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * (xapi.CHUNK_SIZE + 10))  # forces 2 chunks

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    # INIT -> media id; APPEND x2 -> 204; FINALIZE -> processing done;
    # STATUS -> succeeded; tweets -> id
    client.post.side_effect = [
        _ok({"media_id_string": "m123"}),  # INIT
        _ok({}, status=204),               # APPEND 0
        _ok({}, status=204),               # APPEND 1
        _ok({"processing_info": {"state": "succeeded"}}),  # FINALIZE
        _ok({"data": {"id": "555"}}),      # tweets
    ]
    client.get.return_value = _ok({"processing_info": {"state": "succeeded"}})
    monkeypatch.setattr(xapi.httpx, "Client", lambda **kw: client)

    url = xapi.post_to_x(_post(video_path=str(video)), _account())
    assert url == "https://x.com/i/web/status/555"

    # 5 POSTs: INIT, APPEND, APPEND, FINALIZE, tweets
    assert client.post.call_count == 5
    tweet_body = client.post.call_args.kwargs["json"]
    assert tweet_body["media"]["media_ids"] == ["m123"]


def test_post_to_x_missing_video_raises(monkeypatch):
    monkeypatch.setattr(xapi.httpx, "Client", lambda **kw: MagicMock())
    with pytest.raises(xapi.XApiError, match="Video file not found"):
        xapi.post_to_x(_post(video_path="/nonexistent/clip.mp4"), _account())


def test_media_processing_failure_raises(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 100)

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post.side_effect = [
        _ok({"media_id_string": "m9"}),      # INIT
        _ok({}, status=204),                  # APPEND
        _ok({"processing_info": {"state": "failed", "error": {"message": "bad codec"}}}),  # FINALIZE
    ]
    # STATUS returns failed too
    client.get.return_value = _ok(
        {"processing_info": {"state": "failed", "error": {"message": "bad codec"}}}
    )
    monkeypatch.setattr(xapi.httpx, "Client", lambda **kw: client)
    monkeypatch.setattr(xapi.time, "sleep", lambda s: None)

    with pytest.raises(xapi.XApiError, match="processing failed|bad codec"):
        xapi.post_to_x(_post(video_path=str(video)), _account())


def test_raise_for_status_extracts_detail():
    response = httpx.Response(
        403,
        json={"detail": "You currently have access to a subset"},
        request=httpx.Request("POST", "https://api.x.com/2/tweets"),
    )
    with pytest.raises(xapi.XApiError, match="subset"):
        xapi._raise_for_status(response, "create post")
