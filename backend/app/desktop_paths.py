import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def bundled_binary(name: str, root: Path | None = None) -> str:
    bin_root = (root or bundle_root()) / "bin"
    candidates = [bin_root / name]
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        candidates.insert(0, bin_root / f"{name}.exe")
    for packaged in candidates:
        if packaged.is_file():
            return str(packaged)
    return shutil.which(name) or name


def hidden_subprocess_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


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


def user_video_exports_root() -> Path:
    configured = os.environ.get("SUBTITLE_STUDIO_EXPORTS")
    if configured:
        return Path(configured).expanduser()
    media_folder = "Movies" if sys.platform == "darwin" else "Videos"
    return Path.home() / media_folder / "Subtitle Studio Exports"


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
