import { describe, expect, it } from "vitest";

import { npmCommand } from "./platform-commands.mjs";

describe("npmCommand", () => {
  it("uses the Windows command shim on win32", () => {
    expect(npmCommand("win32")).toBe("npm.cmd");
  });
});
