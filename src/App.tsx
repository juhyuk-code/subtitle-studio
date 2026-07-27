import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  CloudSlashIcon,
  DownloadSimpleIcon,
  FileAudioIcon,
  FolderOpenIcon,
  GearSixIcon,
  LockKeyIcon,
  MagnifyingGlassIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  SlidersHorizontalIcon,
  SparkleIcon,
  UploadSimpleIcon,
  WarningCircleIcon,
  WaveformIcon,
  XIcon
} from "@phosphor-icons/react";
import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { api } from "./api";
import {
  describeMediaPreparation,
  type MediaPreparation
} from "./lib/upload";
import type {
  GlossaryEntry,
  Job,
  Project,
  RuntimeStatus,
  Segment,
  TimestampClip,
  TranslationProfile
} from "./types";

const stageLabels: Record<string, string> = {
  draft: "Draft",
  media_ready: "Media ready",
  transcribed: "Raw transcript",
  corrected_pass_1: "Local correction",
  corrected: "Episode consistency",
  translated: "English ready",
  queued: "Queued",
  preparing_model: "Downloading Whisper model",
  transcribing: "Transcribing Korean",
  correcting_pass_1: "Correcting nearby context",
  correcting_pass_2: "Checking episode consistency",
  translating: "Writing conversational English",
  failed: "Needs attention",
  cancelled: "Cancelled"
};

const profiles: { value: TranslationProfile; label: string; detail: string }[] = [
  {
    value: "natural_conversation",
    label: "Natural conversation",
    detail: "Spoken English, contractions, original tone"
  },
  {
    value: "clean_youtube",
    label: "Clean YouTube",
    detail: "Reduced fillers and softened profanity"
  },
  {
    value: "faithful_review",
    label: "Faithful review",
    detail: "Closer wording for meaning verification"
  },
  {
    value: "custom",
    label: "Custom",
    detail: "Use your own translation direction"
  }
];

function formatTime(milliseconds: number) {
  const totalSeconds = Math.max(0, milliseconds / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const tenths = Math.floor((totalSeconds % 1) * 10);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

function formatTimestamp(milliseconds: number) {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatDuration(milliseconds: number) {
  const totalMinutes = Math.round(milliseconds / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours}h ${minutes}m selected` : `${minutes}m selected`;
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshProjects = useCallback(async () => {
    const next = await api.projects();
    setProjects(next);
    return next;
  }, []);

  useEffect(() => {
    Promise.all([refreshProjects(), api.runtime()])
      .then(([, status]) => {
        setRuntime(status);
        if (
          !status.openrouter_configured &&
          window.localStorage.getItem("subtitle-studio:connection-dismissed") !== "true"
        ) {
          setConnecting(true);
        }
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [refreshProjects]);

  if (loading) return <LoadingShell />;

  if (selectedId) {
    return (
      <>
        <Editor
          projectId={selectedId}
          runtime={runtime}
          onOpenSettings={() => setConnecting(true)}
          onBack={async () => {
            await refreshProjects();
            setSelectedId(null);
          }}
        />
        {connecting ? (
          <OpenRouterModal
            configured={runtime?.openrouter_configured ?? false}
            onClose={() => {
              window.localStorage.setItem(
                "subtitle-studio:connection-dismissed",
                "true"
              );
              setConnecting(false);
            }}
            onConnected={async () => {
              setRuntime(await api.runtime());
              setConnecting(false);
            }}
          />
        ) : null}
      </>
    );
  }

  return (
    <div className="home-shell">
      <header className="home-nav">
        <Brand />
        <div className="home-nav-actions">
          <button
            className="connection-button"
            onClick={() => setConnecting(true)}
          >
            <span className={runtime?.openrouter_configured ? "connection-dot ready" : "connection-dot"} />
            {runtime?.openrouter_configured ? "OpenRouter connected" : "Connect OpenRouter"}
          </button>
          <div className="privacy-pill">
            <CloudSlashIcon size={15} weight="bold" />
            Local media
          </div>
        </div>
      </header>

      <main className="home-main">
        <section className="intro">
          <div className="eyebrow">
            <span className="status-dot" />
            Korean in. Natural English out.
          </div>
          <h1>Keep the timing.<br />Fix the meaning.</h1>
          <p>
            Build a reliable Korean master transcript before translation, then
            shape the English for real viewers.
          </p>
          <button className="primary large" onClick={() => setCreating(true)}>
            <PlusIcon size={18} weight="bold" />
            New project
          </button>
        </section>

        <section className="project-rail" aria-label="Projects">
          <div className="section-heading">
            <span>Recent work</span>
            <span className="count">{projects.length}</span>
          </div>
          {error ? <InlineError message={error} /> : null}
          {projects.length === 0 ? (
            <button className="empty-project" onClick={() => setCreating(true)}>
              <span className="empty-icon">
                <FolderOpenIcon size={28} />
              </span>
              <span>
                <strong>No projects yet</strong>
                <small>Your media stays on this computer.</small>
              </span>
              <ArrowRightIcon size={18} />
            </button>
          ) : (
            <div className="project-list">
              {projects.map((project, index) => (
                <button
                  className="project-row"
                  key={project.project_id}
                  style={{ "--delay": `${index * 55}ms` } as React.CSSProperties}
                  onClick={() => setSelectedId(project.project_id)}
                >
                  <span className="project-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="project-copy">
                    <strong>{project.name}</strong>
                    <small>
                      {project.media_name ?? "Waiting for media"} ·{" "}
                      {new Date(project.updated_at).toLocaleDateString()}
                    </small>
                  </span>
                  <span className={`stage-chip ${project.status}`}>
                    {stageLabels[project.status] ?? project.status}
                  </span>
                  <ArrowRightIcon size={17} />
                </button>
              ))}
            </div>
          )}
        </section>

        <aside className="runtime-panel">
          <span className="runtime-kicker">Local pipeline</span>
          <div>
            <RuntimeLine label="FFmpeg" ready={runtime?.ffmpeg ?? false} />
            <RuntimeLine label="Whisper" ready={runtime?.whisper ?? false} />
            <RuntimeLine
              label="OpenRouter"
              ready={runtime?.openrouter_configured ?? false}
              detail={runtime?.openrouter_configured ? "Configured" : "API key needed"}
            />
          </div>
          <p>Correction and translation send transcript text to OpenRouter.</p>
        </aside>
      </main>

      {creating ? (
        <NewProjectModal
          onClose={() => setCreating(false)}
          onCreate={async (data) => {
            const project = await api.createProject(data);
            setProjects((current) => [project, ...current]);
            setCreating(false);
            setSelectedId(project.project_id);
          }}
        />
      ) : null}
      {connecting ? (
        <OpenRouterModal
          configured={runtime?.openrouter_configured ?? false}
          onClose={() => {
            window.localStorage.setItem(
              "subtitle-studio:connection-dismissed",
              "true"
            );
            setConnecting(false);
          }}
          onConnected={async () => {
            setRuntime(await api.runtime());
            setConnecting(false);
          }}
        />
      ) : null}
    </div>
  );
}

function OpenRouterModal({
  configured,
  onClose,
  onConnected
}: {
  configured: boolean;
  onClose: () => void;
  onConnected: () => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.saveOpenRouterSettings(apiKey.trim());
      window.localStorage.removeItem("subtitle-studio:connection-dismissed");
      await onConnected();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the connection.");
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <form
        className="new-project-modal settings-modal"
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">{configured ? "Connection settings" : "One-time setup"}</span>
            <h2>{configured ? "OpenRouter is connected." : "Connect the language engine."}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            <XIcon size={18} />
          </button>
        </div>
        <div className="connection-explainer">
          <span><LockKeyIcon size={21} /></span>
          <div>
            <strong>One key, stored on this computer</strong>
            <p>
              The key is saved by the local backend and is never returned to
              the browser. Transcript text is sent to OpenRouter only when you
              run a correction or translation stage.
            </p>
          </div>
        </div>
        <label>
          OpenRouter API key
          <input
            autoFocus
            required
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={configured ? "Enter a replacement key" : "sk-or-v1-…"}
          />
          <small>
            Create a key at{" "}
            <a href="https://openrouter.ai/settings/keys" target="_blank" rel="noreferrer">
              openrouter.ai/settings/keys
            </a>
            . The app chooses the models automatically.
          </small>
        </label>
        {error ? <InlineError message={error} /> : null}
        <div className="modal-actions split">
          <button type="button" className="secondary" onClick={onClose}>
            {configured ? "Cancel" : "I’ll do this later"}
          </button>
          <button className="primary" disabled={saving || !apiKey.trim()}>
            {saving ? "Connecting…" : configured ? "Replace key" : "Connect & continue"}
            <ArrowRightIcon size={17} />
          </button>
        </div>
      </form>
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "compact" : ""}`}>
      <span className="brand-mark"><WaveformIcon size={20} weight="bold" /></span>
      <span>Subtitle Studio</span>
    </div>
  );
}

function RuntimeLine({
  label,
  ready,
  detail
}: {
  label: string;
  ready: boolean;
  detail?: string;
}) {
  return (
    <div className="runtime-line">
      <span className={ready ? "check ready" : "check muted"}>
        {ready ? <CheckIcon size={11} weight="bold" /> : <span />}
      </span>
      <span>{label}</span>
      <small>{ready ? "Ready" : detail ?? "Unavailable"}</small>
    </div>
  );
}

function NewProjectModal({
  onClose,
  onCreate
}: {
  onClose: () => void;
  onCreate: Parameters<typeof api.createProject>[0] extends infer T
    ? (data: T) => Promise<void>
    : never;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [speakers, setSpeakers] = useState("");
  const [profile, setProfile] =
    useState<TranslationProfile>("natural_conversation");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onCreate({
        name,
        description,
        speakers: speakers.split(",").map((value) => value.trim()).filter(Boolean),
        translation_profile: profile,
        custom_instructions: instructions
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create project.");
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <form
        className="new-project-modal"
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">New project</span>
            <h2>Set the episode context.</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            <XIcon size={18} />
          </button>
        </div>
        <label>
          Project name
          <input
            autoFocus
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Episode 24 — Market structure"
          />
        </label>
        <label>
          Episode description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Who is speaking, what they discuss, and useful context."
            rows={3}
          />
        </label>
        <label>
          Speaker names
          <input
            value={speakers}
            onChange={(event) => setSpeakers(event.target.value)}
            placeholder="민준, 서윤, Host"
          />
          <small>Separate names with commas. You can assign them later.</small>
        </label>
        <fieldset>
          <legend>Translation style</legend>
          <div className="profile-grid">
            {profiles.map((item) => (
              <button
                type="button"
                key={item.value}
                className={profile === item.value ? "profile selected" : "profile"}
                onClick={() => setProfile(item.value)}
              >
                <span>{item.label}</span>
                <small>{item.detail}</small>
              </button>
            ))}
          </div>
        </fieldset>
        {profile === "custom" ? (
          <label>
            Custom direction
            <textarea
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="Use American English. Keep crypto slang. Preserve profanity."
              rows={2}
            />
          </label>
        ) : null}
        {error ? <InlineError message={error} /> : null}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={saving || !name.trim()}>
            {saving ? "Creating…" : "Create project"}
            <ArrowRightIcon size={17} />
          </button>
        </div>
      </form>
    </div>
  );
}

function Editor({
  projectId,
  runtime,
  onOpenSettings,
  onBack
}: {
  projectId: string;
  runtime: RuntimeStatus | null;
  onOpenSettings: () => void;
  onBack: () => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [glossary, setGlossary] = useState<GlossaryEntry[]>([]);
  const [clips, setClips] = useState<TimestampClip[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [warningOnly, setWarningOnly] = useState(false);
  const [sidebarTab, setSidebarTab] =
    useState<"stages" | "timestamps" | "glossary">("stages");
  const [mediaPreparation, setMediaPreparation] =
    useState<MediaPreparation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentMs, setCurrentMs] = useState(0);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    const [nextProject, nextSegments, nextGlossary, nextClips, activeJob] = await Promise.all([
      api.project(projectId),
      api.segments(projectId),
      api.glossary(projectId),
      api.clips(projectId),
      api.activeJob(projectId)
    ]);
    setProject(nextProject);
    setSegments(nextSegments);
    setGlossary(nextGlossary);
    setClips(nextClips);
    setJob(activeJob);
  }, [projectId]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh]);

  useEffect(() => {
    if (!job || ["failed", "cancelled", "transcribed", "corrected_pass_1", "corrected", "translated"].includes(job.stage)) {
      return;
    }
    const timer = window.setInterval(async () => {
      const next = await api.job(job.job_id);
      setJob(next);
      if (["failed", "cancelled", "transcribed", "corrected_pass_1", "corrected", "translated"].includes(next.stage)) {
        await refresh();
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job, refresh]);

  const visibleSegments = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return segments.filter((segment) => {
      if (warningOnly && segment.warnings.length === 0 && segment.confidence >= 0.65) return false;
      return (
        !normalized ||
        [segment.raw_korean, segment.pass_2_korean, segment.english, segment.speaker_id]
          .some((value) => value?.toLowerCase().includes(normalized))
      );
    });
  }, [segments, query, warningOnly]);

  const active = segments.find(
    (segment) => currentMs >= segment.start_ms && currentMs <= segment.end_ms
  );

  async function uploadFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setMediaPreparation({
      phase: "uploading",
      progress: 0,
      filename: file.name
    });
    setError(null);
    try {
      setProject(
        await api.upload(projectId, file, {
          onProgress: (progress) =>
            setMediaPreparation({
              phase: "uploading",
              progress,
              filename: file.name
            }),
          onProcessing: () =>
            setMediaPreparation({
              phase: "processing",
              progress: 1,
              filename: file.name
            })
        })
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally {
      setMediaPreparation(null);
      event.target.value = "";
    }
  }

  async function startStage(stage: "transcribe" | "pass-1" | "pass-2" | "translate") {
    setError(null);
    try {
      setJob(await api.startStage(projectId, stage));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start processing.");
    }
  }

  async function patchSegment(id: string, patch: Partial<Segment>) {
    const updated = await api.patchSegment(projectId, id, patch);
    setSegments((current) =>
      current.map((segment) => (segment.segment_id === id ? updated : segment))
    );
  }

  function seek(segment: Segment) {
    setSelected(segment.segment_id);
    setCurrentMs(segment.start_ms);
    if (mediaRef.current) mediaRef.current.currentTime = segment.start_ms / 1000;
  }

  if (!project) return <LoadingShell />;

  const isVideo = /\.(mp4|mov|mkv)$/i.test(project.media_name ?? "");
  const busy = job && !["failed", "cancelled", "transcribed", "corrected_pass_1", "corrected", "translated"].includes(job.stage);
  const preparationCopy = mediaPreparation
    ? describeMediaPreparation(mediaPreparation)
    : null;

  return (
    <div className="editor-shell">
      <header className="editor-topbar">
        <button className="back-button" onClick={onBack} aria-label="Back to projects">
          <ArrowLeftIcon size={18} />
        </button>
        <Brand compact />
        <span className="top-divider" />
        <div className="project-title">
          <strong>{project.name}</strong>
          <span>{stageLabels[project.status] ?? project.status}</span>
        </div>
        <div className="top-actions">
          <span className={`save-state ${mediaPreparation ? "working" : ""}`}>
            {mediaPreparation ? (
              <span className="activity-spinner" />
            ) : (
              <CheckIcon size={13} weight="bold" />
            )}
            {preparationCopy?.title ?? "Saved locally"}
          </span>
          <button
            className="icon-button editor-settings"
            onClick={onOpenSettings}
            aria-label="OpenRouter settings"
            title="OpenRouter settings"
          >
            <GearSixIcon size={16} />
          </button>
          <a
            className="secondary export"
            href={`/api/projects/${projectId}/export/srt?language=en`}
            download
          >
            <DownloadSimpleIcon size={16} />
            Export SRT
          </a>
        </div>
      </header>

      <div className="editor-grid">
        <aside className="editor-sidebar">
          <div className="sidebar-tabs">
            <button
              className={sidebarTab === "stages" ? "active" : ""}
              onClick={() => setSidebarTab("stages")}
            >
              Stages
            </button>
            <button
              className={sidebarTab === "timestamps" ? "active" : ""}
              onClick={() => setSidebarTab("timestamps")}
            >
              Clips <span>{clips.filter((clip) => clip.selected).length}</span>
            </button>
            <button
              className={sidebarTab === "glossary" ? "active" : ""}
              onClick={() => setSidebarTab("glossary")}
            >
              Glossary <span>{glossary.length}</span>
            </button>
          </div>
          {sidebarTab === "stages" ? (
            <Pipeline
              project={project}
              job={job}
              runtime={runtime}
              clips={clips}
              onUpload={() => uploadRef.current?.click()}
              onStart={startStage}
              mediaPreparation={mediaPreparation}
            />
          ) : sidebarTab === "timestamps" ? (
            <TimestampPanel
              clips={clips}
              durationMs={project.duration_ms}
              disabled={!!busy || !!mediaPreparation}
              onImport={async (text) => {
                const imported = await api.importClips(projectId, text);
                setClips(imported);
                setSidebarTab("timestamps");
              }}
              onSelectionChange={async (clipId, selected) => {
                const updated = await api.selectClip(
                  projectId,
                  clipId,
                  selected
                );
                setClips((current) =>
                  current.map((clip) =>
                    clip.clip_id === updated.clip_id ? updated : clip
                  )
                );
              }}
              onError={(message) => setError(message)}
            />
          ) : (
            <GlossaryPanel
              entries={glossary}
              onAdd={async (entry) => {
                const saved = await api.addGlossary(projectId, entry);
                setGlossary((current) => [...current, saved]);
              }}
            />
          )}
          <input
            hidden
            ref={uploadRef}
            type="file"
            accept=".mp4,.mov,.mkv,.mp3,.wav,.m4a,.aac"
            onChange={uploadFile}
          />
        </aside>

        <main className="workbench">
          {error || job?.error ? <InlineError message={error ?? job?.error ?? ""} /> : null}
          {mediaPreparation && preparationCopy ? (
            <section
              className={`media-preparation ${mediaPreparation.phase}`}
              role="status"
              aria-live="polite"
            >
              <span className="media-preparation-icon">
                <WaveformIcon size={22} weight="bold" />
              </span>
              <span className="media-preparation-copy">
                <strong>{preparationCopy.title}</strong>
                <small>{mediaPreparation.filename}</small>
                <p>{preparationCopy.detail}</p>
              </span>
              <span className="media-preparation-progress" aria-hidden="true">
                <i
                  style={{
                    transform:
                      mediaPreparation.phase === "uploading"
                        ? `scaleX(${mediaPreparation.progress})`
                        : undefined
                  }}
                />
              </span>
            </section>
          ) : null}
          <section className={`player ${project.media_url ? "" : "empty"}`}>
            {project.media_url ? (
              <>
                {isVideo ? (
                  <video
                    ref={(node) => { mediaRef.current = node; }}
                    src={project.media_url}
                    onTimeUpdate={(event) => setCurrentMs(event.currentTarget.currentTime * 1000)}
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                  />
                ) : (
                  <audio
                    ref={(node) => { mediaRef.current = node; }}
                    src={project.media_url}
                    onTimeUpdate={(event) => setCurrentMs(event.currentTarget.currentTime * 1000)}
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                  />
                )}
                <div className="transport">
                  <button
                    className="play-button"
                    onClick={() => {
                      if (!mediaRef.current) return;
                      if (mediaRef.current.paused) void mediaRef.current.play();
                      else mediaRef.current.pause();
                    }}
                  >
                    {playing ? <PauseIcon size={17} weight="fill" /> : <PlayIcon size={17} weight="fill" />}
                  </button>
                  <button onClick={() => {
                    if (mediaRef.current) mediaRef.current.currentTime = Math.max(0, mediaRef.current.currentTime - 5);
                  }}>−5</button>
                  <span className="timecode">{formatTime(currentMs)}</span>
                  <div className="waveform-placeholder" aria-hidden="true">
                    {Array.from({ length: 58 }).map((_, index) => (
                      <i key={index} style={{ height: `${18 + ((index * 19) % 55)}%` }} />
                    ))}
                    <b style={{ width: `${Math.min(100, (currentMs / Math.max(1, project.duration_ms)) * 100)}%` }} />
                  </div>
                  <span className="timecode muted">{formatTime(project.duration_ms)}</span>
                  <select
                    aria-label="Playback speed"
                    defaultValue="1"
                    onChange={(event) => {
                      if (mediaRef.current) mediaRef.current.playbackRate = Number(event.target.value);
                    }}
                  >
                    <option value=".75">0.75×</option>
                    <option value="1">1×</option>
                    <option value="1.25">1.25×</option>
                    <option value="1.5">1.5×</option>
                  </select>
                </div>
                <div className="live-caption">
                  {active?.english || active?.pass_2_korean || active?.raw_korean || "Playback is ready."}
                </div>
              </>
            ) : (
              <button className="player-empty" onClick={() => uploadRef.current?.click()}>
                <span><UploadSimpleIcon size={23} /></span>
                <strong>Bring in the episode</strong>
                <small>MP4, MOV, MKV, MP3, WAV, M4A or AAC</small>
              </button>
            )}
          </section>

          <section className="transcript">
            <div className="transcript-toolbar">
              <div>
                <h2>Transcript</h2>
                <span>{segments.length} segments</span>
              </div>
              <label className="search">
                <MagnifyingGlassIcon size={16} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search transcript"
                />
              </label>
              <button
                className={warningOnly ? "filter active" : "filter"}
                onClick={() => setWarningOnly((value) => !value)}
              >
                <SlidersHorizontalIcon size={16} />
                Review flags
                <span>{segments.filter((item) => item.warnings.length || item.confidence < 0.65).length}</span>
              </button>
            </div>

            {busy && segments.length === 0 ? (
              <TranscriptSkeleton />
            ) : visibleSegments.length === 0 ? (
              <div className="transcript-empty">
                <FileAudioIcon size={29} />
                <strong>{segments.length ? "No matching lines" : "No transcript yet"}</strong>
                <p>
                  {segments.length
                    ? "Clear the search or review filter."
                    : "Upload media, then run the Korean transcription stage."}
                </p>
              </div>
            ) : (
              <div className="transcript-table">
                <div className="table-head">
                  <span>Time / speaker</span>
                  <span>Raw Korean</span>
                  <span>Corrected Korean</span>
                  <span>Natural English</span>
                  <span>Status</span>
                </div>
                {visibleSegments.map((segment, index) => (
                  <SegmentRow
                    key={segment.segment_id}
                    segment={segment}
                    index={index}
                    selected={selected === segment.segment_id}
                    onSelect={() => seek(segment)}
                    onPatch={(patch) => patchSegment(segment.segment_id, patch)}
                  />
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}

function Pipeline({
  project,
  job,
  runtime,
  clips,
  onUpload,
  onStart,
  mediaPreparation
}: {
  project: Project;
  job: Job | null;
  runtime: RuntimeStatus | null;
  clips: TimestampClip[];
  onUpload: () => void;
  onStart: (stage: "transcribe" | "pass-1" | "pass-2" | "translate") => void;
  mediaPreparation: MediaPreparation | null;
}) {
  const ranks: Record<string, number> = {
    draft: 0, media_ready: 1, transcribed: 2, corrected_pass_1: 3, corrected: 4, translated: 5
  };
  const rank = ranks[project.status] ?? 0;
  const selectedClips = clips.filter((clip) => clip.selected);
  const selectedDurationMs = selectedClips.reduce(
    (total, clip) => total + clip.end_ms - clip.start_ms,
    0
  );
  const steps = [
    {
      title: "Import media",
      detail: project.media_name ?? "Audio or video",
      enabled: true,
      done: rank >= 1,
      action: onUpload
    },
    {
      title: "Transcribe Korean",
      detail: runtime?.whisper
        ? clips.length
          ? `${selectedClips.length} clips · ${formatDuration(selectedDurationMs)}`
          : "Entire video · Whisper small"
        : "Whisper unavailable",
      enabled: rank >= 1 && !!runtime?.whisper,
      done: rank >= 2,
      action: () => onStart("transcribe")
    },
    {
      title: "Local correction",
      detail: runtime?.openrouter_configured
        ? "Nearby dialogue · OpenRouter"
        : "OpenRouter API key needed",
      enabled: rank >= 2 && !!runtime?.openrouter_configured,
      done: rank >= 3,
      action: () => onStart("pass-1")
    },
    {
      title: "Episode consistency",
      detail: runtime?.openrouter_configured
        ? "Names and terms · OpenRouter"
        : "OpenRouter API key needed",
      enabled: rank >= 3 && !!runtime?.openrouter_configured,
      done: rank >= 4,
      action: () => onStart("pass-2")
    },
    {
      title: "Conversational English",
      detail: runtime?.openrouter_configured
        ? "Meaning-first · OpenRouter"
        : "OpenRouter API key needed",
      enabled: rank >= 4 && !!runtime?.openrouter_configured,
      done: rank >= 5,
      action: () => onStart("translate")
    }
  ];
  const busy = !!mediaPreparation || (job && !["failed", "cancelled", "transcribed", "corrected_pass_1", "corrected", "translated"].includes(job.stage));
  const preparationCopy = mediaPreparation
    ? describeMediaPreparation(mediaPreparation)
    : null;

  return (
    <div className="pipeline">
      <div className="pipeline-heading">
        <span>Processing</span>
        <small>{Math.round(job?.progress ? job.progress * 100 : (rank / 5) * 100)}%</small>
      </div>
      <div className="overall-progress"><i style={{ transform: `scaleX(${job?.progress ?? rank / 5})` }} /></div>
      <div className="step-list">
        {steps.map((step, index) => (
          <button
            className={`pipeline-step ${step.done ? "done" : ""}`}
            key={step.title}
            disabled={!step.enabled || !!busy}
            onClick={step.action}
          >
            <span className="step-number">
              {step.done ? <CheckIcon size={12} weight="bold" /> : index + 1}
            </span>
            <span>
              <strong>{step.title}</strong>
              <small>{step.detail}</small>
            </span>
            {!step.done && step.enabled ? <ArrowRightIcon size={14} /> : null}
          </button>
        ))}
      </div>
      {busy ? (
        <div className="active-job">
          <div>
            <span className="processing-mark"><SparkleIcon size={15} weight="fill" /></span>
            <span>
              <strong>{preparationCopy?.title ?? stageLabels[job?.stage ?? "queued"]}</strong>
              <small>
                {preparationCopy?.detail ?? "Completed work is checkpointed."}
              </small>
            </span>
          </div>
          <div className={`job-progress ${mediaPreparation?.phase === "processing" ? "indeterminate" : ""}`}>
            <i
              style={{
                transform:
                  mediaPreparation?.phase === "uploading"
                    ? `scaleX(${mediaPreparation.progress})`
                    : mediaPreparation?.phase === "processing"
                      ? undefined
                      : `scaleX(${job?.progress ?? 0.15})`
              }}
            />
          </div>
        </div>
      ) : null}
      <div className="privacy-note">
        <CloudSlashIcon size={16} />
        <span><strong>Hybrid processing</strong><small>Media stays local. Transcript text is sent to OpenRouter.</small></span>
      </div>
    </div>
  );
}

function TimestampPanel({
  clips,
  durationMs,
  disabled,
  onImport,
  onSelectionChange,
  onError
}: {
  clips: TimestampClip[];
  durationMs: number;
  disabled: boolean;
  onImport: (text: string) => Promise<void>;
  onSelectionChange: (clipId: string, selected: boolean) => Promise<void>;
  onError: (message: string) => void;
}) {
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(clips.length === 0);
  const [saving, setSaving] = useState(false);
  const selectedCount = clips.filter((clip) => clip.selected).length;

  async function importTimestamps(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    onError("");
    try {
      await onImport(text);
      setEditing(false);
      setText("");
    } catch (reason) {
      onError(
        reason instanceof Error ? reason.message : "Could not import timestamps."
      );
    } finally {
      setSaving(false);
    }
  }

  async function setAll(selected: boolean) {
    setSaving(true);
    onError("");
    try {
      await Promise.all(
        clips
          .filter((clip) => clip.selected !== selected)
          .map((clip) => onSelectionChange(clip.clip_id, selected))
      );
    } catch (reason) {
      onError(
        reason instanceof Error ? reason.message : "Could not update selection."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="timestamp-panel">
      <div className="timestamp-heading">
        <span>
          <strong>Timestamp clips</strong>
          <small>
            {clips.length
              ? `${selectedCount} of ${clips.length} selected`
              : "Paste your chapter list"}
          </small>
        </span>
        {clips.length && !editing ? (
          <button onClick={() => setEditing(true)} disabled={disabled || saving}>
            Replace
          </button>
        ) : null}
      </div>

      {editing ? (
        <form className="timestamp-import" onSubmit={importTimestamps}>
          <label>
            One timestamp and title per line
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={"00:00 Opening topic\n03:37 Next topic\n01:03:47 Later topic"}
              rows={8}
              disabled={disabled || saving}
            />
          </label>
          <small>
            Each clip ends where the next one starts. The final clip ends at{" "}
            {durationMs ? formatTimestamp(durationMs) : "the end of the media"}.
          </small>
          <div>
            {clips.length ? (
              <button
                type="button"
                onClick={() => setEditing(false)}
                disabled={saving}
              >
                Cancel
              </button>
            ) : null}
            <button
              className="timestamp-import-button"
              disabled={disabled || saving || !text.trim()}
            >
              {saving ? "Importing…" : "Import timestamps"}
            </button>
          </div>
        </form>
      ) : (
        <>
          <div className="timestamp-actions">
            <button
              onClick={() => void setAll(true)}
              disabled={disabled || saving || selectedCount === clips.length}
            >
              Select all
            </button>
            <button
              onClick={() => void setAll(false)}
              disabled={disabled || saving || selectedCount === 0}
            >
              Select none
            </button>
          </div>
          <div className="timestamp-list">
            {clips.map((clip) => (
              <label
                className={`timestamp-row ${clip.selected ? "selected" : ""}`}
                key={clip.clip_id}
              >
                <input
                  type="checkbox"
                  checked={clip.selected}
                  disabled={disabled || saving}
                  onChange={(event) =>
                    void onSelectionChange(clip.clip_id, event.target.checked)
                  }
                />
                <span className="timestamp-check">
                  {clip.selected ? <CheckIcon size={10} weight="bold" /> : null}
                </span>
                <span>
                  <small>
                    {formatTimestamp(clip.start_ms)}–{formatTimestamp(clip.end_ms)}
                  </small>
                  <strong>{clip.title}</strong>
                </span>
              </label>
            ))}
          </div>
          {selectedCount === 0 ? (
            <p className="timestamp-warning">
              Select at least one clip before processing.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

function GlossaryPanel({
  entries,
  onAdd
}: {
  entries: GlossaryEntry[];
  onAdd: (entry: Omit<GlossaryEntry, "entry_id">) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [korean, setKorean] = useState("");
  const [english, setEnglish] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onAdd({
      source_variants: korean.split(",").map((item) => item.trim()).filter(Boolean),
      canonical_korean: korean.split(",")[0]?.trim() ?? "",
      canonical_english: english.trim(),
      category: "term",
      case_sensitive: true,
      notes: ""
    });
    setKorean("");
    setEnglish("");
    setAdding(false);
  }

  return (
    <div className="glossary-panel">
      <div className="glossary-heading">
        <div><strong>Episode terms</strong><small>Applied at every AI stage</small></div>
        <button className="icon-button small" onClick={() => setAdding(true)}><PlusIcon size={15} /></button>
      </div>
      {adding ? (
        <form className="glossary-form" onSubmit={submit}>
          <label>Korean forms<input required value={korean} onChange={(event) => setKorean(event.target.value)} placeholder="폴리 마켓, 포리마켓" /></label>
          <label>Canonical English<input required value={english} onChange={(event) => setEnglish(event.target.value)} placeholder="Polymarket" /></label>
          <div><button type="button" onClick={() => setAdding(false)}>Cancel</button><button className="primary">Add</button></div>
        </form>
      ) : null}
      {entries.length === 0 ? (
        <div className="glossary-empty"><SparkleIcon size={22} /><p>Add names, slang, companies, and technical terms before transcription.</p></div>
      ) : (
        <div className="glossary-list">
          {entries.map((entry) => (
            <div key={entry.entry_id}>
              <span>{entry.canonical_korean}</span>
              <ArrowRightIcon size={13} />
              <strong>{entry.canonical_english}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SegmentRow({
  segment,
  index,
  selected,
  onSelect,
  onPatch
}: {
  segment: Segment;
  index: number;
  selected: boolean;
  onSelect: () => void;
  onPatch: (patch: Partial<Segment>) => Promise<void>;
}) {
  const [korean, setKorean] = useState(segment.pass_2_korean || segment.pass_1_korean);
  const [english, setEnglish] = useState(segment.english);
  const hasWarning = segment.warnings.length > 0 || segment.confidence < 0.65;

  useEffect(() => {
    setKorean(segment.pass_2_korean || segment.pass_1_korean);
    setEnglish(segment.english);
  }, [segment.pass_1_korean, segment.pass_2_korean, segment.english]);

  return (
    <div
      className={`segment-row ${selected ? "selected" : ""} ${hasWarning ? "warning" : ""}`}
      style={{ "--delay": `${Math.min(index, 12) * 35}ms` } as React.CSSProperties}
      onClick={onSelect}
    >
      <div className="segment-meta">
        <span className="segment-time">{formatTime(segment.start_ms)}</span>
        <select
          value={segment.speaker_id ?? ""}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => void onPatch({ speaker_id: event.target.value || null })}
        >
          <option value="">Speaker</option>
          <option value="SPEAKER_01">Speaker 01</option>
          <option value="SPEAKER_02">Speaker 02</option>
          <option value="SPEAKER_03">Speaker 03</option>
        </select>
      </div>
      <div className="raw-text">{segment.raw_korean}</div>
      <textarea
        value={korean}
        placeholder="Waiting for correction"
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => setKorean(event.target.value)}
        onBlur={() => {
          if (korean !== (segment.pass_2_korean || segment.pass_1_korean)) {
            void onPatch({ pass_2_korean: korean });
          }
        }}
      />
      <textarea
        className="english-edit"
        value={english}
        placeholder="Waiting for translation"
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => setEnglish(event.target.value)}
        onBlur={() => {
          if (english !== segment.english) void onPatch({ english });
        }}
      />
      <div className="segment-status">
        {hasWarning ? (
          <span className="warning-badge" title={segment.warnings.join("\n") || "Low confidence"}>
            <WarningCircleIcon size={16} weight="fill" />
          </span>
        ) : null}
        <button
          className={segment.locked ? "row-action active" : "row-action"}
          title="Lock against regeneration"
          onClick={(event) => {
            event.stopPropagation();
            void onPatch({ locked: !segment.locked });
          }}
        >
          <LockKeyIcon size={15} weight={segment.locked ? "fill" : "regular"} />
        </button>
        <button
          className={segment.approved ? "row-action approved" : "row-action"}
          title="Approve cue"
          onClick={(event) => {
            event.stopPropagation();
            void onPatch({ approved: !segment.approved });
          }}
        >
          <CheckIcon size={15} weight="bold" />
        </button>
      </div>
    </div>
  );
}

function LoadingShell() {
  return (
    <div className="loading-shell">
      <Brand />
      <div><i /><i /><i /></div>
    </div>
  );
}

function TranscriptSkeleton() {
  return (
    <div className="transcript-skeleton">
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index}><i /><i /><i /><i /></div>
      ))}
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="inline-error" role="alert">
      <WarningCircleIcon size={18} weight="fill" />
      <span>{message}</span>
    </div>
  );
}

export default App;
