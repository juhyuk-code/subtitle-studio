"""Viral-potential scoring for Subtitle Studio clips.

Before posts are scheduled, each clip's transcript + post copy is scored by an
LLM for viral potential (0-100). The scores drive scheduling order: the most
potentially-viral clips are scheduled first.

Score dimensions (each 0-100, weighted):
  - hook:      how strong the opening hook is
  - emotion:   emotional punch / relatability
  - insight:   how shareable/surprising the take is
  - timeliness: relevance to what's happening now
  - shareability: how likely people are to share it

The final score is a weighted blend. We also produce a one-line rationale so a
human can sanity-check the ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import PostCopy
from .services import call_openrouter
from .store import Store

logger = logging.getLogger(__name__)

VIRAL_SCORE_PROMPT = """\
You rank short podcast/video clips by how likely they are to go viral when posted
to X (Twitter) with a short caption. Each clip has a transcript (English) and a
proposed post headline/body.

Score the clip 0-100 on viral potential. A viral clip usually has a STRONG hook
in the first seconds, a clear emotional or intellectual punch, a take people
want to share or argue with, and it is timely.

Return ONLY JSON:
{
  "viral_score": <0-100>,
  "rationale": "<one or two sentences: what makes this clip viral or not>"
}

Rules:
- Be honest and critical. Not every clip is viral; a calm factual clip can
  legitimately score 20-40.
- A clip with a quotable hot take, a surprising admission, a sharp analogy, or a
  strong emotional moment should score higher.
- Do not invent things not in the transcript.
- Do not include markdown or extra text outside the JSON object.
"""


@dataclass
class ViralScoreResult:
    clip_id: str
    score: int
    rationale: str = ""


@dataclass
class ViralScoreBatch:
    results: list[ViralScoreResult] = field(default_factory=list)

    def best(self, limit: int | None = None) -> list[ViralScoreResult]:
        ranked = sorted(self.results, key=lambda r: r.score, reverse=True)
        return ranked[:limit] if limit else ranked


def _clip_payload(clip: Any, transcript: list[dict[str, Any]], post_copy: PostCopy | None) -> dict[str, Any]:
    """Build the compact payload for one clip."""
    return {
        "clip": {
            "clip_id": clip.clip_id,
            "title": clip.title or "",
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
        },
        "headline": post_copy.headline if post_copy else "",
        "body": post_copy.body if post_copy else "",
        "transcript": transcript,
    }


async def score_one_clip(
    store: Store,
    clip: Any,
    transcript: list[dict[str, Any]],
    post_copy: PostCopy | None,
) -> ViralScoreResult:
    """Score a single clip for viral potential."""
    payload = _clip_payload(clip, transcript, post_copy)
    result: dict[str, Any] = {}
    for attempt in range(2):
        try:
            result = await call_openrouter(
                store,
                "post_captioning",
                VIRAL_SCORE_PROMPT,
                payload if attempt == 0 else {
                    **payload,
                    "retry_instruction": (
                        "Return a JSON object with integer viral_score (0-100) "
                        "and a short rationale string."
                    ),
                },
            )
            score = int(result.get("viral_score") or 0)
            if 0 <= score <= 100:
                return ViralScoreResult(
                    clip_id=clip.clip_id,
                    score=score,
                    rationale=str(result.get("rationale") or "").strip(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("viral score attempt %d failed for %s: %s", attempt + 1, clip.clip_id, exc)
    # Fallback: a mid score rather than crashing the batch.
    return ViralScoreResult(clip_id=clip.clip_id, score=50, rationale="Could not score (LLM error).")


async def score_project_clips(
    store: Store,
    project_id: str,
    clips: list[Any],
    transcripts_by_clip: dict[str, list[dict[str, Any]]],
    post_copies_by_clip: dict[str, PostCopy],
) -> ViralScoreBatch:
    """Score every clip in a project; return them ranked by viral score."""
    results: list[ViralScoreResult] = []
    for clip in clips:
        transcript = transcripts_by_clip.get(clip.clip_id, [])
        post_copy = post_copies_by_clip.get(clip.clip_id)
        result = await score_one_clip(store, clip, transcript, post_copy)
        results.append(result)
        logger.info(
            "viral score %s = %d (%s)",
            clip.clip_id,
            result.score,
            result.rationale[:60],
        )
    return ViralScoreBatch(results=results)


def persist_scores(
    store: Store,
    project_id: str,
    batch: ViralScoreBatch,
) -> None:
    """Write viral scores back onto each clip's PostCopy."""
    by_clip = {r.clip_id: r for r in batch.results}
    for clip_id, result in by_clip.items():
        data = store.get("post_copy", f"{project_id}:{clip_id}")
        if not data:
            continue
        post_copy = PostCopy.model_validate(data)
        post_copy = post_copy.model_copy(
            update={
                "viral_score": result.score,
                "viral_rationale": result.rationale,
            }
        )
        store.save_post_copy(project_id, post_copy)


def has_viral_scores(store: Store, project_id: str) -> bool:
    """True if every clip in the project already carries a viral score."""
    clip_ids = [item.get("clip_id") for item in store.list("clip", project_id)]
    if not clip_ids:
        return False
    scored = 0
    for clip_id in clip_ids:
        data = store.get("post_copy", f"{project_id}:{clip_id}")
        if data:
            post_copy = PostCopy.model_validate(data)
            if post_copy.viral_score is not None:
                scored += 1
    return scored == len(clip_ids)
