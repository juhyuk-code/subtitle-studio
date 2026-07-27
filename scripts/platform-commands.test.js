import { describe, expect, it } from "vitest";

import { commandNeedsShell, npmCommand } from "./platform-commands.mjs";

describe("npmCommand", () => {
  it("uses the Windows command shim on win32", () => {
    expect(npmCommand("win32")).toBe("npm.cmd");
  });
});

describe("commandNeedsShell", () => {
  it("runs Windows command shims through the shell", () => {
    expect(commandNeedsShell("npm.cmd", "win32")).toBe(true);
  });
});
