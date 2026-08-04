from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import Project, ProjectCreate, Segment, SubtitleStyle
from backend.app.store import Store


def test_subtitle_style_defaults_and_updates_persist(tmp_path):
    client = TestClient(create_app(tmp_path))
    project = client.post(
        "/api/projects", json={"name": "Styled episode"}
    ).json()

    assert project["subtitle_style"]["font_family"] == "Pretendard"
    assert project["subtitle_style"]["background_enabled"] is True
    default_ass = client.get(
        f"/api/projects/{project['project_id']}/export/ass?language=en"
    )
    assert "Style: Default,Pretendard,48" in default_ass.text

    response = client.patch(
        f"/api/projects/{project['project_id']}/subtitle-style",
        json={
            "font_family": "Malgun Gothic",
            "font_size": 62,
            "line_spacing": 1.4,
            "background_color": "#123456",
            "background_opacity": 0.65,
            "position": "top",
        },
    )

    assert response.status_code == 200
    assert response.json()["font_family"] == "Malgun Gothic"
    assert response.json()["font_size"] == 62
    assert response.json()["background_opacity"] == 0.65
    saved = client.get(f"/api/projects/{project['project_id']}").json()
    assert saved["subtitle_style"] == response.json()


def test_existing_arial_styles_migrate_to_pretendard_once(tmp_path):
    store = Store(tmp_path)
    untouched = Project.create(
        ProjectCreate(
            name="Old default",
            subtitle_style=SubtitleStyle(font_family="Arial"),
        )
    )
    customized = Project.create(
        ProjectCreate(
            name="Selected Arial",
            subtitle_style=SubtitleStyle(
                font_family="Arial",
                font_size=62,
            ),
        )
    )
    store.save_project(untouched)
    store.save_project(customized)
    partial_legacy = store.get("project", untouched.project_id)
    partial_legacy["subtitle_style"] = {"font_family": "Arial"}
    store.put(
        "project",
        untouched.project_id,
        untouched.project_id,
        partial_legacy,
    )

    client = TestClient(create_app(tmp_path))

    migrated = client.get(
        f"/api/projects/{untouched.project_id}"
    ).json()
    preserved = client.get(
        f"/api/projects/{customized.project_id}"
    ).json()
    assert migrated["subtitle_style"]["font_family"] == "Pretendard"
    assert preserved["subtitle_style"]["font_family"] == "Pretendard"
    client.patch(
        f"/api/projects/{customized.project_id}/subtitle-style",
        json={"font_family": "Arial"},
    )

    restarted_client = TestClient(create_app(tmp_path))
    selected_arial = restarted_client.get(
        f"/api/projects/{customized.project_id}"
    ).json()
    assert selected_arial["subtitle_style"]["font_family"] == "Arial"


def test_ass_export_contains_saved_subtitle_style(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Styled export"}
    ).json()
    project_id = project["project_id"]
    client.patch(
        f"/api/projects/{project_id}/subtitle-style",
        json={
            "font_family": "Malgun Gothic",
            "font_size": 60,
            "font_weight": "bold",
            "letter_spacing": 2.5,
            "text_color": "#AABBCC",
            "background_color": "#112233",
            "background_opacity": 0.5,
            "position": "bottom",
            "alignment": "center",
        },
    )
    app.state.store.save_segment(
        project_id,
        Segment(
            segment_id="seg_001",
            start_ms=1_250,
            end_ms=3_500,
            raw_korean="안녕하세요",
            english="Hello there",
        ),
    )

    response = client.get(
        f"/api/projects/{project_id}/export/ass?language=en"
    )

    assert response.status_code == 200
    assert "[V4+ Styles]" in response.text
    assert "Malgun Gothic,60" in response.text
    assert "&H80332211,&H80332211" in response.text
    assert ",2.5,0,3,1," in response.text
    assert "Dialogue: 0,0:00:01.25,0:00:03.50" in response.text
    assert response.text.endswith(
        r"{\xbord20\ybord10}Hello there" + "\n"
    )


def test_ass_background_renders_with_zero_vertical_padding(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Zero vertical padding"}
    ).json()
    project_id = project["project_id"]
    client.patch(
        f"/api/projects/{project_id}/subtitle-style",
        json={
            "background_enabled": True,
            "background_color": "#112233",
            "background_opacity": 1,
            "background_padding_x": 14,
            "background_padding_y": 0,
        },
    )
    app.state.store.save_segment(
        project_id,
        Segment(
            segment_id="seg_background",
            start_ms=0,
            end_ms=1_000,
            raw_korean="배경",
            english="Visible background",
        ),
    )

    ass = client.get(
        f"/api/projects/{project_id}/export/ass?language=en"
    ).text

    assert "&H00332211,&H00332211" in ass
    assert ",3,1,0,2," in ass
    assert r"{\xbord14\ybord0}Visible background" in ass


def test_subtitle_style_rejects_invalid_color(tmp_path):
    client = TestClient(create_app(tmp_path))
    project = client.post(
        "/api/projects", json={"name": "Validation"}
    ).json()

    response = client.patch(
        f"/api/projects/{project['project_id']}/subtitle-style",
        json={"text_color": "white"},
    )

    assert response.status_code == 422


def test_words_per_line_allows_up_to_forty(tmp_path):
    client = TestClient(create_app(tmp_path))
    project = client.post(
        "/api/projects", json={"name": "Long caption lines"}
    ).json()
    endpoint = (
        f"/api/projects/{project['project_id']}/subtitle-style"
    )

    accepted = client.patch(
        endpoint,
        json={"max_words_per_line": 40},
    )
    rejected = client.patch(
        endpoint,
        json={"max_words_per_line": 41},
    )

    assert accepted.status_code == 200
    assert accepted.json()["max_words_per_line"] == 40
    assert rejected.status_code == 422


def test_word_limit_wraps_srt_and_ass_exports(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Line wrapping"}
    ).json()
    project_id = project["project_id"]
    client.patch(
        f"/api/projects/{project_id}/subtitle-style",
        json={"max_words_per_line": 3, "max_lines": 3},
    )
    app.state.store.save_segment(
        project_id,
        Segment(
            segment_id="seg_001",
            start_ms=0,
            end_ms=2_000,
            raw_korean="하나 둘 셋 넷 다섯 여섯",
            english="one two three four five six seven",
        ),
    )

    srt = client.get(
        f"/api/projects/{project_id}/export/srt?language=en"
    ).text
    ass = client.get(
        f"/api/projects/{project_id}/export/ass?language=en"
    ).text

    assert "one two three\nfour five\nsix seven" in srt
    assert r"one two three\Nfour five\Nsix seven" in ass


def test_caption_line_limit_pages_long_subtitles_without_losing_words(
    tmp_path,
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "One line captions"}
    ).json()
    project_id = project["project_id"]
    assert project["subtitle_style"]["max_lines"] == 1
    client.patch(
        f"/api/projects/{project_id}/subtitle-style",
        json={"max_words_per_line": 3, "max_lines": 1},
    )
    app.state.store.save_segment(
        project_id,
        Segment(
            segment_id="seg_001",
            start_ms=0,
            end_ms=3_000,
            raw_korean="하나 둘 셋 넷 다섯 여섯 일곱",
            english="one two three four five six seven",
        ),
    )

    srt = client.get(
        f"/api/projects/{project_id}/export/srt?language=en"
    ).text
    ass = client.get(
        f"/api/projects/{project_id}/export/ass?language=en"
    ).text

    assert "00:00:00,000 --> 00:00:01,000\none two three" in srt
    assert "00:00:01,000 --> 00:00:02,000\nfour five six" in srt
    assert "00:00:02,000 --> 00:00:03,000\nseven" in srt
    assert ass.count("Dialogue: 0,") == 3
    assert r"\N" not in "\n".join(
        line for line in ass.splitlines() if line.startswith("Dialogue:")
    )
