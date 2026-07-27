import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const candidates =
  process.platform === "win32"
    ? [resolve(".venv", "Scripts", "python.exe")]
    : [resolve(".venv", "bin", "python"), resolve(".venv", "Scripts", "python.exe")];

const python = candidates.find(existsSync);
if (!python) {
  console.error(
    "Python environment not found. Create it with: python -m venv .venv"
  );
  process.exit(1);
}

const child = spawn(python, process.argv.slice(2), {
  cwd: process.cwd(),
  env: process.env,
  stdio: "inherit",
});

child.on("error", (error) => {
  console.error(`Could not start Python: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
