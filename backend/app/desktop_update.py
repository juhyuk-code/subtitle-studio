import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.app.desktop_paths import hidden_subprocess_kwargs, user_data_root


UPDATE_MANIFEST = ".subtitle-studio-update.json"


@dataclass(frozen=True)
class DesktopUpdatePaths:
    executable: Path
    live_dir: Path
    release_dir: Path
    update_root: Path
    staged_dir: Path
    staged_executable: Path
    backup_dir: Path


def _macos_app_bundle(executable: Path) -> Path | None:
    for candidate in (executable, *executable.parents):
        if candidate.suffix.lower() == ".app":
            return candidate
    return None


def desktop_update_paths(
    executable: Path | None = None,
    *,
    platform: str | None = None,
    update_root: Path | None = None,
) -> DesktopUpdatePaths:
    current_executable = (executable or Path(sys.executable)).resolve()
    current_platform = sys.platform if platform is None else platform
    macos_bundle = (
        _macos_app_bundle(current_executable)
        if current_platform == "darwin"
        else None
    )
    live_dir = macos_bundle or current_executable.parent
    release_dir = live_dir.parent
    resolved_update_root = (update_root or user_data_root() / "_update").resolve()
    staged_dir = resolved_update_root / live_dir.name
    backup_dir = release_dir / "_previous"
    executable_relative = current_executable.relative_to(live_dir)
    return DesktopUpdatePaths(
        executable=current_executable,
        live_dir=live_dir,
        release_dir=release_dir,
        update_root=resolved_update_root,
        staged_dir=staged_dir,
        staged_executable=staged_dir / executable_relative,
        backup_dir=backup_dir,
    )


def update_status(
    executable: Path | None = None,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    update_root: Path | None = None,
) -> dict[str, object]:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    current_platform = sys.platform if platform is None else platform
    if current_platform not in {"win32", "darwin"} or not is_frozen:
        return {
            "supported": False,
            "available": False,
            "built_at": None,
        }

    paths = desktop_update_paths(
        executable,
        platform=current_platform,
        update_root=update_root,
    )
    available = paths.staged_executable.is_file()
    built_at = None
    manifests = (
        paths.update_root / UPDATE_MANIFEST,
        paths.staged_dir / UPDATE_MANIFEST,
    )
    if available:
        for manifest in manifests:
            if not manifest.is_file():
                continue
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
                built_at = value.get("built_at")
            except (OSError, json.JSONDecodeError):
                built_at = None
            break
    return {
        "supported": True,
        "available": available,
        "built_at": built_at,
    }


def _powershell_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_restart_script(
    paths: DesktopUpdatePaths,
    *,
    process_id: int,
    apply_update: bool,
    log_path: Path,
    task_name: str | None = None,
) -> str:
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$processId = {process_id}",
        f"$live = {_powershell_literal(paths.live_dir)}",
        f"$executableName = {_powershell_text(paths.executable.name)}",
        f"$logPath = {_powershell_literal(log_path)}",
    ]
    if apply_update:
        lines.extend(
            [
                f"$staged = {_powershell_literal(paths.staged_dir)}",
                f"$backup = {_powershell_literal(paths.backup_dir)}",
                f"$updateRoot = {_powershell_literal(paths.update_root)}",
            ]
        )
    if task_name:
        lines.append(f"$taskName = {_powershell_text(task_name)}")
    lines.extend([
        "try {",
        "Write-Output 'Updater started'",
        "  while (Get-Process -Id $processId -ErrorAction SilentlyContinue) {",
        "    Start-Sleep -Milliseconds 200",
        "  }",
    ])
    if apply_update:
        lines.extend(
            [
                "  if (-not (Test-Path -LiteralPath (Join-Path $staged $executableName))) {",
                "    throw 'The staged Subtitle Studio update is missing.'",
                "  }",
                "  if (Test-Path -LiteralPath $backup) {",
                "    Remove-Item -LiteralPath $backup -Recurse -Force",
                "  }",
                "  Move-Item -LiteralPath $live -Destination $backup",
                "  try {",
                "    Move-Item -LiteralPath $staged -Destination $live",
                "  } catch {",
                "    Move-Item -LiteralPath $backup -Destination $live",
                "    throw",
                "  }",
                "  Start-Process -FilePath (Join-Path $live $executableName) -WorkingDirectory $live",
                "  Remove-Item -LiteralPath $backup -Recurse -Force",
                "  if (Test-Path -LiteralPath $updateRoot) {",
                "    Remove-Item -LiteralPath $updateRoot -Recurse -Force",
                "  }",
            ]
        )
    else:
        lines.append(
            "  Start-Process -FilePath (Join-Path $live $executableName) -WorkingDirectory $live"
        )
    lines.extend([
        "  Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue",
        "} catch {",
        "  $_ | Out-String | Set-Content -LiteralPath $logPath",
    ])
    if apply_update:
        lines.extend([
            "  if ((-not (Test-Path -LiteralPath $live)) -and (Test-Path -LiteralPath $backup)) {",
            "    Move-Item -LiteralPath $backup -Destination $live",
            "  }",
        ])
    lines.extend([
        "  if (Test-Path -LiteralPath (Join-Path $live $executableName)) {",
        "    Start-Process -FilePath (Join-Path $live $executableName) -WorkingDirectory $live",
        "  }",
        "} finally {",
    ])
    if task_name:
        lines.append(
            "  schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null"
        )
    lines.extend([
        "  Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue",
        "}",
    ])
    return "\n".join(lines) + "\n"


def _shell_literal(value: Path | str) -> str:
    return shlex.quote(str(value))


def build_macos_restart_script(
    paths: DesktopUpdatePaths,
    *,
    process_id: int,
    apply_update: bool,
    log_path: Path,
) -> str:
    lines = [
        "#!/bin/sh",
        "set -u",
        f"PROCESS_ID={process_id}",
        f"LIVE={_shell_literal(paths.live_dir)}",
        f"LOG_PATH={_shell_literal(log_path)}",
        'SCRIPT_PATH="$0"',
        "cleanup() { /bin/rm -f \"$SCRIPT_PATH\"; }",
        "report_error() { printf '%s\\n' \"$1\" > \"$LOG_PATH\"; }",
        "while /bin/kill -0 \"$PROCESS_ID\" 2>/dev/null; do /bin/sleep 0.2; done",
    ]
    if apply_update:
        lines.extend(
            [
                f"STAGED={_shell_literal(paths.staged_dir)}",
                f"STAGED_EXECUTABLE={_shell_literal(paths.staged_executable)}",
                f"BACKUP={_shell_literal(paths.backup_dir)}",
                f"UPDATE_ROOT={_shell_literal(paths.update_root)}",
                'if [ ! -x "$STAGED_EXECUTABLE" ]; then',
                "  report_error 'The staged Subtitle Studio update is missing or is not executable.'",
                '  /usr/bin/open -n "$LIVE" >/dev/null 2>&1 || true',
                "  cleanup",
                "  exit 1",
                "fi",
                '/bin/rm -rf "$BACKUP"',
                'if ! /bin/mv "$LIVE" "$BACKUP"; then',
                "  report_error 'Subtitle Studio could not move the installed app while applying the update.'",
                '  /usr/bin/open -n "$LIVE" >/dev/null 2>&1 || true',
                "  cleanup",
                "  exit 1",
                "fi",
                'if ! /bin/mv "$STAGED" "$LIVE"; then',
                '  /bin/mv "$BACKUP" "$LIVE" >/dev/null 2>&1 || true',
                "  report_error 'Subtitle Studio could not install the staged update. The previous app was restored.'",
                '  /usr/bin/open -n "$LIVE" >/dev/null 2>&1 || true',
                "  cleanup",
                "  exit 1",
                "fi",
                'if ! /usr/bin/open -n "$LIVE"; then',
                "  report_error 'The update was installed, but macOS could not reopen Subtitle Studio.'",
                "  cleanup",
                "  exit 1",
                "fi",
                '/bin/rm -rf "$BACKUP" "$UPDATE_ROOT"',
            ]
        )
    else:
        lines.extend(
            [
                'if ! /usr/bin/open -n "$LIVE"; then',
                "  report_error 'macOS could not reopen Subtitle Studio.'",
                "  cleanup",
                "  exit 1",
                "fi",
            ]
        )
    lines.extend(
        [
            '/bin/rm -f "$LOG_PATH"',
            "cleanup",
            "exit 0",
        ]
    )
    return "\n".join(lines) + "\n"


def scheduled_task_create_args(task_name: str, script_path: Path) -> list[str]:
    task_command = (
        "powershell.exe -NoProfile -NonInteractive "
        "-WindowStyle Hidden "
        f'-ExecutionPolicy Bypass -File "{script_path}"'
    )
    return [
        "schtasks.exe",
        "/Create",
        "/TN",
        task_name,
        "/TR",
        task_command,
        "/SC",
        "ONCE",
        "/ST",
        "00:00",
        "/F",
        "/RL",
        "LIMITED",
    ]


def launch_restart(
    *,
    apply_update: bool,
    executable: Path | None = None,
    exit_process: Callable[[int], None] = os._exit,
) -> None:
    if sys.platform not in {"win32", "darwin"} or not getattr(
        sys, "frozen", False
    ):
        raise RuntimeError("Restart is available in the installed desktop app.")

    paths = desktop_update_paths(executable, platform=sys.platform)
    if apply_update and not paths.staged_executable.is_file():
        raise RuntimeError("No new Subtitle Studio build is ready yet.")

    data_root = user_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        script_path = Path(tempfile.gettempdir()) / (
            f"SubtitleStudioUpdate-{uuid.uuid4().hex}.sh"
        )
        script_path.write_text(
            build_macos_restart_script(
                paths,
                process_id=os.getpid(),
                apply_update=apply_update,
                log_path=data_root / "update-error.log",
            ),
            encoding="utf-8",
        )
        script_path.chmod(0o700)
        launcher_log_path = data_root / "update-launch.log"
        with launcher_log_path.open("a", encoding="utf-8") as launcher_log:
            subprocess.Popen(
                ["/bin/sh", str(script_path)],
                stdin=subprocess.DEVNULL,
                stdout=launcher_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )

        def exit_after_response() -> None:
            time.sleep(0.75)
            exit_process(0)

        threading.Thread(target=exit_after_response, daemon=True).start()
        return

    task_name = f"SubtitleStudioUpdate-{uuid.uuid4().hex}"
    script_path = Path(tempfile.gettempdir()) / f"{task_name}.ps1"
    script_path.write_text(
        build_restart_script(
            paths,
            process_id=os.getpid(),
            apply_update=apply_update,
            log_path=data_root / "update-error.log",
            task_name=task_name,
        ),
        encoding="utf-8",
    )
    launcher_log_path = data_root / "update-launch.log"
    create_result = subprocess.run(
        scheduled_task_create_args(task_name, script_path),
        capture_output=True,
        text=True,
        timeout=15,
        **hidden_subprocess_kwargs(),
    )
    if create_result.returncode != 0:
        launcher_log_path.write_text(
            create_result.stdout + create_result.stderr,
            encoding="utf-8",
        )
        script_path.unlink(missing_ok=True)
        raise RuntimeError("Windows could not prepare the update restart.")
    run_result = subprocess.run(
        ["schtasks.exe", "/Run", "/TN", task_name],
        capture_output=True,
        text=True,
        timeout=15,
        **hidden_subprocess_kwargs(),
    )
    if run_result.returncode != 0:
        subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
            timeout=15,
            **hidden_subprocess_kwargs(),
        )
        launcher_log_path.write_text(
            run_result.stdout + run_result.stderr,
            encoding="utf-8",
        )
        script_path.unlink(missing_ok=True)
        raise RuntimeError("Windows could not start the update restart.")
    launcher_log_path.write_text(
        create_result.stdout + run_result.stdout,
        encoding="utf-8",
    )

    def exit_after_response() -> None:
        time.sleep(0.75)
        exit_process(0)

    threading.Thread(target=exit_after_response, daemon=True).start()
