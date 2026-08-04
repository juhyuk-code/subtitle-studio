import ast
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app import desktop_paths
from backend.app.desktop_paths import bundled_binary, hidden_subprocess_kwargs


def test_bundled_binary_prefers_the_packaged_copy(tmp_path):
    executable = tmp_path / "bin" / "ffmpeg"
    executable.parent.mkdir()
    executable.touch()

    assert bundled_binary("ffmpeg", tmp_path) == str(executable)


def test_macos_default_exports_use_the_movies_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_paths.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_paths.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("SUBTITLE_STUDIO_EXPORTS", raising=False)

    assert desktop_paths.user_video_exports_root() == (
        tmp_path / "Movies" / "Subtitle Studio Exports"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process flags")
def test_hidden_subprocess_kwargs_prevent_console_windows():
    kwargs = hidden_subprocess_kwargs()

    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    startupinfo = kwargs["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == subprocess.SW_HIDE


def test_every_media_subprocess_uses_hidden_windows():
    services_path = Path(__file__).parents[1] / "app" / "services.py"
    tree = ast.parse(services_path.read_text(encoding="utf-8"))
    media_process_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "subprocess"
            and function.attr in {"run", "Popen"}
        ):
            continue
        media_process_calls.append(node)
        assert any(
            keyword.arg is None
            and isinstance(keyword.value, ast.Call)
            and isinstance(keyword.value.func, ast.Name)
            and keyword.value.func.id == "hidden_subprocess_kwargs"
            for keyword in node.keywords
        ), f"subprocess.{function.attr} at line {node.lineno} can flash a console"
    assert media_process_calls
