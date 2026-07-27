import os
import shutil
import sys
from pathlib import Path


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def bundled_binary(name: str, root: Path | None = None) -> str:
    suffix = ".exe" if sys.platform == "win32" else ""
    packaged = (root or bundle_root()) / "bin" / f"{name}{suffix}"
    if packaged.is_file():
        return str(packaged)
    return shutil.which(name) or name


def user_data_root() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Subtitle Studio"


def model_cache_root() -> Path:
    return user_data_root() / "models"
