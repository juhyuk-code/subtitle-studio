import { describe, expect, it } from "vitest";
import { describeMediaPreparation } from "./upload";

describe("describeMediaPreparation", () => {
  it("makes upload and local audio processing visibly distinct", () => {
    expect(
      describeMediaPreparation({ phase: "uploading", progress: 0.42 })
    ).toEqual({
      title: "Uploading video · 42%",
      detail: "Keep Subtitle Studio open while the file is copied."
    });

    expect(
      describeMediaPreparation({ phase: "processing", progress: 1 })
    ).toEqual({
      title: "Preparing audio locally…",
      detail: "Long videos can take several minutes. Subtitle Studio is still working."
    });
  });
});
