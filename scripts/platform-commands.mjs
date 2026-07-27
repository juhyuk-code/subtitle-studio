export function npmCommand(platform = process.platform) {
  return platform === "win32" ? "npm.cmd" : "npm";
}

export function commandNeedsShell(command, platform = process.platform) {
  return platform === "win32" && command.toLowerCase().endsWith(".cmd");
}
