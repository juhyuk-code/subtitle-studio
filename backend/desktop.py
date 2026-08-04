import importlib
import logging
import os
import socket
import subprocess
import sys
import threading
import time

from backend.app.frozen_runtime import acquire_instance_lock, prepare_desktop_runtime

prepare_desktop_runtime()

import uvicorn
import webview

from backend.app.desktop_paths import (
    bundle_root,
    bundled_binary,
    hidden_subprocess_kwargs,
    user_data_root,
    user_video_exports_root,
)
from backend.app.desktop_update import launch_restart, update_status
from backend.app.main import create_app


class DesktopApi:
    @staticmethod
    def _close_app(exit_code: int) -> None:
        try:
            if webview.windows:
                window = webview.windows[0]
                window.confirm_close = False
                window.destroy()
        finally:
            time.sleep(5)
            os._exit(exit_code)

    def select_export_folder(self, current_path: str = "") -> str | None:
        window = webview.windows[0]
        selection = window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=current_path,
        )
        return selection[0] if selection else None

    def get_update_status(self) -> dict[str, object]:
        return update_status()

    def apply_update_and_restart(self) -> bool:
        launch_restart(apply_update=True, exit_process=self._close_app)
        return True

    def restart_app(self) -> bool:
        launch_restart(apply_update=False, exit_process=self._close_app)
        return True


def packaged_self_test() -> None:
    for module_name in (
        "av",
        "ctranslate2",
        "faster_whisper",
        "pyannote.audio",
        "torch",
        "webview",
    ):
        importlib.import_module(module_name)

    root = bundle_root()
    required_files = (
        root / "dist" / "index.html",
        root / "docs" / "QUICK_START_KO.md",
        root / "docs" / "USER_MANUAL.md",
    )
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise RuntimeError(f"Packaged resources are missing: {', '.join(missing)}")

    for binary_name in ("ffmpeg", "ffprobe"):
        result = subprocess.run(
            [bundled_binary(binary_name), "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Packaged {binary_name} could not start.")


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
    application = create_app(
        data_root,
        static_root=bundle_root() / "dist",
        video_export_root=user_video_exports_root(),
    )
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
            js_api=DesktopApi(),
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
    if "--package-self-test" in sys.argv:
        packaged_self_test()
    else:
        main()
