import { describe, expect, it } from "vitest";
import { readableErrorMessage } from "./errors";

describe("readableErrorMessage", () => {
  it("formats FastAPI validation details", () => {
    expect(
      readableErrorMessage([
        {
          loc: ["body", "start_ms"],
          msg: "Input should be greater than or equal to 0"
        }
      ])
    ).toBe("start_ms: Input should be greater than or equal to 0");
  });

  it("unwraps nested API errors", () => {
    expect(
      readableErrorMessage({
        detail: { message: "The clip boundary is invalid." }
      })
    ).toBe("The clip boundary is invalid.");
  });

  it("never displays an object coercion", () => {
    expect(readableErrorMessage("[object Object]")).toBe(
      "Something went wrong. Please try again."
    );
  });
});
