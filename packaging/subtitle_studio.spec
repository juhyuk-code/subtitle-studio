import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).parent
path_suffix = ".exe" if sys.platform == "win32" else ""
icon_suffix = ".icns" if sys.platform == "darwin" else ".ico"
icon_path = project_root / "packaging" / "generated" / f"SubtitleStudio{icon_suffix}"
codesign_identity = os.environ.get("SUBTITLE_STUDIO_CODESIGN_IDENTITY") or None
target_arch = os.environ.get("SUBTITLE_STUDIO_TARGET_ARCH") or None
entitlements_path = project_root / "packaging" / "macos-entitlements.plist"
app_version = os.environ.get("SUBTITLE_STUDIO_VERSION", "0.1.0")


def required_binary(name):
    configured = os.environ.get(f"SUBTITLE_STUDIO_{name.upper()}")
    source = configured or shutil.which(f"{name}{path_suffix}") or shutil.which(name)
    if not source:
        raise SystemExit(
            f"{name} was not found. Install FFmpeg or set SUBTITLE_STUDIO_{name.upper()}."
        )
    return (source, "bin")


datas = [
    (str(project_root / "dist"), "dist"),
    (str(project_root / "USER_MANUAL.md"), "docs"),
    (str(project_root / "QUICK_START_KO.md"), "docs"),
]
binaries = [required_binary("ffmpeg"), required_binary("ffprobe")]
hiddenimports = []

packages = [
    "av",
    "ctranslate2",
    "faster_whisper",
    "huggingface_hub",
    "pyannote.audio",
    "tokenizers",
    "webview",
]
if sys.platform == "win32":
    packages.append("nvidia.cublas")

for package in packages:
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
    excludes=["torchcodec"],
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
    icon=str(icon_path),
    target_arch=target_arch,
    codesign_identity=codesign_identity,
    entitlements_file=(
        str(entitlements_path)
        if sys.platform == "darwin" and codesign_identity
        else None
    ),
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
        icon=str(icon_path),
        bundle_identifier="com.subtitlestudio.desktop",
        info_plist={
            "CFBundleName": "Subtitle Studio",
            "CFBundleDisplayName": "Subtitle Studio",
            "CFBundleShortVersionString": app_version,
            "CFBundleVersion": app_version,
            "LSApplicationCategoryType": "public.app-category.video",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Copyright 2026 Subtitle Studio",
        },
    )
