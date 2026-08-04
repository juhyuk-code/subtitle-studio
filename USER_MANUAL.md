# Subtitle Studio User Manual

Subtitle Studio turns Korean podcast or video clips into reviewed Korean
transcripts, natural English captions, social post copy, subtitle files, and
finished captioned videos.

This manual is written for a first-time user. You do not need to know anything
about speech-recognition software or video-editing software before starting.

## The five-minute version

1. Open Subtitle Studio and create a project.
2. Open **Settings**, add your OpenRouter API key, and choose the correction,
   translation, and post-copy models.
3. If you want speaker names, add a Hugging Face token. For recurring hosts,
   enroll clean voice samples under **Settings > Voices**.
4. Import a video or audio file.
5. In **Clips**, paste one timestamp and title per line.
6. Click a timestamp to preview that moment. Click its arrow to create and open
   an editable clip tab.
7. Set the clip's exact **IN** and **OUT** boundaries on the waveform.
8. Open **Transcribe** and click **Create English transcript**.
9. Review and edit the transcript. Open **Style**, set the caption layout, and
   generate captions.
10. Add finished clips to the **Render queue**, choose the output formats, and
    export.

## Before you begin

### What runs locally

The video or audio file, audio preparation, Whisper transcription, waveform,
speaker analysis, voice samples, caption rendering, and video export stay on
this computer. Transcript text is sent to OpenRouter when the app performs
correction, translation, or post-copy generation.

### What requires the internet

- OpenRouter correction, translation, and post-copy generation.
- The first download of the Whisper `large-v3` model.
- The first download of the speaker-detection model.
- Initial setup of model access tokens.

Downloaded local models are cached, so later jobs do not download them again.

### Recommended first-time setup

Open the gear button in the upper-right corner.

- **OpenRouter API key:** Required for correction, translation, and post copy.
- **Correction model:** Cleans the Korean transcript while preserving meaning.
- **Translation model:** Produces the natural English transcript.
- **Post copy model:** Writes the social post for an individual clip.
- **Model browser:** Browse by provider, **Newest**, or **Free**. The date shown
  beside a model helps identify recent releases.
- **Hugging Face token:** Required only for speaker detection.
- **Voices:** Enroll recurring hosts for automatic name matching.
- **Video export folder:** All exported files are written directly here.
- **App font size:** Changes the interface text size, not subtitle text size.

The app remembers these settings, the sidebar width, the last project, active
clip, playhead, playback speed, timeline zoom, and other workspace choices.

## Core concepts

Understanding these terms makes the rest of the app much easier.

### Project

A project contains one source media file plus its timestamps, clip workspaces,
transcripts, caption styles, post copy, and render queue. App-wide settings such
as voice profiles, style presets, models, and the export folder can be reused
across projects.

### Navigation timestamp

A navigation timestamp is only a named jump point in the original video. It is
not yet an editable clip.

Click the timestamp or title in **Clips** to move the playhead there. This does
not open a tab. Click the arrow at the right side of the timestamp when you
want to turn that area into an editable clip workspace.

### Clip workspace and tab

A clip is an independent editing workspace with its own:

- IN and OUT boundaries
- transcript rows
- subtitle style
- generated captions
- post copy
- render-queue state

Opened clips appear as tabs above the transcript. A filled circle in the Clips
list means that timestamp already has an open clip tab. Close a tab with its
`X`; closing a tab does not delete the project or source media.

### IN and OUT boundaries

The IN boundary is the first frame of the clip. The OUT boundary is the end of
the clip. A timestamp gets you near the right place; the boundaries decide what
is actually transcribed and exported.

Move the playhead, then use **Set in** or **Set out**, or drag the IN and OUT
pointers above the waveform. A boundary snaps to the playhead when it is close
enough. The playhead itself does not snap to a boundary.

Changing a boundary removes that clip's old transcript and captions because
the audio range has changed. Transcribe the clip again after changing its
boundaries. The clip is also removed from the render queue until it is ready
again.

### Transcript row

A transcript row is a timed piece of recognized speech. Each row keeps four
different kinds of information separate:

- **Raw Korean:** Whisper's original recognition. This is kept as a reference.
- **Corrected Korean:** The editable, cleaned Korean transcript.
- **Natural English:** The editable English translation.
- **Time / speaker:** The row's start time and assigned speaker.

Transcript rows follow speech timing. They are not the final on-screen caption
layout.

### Caption cue

A caption cue is the text that appears on screen for a specific period. Caption
cues are generated from the reviewed transcript. **Words per line** and
**Caption lines** change how translated words are regrouped into new caption
cues and timestamps.

This separation is intentional: first make an accurate transcript, then create
captions that fit the desired visual format.

### Render queue

The render queue is the list of clips you intend to export. Export is always
clip-based; the queue shows each clip's title and exact boundaries. Successfully
exported clips leave the queue automatically.

## Complete workflow

### 1. Create a project

Choose **New project**, enter a project name, and add known speaker names when
useful. The translation profile controls the general English style:

- **Natural conversation:** Spoken, meaning-first English.
- **Clean YouTube:** Fewer fillers and softened profanity.
- **Faithful review:** Wording kept closer to the Korean for checking meaning.
- **Custom:** Your own translation instructions.

### 2. Import media

Import MP4, MOV, MKV, MP3, WAV, M4A, or AAC. Subtitle Studio prepares local
audio and builds the timeline waveform.

To replace the source later, use the upload button in the project header. Media
replacement keeps the project's reusable settings, but removes the old
timestamps, clips, transcript analysis, and post copy. Import timestamps for
the replacement media and create its clips again.

### 3. Import navigation timestamps

Open **Clips** and paste one timestamp and title per line:

```text
00:00 Opening topic
03:37 Why leverage changes the risk
12:48 Market outlook
01:03:47 Closing discussion
```

Accepted time formats are `MM:SS` and `HH:MM:SS`. Use **Replace** when the
timestamp list needs to be reassigned.

The timestamp list remains a single full-video navigation list. Clicking a
timestamp moves the player but does not create another tab.

### 4. Open a clip workspace

Click the arrow at the right side of a timestamp. The new tab opens an
independent clip workspace and frames that area of the timeline. Full-video
timestamp markers are hidden while editing the clip so the timeline can focus
on the clip sequence.

### 5. Refine the boundaries

Use the real waveform and time ruler to find the first and last frames.

- Click anywhere on the waveform to place the playhead.
- Drag the orange playhead to scrub.
- Drag IN or OUT to change a boundary.
- Move the playhead first and press **Set in** or **Set out** for an exact cut.
- Zoom until the left and right arrow keys can move frame by frame.

### 6. Create the English transcript

Open **Transcribe** and click **Create English transcript**. It runs every
remaining stage in the correct order:

1. **Detect speakers:** Finds speaker turns and matches enrolled hosts when
   voice profiles are available.
2. **Transcribe Korean:** Runs local Whisper `large-v3` on this clip.
3. **Local correction:** Uses nearby dialogue to correct recognition errors.
4. **Episode consistency:** Standardizes names, terms, and recurring context.
5. **Conversational English:** Produces the final natural English transcript.

Why speaker detection comes first: it determines who spoke during each audio
range. Whisper then determines what was said. The app can combine the timing
and identity information when it creates the transcript rows.

You may run an individual stage instead of the full sequence. Completed stages
are skipped when the one-click workflow resumes.

### 7. Monitor, pause, or stop a task

The active task shows its current stage and estimated completion time. The ETA
appears after enough progress has been measured.

- **Pause:** Stops at a safe processing point and keeps progress for resuming.
- **Resume:** Continues a paused task.
- **Stop:** Cancels the task. A stopped task cannot be resumed.

The first transcription can take longer while the largest Whisper model is
downloaded and prepared.

### 8. Review and edit the transcript

- Click a transcript row to seek to its time.
- Playback automatically scrolls the transcript area to the active row.
- Edit **Corrected Korean** or **Natural English** directly. The edit saves when
  the field loses focus.
- Use the speaker menu in a row to correct its speaker.
- Use **Search transcript** to find words or names in the active clip.
- Use **Review flags** to show low-confidence or warning rows.
- Use the lock button to protect a row from regeneration.
- Use the check button to mark a row approved.

Editing transcript text makes generated captions stale. Regenerate captions
before export so the caption track includes the edits.

### 9. Generate and style captions

Open **Style** for the active clip.

#### Caption timing controls

- **Words per line:** Maximum target word count for one visual line, from 2 to
  40 words.
- **Caption lines:** Maximum lines shown in one caption, from 1 to 4.
- **Generate captions / Regenerate captions:** Rebuilds caption cues and their
  timestamps from the reviewed transcript.

When a transcript already exists, changing words per line or caption lines
automatically rebuilds the clip's caption timing after the setting is saved.
Use **Regenerate captions** whenever the panel reports that the caption track is
stale.

The words-per-line value is a target, not permission to draw outside the video.
Font size stays fixed. If the selected font size and maximum width physically
fit fewer words, the preview reports the width limit rather than shrinking the
font.

#### Appearance controls

The Style panel includes font, font size, bold, italic, text color, letter
spacing, line spacing, alignment, vertical position, maximum width, screen
margin, background color and opacity, padding, corner radius, edge, and shadow.

Styles belong to individual clips. Use **Apply to all** to copy the active
clip's style to every clip in the project. Save a named **Style preset** to use
the same design in other projects. Style presets are permanent app-wide items.

The preview and rendered video use the same subtitle-size calculation. Subtitle
font size is independent from the **App font size** setting.

### 10. Generate post copy

Open **Post copy** inside the clip tab. Generation uses only the active clip's
English transcript and the Post copy model selected in Settings.

The result contains an editable headline and sentence-separated quote blocks.
It is intended to communicate the clip's essential information even when the
reader does not watch the video. Use **Regenerate** for a new version and
**Copy** at the top of the section to place the complete post on the clipboard.

### 11. Add clips to the render queue

In each finished clip tab, click **Render queue** beside the IN and OUT values.
The button changes to **Queued**. Repeat for every clip you want to export.

Open **Render queue** in the project header to:

- select or deselect individual clips
- select all or none
- remove everything from the queue
- choose 1080p or original resolution
- choose GPU or CPU encoding
- choose Maximum or High quality
- export Video, SRT, Styled ASS, or any combination

Video is selected by default. SRT and Styled ASS are unselected by default. To
export subtitle files without rendering video, turn off **Video** and select
SRT and/or Styled ASS.

GPU mode is the default. On Windows, Subtitle Studio tries NVIDIA NVENC, Intel
Quick Sync, or AMD AMF and falls back to CPU encoding when necessary. The
default video canvas is 1920 x 1080. **Original** keeps the source dimensions.

Files are written directly into the export folder selected in Settings; the app
does not create an extra subfolder. A completion message confirms the export
and can open the destination folder.

## Speaker detection and voice profiles

Speaker detection is optional, but useful when several people appear in one
clip.

### Automatic speaker count

Use **Expected speakers: Auto** when the voices are very distinct. If Auto
merges three hosts into two speakers, choose the known count before running
detection again.

### Enrolled recurring hosts

Under **Settings > Voices**, provide a clean solo recording for each recurring
host.

- Minimum: 5 seconds.
- Recommended: 30 to 90 seconds.
- Maximum: 10 minutes.
- Longer samples can help when they contain varied, clean speech.
- Music, echo, background speech, and overlapping voices reduce accuracy.

With enrolled hosts, detection analyzes the selected clip ranges and compares
speakers with the saved profiles. Without profiles, the app falls back to
general episode speaker detection. Voice samples and embeddings remain local.

Always review speaker assignments. Detection is an editing aid, not a guarantee.

## Keyboard and mouse shortcuts

Playback shortcuts work unless the cursor is actively editing a text or search
field.

| Shortcut | Action |
| --- | --- |
| `Space` | Toggle play and pause. |
| `Ctrl+Space` | Play the active clip from its IN boundary. |
| `J` | Play at 0.5x speed. |
| `K` | Play at 1x speed. |
| `L` | Play at 2x speed. |
| Click waveform | Place the playhead at that point. |
| Drag playhead | Scrub to a new time. |
| `Alt+mouse wheel` over waveform | Zoom in or out around the pointer. |
| `Ctrl+mouse wheel` over waveform | Pan through a zoomed waveform. |
| `Left Arrow` / `Right Arrow` with waveform focused | Move one video frame. |
| `Shift+Left Arrow` / `Shift+Right Arrow` with waveform focused | Move ten video frames. |

The left sidebar has a resize handle on its right edge. Drag it freely. When
the handle has keyboard focus:

| Shortcut | Action |
| --- | --- |
| `Left Arrow` / `Right Arrow` | Resize the sidebar by 12 pixels. |
| `Shift+Left Arrow` / `Shift+Right Arrow` | Resize by 40 pixels. |
| `Home` | Reset the sidebar to its default width. |

## What the app remembers

Subtitle Studio saves work continuously. After a normal restart it restores:

- the last-opened project
- the active clip tab and selected transcript row
- the playhead and playback speed
- the open left-panel section
- transcript search and review filtering
- timeline zoom
- export resolution, quality, and encoder choices
- app font size and sidebar width
- clip styles, style presets, transcripts, post copy, and render queue

Use **Settings > Apply update & restart** when an update is ready. The update
reopens the Windows or macOS app without requiring a terminal window. The app
and its update can live in different folders; projects and settings remain in
the operating system's application-data folder.

## Troubleshooting

### Create English transcript is unavailable

Make sure media is imported, a clip tab is open, the OpenRouter key is saved,
and the required local models are available. Speaker detection also requires a
Hugging Face token when that stage has not been completed.

### Transcription appears slow

Check whether the app is downloading or preparing Whisper `large-v3`. Later
transcriptions reuse the cached model. Transcription is limited to the active
clip boundaries, not the whole video.

### Speaker detection finds the wrong number of people

Set **Expected speakers** to the known number and run detection again. For
regular hosts, enroll cleaner solo voice samples and review row assignments.

### Caption word or line limits do not appear in export

Confirm the caption track is not marked stale. Press **Regenerate captions**
after transcript edits or an Apply-to-all style change.

### Export is disabled

At least one clip must be in the render queue, at least one output format must
be selected, and the generated caption track must be current.

### A boundary change removed the transcript

This is expected. The previous words and timestamps belonged to the old audio
range. Run **Create English transcript** again for that clip.

### A task was interrupted when the app closed

The app marks the interrupted task as needing attention. Start the task again;
completed pipeline stages remain available.

### An interface setting did not appear to save

Wait briefly for the **Saved locally** status, then restart. App font size,
sidebar width, project position, and style presets are stored in the permanent
local database.

## Recommended first-project checklist

- [ ] OpenRouter key and models selected
- [ ] Export folder selected
- [ ] Hugging Face token added if speaker detection is needed
- [ ] Regular hosts enrolled if applicable
- [ ] Media imported
- [ ] Navigation timestamps imported
- [ ] Clip tab opened and boundaries refined
- [ ] English transcript created and reviewed
- [ ] Caption timing regenerated after final edits
- [ ] Subtitle appearance checked in preview
- [ ] Post copy generated and reviewed if needed
- [ ] Clip added to render queue
- [ ] Correct output formats selected and exported
