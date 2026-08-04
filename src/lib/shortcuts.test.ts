import { describe, expect, it } from "vitest";
import { isTextEditingTarget } from "./shortcuts";

describe("isTextEditingTarget", () => {
  it("keeps normal spacebar input while entering text", () => {
    expect(isTextEditingTarget(document.createElement("textarea"))).toBe(true);

    const text = document.createElement("input");
    text.type = "text";
    expect(isTextEditingTarget(text)).toBe(true);

    const editable = document.createElement("div");
    editable.contentEditable = "true";
    expect(isTextEditingTarget(editable)).toBe(true);
  });

  it("allows playback shortcuts after using style controls", () => {
    const range = document.createElement("input");
    range.type = "range";
    expect(isTextEditingTarget(range)).toBe(false);

    const color = document.createElement("input");
    color.type = "color";
    expect(isTextEditingTarget(color)).toBe(false);
    expect(isTextEditingTarget(document.createElement("select"))).toBe(false);
    expect(isTextEditingTarget(document.createElement("button"))).toBe(false);
  });
});
