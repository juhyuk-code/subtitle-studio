import asyncio

import httpx

from backend.app import services
from backend.app.services import call_openrouter
from backend.app.store import Store


def test_openrouter_retries_invalid_json_once(tmp_path, monkeypatch):
    class JsonRetryClient:
        calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            self.calls.append(kwargs["json"])
            content = "{not valid json" if len(self.calls) == 1 else '{"ok": true}'
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": content}}]},
            )

    client = JsonRetryClient()
    store = Store(tmp_path)
    store.save_setting("OPENROUTER_API_KEY", "sk-or-test-secret")
    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda **kwargs: client,
    )

    result = asyncio.run(
        call_openrouter(store, "translating", "Translate", {"text": "test"})
    )

    assert result == {"ok": True}
    assert len(client.calls) == 2
    assert "valid JSON object" in client.calls[1]["messages"][-1]["content"]
