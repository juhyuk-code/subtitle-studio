from backend.app.clips import parse_timestamp_clips, selected_clip_ranges
from backend.app.models import TimestampClip


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
