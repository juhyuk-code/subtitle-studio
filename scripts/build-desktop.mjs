import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { join } from "node:path";

import {
  commandNeedsShell,
  desktopUserDataRoot
} from "./platform-commands.mjs";

const projectRoot = process.cwd();
const stageUpdate = process.argv.includes("--stage-update");
const python =
  process.platform === "win32"
    ? join(projectRoot, ".venv", "Scripts", "python.exe")
    : join(projectRoot, ".venv", "bin", "python");

if (!existsSync(python)) {
  console.error("Create .venv and install the desktop dependencies first.");
  process.exit(1);
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: "inherit",
    shell: commandNeedsShell(command)
  });
  if (result.error) console.error(result.error);
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run(process.execPath, [join(projectRoot, "node_modules", "typescript", "bin", "tsc"), "-b"]);
run(process.execPath, [join(projectRoot, "node_modules", "vite", "bin", "vite.js"), "build"]);
run(python, [join(projectRoot, "scripts", "create_app_icons.py")]);

const releaseRoot = join(projectRoot, "release");
const release = stageUpdate
  ? join(desktopUserDataRoot(), "_update")
  : releaseRoot;
const work = join(
  projectRoot,
  stageUpdate ? ".desktop-update-build" : ".desktop-build"
);
mkdirSync(releaseRoot, { recursive: true });
if (stageUpdate) rmSync(release, { recursive: true, force: true });
rmSync(work, { recursive: true, force: true });

run(python, [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--distpath",
  release,
  "--workpath",
  work,
  join(projectRoot, "packaging", "subtitle_studio.spec")
]);

if (process.platform === "win32") {
  copyFileSync(
    join(projectRoot, "QUICK_START_KO.md"),
    join(release, "Subtitle Studio", "Subtitle Studio Korean Quick Start.md")
  );
  copyFileSync(
    join(projectRoot, "USER_MANUAL.md"),
    join(release, "Subtitle Studio", "Subtitle Studio User Manual.md")
  );
}

if (stageUpdate) {
  writeFileSync(
    join(release, ".subtitle-studio-update.json"),
    JSON.stringify({ built_at: new Date().toISOString() }, null, 2),
    "utf8"
  );
  console.log("Update ready. Open Settings in Subtitle Studio and choose Apply update & restart.");
}
