from pathlib import Path

from backend.app.desktop_paths import bundled_binary


def test_bundled_binary_prefers_the_packaged_copy(tmp_path):
    executable = tmp_path / "bin" / "ffmpeg"
    executable.parent.mkdir()
    executable.touch()

    assert bundled_binary("ffmpeg", tmp_path) == str(executable)
