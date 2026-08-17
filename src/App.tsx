import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ArrowsClockwiseIcon,
  CaretDownIcon,
  CheckIcon,
  CircleIcon,
  CloudSlashIcon,
  CopyIcon,
  DownloadSimpleIcon,
  FileAudioIcon,
  FilmSlateIcon,
  FloppyDiskIcon,
  FolderOpenIcon,
  GearSixIcon,
  LockKeyIcon,
  MagnifyingGlassIcon,
  PaperPlaneTiltIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  SlidersHorizontalIcon,
  SparkleIcon,
  StopIcon,
  TrashIcon,
  UploadSimpleIcon,
  UsersThreeIcon,
  WarningCircleIcon,
  WaveformIcon,
  XIcon
} from "@phosphor-icons/react";
import {
  type CSSProperties,
  type ChangeEvent,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { api } from "./api";
import { ScheduledPostsPanel } from "./ScheduledPosts";
import {
  containedMediaBounds,
  subtitlePreviewScale
} from "./lib/media";
import { readableErrorMessage } from "./lib/errors";
import { isTextEditingTarget } from "./lib/shortcuts";
import {
  panTimelineViewport,
  snapBoundaryToPlayhead,
  timelineViewport
} from "./lib/timeline";
import {
  paginateCaptionByWords,
  paginateCaptionToWidth
} from "./lib/subtitles";
import {
  describeMediaPreparation,
  type MediaPreparation
} from "./lib/upload";
import type {
  CaptionTrack,
  GlossaryEntry,
  Job,
  MediaTimelineInfo,
  NavigationMarker,
  OpenRouterModel,
  PostCopy,
  Project,
  ProjectWorkspaceState,
  RuntimeStatus,
  Segment,
  Speaker,
  SubtitleStyle,
  SubtitleStylePreset,
  TimestampClip,
  TranslationProfile,
  VoiceProfile,
  WorkspaceSidebarTab
} from "./types";

declare global {
  interface Window {
    pywebview?: {
      api?: {
        select_export_folder: (
          currentPath?: string
        ) => Promise<string | null>;
        get_update_status?: () => Promise<DesktopUpdateStatus>;
        apply_update_and_restart?: () => Promise<boolean>;
        restart_app?: () => Promise<boolean>;
      };
    };
  }
}

type DesktopUpdateStatus = {
  supported: boolean;
  available: boolean;
  built_at: string | null;
};

type WaveformSlice = {
  url: string;
  startMs: number;
  endMs: number;
};

const lastProjectStorageKey = "subtitle-studio:last-project";
const sidebarWidthStorageKey = "subtitle-studio:sidebar-width";
const stylePresetsStorageKey = "subtitle-studio:style-presets";
const appFontScaleStorageKey = "subtitle-studio:app-font-scale";

const stageLabels: Record<string, string> = {
  draft: "Draft",
  media_ready: "Media ready",
  transcribed: "Raw transcript",
  speakers_detected: "Speakers detected",
  corrected_pass_1: "Local correction",
  corrected: "Episode consistency",
  translated: "English ready",
  queued: "Queued",
  preparing_model: "Downloading Whisper model",
  transcribing: "Transcribing Korean",
  preparing_diarization: "Preparing speaker model",
  diarizing: "Detecting speakers",
  saving_speaker_turns: "Saving speaker turns",
  correcting_pass_1: "Correcting nearby context",
  correcting_pass_2: "Checking episode consistency",
  translating: "Writing conversational English",
  exporting_video: "Exporting captioned video",
  video_exported: "Video ready",
  failed: "Needs attention",
  cancelled: "Cancelled"
};

const terminalJobStages = [
  "failed",
  "cancelled",
  "transcribed",
  "speakers_detected",
  "corrected_pass_1",
  "corrected",
  "translated",
  "video_exported"
];

function isJobTerminal(job: Job) {
  return (
    terminalJobStages.includes(job.stage) &&
    !(job.pipeline && !job.pipeline_completed)
  );
}

const defaultSubtitleStyle: SubtitleStyle = {
  font_family: "Pretendard",
  font_size: 48,
  font_weight: "bold",
  font_style: "normal",
  text_color: "#FFFFFF",
  letter_spacing: 0,
  line_spacing: 1.2,
  max_words_per_line: 8,
  max_lines: 1,
  alignment: "center",
  position: "bottom",
  max_width_percent: 72,
  margin_vertical: 54,
  background_enabled: true,
  background_color: "#20211F",
  background_opacity: 0.88,
  background_padding_x: 20,
  background_padding_y: 10,
  background_radius: 4,
  outline_color: "#000000",
  outline_size: 0,
  shadow_size: 0
};

type LegacySubtitleStylePreset = {
  presetId: string;
  name: string;
  style: SubtitleStyle;
};

function clampSidebarWidth(width: number) {
  const viewportMaximum =
    typeof window === "undefined"
      ? 4_000
      : Math.max(245, window.innerWidth - 320);
  return Math.round(Math.max(245, Math.min(viewportMaximum, width)));
}

function clampAppFontScale(scale: number) {
  return Math.max(0.75, Math.min(2, Math.round(scale * 20) / 20));
}

function loadLegacySubtitleStylePresets(): LegacySubtitleStylePreset[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(stylePresetsStorageKey) ?? "[]"
    );
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item) =>
          item &&
          typeof item.presetId === "string" &&
          typeof item.name === "string" &&
          item.style &&
          typeof item.style === "object"
      )
      .map((item) => ({
        presetId: item.presetId,
        name: item.name,
        style: { ...defaultSubtitleStyle, ...item.style }
      }));
  } catch {
    return [];
  }
}

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

const timelineRulerIntervalsMs = [
  10,
  20,
  50,
  100,
  200,
  500,
  1_000,
  2_000,
  5_000,
  10_000,
  15_000,
  30_000,
  60_000,
  120_000,
  300_000,
  600_000,
  900_000,
  1_800_000,
  3_600_000
];

function timelineRulerInterval(visibleDurationMs: number) {
  const targetInterval = visibleDurationMs / 8;
  return (
    timelineRulerIntervalsMs.find(
      (interval) => interval >= targetInterval
    ) ??
    Math.ceil(targetInterval / 3_600_000) * 3_600_000
  );
}

function formatTimelineRulerTime(
  milliseconds: number,
  intervalMs: number
) {
  if (intervalMs >= 1_000) return formatTimestamp(milliseconds);
  const wholeMilliseconds = Math.max(0, Math.round(milliseconds));
  const totalSeconds = Math.floor(wholeMilliseconds / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const fractionDigits = intervalMs >= 100 ? 1 : 2;
  const fraction = String(wholeMilliseconds % 1_000)
    .padStart(3, "0")
    .slice(0, fractionDigits);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(
    2,
    "0"
  )}.${fraction}`;
}

function formatPreciseTime(milliseconds: number) {
  const wholeMilliseconds = Math.max(0, Math.round(milliseconds));
  const totalSeconds = Math.floor(wholeMilliseconds / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const fraction = String(wholeMilliseconds % 1_000).padStart(3, "0");
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(
    2,
    "0"
  )}.${fraction}`;
}

function formatDuration(milliseconds: number) {
  const totalMinutes = Math.round(milliseconds / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours}h ${minutes}m selected` : `${minutes}m selected`;
}

function formatEta(milliseconds: number) {
  const totalMinutes = Math.max(1, Math.ceil(milliseconds / 60_000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours}h ${minutes}m remaining` : `${minutes}m remaining`;
}

function etaClock(milliseconds: number) {
  return new Date(Date.now() + milliseconds).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit"
  });
}

function colorWithOpacity(hex: string, opacity: number) {
  const value = hex.replace("#", "");
  const red = Number.parseInt(value.slice(0, 2), 16);
  const green = Number.parseInt(value.slice(2, 4), 16);
  const blue = Number.parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${opacity})`;
}

function paginatePreviewCaption(
  text: string,
  maxWordsPerLine: number,
  maxLines: number,
  availableWidth: number,
  style: SubtitleStyle,
  fontSize: number,
  letterSpacing: number
) {
  if (availableWidth <= 0 || typeof document === "undefined") {
    return paginateCaptionByWords(text, maxWordsPerLine, maxLines);
  }
  const context = document.createElement("canvas").getContext("2d");
  if (!context) {
    return paginateCaptionByWords(text, maxWordsPerLine, maxLines);
  }
  context.font =
    `${style.font_style} ${style.font_weight} ${fontSize}px ` +
    `"${style.font_family}", Pretendard, "Malgun Gothic", Arial, sans-serif`;
  return paginateCaptionToWidth(
    text,
    maxWordsPerLine,
    maxLines,
    availableWidth,
    (line) => {
      const spacingWidth = Math.max(0, line.length - 1) * letterSpacing;
      return context.measureText(line).width + spacingWidth;
    }
  );
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [showScheduled, setShowScheduled] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);
  const [appFontScale, setAppFontScale] = useState(1);
  const [savedSidebarWidth, setSavedSidebarWidth] = useState(245);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const updateAppFontScale = useCallback((scale: number) => {
    const next = clampAppFontScale(scale);
    setAppFontScale(next);
    void api
      .updateAppPreferences({ app_font_scale: next })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const updateSavedSidebarWidth = useCallback((width: number) => {
    const next = clampSidebarWidth(width);
    setSavedSidebarWidth(next);
    void api
      .updateAppPreferences({ sidebar_width: next })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    document.documentElement.style.setProperty(
      "--app-font-scale",
      String(appFontScale)
    );
    return () => {
      document.documentElement.style.removeProperty("--app-font-scale");
    };
  }, [appFontScale]);

  const refreshProjects = useCallback(async () => {
    const next = await api.projects();
    setProjects(next);
    return next;
  }, []);

  const openProject = useCallback((projectId: string) => {
    setSelectedId(projectId);
    void api
      .updateAppPreferences({ last_project_id: projectId })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    async function loadApp() {
      try {
        const [nextProjects, status, storedPreferences] = await Promise.all([
          refreshProjects(),
          api.runtime(),
          api.appPreferences()
        ]);
        const preferencesPatch: Parameters<
          typeof api.updateAppPreferences
        >[0] = {};
        const legacyFontScale = window.localStorage.getItem(
          appFontScaleStorageKey
        );
        const legacySidebarWidth = window.localStorage.getItem(
          sidebarWidthStorageKey
        );
        const legacyProjectId = window.localStorage.getItem(
          lastProjectStorageKey
        );
        const legacyConnectionDismissed = window.localStorage.getItem(
          "subtitle-studio:connection-dismissed"
        );
        const parsedFontScale = Number(legacyFontScale);
        const parsedSidebarWidth = Number(legacySidebarWidth);
        if (legacyFontScale !== null && Number.isFinite(parsedFontScale)) {
          preferencesPatch.app_font_scale =
            clampAppFontScale(parsedFontScale);
        }
        if (
          legacySidebarWidth !== null &&
          Number.isFinite(parsedSidebarWidth)
        ) {
          preferencesPatch.sidebar_width =
            clampSidebarWidth(parsedSidebarWidth);
        }
        if (legacyProjectId) {
          preferencesPatch.last_project_id = legacyProjectId;
        }
        if (legacyConnectionDismissed !== null) {
          preferencesPatch.connection_dismissed =
            legacyConnectionDismissed === "true";
        }
        const preferences = Object.keys(preferencesPatch).length
          ? await api.updateAppPreferences(preferencesPatch)
          : storedPreferences;
        window.localStorage.removeItem(appFontScaleStorageKey);
        window.localStorage.removeItem(sidebarWidthStorageKey);
        window.localStorage.removeItem(lastProjectStorageKey);
        window.localStorage.removeItem(
          "subtitle-studio:connection-dismissed"
        );
        setRuntime(status);
        setAppFontScale(clampAppFontScale(preferences.app_font_scale));
        setSavedSidebarWidth(
          clampSidebarWidth(preferences.sidebar_width)
        );
        const rememberedProject = preferences.last_project_id;
        if (
          rememberedProject &&
          nextProjects.some(
            (project) => project.project_id === rememberedProject
          )
        ) {
          setSelectedId(rememberedProject);
        } else if (rememberedProject) {
          await api.updateAppPreferences({ last_project_id: null });
        }
        if (
          !status.openrouter_configured &&
          !preferences.connection_dismissed
        ) {
          setConnecting(true);
        }
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load Subtitle Studio."
        );
      } finally {
        setLoading(false);
      }
    }
    void loadApp();
  }, [refreshProjects]);

  if (loading) return <LoadingShell />;

  if (selectedId) {
    return (
      <>
        <Editor
          projectId={selectedId}
          runtime={runtime}
          settingsRevision={settingsRevision}
          savedSidebarWidth={savedSidebarWidth}
          onSidebarWidthChange={updateSavedSidebarWidth}
          onOpenSettings={() => setConnecting(true)}
          onBack={async () => {
            await api.updateAppPreferences({ last_project_id: null });
            await refreshProjects();
            setSelectedId(null);
          }}
        />
        {connecting ? (
          <OpenRouterModal
            configured={runtime?.openrouter_configured ?? false}
            projectId={selectedId}
            appFontScale={appFontScale}
            onAppFontScaleChange={updateAppFontScale}
            onVoiceDataChanged={() =>
              setSettingsRevision((current) => current + 1)
            }
            onClose={() => {
              void api.updateAppPreferences({
                connection_dismissed: true
              });
              setConnecting(false);
            }}
            onConnected={async () => {
              await api.updateAppPreferences({
                connection_dismissed: false
              });
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
            onClick={() => setShowScheduled(true)}
          >
            <PaperPlaneTiltIcon size={15} weight="bold" />
            Scheduled posts
          </button>
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
                  onClick={() => openProject(project.project_id)}
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
              label="Speaker detection"
              ready={
                (runtime?.diarization ?? false) &&
                (runtime?.diarization_configured ?? false)
              }
              detail={
                runtime?.diarization_configured
                  ? runtime.diarization_model.split("/").pop()
                  : "Hugging Face token needed"
              }
            />
            <RuntimeLine
              label="OpenRouter"
              ready={runtime?.openrouter_configured ?? false}
              detail={runtime?.openrouter_configured ? "Configured" : "API key needed"}
            />
          </div>
          <p>Correction and translation send transcript text to OpenRouter.</p>
        </aside>
      </main>

      {showScheduled ? (
        <div className="sched-overlay">
          <ScheduledPostsPanel onClose={() => setShowScheduled(false)} />
        </div>
      ) : null}

      {creating ? (
        <NewProjectModal
          onClose={() => setCreating(false)}
          onCreate={async (data) => {
            const project = await api.createProject(data);
            setProjects((current) => [project, ...current]);
            setCreating(false);
            openProject(project.project_id);
          }}
        />
      ) : null}
      {connecting ? (
        <OpenRouterModal
          configured={runtime?.openrouter_configured ?? false}
          appFontScale={appFontScale}
          onAppFontScaleChange={updateAppFontScale}
          onClose={() => {
            void api.updateAppPreferences({ connection_dismissed: true });
            setConnecting(false);
          }}
          onConnected={async () => {
            await api.updateAppPreferences({
              connection_dismissed: false
            });
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
  projectId,
  appFontScale,
  onClose,
  onConnected,
  onAppFontScaleChange,
  onVoiceDataChanged
}: {
  configured: boolean;
  projectId?: string;
  appFontScale: number;
  onClose: () => void;
  onConnected: () => Promise<void>;
  onAppFontScaleChange: (scale: number) => void;
  onVoiceDataChanged?: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [huggingfaceToken, setHuggingfaceToken] = useState("");
  const [speakerDetectionConfigured, setSpeakerDetectionConfigured] =
    useState(false);
  const [speakerDetectionModel, setSpeakerDetectionModel] = useState(
    "pyannote/speaker-diarization-community-1"
  );
  const [correctionModel, setCorrectionModel] = useState("");
  const [translationModel, setTranslationModel] = useState("");
  const [postCopyModel, setPostCopyModel] = useState("");
  const [videoExportFolder, setVideoExportFolder] = useState("");
  const [defaultVideoExportFolder, setDefaultVideoExportFolder] = useState("");
  const [videoExportFolderIsDefault, setVideoExportFolderIsDefault] =
    useState(true);
  const [models, setModels] = useState<OpenRouterModel[]>([]);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [loadingModels, setLoadingModels] = useState(true);
  const [saving, setSaving] = useState(false);
  const [choosingExportFolder, setChoosingExportFolder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
  const [projectSpeakers, setProjectSpeakers] = useState<Speaker[]>([]);
  const [desktopUpdate, setDesktopUpdate] =
    useState<DesktopUpdateStatus | null>(null);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    let mounted = true;
    const getStatus = window.pywebview?.api?.get_update_status;
    if (!getStatus) return;
    getStatus()
      .then((status) => {
        if (mounted) setDesktopUpdate(status);
      })
      .catch(() => {
        if (mounted) setDesktopUpdate(null);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    api.openRouterSettings()
      .then((settings) => {
        if (!mounted) return;
        setCorrectionModel(settings.correction_model);
        setTranslationModel(settings.translation_model);
        setPostCopyModel(settings.post_copy_model);
      })
      .catch((reason: Error) => {
        if (mounted) setError(reason.message);
      })
      .finally(() => {
        if (mounted) setLoadingSettings(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    Promise.all([
      api.voiceProfiles(),
      projectId ? api.speakers(projectId) : Promise.resolve([])
    ])
      .then(([profiles, speakers]) => {
        if (!mounted) return;
        setVoiceProfiles(profiles);
        setProjectSpeakers(speakers);
      })
      .catch((reason: Error) => {
        if (mounted) setError(reason.message);
      });
    return () => {
      mounted = false;
    };
  }, [projectId]);

  useEffect(() => {
    let mounted = true;
    api.videoExportFolderSettings()
      .then((settings) => {
        if (!mounted) return;
        setVideoExportFolder(settings.path);
        setDefaultVideoExportFolder(settings.default_path);
        setVideoExportFolderIsDefault(settings.is_default);
      })
      .catch((reason: Error) => {
        if (mounted) setError(reason.message);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    api.speakerDetectionSettings()
      .then((settings) => {
        if (mounted) {
          setSpeakerDetectionConfigured(settings.configured);
          setSpeakerDetectionModel(settings.model);
        }
      })
      .catch((reason: Error) => {
        if (mounted) setError(reason.message);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    api.openRouterModels()
      .then((availableModels) => {
        if (mounted) setModels(availableModels);
      })
      .catch((reason: Error) => {
        if (mounted) setModelsError(reason.message);
      })
      .finally(() => {
        if (mounted) setLoadingModels(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  async function chooseVideoExportFolder() {
    const desktopApi = window.pywebview?.api;
    if (!desktopApi?.select_export_folder) {
      setError("Folder selection is available in the installed desktop app.");
      return;
    }
    setChoosingExportFolder(true);
    setError(null);
    try {
      const selected = await desktopApi.select_export_folder(
        videoExportFolder
      );
      if (!selected) return;
      const settings = await api.saveVideoExportFolder(selected);
      setVideoExportFolder(settings.path);
      setDefaultVideoExportFolder(settings.default_path);
      setVideoExportFolderIsDefault(settings.is_default);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not change the video export folder."
      );
    } finally {
      setChoosingExportFolder(false);
    }
  }

  async function resetVideoExportFolder() {
    setChoosingExportFolder(true);
    setError(null);
    try {
      const settings = await api.resetVideoExportFolder();
      setVideoExportFolder(settings.path);
      setDefaultVideoExportFolder(settings.default_path);
      setVideoExportFolderIsDefault(settings.is_default);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not reset the video export folder."
      );
    } finally {
      setChoosingExportFolder(false);
    }
  }

  async function restartDesktopApp() {
    const desktopApi = window.pywebview?.api;
    const restart = desktopUpdate?.available
      ? desktopApi?.apply_update_and_restart
      : desktopApi?.restart_app;
    if (!restart) {
      setError("Restart is available in the installed desktop app.");
      return;
    }
    setRestarting(true);
    setError(null);
    try {
      await restart();
    } catch (reason) {
      setRestarting(false);
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not restart Subtitle Studio."
      );
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.saveOpenRouterSettings({
        apiKey: apiKey.trim() || undefined,
        correctionModel: correctionModel.trim(),
        translationModel: translationModel.trim(),
        postCopyModel: postCopyModel.trim()
      });
      if (huggingfaceToken.trim()) {
        await api.saveSpeakerDetectionSettings(huggingfaceToken.trim());
      }
      await onConnected();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the connection.");
      setSaving(false);
    }
  }

  const keyRequired = !configured;
  const canSubmit =
    !saving &&
    !loadingSettings &&
    (!keyRequired || !!apiKey.trim()) &&
    !!correctionModel.trim() &&
    !!translationModel.trim() &&
    !!postCopyModel.trim();
  const primaryLabel = saving
    ? "Saving..."
    : configured
      ? "Save settings"
      : "Connect & continue";

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <form
        className="new-project-modal settings-modal"
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">{configured ? "Settings" : "One-time setup"}</span>
            <h2>{configured ? "Subtitle Studio settings." : "Connect the language engine."}</h2>
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
        {desktopUpdate?.supported ? (
          <div className="settings-section app-update-settings">
            <div>
              <strong>
                {desktopUpdate.available ? "New app build ready" : "Restart Subtitle Studio"}
              </strong>
              <small>
                {desktopUpdate.available
                  ? "Apply the waiting build and reopen the app automatically. Your projects and settings stay in place."
                  : "Close and reopen the app automatically without hunting for the shortcut."}
              </small>
            </div>
            <button
              type="button"
              className={desktopUpdate.available ? "primary" : "secondary"}
              disabled={restarting}
              onClick={() => void restartDesktopApp()}
            >
              <ArrowsClockwiseIcon size={16} />
              {restarting
                ? "Restarting..."
                : desktopUpdate.available
                  ? "Apply update & restart"
                  : "Restart app"}
            </button>
          </div>
        ) : null}
        <div className="settings-section app-font-size-settings">
          <div className="app-font-size-heading">
            <strong>App font size</strong>
            <output>{Math.round(appFontScale * 100)}%</output>
          </div>
          <div className="app-font-size-control">
            <input
              type="range"
              min={75}
              max={200}
              step={5}
              value={Math.round(appFontScale * 100)}
              aria-label="App font size"
              aria-valuetext={`${Math.round(appFontScale * 100)} percent`}
              onChange={(event) =>
                onAppFontScaleChange(Number(event.target.value) / 100)
              }
            />
            <button
              type="button"
              className="icon-button"
              aria-label="Reset app font size"
              title="Reset font size to 100%"
              disabled={appFontScale === 1}
              onClick={() => onAppFontScaleChange(1)}
            >
              <ArrowsClockwiseIcon size={15} />
            </button>
          </div>
        </div>
        <div className="settings-section export-folder-settings">
          <div>
            <strong>Video export folder</strong>
            <small>
              Finished MP4 files are saved directly here. No extra download
              step.
            </small>
          </div>
          <div className="export-folder-picker">
            <span title={videoExportFolder}>
              <FolderOpenIcon size={16} />
              <code>{videoExportFolder || defaultVideoExportFolder}</code>
            </span>
            <button
              type="button"
              className="secondary"
              disabled={choosingExportFolder}
              onClick={() => void chooseVideoExportFolder()}
            >
              <FolderOpenIcon size={15} />
              {choosingExportFolder ? "Choosing..." : "Choose folder"}
            </button>
          </div>
          {!videoExportFolderIsDefault ? (
            <button
              type="button"
              className="settings-reset-folder"
              disabled={choosingExportFolder}
              onClick={() => void resetVideoExportFolder()}
            >
              Reset to {defaultVideoExportFolder}
            </button>
          ) : null}
        </div>
        <label>
          OpenRouter API key
          <input
            autoFocus
            required={!configured}
            type="password"
            autoComplete="off"
            disabled={saving || loadingSettings}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={configured ? "Enter a replacement key" : "sk-or-v1-…"}
          />
          <small>
            Create a key at{" "}
            <a href="https://openrouter.ai/settings/keys" target="_blank" rel="noreferrer">
              openrouter.ai/settings/keys
            </a>
            . {configured
              ? "Leave blank to keep the current key."
              : "This key is required before AI stages can run."}
          </small>
        </label>
        <div className="settings-section">
          <div>
            <strong>Local speaker detection</strong>
            <small>
              Pyannote {speakerDetectionModel.split("/").pop()} separates voices
              before correction.
            </small>
          </div>
          <label>
            Hugging Face access token
            <input
              type="password"
              autoComplete="off"
              disabled={saving || loadingSettings}
              value={huggingfaceToken}
              onChange={(event) => setHuggingfaceToken(event.target.value)}
              placeholder={
                speakerDetectionConfigured
                  ? "Enter a replacement token"
                  : "hf_..."
              }
            />
            <small>
              First accept the{" "}
              <a
                href={`https://huggingface.co/${speakerDetectionModel}`}
                target="_blank"
                rel="noreferrer"
              >
                selected model terms
              </a>
              , then create a token at{" "}
              <a
                href="https://huggingface.co/settings/tokens"
                target="_blank"
                rel="noreferrer"
              >
                huggingface.co/settings/tokens
              </a>
              . {speakerDetectionConfigured
                ? "Leave blank to keep the current token."
                : "The model downloads once and then runs locally."}
            </small>
          </label>
        </div>
        <div className="settings-section voice-settings-section">
          <SpeakerPanel
            speakers={projectSpeakers}
            voiceProfiles={voiceProfiles}
            disabled={saving}
            showEpisodeSpeakers={!!projectId}
            onCreateProfile={async (name, sample) => {
              const created = await api.createVoiceProfile(name, sample);
              setVoiceProfiles((current) => [...current, created]);
              onVoiceDataChanged?.();
            }}
            onDeleteProfile={async (profileId) => {
              await api.deleteVoiceProfile(profileId);
              setVoiceProfiles((current) =>
                current.filter(
                  (profile) => profile.profile_id !== profileId
                )
              );
              onVoiceDataChanged?.();
            }}
            onRename={async (speakerId, name) => {
              if (!projectId) return;
              const updated = await api.renameSpeaker(
                projectId,
                speakerId,
                name
              );
              setProjectSpeakers((current) =>
                current.map((speaker) =>
                  speaker.speaker_id === updated.speaker_id
                    ? updated
                    : speaker
                )
              );
              onVoiceDataChanged?.();
            }}
          />
        </div>
        {modelsError ? (
          <>
            <div className="model-catalog-note">
              The live model list could not be loaded. You can still use an
              OpenRouter model ID.
            </div>
            <div className="model-settings-grid">
              <label>
                Correction model
                <input
                  required
                  disabled={saving || loadingSettings}
                  value={correctionModel}
                  onChange={(event) => setCorrectionModel(event.target.value)}
                />
              </label>
              <label>
                Translation model
                <input
                  required
                  disabled={saving || loadingSettings}
                  value={translationModel}
                  onChange={(event) => setTranslationModel(event.target.value)}
                />
              </label>
              <label>
                Post copy model
                <input
                  required
                  disabled={saving || loadingSettings}
                  value={postCopyModel}
                  onChange={(event) => setPostCopyModel(event.target.value)}
                />
              </label>
            </div>
          </>
        ) : (
          <div className="model-picker-stack">
            <ModelPicker
              label="Correction model"
              value={correctionModel}
              models={models}
              loading={loadingModels}
              disabled={saving || loadingSettings}
              onChange={setCorrectionModel}
            />
            <ModelPicker
              label="Translation model"
              value={translationModel}
              models={models}
              loading={loadingModels}
              disabled={saving || loadingSettings}
              onChange={setTranslationModel}
            />
            <ModelPicker
              label="Post copy model"
              value={postCopyModel}
              models={models}
              loading={loadingModels}
              disabled={saving || loadingSettings}
              onChange={setPostCopyModel}
            />
          </div>
        )}
        {error ? <InlineError message={error} /> : null}
        <div className="modal-actions split">
          <button type="button" className="secondary" onClick={onClose}>
            {configured ? "Cancel" : "I'll do this later"}
          </button>
          <button className="primary" disabled={!canSubmit}>
            {primaryLabel}
            <ArrowRightIcon size={17} />
          </button>
        </div>
      </form>
    </div>
  );
}

const providerNames: Record<string, string> = {
  "meta-llama": "Meta",
  "mistralai": "Mistral AI",
  "x-ai": "xAI",
  anthropic: "Anthropic",
  cohere: "Cohere",
  deepseek: "DeepSeek",
  google: "Google",
  microsoft: "Microsoft",
  nvidia: "NVIDIA",
  openai: "OpenAI",
  qwen: "Qwen"
};

function providerName(provider: string) {
  const isLatestAlias = provider.startsWith("~");
  const providerId = isLatestAlias ? provider.slice(1) : provider;
  const name = providerNames[providerId] ??
    providerId
      .split("-")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  return isLatestAlias ? `${name} latest aliases` : name;
}

function modelAddedDate(created: number) {
  if (!created) return "Date unavailable";
  return `Added ${new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(created * 1000))}`;
}

function modelContext(tokens: number) {
  if (!tokens) return "Context unavailable";
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return `${millions.toFixed(Number.isInteger(millions) ? 0 : 1)}M context`;
  }
  return `${Math.round(tokens / 1000)}K context`;
}

function modelIsFree(model: OpenRouterModel) {
  const prices = [
    model.prompt_price,
    model.completion_price,
    model.request_price
  ];
  return (
    model.model_id.endsWith(":free") ||
    (prices.every((price) => price.trim() !== "") &&
      prices.every((price) => Number(price) === 0))
  );
}

function modelPrice(model: OpenRouterModel) {
  if (modelIsFree(model)) return "Free";
  if (!model.prompt_price || !model.completion_price) {
    return "Pricing unavailable";
  }
  const prompt = Number(model.prompt_price) * 1_000_000;
  const completion = Number(model.completion_price) * 1_000_000;
  if (!Number.isFinite(prompt) || !Number.isFinite(completion)) {
    return "Pricing unavailable";
  }
  const format = (price: number) =>
    price < 0.01 ? price.toFixed(3) : price.toFixed(price < 1 ? 2 : 1);
  return `$${format(prompt)} in / $${format(completion)} out per 1M`;
}

function ModelPicker({
  label,
  value,
  models,
  loading,
  disabled,
  onChange
}: {
  label: string;
  value: string;
  models: OpenRouterModel[];
  loading: boolean;
  disabled: boolean;
  onChange: (modelId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<"providers" | "newest" | "free">("providers");
  const selected = models.find((model) => model.model_id === value);

  const visibleModels = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const filtered = normalizedQuery
      ? models.filter((model) =>
          [model.name, model.model_id, providerName(model.provider)]
            .join(" ")
            .toLowerCase()
            .includes(normalizedQuery)
        )
      : models;
    return [...filtered].sort((left, right) =>
      right.created - left.created || left.name.localeCompare(right.name)
    );
  }, [models, query]);

  const latestByProvider = useMemo(() => {
    const providers = new Set<string>();
    const latestModels = new Set<string>();
    for (const model of [...models].sort((left, right) => right.created - left.created)) {
      if (!providers.has(model.provider)) {
        providers.add(model.provider);
        latestModels.add(model.model_id);
      }
    }
    return latestModels;
  }, [models]);

  const providerGroups = useMemo(() => {
    const groups = new Map<string, OpenRouterModel[]>();
    for (const model of visibleModels) {
      const group = groups.get(model.provider) ?? [];
      group.push(model);
      groups.set(model.provider, group);
    }
    return [...groups.entries()].sort(([left], [right]) => {
      const aliasOrder =
        Number(left.startsWith("~")) - Number(right.startsWith("~"));
      return aliasOrder || providerName(left).localeCompare(providerName(right));
    });
  }, [visibleModels]);
  const listedModels =
    view === "free" ? visibleModels.filter(modelIsFree) : visibleModels;

  function choose(modelId: string) {
    onChange(modelId);
    setOpen(false);
    setQuery("");
  }

  function modelOption(model: OpenRouterModel) {
    return (
      <button
        type="button"
        className={`model-option ${model.model_id === value ? "selected" : ""}`}
        key={model.model_id}
        role="option"
        aria-selected={model.model_id === value}
        onClick={() => choose(model.model_id)}
      >
        <span className="model-option-main">
          <strong>{model.name}</strong>
          {latestByProvider.has(model.model_id) ? (
            <span className="latest-badge">Latest</span>
          ) : null}
          {modelIsFree(model) ? <span className="free-badge">Free</span> : null}
        </span>
        <span className="model-option-meta">
          {providerName(model.provider)} · {modelAddedDate(model.created)} ·{" "}
          {modelContext(model.context_length)} · {modelPrice(model)}
        </span>
      </button>
    );
  }

  return (
    <div className={`model-picker ${open ? "open" : ""}`}>
      <span className="model-picker-label">{label}</span>
      <button
        type="button"
        className="model-picker-trigger"
        disabled={disabled || loading}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span>
          <strong>
            {loading ? "Loading OpenRouter models..." : selected?.name ?? value}
          </strong>
          {!loading && value ? (
            <small>
              {selected
                ? `${providerName(selected.provider)} · ${modelAddedDate(selected.created)}`
                : value}
            </small>
          ) : null}
        </span>
        <CaretDownIcon size={16} />
      </button>
      {open ? (
        <div className="model-picker-panel">
          <div className="model-picker-tools">
            <label className="model-search">
              <MagnifyingGlassIcon size={15} />
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search models or providers"
              />
            </label>
            <div className="model-view-switch" aria-label="Model list order">
              <button
                type="button"
                className={view === "providers" ? "active" : ""}
                onClick={() => setView("providers")}
              >
                Providers
              </button>
              <button
                type="button"
                className={view === "newest" ? "active" : ""}
                onClick={() => setView("newest")}
              >
                Newest
              </button>
              <button
                type="button"
                className={view === "free" ? "active" : ""}
                onClick={() => setView("free")}
              >
                Free
              </button>
            </div>
          </div>
          <div className="model-option-list" role="listbox">
            {listedModels.length ? (
              view !== "providers" ? (
                listedModels.map(modelOption)
              ) : (
                providerGroups.map(([provider, providerModels]) => (
                  <section className="model-provider-group" key={provider}>
                    <div className="model-provider-heading">
                      <strong>{providerName(provider)}</strong>
                      <span>{providerModels.length}</span>
                    </div>
                    {providerModels.map(modelOption)}
                  </section>
                ))
              )
            ) : (
              <div className="model-empty">No matching models.</div>
            )}
          </div>
        </div>
      ) : null}
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
  settingsRevision,
  savedSidebarWidth,
  onSidebarWidthChange,
  onOpenSettings,
  onBack
}: {
  projectId: string;
  runtime: RuntimeStatus | null;
  settingsRevision: number;
  savedSidebarWidth: number;
  onSidebarWidthChange: (width: number) => void;
  onOpenSettings: () => void;
  onBack: () => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [captionTrack, setCaptionTrack] = useState<CaptionTrack | null>(null);
  const [speakers, setSpeakers] = useState<Speaker[]>([]);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
  const [glossary, setGlossary] = useState<GlossaryEntry[]>([]);
  const [clips, setClips] = useState<TimestampClip[]>([]);
  const [markers, setMarkers] = useState<NavigationMarker[]>([]);
  const [postCopies, setPostCopies] = useState<PostCopy[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [activeClipId, setActiveClipId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [warningOnly, setWarningOnly] = useState(false);
  const [sidebarTab, setSidebarTab] =
    useState<WorkspaceSidebarTab>("timestamps");
  const [sidebarWidth, setSidebarWidth] = useState(() =>
    clampSidebarWidth(savedSidebarWidth)
  );
  const [resizingSidebar, setResizingSidebar] = useState(false);
  const [mediaPreparation, setMediaPreparation] =
    useState<MediaPreparation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [currentMs, setCurrentMs] = useState(0);
  const [timelineZoom, setTimelineZoom] = useState(1);
  const [timelineInfo, setTimelineInfo] = useState<MediaTimelineInfo>({
    frame_rate: 30,
    waveform_url: null
  });
  const [displayedWaveform, setDisplayedWaveform] =
    useState<WaveformSlice | null>(null);
  const [previousWaveform, setPreviousWaveform] =
    useState<WaveformSlice | null>(null);
  const [failedWaveformUrl, setFailedWaveformUrl] =
    useState<string | null>(null);
  const [waveformRange, setWaveformRange] = useState<{
    startMs: number;
    endMs: number;
  } | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [scrubFocusMs, setScrubFocusMs] = useState<number | null>(null);
  const [boundaryDrag, setBoundaryDrag] = useState<{
    clipId: string;
    edge: "start" | "end";
    originalStartMs: number;
    originalEndMs: number;
    viewportCenterMs: number;
  } | null>(null);
  const [etaMs, setEtaMs] = useState<number | null>(null);
  const [styleSaving, setStyleSaving] = useState(false);
  const [captionsGenerating, setCaptionsGenerating] = useState(false);
  const [generatingPostCopyIds, setGeneratingPostCopyIds] = useState<
    string[]
  >([]);
  const [videoExportOpen, setVideoExportOpen] = useState(false);
  const [videoResolution, setVideoResolution] =
    useState<"1080p" | "source">("1080p");
  const [videoQuality, setVideoQuality] =
    useState<"high" | "maximum">("maximum");
  const [videoEncoder, setVideoEncoder] =
    useState<"gpu" | "cpu">("gpu");
  const [includeVideoExport, setIncludeVideoExport] = useState(true);
  const [includeSrtExport, setIncludeSrtExport] = useState(false);
  const [includeAssExport, setIncludeAssExport] = useState(false);
  const [videoExportClipIds, setVideoExportClipIds] = useState<string[]>([]);
  const [clearingRenderQueue, setClearingRenderQueue] = useState(false);
  const [latestVideoExport, setLatestVideoExport] = useState<Job | null>(null);
  const [completedVideoExport, setCompletedVideoExport] = useState<Job | null>(
    null
  );
  const [videoBounds, setVideoBounds] = useState({
    left: 0,
    top: 0,
    width: 0,
    height: 290
  });
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const editorGridRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const mediaViewportRef = useRef<HTMLDivElement | null>(null);
  const transcriptScrollRef = useRef<HTMLDivElement | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const timelineScrubbingRef = useRef(false);
  const waveformFadeTimerRef = useRef<number | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const segmentRowRefs = useRef(new Map<string, HTMLDivElement>());
  const etaTrackerRef = useRef<{
    jobId: string;
    progress: number;
    measuredAt: number;
    rate: number | null;
  } | null>(null);
  const styleSaveTimerRef = useRef<number | null>(null);
  const captionGenerationRequestRef = useRef(0);
  const pendingCaptionLayoutRef = useRef<{
    clipId: string;
    maxWordsPerLine: number;
    maxLines: number;
  } | null>(null);
  const workspaceLoadedRef = useRef(false);
  const workspaceSaveTimerRef = useRef<number | null>(null);
  const workspaceSnapshotRef = useRef<ProjectWorkspaceState | null>(null);

  useEffect(() => {
    const fitSidebarToViewport = () => {
      setSidebarWidth((current) => {
        const next = clampSidebarWidth(current);
        if (next !== current) {
          onSidebarWidthChange(next);
        }
        return next;
      });
    };
    window.addEventListener("resize", fitSidebarToViewport);
    return () => window.removeEventListener("resize", fitSidebarToViewport);
  }, [onSidebarWidthChange]);

  const refresh = useCallback(async () => {
    const [
      nextProject,
      nextSegments,
      nextCaptionTrack,
      nextGlossary,
      nextClips,
      nextMarkers,
      nextSpeakers,
      nextVoiceProfiles,
      nextPostCopies,
      videoExports,
      activeJob
    ] = await Promise.all([
      api.project(projectId),
      api.segments(projectId),
      api.captionTrack(projectId),
      api.glossary(projectId),
      api.clips(projectId),
      api.markers(projectId),
      api.speakers(projectId),
      api.voiceProfiles(),
      api.postCopies(projectId),
      api.videoExports(projectId),
      api.activeJob(projectId)
    ]);
    setProject(nextProject);
    setSegments(nextSegments);
    setCaptionTrack(nextCaptionTrack);
    setGlossary(nextGlossary);
    setClips(nextClips);
    setMarkers(nextMarkers);
    setSpeakers(nextSpeakers);
    setVoiceProfiles(nextVoiceProfiles);
    setPostCopies(nextPostCopies);
    setLatestVideoExport(videoExports[0] ?? null);
    setJob(activeJob);
  }, [projectId]);

  useEffect(() => {
    if (settingsRevision === 0) return;
    void refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh, settingsRevision]);

  useEffect(() => {
    let cancelled = false;
    workspaceLoadedRef.current = false;
    if (workspaceSaveTimerRef.current !== null) {
      window.clearTimeout(workspaceSaveTimerRef.current);
      workspaceSaveTimerRef.current = null;
    }
    Promise.all([refresh(), api.workspace(projectId)])
      .then(([, storedWorkspace]) => {
        if (cancelled) return;
        const workspace = storedWorkspace;
        setActiveClipId(workspace.active_clip_id);
        setSelected(workspace.selected_segment_id);
        setSidebarTab(
          workspace.sidebar_tab === "speakers" ||
            workspace.sidebar_tab === "glossary"
            ? "timestamps"
            : workspace.sidebar_tab
        );
        setCurrentMs(workspace.playhead_ms);
        setPlaybackRate(workspace.playback_rate);
        setQuery(workspace.transcript_query);
        setWarningOnly(workspace.warning_only);
        setVideoResolution(workspace.video_resolution);
        setVideoQuality(workspace.video_quality);
        setVideoEncoder(workspace.video_encoder);
        setTimelineZoom(workspace.timeline_zoom ?? 1);
        const media = mediaRef.current;
        if (media && media.readyState >= 1) {
          media.playbackRate = workspace.playback_rate;
          media.currentTime = Math.min(
            media.duration || Number.POSITIVE_INFINITY,
            workspace.playhead_ms / 1_000
          );
        }
        workspaceLoadedRef.current = true;
      })
      .catch((reason: Error) => setError(reason.message));
    return () => {
      cancelled = true;
    };
  }, [projectId, refresh]);

  useEffect(() => {
    let cancelled = false;
    setDisplayedWaveform(null);
    setPreviousWaveform(null);
    setFailedWaveformUrl(null);
    setWaveformRange(null);
    if (waveformFadeTimerRef.current !== null) {
      window.clearTimeout(waveformFadeTimerRef.current);
      waveformFadeTimerRef.current = null;
    }
    if (!project?.media_url) {
      setTimelineInfo({ frame_rate: 30, waveform_url: null });
      return () => {
        cancelled = true;
      };
    }
    void api
      .timelineInfo(projectId)
      .then((info) => {
        if (!cancelled) setTimelineInfo(info);
      })
      .catch(() => {
        if (!cancelled) {
          setTimelineInfo({ frame_rate: 30, waveform_url: null });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [project?.media_hash, project?.media_url, projectId]);

  useEffect(
    () => () => {
      if (waveformFadeTimerRef.current !== null) {
        window.clearTimeout(waveformFadeTimerRef.current);
      }
    },
    []
  );

  const workspaceSnapshot = useMemo<ProjectWorkspaceState>(
    () => ({
      active_clip_id: activeClipId,
      selected_segment_id: selected,
      sidebar_tab: sidebarTab,
      playhead_ms: Math.max(0, Math.round(currentMs)),
      playback_rate: playbackRate,
      transcript_query: query,
      warning_only: warningOnly,
      video_resolution: videoResolution,
      video_quality: videoQuality,
      video_encoder: videoEncoder,
      timeline_zoom: timelineZoom
    }),
    [
      activeClipId,
      currentMs,
      playbackRate,
      query,
      selected,
      sidebarTab,
      timelineZoom,
      videoEncoder,
      videoQuality,
      videoResolution,
      warningOnly
    ]
  );

  useEffect(() => {
    workspaceSnapshotRef.current = workspaceSnapshot;
    if (!workspaceLoadedRef.current) return;
    if (workspaceSaveTimerRef.current !== null) {
      window.clearTimeout(workspaceSaveTimerRef.current);
    }
    workspaceSaveTimerRef.current = window.setTimeout(() => {
      void api
        .updateWorkspace(projectId, workspaceSnapshot)
        .catch((reason: Error) => setError(reason.message));
    }, 750);
  }, [projectId, workspaceSnapshot]);

  useEffect(
    () => () => {
      if (workspaceSaveTimerRef.current !== null) {
        window.clearTimeout(workspaceSaveTimerRef.current);
      }
      const latest = workspaceSnapshotRef.current;
      if (!workspaceLoadedRef.current || !latest) return;
      void api.updateWorkspace(projectId, latest);
    },
    [projectId]
  );

  useEffect(() => {
    const timer = window.setInterval(() => {
      const latest = workspaceSnapshotRef.current;
      if (!workspaceLoadedRef.current || !latest) return;
      void api
        .updateWorkspace(projectId, latest)
        .catch((reason: Error) => setError(reason.message));
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [projectId]);

  useEffect(() => {
    if (!job || isJobTerminal(job)) {
      return;
    }
    const timer = window.setInterval(async () => {
      try {
        const next = await api.job(job.job_id);
        setJob(next);
        if (next.stage === "failed") {
          setError(next.error ?? "The task failed. Check the connection and try again.");
          return;
        }
        if (next.stage === "video_exported" && next.output_url) {
          const exportedClipIds = new Set(
            next.outputs.map((output) => output.clip_id)
          );
          setClips((current) =>
            current.map((clip) =>
              exportedClipIds.has(clip.clip_id)
                ? { ...clip, render_queued: false }
                : clip
            )
          );
          setVideoExportClipIds((current) =>
            current.filter((clipId) => !exportedClipIds.has(clipId))
          );
          setLatestVideoExport(next);
          setCompletedVideoExport(next);
          return;
        }
        if (isJobTerminal(next)) {
          await refresh();
        }
      } catch (reason) {
        setError(
          reason instanceof Error ? reason.message : "Could not check the task status."
        );
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job, refresh]);

  useEffect(() => {
    if (
      !job ||
      isJobTerminal(job)
    ) {
      etaTrackerRef.current = null;
      setEtaMs(null);
      return;
    }
    const now = Date.now();
    const tracker = etaTrackerRef.current;
    if (!tracker || tracker.jobId !== job.job_id) {
      etaTrackerRef.current = {
        jobId: job.job_id,
        progress: job.overall_progress ?? job.progress,
        measuredAt: now,
        rate: null
      };
      setEtaMs(null);
      return;
    }
    if (job.paused) {
      tracker.measuredAt = now;
      return;
    }
    const trackedProgress = job.overall_progress ?? job.progress;
    const progressDelta = trackedProgress - tracker.progress;
    const elapsedSeconds = (now - tracker.measuredAt) / 1000;
    if (progressDelta > 0.002 && elapsedSeconds > 0.2) {
      const measuredRate = progressDelta / elapsedSeconds;
      tracker.rate =
        tracker.rate === null
          ? measuredRate
          : tracker.rate * 0.65 + measuredRate * 0.35;
      tracker.progress = trackedProgress;
      tracker.measuredAt = now;
      const estimate = ((1 - trackedProgress) / tracker.rate) * 1000;
      setEtaMs(Number.isFinite(estimate) ? Math.max(0, estimate) : null);
    }
  }, [job]);

  useEffect(() => {
    const viewport = mediaViewportRef.current;
    if (!viewport) return;
    const video = videoRef.current;
    const measure = () => {
      const containerWidth = viewport.clientWidth;
      const containerHeight = viewport.clientHeight;
      setVideoBounds(
        containedMediaBounds(
          containerWidth,
          containerHeight,
          video?.videoWidth ?? 0,
          video?.videoHeight ?? 0
        )
      );
    };
    measure();
    video?.addEventListener("loadedmetadata", measure);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    observer?.observe(viewport);
    return () => {
      video?.removeEventListener("loadedmetadata", measure);
      observer?.disconnect();
    };
  }, [project?.media_url]);

  const openedClips = useMemo(
    () =>
      clips
        .filter((clip) => clip.opened)
        .sort((left, right) => left.start_ms - right.start_ms),
    [clips]
  );
  const activeClip = useMemo(
    () => clips.find((clip) => clip.clip_id === activeClipId) ?? null,
    [activeClipId, clips]
  );
  const playbackMarkerId = useMemo(() => {
    let currentMarker: NavigationMarker | null = null;
    for (const marker of markers) {
      if (marker.timestamp_ms > currentMs) break;
      currentMarker = marker;
    }
    return currentMarker?.marker_id ?? null;
  }, [currentMs, markers]);
  const clipsByMarkerId = useMemo(() => {
    const result = new Map<string, TimestampClip>();
    for (const marker of markers) {
      const clip =
        clips.find(
          (candidate) =>
            candidate.navigation_marker_id === marker.marker_id
        ) ??
        clips.find(
          (candidate) =>
            !candidate.navigation_marker_id &&
            candidate.title === marker.title
        );
      if (clip) result.set(marker.marker_id, clip);
    }
    return result;
  }, [clips, markers]);
  const queuedClips = useMemo(
    () =>
      clips
        .filter((clip) => clip.render_queued)
        .sort((left, right) => left.start_ms - right.start_ms),
    [clips]
  );
  const timelineDurationMs = Math.max(1, project?.duration_ms ?? 1);
  const frameRate = Math.max(
    1,
    Math.min(240, timelineInfo.frame_rate || 30)
  );
  const frameDurationMs = 1_000 / frameRate;
  const maxTimelineZoom = Math.max(
    20,
    Math.min(
      100_000,
      Math.ceil(timelineDurationMs / (frameDurationMs * 30))
    )
  );
  const effectiveTimelineZoom = Math.max(
    1,
    Math.min(maxTimelineZoom, timelineZoom)
  );
  const clipOverviewZoom = activeClip
    ? timelineZoomForClip(activeClip)
    : 1;
  const timelineFocusMs =
    boundaryDrag &&
    boundaryDrag.clipId === activeClip?.clip_id
      ? boundaryDrag.viewportCenterMs
      : scrubFocusMs ??
        (activeClip && effectiveTimelineZoom <= clipOverviewZoom
          ? (activeClip.start_ms + activeClip.end_ms) / 2
          : currentMs);
  const visibleTimeline = timelineViewport(
    timelineDurationMs,
    effectiveTimelineZoom,
    timelineFocusMs
  );
  const visibleTimelineStartMs = visibleTimeline.startMs;
  const visibleTimelineEndMs = visibleTimeline.endMs;
  const visibleTimelineDurationMs = visibleTimeline.durationMs;
  useEffect(() => {
    if (
      !playing ||
      scrubbing ||
      boundaryDrag ||
      scrubFocusMs === null ||
      effectiveTimelineZoom <= 1
    ) {
      return;
    }
    if (
      currentMs < visibleTimelineStartMs ||
      currentMs > visibleTimelineEndMs
    ) {
      setScrubFocusMs(currentMs);
    }
  }, [
    boundaryDrag,
    currentMs,
    effectiveTimelineZoom,
    playing,
    scrubFocusMs,
    scrubbing,
    visibleTimelineEndMs,
    visibleTimelineStartMs
  ]);
  const waveformWindowDurationMs = Math.min(
    timelineDurationMs,
    visibleTimelineDurationMs * 2
  );
  const waveformBucketMs = Math.max(
    frameDurationMs,
    visibleTimelineDurationMs / 2
  );
  const waveformCenterMs =
    Math.round(timelineFocusMs / waveformBucketMs) * waveformBucketMs;
  let desiredWaveformStartMs = Math.max(
    0,
    waveformCenterMs - waveformWindowDurationMs / 2
  );
  let desiredWaveformEndMs = Math.min(
    timelineDurationMs,
    desiredWaveformStartMs + waveformWindowDurationMs
  );
  desiredWaveformStartMs = Math.max(
    0,
    desiredWaveformEndMs - waveformWindowDurationMs
  );
  desiredWaveformEndMs = Math.min(
    timelineDurationMs,
    desiredWaveformStartMs + waveformWindowDurationMs
  );
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setWaveformRange({
        startMs: desiredWaveformStartMs,
        endMs: desiredWaveformEndMs
      });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [desiredWaveformEndMs, desiredWaveformStartMs]);
  const waveformStartMs =
    waveformRange?.startMs ?? desiredWaveformStartMs;
  const waveformEndMs = waveformRange?.endMs ?? desiredWaveformEndMs;
  const waveformUrl =
    timelineInfo.waveform_url && project?.media_url
      ? api.waveformUrl(timelineInfo.waveform_url, {
          startMs: waveformStartMs,
          endMs: waveformEndMs,
          width: 2048,
          height: 128,
          version: project.media_hash ?? project.media_name ?? undefined
        })
      : null;
  function waveformSliceStyle(slice: WaveformSlice): CSSProperties {
    const offset =
      ((slice.startMs - visibleTimelineStartMs) /
        visibleTimelineDurationMs) *
      100;
    const scale =
      (slice.endMs - slice.startMs) / visibleTimelineDurationMs;
    return {
      transform: `translate3d(${offset}%, 0, 0) scaleX(${scale})`
    };
  }
  const rulerIntervalMs = timelineRulerInterval(
    visibleTimelineDurationMs
  );
  const firstRulerTickMs =
    Math.ceil(visibleTimelineStartMs / rulerIntervalMs) *
    rulerIntervalMs;
  const timelineRulerTicks: number[] = [];
  for (
    let timestampMs = firstRulerTickMs;
    timestampMs <= visibleTimelineEndMs &&
    timelineRulerTicks.length < 24;
    timestampMs += rulerIntervalMs
  ) {
    timelineRulerTicks.push(timestampMs);
  }

  function timelinePercent(timestampMs: number) {
    return (
      ((timestampMs - visibleTimelineStartMs) /
        visibleTimelineDurationMs) *
      100
    );
  }

  function snapTimelineMs(timestampMs: number) {
    const snapped =
      Math.round(timestampMs / frameDurationMs) * frameDurationMs;
    return Math.max(
      0,
      Math.min(timelineDurationMs, Math.round(snapped))
    );
  }

  function timelineMsFromClientX(clientX: number) {
    const timeline = timelineRef.current;
    if (!timeline) return currentMs;
    const bounds = timeline.getBoundingClientRect();
    const ratio = Math.max(
      0,
      Math.min(1, (clientX - bounds.left) / bounds.width)
    );
    return snapTimelineMs(
      visibleTimelineStartMs + visibleTimelineDurationMs * ratio
    );
  }

  function applyTimelineWheelZoom(
    clientX: number,
    deltaY: number,
    deltaMode: number
  ) {
    if (!project?.duration_ms || boundaryDrag || deltaY === 0) return;
    const timeline = timelineRef.current;
    if (!timeline) return;
    const bounds = timeline.getBoundingClientRect();
    const pointerRatio = Math.max(
      0,
      Math.min(1, (clientX - bounds.left) / bounds.width)
    );
    const deltaScale =
      deltaMode === WheelEvent.DOM_DELTA_LINE
        ? 16
        : deltaMode === WheelEvent.DOM_DELTA_PAGE
          ? bounds.height
          : 1;
    const normalizedDelta = Math.max(
      -240,
      Math.min(240, deltaY * deltaScale)
    );
    const nextZoom = Math.max(
      1,
      Math.min(
        maxTimelineZoom,
        effectiveTimelineZoom * Math.exp(-normalizedDelta * 0.0018)
      )
    );
    if (Math.abs(nextZoom - effectiveTimelineZoom) < 0.0001) return;

    const anchorMs =
      visibleTimelineStartMs +
      visibleTimelineDurationMs * pointerRatio;
    const nextVisibleDurationMs = timelineDurationMs / nextZoom;
    const nextVisibleStartMs = Math.max(
      0,
      Math.min(
        timelineDurationMs - nextVisibleDurationMs,
        anchorMs - nextVisibleDurationMs * pointerRatio
      )
    );
    setScrubFocusMs(nextVisibleStartMs + nextVisibleDurationMs / 2);
    setTimelineZoom(nextZoom);
  }

  function applyTimelineWheelPan(
    deltaX: number,
    deltaY: number,
    deltaMode: number
  ) {
    if (
      !project?.duration_ms ||
      boundaryDrag ||
      effectiveTimelineZoom <= 1
    ) {
      return;
    }
    const timeline = timelineRef.current;
    if (!timeline) return;
    const bounds = timeline.getBoundingClientRect();
    const rawDelta =
      Math.abs(deltaX) > Math.abs(deltaY) ? deltaX : deltaY;
    if (rawDelta === 0) return;
    const deltaScale =
      deltaMode === WheelEvent.DOM_DELTA_LINE
        ? 16
        : deltaMode === WheelEvent.DOM_DELTA_PAGE
          ? bounds.width
          : 1;
    const deltaMs =
      rawDelta *
      deltaScale *
      (visibleTimelineDurationMs / Math.max(1, bounds.width));
    const nextViewport = panTimelineViewport(
      timelineDurationMs,
      effectiveTimelineZoom,
      visibleTimeline.centerMs,
      deltaMs
    );
    setScrubFocusMs(nextViewport.centerMs);
  }

  useEffect(() => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    function handleTimelineWheel(event: WheelEvent) {
      if (!event.altKey && !event.ctrlKey) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.altKey) {
        applyTimelineWheelZoom(
          event.clientX,
          event.deltaY,
          event.deltaMode
        );
        return;
      }
      applyTimelineWheelPan(
        event.deltaX,
        event.deltaY,
        event.deltaMode
      );
    }
    timeline.addEventListener("wheel", handleTimelineWheel, {
      passive: false
    });
    return () =>
      timeline.removeEventListener("wheel", handleTimelineWheel);
  }, [
    boundaryDrag,
    effectiveTimelineZoom,
    maxTimelineZoom,
    project?.duration_ms,
    timelineDurationMs,
    visibleTimeline.centerMs,
    visibleTimelineDurationMs,
    visibleTimelineStartMs
  ]);

  const timelineZoomSliderValue =
    maxTimelineZoom <= 1
      ? 0
      : Math.round(
          (Math.log(effectiveTimelineZoom) /
            Math.log(maxTimelineZoom)) *
            1_000
        );

  useEffect(() => {
    if (timelineZoom !== effectiveTimelineZoom) {
      setTimelineZoom(effectiveTimelineZoom);
    }
  }, [effectiveTimelineZoom, timelineZoom]);

  useEffect(() => {
    if (openedClips.length === 0) {
      setActiveClipId(null);
      return;
    }
    if (
      activeClipId &&
      !openedClips.some((clip) => clip.clip_id === activeClipId)
    ) {
      setActiveClipId(null);
    }
  }, [activeClipId, openedClips]);

  useEffect(() => {
    if (
      !activeClip &&
      (sidebarTab === "stages" ||
        sidebarTab === "style" ||
        sidebarTab === "post_copy")
    ) {
      setSidebarTab("timestamps");
    }
  }, [activeClip, sidebarTab]);

  const clipSegments = useMemo(
    () =>
      activeClipId
        ? segments.filter((segment) => segment.clip_id === activeClipId)
        : [],
    [activeClipId, segments]
  );

  const visibleSegments = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return clipSegments.filter((segment) => {
      if (warningOnly && segment.warnings.length === 0 && segment.confidence >= 0.65) return false;
      return (
        !normalized ||
        [segment.raw_korean, segment.pass_2_korean, segment.english, segment.speaker_id]
          .some((value) => value?.toLowerCase().includes(normalized))
      );
    });
  }, [clipSegments, query, warningOnly]);

  const active = segments.find(
    (segment) =>
      currentMs >= segment.start_ms &&
      currentMs <= segment.end_ms &&
      (!activeClipId || segment.clip_id === activeClipId)
  );
  const activeCaptionCue = captionTrack?.cues.find(
    (cue) =>
      currentMs >= cue.start_ms &&
      currentMs < cue.end_ms &&
      (!activeClipId || cue.clip_id === activeClipId)
  );
  const transcriptCaption =
    active?.english?.trim() ||
    active?.pass_2_korean?.trim() ||
    active?.raw_korean?.trim() ||
    "";
  const liveCaption = captionTrack
    ? activeCaptionCue?.lines.join("\n") ?? ""
    : transcriptCaption;
  const clipTitles = useMemo(
    () => new Map(clips.map((clip) => [clip.clip_id, clip.title])),
    [clips]
  );

  useEffect(() => {
    if (!playing || !active) return;
    if (
      active.clip_id &&
      openedClips.some((clip) => clip.clip_id === active.clip_id)
    ) {
      setActiveClipId(active.clip_id);
    }
    setSelected(active.segment_id);
    const row = segmentRowRefs.current.get(active.segment_id);
    const viewport = transcriptScrollRef.current;
    if (!row || !viewport) return;
    const rowBox = row.getBoundingClientRect();
    const viewportBox = viewport.getBoundingClientRect();
    const centeredTop =
      viewport.scrollTop +
      rowBox.top -
      viewportBox.top -
      (viewport.clientHeight - rowBox.height) / 2;
    viewport.scrollTo({
      top: Math.max(0, centeredTop),
      behavior: "smooth"
    });
  }, [playing, active?.segment_id, activeClipId, openedClips]);

  useEffect(() => {
    function handlePlaybackShortcut(event: KeyboardEvent) {
      if (event.repeat) return;
      if (isTextEditingTarget(event.target)) return;
      const media = mediaRef.current;
      if (!media) return;
      const key = event.key.toLowerCase();
      if (event.code === "Space") {
        event.preventDefault();
        if (event.ctrlKey && activeClip) {
          media.currentTime = activeClip.start_ms / 1_000;
          setCurrentMs(activeClip.start_ms);
          void media.play();
          return;
        }
        if (media.paused) void media.play();
        else media.pause();
        return;
      }
      const rates: Record<string, number> = {
        j: 0.5,
        k: 1,
        l: 2
      };
      const rate = rates[key];
      if (!rate) return;
      event.preventDefault();
      media.playbackRate = rate;
      setPlaybackRate(rate);
      void media.play();
    }
    window.addEventListener("keydown", handlePlaybackShortcut, true);
    return () =>
      window.removeEventListener("keydown", handlePlaybackShortcut, true);
  }, [activeClip]);

  async function uploadFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const replacingMedia = !!project?.media_name;
    setMediaPreparation({
      phase: "uploading",
      progress: 0,
      filename: file.name
    });
    setError(null);
    try {
      const nextProject = await api.upload(projectId, file, {
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
      });
      setProject(nextProject);
      if (replacingMedia) {
        setActiveClipId(null);
        setSelected(null);
        setSidebarTab("timestamps");
        setCurrentMs(0);
        setScrubFocusMs(null);
        setTimelineZoom(1);
        setQuery("");
        setWarningOnly(false);
        setVideoExportClipIds([]);
        setLatestVideoExport(null);
        setCompletedVideoExport(null);
      }
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally {
      setMediaPreparation(null);
      event.target.value = "";
    }
  }

  async function startStage(stage: "transcribe" | "diarize" | "pass-1" | "pass-2" | "translate") {
    if (!activeClipId) {
      setError("Open a timestamp clip before starting transcription.");
      return;
    }
    setError(null);
    setCompletedVideoExport(null);
    try {
      setJob(await api.startStage(projectId, stage, activeClipId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start processing.");
    }
  }

  async function startEnglishPipeline() {
    if (!activeClipId) {
      setError("Open a timestamp clip before creating its transcript.");
      return;
    }
    setError(null);
    try {
      setJob(await api.startEnglishPipeline(projectId, activeClipId));
      setSidebarTab("stages");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not start the English transcript."
      );
    }
  }

  async function toggleJobPause() {
    if (!job) return;
    setError(null);
    try {
      setJob(
        job.paused
          ? await api.resumeJob(job.job_id)
          : await api.pauseJob(job.job_id)
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not update the task."
      );
    }
  }

  async function stopCurrentJob() {
    if (!job || !window.confirm("Stop this task? It cannot be resumed afterward.")) {
      return;
    }
    setError(null);
    try {
      setJob(await api.cancelJob(job.job_id));
      await refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not stop the task."
      );
    }
  }

  async function patchSegment(id: string, patch: Partial<Segment>) {
    try {
      const updated = await api.patchSegment(projectId, id, patch);
      setSegments((current) =>
        current.map((segment) =>
          segment.segment_id === id ? updated : segment
        )
      );
      setCaptionTrack((current) =>
        current ? { ...current, stale: true } : current
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not save the edit."
      );
    }
  }

  async function regenerateCaptionsForClip(
    clipId: string,
    style: SubtitleStyle
  ) {
    const requestId = captionGenerationRequestRef.current + 1;
    captionGenerationRequestRef.current = requestId;
    setCaptionsGenerating(true);
    setError(null);
    try {
      const track = await api.regenerateCaptions(projectId, {
        language: "en",
        maxWordsPerLine: style.max_words_per_line,
        maxLines: style.max_lines,
        clipId
      });
      if (captionGenerationRequestRef.current === requestId) {
        setCaptionTrack(track);
        const pendingLayout = pendingCaptionLayoutRef.current;
        if (
          pendingLayout?.clipId === clipId &&
          pendingLayout.maxWordsPerLine === style.max_words_per_line &&
          pendingLayout.maxLines === style.max_lines
        ) {
          pendingCaptionLayoutRef.current = null;
        }
      }
    } catch (reason) {
      if (captionGenerationRequestRef.current === requestId) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not regenerate captions."
        );
      }
    } finally {
      if (captionGenerationRequestRef.current === requestId) {
        setCaptionsGenerating(false);
      }
    }
  }

  async function regenerateCaptions() {
    if (!project || !activeClip) return;
    const style = activeClip.subtitle_style ?? project.subtitle_style;
    await regenerateCaptionsForClip(activeClip.clip_id, style);
  }

  function replacePostCopy(next: PostCopy) {
    setPostCopies((current) => [
      ...current.filter((item) => item.clip_id !== next.clip_id),
      next
    ]);
  }

  async function generatePostCopyForClip(clipId: string) {
    setGeneratingPostCopyIds((current) =>
      current.includes(clipId) ? current : [...current, clipId]
    );
    setError(null);
    try {
      replacePostCopy(await api.generatePostCopy(projectId, clipId));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not generate post copy."
      );
    } finally {
      setGeneratingPostCopyIds((current) =>
        current.filter((item) => item !== clipId)
      );
    }
  }

  async function savePostCopy(
    clipId: string,
    patch: Pick<Partial<PostCopy>, "headline" | "body">
  ) {
    try {
      replacePostCopy(await api.updatePostCopy(projectId, clipId, patch));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save post copy."
      );
    }
  }

  async function startVideoExport() {
    if (videoExportClipIds.length === 0) {
      setError("Add at least one clip to the rendering queue.");
      return;
    }
    if (!includeVideoExport && !includeSrtExport && !includeAssExport) {
      setError("Select at least one export format.");
      return;
    }
    setError(null);
    try {
      const next = await api.exportVideo(projectId, {
        resolution: videoResolution,
        quality: videoQuality,
        encoder: videoEncoder,
        clipIds: videoExportClipIds,
        includeVideo: includeVideoExport,
        includeSrt: includeSrtExport,
        includeAss: includeAssExport
      });
      setJob(next);
      setVideoExportOpen(false);
      setSidebarTab("stages");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not start the video export."
      );
    }
  }

  async function openVideoExportFolder() {
    setError(null);
    try {
      await api.openVideoExportFolder(projectId);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not open the video export folder."
      );
    }
  }

  function openVideoExport() {
    setVideoExportClipIds(queuedClips.map((clip) => clip.clip_id));
    setIncludeVideoExport(true);
    setIncludeSrtExport(false);
    setIncludeAssExport(false);
    setVideoExportOpen(true);
  }

  function toggleVideoExportClip(clipId: string) {
    setVideoExportClipIds((current) =>
      current.includes(clipId)
        ? current.filter((value) => value !== clipId)
        : [...current, clipId]
    );
  }

  async function clearRenderQueue() {
    setClearingRenderQueue(true);
    setError(null);
    try {
      setClips(await api.clearRenderQueue(projectId));
      setVideoExportClipIds([]);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not clear the rendering queue."
      );
    } finally {
      setClearingRenderQueue(false);
    }
  }

  function updateSubtitleStyle(nextStyle: SubtitleStyle) {
    if (!activeClipId) return;
    const clipId = activeClipId;
    const previousStyle =
      clips.find((clip) => clip.clip_id === activeClipId)
        ?.subtitle_style ??
      project?.subtitle_style ??
      defaultSubtitleStyle;
    const captionLayoutChanged =
      previousStyle.max_words_per_line !==
        nextStyle.max_words_per_line ||
      previousStyle.max_lines !== nextStyle.max_lines;
    if (captionLayoutChanged) {
      captionGenerationRequestRef.current += 1;
      setCaptionsGenerating(false);
      pendingCaptionLayoutRef.current = {
        clipId,
        maxWordsPerLine: nextStyle.max_words_per_line,
        maxLines: nextStyle.max_lines
      };
    }
    setClips((current) =>
      current.map((clip) =>
        clip.clip_id === activeClipId
          ? { ...clip, subtitle_style: nextStyle }
          : clip
      )
    );
    setCaptionTrack((current) =>
      current && captionLayoutChanged
        ? { ...current, stale: true }
        : current
    );
    if (styleSaveTimerRef.current !== null) {
      window.clearTimeout(styleSaveTimerRef.current);
    }
    setStyleSaving(true);
    styleSaveTimerRef.current = window.setTimeout(async () => {
      try {
        const saved = await api.updateClipSubtitleStyle(
          projectId,
          clipId,
          nextStyle
        );
        setClips((current) =>
          current.map((clip) =>
            clip.clip_id === saved.clip_id ? saved : clip
          )
        );
        const pendingLayout = pendingCaptionLayoutRef.current;
        const savedStyle = saved.subtitle_style ?? nextStyle;
        if (
          pendingLayout?.clipId === clipId &&
          pendingLayout.maxWordsPerLine ===
            savedStyle.max_words_per_line &&
          pendingLayout.maxLines === savedStyle.max_lines &&
          segments.some((segment) => segment.clip_id === clipId)
        ) {
          await regenerateCaptionsForClip(clipId, savedStyle);
          if (pendingCaptionLayoutRef.current === pendingLayout) {
            pendingCaptionLayoutRef.current = null;
          }
        }
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not save subtitle styling."
        );
      } finally {
        setStyleSaving(false);
        styleSaveTimerRef.current = null;
      }
    }, 300);
  }

  async function applySubtitleStyleToAll() {
    if (!activeClipId || styleSaving) return;
    setStyleSaving(true);
    setError(null);
    try {
      if (styleSaveTimerRef.current !== null) {
        window.clearTimeout(styleSaveTimerRef.current);
        styleSaveTimerRef.current = null;
      }
      const currentStyle = clips.find(
        (clip) => clip.clip_id === activeClipId
      )?.subtitle_style;
      if (currentStyle) {
        await api.updateClipSubtitleStyle(
          projectId,
          activeClipId,
          currentStyle
        );
      }
      const updated = await api.applyClipSubtitleStyleToAll(
        projectId,
        activeClipId
      );
      setClips(updated);
      const applied = updated.find(
        (clip) => clip.clip_id === activeClipId
      )?.subtitle_style;
      if (applied) {
        setProject((current) =>
          current ? { ...current, subtitle_style: applied } : current
        );
      }
      setCaptionTrack((current) =>
        current ? { ...current, stale: true } : current
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not apply this style to every clip."
      );
    } finally {
      setStyleSaving(false);
    }
  }

  function seek(segment: Segment) {
    setSelected(segment.segment_id);
    setCurrentMs(segment.start_ms);
    if (mediaRef.current) mediaRef.current.currentTime = segment.start_ms / 1000;
  }

  function timelineZoomForClip(clip: TimestampClip) {
    const durationMs = Math.max(1, project?.duration_ms ?? 1);
    const clipDurationMs = Math.max(1, clip.end_ms - clip.start_ms);
    const framedDurationMs = Math.min(
      durationMs,
      Math.max(5_000, clipDurationMs * 1.35)
    );
    return Math.max(
      1,
      Math.min(
        maxTimelineZoom,
        Math.floor(durationMs / framedDurationMs)
      )
    );
  }

  function selectTranscriptClip(clip: TimestampClip) {
    setActiveClipId(clip.clip_id);
    setSelected(null);
    setTimelineZoom(timelineZoomForClip(clip));
    setScrubFocusMs((clip.start_ms + clip.end_ms) / 2);
    setCurrentMs(clip.start_ms);
    if (mediaRef.current) mediaRef.current.currentTime = clip.start_ms / 1000;
  }

  function navigateMarker(marker: NavigationMarker) {
    setActiveClipId(null);
    setSelected(null);
    setTimelineZoom(1);
    setScrubFocusMs(null);
    setCurrentMs(marker.timestamp_ms);
    if (mediaRef.current) {
      mediaRef.current.currentTime = marker.timestamp_ms / 1000;
    }
  }

  async function openMarkerWorkspace(
    marker: NavigationMarker,
    markerIndex: number
  ) {
    if (!project?.duration_ms) return;
    setError(null);
    try {
      const existing = clipsByMarkerId.get(marker.marker_id);
      if (existing) {
        await openClip(existing);
        return;
      }
      const nextMarker = markers[markerIndex + 1];
      const created = await api.createClip(projectId, {
        navigation_marker_id: marker.marker_id,
        start_ms: marker.timestamp_ms,
        end_ms: nextMarker?.timestamp_ms ?? project.duration_ms,
        title: marker.title
      });
      setClips((current) => [...current, created]);
      await openClip(created);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not open this timestamp as a transcript tab."
      );
    }
  }

  async function saveClipPatch(
    clipId: string,
    patch: Parameters<typeof api.patchClip>[2],
    boundaryChange = false
  ) {
    const preservedTimelineCenterMs =
      visibleTimeline.centerMs;
    if (boundaryChange && activeClipId === clipId) {
      setScrubFocusMs(preservedTimelineCenterMs);
    }
    const normalizedPatch = { ...patch };
    if (normalizedPatch.start_ms !== undefined) {
      normalizedPatch.start_ms = snapTimelineMs(
        normalizedPatch.start_ms
      );
    }
    if (normalizedPatch.end_ms !== undefined) {
      normalizedPatch.end_ms = snapTimelineMs(
        normalizedPatch.end_ms
      );
    }
    const saved = await api.patchClip(
      projectId,
      clipId,
      normalizedPatch
    );
    setClips((current) =>
      current
        .map((clip) => (clip.clip_id === saved.clip_id ? saved : clip))
        .sort((left, right) => left.start_ms - right.start_ms)
    );
    if (boundaryChange) {
      setSegments((current) =>
        current.filter((segment) => segment.clip_id !== clipId)
      );
      setCaptionTrack(null);
      setSelected(null);
    }
    return saved;
  }

  async function setClipBoundary(
    clip: TimestampClip,
    edge: "start" | "end",
    timestampMs: number
  ) {
    const snappedTimestampMs = snapTimelineMs(timestampMs);
    const minimumGapMs = Math.max(1, Math.round(frameDurationMs));
    const value =
      edge === "start"
        ? Math.min(
            Math.max(0, snappedTimestampMs),
            clip.end_ms - minimumGapMs
          )
        : Math.max(
            clip.start_ms + minimumGapMs,
            Math.min(
              project?.duration_ms ?? clip.end_ms,
              snappedTimestampMs
            )
          );
    setError(null);
    try {
      await saveClipPatch(
        clip.clip_id,
        edge === "start" ? { start_ms: value } : { end_ms: value },
        true
      );
    } catch (reason) {
      await refresh();
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not change the clip boundary."
      );
    }
  }

  function beginBoundaryDrag(
    event: ReactPointerEvent<HTMLButtonElement>,
    clip: TimestampClip,
    edge: "start" | "end"
  ) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setBoundaryDrag({
      clipId: clip.clip_id,
      edge,
      originalStartMs: clip.start_ms,
      originalEndMs: clip.end_ms,
      viewportCenterMs: visibleTimeline.centerMs
    });
  }

  function moveBoundaryDrag(event: ReactPointerEvent<HTMLElement>) {
    if (!boundaryDrag) return;
    const pointerValue = timelineMsFromClientX(event.clientX);
    const minimumGapMs = Math.max(1, Math.round(frameDurationMs));
    const timelineWidthPx =
      timelineRef.current?.getBoundingClientRect().width ?? 0;
    const playheadMs = snapTimelineMs(currentMs);
    setClips((current) =>
      current.map((clip) => {
        if (clip.clip_id !== boundaryDrag.clipId) return clip;
        const minimumMs =
          boundaryDrag.edge === "start"
            ? 0
            : clip.start_ms + minimumGapMs;
        const maximumMs =
          boundaryDrag.edge === "start"
            ? clip.end_ms - minimumGapMs
            : timelineDurationMs;
        const value = snapBoundaryToPlayhead(
          pointerValue,
          playheadMs,
          visibleTimelineDurationMs,
          timelineWidthPx,
          minimumMs,
          maximumMs
        );
        return boundaryDrag.edge === "start"
          ? {
              ...clip,
              start_ms: Math.min(
                value,
                clip.end_ms - minimumGapMs
              )
            }
          : {
              ...clip,
              end_ms: Math.max(
                clip.start_ms + minimumGapMs,
                Math.min(timelineDurationMs, value)
              )
            };
      })
    );
  }

  async function finishBoundaryDrag(event: ReactPointerEvent<HTMLElement>) {
    if (!boundaryDrag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const changed = clips.find(
      (clip) => clip.clip_id === boundaryDrag.clipId
    );
    const original = boundaryDrag;
    setScrubFocusMs(original.viewportCenterMs);
    setBoundaryDrag(null);
    if (
      !changed ||
      (changed.start_ms === original.originalStartMs &&
        changed.end_ms === original.originalEndMs)
    ) {
      return;
    }
    try {
      await saveClipPatch(
        changed.clip_id,
        { start_ms: changed.start_ms, end_ms: changed.end_ms },
        true
      );
    } catch (reason) {
      await refresh();
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not change the clip boundary."
      );
    }
  }

  async function toggleRenderQueue(clip: TimestampClip) {
    setError(null);
    try {
      await saveClipPatch(clip.clip_id, {
        render_queued: !clip.render_queued
      });
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not update the rendering queue."
      );
    }
  }

  async function openClip(clip: TimestampClip) {
    setError(null);
    try {
      const opened = clip.opened
        ? clip
        : await api.patchClip(projectId, clip.clip_id, { opened: true });
      if (!clip.opened) {
        setClips((current) =>
          current.map((item) =>
            item.clip_id === opened.clip_id ? opened : item
          )
        );
      }
      selectTranscriptClip(opened);
      setSidebarTab("stages");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not open the clip."
      );
    }
  }

  async function closeClip(clip: TimestampClip) {
    setError(null);
    const closingIndex = openedClips.findIndex(
      (item) => item.clip_id === clip.clip_id
    );
    const fallbackClip =
      openedClips[closingIndex + 1] ?? openedClips[closingIndex - 1] ?? null;

    try {
      const closed = await api.patchClip(projectId, clip.clip_id, {
        opened: false
      });
      setClips((current) =>
        current.map((item) =>
          item.clip_id === closed.clip_id ? closed : item
        )
      );
      if (activeClipId === clip.clip_id) {
        if (fallbackClip) {
          selectTranscriptClip(fallbackClip);
        } else {
          setActiveClipId(null);
          setSelected(null);
          setTimelineZoom(1);
          setScrubFocusMs(null);
          setSidebarTab("timestamps");
        }
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not close the clip tab."
      );
    }
  }

  function updatePlayheadFromClientX(clientX: number) {
    if (!project?.duration_ms) return;
    const targetMs = timelineMsFromClientX(clientX);
    setCurrentMs(targetMs);
    if (mediaRef.current) {
      mediaRef.current.currentTime = targetMs / 1000;
    }
  }

  function seekTimelineFromPointer(
    event: ReactPointerEvent<HTMLDivElement>
  ) {
    if (
      event.button !== 0 ||
      !project?.duration_ms ||
      boundaryDrag
    ) {
      return;
    }
    event.preventDefault();
    setScrubFocusMs(
      visibleTimelineStartMs + visibleTimelineDurationMs / 2
    );
    updatePlayheadFromClientX(event.clientX);
  }

  function beginTimelineScrub(
    event: ReactPointerEvent<HTMLButtonElement>
  ) {
    if (
      event.button !== 0 ||
      !project?.duration_ms ||
      boundaryDrag
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    timelineScrubbingRef.current = true;
    setScrubbing(true);
    setScrubFocusMs(
      visibleTimelineStartMs + visibleTimelineDurationMs / 2
    );
  }

  function moveTimelineScrub(
    event: ReactPointerEvent<HTMLButtonElement>
  ) {
    if (!timelineScrubbingRef.current) return;
    updatePlayheadFromClientX(event.clientX);
  }

  function finishTimelineScrub(
    event: ReactPointerEvent<HTMLButtonElement>
  ) {
    if (!timelineScrubbingRef.current) return;
    updatePlayheadFromClientX(event.clientX);
    timelineScrubbingRef.current = false;
    setScrubbing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function cancelTimelineScrub(
    event: ReactPointerEvent<HTMLButtonElement>
  ) {
    if (!timelineScrubbingRef.current) return;
    timelineScrubbingRef.current = false;
    setScrubbing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function sidebarWidthFromPointer(clientX: number) {
    const gridLeft = editorGridRef.current?.getBoundingClientRect().left ?? 0;
    return clampSidebarWidth(clientX - gridLeft);
  }

  function beginSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setResizingSidebar(true);
    setSidebarWidth(sidebarWidthFromPointer(event.clientX));
  }

  function moveSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    setSidebarWidth(sidebarWidthFromPointer(event.clientX));
  }

  function finishSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const next = sidebarWidthFromPointer(event.clientX);
    setSidebarWidth(next);
    setResizingSidebar(false);
    onSidebarWidthChange(next);
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function adjustSidebarWidth(change: number) {
    const next = clampSidebarWidth(sidebarWidth + change);
    setSidebarWidth(next);
    onSidebarWidthChange(next);
  }

  function resetSidebarWidth() {
    setSidebarWidth(245);
    onSidebarWidthChange(245);
  }

  if (!project) return <LoadingShell />;

  const isVideo = /\.(mp4|mov|mkv)$/i.test(project.media_name ?? "");
  const busy = job && !isJobTerminal(job);
  const preparationCopy = mediaPreparation
    ? describeMediaPreparation(mediaPreparation)
    : null;
  const previewClip = clips.find(
    (clip) => clip.start_ms <= currentMs && currentMs < clip.end_ms
  );
  const subtitleStyle =
    activeClip?.subtitle_style ??
    previewClip?.subtitle_style ??
    project.subtitle_style ??
    defaultSubtitleStyle;
  const captionScale = subtitlePreviewScale(videoBounds.height);
  const previewFontSize = subtitleStyle.font_size * captionScale;
  const captionLetterSpacing =
    subtitleStyle.letter_spacing * captionScale;
  const captionHorizontalPadding = subtitleStyle.background_enabled
    ? subtitleStyle.background_padding_x * captionScale * 2
    : 0;
  const captionTextWidth = Math.max(
    24,
    videoBounds.width * (subtitleStyle.max_width_percent / 100) -
      captionHorizontalPadding
  );
  const liveCaptionPages = paginatePreviewCaption(
    liveCaption.replace(/\r?\n/g, " "),
    subtitleStyle.max_words_per_line,
    subtitleStyle.max_lines,
    captionTextWidth,
    subtitleStyle,
    previewFontSize,
    captionLetterSpacing
  );
  const wordLimitedCaptionPages = paginateCaptionByWords(
    liveCaption.replace(/\r?\n/g, " "),
    subtitleStyle.max_words_per_line,
    subtitleStyle.max_lines
  );
  const previewWordsPerLine = Math.max(
    0,
    ...liveCaptionPages.flatMap((page) =>
      page.split("\n").map(
        (line) => line.trim().split(/\s+/).filter(Boolean).length
      )
    )
  );
  const previewWidthLimited =
    liveCaptionPages.join("\n\n") !==
    wordLimitedCaptionPages.join("\n\n");
  const activeProgress = active
    ? (currentMs - active.start_ms) /
      Math.max(1, active.end_ms - active.start_ms)
    : 0;
  const captionProgress = activeCaptionCue
    ? (currentMs - activeCaptionCue.start_ms) /
      Math.max(1, activeCaptionCue.end_ms - activeCaptionCue.start_ms)
    : activeProgress;
  const liveCaptionPage =
    liveCaptionPages[
      Math.min(
        liveCaptionPages.length - 1,
        Math.max(0, Math.floor(captionProgress * liveCaptionPages.length))
      )
    ];
  const captionPosition: CSSProperties =
    subtitleStyle.position === "top"
      ? { top: `${subtitleStyle.margin_vertical * captionScale}px` }
      : subtitleStyle.position === "middle"
        ? { top: "50%", transform: "translate(-50%, -50%)" }
        : {
            bottom: `${subtitleStyle.margin_vertical * captionScale}px`
          };
  const captionStyle: CSSProperties = {
    ...captionPosition,
    maxWidth: `${subtitleStyle.max_width_percent}%`,
    fontFamily: `"${subtitleStyle.font_family}", Pretendard, "Malgun Gothic", Arial, sans-serif`,
    fontSize: `${previewFontSize}px`,
    fontWeight: subtitleStyle.font_weight,
    fontStyle: subtitleStyle.font_style,
    color: subtitleStyle.text_color,
    letterSpacing: `${captionLetterSpacing}px`,
    lineHeight: subtitleStyle.line_spacing,
    textAlign: subtitleStyle.alignment,
    whiteSpace: "pre",
    overflowWrap: "normal",
    background: subtitleStyle.background_enabled
      ? colorWithOpacity(
          subtitleStyle.background_color,
          subtitleStyle.background_opacity
        )
      : "transparent",
    padding: subtitleStyle.background_enabled
      ? `${subtitleStyle.background_padding_y * captionScale}px ${subtitleStyle.background_padding_x * captionScale}px`
      : 0,
    borderRadius: `${subtitleStyle.background_radius * captionScale}px`,
    WebkitTextStroke:
      subtitleStyle.outline_size > 0
        ? `${subtitleStyle.outline_size * captionScale}px ${subtitleStyle.outline_color}`
        : undefined,
    filter:
      subtitleStyle.shadow_size > 0
        ? `drop-shadow(0 ${subtitleStyle.shadow_size * captionScale}px ${subtitleStyle.shadow_size * captionScale * 1.5}px rgba(0, 0, 0, .8))`
        : undefined
  };
  const captionSafeAreaStyle: CSSProperties = {
    left: `${videoBounds.left}px`,
    top: `${videoBounds.top}px`,
    width: `${videoBounds.width}px`,
    height: `${videoBounds.height}px`
  };

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
          <span>
            {stageLabels[activeClip?.status ?? project.status] ??
              activeClip?.status ??
              project.status}
          </span>
        </div>
        <div className="top-actions">
          <span className={`save-state ${mediaPreparation || styleSaving ? "working" : ""}`}>
            {mediaPreparation || styleSaving ? (
              <span className="activity-spinner" />
            ) : (
              <CheckIcon size={13} weight="bold" />
            )}
            {preparationCopy?.title ?? (styleSaving ? "Saving style" : "Saved locally")}
          </span>
          {project.media_name ? (
            <button
              type="button"
              className="icon-button editor-settings"
              onClick={() => uploadRef.current?.click()}
              disabled={!!busy || !!mediaPreparation}
              aria-label="Replace project video"
              title="Replace project video"
            >
              <UploadSimpleIcon size={16} />
            </button>
          ) : null}
          <button
            className="icon-button editor-settings"
            onClick={onOpenSettings}
            aria-label="App settings"
            title="App settings"
          >
            <GearSixIcon size={16} />
          </button>
          <button
            type="button"
            className="primary export video-export-button"
            disabled={!!busy}
            onClick={openVideoExport}
          >
            <FilmSlateIcon size={16} />
            Render queue {queuedClips.length ? `(${queuedClips.length})` : ""}
          </button>
        </div>
      </header>

      {completedVideoExport ? (
        <section
          className="video-export-complete"
          role="status"
          aria-live="polite"
          aria-label="Video export complete"
        >
          <span className="video-export-complete-mark">
            <CheckIcon size={17} weight="bold" />
          </span>
          <div>
            <strong>Export complete</strong>
            <small title={completedVideoExport.output_folder ?? undefined}>
              {completedVideoExport.outputs.length || 1}{" "}
              {(completedVideoExport.outputs.length || 1) === 1
                ? "file"
                : "files"}{" "}
              saved
              {completedVideoExport.output_folder
                ? ` to ${completedVideoExport.output_folder}`
                : ""}
            </small>
          </div>
          {completedVideoExport.output_folder ? (
            <button
              type="button"
              className="secondary"
              onClick={() => void openVideoExportFolder()}
            >
              <FolderOpenIcon size={15} />
              Open folder
            </button>
          ) : null}
          <button
            type="button"
            className="icon-button"
            aria-label="Dismiss export complete message"
            onClick={() => setCompletedVideoExport(null)}
          >
            <XIcon size={16} />
          </button>
        </section>
      ) : null}

      {videoExportOpen ? (
        <div className="modal-backdrop">
          <div
            className="new-project-modal video-export-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="video-export-title"
          >
            <div className="modal-heading">
              <div>
                <span className="eyebrow">Export</span>
                <h2 id="video-export-title">Render queue</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close video export"
                onClick={() => setVideoExportOpen(false)}
              >
                <XIcon size={17} />
              </button>
            </div>
            {latestVideoExport?.outputs.length ? (
              <div className="video-export-results">
                <div className="video-export-results-heading">
                  <div>
                    <strong>Latest export</strong>
                    <small>{latestVideoExport.outputs.length} exported files</small>
                  </div>
                  {latestVideoExport.output_folder ? (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => void openVideoExportFolder()}
                    >
                      <FolderOpenIcon size={15} />
                      Open folder
                    </button>
                  ) : null}
                </div>
                {latestVideoExport.output_folder ? (
                  <div
                    className="video-export-folder-path"
                    title={latestVideoExport.output_folder}
                  >
                    <FolderOpenIcon size={14} />
                    <span>{latestVideoExport.output_folder}</span>
                  </div>
                ) : null}
                {latestVideoExport.outputs.map((output) => (
                  <div className="video-export-ready" key={output.output_name}>
                    <span>
                      <CheckIcon size={16} weight="bold" />
                    </span>
                    <div>
                      <strong>{output.title}</strong>
                      <small>
                        {output.kind === "video" ? "MP4" : output.kind.toUpperCase()}
                        {" | "}
                        {formatTimestamp(output.start_ms)} - {formatTimestamp(output.end_ms)}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            ) : latestVideoExport?.output_url ? (
              <>
                {latestVideoExport.output_folder ? (
                  <div className="video-export-folder-path single">
                    <FolderOpenIcon size={14} />
                    <span title={latestVideoExport.output_folder}>
                      {latestVideoExport.output_folder}
                    </span>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => void openVideoExportFolder()}
                    >
                      <FolderOpenIcon size={15} />
                      Open folder
                    </button>
                  </div>
                ) : null}
                <div className="video-export-ready">
                  <span><CheckIcon size={16} weight="bold" /></span>
                  <div>
                    <strong>Latest video ready</strong>
                    <small>{latestVideoExport.output_name}</small>
                  </div>
                </div>
              </>
            ) : null}
            {queuedClips.length ? (
              <fieldset className="video-export-segments">
                <div className="video-export-segments-heading">
                  <legend>Rendering queue</legend>
                  <div>
                    <button
                      type="button"
                      onClick={() =>
                        setVideoExportClipIds(
                          queuedClips.map((clip) => clip.clip_id)
                        )
                      }
                    >
                      Select all
                    </button>
                    <button
                      type="button"
                      onClick={() => setVideoExportClipIds([])}
                    >
                      Select none
                    </button>
                    <button
                      type="button"
                      className="remove-all"
                      disabled={clearingRenderQueue || !!busy}
                      onClick={() => void clearRenderQueue()}
                    >
                      <TrashIcon size={11} />
                      {clearingRenderQueue ? "Removing..." : "Remove all"}
                    </button>
                  </div>
                </div>
                <div className="video-export-segment-list">
                  {queuedClips.map((clip, index) => (
                    <label key={clip.clip_id}>
                      <input
                        type="checkbox"
                        checked={videoExportClipIds.includes(clip.clip_id)}
                        onChange={() => toggleVideoExportClip(clip.clip_id)}
                      />
                      <span className="video-export-segment-index">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <span>
                        <strong>{clip.title}</strong>
                        <small>
                          {formatTimestamp(clip.start_ms)} - {formatTimestamp(clip.end_ms)}
                          {" | "}
                          {formatTimestamp(clip.end_ms - clip.start_ms)}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : (
              <div className="video-export-warning">
                Set clip boundaries, then add clips to the rendering queue from
                the Clips panel.
              </div>
            )}
            <fieldset className={`video-export-options ${includeVideoExport ? "" : "inactive"}`}>
              <legend>Resolution</legend>
              <div className="export-choice-grid">
                <button
                  type="button"
                  disabled={!includeVideoExport}
                  className={videoResolution === "1080p" ? "selected" : ""}
                  onClick={() => setVideoResolution("1080p")}
                >
                  <strong>1080p</strong>
                  <small>1920 × 1080</small>
                </button>
                <button
                  type="button"
                  disabled={!includeVideoExport}
                  className={videoResolution === "source" ? "selected" : ""}
                  onClick={() => setVideoResolution("source")}
                >
                  <strong>Original</strong>
                  <small>Source dimensions</small>
                </button>
              </div>
            </fieldset>
            <fieldset className={`video-export-options ${includeVideoExport ? "" : "inactive"}`}>
              <legend>Encoder</legend>
              <div className="export-choice-grid">
                <button
                  type="button"
                  disabled={!includeVideoExport}
                  className={videoEncoder === "gpu" ? "selected" : ""}
                  onClick={() => setVideoEncoder("gpu")}
                >
                  <strong>GPU</strong>
                  <small>Hardware · automatic fallback</small>
                </button>
                <button
                  type="button"
                  disabled={!includeVideoExport}
                  className={videoEncoder === "cpu" ? "selected" : ""}
                  onClick={() => setVideoEncoder("cpu")}
                >
                  <strong>CPU</strong>
                  <small>libx264 · compatible</small>
                </button>
              </div>
            </fieldset>
            <fieldset className={`video-export-options ${includeVideoExport ? "" : "inactive"}`}>
              <legend>Quality</legend>
              <div className="export-choice-grid">
                <button
                  type="button"
                  disabled={!includeVideoExport}
                  className={videoQuality === "maximum" ? "selected" : ""}
                  onClick={() => setVideoQuality("maximum")}
                >
                  <strong>Maximum</strong>
                  <small>CQ 16 · best quality</small>
                </button>
                <button
                  type="button"
                  disabled={!includeVideoExport}
                  className={videoQuality === "high" ? "selected" : ""}
                  onClick={() => setVideoQuality("high")}
                >
                  <strong>High</strong>
                  <small>CQ 18 · smaller file</small>
                </button>
              </div>
            </fieldset>
            <fieldset className="video-export-options export-format-options">
              <legend>Output files</legend>
              <div className="export-format-grid">
                <label className={includeVideoExport ? "selected" : ""}>
                  <input
                    type="checkbox"
                    checked={includeVideoExport}
                    onChange={(event) => setIncludeVideoExport(event.target.checked)}
                  />
                  <FilmSlateIcon size={17} />
                  <span>
                    <strong>Video</strong>
                    <small>Captioned MP4</small>
                  </span>
                </label>
                <label className={includeSrtExport ? "selected" : ""}>
                  <input
                    type="checkbox"
                    checked={includeSrtExport}
                    onChange={(event) => setIncludeSrtExport(event.target.checked)}
                  />
                  <DownloadSimpleIcon size={17} />
                  <span>
                    <strong>SRT</strong>
                    <small>One file per selected clip</small>
                  </span>
                </label>
                <label className={includeAssExport ? "selected" : ""}>
                  <input
                    type="checkbox"
                    checked={includeAssExport}
                    onChange={(event) => setIncludeAssExport(event.target.checked)}
                  />
                  <DownloadSimpleIcon size={17} />
                  <span>
                    <strong>Styled ASS</strong>
                    <small>One file per selected clip</small>
                  </span>
                </label>
              </div>
              {!includeVideoExport && (includeSrtExport || includeAssExport) ? (
                <small className="export-format-note">
                  Subtitle files only. Video rendering will be skipped.
                </small>
              ) : null}
            </fieldset>
            {!captionTrack || captionTrack.stale ? (
              <div className="video-export-warning">
                {!captionTrack
                  ? "Generate captions before exporting."
                  : "Regenerate captions to apply the current words and lines."}
              </div>
            ) : null}
            <div className="modal-actions split">
              <button
                type="button"
                className="secondary"
                onClick={() => setVideoExportOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary"
                disabled={
                  !captionTrack ||
                  captionTrack.stale ||
                  !!busy ||
                  videoExportClipIds.length === 0 ||
                  (!includeVideoExport && !includeSrtExport && !includeAssExport)
                }
                onClick={() => void startVideoExport()}
              >
                {includeVideoExport ? (
                  <FilmSlateIcon size={16} />
                ) : (
                  <DownloadSimpleIcon size={16} />
                )}
                {videoExportClipIds.length
                  ? `Export ${videoExportClipIds.length} ${
                      videoExportClipIds.length === 1 ? "segment" : "segments"
                    }`
                  : "Select segments"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div
        ref={editorGridRef}
        className={`editor-grid ${resizingSidebar ? "resizing-sidebar" : ""}`}
        style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}
      >
        <aside className="editor-sidebar">
          <div className="sidebar-tabs four">
            <button
              className={sidebarTab === "timestamps" ? "active" : ""}
              onClick={() => setSidebarTab("timestamps")}
            >
              Clips <span>{markers.length}</span>
            </button>
            <button
              className={sidebarTab === "stages" ? "active" : ""}
              onClick={() => setSidebarTab("stages")}
              disabled={!activeClip}
              title={!activeClip ? "Open a clip with its Transcribe button first" : undefined}
            >
              Transcribe
            </button>
            <button
              className={sidebarTab === "style" ? "active" : ""}
              onClick={() => setSidebarTab("style")}
              disabled={!activeClip}
              title={!activeClip ? "Open a clip with its Transcribe button first" : undefined}
            >
              Style
            </button>
            <button
              className={sidebarTab === "post_copy" ? "active" : ""}
              onClick={() => setSidebarTab("post_copy")}
              disabled={!activeClip}
              title={!activeClip ? "Open a clip tab first" : undefined}
            >
              Post copy
            </button>
          </div>
          {sidebarTab === "stages" && activeClip ? (
            <Pipeline
              project={project}
              clip={activeClip}
              job={job}
              runtime={runtime}
              voiceProfileCount={voiceProfiles.length}
              onStart={startStage}
              onRunAll={startEnglishPipeline}
              onExpectedSpeakerCount={async (count) => {
                setProject(
                  await api.updateExpectedSpeakerCount(projectId, count)
                );
                await refresh();
              }}
              onTogglePause={toggleJobPause}
              onStop={stopCurrentJob}
              etaMs={etaMs}
              mediaPreparation={mediaPreparation}
            />
          ) : sidebarTab === "timestamps" ? (
            <ClipPanel
              markers={markers}
              clipsByMarkerId={clipsByMarkerId}
              disabled={!!mediaPreparation}
              onImportMarkers={async (text) => {
                await api.importMarkers(projectId, text);
                await refresh();
                setSidebarTab("timestamps");
              }}
              playbackMarkerId={playbackMarkerId}
              onNavigateMarker={navigateMarker}
              onTranscribe={openMarkerWorkspace}
              onError={(message) => setError(message)}
            />
          ) : sidebarTab === "post_copy" && activeClip ? (
            <PostCopyPanel
              clip={activeClip}
              segments={clipSegments}
              postCopy={postCopies.find(
                (item) => item.clip_id === activeClip.clip_id
              )}
              generating={generatingPostCopyIds.includes(activeClip.clip_id)}
              onGenerate={generatePostCopyForClip}
              onChange={(clipId, patch) =>
                setPostCopies((current) =>
                  current.map((item) =>
                    item.clip_id === clipId ? { ...item, ...patch } : item
                  )
                )
              }
              onSave={savePostCopy}
              onError={setError}
            />
          ) : (
            <SubtitleStylePanel
              value={subtitleStyle}
              saving={styleSaving}
              captionTrack={captionTrack}
              canGenerate={clipSegments.length > 0}
              generating={captionsGenerating}
              previewWordsPerLine={
                liveCaption ? previewWordsPerLine : null
              }
              previewWidthLimited={previewWidthLimited}
              onChange={updateSubtitleStyle}
              onApplyToAll={applySubtitleStyleToAll}
              onRegenerate={regenerateCaptions}
            />
          )}
          <input
            hidden
            ref={uploadRef}
            type="file"
            accept=".mp4,.mov,.mkv,.mp3,.wav,.m4a,.aac"
            onChange={uploadFile}
          />
          <div
            className="sidebar-resize-handle"
            role="separator"
            tabIndex={0}
            aria-label="Resize left sidebar"
            aria-orientation="vertical"
            aria-valuemin={245}
            aria-valuemax={Math.max(245, window.innerWidth - 320)}
            aria-valuenow={sidebarWidth}
            title="Drag to resize. Double-click to reset."
            onPointerDown={beginSidebarResize}
            onPointerMove={moveSidebarResize}
            onPointerUp={finishSidebarResize}
            onPointerCancel={(event) => {
              setResizingSidebar(false);
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
            }}
            onDoubleClick={resetSidebarWidth}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft") {
                event.preventDefault();
                adjustSidebarWidth(event.shiftKey ? -40 : -12);
              } else if (event.key === "ArrowRight") {
                event.preventDefault();
                adjustSidebarWidth(event.shiftKey ? 40 : 12);
              } else if (event.key === "Home") {
                event.preventDefault();
                resetSidebarWidth();
              }
            }}
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
                <div className="media-viewport" ref={mediaViewportRef}>
                  {isVideo ? (
                    <video
                      ref={(node) => {
                        mediaRef.current = node;
                        videoRef.current = node;
                      }}
                      src={project.media_url}
                      onLoadedMetadata={(event) => {
                        const media = event.currentTarget;
                        media.playbackRate = playbackRate;
                        media.currentTime = Math.min(
                          media.duration || Number.POSITIVE_INFINITY,
                          currentMs / 1_000
                        );
                      }}
                      onTimeUpdate={(event) => setCurrentMs(event.currentTarget.currentTime * 1000)}
                      onPlay={() => setPlaying(true)}
                      onPause={() => setPlaying(false)}
                      onRateChange={(event) => setPlaybackRate(event.currentTarget.playbackRate)}
                    />
                  ) : (
                    <audio
                      ref={(node) => {
                        mediaRef.current = node;
                        videoRef.current = null;
                      }}
                      src={project.media_url}
                      onLoadedMetadata={(event) => {
                        const media = event.currentTarget;
                        media.playbackRate = playbackRate;
                        media.currentTime = Math.min(
                          media.duration || Number.POSITIVE_INFINITY,
                          currentMs / 1_000
                        );
                      }}
                      onTimeUpdate={(event) => setCurrentMs(event.currentTarget.currentTime * 1000)}
                      onPlay={() => setPlaying(true)}
                      onPause={() => setPlaying(false)}
                      onRateChange={(event) => setPlaybackRate(event.currentTarget.playbackRate)}
                    />
                  )}
                  {liveCaption ? (
                    <div
                      className="caption-safe-area"
                      style={captionSafeAreaStyle}
                    >
                      <div className="live-caption" style={captionStyle}>
                        {liveCaptionPage}
                      </div>
                    </div>
                  ) : null}
                </div>
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
                  <button
                    className="skip-back-button"
                    onClick={() => {
                      if (mediaRef.current) {
                        mediaRef.current.currentTime = Math.max(
                          0,
                          mediaRef.current.currentTime - 5
                        );
                      }
                    }}
                  >
                    −5
                  </button>
                  <span className="timecode">
                    {formatPreciseTime(currentMs)}
                  </span>
                  <div className="timeline-stack">
                    <div className="timeline-ruler">
                      {timelineRulerTicks.map((timestampMs) => (
                        <span
                          key={timestampMs}
                          className="timeline-ruler-tick"
                          aria-hidden="true"
                          style={{
                            left: `${timelinePercent(timestampMs)}%`
                          }}
                        >
                          <i />
                          <em>
                            {formatTimelineRulerTime(
                              timestampMs,
                              rulerIntervalMs
                            )}
                          </em>
                        </span>
                      ))}
                      {activeClip &&
                      activeClip.end_ms > visibleTimelineStartMs &&
                      activeClip.start_ms < visibleTimelineEndMs ? (
                        <>
                          {activeClip.start_ms >=
                          visibleTimelineStartMs ? (
                            <button
                              type="button"
                              className={`clip-boundary-pointer start ${
                                activeClip.render_queued ? "queued" : ""
                              }`}
                              style={{
                                left: `${timelinePercent(
                                  activeClip.start_ms
                                )}%`
                              }}
                              aria-label={`Change start of ${activeClip.title}`}
                              title={`In: ${formatTimestamp(
                                activeClip.start_ms
                              )}`}
                              onPointerDown={(event) =>
                                beginBoundaryDrag(
                                  event,
                                  activeClip,
                                  "start"
                                )
                              }
                              onPointerMove={moveBoundaryDrag}
                              onPointerUp={(event) =>
                                void finishBoundaryDrag(event)
                              }
                              onPointerCancel={(event) =>
                                void finishBoundaryDrag(event)
                              }
                              onClick={(event) => event.stopPropagation()}
                            >
                              <span>IN</span>
                            </button>
                          ) : null}
                          {activeClip.end_ms <= visibleTimelineEndMs ? (
                            <button
                              type="button"
                              className={`clip-boundary-pointer end ${
                                activeClip.render_queued ? "queued" : ""
                              }`}
                              style={{
                                left: `${timelinePercent(
                                  activeClip.end_ms
                                )}%`
                              }}
                              aria-label={`Change end of ${activeClip.title}`}
                              title={`Out: ${formatTimestamp(
                                activeClip.end_ms
                              )}`}
                              onPointerDown={(event) =>
                                beginBoundaryDrag(
                                  event,
                                  activeClip,
                                  "end"
                                )
                              }
                              onPointerMove={moveBoundaryDrag}
                              onPointerUp={(event) =>
                                void finishBoundaryDrag(event)
                              }
                              onPointerCancel={(event) =>
                                void finishBoundaryDrag(event)
                              }
                              onClick={(event) => event.stopPropagation()}
                            >
                              <span>OUT</span>
                            </button>
                          ) : null}
                        </>
                      ) : null}
                    </div>
                    <div
                      ref={timelineRef}
                      className={`waveform-placeholder ${
                        boundaryDrag ? "dragging" : ""
                      } ${
                        scrubbing ? "scrubbing" : ""
                      }`}
                      role="slider"
                      tabIndex={0}
                      aria-label="Video timeline"
                      aria-valuemin={Math.round(visibleTimelineStartMs)}
                      aria-valuemax={Math.round(visibleTimelineEndMs)}
                      aria-valuenow={Math.round(currentMs)}
                      onPointerDown={seekTimelineFromPointer}
                      onKeyDown={(event) => {
                        if (!mediaRef.current) return;
                        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
                          return;
                        }
                        event.preventDefault();
                        const direction =
                          event.key === "ArrowLeft" ? -1 : 1;
                        const frameCount = event.shiftKey ? 10 : 1;
                        const targetMs = snapTimelineMs(
                          mediaRef.current.currentTime * 1_000 +
                            direction *
                              frameDurationMs *
                              frameCount
                        );
                        mediaRef.current.currentTime = targetMs / 1_000;
                        setCurrentMs(targetMs);
                      }}
                    >
                      {previousWaveform ? (
                        <img
                          key={previousWaveform.url}
                          className="timeline-waveform-image leaving"
                          src={previousWaveform.url}
                          alt=""
                          aria-hidden="true"
                          style={waveformSliceStyle(previousWaveform)}
                        />
                      ) : null}
                      {displayedWaveform ? (
                        <img
                          key={displayedWaveform.url}
                          className="timeline-waveform-image loaded"
                          src={displayedWaveform.url}
                          alt=""
                          aria-hidden="true"
                          style={waveformSliceStyle(displayedWaveform)}
                        />
                      ) : null}
                      {waveformUrl &&
                      displayedWaveform?.url !== waveformUrl &&
                      failedWaveformUrl !== waveformUrl ? (
                        <img
                          key={waveformUrl}
                          className="timeline-waveform-preloader"
                          src={waveformUrl}
                          alt=""
                          aria-hidden="true"
                          onLoad={() => {
                            if (
                              displayedWaveform &&
                              displayedWaveform.url !== waveformUrl
                            ) {
                              setPreviousWaveform(displayedWaveform);
                              if (waveformFadeTimerRef.current !== null) {
                                window.clearTimeout(
                                  waveformFadeTimerRef.current
                                );
                              }
                              waveformFadeTimerRef.current =
                                window.setTimeout(() => {
                                  setPreviousWaveform(null);
                                  waveformFadeTimerRef.current = null;
                                }, 180);
                            }
                            setDisplayedWaveform({
                              url: waveformUrl,
                              startMs: waveformStartMs,
                              endMs: waveformEndMs
                            });
                            setFailedWaveformUrl(null);
                          }}
                          onError={() => {
                            setFailedWaveformUrl(waveformUrl);
                          }}
                        />
                      ) : null}
                      {!displayedWaveform
                        ? Array.from({ length: 58 }).map((_, index) => (
                            <i
                              key={index}
                              style={{
                                height: `${
                                  18 + ((index * 19) % 55)
                                }%`
                              }}
                            />
                          ))
                        : null}
                      <button
                        type="button"
                        className="timeline-playhead"
                        style={{ left: `${timelinePercent(currentMs)}%` }}
                        aria-label={`Playhead at ${formatPreciseTime(
                          currentMs
                        )}`}
                        title="Drag playhead"
                        onPointerDown={beginTimelineScrub}
                        onPointerMove={moveTimelineScrub}
                        onPointerUp={finishTimelineScrub}
                        onPointerCancel={cancelTimelineScrub}
                        onLostPointerCapture={cancelTimelineScrub}
                        onClick={(event) => event.stopPropagation()}
                      />
                      {!activeClip ? markers
                        .filter(
                          (marker) =>
                            marker.timestamp_ms >= visibleTimelineStartMs &&
                            marker.timestamp_ms <= visibleTimelineEndMs
                        )
                        .map((marker) => (
                          <button
                            key={marker.marker_id}
                            type="button"
                            className="timeline-marker"
                            style={{
                              left: `${timelinePercent(marker.timestamp_ms)}%`,
                              top: "4px"
                            }}
                            title={`${formatTimestamp(marker.timestamp_ms)} ${marker.title}`}
                            aria-label={`Go to ${marker.title} at ${formatTimestamp(
                              marker.timestamp_ms
                            )}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              navigateMarker(marker);
                            }}
                            onPointerDown={(event) =>
                              event.stopPropagation()
                            }
                          >
                            <span aria-hidden="true" />
                            <em>{formatTimestamp(marker.timestamp_ms)}</em>
                          </button>
                        )) : null}
                    </div>
                    <label className="timeline-zoom">
                      <MagnifyingGlassIcon size={12} />
                      <input
                        type="range"
                        min={0}
                        max={1000}
                        step={1}
                        value={timelineZoomSliderValue}
                        onInput={(event) => {
                          const position =
                            Number(event.currentTarget.value) / 1_000;
                          setScrubFocusMs(
                            visibleTimelineStartMs +
                              visibleTimelineDurationMs / 2
                          );
                          setTimelineZoom(
                            Math.max(
                              1,
                              Math.exp(
                                Math.log(maxTimelineZoom) * position
                              )
                            )
                          );
                        }}
                        aria-label="Timeline zoom"
                        title={`${frameRate.toFixed(
                          frameRate % 1 ? 2 : 0
                        )} fps`}
                      />
                      <output>{effectiveTimelineZoom.toFixed(1)}x</output>
                    </label>
                  </div>
                  <span className="timecode muted">{formatTime(project.duration_ms)}</span>
                  <select
                    aria-label="Playback speed"
                    value={playbackRate}
                    onChange={(event) => {
                      const rate = Number(event.target.value);
                      setPlaybackRate(rate);
                      if (mediaRef.current) mediaRef.current.playbackRate = rate;
                    }}
                  >
                    <option value=".5">0.5×</option>
                    <option value=".75">0.75×</option>
                    <option value="1">1×</option>
                    <option value="1.25">1.25×</option>
                    <option value="1.5">1.5×</option>
                    <option value="2">2×</option>
                  </select>
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
            {openedClips.length > 0 ? (
              <div className="transcript-clip-bar">
                <div
                  className="transcript-clip-tabs"
                  role="tablist"
                  aria-label="Opened clip workspaces"
                >
                {openedClips.map((clip, index) => {
                  const rowCount = segments.filter(
                    (segment) => segment.clip_id === clip.clip_id
                  ).length;
                  const isActive = activeClipId === clip.clip_id;
                  return (
                    <div
                      className={`transcript-clip-tab ${
                        isActive ? "active" : ""
                      }`}
                      key={clip.clip_id}
                    >
                      <button
                        type="button"
                        role="tab"
                        aria-selected={isActive}
                        onClick={() => selectTranscriptClip(clip)}
                      >
                        <span>Clip {index + 1}</span>
                        <strong>
                          {clip.title || formatTimestamp(clip.start_ms)}
                        </strong>
                        <small>
                          {formatTimestamp(clip.start_ms)} · {rowCount} lines
                        </small>
                      </button>
                      <button
                        type="button"
                        className="close-clip-tab"
                        aria-label={`Close ${clip.title || "clip"} tab`}
                        title="Close tab"
                        onClick={() => void closeClip(clip)}
                      >
                        <XIcon size={13} weight="bold" />
                      </button>
                    </div>
                  );
                })}
                </div>
                {activeClip ? (
                  <div
                    className="clip-sequence-toolbar"
                    role="toolbar"
                    aria-label={`${activeClip.title} clip boundaries`}
                  >
                <button
                  type="button"
                  disabled={!!busy || currentMs >= activeClip.end_ms}
                  onClick={() =>
                    void setClipBoundary(activeClip, "start", currentMs)
                  }
                >
                  Set in
                </button>
                <span className="sequence-boundary">
                  <small>IN</small>
                  {formatTimestamp(activeClip.start_ms)}
                </span>
                <span className="sequence-duration">
                  {formatDuration(
                    activeClip.end_ms - activeClip.start_ms
                  )}
                </span>
                <span className="sequence-boundary">
                  <small>OUT</small>
                  {formatTimestamp(activeClip.end_ms)}
                </span>
                <button
                  type="button"
                  disabled={!!busy || currentMs <= activeClip.start_ms}
                  onClick={() =>
                    void setClipBoundary(activeClip, "end", currentMs)
                  }
                >
                  Set out
                </button>
                <button
                  type="button"
                  className={`sequence-queue ${
                    activeClip.render_queued ? "queued" : ""
                  }`}
                  disabled={!!busy}
                  onClick={() => void toggleRenderQueue(activeClip)}
                >
                  {activeClip.render_queued ? (
                    <CheckIcon size={13} weight="bold" />
                  ) : (
                    <PlusIcon size={13} />
                  )}
                  {activeClip.render_queued ? "Queued" : "Render queue"}
                </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            <div className="transcript-scroll" ref={transcriptScrollRef}>
              <div className="transcript-toolbar">
                <div>
                  <h2>Transcript</h2>
                  <span>{clipSegments.length} lines</span>
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

              {busy && job?.clip_id === activeClipId && clipSegments.length === 0 ? (
                <TranscriptSkeleton />
              ) : visibleSegments.length === 0 ? (
                <div className="transcript-empty">
                  <FileAudioIcon size={29} />
                  <strong>
                    {!activeClip
                      ? "Full-video navigation"
                      : clipSegments.length
                        ? "No matching lines"
                        : "This clip has not been transcribed"}
                  </strong>
                  <p>
                    {!activeClip
                      ? "No clip workspace is open."
                      : clipSegments.length
                      ? "Clear the search or review filter."
                      : "Open Transcribe to create this clip's Korean and English transcript."}
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
                      clipTitle={
                        segment.clip_id
                          ? clipTitles.get(segment.clip_id)
                          : undefined
                      }
                      speakers={speakers}
                      selected={selected === segment.segment_id}
                      rowRef={(node) => {
                        if (node) {
                          segmentRowRefs.current.set(segment.segment_id, node);
                        } else {
                          segmentRowRefs.current.delete(segment.segment_id);
                        }
                      }}
                      onSelect={() => seek(segment)}
                      onPatch={(patch) => patchSegment(segment.segment_id, patch)}
                    />
                  ))}
                </div>
              )}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

function SubtitleStylePanel({
  value,
  saving,
  captionTrack,
  canGenerate,
  generating,
  previewWordsPerLine,
  previewWidthLimited,
  onChange,
  onApplyToAll,
  onRegenerate
}: {
  value: SubtitleStyle;
  saving: boolean;
  captionTrack: CaptionTrack | null;
  canGenerate: boolean;
  generating: boolean;
  previewWordsPerLine: number | null;
  previewWidthLimited: boolean;
  onChange: (style: SubtitleStyle) => void;
  onApplyToAll: () => Promise<void>;
  onRegenerate: () => Promise<void>;
}) {
  const [presets, setPresets] = useState<SubtitleStylePreset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetError, setPresetError] = useState<string | null>(null);
  const [presetsLoading, setPresetsLoading] = useState(true);
  const [presetSaving, setPresetSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadPresets() {
      try {
        let saved = await api.stylePresets();
        const legacy = loadLegacySubtitleStylePresets();
        for (const item of legacy) {
          if (
            saved.some(
              (preset) =>
                preset.name.toLocaleLowerCase() ===
                item.name.toLocaleLowerCase()
            )
          ) {
            continue;
          }
          const migrated = await api.createStylePreset(item.name, item.style);
          saved = [...saved, migrated];
        }
        if (legacy.length) {
          window.localStorage.removeItem(stylePresetsStorageKey);
        }
        if (!cancelled) setPresets(saved);
      } catch (error) {
        if (!cancelled) {
          setPresetError(readableErrorMessage(error));
        }
      } finally {
        if (!cancelled) setPresetsLoading(false);
      }
    }
    void loadPresets();
    return () => {
      cancelled = true;
    };
  }, []);
  const patch = (next: Partial<SubtitleStyle>) => {
    setSelectedPresetId("");
    setPresetError(null);
    onChange({ ...value, ...next });
  };

  function applyPreset(presetId: string) {
    const preset = presets.find((item) => item.preset_id === presetId);
    if (!preset) {
      setSelectedPresetId("");
      return;
    }
    setSelectedPresetId(preset.preset_id);
    setPresetName(preset.name);
    setPresetError(null);
    onChange({ ...preset.style });
  }

  async function savePreset(event: FormEvent) {
    event.preventDefault();
    const name = presetName.trim();
    if (!name) {
      setPresetError("Enter a preset name.");
      return;
    }
    const existing = presets.find(
      (item) => item.name.toLocaleLowerCase() === name.toLocaleLowerCase()
    );
    setPresetSaving(true);
    try {
      const saved = existing
        ? await api.updateStylePreset(existing.preset_id, name, value)
        : await api.createStylePreset(name, value);
      const next = existing
        ? presets.map((item) =>
            item.preset_id === existing.preset_id ? saved : item
          )
        : [...presets, saved];
      setPresets(next);
      setSelectedPresetId(saved.preset_id);
      setPresetName(saved.name);
      setPresetError(null);
    } catch (error) {
      setPresetError(readableErrorMessage(error));
    } finally {
      setPresetSaving(false);
    }
  }

  async function deleteSelectedPreset() {
    if (!selectedPresetId) return;
    setPresetSaving(true);
    try {
      await api.deleteStylePreset(selectedPresetId);
      setPresets((current) =>
        current.filter((item) => item.preset_id !== selectedPresetId)
      );
      setSelectedPresetId("");
      setPresetName("");
      setPresetError(null);
    } catch (error) {
      setPresetError(readableErrorMessage(error));
    } finally {
      setPresetSaving(false);
    }
  }

  return (
    <div className="subtitle-style-panel">
      <div className="style-panel-heading">
        <span>
          <strong>Subtitle appearance</strong>
          <small>{saving ? "Saving changes" : "Saved with this project"}</small>
        </span>
        <span className="style-heading-actions">
          <button
            type="button"
            disabled={saving}
            onClick={() => void onApplyToAll()}
          >
            Apply to all
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPresetId("");
              setPresetName("");
              onChange({ ...defaultSubtitleStyle });
            }}
          >
            Reset
          </button>
        </span>
      </div>

      <form className="style-presets" onSubmit={savePreset}>
        <div className="style-presets-heading">
          <span>
            <strong>Style presets</strong>
            <small>Reusable across projects and clips</small>
          </span>
          <button
            type="button"
            className="icon-button"
            disabled={!selectedPresetId || presetSaving}
            aria-label="Delete selected style preset"
            title="Delete selected preset"
            onClick={() => void deleteSelectedPreset()}
          >
            <TrashIcon size={13} />
          </button>
        </div>
        <select
          value={selectedPresetId}
          disabled={presetsLoading || presetSaving}
          aria-label="Choose a style preset"
          onChange={(event) => applyPreset(event.target.value)}
        >
          <option value="">Custom style</option>
          {presets.map((preset) => (
            <option key={preset.preset_id} value={preset.preset_id}>
              {preset.name}
            </option>
          ))}
        </select>
        <div className="style-preset-save">
          <input
            value={presetName}
            maxLength={60}
            placeholder="Preset name"
            aria-label="Style preset name"
            onChange={(event) => {
              setPresetName(event.target.value);
              setPresetError(null);
            }}
          />
          <button
            type="submit"
            disabled={!presetName.trim() || presetsLoading || presetSaving}
          >
            <FloppyDiskIcon size={13} />
            {presetSaving
              ? "Saving"
              : presets.some(
              (item) =>
                item.name.toLocaleLowerCase() ===
                presetName.trim().toLocaleLowerCase()
            )
              ? "Update"
              : "Save"}
          </button>
        </div>
        {presetError ? <small className="style-preset-error">{presetError}</small> : null}
      </form>

      <div
        className={`caption-generator ${captionTrack?.stale ? "stale" : ""}`}
      >
        <span>
          <strong>Caption timing</strong>
          <small>
            {!canGenerate
              ? "Transcribe the media first"
              : !captionTrack
                ? "No generated caption track"
                : captionTrack.stale
                  ? "Words, lines, or transcript changed"
                  : `${captionTrack.cues.length} captions ready`}
          </small>
        </span>
        <button
          type="button"
          className="primary"
          disabled={!canGenerate || generating}
          onClick={() => void onRegenerate()}
        >
          <ArrowsClockwiseIcon
            size={14}
            className={generating ? "spinning" : ""}
          />
          {generating
            ? "Generating"
            : captionTrack
              ? "Regenerate captions"
              : "Generate captions"}
        </button>
      </div>

      <div className="style-section">
        <strong>Typography</strong>
        <label className="style-select">
          <span>Font</span>
          <select
            value={value.font_family}
            onChange={(event) => patch({ font_family: event.target.value })}
          >
            <option value="Pretendard">Pretendard</option>
            <option value="Arial">Arial</option>
            <option value="Malgun Gothic">Malgun Gothic</option>
            <option value="Noto Sans KR">Noto Sans KR</option>
            <option value="NanumGothic">Nanum Gothic</option>
            <option value="Geist">Geist</option>
            <option value="Georgia">Georgia</option>
            <option value="Impact">Impact</option>
          </select>
        </label>
        <div className="style-segmented two" aria-label="Font emphasis">
          <button
            type="button"
            className={value.font_weight === "bold" ? "active" : ""}
            aria-pressed={value.font_weight === "bold"}
            onClick={() =>
              patch({
                font_weight: value.font_weight === "bold" ? "normal" : "bold"
              })
            }
          >
            <b>B</b>
          </button>
          <button
            type="button"
            className={value.font_style === "italic" ? "active" : ""}
            aria-pressed={value.font_style === "italic"}
            onClick={() =>
              patch({
                font_style: value.font_style === "italic" ? "normal" : "italic"
              })
            }
          >
            <i>I</i>
          </button>
        </div>
        <StyleRange
          label="Size"
          value={value.font_size}
          min={20}
          max={96}
          unit="px"
          onChange={(font_size) => patch({ font_size })}
        />
        <StyleRange
          label="Letter spacing"
          value={value.letter_spacing}
          min={-2}
          max={12}
          step={0.5}
          unit="px"
          onChange={(letter_spacing) => patch({ letter_spacing })}
        />
        <StyleRange
          label="Line spacing"
          value={value.line_spacing}
          min={0.8}
          max={2.5}
          step={0.05}
          unit="x"
          onChange={(line_spacing) => patch({ line_spacing })}
        />
        <StyleRange
          label="Words per line"
          value={value.max_words_per_line}
          min={2}
          max={40}
          unit=""
          onChange={(max_words_per_line) => {
            const widthGrowth =
              max_words_per_line > value.max_words_per_line
                ? max_words_per_line / value.max_words_per_line
                : 1;
            patch({
              max_words_per_line,
              max_width_percent: Math.min(
                96,
                Math.ceil(value.max_width_percent * widthGrowth)
              )
            });
          }}
        />
        {previewWidthLimited && previewWordsPerLine !== null ? (
          <small className="style-limit-note">
            Current size fits {previewWordsPerLine} words before the video edge.
          </small>
        ) : null}
        <StyleRange
          label="Caption lines"
          value={value.max_lines}
          min={1}
          max={4}
          unit={value.max_lines === 1 ? " line" : " lines"}
          onChange={(max_lines) => patch({ max_lines })}
        />
        <StyleColor
          label="Text"
          value={value.text_color}
          onChange={(text_color) => patch({ text_color })}
        />
      </div>

      <div className="style-section">
        <strong>Layout</strong>
        <span className="style-control-label">Alignment</span>
        <div className="style-segmented three">
          {(["left", "center", "right"] as const).map((alignment) => (
            <button
              type="button"
              key={alignment}
              className={value.alignment === alignment ? "active" : ""}
              onClick={() => patch({ alignment })}
            >
              {alignment.charAt(0).toUpperCase()}
            </button>
          ))}
        </div>
        <span className="style-control-label">Position</span>
        <div className="style-segmented three position">
          {(["top", "middle", "bottom"] as const).map((position) => (
            <button
              type="button"
              key={position}
              className={value.position === position ? "active" : ""}
              onClick={() => patch({ position })}
            >
              {position === "middle" ? "Center" : position}
            </button>
          ))}
        </div>
        <StyleRange
          label="Maximum width"
          value={value.max_width_percent}
          min={40}
          max={96}
          unit="%"
          onChange={(max_width_percent) => patch({ max_width_percent })}
        />
        <StyleRange
          label="Screen margin"
          value={value.margin_vertical}
          min={0}
          max={300}
          step={2}
          unit="px"
          onChange={(margin_vertical) => patch({ margin_vertical })}
        />
      </div>

      <div className="style-section">
        <label className="style-toggle">
          <span>
            <strong>Background</strong>
            <small>Place a box behind each caption</small>
          </span>
          <input
            type="checkbox"
            checked={value.background_enabled}
            onChange={(event) =>
              patch({ background_enabled: event.target.checked })
            }
          />
          <i aria-hidden="true" />
        </label>
        <fieldset disabled={!value.background_enabled}>
          <StyleColor
            label="Background"
            value={value.background_color}
            onChange={(background_color) => patch({ background_color })}
          />
          <StyleRange
            label="Opacity"
            value={Math.round(value.background_opacity * 100)}
            min={0}
            max={100}
            unit="%"
            onChange={(opacity) =>
              patch({ background_opacity: opacity / 100 })
            }
          />
          <StyleRange
            label="Side padding"
            value={value.background_padding_x}
            min={0}
            max={80}
            unit="px"
            onChange={(background_padding_x) =>
              patch({ background_padding_x })
            }
          />
          <StyleRange
            label="Top / bottom"
            value={value.background_padding_y}
            min={0}
            max={50}
            unit="px"
            onChange={(background_padding_y) =>
              patch({ background_padding_y })
            }
          />
          <StyleRange
            label="Corner radius"
            value={value.background_radius}
            min={0}
            max={30}
            unit="px"
            onChange={(background_radius) => patch({ background_radius })}
          />
        </fieldset>
      </div>

      <div className="style-section">
        <strong>Edge and shadow</strong>
        <StyleColor
          label="Edge"
          value={value.outline_color}
          onChange={(outline_color) => patch({ outline_color })}
        />
        <StyleRange
          label="Edge size"
          value={value.outline_size}
          min={0}
          max={8}
          step={0.5}
          unit="px"
          onChange={(outline_size) => patch({ outline_size })}
        />
        <StyleRange
          label="Shadow"
          value={value.shadow_size}
          min={0}
          max={8}
          step={0.5}
          unit="px"
          onChange={(shadow_size) => patch({ shadow_size })}
        />
      </div>
    </div>
  );
}

function StyleRange({
  label,
  value,
  min,
  max,
  step = 1,
  unit,
  onChange
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="style-range">
      <span>
        {label}
        <output>{Number.isInteger(value) ? value : value.toFixed(2)}{unit}</output>
      </span>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function StyleColor({
  label,
  value,
  onChange
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="style-color">
      <span>{label}</span>
      <input
        type="color"
        value={value}
        onChange={(event) => onChange(event.target.value.toUpperCase())}
      />
      <output>{value.toUpperCase()}</output>
    </label>
  );
}

function Pipeline({
  project,
  clip,
  job,
  runtime,
  voiceProfileCount,
  onStart,
  onRunAll,
  onExpectedSpeakerCount,
  onTogglePause,
  onStop,
  etaMs,
  mediaPreparation
}: {
  project: Project;
  clip: TimestampClip;
  job: Job | null;
  runtime: RuntimeStatus | null;
  voiceProfileCount: number;
  onStart: (stage: "transcribe" | "diarize" | "pass-1" | "pass-2" | "translate") => void;
  onRunAll: () => void;
  onExpectedSpeakerCount: (count: number | null) => Promise<void>;
  onTogglePause: () => void;
  onStop: () => void;
  etaMs: number | null;
  mediaPreparation: MediaPreparation | null;
}) {
  const ranks: Record<string, number> = {
    draft: 0, media_ready: 1, speakers_detected: 2, transcribed: 3,
    corrected_pass_1: 4, corrected: 5, translated: 6
  };
  const rank = ranks[clip.status] ?? 0;
  const clipDurationMs = clip.end_ms - clip.start_ms;
  const steps = [
    {
      title: "Detect speakers",
      detail: !runtime?.diarization
        ? "Speaker detection unavailable"
        : runtime.diarization_configured
          ? voiceProfileCount
            ? `${voiceProfileCount} enrolled hosts · selected clips`
            : project.expected_speaker_count
              ? `Full episode · ${project.expected_speaker_count} speakers`
              : "Full episode · Auto speaker count"
          : "Hugging Face token needed in Settings",
      enabled:
        rank >= 1 &&
        !!runtime?.diarization &&
        !!runtime?.diarization_configured,
      done: rank >= 2,
      action: () => onStart("diarize")
    },
    {
      title: "Transcribe Korean",
      detail: runtime?.whisper
        ? `This clip · ${formatDuration(clipDurationMs)} · Whisper large-v3`
        : "Whisper unavailable",
      enabled: rank >= 2 && !!runtime?.whisper,
      done: rank >= 3,
      action: () => onStart("transcribe")
    },
    {
      title: "Local correction",
      detail: runtime?.openrouter_configured
        ? "Nearby dialogue · OpenRouter"
        : "OpenRouter API key needed",
      enabled: rank >= 3 && !!runtime?.openrouter_configured,
      done: rank >= 4,
      action: () => onStart("pass-1")
    },
    {
      title: "Episode consistency",
      detail: runtime?.openrouter_configured
        ? "Names and terms · OpenRouter"
        : "OpenRouter API key needed",
      enabled: rank >= 4 && !!runtime?.openrouter_configured,
      done: rank >= 5,
      action: () => onStart("pass-2")
    },
    {
      title: "Conversational English",
      detail: runtime?.openrouter_configured
        ? "Meaning-first · OpenRouter"
        : "OpenRouter API key needed",
      enabled: rank >= 5 && !!runtime?.openrouter_configured,
      done: rank >= 6,
      action: () => onStart("translate")
    }
  ];
  const busy = !!mediaPreparation || (job && !isJobTerminal(job));
  const canRunAll =
    rank >= 1 &&
    rank < 6 &&
    !!runtime?.openrouter_configured &&
    (rank >= 2 ||
      (!!runtime?.diarization && !!runtime?.diarization_configured)) &&
    (rank >= 3 || !!runtime?.whisper);
  const preparationCopy = mediaPreparation
    ? describeMediaPreparation(mediaPreparation)
    : null;
  const jobTimingDetail = job?.paused
    ? "Paused · progress is saved"
    : etaMs !== null
      ? `ETA ${etaClock(etaMs)} · ${formatEta(etaMs)}`
      : job?.stage === "preparing_model"
        ? "ETA starts after the model is ready"
        : "Calculating ETA…";
  const jobDetail =
    job?.stage === "exporting_video" && job.encoder_name
      ? `${job.encoder_name} · ${jobTimingDetail}`
      : job?.pipeline && !job.pipeline_completed
        ? `Step ${job.pipeline_step} of ${job.pipeline_total} · ${jobTimingDetail}`
      : jobTimingDetail;

  return (
    <div className="pipeline">
      <button
        type="button"
        className={`run-all-button ${rank >= 6 ? "done" : ""}`}
        disabled={!canRunAll || !!busy}
        onClick={onRunAll}
        title="Run every remaining stage through English translation"
      >
        {rank >= 6 ? (
          <CheckIcon size={16} weight="bold" />
        ) : (
          <SparkleIcon size={16} weight="fill" />
        )}
        {rank >= 6 ? "English transcript ready" : "Create English transcript"}
      </button>
      {rank >= 1 ? (
        <label className="speaker-count-control">
          <span>
            <strong>Expected speakers</strong>
            <small>Use a fixed count when Auto merges voices.</small>
          </span>
          <select
            value={project.expected_speaker_count ?? ""}
            disabled={!!busy}
            onChange={(event) => {
              const value = event.target.value;
              void onExpectedSpeakerCount(
                value ? Number(value) : null
              );
            }}
          >
            <option value="">Auto</option>
            {Array.from({ length: 8 }, (_, index) => index + 1).map(
              (count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              )
            )}
          </select>
        </label>
      ) : null}
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
                {preparationCopy?.detail ?? jobDetail}
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
                      : `scaleX(${job?.overall_progress ?? job?.progress ?? 0.15})`
              }}
            />
          </div>
          {!mediaPreparation && job ? (
            <div className="job-controls">
              <button
                type="button"
                className="job-control"
                onClick={onTogglePause}
                title={job.paused ? "Resume task" : "Pause task"}
              >
                {job.paused ? (
                  <PlayIcon size={14} weight="fill" />
                ) : (
                  <PauseIcon size={14} weight="fill" />
                )}
                {job.paused ? "Resume" : "Pause"}
              </button>
              <button
                type="button"
                className="job-control danger"
                onClick={onStop}
                title="Stop task"
              >
                <StopIcon size={14} weight="fill" />
                Stop
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="privacy-note">
        <CloudSlashIcon size={16} />
        <span><strong>Hybrid processing</strong><small>Media stays local. Transcript text is sent to OpenRouter.</small></span>
      </div>
    </div>
  );
}

function PostCopyPanel({
  clip,
  segments,
  postCopy,
  generating,
  onGenerate,
  onChange,
  onSave,
  onError
}: {
  clip: TimestampClip;
  segments: Segment[];
  postCopy?: PostCopy;
  generating: boolean;
  onGenerate: (clipId: string) => Promise<void>;
  onChange: (
    clipId: string,
    patch: Pick<Partial<PostCopy>, "headline" | "body">
  ) => void;
  onSave: (
    clipId: string,
    patch: Pick<Partial<PostCopy>, "headline" | "body">
  ) => Promise<void>;
  onError: (message: string) => void;
}) {
  const [copiedClipId, setCopiedClipId] = useState<string | null>(null);
  const translated = segments.some((segment) => segment.english.trim());

  async function copyPost(postCopy: PostCopy) {
    try {
      await window.navigator.clipboard.writeText(
        `${postCopy.headline.trim()}\n\n${postCopy.body.trim()}`
      );
      setCopiedClipId(postCopy.clip_id);
      window.setTimeout(() => setCopiedClipId(null), 1_500);
    } catch {
      onError("Could not copy the post copy to the clipboard.");
    }
  }

  return (
    <div className="post-copy-panel">
      <div className="post-copy-heading">
        <span>
          <strong>Post copy</strong>
          <small>For the active clip tab</small>
        </span>
      </div>
      <section className="post-copy-item">
        <header>
          <span>
            <strong>{clip.title}</strong>
            {postCopy?.stale ? <em>Transcript changed</em> : null}
          </span>
          {postCopy ? (
            <button
              type="button"
              className="secondary post-copy-button"
              onClick={() => void copyPost(postCopy)}
            >
              {copiedClipId === clip.clip_id ? (
                <CheckIcon size={13} />
              ) : (
                <CopyIcon size={13} />
              )}
              {copiedClipId === clip.clip_id ? "Copied" : "Copy"}
            </button>
          ) : null}
        </header>
        {postCopy ? (
          <>
            <label>
              Headline
              <textarea
                rows={2}
                value={postCopy.headline}
                onChange={(event) =>
                  onChange(clip.clip_id, { headline: event.target.value })
                }
                onBlur={(event) => {
                  const headline = event.currentTarget.value.trim();
                  if (headline) void onSave(clip.clip_id, { headline });
                }}
              />
            </label>
            <label>
              Quotes
              <textarea
                rows={9}
                value={postCopy.body}
                onChange={(event) =>
                  onChange(clip.clip_id, { body: event.target.value })
                }
                onBlur={(event) => {
                  const body = event.currentTarget.value.trim();
                  if (body) void onSave(clip.clip_id, { body });
                }}
              />
            </label>
            <div className="post-copy-actions">
              <button
                type="button"
                className="secondary"
                disabled={generating || !translated}
                onClick={() => void onGenerate(clip.clip_id)}
              >
                <ArrowsClockwiseIcon size={13} />
                {generating ? "Generating..." : "Regenerate"}
              </button>
            </div>
          </>
        ) : (
          <button
            type="button"
            className="post-copy-generate"
            disabled={generating || !translated}
            onClick={() => void onGenerate(clip.clip_id)}
          >
            <SparkleIcon size={14} />
            {generating
              ? "Generating..."
              : translated
                ? "Generate post copy"
                : "Translate this clip first"}
          </button>
        )}
      </section>
    </div>
  );
}

function ClipPanel({
  markers,
  clipsByMarkerId,
  disabled,
  onImportMarkers,
  playbackMarkerId,
  onNavigateMarker,
  onTranscribe,
  onError
}: {
  markers: NavigationMarker[];
  clipsByMarkerId: Map<string, TimestampClip>;
  disabled: boolean;
  onImportMarkers: (text: string) => Promise<void>;
  playbackMarkerId: string | null;
  onNavigateMarker: (marker: NavigationMarker) => void;
  onTranscribe: (
    marker: NavigationMarker,
    markerIndex: number
  ) => Promise<void>;
  onError: (message: string) => void;
}) {
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(markers.length === 0);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (markers.length === 0) {
      setEditing(true);
      setText("");
    }
  }, [markers.length]);

  async function importTimestamps(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    onError("");
    try {
      await onImportMarkers(text);
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

  return (
    <div className="timestamp-panel">
      <div className="timestamp-heading">
        <span>
          <strong>Clips</strong>
          <small>
            {markers.length
              ? `${markers.length} navigation timestamps`
              : "Paste your timestamp list"}
          </small>
        </span>
        {markers.length && !editing ? (
          <button
            type="button"
            onClick={() => setEditing(true)}
            disabled={disabled || saving}
          >
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
              rows={6}
              disabled={disabled || saving}
            />
          </label>
          <small>
            Navigation timestamps stay separate from clip workspace
            boundaries.
          </small>
          <div>
            {markers.length ? (
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
              {saving ? "Importing…" : "Import markers"}
            </button>
          </div>
        </form>
      ) : (
        <div className="timestamp-list">
          {markers.map((marker, index) => {
            const clip = clipsByMarkerId.get(marker.marker_id);
            return (
              <div
                className={`timestamp-row ${
                  playbackMarkerId === marker.marker_id ? "active" : ""
                }`}
                key={marker.marker_id}
              >
                <button
                  type="button"
                  className="timestamp-navigate"
                  onClick={() => onNavigateMarker(marker)}
                  disabled={disabled || saving}
                  aria-label={`Go to ${marker.title} at ${formatTimestamp(
                    marker.timestamp_ms
                  )}`}
                >
                  <span>
                    <small>{formatTimestamp(marker.timestamp_ms)}</small>
                    <strong>{marker.title}</strong>
                  </span>
                </button>
                <button
                  type="button"
                  className={`timestamp-transcribe ${
                    clip?.opened ? "opened" : ""
                  }`}
                  onClick={() => void onTranscribe(marker, index)}
                  disabled={disabled || saving}
                  aria-label={
                    clip?.opened
                      ? `Open the ${marker.title} clip tab`
                      : `Create a clip tab for ${marker.title}`
                  }
                  title={clip?.opened ? "Clip tab open" : "Open clip tab"}
                >
                  {clip?.opened ? (
                    <CircleIcon size={11} weight="fill" />
                  ) : (
                    <ArrowRightIcon size={15} />
                  )}
                </button>
              </div>
            );
          })}
          {!markers.length ? (
            <div className="custom-clip-empty">
              <WaveformIcon size={20} />
              <span>Import timestamps to navigate the video.</span>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function SpeakerPanel({
  speakers,
  voiceProfiles,
  disabled,
  showEpisodeSpeakers = true,
  onCreateProfile,
  onDeleteProfile,
  onRename
}: {
  speakers: Speaker[];
  voiceProfiles: VoiceProfile[];
  disabled: boolean;
  showEpisodeSpeakers?: boolean;
  onCreateProfile: (name: string, sample: File) => Promise<void>;
  onDeleteProfile: (profileId: string) => Promise<void>;
  onRename: (speakerId: string, name: string) => Promise<void>;
}) {
  const [hostName, setHostName] = useState("");
  const [sample, setSample] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const sampleRef = useRef<HTMLInputElement | null>(null);

  async function enrollHost() {
    if (!hostName.trim() || !sample) return;
    setSaving(true);
    setProfileError(null);
    try {
      await onCreateProfile(hostName.trim(), sample);
      setHostName("");
      setSample(null);
      if (sampleRef.current) sampleRef.current.value = "";
    } catch (reason) {
      setProfileError(
        reason instanceof Error
          ? reason.message
          : "Could not create the voice profile."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="speaker-panel">
      <div className="glossary-heading">
        <div>
          <strong>Regular hosts</strong>
          <small>Reusable local voice profiles</small>
        </div>
        <UsersThreeIcon size={18} />
      </div>
      <p className="voice-profile-guidance">
        Use 30–90 seconds of clean solo speech. Longer samples are accepted
        up to 10 minutes, but music and overlapping voices reduce accuracy.
      </p>
      {voiceProfiles.length ? (
        <div className="voice-profile-list">
          {voiceProfiles.map((profile) => (
            <div key={profile.profile_id}>
              <span>
                <strong>{profile.name}</strong>
                <small>
                  {formatTime(profile.duration_ms)} · {profile.sample_name}
                </small>
              </span>
              <button
                type="button"
                title={`Remove ${profile.name}`}
                disabled={disabled || saving}
                onClick={async () => {
                  setProfileError(null);
                  try {
                    await onDeleteProfile(profile.profile_id);
                  } catch (reason) {
                    setProfileError(
                      reason instanceof Error
                        ? reason.message
                        : "Could not remove the voice profile."
                    );
                  }
                }}
              >
                <TrashIcon size={14} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="voice-profile-empty">
          No regular hosts enrolled yet.
        </div>
      )}
      <div className="voice-profile-form">
        <label>
          Host name
          <input
            value={hostName}
            disabled={disabled || saving}
            maxLength={80}
            placeholder="e.g. Juhyuk"
            onChange={(event) => setHostName(event.target.value)}
          />
        </label>
        <label>
          Voice sample
          <input
            ref={sampleRef}
            type="file"
            accept=".mp4,.mov,.mkv,.mp3,.wav,.m4a,.aac"
            disabled={disabled || saving}
            onChange={(event) =>
              setSample(event.target.files?.[0] ?? null)
            }
          />
        </label>
        <button
          type="button"
          className="primary"
          disabled={
            disabled || saving || !hostName.trim() || sample === null
          }
          onClick={() => void enrollHost()}
        >
          <UploadSimpleIcon size={13} />
          {saving ? "Creating profile…" : "Enroll host"}
        </button>
      </div>
      {profileError ? (
        <p className="voice-profile-error">{profileError}</p>
      ) : null}
      {showEpisodeSpeakers ? (
        <>
          <div className="speaker-section-heading">
            <strong>Episode speakers</strong>
            <small>Names used during correction and translation</small>
          </div>
          {speakers.length === 0 ? (
            <div className="glossary-empty">
              <UsersThreeIcon size={24} />
              <p>Add speaker names when creating a project.</p>
            </div>
          ) : (
            <div className="speaker-list">
              {speakers.map((speaker) => {
                const matchedProfile = voiceProfiles.find(
                  (profile) => profile.profile_id === speaker.speaker_id
                );
                return (
                  <label key={speaker.speaker_id}>
                    <span>
                      {speaker.speaker_id.replace("_", " ")}
                      {matchedProfile ? <b>Voice profile matched</b> : null}
                    </span>
                    <input
                      defaultValue={speaker.name}
                      onBlur={(event) => {
                        const name = event.currentTarget.value.trim();
                        if (name && name !== speaker.name) {
                          void onRename(speaker.speaker_id, name);
                        }
                      }}
                    />
                  </label>
                );
              })}
            </div>
          )}
        </>
      ) : null}
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
  clipTitle,
  speakers,
  selected,
  rowRef,
  onSelect,
  onPatch
}: {
  segment: Segment;
  index: number;
  clipTitle?: string;
  speakers: Speaker[];
  selected: boolean;
  rowRef: (node: HTMLDivElement | null) => void;
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
      ref={rowRef}
      className={`segment-row ${selected ? "selected" : ""} ${hasWarning ? "warning" : ""}`}
      style={{ "--delay": `${Math.min(index, 12) * 35}ms` } as React.CSSProperties}
      onClick={onSelect}
    >
      <div className="segment-meta">
        <span className="segment-time">{formatTime(segment.start_ms)}</span>
        {clipTitle ? (
          <strong className="segment-clip-title">{clipTitle}</strong>
        ) : null}
        <select
          value={segment.speaker_id ?? ""}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => void onPatch({ speaker_id: event.target.value || null })}
        >
          <option value="">Speaker</option>
          {speakers.map((speaker) => (
            <option key={speaker.speaker_id} value={speaker.speaker_id}>
              {speaker.name}
            </option>
          ))}
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

function InlineError({ message }: { message: unknown }) {
  const displayMessage = readableErrorMessage(message);
  return (
    <div className="inline-error" role="alert">
      <WarningCircleIcon size={18} weight="fill" />
      <span>{displayMessage}</span>
      {displayMessage.toLowerCase().includes("credits") ? (
        <a
          href="https://openrouter.ai/settings/credits"
          target="_blank"
          rel="noreferrer"
        >
          Add credits
        </a>
      ) : null}
    </div>
  );
}

export default App;
