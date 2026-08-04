import sys
import wave
from pathlib import Path
from types import SimpleNamespace

from backend.app import services


def write_pcm_wave(path: Path, duration_seconds: int = 4) -> None:
    sample_rate = 16_000
    frames = b"".join(
        int(index % 32_000 - 16_000).to_bytes(
            2, byteorder="little", signed=True
        )
        for index in range(sample_rate * duration_seconds)
    )
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(frames)


def test_prepare_transcription_clip_copies_only_selected_audio(tmp_path):
    source = tmp_path / "normalized.wav"
    output = tmp_path / "clip.wav"
    write_pcm_wave(source)

    services.prepare_transcription_clip(source, output, 1_000, 2_500)

    with wave.open(str(output), "rb") as reader:
        assert reader.getframerate() == 16_000
        assert reader.getnchannels() == 1
        assert reader.getnframes() == 24_000


def test_preferred_whisper_backend_uses_cuda_when_available(monkeypatch):
    monkeypatch.delenv("SUBTITLE_STUDIO_WHISPER_DEVICE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: 1),
    )

    assert services._preferred_whisper_backend() == ("cuda", "float16")


def test_cuda_dll_directory_is_added_on_windows(monkeypatch, tmp_path):
    cublas = tmp_path / "nvidia" / "cublas" / "bin"
    cublas.mkdir(parents=True)
    (cublas / "cublas64_12.dll").write_bytes(b"dll")
    added = []
    fake_lib = SimpleNamespace(__file__=str(cublas / "__init__.py"))
    monkeypatch.setattr(services.sys, "platform", "win32")
    monkeypatch.setattr(services.sys, "prefix", str(tmp_path / "missing"))
    monkeypatch.setattr(
        services.os,
        "add_dll_directory",
        lambda value: added.append(value) or value,
    )
    monkeypatch.setitem(
        sys.modules,
        "nvidia",
        SimpleNamespace(cublas=SimpleNamespace(lib=fake_lib)),
    )
    monkeypatch.setitem(
        sys.modules,
        "nvidia.cublas",
        SimpleNamespace(lib=fake_lib),
    )
    monkeypatch.setitem(sys.modules, "nvidia.cublas.lib", fake_lib)
    services._CUDA_DLL_DIRECTORIES.clear()

    services._configure_cuda_dll_directories()

    assert str(cublas) in added
    assert str(cublas) in services.os.environ["PATH"].split(
        services.os.pathsep
    )


def test_whisper_model_is_reused(monkeypatch, tmp_path):
    created = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

    monkeypatch.setattr(services, "model_cache_root", lambda: tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel),
    )
    services._WHISPER_MODELS.clear()

    first = services._load_whisper_model("large-v3", "cuda", "float16")
    second = services._load_whisper_model("large-v3", "cuda", "float16")

    assert first is second
    assert len(created) == 1


def test_whisper_timestamps_are_restored_to_project_timeline():
    transcribe_options = {}

    class FakeModel:
        def transcribe(self, *_args, **kwargs):
            transcribe_options.update(kwargs)
            word = SimpleNamespace(
                word=" hello",
                start=0.25,
                end=0.75,
                probability=0.9,
            )
            segment = SimpleNamespace(
                start=0.0,
                end=1.0,
                text="hello",
                avg_logprob=-0.1,
                no_speech_prob=0.0,
                words=[word],
            )
            return iter([segment]), None

    progress = []
    payload = services._collect_whisper_segments(
        FakeModel(),
        Path("clip.wav"),
        "",
        progress.append,
        217.0,
    )

    segment = payload["segments"][0]
    assert segment["start"] == 217.0
    assert segment["end"] == 218.0
    assert segment["words"][0]["start"] == 217.25
    assert progress == [218.0]
    assert transcribe_options["vad_filter"] is True
    assert transcribe_options["vad_parameters"] == {
        "min_silence_duration_ms": 500
    }
