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


def test_oauth1_signature_changes_with_query_params():
    """Query params must be folded into the signature base string. Two
    signatures that differ only in signed query params must not be equal;
    if the params are dropped from signing, both calls yield the same
    signature and X rejects the request with 401 (the media-upload bug)."""
    import hmac
    import hashlib

    base = xapi._oauth1_header(
        "GET", "https://api.x.com/2/users/me", _account(),
        query_params={"user.fields": "username"},
    )
    # Rebuild what the signature WOULD be without query params and compare
    no_params = xapi._oauth1_header(
        "GET", "https://api.x.com/2/users/me", _account()
    )

    def sig_of(header):
        for part in header.split(", "):
            if part.startswith("oauth_signature="):
                return part.split("=", 1)[1]
        return None

    assert sig_of(base) != sig_of(no_params)


def test_verify_credentials_sends_query_params_in_signature(monkeypatch):
    """The /2/users/me verification call must sign its user.fields query
    param; otherwise X returns 401 for perfectly valid keys."""
    captured = {}

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.return_value = _ok(
        {"data": {"id": "1", "username": "someuser"}}
    )

    def fake_header(method, url, account, query_params=None):
        captured["method"] = method
        captured["url"] = url
        captured["query_params"] = query_params
        return "OAuth oauth_signature=\"fake\""

    monkeypatch.setattr(xapi.httpx, "Client", lambda **kw: client)
    monkeypatch.setattr(xapi, "_oauth1_header", fake_header)

    username = xapi.verify_credentials(_account())
    assert username == "someuser"
    assert captured["url"].endswith("/2/users/me")
    assert captured["query_params"] == {"user.fields": "username"}
    # and the outgoing request carried the same params
    assert client.get.call_args.kwargs["params"] == {"user.fields": "username"}


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

    # v2 chunked convention: dedicated sub-paths, no "command" field.
    # INIT is JSON to /initialize; APPEND is multipart to /{id}/append;
    # FINALIZE has no body at /{id}/finalize.
    init_args = client.post.call_args_list[0]
    append_args = client.post.call_args_list[1]
    finalize_args = client.post.call_args_list[3]
    assert init_args.args[0].endswith("/2/media/upload/initialize")
    assert init_args.kwargs["json"]["total_bytes"] > 0
    assert append_args.args[0].endswith("/m123/append")
    assert "media" in append_args.kwargs["files"]
    assert finalize_args.args[0].endswith("/m123/finalize")
    assert "files" not in finalize_args.kwargs
    assert "json" not in finalize_args.kwargs


def test_upload_uses_v2_chunked_subpaths(monkeypatch, tmp_path):
    """Regression: the v2 chunked upload uses dedicated sub-paths.
    POSTing command=INIT multipart to the one-shot /2/media/upload URL
    fails with 'Missing media field in JSON'."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 10)

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post.side_effect = [
        _ok({"data": {"id": "m7"}}),          # INIT
        _ok({}, status=204),                   # APPEND
        _ok({"data": {"processing_info": {"state": "succeeded"}}}),  # FINALIZE
    ]
    client.get.return_value = _ok(
        {"data": {"processing_info": {"state": "succeeded"}}}
    )
    monkeypatch.setattr(xapi.httpx, "Client", lambda **kw: client)

    media_id = xapi._upload_media(client, _account(), video)
    assert media_id == "m7"

    init_args = client.post.call_args_list[0]
    append_args = client.post.call_args_list[1]
    finalize_args = client.post.call_args_list[2]
    # INIT: JSON body, correct sub-path
    assert init_args.args[0] == xapi.MEDIA_UPLOAD_INIT_URL
    assert init_args.kwargs["json"] == {
        "total_bytes": 10,
        "media_type": "video/mp4",
        "media_category": "tweet_video",
    }
    # APPEND: media_id in path, chunk in "media" part, index in form field
    assert append_args.args[0] == xapi.MEDIA_UPLOAD_URL + "/m7/append"
    assert append_args.kwargs["data"] == {"segment_index": "0"}
    assert "media" in append_args.kwargs["files"]
    # FINALIZE: id in path, no body at all
    assert finalize_args.args[0] == xapi.MEDIA_UPLOAD_URL + "/m7/finalize"
    assert "json" not in finalize_args.kwargs
    assert "files" not in finalize_args.kwargs
    assert "data" not in finalize_args.kwargs


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
