import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import main, services
from backend.app.main import create_app
from backend.app.models import Job, OpenRouterModel, Project, ProjectCreate, Segment
from backend.app.services import (
    _dialogue_batches,
    _normalize_openrouter_models,
    call_openrouter,
    openrouter_headers,
    openrouter_model_for_stage,
    run_language_stage,
)
from backend.app.store import Store


def test_openrouter_headers_keep_authentication_on_the_backend(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-secret")

    headers = openrouter_headers()

    assert headers == {
        "Authorization": "Bearer sk-or-test-secret",
        "HTTP-Referer": "http://localhost:3000",
        "X-OpenRouter-Title": "Subtitle Studio",
    }


def test_openrouter_can_use_separate_correction_and_translation_models(monkeypatch):
    monkeypatch.setenv("OPENROUTER_CORRECTION_MODEL", "google/gemini-3.1-pro-preview")
    monkeypatch.setenv("OPENROUTER_TRANSLATION_MODEL", "anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("OPENROUTER_POST_COPY_MODEL", "openai/gpt-5.4")

    assert (
        openrouter_model_for_stage("correcting_pass_1")
        == "google/gemini-3.1-pro-preview"
    )
    assert (
        openrouter_model_for_stage("translating")
        == "anthropic/claude-sonnet-4.6"
    )
    assert openrouter_model_for_stage("post_captioning") == "openai/gpt-5.4"


def test_post_copy_model_defaults_to_the_translation_model(monkeypatch):
    monkeypatch.delenv("OPENROUTER_POST_COPY_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_TRANSLATION_MODEL", "anthropic/claude-sonnet-4.6")

    assert (
        openrouter_model_for_stage("post_captioning")
        == "anthropic/claude-sonnet-4.6"
    )


def test_openrouter_uses_stage_specific_default_models(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_CORRECTION_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_TRANSLATION_MODEL", raising=False)

    assert (
        openrouter_model_for_stage("correcting_pass_1")
        == "google/gemini-3.1-flash-lite"
    )
    assert (
        openrouter_model_for_stage("translating")
        == "anthropic/claude-sonnet-4.6"
    )


def test_openrouter_catalog_is_normalized_and_sorted_by_release_date():
    models = _normalize_openrouter_models(
        {
            "data": [
                {
                    "id": "anthropic/older",
                    "name": "Older",
                    "created": 100,
                    "context_length": 100_000,
                    "architecture": {"output_modalities": ["text"]},
                    "pricing": {"prompt": "0.1", "completion": "0.2"},
                },
                {
                    "id": "google/newer",
                    "name": "Newer",
                    "created": 200,
                    "context_length": 1_000_000,
                    "architecture": {"output_modalities": ["text"]},
                    "pricing": {"prompt": "0.3", "completion": "0.4"},
                },
                {
                    "id": "image/generator",
                    "name": "Image only",
                    "created": 300,
                    "architecture": {"output_modalities": ["image"]},
                },
            ]
        }
    )

    assert [model.model_id for model in models] == [
        "google/newer",
        "anthropic/older",
    ]
    assert models[0].provider == "google"
    assert models[0].context_length == 1_000_000


def test_openrouter_catalog_endpoint_keeps_the_key_on_the_backend(
    tmp_path, monkeypatch
):
    async def fake_catalog(store):
        assert store is not None
        return [
            OpenRouterModel(
                model_id="openai/latest",
                name="Latest",
                provider="openai",
                created=200,
                context_length=128_000,
                prompt_price="0.1",
                completion_price="0.2",
                request_price="0",
            )
        ]

    monkeypatch.setattr(main, "fetch_openrouter_models", fake_catalog)
    response = TestClient(create_app(tmp_path)).get("/api/openrouter/models")

    assert response.status_code == 200
    assert response.json()[0]["model_id"] == "openai/latest"
    assert "api_key" not in response.text


def test_openrouter_settings_save_a_dedicated_post_copy_model(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)

    response = client.put(
        "/api/settings/openrouter",
        json={
            "correction_model": "google/gemini-3.1-pro-preview",
            "translation_model": "anthropic/claude-sonnet-4.6",
            "post_copy_model": "openai/gpt-5.4",
        },
    )

    assert response.status_code == 200
    assert response.json()["post_copy_model"] == "openai/gpt-5.4"
    assert (
        app.state.store.get_setting("OPENROUTER_POST_COPY_MODEL")
        == "openai/gpt-5.4"
    )


def test_openrouter_payment_error_explains_the_available_actions(
    tmp_path, monkeypatch
):
    class PaymentRequiredClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(402, request=request)

    store = Store(tmp_path)
    store.save_setting("OPENROUTER_API_KEY", "sk-or-test-secret")
    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda **kwargs: PaymentRequiredClient(),
    )

    with pytest.raises(
        RuntimeError,
        match="Add credits or choose a free model",
    ):
        asyncio.run(
            call_openrouter(
                store,
                "correcting_pass_1",
                "Correct the transcript.",
                {"segments": []},
            )
        )


def test_dialogue_batches_bound_long_episodes():
    batches = _dialogue_batches(
        [
            {
                "segment_id": f"seg_{index}",
                "start_ms": index * 50_000,
                "end_ms": index * 50_000 + 2_000,
                "raw_korean": "대화",
            }
            for index in range(5)
        ]
    )

    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_language_stage_processes_windows_and_retries_omissions(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    project = Project.create(ProjectCreate(name="Long episode")).model_copy(
        update={"status": "transcribed"}
    )
    store.save_project(project)
    for index, start_ms in enumerate((0, 100_000, 200_000), start=1):
        store.save_segment(
            project.project_id,
            Segment(
                segment_id=f"seg_{index}",
                start_ms=start_ms,
                end_ms=start_ms + 1_000,
                raw_korean=f"원문 {index}",
            ),
        )
    job = Job(
        job_id="job_language",
        project_id=project.project_id,
        stage="queued",
    )
    store.save_job(job)
    calls = []

    async def fake_openrouter(_store, stage, _prompt, payload):
        calls.append(payload)
        required = payload["required_segment_ids"]
        if required == ["seg_2"] and len(
            [call for call in calls if call["required_segment_ids"] == required]
        ) == 1:
            return {"corrected_segments": []}
        return {
            "corrected_segments": [
                {
                    "segment_id": segment_id,
                    "corrected_korean": f"교정 {segment_id}",
                }
                for segment_id in required
            ]
        }

    monkeypatch.setattr(services, "call_openrouter", fake_openrouter)

    asyncio.run(
        run_language_stage(
            store,
            project.project_id,
            job.job_id,
            "correcting_pass_1",
        )
    )

    completed = Job.model_validate(store.get("job", job.job_id))
    assert completed.stage == "corrected_pass_1"
    assert len(calls) == 4
    assert any("repair_instruction" in call for call in calls)
    assert [
        item["pass_1_korean"]
        for item in store.list("segment", project.project_id)
    ] == ["교정 seg_1", "교정 seg_2", "교정 seg_3"]
