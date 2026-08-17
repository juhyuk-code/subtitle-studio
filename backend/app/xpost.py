"""X (Twitter) posting and scheduling.

The posting *method* is pluggable. The scheduler, persistence, retry logic and
HTTP surface are fully implemented here; the actual network call that publishes
a tweet lives behind the ``Poster`` protocol so an official-API implementation
or a browser-automation implementation can be dropped in without touching the
scheduler.

To enable a real backend, register it::

    from . import xpost
    xpost.register_poster("api", my_api_poster)

A poster is any callable ``(ScheduledPost, XAccountSettings) -> str`` that
publishes the post and returns the resulting post URL.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Protocol

from .models import (
    ScheduledPost,
    XAccountSettings,
)
from .store import Store

# How often the scheduler wakes to look for due posts.
SCHEDULER_TICK_SECONDS = 15
# How many times a failing post is retried before it is marked "failed".
MAX_POST_ATTEMPTS = 3


class Poster(Protocol):
    """Publishes a single scheduled post. Returns the published post URL."""

    def __call__(
        self, post: ScheduledPost, account: XAccountSettings
    ) -> str:  # pragma: no cover - protocol
        ...


_POSTERS: dict[str, Poster] = {}
_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()


def register_poster(method: str, poster: Poster) -> None:
    """Make a posting backend available under a method name ("api"/"browser")."""
    _POSTERS[method] = poster


def get_poster(method: str) -> Poster | None:
    return _POSTERS.get(method)


# --- persistence -----------------------------------------------------------

_SETTINGS_KEY = "X_ACCOUNT_SETTINGS"


def load_account_settings(store: Store) -> XAccountSettings:
    raw = store.get_setting(_SETTINGS_KEY)
    if not raw:
        return XAccountSettings()
    import json

    try:
        return XAccountSettings.model_validate(json.loads(raw))
    except Exception:
        return XAccountSettings()


def save_account_settings(store: Store, settings: XAccountSettings) -> None:
    store.save_setting(_SETTINGS_KEY, settings.model_dump_json())


def account_is_configured(settings: XAccountSettings) -> bool:
    if settings.method == "api":
        return all(
            [
                settings.api_key,
                settings.api_secret,
                settings.access_token,
                settings.access_secret,
            ]
        )
    # browser method is "configured" once a session exists; that is reported
    # by the browser poster itself, so optimistically treat it as configured.
    return True


def save_scheduled_post(store: Store, post: ScheduledPost) -> None:
    store.put(
        "scheduled_post",
        post.project_id,
        post.post_id,
        post,
        _scheduled_sort_key(post.scheduled_at),
    )


def list_scheduled_posts(
    store: Store, project_id: str = "*", status: str | None = None
) -> list[ScheduledPost]:
    posts = [
        ScheduledPost.model_validate(item)
        for item in store.list("scheduled_post", project_id)
    ]
    if status:
        posts = [p for p in posts if p.status == status]
    return sorted(posts, key=lambda p: p.scheduled_at)


def get_scheduled_post(store: Store, post_id: str) -> ScheduledPost | None:
    data = store.get("scheduled_post", post_id)
    return ScheduledPost.model_validate(data) if data else None


def delete_scheduled_post(store: Store, post_id: str) -> None:
    store.delete("scheduled_post", post_id)


def _scheduled_sort_key(scheduled_at: str) -> int:
    return int(_parse_dt(scheduled_at).timestamp())


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --- publishing ------------------------------------------------------------


def publish_due_posts(store: Store) -> int:
    """Publish every pending post whose time has come. Returns count posted."""
    now = datetime.now(timezone.utc)
    posted = 0
    for post in list_scheduled_posts(store, status="pending"):
        if _parse_dt(post.scheduled_at) > now:
            continue
        if _publish_one(store, post):
            posted += 1
    return posted


def _publish_one(store: Store, post: ScheduledPost) -> bool:
    post.status = "posting"
    post.attempts += 1
    post.error = None
    save_scheduled_post(store, post)

    account = load_account_settings(store)
    poster = get_poster(post.method)
    try:
        if poster is None:
            raise RuntimeError(
                f"No posting backend registered for method '{post.method}'. "
                "Register one with xpost.register_poster()."
            )
        result_url = poster(post, account)
        post.status = "posted"
        post.posted_at = datetime.now(timezone.utc).isoformat()
        post.result_url = result_url
        save_scheduled_post(store, post)
        return True
    except Exception as exc:  # noqa: BLE001 - surfaced to the user via status
        post.error = str(exc)
        if post.attempts >= MAX_POST_ATTEMPTS:
            post.status = "failed"
        else:
            # back off and retry on the next scheduler ticks
            post.status = "pending"
            post.scheduled_at = _backoff_time(post.attempts)
        save_scheduled_post(store, post)
        return False


def _backoff_time(attempts: int) -> str:
    delay = min(300, 30 * attempts)
    return datetime.fromtimestamp(
        time.time() + delay, tz=timezone.utc
    ).isoformat()


# --- background scheduler loop ---------------------------------------------


def _scheduler_loop(store: Store) -> None:
    while not _scheduler_stop.is_set():
        try:
            publish_due_posts(store)
        except Exception:  # noqa: BLE001 - never let the loop die
            pass
        _scheduler_stop.wait(SCHEDULER_TICK_SECONDS)


def start_scheduler(store: Store) -> None:
    """Start the background scheduler thread (idempotent)."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(store,),
        name="x-post-scheduler",
        daemon=True,
    )
    _scheduler_thread.start()


def stop_scheduler() -> None:
    global _scheduler_thread
    _scheduler_stop.set()
    if _scheduler_thread and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=2)
    _scheduler_thread = None
