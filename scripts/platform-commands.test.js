import { describe, expect, it } from "vitest";

import {
  commandNeedsShell,
  desktopUserDataRoot,
  npmCommand
} from "./platform-commands.mjs";

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

describe("desktopUserDataRoot", () => {
  it("uses Application Support for a macOS update", () => {
    expect(desktopUserDataRoot("darwin", "/Users/editor", {})).toBe(
      "/Users/editor/Library/Application Support/Subtitle Studio"
    );
  });

  it("uses Local AppData for a Windows update", () => {
    expect(
      desktopUserDataRoot("win32", "C:\\Users\\editor", {
        LOCALAPPDATA: "C:\\Users\\editor\\AppData\\Local"
      })
    ).toContain("Subtitle Studio");
  });
});
