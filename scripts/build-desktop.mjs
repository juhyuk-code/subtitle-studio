import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";

import { commandNeedsShell, npmCommand } from "./platform-commands.mjs";

const projectRoot = process.cwd();
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

run(npmCommand(), ["run", "build"]);

const release = join(projectRoot, "release");
const work = join(projectRoot, ".desktop-build");
mkdirSync(release, { recursive: true });
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
