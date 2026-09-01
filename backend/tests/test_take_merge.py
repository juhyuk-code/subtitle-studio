"""Tests for merge_segments_into_takes (continuous-take paragraph merging)."""

from backend.app.models import Segment, Word
from backend.app.services import merge_segments_into_takes


def _seg(segment_id, start, end, speaker="SPK", clip="clip_1", text="hi"):
    return Segment(
        segment_id=segment_id,
        start_ms=start,
        end_ms=end,
        clip_id=clip,
        speaker_id=speaker,
        raw_korean=text,
        words=[Word(text="hi", start_ms=start, end_ms=end)],
    )


def test_merges_consecutive_same_speaker_short_segments():
    segs = [
        _seg("seg_000001", 0, 1000, text="안녕"),
        _seg("seg_000002", 1100, 2500, text="하세요"),
        _seg("seg_000003", 2600, 4000, text="즐거운"),
    ]
    merged = merge_segments_into_takes(segs)
    assert len(merged) == 1
    assert merged[0].raw_korean == "안녕 하세요 즐거운"
    assert merged[0].start_ms == 0
    assert merged[0].end_ms == 4000
    assert "merged_into_take" in merged[0].change_reasons


def test_does_not_merge_across_speakers():
    segs = [
        _seg("seg_000001", 0, 1000, speaker="A", text="안녕"),
        _seg("seg_000002", 1100, 2500, speaker="B", text="하세요"),
        _seg("seg_000003", 2600, 4000, speaker="B", text="즐거운"),
    ]
    merged = merge_segments_into_takes(segs)
    # A stays separate, B's two merge
    assert len(merged) == 2
    assert merged[0].raw_korean == "안녕"
    assert merged[1].raw_korean == "하세요 즐거운"


def test_does_not_merge_across_long_pause():
    segs = [
        _seg("seg_000001", 0, 1000, text="안녕"),
        _seg("seg_000002", 3000, 4500, text="하세요"),  # gap 2000ms > 1200
    ]
    merged = merge_segments_into_takes(segs)
    assert len(merged) == 2


def test_does_not_merge_across_clips():
    segs = [
        _seg("seg_000001", 0, 1000, clip="clip_1", text="안녕"),
        _seg("seg_000002", 1100, 2500, clip="clip_2", text="하세요"),
    ]
    merged = merge_segments_into_takes(segs)
    assert len(merged) == 2


def test_does_not_merge_past_max_duration():
    segs = [
        _seg("seg_000001", 0, 44000, text="a"),
        _seg("seg_000002", 45000, 46000, text="b"),  # would exceed 45s cap
    ]
    merged = merge_segments_into_takes(segs)
    assert len(merged) == 2


def test_joins_language_fields():
    segs = [
        _seg("seg_000001", 0, 1000, text="안녕"),
        _seg("seg_000002", 1100, 2500, text="하세요"),
    ]
    segs[0].english = "Hello."
    segs[1].english = "There."
    segs[0].pass_2_korean = "안녕하세요"
    segs[1].pass_2_korean = "반갑습니다"
    merged = merge_segments_into_takes(segs)
    assert merged[0].english == "Hello. There."
    assert merged[0].pass_2_korean == "안녕하세요 반갑습니다"


def test_renumbers_segment_ids():
    segs = [
        _seg("seg_old_1", 0, 1000, text="a"),
        _seg("seg_old_2", 1100, 2500, text="b"),
        _seg("seg_old_3", 2600, 4000, text="c"),
    ]
    merged = merge_segments_into_takes(segs)
    assert [s.segment_id for s in merged] == ["seg_000001"]
