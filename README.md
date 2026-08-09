# Subtitle Studio

## Download for Apple Silicon Mac

### [Download Subtitle Studio DMG](https://github.com/juhyuk-code/subtitle-studio/releases/latest/download/Subtitle-Studio-macOS-arm64.dmg)

**[한국어: 첫 영상 클립 만들기](QUICK_START_KO.md)**

[전체 사용자 매뉴얼](USER_MANUAL.md)

Open the DMG and drag **Subtitle Studio** onto **Applications**. If macOS blocks
the first launch, Control-click the app in Applications, choose **Open**, then
confirm **Open** once.

Subtitle Studio is a native Windows and macOS workflow for turning Korean
podcast footage into reviewed Korean transcripts, natural English captions,
social post copy, subtitle files, and finished captioned clips.

`media -> speaker detection -> Whisper large-v3 -> Korean correction -> English translation -> captions -> export`

The DMG is published through the repository's latest GitHub Release.

## Desktop apps

The packaged app includes the frontend, local API, Python runtime, FFmpeg,
FFprobe, Faster Whisper, speaker analysis, Pretendard, and both manuals. It does
not require Node, Python, Homebrew, a terminal, or a separate web server on the
user's computer.

- Apple Silicon Mac: `Subtitle-Studio-macOS-arm64.dmg`

Open the DMG and drag **Subtitle Studio** into **Applications**.

The first transcription downloads Whisper `large-v3` and caches it under the
current user's application-data folder. Speaker detection similarly downloads
Pyannote Community-1 after its model terms are accepted and a Hugging Face token
is added in Settings. Media preparation, transcription, speaker analysis,
waveforms, voice profiles, caption rendering, and video export stay local. Only
transcript text is sent to OpenRouter for correction, translation, and post-copy
generation.

Video export defaults to a 1920x1080 canvas, maximum quality, and GPU encoding.
macOS uses Apple VideoToolbox; Windows probes NVIDIA NVENC, Intel Quick Sync,
and AMD AMF. Both platforms retry the full export with CPU libx264 when hardware
encoding is unavailable or fails.

Projects and settings are stored outside the installed app, so replacing or
updating the app preserves work. The default export folder is
`~/Movies/Subtitle Studio Exports` on macOS and
`~/Videos/Subtitle Studio Exports` on Windows unless the user chooses another
folder in Settings.

## Build locally

Desktop apps must be built on their target operating system.

```sh
npm install
python3.11 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev,desktop]'
npm run check
npm run desktop:build
```

Set `SUBTITLE_STUDIO_FFMPEG` and `SUBTITLE_STUDIO_FFPROBE` to portable binaries
before packaging. A macOS FFmpeg build must include the `subtitles` filter and
`h264_videotoolbox` encoder. Local outputs are:

- macOS: `release/Subtitle Studio.app`
- Windows: `release/Subtitle Studio/Subtitle Studio.exe`

To build an update while the installed app is still running:

```sh
npm run desktop:update
```

The new build waits in the current user's Subtitle Studio application-data
folder. In the app, open **Settings** and use **Apply update & restart**. The
updater replaces only the app bundle or Windows program directory; projects,
models, API settings, voice profiles, style presets, export folders, font
scale, sidebar width, and workspaces remain untouched. This works even when the
Mac app lives in `/Applications` or the Windows app is moved elsewhere.

## Release workflow

Every successful build on `main` refreshes the permanent latest GitHub Release
with the Apple Silicon DMG. A version tag such as
`v0.1.0` creates a separate versioned Release:

```sh
git tag v0.1.0
git push origin v0.1.0
```

The workflow validates bundle metadata, FFmpeg features, embedded manuals, a
native launch smoke test, and SHA-256 checksums before a Release becomes
downloadable. When Apple credentials are configured, it additionally signs and
notarizes the app and DMG. See
[MACOS_PARITY.md](MACOS_PARITY.md) for the complete acceptance checklist.

Unsigned ad-hoc Mac artifacts can be used for internal testing. For normal
distribution through Gatekeeper, configure these repository secrets:

- `MACOS_CERTIFICATE_P12`: base64-encoded Developer ID Application certificate
- `MACOS_CERTIFICATE_PASSWORD`: certificate export password
- `MACOS_SIGNING_IDENTITY`: full Developer ID Application identity
- `APPLE_ID`: Apple developer account email
- `APPLE_TEAM_ID`: Apple developer team ID
- `APPLE_APP_PASSWORD`: app-specific password for notarization

When those secrets are present, the workflow signs with hardened runtime,
submits the app to Apple's notary service, staples the ticket, and validates it
before creating the installers.

## Development

```sh
npm install
python3.11 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev,desktop]'
npm run dev
```

Run all frontend, backend, and production-build checks with:

```sh
npm run check
```
