import type {
  AppPreferences,
  CaptionTrack,
  GlossaryEntry,
  Job,
  MediaTimelineInfo,
  NavigationMarker,
  OpenRouterModel,
  OpenRouterSettings,
  PostCopy,
  Project,
  ProjectWorkspaceState,
  RuntimeStatus,
  Segment,
  Speaker,
  SpeakerDetectionSettings,
  SubtitleStyle,
  SubtitleStylePreset,
  TimestampClip,
  TranslationProfile,
  VideoExportFolderSettings,
  VoiceProfile
} from "./types";
import { readableErrorMessage } from "./lib/errors";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const body: unknown = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(
      readableErrorMessage(
        body,
        response.statusText || "The request could not be completed."
      )
    );
  }
  return (response.status === 204 ? undefined : response.json()) as Promise<T>;
}

export const api = {
  runtime: () => request<RuntimeStatus>("/api/runtime"),
  appPreferences: () =>
    request<AppPreferences>("/api/settings/app-preferences"),
  updateAppPreferences: (patch: Partial<AppPreferences>) =>
    request<AppPreferences>("/api/settings/app-preferences", {
      method: "PATCH",
      body: JSON.stringify(patch)
    }),
  openRouterSettings: () =>
    request<OpenRouterSettings>("/api/settings/openrouter"),
  openRouterModels: () =>
    request<OpenRouterModel[]>("/api/openrouter/models"),
  videoExportFolderSettings: () =>
    request<VideoExportFolderSettings>(
      "/api/settings/video-export-folder"
    ),
  saveVideoExportFolder: (path: string) =>
    request<VideoExportFolderSettings>(
      "/api/settings/video-export-folder",
      {
        method: "PUT",
        body: JSON.stringify({ path })
      }
    ),
  resetVideoExportFolder: () =>
    request<VideoExportFolderSettings>(
      "/api/settings/video-export-folder",
      {
        method: "PUT",
        body: JSON.stringify({ path: null })
      }
    ),
  speakerDetectionSettings: () =>
    request<SpeakerDetectionSettings>("/api/settings/speaker-detection"),
  saveSpeakerDetectionSettings: (huggingfaceToken?: string) =>
    request<SpeakerDetectionSettings>("/api/settings/speaker-detection", {
      method: "PUT",
      body: JSON.stringify({
        huggingface_token: huggingfaceToken || undefined
      })
    }),
  saveOpenRouterSettings: (settings: {
    apiKey?: string;
    correctionModel: string;
    translationModel: string;
    postCopyModel: string;
  }) =>
    request<OpenRouterSettings>("/api/settings/openrouter", {
      method: "PUT",
      body: JSON.stringify({
        api_key: settings.apiKey || undefined,
        correction_model: settings.correctionModel,
        translation_model: settings.translationModel,
        post_copy_model: settings.postCopyModel
      })
    }),
  projects: () => request<Project[]>("/api/projects"),
  createProject: (data: {
    name: string;
    description: string;
    speakers: string[];
    translation_profile: TranslationProfile;
    custom_instructions: string;
  }) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(data)
    }),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  timelineInfo: (id: string) =>
    request<MediaTimelineInfo>(`/api/projects/${id}/timeline-info`),
  waveformUrl: (
    baseUrl: string,
    options: {
      startMs: number;
      endMs: number;
      width?: number;
      height?: number;
      version?: string;
    }
  ) => {
    const query = new URLSearchParams({
      start_ms: String(Math.round(options.startMs)),
      end_ms: String(Math.round(options.endMs)),
      width: String(options.width ?? 2048),
      height: String(options.height ?? 128)
    });
    if (options.version) query.set("v", options.version);
    return `${baseUrl}?${query.toString()}`;
  },
  workspace: (id: string) =>
    request<ProjectWorkspaceState>(`/api/projects/${id}/workspace`),
  updateWorkspace: (
    id: string,
    patch: Partial<ProjectWorkspaceState>
  ) =>
    request<ProjectWorkspaceState>(`/api/projects/${id}/workspace`, {
      method: "PATCH",
      body: JSON.stringify(patch)
    }),
  updateExpectedSpeakerCount: (
    id: string,
    expectedSpeakerCount: number | null
  ) =>
    request<Project>(`/api/projects/${id}/speaker-settings`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_speaker_count: expectedSpeakerCount
      })
    }),
  updateSubtitleStyle: (id: string, patch: Partial<SubtitleStyle>) =>
    request<SubtitleStyle>(`/api/projects/${id}/subtitle-style`, {
      method: "PATCH",
      body: JSON.stringify(patch)
    }),
  stylePresets: () =>
    request<SubtitleStylePreset[]>("/api/style-presets"),
  createStylePreset: (name: string, style: SubtitleStyle) =>
    request<SubtitleStylePreset>("/api/style-presets", {
      method: "POST",
      body: JSON.stringify({ name, style })
    }),
  updateStylePreset: (
    presetId: string,
    name: string,
    style: SubtitleStyle
  ) =>
    request<SubtitleStylePreset>(`/api/style-presets/${presetId}`, {
      method: "PUT",
      body: JSON.stringify({ name, style })
    }),
  deleteStylePreset: (presetId: string) =>
    request<void>(`/api/style-presets/${presetId}`, {
      method: "DELETE"
    }),
  activeJob: (id: string) =>
    request<Job | null>(`/api/projects/${id}/jobs/active`),
  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  upload: (
    id: string,
    file: File,
    callbacks?: {
      onProgress?: (progress: number) => void;
      onProcessing?: () => void;
    }
  ) => {
    const body = new FormData();
    body.append("media", file);
    return new Promise<Project>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/projects/${id}/media`);
      xhr.responseType = "json";
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          callbacks?.onProgress?.(event.loaded / event.total);
        }
      };
      xhr.upload.onload = () => callbacks?.onProcessing?.();
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(xhr.response as Project);
          return;
        }
        reject(
          new Error(
            readableErrorMessage(
              xhr.response,
              xhr.statusText || "Upload failed."
            )
          )
        );
      };
      xhr.onerror = () =>
        reject(new Error("The upload was interrupted. Please try again."));
      xhr.send(body);
    });
  },
  segments: (id: string) =>
    request<Segment[]>(`/api/projects/${id}/segments`),
  captionTrack: (id: string, language: "ko" | "en" = "en") =>
    request<CaptionTrack | null>(
      `/api/projects/${id}/captions?language=${language}`
    ),
  regenerateCaptions: (
    id: string,
    options: {
      language: "ko" | "en";
      maxWordsPerLine: number;
      maxLines: number;
      clipId?: string;
    }
  ) =>
    request<CaptionTrack>(
      `/api/projects/${id}/captions/regenerate`,
      {
        method: "POST",
        body: JSON.stringify({
          language: options.language,
          max_words_per_line: options.maxWordsPerLine,
          max_lines: options.maxLines,
          clip_id: options.clipId
        })
      }
    ),
  exportVideo: (
    id: string,
    options: {
      resolution: "1080p" | "source";
      quality: "high" | "maximum";
      encoder: "gpu" | "cpu";
      clipIds: string[];
      includeVideo: boolean;
      includeSrt: boolean;
      includeAss: boolean;
    }
  ) =>
    request<Job>(`/api/projects/${id}/export/video`, {
      method: "POST",
      body: JSON.stringify({
        resolution: options.resolution,
        quality: options.quality,
        encoder: options.encoder,
        clip_ids: options.clipIds,
        include_video: options.includeVideo,
        include_srt: options.includeSrt,
        include_ass: options.includeAss
      })
    }),
  videoExports: (id: string) =>
    request<Job[]>(`/api/projects/${id}/video-exports`),
  openVideoExportFolder: (id: string) =>
    request<{ path: string }>(
      `/api/projects/${id}/video-exports/open-folder`,
      { method: "POST" }
    ),
  speakers: (id: string) =>
    request<Speaker[]>(`/api/projects/${id}/speakers`),
  voiceProfiles: () =>
    request<VoiceProfile[]>("/api/voice-profiles"),
  createVoiceProfile: (name: string, sample: File) => {
    const body = new FormData();
    body.append("name", name);
    body.append("sample", sample);
    return request<VoiceProfile>("/api/voice-profiles", {
      method: "POST",
      body
    });
  },
  deleteVoiceProfile: (profileId: string) =>
    request<void>(`/api/voice-profiles/${profileId}`, {
      method: "DELETE"
    }),
  renameSpeaker: (projectId: string, speakerId: string, name: string) =>
    request<Speaker>(`/api/projects/${projectId}/speakers/${speakerId}`, {
      method: "PATCH",
      body: JSON.stringify({ name })
    }),
  clips: (id: string) =>
    request<TimestampClip[]>(`/api/projects/${id}/clips`),
  postCopies: (id: string) =>
    request<PostCopy[]>(`/api/projects/${id}/post-copies`),
  generatePostCopy: (projectId: string, clipId: string) =>
    request<PostCopy>(
      `/api/projects/${projectId}/post-copies/${clipId}/generate`,
      { method: "POST" }
    ),
  updatePostCopy: (
    projectId: string,
    clipId: string,
    patch: Pick<Partial<PostCopy>, "headline" | "body">
  ) =>
    request<PostCopy>(
      `/api/projects/${projectId}/post-copies/${clipId}`,
      {
        method: "PATCH",
        body: JSON.stringify(patch)
      }
    ),
  createClip: (
    id: string,
    clip: {
      navigation_marker_id?: string;
      start_ms: number;
      end_ms: number;
      title?: string;
    }
  ) =>
    request<TimestampClip>(`/api/projects/${id}/clips`, {
      method: "POST",
      body: JSON.stringify(clip)
    }),
  deleteClip: (projectId: string, clipId: string) =>
    request<void>(`/api/projects/${projectId}/clips/${clipId}`, {
      method: "DELETE"
    }),
  markers: (id: string) =>
    request<NavigationMarker[]>(`/api/projects/${id}/markers`),
  importMarkers: (id: string, text: string) =>
    request<NavigationMarker[]>(`/api/projects/${id}/markers`, {
      method: "PUT",
      body: JSON.stringify({ text })
    }),
  patchClip: (
    projectId: string,
    clipId: string,
    patch: {
      selected?: boolean;
      opened?: boolean;
      start_ms?: number;
      end_ms?: number;
      title?: string;
      render_queued?: boolean;
    }
  ) =>
    request<TimestampClip>(`/api/projects/${projectId}/clips/${clipId}`, {
      method: "PATCH",
      body: JSON.stringify(patch)
    }),
  clearRenderQueue: (projectId: string) =>
    request<TimestampClip[]>(`/api/projects/${projectId}/render-queue`, {
      method: "DELETE"
    }),
  updateClipSubtitleStyle: (
    projectId: string,
    clipId: string,
    patch: Partial<SubtitleStyle>
  ) =>
    request<TimestampClip>(
      `/api/projects/${projectId}/clips/${clipId}/subtitle-style`,
      {
        method: "PATCH",
        body: JSON.stringify(patch)
      }
    ),
  applyClipSubtitleStyleToAll: (projectId: string, clipId: string) =>
    request<TimestampClip[]>(
      `/api/projects/${projectId}/clips/${clipId}/subtitle-style/apply-all`,
      { method: "POST" }
    ),
  patchSegment: (projectId: string, segmentId: string, patch: Partial<Segment>) =>
    request<Segment>(`/api/projects/${projectId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify(patch)
    }),
  startStage: (
    id: string,
    stage: "transcribe" | "diarize" | "pass-1" | "pass-2" | "translate",
    clipId?: string
  ) => {
    const path =
      stage === "transcribe"
        ? "transcribe?model=large-v3"
        : stage === "diarize"
          ? "diarize"
        : stage === "translate"
          ? "translate"
          : `correct/${stage}`;
    const separator = path.includes("?") ? "&" : "?";
    const scopedPath = clipId
      ? `${path}${separator}clip_id=${encodeURIComponent(clipId)}`
      : path;
    return request<Job>(`/api/projects/${id}/${scopedPath}`, {
      method: "POST"
    });
  },
  startEnglishPipeline: (id: string, clipId?: string) =>
    request<Job>(
      `/api/projects/${id}/pipeline/english${
        clipId ? `?clip_id=${encodeURIComponent(clipId)}` : ""
      }`,
      {
      method: "POST"
      }
    ),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) =>
    request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  pauseJob: (id: string) =>
    request<Job>(`/api/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) =>
    request<Job>(`/api/jobs/${id}/resume`, { method: "POST" }),
  glossary: (id: string) =>
    request<GlossaryEntry[]>(`/api/projects/${id}/glossary`),
  addGlossary: (
    id: string,
    entry: Omit<GlossaryEntry, "entry_id"> & { entry_id?: string }
  ) =>
    request<GlossaryEntry>(`/api/projects/${id}/glossary`, {
      method: "POST",
      body: JSON.stringify(entry)
    })
};
