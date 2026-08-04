import { describe, expect, it } from "vitest";
import {
  panTimelineViewport,
  snapBoundaryToPlayhead,
  timelineViewport
} from "./timeline";

describe("timelineViewport", () => {
  it("keeps a focused editing viewport stable", () => {
    const viewport = timelineViewport(600_000, 10, 245_000);

    expect(viewport).toEqual({
      startMs: 215_000,
      endMs: 275_000,
      durationMs: 60_000,
      centerMs: 245_000
    });
  });

  it("reports the actual center when the viewport is clamped at an edge", () => {
    expect(timelineViewport(600_000, 10, 5_000)).toEqual({
      startMs: 0,
      endMs: 60_000,
      durationMs: 60_000,
      centerMs: 30_000
    });
  });

  it("changes viewport size only when zoom changes", () => {
    const beforeBoundaryEdit = timelineViewport(
      600_000,
      12,
      245_000
    );
    const afterBoundaryEdit = timelineViewport(
      600_000,
      12,
      beforeBoundaryEdit.centerMs
    );

    expect(afterBoundaryEdit).toEqual(beforeBoundaryEdit);
    expect(timelineViewport(600_000, 24, 245_000).durationMs).toBe(
      beforeBoundaryEdit.durationMs / 2
    );
  });

  it("pans without changing the zoomed viewport duration", () => {
    const panned = panTimelineViewport(
      600_000,
      10,
      245_000,
      12_000
    );

    expect(panned).toEqual({
      startMs: 227_000,
      endMs: 287_000,
      durationMs: 60_000,
      centerMs: 257_000
    });
  });

  it("clamps panning at the ends of the media", () => {
    expect(
      panTimelineViewport(600_000, 10, 575_000, 100_000)
    ).toEqual({
      startMs: 540_000,
      endMs: 600_000,
      durationMs: 60_000,
      centerMs: 570_000
    });
  });

  it("snaps a nearby boundary to the playhead", () => {
    expect(
      snapBoundaryToPlayhead(
        10_450,
        10_000,
        60_000,
        1_200,
        0,
        30_000
      )
    ).toBe(10_000);
  });

  it("leaves a distant boundary under the pointer", () => {
    expect(
      snapBoundaryToPlayhead(
        10_700,
        10_000,
        60_000,
        1_200,
        0,
        30_000
      )
    ).toBe(10_700);
  });

  it("does not snap to a playhead outside the boundary limits", () => {
    expect(
      snapBoundaryToPlayhead(
        29_700,
        30_000,
        60_000,
        1_200,
        0,
        29_900
      )
    ).toBe(29_700);
  });
});
