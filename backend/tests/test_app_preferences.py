from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_app_preferences_persist_across_restarts(tmp_path):
    data_root = tmp_path / "data"
    client = TestClient(create_app(data_root))

    defaults = client.get("/api/settings/app-preferences")
    assert defaults.status_code == 200
    assert defaults.json() == {
        "app_font_scale": 1.0,
        "sidebar_width": 245,
        "last_project_id": None,
        "connection_dismissed": False,
    }

    updated = client.patch(
        "/api/settings/app-preferences",
        json={
            "app_font_scale": 1.35,
            "sidebar_width": 880,
            "last_project_id": "prj_example",
            "connection_dismissed": True,
        },
    )
    assert updated.status_code == 200

    restarted = TestClient(create_app(data_root))
    assert restarted.get("/api/settings/app-preferences").json() == {
        "app_font_scale": 1.35,
        "sidebar_width": 880,
        "last_project_id": "prj_example",
        "connection_dismissed": True,
    }


def test_app_preferences_patch_keeps_untouched_values_and_can_clear_project(
    tmp_path,
):
    client = TestClient(create_app(tmp_path))
    client.patch(
        "/api/settings/app-preferences",
        json={"app_font_scale": 1.5, "last_project_id": "prj_example"},
    )

    cleared = client.patch(
        "/api/settings/app-preferences",
        json={"last_project_id": None},
    )

    assert cleared.status_code == 200
    assert cleared.json()["app_font_scale"] == 1.5
    assert cleared.json()["last_project_id"] is None


def test_app_preferences_reject_invalid_font_scale(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.patch(
        "/api/settings/app-preferences",
        json={"app_font_scale": 2.5},
    )

    assert response.status_code == 422
