from backend.app.clips import (
    parse_timestamp_clips,
    parse_timestamp_markers,
    selected_clip_ranges,
)
from backend.app.main import create_app
from backend.app.models import Project, Segment, TimestampClip, Word
from backend.app.services import prepare_segments_for_clips
from fastapi.testclient import TestClient


def test_timestamp_lines_become_adjacent_clips_ending_at_media_duration():
    clips = parse_timestamp_clips(
        """00:00 AI붐은 끝났는가
03:37 이럴때 항상 등장하는 워렌버핏 매매법
01:03:47 AI 시대에 우린 그럼 뭐 해먹고 살아야하나""",
        duration_ms=4_000_000,
    )

    assert [
        (clip.start_ms, clip.end_ms, clip.title, clip.selected)
        for clip in clips
    ] == [
        (0, 217_000, "AI붐은 끝났는가", True),
        (
            217_000,
            3_827_000,
            "이럴때 항상 등장하는 워렌버핏 매매법",
            True,
        ),
        (
            3_827_000,
            4_000_000,
            "AI 시대에 우린 그럼 뭐 해먹고 살아야하나",
            True,
        ),
    ]


def test_selected_clips_become_whisper_start_end_pairs():
    clips = [
        TimestampClip(
            clip_id="clip_001",
            start_ms=0,
            end_ms=217_000,
            title="Intro",
            selected=True,
        ),
        TimestampClip(
            clip_id="clip_002",
            start_ms=217_000,
            end_ms=300_000,
            title="Skip",
            selected=False,
        ),
        TimestampClip(
            clip_id="clip_003",
            start_ms=300_000,
            end_ms=400_000,
            title="Selected",
            selected=True,
        ),
    ]

    assert selected_clip_ranges(clips) == [0.0, 217.0, 300.0, 400.0]


def test_whisper_output_is_split_at_clip_boundaries():
    clips = [
        TimestampClip(
            clip_id="clip_001",
            start_ms=0,
            end_ms=10_000,
            title="First",
        ),
        TimestampClip(
            clip_id="clip_002",
            start_ms=10_000,
            end_ms=20_000,
            title="Second",
        ),
        TimestampClip(
            clip_id="clip_003",
            start_ms=20_000,
            end_ms=30_000,
            title="Third",
        ),
    ]
    combined = Segment(
        segment_id="seg_combined",
        start_ms=0,
        end_ms=30_000,
        raw_korean="first second third",
        words=[
            Word(text="first", start_ms=1_000, end_ms=2_000),
            Word(text="second", start_ms=11_000, end_ms=12_000),
            Word(text="third", start_ms=21_000, end_ms=22_000),
        ],
    )

    rows = prepare_segments_for_clips([combined], clips)

    assert [row.clip_id for row in rows] == [
        "clip_001",
        "clip_002",
        "clip_003",
    ]
    assert [row.raw_korean for row in rows] == ["first", "second", "third"]
    assert [(row.start_ms, row.end_ms) for row in rows] == [
        (1_000, 2_000),
        (11_000, 12_000),
        (21_000, 22_000),
    ]


def test_timestamp_lines_can_be_navigation_only_markers():
    markers = parse_timestamp_markers(
        "00:00 Intro\n03:37 Main topic",
        duration_ms=600_000,
    )

    assert [
        (marker.timestamp_ms, marker.title) for marker in markers
    ] == [(0, "Intro"), (217_000, "Main topic")]


def test_multiple_whisper_cues_in_one_clip_remain_separate():
    clip = TimestampClip(
        clip_id="clip_001",
        start_ms=0,
        end_ms=30_000,
        title="Topic",
    )
    segments = [
        Segment(
            segment_id="seg_a",
            start_ms=1_000,
            end_ms=4_000,
            raw_korean="first sentence",
        ),
        Segment(
            segment_id="seg_b",
            start_ms=5_000,
            end_ms=8_000,
            raw_korean="second sentence",
        ),
        Segment(
            segment_id="seg_c",
            start_ms=9_000,
            end_ms=12_000,
            raw_korean="third sentence",
        ),
    ]

    rows = prepare_segments_for_clips(segments, [clip])

    assert len(rows) == 3
    assert [row.raw_korean for row in rows] == [
        "first sentence",
        "second sentence",
        "third sentence",
    ]
    assert [row.clip_id for row in rows] == ["clip_001"] * 3


def test_collapsed_clip_is_reconstructed_from_word_timestamps():
    clip = TimestampClip(
        clip_id="clip_001",
        start_ms=0,
        end_ms=30_000,
        title="Topic",
    )
    words = [
        Word(
            text=f"word{index}",
            start_ms=index * 2_000,
            end_ms=index * 2_000 + 900,
        )
        for index in range(1, 13)
    ]
    collapsed = Segment(
        segment_id="seg_collapsed",
        start_ms=0,
        end_ms=30_000,
        clip_id=clip.clip_id,
        raw_korean=" ".join(word.text for word in words),
        words=words,
    )

    rows = prepare_segments_for_clips([collapsed], [clip])

    assert len(rows) > 1
    assert all(row.clip_id == clip.clip_id for row in rows)
    assert [word.text for row in rows for word in row.words] == [
        word.text for word in words
    ]
    assert len({row.start_ms for row in rows}) == len(rows)


def test_opening_a_clip_keeps_existing_transcripts(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Clip workspace"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={"duration_ms": 20_000, "status": "transcribed"}
    )
    app.state.store.save_project(project)
    clips = [
        TimestampClip(
            clip_id=f"clip_{index:03d}",
            start_ms=(index - 1) * 10_000,
            end_ms=index * 10_000,
            title=f"Clip {index}",
        )
        for index in (1, 2)
    ]
    for clip in clips:
        app.state.store.save_clip(project.project_id, clip)
        app.state.store.save_segment(
            project.project_id,
            Segment(
                segment_id=f"seg_{clip.clip_id}",
                start_ms=clip.start_ms,
                end_ms=clip.start_ms + 1_000,
                clip_id=clip.clip_id,
                raw_korean=clip.title,
            ),
        )

    opened = client.patch(
        f"/api/projects/{project.project_id}/clips/clip_002",
        json={"opened": True},
    )

    assert opened.status_code == 200
    assert opened.json()["opened"] is True
    assert len(client.get(
        f"/api/projects/{project.project_id}/segments"
    ).json()) == 2

    closed = client.patch(
        f"/api/projects/{project.project_id}/clips/clip_002",
        json={"opened": False},
    )

    assert closed.status_code == 200
    assert closed.json()["opened"] is False
    assert len(client.get(
        f"/api/projects/{project.project_id}/segments"
    ).json()) == 2


def test_clip_styles_stay_independent_until_apply_to_all(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Styled clips"}
    ).json()
    project = Project.model_validate(project_data)
    for index in (1, 2):
        app.state.store.save_clip(
            project.project_id,
            TimestampClip(
                clip_id=f"clip_{index:03d}",
                start_ms=(index - 1) * 10_000,
                end_ms=index * 10_000,
                title=f"Clip {index}",
                subtitle_style=project.subtitle_style,
            ),
        )

    changed = client.patch(
        f"/api/projects/{project.project_id}/clips/clip_001/subtitle-style",
        json={"font_size": 72, "max_words_per_line": 16},
    )
    before_apply = client.get(
        f"/api/projects/{project.project_id}/clips"
    ).json()
    applied = client.post(
        f"/api/projects/{project.project_id}/clips/clip_001/"
        "subtitle-style/apply-all"
    )

    assert changed.status_code == 200
    assert before_apply[0]["subtitle_style"]["font_size"] == 72
    assert before_apply[1]["subtitle_style"]["font_size"] == 48
    assert applied.status_code == 200
    assert {
        item["subtitle_style"]["max_words_per_line"]
        for item in applied.json()
    } == {16}
