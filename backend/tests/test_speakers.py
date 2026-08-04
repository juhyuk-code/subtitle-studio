from fastapi.testclient import TestClient

from backend.app import main, services
from backend.app.main import create_app
from backend.app.models import (
    DetectedSpeakerTurn,
    Job,
    Project,
    ProjectCreate,
    Segment,
    TimestampClip,
    VoiceProfileRecord,
    Word,
)
from backend.app.services import (
    COMMUNITY_DIARIZATION_MODEL,
    ClipAudioMapping,
    DiarizationResult,
    INTEL_MAC_DIARIZATION_MODEL,
    SpeakerTurn,
    align_segments_to_speakers,
    default_diarization_model,
    match_voice_profiles,
    remap_selected_clip_turns,
    run_diarization,
    run_transcription,
)
from backend.app.store import Store


def test_diarization_model_uses_intel_compatible_pipeline(monkeypatch):
    monkeypatch.delenv("DIARIZATION_MODEL", raising=False)
    assert default_diarization_model("darwin", "x86_64") == INTEL_MAC_DIARIZATION_MODEL
    assert default_diarization_model("darwin", "arm64") == COMMUNITY_DIARIZATION_MODEL


def test_diarization_model_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("DIARIZATION_MODEL", "example/custom-pipeline")
    assert default_diarization_model("darwin", "x86_64") == "example/custom-pipeline"


def test_word_timestamps_are_split_at_speaker_changes():
    segment = Segment(
        segment_id="seg_000001",
        start_ms=1_000,
        end_ms=5_000,
        clip_id="clip_001",
        raw_korean="first second third",
        words=[
            Word(text="first", start_ms=1_000, end_ms=1_800),
            Word(text="second", start_ms=2_000, end_ms=2_800),
            Word(text="third", start_ms=4_000, end_ms=4_800),
        ],
    )
    turns = [
        SpeakerTurn(900, 3_000, "SPEAKER_01"),
        SpeakerTurn(3_000, 5_000, "SPEAKER_02"),
    ]

    aligned = align_segments_to_speakers([segment], turns)

    assert [item.raw_korean for item in aligned] == [
        "first second",
        "third",
    ]
    assert [item.speaker_id for item in aligned] == [
        "SPEAKER_01",
        "SPEAKER_02",
    ]
    assert [item.start_ms for item in aligned] == [1_000, 4_000]
    assert all(item.clip_id == "clip_001" for item in aligned)


def test_nearest_speaker_is_used_for_a_word_in_a_short_gap():
    segment = Segment(
        segment_id="seg_000001",
        start_ms=2_050,
        end_ms=2_150,
        raw_korean="gap",
        words=[Word(text="gap", start_ms=2_050, end_ms=2_150)],
    )
    turns = [
        SpeakerTurn(1_000, 2_000, "SPEAKER_01"),
        SpeakerTurn(2_300, 3_000, "SPEAKER_02"),
    ]

    aligned = align_segments_to_speakers([segment], turns)

    assert aligned[0].speaker_id == "SPEAKER_01"


def test_diarization_endpoint_starts_before_transcription(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects",
        json={
            "name": "Roundtable",
            "expected_speaker_count": 3,
        },
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={"status": "media_ready", "media_name": "episode.mp4"}
    )
    app.state.store.save_project(project)
    app.state.store.save_setting("HUGGINGFACE_TOKEN", "hf_test")
    calls = []
    monkeypatch.setattr(main, "diarization_available", lambda: True)
    monkeypatch.setattr(
        main, "run_diarization", lambda *args: calls.append(args)
    )

    response = client.post(f"/api/projects/{project.project_id}/diarize")

    assert response.status_code == 202
    assert calls[0][1] == project.project_id
    assert calls[0][4] == 3


def test_enrolled_voice_diarization_endpoint_scopes_to_requested_clip(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Scoped voices"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={
            "status": "media_ready",
            "media_name": "episode.mp4",
            "duration_ms": 60_000,
        }
    )
    app.state.store.save_project(project)
    clip = TimestampClip(
        clip_id="clip_scoped",
        start_ms=10_000,
        end_ms=20_000,
        title="Scoped",
    )
    app.state.store.save_clip(project.project_id, clip)
    app.state.store.save_voice_profile(
        VoiceProfileRecord(
            profile_id="HOST_A",
            name="Alice",
            sample_name="alice.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[1.0, 0.0],
        )
    )
    app.state.store.save_setting("HUGGINGFACE_TOKEN", "hf_test")
    calls = []
    monkeypatch.setattr(main, "diarization_available", lambda: True)
    monkeypatch.setattr(
        main, "run_diarization", lambda *args: calls.append(args)
    )

    response = client.post(
        f"/api/projects/{project.project_id}/diarize",
        params={"clip_id": clip.clip_id},
    )

    assert response.status_code == 202
    assert [item.clip_id for item in calls[0][3]] == ["clip_scoped"]


def test_language_stage_requires_speaker_attributed_transcription(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    project_data = client.post(
        "/api/projects", json={"name": "Roundtable"}
    ).json()
    project = Project.model_validate(project_data).model_copy(
        update={"status": "speakers_detected"}
    )
    app.state.store.save_project(project)
    app.state.store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_000001",
            start_ms=0,
            end_ms=1_000,
            raw_korean="test",
        ),
    )
    app.state.store.save_setting("OPENROUTER_API_KEY", "sk-or-test")

    response = client.post(
        f"/api/projects/{project.project_id}/correct/pass-1"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Transcribe the speaker-attributed audio first"
    )


def test_huggingface_token_is_never_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "diarization_available", lambda: True)
    client = TestClient(create_app(tmp_path))

    saved = client.put(
        "/api/settings/speaker-detection",
        json={"huggingface_token": "hf_private"},
    )

    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert "huggingface_token" not in saved.json()


def test_voice_profile_upload_keeps_embedding_private(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.store.save_setting("HUGGINGFACE_TOKEN", "hf_test")
    client = TestClient(app)
    monkeypatch.setattr(main, "media_duration_ms", lambda path: 45_000)
    monkeypatch.setattr(
        main,
        "prepare_diarization_audio",
        lambda source, output: output.write_bytes(b"normalized"),
    )
    monkeypatch.setattr(
        main,
        "extract_voice_embedding",
        lambda path, token: [1.0, 0.0],
    )

    created = client.post(
        "/api/voice-profiles",
        data={"name": "Host One"},
        files={"sample": ("host.wav", b"voice", "audio/wav")},
    )
    listed = client.get("/api/voice-profiles")

    assert created.status_code == 201
    assert created.json()["name"] == "Host One"
    assert created.json()["duration_ms"] == 45_000
    assert "embedding" not in created.json()
    assert listed.json() == [created.json()]
    stored = app.state.store.list("voice_profile", "__app__")[0]
    assert stored["embedding"] == [1.0, 0.0]


def test_voice_profiles_match_clusters_one_to_one():
    profiles = [
        VoiceProfileRecord(
            profile_id="HOST_A",
            name="A",
            sample_name="a.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[1.0, 0.0],
        ),
        VoiceProfileRecord(
            profile_id="HOST_B",
            name="B",
            sample_name="b.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[0.0, 1.0],
        ),
    ]

    matches = match_voice_profiles(
        {
            "cluster_1": [0.95, 0.05],
            "cluster_2": [0.05, 0.95],
        },
        profiles,
    )

    assert matches == {
        "cluster_1": "HOST_A",
        "cluster_2": "HOST_B",
    }


def test_voice_profiles_choose_best_global_assignment():
    profiles = [
        VoiceProfileRecord(
            profile_id="HOST_A",
            name="A",
            sample_name="a.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[1.0, 0.0],
        ),
        VoiceProfileRecord(
            profile_id="HOST_B",
            name="B",
            sample_name="b.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[0.8, 0.6],
        ),
    ]

    matches = match_voice_profiles(
        {
            "cluster_1": [0.9, 0.44],
            "cluster_2": [1.0, 0.0],
        },
        profiles,
    )

    assert matches == {
        "cluster_1": "HOST_B",
        "cluster_2": "HOST_A",
    }


def test_selected_clip_turns_return_to_original_timeline():
    turns = [
        SpeakerTurn(500, 2_000, "cluster_1"),
        SpeakerTurn(2_700, 4_200, "cluster_2"),
    ]
    mappings = [
        ClipAudioMapping(0, 2_000, 10_000),
        ClipAudioMapping(2_750, 4_750, 30_000),
    ]

    remapped = remap_selected_clip_turns(turns, mappings)

    assert remapped == [
        SpeakerTurn(10_500, 12_000, "cluster_1"),
        SpeakerTurn(30_000, 31_450, "cluster_2"),
    ]


def test_diarization_persists_audio_turns_before_any_transcript(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(
        ProjectCreate(name="Roundtable", expected_speaker_count=3)
    ).model_copy(
        update={"media_name": "episode.wav", "status": "media_ready"}
    )
    store.save_project(project)
    project_dir = store.media_root / project.project_id
    project_dir.mkdir(exist_ok=True)
    (project_dir / "normalized.wav").write_bytes(b"normalized")
    diarization_audio = project_dir / "diarization.wav"
    diarization_audio.write_bytes(b"clean full episode")
    store.save_setting("HUGGINGFACE_TOKEN", "hf_test")
    job = Job(
        job_id="job_diarize",
        project_id=project.project_id,
        stage="queued",
    )
    store.save_job(job)
    calls = []

    def fake_pyannote(*args):
        calls.append(args)
        return [
            SpeakerTurn(0, 2_000, "voice_a"),
            SpeakerTurn(2_000, 4_000, "voice_b"),
        ]

    monkeypatch.setattr(services, "_run_pyannote", fake_pyannote)

    run_diarization(
        store,
        project.project_id,
        job.job_id,
        clips=[],
        expected_speaker_count=project.expected_speaker_count,
    )

    assert calls[0][:3] == (diarization_audio, "hf_test", 3)
    assert callable(calls[0][3])
    assert store.get("project", project.project_id)["status"] == (
        "speakers_detected"
    )
    assert [item["speaker_id"] for item in store.list(
        "speaker_turn", project.project_id
    )] == ["SPEAKER_01", "SPEAKER_02"]
    assert store.list("segment", project.project_id) == []


def test_enrolled_hosts_use_selected_clips_and_keep_host_names(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(
        ProjectCreate(name="Roundtable", expected_speaker_count=2)
    ).model_copy(
        update={"media_name": "episode.wav", "status": "media_ready"}
    )
    store.save_project(project)
    project_dir = store.media_root / project.project_id
    project_dir.mkdir(exist_ok=True)
    (project_dir / "diarization.wav").write_bytes(b"full episode")
    store.save_setting("HUGGINGFACE_TOKEN", "hf_test")
    store.save_voice_profile(
        VoiceProfileRecord(
            profile_id="HOST_A",
            name="Alice",
            sample_name="alice.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[1.0, 0.0],
        )
    )
    store.save_voice_profile(
        VoiceProfileRecord(
            profile_id="HOST_B",
            name="Bob",
            sample_name="bob.wav",
            duration_ms=30_000,
            created_at="2026-07-28T00:00:00+00:00",
            embedding=[0.0, 1.0],
        )
    )
    store.save_speaker_turn(
        project.project_id,
        DetectedSpeakerTurn(
            turn_id="turn_existing",
            start_ms=0,
            end_ms=5_000,
            speaker_id="HOST_A",
        ),
    )
    store.save_segment(
        project.project_id,
        Segment(
            segment_id="seg_existing",
            clip_id="clip_other",
            start_ms=1_000,
            end_ms=2_000,
            raw_korean="keep this",
        ),
    )
    job = Job(
        job_id="job_enrolled",
        project_id=project.project_id,
        stage="queued",
    )
    store.save_job(job)
    mappings = [ClipAudioMapping(0, 2_000, 10_000)]
    monkeypatch.setattr(
        services,
        "prepare_selected_diarization_audio",
        lambda *args: mappings,
    )
    monkeypatch.setattr(
        services,
        "_run_pyannote",
        lambda *args: DiarizationResult(
            turns=[
                SpeakerTurn(0, 1_000, "cluster_1"),
                SpeakerTurn(1_000, 2_000, "cluster_2"),
            ],
            embeddings={
                "cluster_1": [0.95, 0.05],
                "cluster_2": [0.05, 0.95],
            },
        ),
    )

    run_diarization(
        store,
        project.project_id,
        job.job_id,
        clips=[
            TimestampClip(
                clip_id="clip_001",
                start_ms=10_000,
                end_ms=12_000,
                title="Selected",
            )
        ],
        expected_speaker_count=2,
    )

    assert [
        (item["speaker_id"], item["name"])
        for item in store.list("speaker", project.project_id)
    ] == [("HOST_A", "Alice"), ("HOST_B", "Bob")]
    assert [
        (item["start_ms"], item["speaker_id"])
        for item in store.list("speaker_turn", project.project_id)
    ] == [
        (0, "HOST_A"),
        (10_000, "HOST_A"),
        (11_000, "HOST_B"),
    ]
    assert [
        item["segment_id"]
        for item in store.list("segment", project.project_id)
    ] == ["seg_existing"]


def test_transcription_aligns_words_to_previously_detected_turns(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Roundtable")).model_copy(
        update={
            "media_name": "episode.wav",
            "status": "speakers_detected",
            "duration_ms": 4_000,
        }
    )
    store.save_project(project)
    store.save_speaker_turn(
        project.project_id,
        DetectedSpeakerTurn(
            turn_id="turn_000001",
            start_ms=0,
            end_ms=2_000,
            speaker_id="SPEAKER_01",
        ),
    )
    store.save_speaker_turn(
        project.project_id,
        DetectedSpeakerTurn(
            turn_id="turn_000002",
            start_ms=2_000,
            end_ms=4_000,
            speaker_id="SPEAKER_02",
        ),
    )
    job = Job(
        job_id="job_transcribe",
        project_id=project.project_id,
        stage="queued",
    )
    store.save_job(job)
    monkeypatch.setattr(
        services,
        "_transcribe_with_faster_whisper",
        lambda *args: {
            "segments": [
                {
                    "start": 0,
                    "end": 4,
                    "text": "one two",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0,
                    "words": [
                        {
                            "word": "one",
                            "start": 0.5,
                            "end": 1.5,
                            "probability": 0.9,
                        },
                        {
                            "word": "two",
                            "start": 2.5,
                            "end": 3.5,
                            "probability": 0.9,
                        },
                    ],
                }
            ]
        },
    )

    run_transcription(
        store, project.project_id, job.job_id, "large-v3"
    )

    segments = store.list("segment", project.project_id)
    assert [item["raw_korean"] for item in segments] == ["one", "two"]
    assert [item["speaker_id"] for item in segments] == [
        "SPEAKER_01",
        "SPEAKER_02",
    ]
    assert store.get("project", project.project_id)["status"] == "transcribed"
