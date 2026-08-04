import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import services
from backend.app.main import create_app
from backend.app.models import (
    PostCopy,
    Project,
    ProjectCreate,
    Segment,
    TimestampClip,
)
from backend.app.services import (
    POST_COPY_PROMPT,
    format_post_copy_quote_blocks,
    generate_post_copy,
    post_copy_source_signature,
)
from backend.app.store import Store


def _post_copy_project(store: Store) -> tuple[Project, TimestampClip]:
    project = Project.create(ProjectCreate(name="Post copy episode"))
    clip = TimestampClip(
        clip_id="clip_target",
        start_ms=10_000,
        end_ms=30_000,
        title="The creator coin argument",
    )
    store.save_project(project)
    store.save_clip(project.project_id, clip)
    store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_target",
            clip_id=clip.clip_id,
            start_ms=11_000,
            end_ms=14_000,
            raw_korean="target Korean",
            english="Creator coins let fans speculate on a public value.",
        ),
    )
    store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_other",
            clip_id="clip_other",
            start_ms=12_000,
            end_ms=13_000,
            raw_korean="other Korean",
            english="This overlapping clip must not leak into the post.",
        ),
    )
    return project, clip


def test_post_copy_generation_uses_only_the_selected_clip(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project, clip = _post_copy_project(store)
    calls = []

    async def fake_openrouter(_store, stage, _prompt, payload):
        calls.append((stage, payload))
        return {
            "headline": "Creator coins give fans a public market",
            "body": '"Fans can speculate on a public value."',
        }

    monkeypatch.setattr(services, "call_openrouter", fake_openrouter)

    result = asyncio.run(
        generate_post_copy(store, project.project_id, clip.clip_id)
    )

    assert result.headline == "Creator coins give fans a public market"
    assert calls[0][0] == "post_captioning"
    transcript = calls[0][1]["transcript"]
    assert [item["segment_id"] for item in transcript] == ["seg_target"]
    assert "must not leak" not in str(transcript)
    assert store.get(
        "post_copy", f"{project.project_id}:{clip.clip_id}"
    )["body"] == result.body


def test_post_copy_retries_an_incomplete_model_response(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project, clip = _post_copy_project(store)
    calls = []
    responses = [
        {"headline": "A hook without its supporting quote"},
        {
            "headline": "Creator coins give fans a public market",
            "body": '"Fans can speculate on a public value."',
        },
    ]

    async def fake_openrouter(_store, stage, _prompt, payload):
        calls.append((stage, payload))
        return responses.pop(0)

    monkeypatch.setattr(services, "call_openrouter", fake_openrouter)

    result = asyncio.run(
        generate_post_copy(store, project.project_id, clip.clip_id)
    )

    assert result.body == '"Fans can speculate on a public value."'
    assert len(calls) == 2
    assert "retry_instruction" in calls[1][1]


def test_post_copy_prompt_prefers_complete_context_over_a_word_limit():
    assert "single moment" in POST_COPY_PROMPT
    assert "every essential detail" in POST_COPY_PROMPT
    assert "without watching the clip" in POST_COPY_PROMPT
    assert "Do not shorten away essential information" in POST_COPY_PROMPT
    assert "arbitrary word count" in POST_COPY_PROMPT
    assert "separated by a blank line" in POST_COPY_PROMPT
    assert "one long quote paragraph" in POST_COPY_PROMPT


def test_long_quote_is_split_into_sentence_by_sentence_blocks():
    body = (
        'Alex: "Korea is having a global cultural moment. '
        "NVIDIA is asking Korea for DRAM. "
        "At the same time, young people are losing leveraged savings. "
        'Is there another country this extreme?"'
    )

    formatted = format_post_copy_quote_blocks(body)

    assert formatted.split("\n\n") == [
        'Alex: "Korea is having a global cultural moment."',
        '"NVIDIA is asking Korea for DRAM."',
        '"At the same time, young people are losing leveraged savings."',
        '"Is there another country this extreme?"',
    ]


def test_post_copy_requires_an_english_transcript(tmp_path):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Untranslated episode"))
    clip = TimestampClip(
        clip_id="clip_untranslated",
        start_ms=0,
        end_ms=10_000,
        title="Not translated",
    )
    store.save_project(project)
    store.save_clip(project.project_id, clip)

    with pytest.raises(RuntimeError, match="Translate this clip"):
        asyncio.run(
            generate_post_copy(store, project.project_id, clip.clip_id)
        )


def test_post_copy_endpoint_marks_changed_transcripts_stale(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project, clip = _post_copy_project(app.state.store)
    signature = post_copy_source_signature(
        app.state.store, project.project_id, clip.clip_id
    )
    app.state.store.save_post_copy(
        project.project_id,
        PostCopy(
            clip_id=clip.clip_id,
            headline="Original headline",
            body='"Original quote."',
            generated_at="2026-08-02T00:00:00+00:00",
            source_signature=signature,
        ),
    )

    current = client.get(
        f"/api/projects/{project.project_id}/post-copies"
    )
    assert current.status_code == 200
    assert current.json()[0]["stale"] is False

    segment = Segment.model_validate(
        app.state.store.get("segment", "seg_target")
    ).model_copy(update={"english": "The translated quote changed."})
    app.state.store.save_segment(project.project_id, segment)

    changed = client.get(
        f"/api/projects/{project.project_id}/post-copies"
    )
    assert changed.status_code == 200
    assert changed.json()[0]["stale"] is True

    edited = client.patch(
        f"/api/projects/{project.project_id}/post-copies/{clip.clip_id}",
        json={"headline": "Manually edited headline"},
    )
    assert edited.status_code == 200
    assert edited.json()["headline"] == "Manually edited headline"
    assert edited.json()["stale"] is True
