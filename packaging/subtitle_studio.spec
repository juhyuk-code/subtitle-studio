import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).parent
path_suffix = ".exe" if sys.platform == "win32" else ""


def required_binary(name):
    configured = os.environ.get(f"SUBTITLE_STUDIO_{name.upper()}")
    source = configured or shutil.which(f"{name}{path_suffix}") or shutil.which(name)
    if not source:
        raise SystemExit(
            f"{name} was not found. Install FFmpeg or set SUBTITLE_STUDIO_{name.upper()}."
        )
    return (source, "bin")


datas = [(str(project_root / "dist"), "dist")]
binaries = [required_binary("ffmpeg"), required_binary("ffprobe")]
hiddenimports = []

for package in (
    "av",
    "ctranslate2",
    "faster_whisper",
    "huggingface_hub",
    "tokenizers",
    "webview",
):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

analysis = Analysis(
    [str(project_root / "backend" / "desktop.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Subtitle Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collected = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Subtitle Studio",
)

if sys.platform == "darwin":
    application = BUNDLE(
        collected,
        name="Subtitle Studio.app",
        icon=None,
        bundle_identifier="com.subtitlestudio.desktop",
        info_plist={
            "CFBundleDisplayName": "Subtitle Studio",
            "NSHighResolutionCapable": True,
        },
    )
