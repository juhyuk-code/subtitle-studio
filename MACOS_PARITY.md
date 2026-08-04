# macOS parity checklist

This checklist defines what must pass before a macOS build is treated as a
complete Subtitle Studio release. The Apple Silicon and Intel jobs run the same
application source and backend tests as Windows; this document records the
platform-specific evidence still required from the packaged app.

## Native app and persistence

- The app opens as `Subtitle Studio.app` without Terminal, Node, Python, or
  Homebrew on the user's computer.
- The bundle contains the web interface, local API, FFmpeg, FFprobe, Whisper,
  Pyannote, Torch, WebView, Pretendard, the Korean quick start, and the full
  manual.
- Projects, API settings, model choices, voice profiles, style presets, app
  font scale, sidebar width, active project, and workspace state persist under
  `~/Library/Application Support/Subtitle Studio`.
- The default output folder is `~/Movies/Subtitle Studio Exports`; a user can
  choose any other folder through the native macOS folder picker.
- Restart and staged update/restart close and reopen the app without Terminal.
  Staged builds live in Application Support, so the workflow also works when
  the installed app is in `/Applications`.

## Editing workflow

- Source media can be imported or replaced while preserving project settings.
- Timestamp rows navigate the full source without creating tabs.
- The arrow creates an independent clip tab; tabs can be selected and closed.
- Each clip has independent frame-level IN/OUT boundaries, waveform zoom and
  pan, draggable playhead, boundary snapping, transcript, captions, style,
  post copy, and render-queue state.
- The real waveform remains stable during zooming and supports pointer seeking.
- Space, Control-Space, J, K, L, arrows, Shift-arrows, Option-wheel, and
  Control-wheel retain the documented behavior in the WebView.

## Speech and language

- The one-click English workflow runs speaker analysis, Whisper `large-v3`,
  both Korean correction passes, and English translation in order.
- Jobs expose ETA, pause, resume, and stop controls and only process the active
  clip boundaries.
- Recurring-host voice profiles and expected speaker count are applied during
  speaker analysis; speaker assignments remain editable afterward.
- OpenRouter models are browsable by provider with newest/free filters, and
  correction, translation, and per-clip post-copy models are independently
  selectable.
- Transcript rows auto-scroll with playback and remain individually editable.

## Captions and export

- Caption regeneration retimes cues from transcript words using the selected
  words-per-line and caption-line limits.
- Preview and rendered output use the same fixed subtitle size, Pretendard
  default, spacing, alignment, margins, background, outline, and clip-specific
  style or saved preset.
- The render queue exports selected clips independently, can be cleared, and
  removes successful items after export.
- Video, SRT, and styled ASS can be selected independently, including subtitle
  files without video.
- 1080p maximum-quality export is the default. GPU mode probes Apple
  VideoToolbox and retries the complete export with CPU libx264 if hardware
  encoding is unavailable or fails.
- Files are written directly into the configured folder and completion is
  reported in the app.

## Release gates

- Backend, frontend, and production builds pass.
- The frozen dependency self-test imports the local ML and desktop runtimes and
  starts the bundled FFmpeg and FFprobe executables.
- FFmpeg exposes the `subtitles` filter and `h264_videotoolbox` encoder.
- The main executable architecture matches the installer (`arm64` or
  `x86_64`).
- Bundle metadata, resources, Pretendard, manuals, and code signatures validate.
- The native app remains alive after a launch smoke test.
- Developer ID builds pass Apple notarization and Gatekeeper assessment for
  both the `.app` and `.dmg`.
- ZIP and DMG installers include SHA-256 checksum files.
