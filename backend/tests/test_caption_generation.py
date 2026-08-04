from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import Segment
from backend.app.services import generate_caption_track


def segment(
    segment_id: str,
    start_ms: int,
    end_ms: int,
    english: str,
    speaker_id: str = "SPEAKER_01",
) -> dict:
    return Segment(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_id=speaker_id,
        raw_korean="원문",
        english=english,
    ).model_dump()


def test_generation_merges_transcript_segments_to_fill_caption_capacity():
    track = generate_caption_track(
        [
            segment("seg_1", 0, 1_000, "one two three"),
            segment("seg_2", 1_000, 2_000, "four five six"),
        ],
        "en",
        max_words_per_line=4,
        max_lines=1,
    )

    assert [cue.lines for cue in track.cues] == [
        ["one two three four"],
        ["five six"],
    ]
    assert track.cues[0].source_segment_ids == ["seg_1", "seg_2"]
    assert track.cues[0].end_ms > 1_000
    assert track.cues[1].start_ms == track.cues[0].end_ms


def test_generation_uses_lines_per_caption_as_part_of_capacity():
    track = generate_caption_track(
        [segment("seg_1", 0, 3_000, "one two three four five six")],
        "en",
        max_words_per_line=3,
        max_lines=2,
    )

    assert len(track.cues) == 1
    assert track.cues[0].lines == [
        "one two three",
        "four five six",
    ]
    assert track.cues[0].start_ms == 0
    assert track.cues[0].end_ms == 3_000


def test_generation_balances_short_captions_across_selected_lines():
    track = generate_caption_track(
        [segment("seg_1", 0, 3_000, "one two three four five six")],
        "en",
        max_words_per_line=8,
        max_lines=2,
    )

    assert len(track.cues) == 1
    assert track.cues[0].lines == [
        "one two three",
        "four five six",
    ]


def test_generation_fills_capacity_across_normal_conversational_pauses():
    track = generate_caption_track(
        [
            segment("seg_1", 0, 1_000, "one two three four"),
            segment("seg_2", 2_300, 3_300, "five six seven eight"),
        ],
        "en",
        max_words_per_line=4,
        max_lines=2,
    )

    assert len(track.cues) == 1
    assert track.cues[0].lines == [
        "one two three four",
        "five six seven eight",
    ]


def test_generation_keeps_genuinely_long_pauses_as_caption_boundaries():
    track = generate_caption_track(
        [
            segment("seg_1", 0, 1_000, "one two"),
            segment("seg_2", 3_100, 4_100, "three four"),
        ],
        "en",
        max_words_per_line=4,
        max_lines=2,
    )

    assert [cue.lines for cue in track.cues] == [
        ["one", "two"],
        ["three", "four"],
    ]


def test_generation_does_not_merge_different_speakers():
    track = generate_caption_track(
        [
            segment("seg_1", 0, 1_000, "one two", "SPEAKER_01"),
            segment("seg_2", 1_000, 2_000, "three four", "SPEAKER_02"),
        ],
        "en",
        max_words_per_line=8,
        max_lines=1,
    )

    assert [cue.lines for cue in track.cues] == [
        ["one two"],
        ["three four"],
    ]


def test_regenerated_track_drives_preview_data_and_srt_export(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Generated captions"}
    ).json()
    project_id = project["project_id"]
    app.state.store.save_segment(
        project_id,
        Segment.model_validate(
            segment("seg_1", 0, 1_000, "one two three")
        ),
    )
    app.state.store.save_segment(
        project_id,
        Segment.model_validate(
            segment("seg_2", 1_000, 2_000, "four five six")
        ),
    )

    generated = client.post(
        f"/api/projects/{project_id}/captions/regenerate",
        json={
            "language": "en",
            "max_words_per_line": 4,
            "max_lines": 1,
        },
    )
    stored = client.get(
        f"/api/projects/{project_id}/captions?language=en"
    )
    exported = client.get(
        f"/api/projects/{project_id}/export/srt?language=en"
    )

    assert generated.status_code == 200
    assert stored.json()["stale"] is False
    assert stored.json()["cues"][0]["lines"] == ["one two three four"]
    assert "one two three four" in exported.text
    assert "five six" in exported.text

    client.patch(
        f"/api/projects/{project_id}/subtitle-style",
        json={"max_words_per_line": 6},
    )
    assert client.get(
        f"/api/projects/{project_id}/captions?language=en"
    ).json()["stale"] is True

    regenerated = client.post(
        f"/api/projects/{project_id}/captions/regenerate",
        json={
            "language": "en",
            "max_words_per_line": 6,
            "max_lines": 1,
        },
    )

    assert regenerated.status_code == 200
    assert len(regenerated.json()["cues"]) == 1
    assert regenerated.json()["cues"][0]["lines"] == [
        "one two three four five six"
    ]
    assert regenerated.json()["cues"][0]["start_ms"] == 0
    assert regenerated.json()["cues"][0]["end_ms"] == 2_000
    assert client.get(
        f"/api/projects/{project_id}/captions?language=en"
    ).json()["stale"] is False
