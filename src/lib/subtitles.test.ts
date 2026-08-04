import { describe, expect, it } from "vitest";
import {
  balanceCaptionWords,
  formatSrtTimestamp,
  paginateCaptionByWords,
  paginateCaptionToWidth,
  toSrt,
  wrapCaptionByWords
} from "./subtitles";

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

describe("wrapCaptionByWords", () => {
  it("keeps every generated line within the selected word count", () => {
    expect(
      wrapCaptionByWords(
        "but first something a little hot a model called Kimi came out",
        4
      )
    ).toBe(
      "but first something a\nlittle hot a model\ncalled Kimi came out"
    );
  });

  it("preserves explicit paragraph breaks while wrapping each paragraph", () => {
    expect(wrapCaptionByWords("one two three\nfour five six", 2)).toBe(
      "one two\nthree\nfour five\nsix"
    );
  });
});

describe("paginateCaptionByWords", () => {
  it("limits every caption page to the selected line count", () => {
    expect(
      paginateCaptionByWords(
        "one two three four five six seven eight nine ten",
        3,
        2
      )
    ).toEqual([
      "one two three\nfour five six",
      "seven eight\nnine ten"
    ]);
  });

  it("defaults naturally to one visible line per page", () => {
    expect(paginateCaptionByWords("one two three four five", 2, 1)).toEqual([
      "one two",
      "three four",
      "five"
    ]);
  });
});

describe("balanceCaptionWords", () => {
  it("uses both selected lines even below the per-line word limit", () => {
    expect(
      balanceCaptionWords(
        ["one", "two", "three", "four", "five", "six"],
        2
      )
    ).toEqual(["one two three", "four five six"]);
  });

  it("uses only as many lines as there are words", () => {
    expect(balanceCaptionWords(["one", "two"], 4)).toEqual([
      "one",
      "two"
    ]);
  });
});

describe("paginateCaptionToWidth", () => {
  it("keeps the font size fixed by moving excess words to another page", () => {
    const measureLine = (line: string) => line.length * 10;
    expect(
      paginateCaptionToWidth(
        "one two three four five six",
        40,
        1,
        100,
        measureLine
      )
    ).toEqual(["one two", "three four", "five six"]);
  });

  it("still honors the selected words-per-line maximum", () => {
    const measureLine = (line: string) => line.length;
    expect(
      paginateCaptionToWidth(
        "one two three four five six",
        2,
        2,
        1_000,
        measureLine
      )
    ).toEqual(["one two\nthree four", "five\nsix"]);
  });
});
