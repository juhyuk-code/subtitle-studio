import { describe, expect, it } from "vitest";
import {
  containedMediaBounds,
  subtitlePreviewScale
} from "./media";

describe("subtitlePreviewScale", () => {
  it("uses the same 1080-line coordinate system as ASS rendering", () => {
    const scale = subtitlePreviewScale(290);

    expect(scale).toBeCloseTo(290 / 1080);
    expect(26 * scale).toBeCloseTo(6.98148, 4);
  });

  it("does not enlarge small preview text with an artificial floor", () => {
    expect(20 * subtitlePreviewScale(216)).toBe(4);
  });
});

describe("containedMediaBounds", () => {
  it("finds the true video rectangle inside a wide letterboxed player", () => {
    expect(containedMediaBounds(1020, 290, 1280, 720)).toEqual({
      left: 252.22222222222223,
      top: 0,
      width: 515.5555555555555,
      height: 290
    });
  });

  it("finds the true video rectangle when horizontal bars are present", () => {
    expect(containedMediaBounds(500, 500, 1920, 1080)).toEqual({
      left: 0,
      top: 109.375,
      width: 500,
      height: 281.25
    });
  });

  it("falls back to the container before metadata is available", () => {
    expect(containedMediaBounds(800, 290, 0, 0)).toEqual({
      left: 0,
      top: 0,
      width: 800,
      height: 290
    });
  });
});
