"""Official X (Twitter) API posting backend.

Implements the ``Poster`` protocol from ``xpost`` using the X API v2 chunked
media upload plus tweet create, signed with OAuth 1.0a User Context
(consumer key/secret + access token/secret) — the credential set already stored
in ``XAccountSettings``.

No third-party dependencies: OAuth 1.0a HMAC-SHA1 signing is done with the
standard library, and HTTP via httpx (already a project dependency).

Registered as the ``"api"`` method::

    from . import xapi, xpost
    xpost.register_poster("api", xapi.post_to_x)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

from .models import ScheduledPost, XAccountSettings

API_BASE = "https://api.x.com"
MEDIA_UPLOAD_URL = f"{API_BASE}/2/media/upload"
TWEETS_URL = f"{API_BASE}/2/tweets"

# Chunk size for the APPEND phase. 4 MiB keeps us well under request-size
# errors while limiting the number of billed requests per video.
CHUNK_SIZE = 4 * 1024 * 1024
MEDIA_STATUS_TIMEOUT_S = 120
HTTP_TIMEOUT_S = 60


class XApiError(RuntimeError):
    pass


# --- OAuth 1.0a ------------------------------------------------------------


def _percent_encode(value: str) -> str:
    return quote(str(value), safe="~-._")


def _oauth1_header(
    method: str,
    url: str,
    account: XAccountSettings,
    extra_params: dict[str, str] | None = None,
) -> str:
    """Build an OAuth 1.0a Authorization header (HMAC-SHA1 signature)."""
    consumer_key = _secret(account.api_key)
    consumer_secret = _secret(account.api_secret)
    token = _secret(account.access_token)
    token_secret = _secret(account.access_secret)

    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    signing_params = {**oauth_params, **(extra_params or {})}
    param_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(signing_params.items())
    )
    base_string = "&".join(
        [method.upper(), _percent_encode(url), _percent_encode(param_string)]
    )
    signing_key = (
        f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"
    )
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode(), base_string.encode(), hashlib.sha1
        ).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature
    return "OAuth " + ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )


def _secret(value) -> str:
    if value is None:
        return ""
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)


def _require_credentials(account: XAccountSettings) -> None:
    if not all(
        [
            _secret(account.api_key),
            _secret(account.api_secret),
            _secret(account.access_token),
            _secret(account.access_secret),
        ]
    ):
        raise XApiError(
            "X API credentials are incomplete. Add API key, API secret, "
            "access token and access secret in Settings."
        )


# --- media upload ----------------------------------------------------------


def _media_category(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return "tweet_image"
    if mime and mime.startswith("video/"):
        return "tweet_video"
    return "tweet_video"


def _upload_media(client: httpx.Client, account: XAccountSettings, path: Path) -> str:
    """Chunked upload: INIT -> APPEND*n -> FINALIZE -> STATUS. Returns media_id."""
    total_bytes = path.stat().st_size
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "video/mp4"

    # INIT (query params are part of the OAuth signature)
    init_params = {
        "command": "INIT",
        "total_bytes": str(total_bytes),
        "media_type": mime,
        "media_category": _media_category(path),
    }
    response = client.post(
        MEDIA_UPLOAD_URL,
        params=init_params,
        headers={
            "Authorization": _oauth1_header(
                "POST", _url_with_query(MEDIA_UPLOAD_URL, init_params), account
            )
        },
    )
    _raise_for_status(response, "initialize media upload")
    media_id = response.json().get("media_id_string") or response.json().get(
        "data", {}
    ).get("id")
    if not media_id:
        raise XApiError(f"X did not return a media id: {response.text}")
    media_id = str(media_id)

    # APPEND
    with path.open("rb") as handle:
        segment_index = 0
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            append_params = {
                "command": "APPEND",
                "media_id": media_id,
                "segment_index": str(segment_index),
            }
            response = client.post(
                MEDIA_UPLOAD_URL,
                params=append_params,
                files={"media": chunk},
                headers={
                    "Authorization": _oauth1_header(
                        "POST",
                        _url_with_query(MEDIA_UPLOAD_URL, append_params),
                        account,
                    )
                },
            )
            # APPEND returns 2xx with an empty body on success
            _raise_for_status(response, f"append media chunk {segment_index}")
            segment_index += 1

    # FINALIZE
    finalize_params = {"command": "FINALIZE", "media_id": media_id}
    response = client.post(
        MEDIA_UPLOAD_URL,
        params=finalize_params,
        headers={
            "Authorization": _oauth1_header(
                "POST", _url_with_query(MEDIA_UPLOAD_URL, finalize_params), account
            )
        },
    )
    _raise_for_status(response, "finalize media upload")

    _wait_for_media(client, account, media_id)
    return media_id


def _wait_for_media(
    client: httpx.Client, account: XAccountSettings, media_id: str
) -> None:
    """Poll STATUS until processing succeeds (videos are transcoded async)."""
    deadline = time.time() + MEDIA_STATUS_TIMEOUT_S
    check_after = 1
    while time.time() < deadline:
        status_params = {"command": "STATUS", "media_id": media_id}
        response = client.get(
            MEDIA_UPLOAD_URL,
            params=status_params,
            headers={
                "Authorization": _oauth1_header(
                    "GET", _url_with_query(MEDIA_UPLOAD_URL, status_params), account
                )
            },
        )
        if response.status_code == 404:
            # Some media report ready as 404; give it a moment and proceed.
            time.sleep(2)
            return
        _raise_for_status(response, "check media status")
        processing = response.json().get("processing_info") or {}
        state = processing.get("state", "succeeded")
        if state == "succeeded":
            return
        if state == "failed":
            detail = (processing.get("error") or {}).get("message", "unknown error")
            raise XApiError(f"X media processing failed: {detail}")
        check_after = int(processing.get("check_after_secs") or check_after)
        time.sleep(min(check_after, 10))
    raise XApiError("Timed out waiting for X to finish processing the media")


# --- tweet create ----------------------------------------------------------


def post_to_x(post: ScheduledPost, account: XAccountSettings) -> str:
    """Publish a scheduled post. Returns the URL of the created post."""
    _require_credentials(account)
    with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
        media_id = None
        if post.video_path:
            video = Path(post.video_path)
            if not video.is_file():
                raise XApiError(f"Video file not found: {post.video_path}")
            media_id = _upload_media(client, account, video)

        body: dict = {"text": post.text}
        if media_id:
            body["media"] = {"media_ids": [media_id]}
        response = client.post(
            TWEETS_URL,
            json=body,
            headers={
                "Authorization": _oauth1_header("POST", TWEETS_URL, account),
                "Content-Type": "application/json",
            },
        )
        _raise_for_status(response, "create post")
        data = response.json().get("data", {})
        tweet_id = data.get("id")
        if not tweet_id:
            raise XApiError(f"X did not return a post id: {response.text}")
        return f"https://x.com/i/web/status/{tweet_id}"


# --- helpers ---------------------------------------------------------------


def _url_with_query(url: str, params: dict[str, str]) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{url}?{query}"


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    detail = response.text
    try:
        payload = response.json()
        detail = (
            payload.get("detail")
            or payload.get("title")
            or payload.get("error")
            or str(payload.get("errors") or payload)
        )
    except ValueError:
        pass
    raise XApiError(f"Could not {action} (HTTP {response.status_code}): {detail}")
