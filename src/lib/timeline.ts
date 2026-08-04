export interface TimelineViewport {
  startMs: number;
  endMs: number;
  durationMs: number;
  centerMs: number;
}

export function timelineViewport(
  timelineDurationMs: number,
  zoom: number,
  focusMs: number
): TimelineViewport {
  const safeDurationMs = Math.max(1, timelineDurationMs);
  const safeZoom = Math.max(1, zoom);
  const durationMs = safeDurationMs / safeZoom;
  const startMs = Math.max(
    0,
    Math.min(
      safeDurationMs - durationMs,
      focusMs - durationMs / 2
    )
  );
  return {
    startMs,
    endMs: startMs + durationMs,
    durationMs,
    centerMs: startMs + durationMs / 2
  };
}

export function panTimelineViewport(
  timelineDurationMs: number,
  zoom: number,
  focusMs: number,
  deltaMs: number
): TimelineViewport {
  return timelineViewport(
    timelineDurationMs,
    zoom,
    focusMs + deltaMs
  );
}

export function snapBoundaryToPlayhead(
  boundaryMs: number,
  playheadMs: number,
  viewportDurationMs: number,
  viewportWidthPx: number,
  minimumMs: number,
  maximumMs: number,
  thresholdPx = 12
): number {
  if (
    viewportDurationMs <= 0 ||
    viewportWidthPx <= 0 ||
    playheadMs < minimumMs ||
    playheadMs > maximumMs
  ) {
    return boundaryMs;
  }
  const thresholdMs =
    (viewportDurationMs / viewportWidthPx) * thresholdPx;
  return Math.abs(boundaryMs - playheadMs) <= thresholdMs
    ? playheadMs
    : boundaryMs;
}
