import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import services
from backend.app.main import create_app
from backend.app.models import Project, ProjectCreate, Segment
from backend.app.services import (
    SHORTFORM_IDEAS_PROMPT,
    _validate_shortform_ideas,
    generate_shortform_ideas,
    shortform_transcript_payload,
)
from backend.app.store import Store


def _seed_transcript(store: Store, project: Project) -> None:
    """Eight adjacent 5-second segments: seg_000001 .. seg_000008."""
    for index in range(8):
        store.save_segment(
            project.project_id,
            Segment(
                segment_id=f"seg_{index + 1:06d}",
                start_ms=index * 5_000,
                end_ms=(index + 1) * 5_000,
                raw_korean=f"korean line {index}",
                pass_1_korean=f"korean line {index}",
                english=f"english line {index}",
            ),
        )


def _make_project(store: Store) -> Project:
    project = Project.create(ProjectCreate(name="Shortform episode"))
    store.save_project(project)
    return project


def test_transcript_payload_is_compact_and_signed(tmp_path):
    store = Store(tmp_path)
    project = _make_project(store)
    _seed_transcript(store, project)
    compact, signature = shortform_transcript_payload(
        store, project.project_id
    )
    assert len(compact) == 8
    assert compact[0] == {
        "id": "seg_000001",
        "s": 0,
        "e": 5_000,
        "ko": "korean line 0",
        "en": "english line 0",
    }
    assert compact[-1]["e"] == 40_000
    assert len(signature) == 64


def test_validation_resolves_timestamps_and_orders_parts(tmp_path):
    store = Store(tmp_path)
    project = _make_project(store)
    _seed_transcript(store, project)
    compact, _ = shortform_transcript_payload(store, project.project_id)
    result = {
        "ideas": [
            {
                "title": "Spliced hot take",
                "hook": "english line 5",
                "rationale": "Same context, minutes apart",
                "parts": [
                    {"segment_ids": ["seg_000006"], "note": "hook"},
                    {"segment_ids": ["seg_000002", "seg_000003"], "note": "payoff"},
                ],
            }
        ]
    }
    ideas = _validate_shortform_ideas(result, compact, 40_000, 10)
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.parts[0].start_ms == 25_000
    assert idea.parts[0].end_ms == 30_000
    assert idea.parts[1].start_ms == 5_000
    assert idea.parts[1].end_ms == 15_000
    assert idea.total_duration_ms == 15_000


def test_validation_rejects_invented_and_nonadjacent_segments(tmp_path):
    store = Store(tmp_path)
    project = _make_project(store)
    _seed_transcript(store, project)
    compact, _ = shortform_transcript_payload(store, project.project_id)
    invented = {
        "ideas": [
            {
                "title": "Ghost",
                "parts": [{"segment_ids": ["seg_999999"]}],
            }
        ]
    }
    assert _validate_shortform_ideas(invented, compact, 40_000, 10) == []
    nonadjacent = {
        "ideas": [
            {
                "title": "Skips a segment",
                "parts": [{"segment_ids": ["seg_000001", "seg_000003"]}],
            }
        ]
    }
    assert _validate_shortform_ideas(nonadjacent, compact, 40_000, 10) == []


def test_validation_rejects_overlap_and_bad_duration(tmp_path):
    store = Store(tmp_path)
    project = _make_project(store)
    _seed_transcript(store, project)
    compact, _ = shortform_transcript_payload(store, project.project_id)
    duplicate = {
        "ideas": [
            {
                "title": "First",
                "parts": [{"segment_ids": ["seg_000001", "seg_000002"]}],
            },
            {
                "title": "Reuses",
                "parts": [{"segment_ids": ["seg_000002", "seg_000003"]}],
            },
        ]
    }
    ideas = _validate_shortform_ideas(duplicate, compact, 40_000, 10)
    assert [idea.title for idea in ideas] == ["First"]
    too_short = {
        "ideas": [
            {
                "title": "Tiny",
                "parts": [{"segment_ids": ["seg_000001"]}],
            }
        ]
    }
    assert _validate_shortform_ideas(too_short, compact, 40_000, 10) == []


def test_generate_shortform_ideas_persists_results(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = _make_project(store)
    _seed_transcript(store, project)

    async def fake_openrouter(_store, stage, _prompt, payload):
        assert stage == "shortform_ideas"
        assert len(payload["segments"]) == 8
        return {
            "ideas": [
                {
                    "title": "Mined idea",
                    "hook": "english line 3",
                    "rationale": "strong",
                    "parts": [
                        {"segment_ids": ["seg_000004", "seg_000005"]},
                        {"segment_ids": ["seg_000007"]},
                    ],
                }
            ]
        }

    monkeypatch.setattr(services, "call_openrouter", fake_openrouter)
    ideas = asyncio.run(
        generate_shortform_ideas(store, project.project_id)
    )
    assert len(ideas) == 1
    saved = store.list("shortform_idea", project.project_id)
    assert len(saved) == 1
    assert saved[0]["title"] == "Mined idea"


def test_generate_shortform_ideas_requires_transcript(tmp_path):
    store = Store(tmp_path)
    project = _make_project(store)
    with pytest.raises(RuntimeError, match="Transcribe"):
        asyncio.run(generate_shortform_ideas(store, project.project_id))


def test_shortform_prompt_encodes_the_splice_rules():
    assert "shared context" in SHORTFORM_IDEAS_PROMPT.lower()
    assert "not need to be adjacent" in SHORTFORM_IDEAS_PROMPT.lower()
    assert "segment_ids" in SHORTFORM_IDEAS_PROMPT
    assert "em dash" in SHORTFORM_IDEAS_PROMPT.lower()


def test_endpoints_generate_list_delete_and_materialize(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = _make_project(app.state.store)
    _seed_transcript(app.state.store, project)

    async def fake_openrouter(_store, stage, _prompt, payload):
        return {
            "ideas": [
                {
                    "title": "Endpoint idea",
                    "hook": "hook",
                    "rationale": "why",
                    "parts": [
                        {"segment_ids": ["seg_000004", "seg_000005"]},
                        {"segment_ids": ["seg_000007"]},
                    ],
                }
            ]
        }

    monkeypatch.setattr(services, "call_openrouter", fake_openrouter)

    created = client.post(
        f"/api/projects/{project.project_id}/shortform-ideas/generate"
    )
    assert created.status_code == 200
    ideas = created.json()
    assert len(ideas) == 1
    idea_id = ideas[0]["idea_id"]
    assert ideas[0]["stale"] is False

    listed = client.get(
        f"/api/projects/{project.project_id}/shortform-ideas"
    )
    assert listed.status_code == 200
    assert listed.json()[0]["idea_id"] == idea_id

    clips = client.post(
        f"/api/projects/{project.project_id}/shortform-ideas/{idea_id}/clips"
    )
    assert clips.status_code == 201
    parts = clips.json()
    assert len(parts) == 2
    assert parts[0]["start_ms"] == 15_000
    assert parts[0]["end_ms"] == 25_000
    assert parts[1]["start_ms"] == 30_000

    deleted = client.delete(
        f"/api/projects/{project.project_id}/shortform-ideas/{idea_id}"
    )
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/projects/{project.project_id}/shortform-ideas").json()
        == []
    )
