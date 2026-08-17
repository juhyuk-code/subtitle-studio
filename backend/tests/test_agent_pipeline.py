"""Tests for the agent clip-everything orchestration endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import NavigationMarker, Project, ProjectCreate


@pytest.fixture()
def client(tmp_path):
    app = create_app(data_root=tmp_path / "data")
    return TestClient(app)


def test_clip_everything_404_for_missing_project(client):
    response = client.post("/api/agent/projects/prj_missing/clip-everything")
    assert response.status_code == 404


def test_derive_clips_from_markers(tmp_path):
    from backend.app import agent_pipeline
    from backend.app.store import Store

    store = Store(tmp_path / "data")
    project = Project.create(
        ProjectCreate(name="Demo")
    ).model_copy(update={"duration_ms": 30_000})
    store.save_project(project)

    # No markers -> no clips (value error)
    with pytest.raises(ValueError, match="no timestamps or clips"):
        agent_pipeline._derive_clips_from_markers(store, project)

    for marker in [
        NavigationMarker(marker_id="m1", timestamp_ms=0, title="Intro"),
        NavigationMarker(marker_id="m2", timestamp_ms=10_000, title="Middle"),
        NavigationMarker(marker_id="m3", timestamp_ms=20_000, title="End"),
    ]:
        store.save_marker(project.project_id, marker)

    clips = agent_pipeline._derive_clips_from_markers(store, project)
    assert len(clips) == 3
    assert clips[0].end_ms == 10_000
    assert clips[1].end_ms == 20_000
    assert clips[2].end_ms == 30_000
