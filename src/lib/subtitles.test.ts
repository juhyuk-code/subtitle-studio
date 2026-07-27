import { describe, expect, it } from "vitest";
import { formatSrtTimestamp, toSrt } from "./subtitles";

describe("formatSrtTimestamp", () => {
  it("formats milliseconds as a Premiere-compatible SRT timestamp", () => {
    expect(formatSrtTimestamp(3_723_456)).toBe("01:02:03,456");
  });
});

describe("toSrt", () => {
  it("exports sequential, non-overlapping UTF-8 cues", () => {
    expect(
      toSrt([
        { id: "a", startMs: 1_200, endMs: 4_500, lines: ["No, that’s not", "what I’m saying."] },
        { id: "b", startMs: 4_700, endMs: 7_900, lines: ["You’re missing the point."] }
      ])
    ).toBe(
      "1\n00:00:01,200 --> 00:00:04,500\nNo, that’s not\nwhat I’m saying.\n\n" +
        "2\n00:00:04,700 --> 00:00:07,900\nYou’re missing the point.\n"
    );
  });
});
