import sys

from backend.app import services
from backend.app.services import whisper_command


def test_whisper_is_available_when_faster_whisper_is_bundled(monkeypatch):
    monkeypatch.setattr(
        services.importlib.util,
        "find_spec",
        lambda name: object() if name == "faster_whisper" else None,
    )
    monkeypatch.setattr(services.shutil, "which", lambda name: None)

    assert services.whisper_available() is True


def test_whisper_uses_the_active_python_environment_when_available(monkeypatch):
    monkeypatch.setattr(services.importlib.util, "find_spec", lambda name: object())

    assert whisper_command() == [sys.executable, "-m", "whisper"]
