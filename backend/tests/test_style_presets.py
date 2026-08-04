from fastapi.testclient import TestClient

from backend.app.main import create_app


def preset_payload(name="Podcast captions", font_size=48):
    return {
        "name": name,
        "style": {
            "font_family": "Pretendard",
            "font_size": font_size,
            "font_weight": "normal",
            "max_words_per_line": 9,
            "max_lines": 2,
            "max_width_percent": 96,
            "margin_vertical": 74,
            "background_color": "#000000",
            "background_opacity": 1,
            "background_padding_x": 14,
            "background_padding_y": 0,
        },
    }


def test_style_presets_persist_across_app_restarts(tmp_path):
    data_root = tmp_path / "data"
    client = TestClient(create_app(data_root))

    created = client.post(
        "/api/style-presets", json=preset_payload()
    )

    assert created.status_code == 201
    preset = created.json()
    assert preset["name"] == "Podcast captions"
    assert preset["style"]["font_family"] == "Pretendard"
    assert preset["style"]["background_enabled"] is True

    restarted_client = TestClient(create_app(data_root))
    assert restarted_client.get("/api/style-presets").json() == [preset]


def test_style_presets_can_be_updated_and_deleted(tmp_path):
    client = TestClient(create_app(tmp_path))
    preset = client.post(
        "/api/style-presets", json=preset_payload()
    ).json()

    updated = client.put(
        f"/api/style-presets/{preset['preset_id']}",
        json=preset_payload(name="Vertical clips", font_size=64),
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Vertical clips"
    assert updated.json()["style"]["font_size"] == 64

    deleted = client.delete(
        f"/api/style-presets/{preset['preset_id']}"
    )
    assert deleted.status_code == 204
    assert client.get("/api/style-presets").json() == []


def test_style_preset_names_are_unique(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.post(
        "/api/style-presets", json=preset_payload()
    ).status_code == 201

    duplicate = client.post(
        "/api/style-presets",
        json=preset_payload(name="PODCAST CAPTIONS"),
    )

    assert duplicate.status_code == 409
