import re

from .models import NavigationMarker, TimestampClip

TIMESTAMP_LINE = re.compile(
    r"^(?P<timestamp>\d{1,3}:\d{2}(?::\d{2})?)\s+(?P<title>.+?)\s*$"
)


def timestamp_to_ms(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        if seconds >= 60:
            raise ValueError(f"Invalid timestamp: {value}")
        return (minutes * 60 + seconds) * 1000
    hours, minutes, seconds = parts
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid timestamp: {value}")
    return (hours * 3600 + minutes * 60 + seconds) * 1000


def _parse_timestamp_starts(
    text: str, duration_ms: int
) -> list[tuple[int, str]]:
    starts: list[tuple[int, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = TIMESTAMP_LINE.match(line)
        if not match:
            raise ValueError(
                f"Line {line_number} must start with MM:SS or HH:MM:SS."
            )
        starts.append(
            (
                timestamp_to_ms(match.group("timestamp")),
                match.group("title").strip(),
            )
        )

    if not starts:
        raise ValueError("Paste at least one timestamp and title.")
    if any(current[0] >= following[0] for current, following in zip(starts, starts[1:])):
        raise ValueError("Timestamps must be in increasing order.")
    if duration_ms <= 0:
        raise ValueError("Upload media before importing timestamps.")
    if starts[-1][0] >= duration_ms:
        raise ValueError("The final timestamp must be before the end of the media.")
    return starts


def parse_timestamp_markers(
    text: str, duration_ms: int
) -> list[NavigationMarker]:
    starts = _parse_timestamp_starts(text, duration_ms)
    return [
        NavigationMarker(
            marker_id=f"marker_{index + 1:03d}",
            timestamp_ms=timestamp_ms,
            title=title,
        )
        for index, (timestamp_ms, title) in enumerate(starts)
    ]


def parse_timestamp_clips(text: str, duration_ms: int) -> list[TimestampClip]:
    starts = _parse_timestamp_starts(text, duration_ms)

    return [
        TimestampClip(
            clip_id=f"clip_{index + 1:03d}",
            start_ms=start_ms,
            end_ms=starts[index + 1][0] if index + 1 < len(starts) else duration_ms,
            title=title,
        )
        for index, (start_ms, title) in enumerate(starts)
    ]


def selected_clip_ranges(clips: list[TimestampClip]) -> list[float]:
    ranges: list[float] = []
    for clip in clips:
        if clip.selected:
            ranges.extend([clip.start_ms / 1000, clip.end_ms / 1000])
    return ranges
