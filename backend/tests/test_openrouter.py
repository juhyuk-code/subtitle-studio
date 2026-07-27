from backend.app.services import openrouter_headers, openrouter_model_for_stage


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

    assert (
        openrouter_model_for_stage("correcting_pass_1")
        == "google/gemini-3.1-pro-preview"
    )
    assert (
        openrouter_model_for_stage("translating")
        == "anthropic/claude-sonnet-4.6"
    )
