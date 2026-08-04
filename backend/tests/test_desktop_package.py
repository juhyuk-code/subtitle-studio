import subprocess
import sys
from pathlib import Path

from backend import desktop


def test_packaged_self_test_checks_dependencies_resources_and_binaries(
    monkeypatch,
    tmp_path: Path,
):
    imported = []
    commands = []
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("app", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "QUICK_START_KO.md").write_text(
        "quick start", encoding="utf-8"
    )
    (tmp_path / "docs" / "USER_MANUAL.md").write_text(
        "manual", encoding="utf-8"
    )

    monkeypatch.setattr(
        desktop.importlib,
        "import_module",
        lambda name: imported.append(name),
    )
    monkeypatch.setattr(desktop, "bundle_root", lambda: tmp_path)
    monkeypatch.setattr(
        desktop,
        "bundled_binary",
        lambda name: str(tmp_path / "bin" / name),
    )

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(desktop.subprocess, "run", run)

    desktop.packaged_self_test()

    assert "faster_whisper" in imported
    assert "pyannote.audio" in imported
    assert "webview" in imported
    assert commands == [
        [str(tmp_path / "bin" / "ffmpeg"), "-version"],
        [str(tmp_path / "bin" / "ffprobe"), "-version"],
    ]


def test_packaged_self_test_is_selected_before_desktop_main():
    source = Path(desktop.__file__).read_text(encoding="utf-8")
    main_guard = source.index('if __name__ == "__main__":')
    self_test = source.index('if "--package-self-test" in sys.argv:', main_guard)
    desktop_main = source.index("main()", self_test)

    assert self_test < desktop_main
