"""Regression test: new agent/X routes must not be shadowed by the static mount.

The desktop app mounts StaticFiles at "/" to serve the built frontend. That
mount is a catch-all, so any route registered AFTER it silently 404s (the
mount matches first and fails to find a file). This test pins the ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture()
def client_with_static_root(tmp_path, monkeypatch):
    # Simulate the packaged desktop app: a real static root so the "/" mount is
    # added, plus an index.html for the mount to serve.
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    app = create_app(data_root=tmp_path / "data", static_root=static)
    return TestClient(app)


def test_new_routes_not_shadowed_by_static_mount(client_with_static_root):
    client = client_with_static_root
    # /api/settings/x (GET) must hit the real handler, not the mount's 404.
    response = client.get("/api/settings/x")
    assert response.status_code == 200
    assert "method" in response.json()

    # /api/scheduled-posts (GET) must return a list, not 404.
    response = client.get("/api/scheduled-posts")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # The static mount still serves the frontend at the root.
    index = client.get("/")
    assert index.status_code == 200
    assert "hi" in index.text


def test_static_mount_is_registered_last(client_with_static_root):
    from starlette.routing import Mount

    app = client_with_static_root.app
    assert isinstance(app.routes[-1], Mount)
