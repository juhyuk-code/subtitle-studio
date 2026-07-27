# Subtitle Studio

A locally hosted Korean podcast subtitle workflow with local media processing
and OpenRouter-powered transcript refinement:

`media → Korean ASR → local correction → episode consistency → conversational English → SRT/VTT`

## Standalone apps

Packaged releases include the frontend, API, Python runtime, Faster Whisper,
FFmpeg, and FFprobe. They open in a native window and do not require Node,
Python, Homebrew, Winget, a terminal, or a separately running web server.

- macOS: open `release/Subtitle Studio.app`
- Windows: open `release/Subtitle Studio/Subtitle Studio.exe`

The selected Whisper model downloads automatically on the first transcription
and is then cached locally. An internet connection and OpenRouter API key are
still required for transcript correction and translation. Media normalization
and Whisper transcription remain on the computer; only transcript text is sent
to OpenRouter.

Timestamp lists can be pasted into the **Clips** tab using `MM:SS Title` or
`HH:MM:SS Title`. Each clip ends at the following timestamp, and the final clip
ends at the media duration. Select any subset before transcription, correction,
or translation to process only those ranges.

### Build a release

Desktop apps must be built on their target operating system.

```sh
npm install
python3.11 -m venv .venv
./.venv/bin/pip install -e '.[desktop]'
npm run desktop:build
```

The repository also includes a manually triggered GitHub Actions workflow that
builds downloadable macOS and Windows artifacts.

## Development

Development still uses Node.js 20+, Python 3.11–3.13, and FFmpeg:

```sh
npm install
python3.11 -m venv .venv
./.venv/bin/pip install -e '.[dev,desktop]'
npm run dev
```

On first launch, paste an OpenRouter key into the connection screen. It is
stored in the local SQLite database and never returned to the frontend.

## Verification

```bash
npm run check
```

Packaged project data is stored in the operating system's application-data
folder. Raw ASR text is immutable; corrections and English remain separate.
