import multiprocessing
import socket


def prepare_desktop_runtime() -> None:
    multiprocessing.freeze_support()


def acquire_instance_lock(port: int = 47831) -> socket.socket | None:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", port))
        lock.listen(1)
        return lock
    except OSError:
        lock.close()
        return None
