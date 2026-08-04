import json
from pathlib import Path
from types import SimpleNamespace

from backend.app import desktop_update

from backend.app.desktop_update import (
    UPDATE_MANIFEST,
    build_macos_restart_script,
    build_restart_script,
    desktop_update_paths,
    scheduled_task_create_args,
    update_status,
)


def test_desktop_update_paths_keep_staged_build_outside_live_app(tmp_path: Path):
    executable = tmp_path / "release" / "Subtitle Studio" / "Subtitle Studio.exe"
    update_root = tmp_path / "user-data" / "_update"

    paths = desktop_update_paths(executable, update_root=update_root)

    assert paths.live_dir == executable.parent.resolve()
    assert paths.staged_dir == update_root.resolve() / "Subtitle Studio"
    assert paths.staged_executable == paths.staged_dir / "Subtitle Studio.exe"
    assert paths.backup_dir == tmp_path.resolve() / "release" / "_previous"


def test_update_status_finds_staged_windows_build(tmp_path: Path):
    executable = tmp_path / "release" / "Subtitle Studio" / "Subtitle Studio.exe"
    update_root = tmp_path / "user-data" / "_update"
    paths = desktop_update_paths(executable, update_root=update_root)
    paths.staged_dir.mkdir(parents=True)
    paths.staged_executable.write_bytes(b"new build")
    (paths.update_root / UPDATE_MANIFEST).write_text(
        json.dumps({"built_at": "2026-08-02T12:00:00.000Z"}),
        encoding="utf-8",
    )

    status = update_status(
        executable,
        frozen=True,
        platform="win32",
        update_root=update_root,
    )

    assert status == {
        "supported": True,
        "available": True,
        "built_at": "2026-08-02T12:00:00.000Z",
    }


def test_desktop_update_paths_replace_the_complete_macos_bundle(tmp_path: Path):
    executable = (
        tmp_path
        / "release"
        / "Subtitle Studio.app"
        / "Contents"
        / "MacOS"
        / "Subtitle Studio"
    )

    update_root = tmp_path / "user-data" / "_update"
    paths = desktop_update_paths(
        executable,
        platform="darwin",
        update_root=update_root,
    )

    assert paths.live_dir == tmp_path.resolve() / "release" / "Subtitle Studio.app"
    assert paths.staged_dir == (
        update_root.resolve() / "Subtitle Studio.app"
    )
    assert paths.staged_executable == (
        paths.staged_dir / "Contents" / "MacOS" / "Subtitle Studio"
    )


def test_update_status_finds_staged_macos_app(tmp_path: Path):
    executable = (
        tmp_path
        / "release"
        / "Subtitle Studio.app"
        / "Contents"
        / "MacOS"
        / "Subtitle Studio"
    )
    update_root = tmp_path / "user-data" / "_update"
    paths = desktop_update_paths(
        executable,
        platform="darwin",
        update_root=update_root,
    )
    paths.staged_executable.parent.mkdir(parents=True)
    paths.staged_executable.write_bytes(b"new build")
    (paths.update_root / UPDATE_MANIFEST).write_text(
        json.dumps({"built_at": "2026-08-04T12:00:00.000Z"}),
        encoding="utf-8",
    )

    status = update_status(
        executable,
        frozen=True,
        platform="darwin",
        update_root=update_root,
    )

    assert status == {
        "supported": True,
        "available": True,
        "built_at": "2026-08-04T12:00:00.000Z",
    }


def test_macos_update_script_swaps_app_bundles_and_reopens(tmp_path: Path):
    executable = (
        tmp_path
        / "release"
        / "Subtitle Studio.app"
        / "Contents"
        / "MacOS"
        / "Subtitle Studio"
    )
    paths = desktop_update_paths(
        executable,
        platform="darwin",
        update_root=tmp_path / "user-data" / "_update",
    )

    script = build_macos_restart_script(
        paths,
        process_id=789,
        apply_update=True,
        log_path=tmp_path / "update-error.log",
    )

    assert "PROCESS_ID=789" in script
    assert 'if ! /bin/mv "$LIVE" "$BACKUP"' in script
    assert 'if ! /bin/mv "$STAGED" "$LIVE"' in script
    assert '/bin/mv "$BACKUP" "$LIVE"' in script
    assert '/usr/bin/open -n "$LIVE"' in script
    assert "STAGED_EXECUTABLE=" in script
    assert "Contents" in script and "MacOS" in script


def test_plain_macos_restart_does_not_replace_app(tmp_path: Path):
    executable = (
        tmp_path
        / "Subtitle Studio.app"
        / "Contents"
        / "MacOS"
        / "Subtitle Studio"
    )

    script = build_macos_restart_script(
        desktop_update_paths(
            executable,
            platform="darwin",
            update_root=tmp_path / "user-data" / "_update",
        ),
        process_id=321,
        apply_update=False,
        log_path=tmp_path / "update-error.log",
    )

    assert "STAGED=" not in script
    assert "/bin/mv" not in script
    assert '/usr/bin/open -n "$LIVE"' in script


def test_macos_restart_launches_a_detached_native_helper(monkeypatch, tmp_path):
    executable = (
        tmp_path
        / "release"
        / "Subtitle Studio.app"
        / "Contents"
        / "MacOS"
        / "Subtitle Studio"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"app")
    calls = []
    exits = []

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(desktop_update.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_update.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        desktop_update,
        "user_data_root",
        lambda: tmp_path / "user-data",
    )
    monkeypatch.setattr(
        desktop_update.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(desktop_update.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(desktop_update.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(desktop_update.time, "sleep", lambda _seconds: None)

    desktop_update.launch_restart(
        apply_update=False,
        executable=executable,
        exit_process=exits.append,
    )

    command, kwargs = calls[0]
    assert command[0] == "/bin/sh"
    assert Path(command[1]).is_file()
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert exits == [0]


def test_update_status_is_disabled_outside_installed_windows_app(tmp_path: Path):
    status = update_status(
        tmp_path / "Subtitle Studio.exe",
        frozen=False,
        platform="win32",
    )

    assert status == {
        "supported": False,
        "available": False,
        "built_at": None,
    }


def test_update_script_swaps_builds_and_restores_backup_on_failure(tmp_path: Path):
    executable = tmp_path / "release" / "Subtitle Studio" / "Subtitle Studio's.exe"
    paths = desktop_update_paths(executable)

    script = build_restart_script(
        paths,
        process_id=123,
        apply_update=True,
        log_path=tmp_path / "update-error.log",
        task_name="SubtitleStudioUpdate-test",
    )

    assert "$processId = 123" in script
    assert "$executableName = 'Subtitle Studio''s.exe'" in script
    assert "Move-Item -LiteralPath $live -Destination $backup" in script
    assert "Move-Item -LiteralPath $staged -Destination $live" in script
    assert "Move-Item -LiteralPath $backup -Destination $live" in script
    assert "Start-Process -FilePath" in script
    assert "$MyInvocation.MyCommand.Path" in script
    assert "schtasks.exe /Delete /TN $taskName /F" in script


def test_plain_restart_script_does_not_replace_installation(tmp_path: Path):
    executable = tmp_path / "release" / "Subtitle Studio" / "Subtitle Studio.exe"

    script = build_restart_script(
        desktop_update_paths(executable),
        process_id=456,
        apply_update=False,
        log_path=tmp_path / "update-error.log",
    )

    assert "$processId = 456" in script
    assert "$staged" not in script
    assert "Move-Item" not in script
    assert "Start-Process -FilePath" in script


def test_restart_worker_uses_independent_one_time_windows_task(tmp_path: Path):
    script_path = tmp_path / "SubtitleStudioUpdate-test.ps1"

    arguments = scheduled_task_create_args("SubtitleStudioUpdate-test", script_path)

    assert arguments[:4] == [
        "schtasks.exe",
        "/Create",
        "/TN",
        "SubtitleStudioUpdate-test",
    ]
    assert "ONCE" in arguments
    assert arguments[arguments.index("/ST") + 1] == "00:00"
    assert str(script_path) in arguments[5]
    assert "-WindowStyle Hidden" in arguments[5]


def test_programmatic_restart_disables_the_manual_close_confirmation():
    desktop_path = Path(__file__).parents[1] / "desktop.py"
    source = desktop_path.read_text(encoding="utf-8")

    disable_confirmation = source.index("window.confirm_close = False")
    destroy_window = source.index("window.destroy()")
    assert disable_confirmation < destroy_window
