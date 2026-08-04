import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app import services
from backend.app.main import create_app
from backend.app.models import (
    Job,
    Project,
    ProjectCreate,
    Segment,
    TimestampClip,
    VideoExportOutput,
)
from backend.app.services import (
    available_gpu_video_encoder,
    generate_caption_track,
    run_video_export,
    video_encoder_args,
    video_export_filter,
)
from backend.app.store import Store


def prepared_video_project(app, client):
    data = client.post(
        "/api/projects", json={"name": "Video export"}
    ).json()
    project = Project.model_validate(data).model_copy(
        update={
            "media_name": "episode.mp4",
            "media_url": f"/media/{data['project_id']}/episode.mp4",
            "duration_ms": 2_000,
            "status": "translated",
        }
    )
    app.state.store.save_project(project)
    project_dir = app.state.store.media_root / project.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "episode.mp4").write_bytes(b"video")
    app.state.store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_1",
            start_ms=0,
            end_ms=2_000,
            raw_korean="원문",
            english="one two three four",
        ),
    )
    response = client.post(
        f"/api/projects/{project.project_id}/captions/regenerate",
        json={
            "language": "en",
            "max_words_per_line": 8,
            "max_lines": 1,
        },
    )
    assert response.status_code == 200
    return project


def test_video_export_defaults_to_1080p_maximum_quality(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = prepared_video_project(app, client)
    calls = []
    monkeypatch.setattr(
        main,
        "run_video_export",
        lambda *args: calls.append(args),
    )

    response = client.post(
        f"/api/projects/{project.project_id}/export/video",
        json={},
    )

    assert response.status_code == 202
    assert response.json()["stage"] == "exporting_video"
    assert calls[0][-7:] == (
        "1080p",
        "maximum",
        "gpu",
        [],
        True,
        False,
        False,
    )


def test_video_export_passes_selected_clip_ids(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = prepared_video_project(app, client)
    app.state.store.save_clip(
        project.project_id,
        TimestampClip(
            clip_id="clip_001",
            start_ms=0,
            end_ms=1_000,
            title="Opening",
            render_queued=True,
        ),
    )
    regenerated = client.post(
        f"/api/projects/{project.project_id}/captions/regenerate",
        json={
            "language": "en",
            "max_words_per_line": 8,
            "max_lines": 1,
        },
    )
    assert regenerated.status_code == 200
    calls = []
    monkeypatch.setattr(
        main,
        "run_video_export",
        lambda *args: calls.append(args),
    )

    response = client.post(
        f"/api/projects/{project.project_id}/export/video",
        json={"clip_ids": ["clip_001"]},
    )

    assert response.status_code == 202
    assert calls[0][-4] == ["clip_001"]


def test_video_export_passes_subtitle_only_formats(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = prepared_video_project(app, client)
    app.state.store.save_clip(
        project.project_id,
        TimestampClip(
            clip_id="clip_001",
            start_ms=0,
            end_ms=1_000,
            title="Opening",
            render_queued=True,
        ),
    )
    regenerated = client.post(
        f"/api/projects/{project.project_id}/captions/regenerate",
        json={"language": "en", "max_words_per_line": 8, "max_lines": 1},
    )
    assert regenerated.status_code == 200
    calls = []
    monkeypatch.setattr(
        main,
        "run_video_export",
        lambda *args: calls.append(args),
    )

    response = client.post(
        f"/api/projects/{project.project_id}/export/video",
        json={
            "clip_ids": ["clip_001"],
            "include_video": False,
            "include_srt": True,
            "include_ass": True,
        },
    )

    assert response.status_code == 202
    assert calls[0][-4:] == (["clip_001"], False, True, True)


def test_video_export_rejects_no_selected_format(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = prepared_video_project(app, client)

    response = client.post(
        f"/api/projects/{project.project_id}/export/video",
        json={
            "include_video": False,
            "include_srt": False,
            "include_ass": False,
        },
    )

    assert response.status_code == 409
    assert "export format" in response.json()["detail"]


def test_video_export_rejects_unknown_clip_ids(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = prepared_video_project(app, client)
    monkeypatch.setattr(main, "run_video_export", lambda *args: None)

    response = client.post(
        f"/api/projects/{project.project_id}/export/video",
        json={"clip_ids": ["clip_missing"]},
    )

    assert response.status_code == 404
    assert "segments were not found" in response.json()["detail"]


def test_video_export_folder_can_be_changed_persisted_and_reset(tmp_path):
    data_root = tmp_path / "data"
    default_root = tmp_path / "Videos" / "Subtitle Studio Exports"
    selected_root = tmp_path / "Selected exports"
    app = create_app(
        data_root,
        video_export_root=default_root,
    )
    client = TestClient(app)

    initial = client.get("/api/settings/video-export-folder")
    changed = client.put(
        "/api/settings/video-export-folder",
        json={"path": str(selected_root)},
    )
    restarted = TestClient(
        create_app(data_root, video_export_root=default_root)
    )
    persisted = restarted.get("/api/settings/video-export-folder")
    reset = restarted.put(
        "/api/settings/video-export-folder",
        json={"path": None},
    )

    assert initial.json() == {
        "path": str(default_root.resolve()),
        "default_path": str(default_root.resolve()),
        "is_default": True,
    }
    assert changed.status_code == 200
    assert changed.json()["path"] == str(selected_root.resolve())
    assert changed.json()["is_default"] is False
    assert selected_root.is_dir()
    assert persisted.json()["path"] == str(selected_root.resolve())
    assert reset.json()["path"] == str(default_root.resolve())
    assert reset.json()["is_default"] is True


def test_video_export_folder_rejects_relative_path(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.put(
        "/api/settings/video-export-folder",
        json={"path": "relative/exports"},
    )

    assert response.status_code == 422
    assert "absolute export folder" in response.json()["detail"]


def test_video_export_requires_current_generated_captions(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project = prepared_video_project(app, client)
    monkeypatch.setattr(main, "run_video_export", lambda *args: None)
    client.patch(
        f"/api/projects/{project.project_id}/subtitle-style",
        json={"max_words_per_line": 12},
    )

    response = client.post(
        f"/api/projects/{project.project_id}/export/video",
        json={},
    )

    assert response.status_code == 409
    assert "Regenerate captions" in response.json()["detail"]


def test_1080p_filter_preserves_aspect_ratio_and_canvas_size():
    value = video_export_filter("1080p")

    assert "scale=1920:1080" in value
    assert "force_original_aspect_ratio=decrease" in value
    assert "pad=1920:1080" in value
    assert "subtitles=chunk.ass" in value


def test_source_resolution_filter_does_not_resize_video():
    value = video_export_filter("source")

    assert "scale=" not in value
    assert value.startswith("subtitles=chunk.ass")


def test_nvenc_maximum_quality_uses_high_quality_constant_quality_mode():
    args = video_encoder_args("h264_nvenc", "maximum")

    assert args[:2] == ["-c:v", "h264_nvenc"]
    assert ["-preset", "p7"] == args[2:4]
    assert "-cq" in args
    assert args[args.index("-cq") + 1] == "16"


def test_gpu_detection_falls_through_to_next_available_encoder(
    monkeypatch,
):
    calls = []

    def probe(command, **kwargs):
        calls.append(command[command.index("-c:v") + 1])
        if calls[-1] == "h264_nvenc":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(services.sys, "platform", "win32")
    monkeypatch.setattr(services.subprocess, "run", probe)
    monkeypatch.setattr(services, "hidden_subprocess_kwargs", lambda: {})

    assert available_gpu_video_encoder("ffmpeg") == (
        "h264_qsv",
        "Intel Quick Sync",
    )
    assert calls == ["h264_nvenc", "h264_qsv"]


def test_macos_gpu_detection_uses_apple_videotoolbox(monkeypatch):
    calls = []

    def probe(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(services.sys, "platform", "darwin")
    monkeypatch.setattr(services.subprocess, "run", probe)

    assert available_gpu_video_encoder("ffmpeg") == (
        "h264_videotoolbox",
        "Apple VideoToolbox",
    )
    assert calls[0][calls[0].index("-c:v") + 1] == "h264_videotoolbox"


def test_videotoolbox_maximum_quality_uses_a_high_bitrate():
    args = video_encoder_args("h264_videotoolbox", "maximum")

    assert args[:2] == ["-c:v", "h264_videotoolbox"]
    assert args[args.index("-b:v") + 1] == "20M"
    assert args[args.index("-profile:v") + 1] == "high"


def test_gpu_export_retries_the_complete_export_on_cpu(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Fallback")).model_copy(
        update={
            "media_name": "episode.mp4",
            "duration_ms": 1_000,
            "status": "translated",
        }
    )
    store.save_project(project)
    project_dir = store.media_root / project.project_id
    project_dir.mkdir(parents=True)
    (project_dir / "episode.mp4").write_bytes(b"video")
    segment = Segment(
        segment_id="seg_1",
        start_ms=0,
        end_ms=1_000,
        raw_korean="source",
        english="Caption",
    )
    store.save_segment(project.project_id, segment)
    store.save_caption_track(
        project.project_id,
        generate_caption_track([segment.model_dump()], "en", 8, 1),
    )
    job = Job(
        job_id="job_gpu_fallback",
        project_id=project.project_id,
        stage="exporting_video",
    )
    store.save_job(job)
    codecs = []

    def fake_encode(
        _store,
        _job,
        command,
        work_dir,
        *_progress,
    ):
        codec = command[command.index("-c:v") + 1]
        codecs.append(codec)
        if codec == "h264_nvenc":
            raise RuntimeError("GPU initialization failed")
        (work_dir / command[-1]).write_bytes(b"part")

    def fake_concat(command, **_kwargs):
        Path(command[-1]).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(services, "bundled_binary", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        services,
        "available_gpu_video_encoder",
        lambda _executable: ("h264_nvenc", "NVIDIA NVENC"),
    )
    monkeypatch.setattr(services, "_run_video_chunk", fake_encode)
    monkeypatch.setattr(services.subprocess, "run", fake_concat)

    run_video_export(
        store,
        project.project_id,
        job.job_id,
        encoder="gpu",
    )

    completed = Job.model_validate(store.get("job", job.job_id))
    assert codecs == ["h264_nvenc", "libx264"]
    assert completed.stage == "video_exported"
    assert completed.encoder_name == "CPU (libx264 fallback)"


def test_video_export_creates_one_file_per_selected_clip(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Episode")).model_copy(
        update={
            "media_name": "episode.mp4",
            "media_url": "/media/episode.mp4",
            "duration_ms": 10_000,
            "status": "translated",
        }
    )
    store.save_project(project)
    project_dir = store.media_root / project.project_id
    project_dir.mkdir(parents=True)
    (project_dir / "episode.mp4").write_bytes(b"video")
    clips = [
        TimestampClip(
            clip_id="clip_001",
            start_ms=1_000,
            end_ms=3_000,
            title="First topic",
            render_queued=True,
        ),
        TimestampClip(
            clip_id="clip_002",
            start_ms=5_000,
            end_ms=8_000,
            title="Second topic",
            render_queued=True,
        ),
        TimestampClip(
            clip_id="clip_003",
            start_ms=8_000,
            end_ms=10_000,
            title="Keep queued",
            render_queued=True,
        ),
    ]
    for clip in clips:
        store.save_clip(project.project_id, clip)
    segments = [
        Segment(
            segment_id="seg_1",
            clip_id="clip_001",
            start_ms=1_000,
            end_ms=3_000,
            raw_korean="one",
            english="First caption",
        ),
        Segment(
            segment_id="seg_2",
            clip_id="clip_002",
            start_ms=5_000,
            end_ms=8_000,
            raw_korean="two",
            english="Second caption",
        ),
    ]
    for segment in segments:
        store.save_segment(project.project_id, segment)
    store.save_caption_track(
        project.project_id,
        generate_caption_track(
            [segment.model_dump() for segment in segments],
            "en",
            8,
            1,
        ),
    )
    job = Job(
        job_id="job_segment_export",
        project_id=project.project_id,
        stage="exporting_video",
    )
    store.save_job(job)
    encode_calls = []

    def fake_encode(
        _store,
        _job,
        command,
        work_dir,
        completed_ms,
        chunk_duration_ms,
        total_duration_ms,
    ):
        encode_calls.append(
            {
                "seek": command[command.index("-ss") + 1],
                "duration": command[command.index("-t") + 1],
                "completed": completed_ms,
                "total": total_duration_ms,
                "captions": (work_dir / "chunk.ass").read_text(
                    encoding="utf-8"
                ),
            }
        )
        (work_dir / command[-1]).write_bytes(b"part")

    def fake_concat(command, **_kwargs):
        Path(command[-1]).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(services, "bundled_binary", lambda _name: "ffmpeg")
    monkeypatch.setattr(services, "_run_video_chunk", fake_encode)
    monkeypatch.setattr(services.subprocess, "run", fake_concat)

    run_video_export(
        store,
        project.project_id,
        job.job_id,
        resolution="1080p",
        quality="maximum",
        encoder="cpu",
        clip_ids=["clip_002", "clip_001"],
    )

    completed = Job.model_validate(store.get("job", job.job_id))
    assert completed.stage == "video_exported", completed.error
    assert [output.clip_id for output in completed.outputs] == [
        "clip_001",
        "clip_002",
    ]
    assert [output.title for output in completed.outputs] == [
        "First topic",
        "Second topic",
    ]
    export_dir = store.video_export_dir(project.project_id, project.name)
    assert export_dir == store.video_export_root
    assert completed.output_folder == str(export_dir.resolve())
    assert all(
        (export_dir / output.output_name).is_file()
        for output in completed.outputs
    )
    assert "First topic" in completed.outputs[0].output_name
    assert "Second topic" in completed.outputs[1].output_name
    assert [call["seek"] for call in encode_calls] == ["1.000", "5.000"]
    assert [call["duration"] for call in encode_calls] == [
        "2.000",
        "3.000",
    ]
    assert [call["completed"] for call in encode_calls] == [0, 2_000]
    assert all(call["total"] == 5_000 for call in encode_calls)
    assert "First caption" in encode_calls[0]["captions"]
    assert "Second caption" in encode_calls[1]["captions"]
    assert r"{\xbord20\ybord10}First caption" in encode_calls[0][
        "captions"
    ]
    saved_clips = {
        item["clip_id"]: item
        for item in store.list("clip", project.project_id)
    }
    assert saved_clips["clip_001"]["render_queued"] is False
    assert saved_clips["clip_002"]["render_queued"] is False
    assert saved_clips["clip_003"]["render_queued"] is True


def test_subtitle_only_export_creates_per_clip_files_without_encoding(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Episode")).model_copy(
        update={"duration_ms": 8_000, "status": "translated"}
    )
    store.save_project(project)
    clip = TimestampClip(
        clip_id="clip_001",
        start_ms=5_000,
        end_ms=8_000,
        title="Selected moment",
        render_queued=True,
    )
    store.save_clip(project.project_id, clip)
    segment = Segment(
        segment_id="seg_1",
        clip_id=clip.clip_id,
        start_ms=5_250,
        end_ms=7_500,
        raw_korean="원문",
        english="Selected caption",
    )
    store.save_segment(project.project_id, segment)
    store.save_caption_track(
        project.project_id,
        generate_caption_track([segment.model_dump()], "en", 8, 1),
    )
    job = Job(
        job_id="job_subtitle_only",
        project_id=project.project_id,
        stage="exporting_video",
    )
    store.save_job(job)
    monkeypatch.setattr(
        services,
        "available_gpu_video_encoder",
        lambda *_args: pytest.fail("Subtitle-only export invoked encoder detection"),
    )
    monkeypatch.setattr(
        services,
        "_run_video_chunk",
        lambda *_args: pytest.fail("Subtitle-only export invoked FFmpeg"),
    )

    run_video_export(
        store,
        project.project_id,
        job.job_id,
        clip_ids=[clip.clip_id],
        include_video=False,
        include_srt=True,
        include_ass=True,
    )

    completed = Job.model_validate(store.get("job", job.job_id))
    assert completed.stage == "video_exported", completed.error
    assert completed.encoder_name == "Subtitle files only"
    assert [output.kind for output in completed.outputs] == ["srt", "ass"]
    assert all(output.clip_id == clip.clip_id for output in completed.outputs)
    export_dir = Path(completed.output_folder)
    srt_path = export_dir / completed.outputs[0].output_name
    ass_path = export_dir / completed.outputs[1].output_name
    assert srt_path.is_file()
    assert ass_path.is_file()
    assert "00:00:00,250 --> 00:00:02,500" in srt_path.read_text(
        encoding="utf-8"
    )
    assert "Selected caption" in ass_path.read_text(encoding="utf-8")
    assert not list(export_dir.glob("*.mp4"))
    saved_clip = store.get("clip", clip.clip_id)
    assert saved_clip["render_queued"] is False


def test_video_exports_use_selected_folder_directly_and_can_open_it(
    tmp_path, monkeypatch
):
    export_root = tmp_path / "Videos" / "Subtitle Studio Exports"
    app = create_app(
        tmp_path / "data",
        video_export_root=export_root,
    )
    client = TestClient(app)
    project = prepared_video_project(app, client)
    export_dir = app.state.store.video_export_dir(
        project.project_id, project.name
    )
    assert export_dir == export_root
    export_dir.mkdir(parents=True)
    output_name = "01-Opening-1080p-abc123.mp4"
    (export_dir / output_name).write_bytes(b"captioned video")
    job = Job(
        job_id="job_dedicated_folder",
        project_id=project.project_id,
        stage="video_exported",
        output_name=output_name,
        output_url=(
            f"/api/projects/{project.project_id}/video-exports/"
            f"{output_name}"
        ),
        output_folder=str(export_dir.resolve()),
        outputs=[
            VideoExportOutput(
                clip_id="full_video",
                title="Opening",
                start_ms=0,
                end_ms=2_000,
                output_name=output_name,
                output_url=(
                    f"/api/projects/{project.project_id}/video-exports/"
                    f"{output_name}"
                ),
            )
        ],
    )
    app.state.store.save_job(job)
    opened = []
    monkeypatch.setattr(main, "open_folder", opened.append)
    changed_root = tmp_path / "Different export destination"
    changed = client.put(
        "/api/settings/video-export-folder",
        json={"path": str(changed_root)},
    )

    listed = client.get(
        f"/api/projects/{project.project_id}/video-exports"
    )
    downloaded = client.get(
        f"/api/projects/{project.project_id}/video-exports/{output_name}"
    )
    opened_response = client.post(
        f"/api/projects/{project.project_id}/video-exports/open-folder"
    )

    assert changed.status_code == 200
    assert listed.status_code == 200
    assert listed.json()[0]["output_folder"] == str(export_dir.resolve())
    assert downloaded.status_code == 200
    assert downloaded.content == b"captioned video"
    assert opened_response.status_code == 200
    assert opened_response.json()["path"] == str(export_dir.resolve())
    assert opened == [export_dir.resolve()]


@pytest.mark.skipif(
    not os.environ.get("SUBTITLE_STUDIO_TEST_FFMPEG"),
    reason="Real FFmpeg smoke test is opt-in",
)
def test_real_video_export_is_1080p_with_caption_burn_in(
    tmp_path, monkeypatch
):
    ffmpeg = os.environ["SUBTITLE_STUDIO_TEST_FFMPEG"]
    ffprobe = os.environ["SUBTITLE_STUDIO_TEST_FFPROBE"]
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        services,
        "bundled_binary",
        lambda name: ffmpeg if name == "ffmpeg" else ffprobe,
    )
    monkeypatch.setattr(services, "bundle_root", lambda: project_root)
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Smoke")).model_copy(
        update={
            "media_name": "source.mp4",
            "media_url": "/media/source.mp4",
            "duration_ms": 1_000,
            "status": "translated",
        }
    )
    store.save_project(project)
    project_dir = store.media_root / project.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    source = project_dir / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    segment_data = Segment(
        segment_id="seg_1",
        start_ms=0,
        end_ms=1_000,
        raw_korean="원문",
        english="Caption export smoke test",
    )
    store.save_segment(project.project_id, segment_data)
    store.save_caption_track(
        project.project_id,
        generate_caption_track(
            [segment_data.model_dump()], "en", 8, 1
        ),
    )
    job = Job(
        job_id="job_export",
        project_id=project.project_id,
        stage="exporting_video",
    )
    store.save_job(job)

    run_video_export(
        store,
        project.project_id,
        job.job_id,
        resolution="1080p",
        quality="high",
    )

    completed = Job.model_validate(store.get("job", job.job_id))
    assert completed.stage == "video_exported", completed.error
    assert completed.encoder_name
    expected_encoder = os.environ.get(
        "SUBTITLE_STUDIO_EXPECT_ENCODER"
    )
    if expected_encoder:
        assert completed.encoder_name == expected_encoder
    output = (
        store.video_export_dir(project.project_id, project.name)
        / completed.output_name
    )
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream == {
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
    }
