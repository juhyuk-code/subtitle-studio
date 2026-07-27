import logging
import socket
import threading
import time

from backend.app.frozen_runtime import acquire_instance_lock, prepare_desktop_runtime

prepare_desktop_runtime()

import uvicorn
import webview

from backend.app.desktop_paths import bundle_root, user_data_root
from backend.app.main import create_app


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.bind(("127.0.0.1", 0))
        return int(connection.getsockname()[1])


def wait_until_ready(port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("The embedded server did not start.")


def main() -> None:
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        return
    data_root = user_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=data_root / "subtitle-studio.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    port = available_port()
    application = create_app(data_root, static_root=bundle_root() / "dist")
    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, name="subtitle-studio-api", daemon=True)
    thread.start()

    try:
        wait_until_ready(port)
        webview.create_window(
            "Subtitle Studio",
            f"http://127.0.0.1:{port}",
            width=1440,
            height=920,
            min_size=(1050, 700),
            confirm_close=True,
        )
        webview.start(private_mode=False)
    except Exception:
        logging.exception("Subtitle Studio failed to start")
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        instance_lock.close()


if __name__ == "__main__":
    main()
