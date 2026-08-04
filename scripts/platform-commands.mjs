import { homedir } from "node:os";
import { posix, win32 } from "node:path";


export function npmCommand(platform = process.platform) {
  return platform === "win32" ? "npm.cmd" : "npm";
}

export function commandNeedsShell(command, platform = process.platform) {
  return platform === "win32" && command.toLowerCase().endsWith(".cmd");
}

export function desktopUserDataRoot(
  platform = process.platform,
  home = homedir(),
  environment = process.env
) {
  const path = platform === "win32" ? win32 : posix;
  if (platform === "darwin") {
    return path.join(home, "Library", "Application Support", "Subtitle Studio");
  }
  if (platform === "win32") {
    return path.join(
      environment.LOCALAPPDATA || path.join(home, "AppData", "Local"),
      "Subtitle Studio"
    );
  }
  return path.join(
    environment.XDG_DATA_HOME || path.join(home, ".local", "share"),
    "Subtitle Studio"
  );
}
