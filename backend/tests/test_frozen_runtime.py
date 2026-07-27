from backend.app import frozen_runtime


def test_preparing_desktop_runtime_enables_frozen_workers(monkeypatch):
    calls = []
    monkeypatch.setattr(
        frozen_runtime.multiprocessing,
        "freeze_support",
        lambda: calls.append("freeze_support"),
    )

    frozen_runtime.prepare_desktop_runtime()

    assert calls == ["freeze_support"]


def test_single_instance_lock_rejects_a_second_desktop_process():
    first = frozen_runtime.acquire_instance_lock(0)
    assert first is not None
    port = first.getsockname()[1]

    second = frozen_runtime.acquire_instance_lock(port)

    assert second is None
    first.close()
