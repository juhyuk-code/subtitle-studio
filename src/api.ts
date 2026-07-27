import type {
  GlossaryEntry,
  Job,
  OpenRouterSettings,
  Project,
  RuntimeStatus,
  Segment,
  TimestampClip,
  TranslationProfile
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "The request could not be completed.");
  }
  return (response.status === 204 ? undefined : response.json()) as Promise<T>;
}

export const api = {
  runtime: () => request<RuntimeStatus>("/api/runtime"),
  openRouterSettings: () =>
    request<OpenRouterSettings>("/api/settings/openrouter"),
  saveOpenRouterSettings: (apiKey: string) =>
    request<OpenRouterSettings>("/api/settings/openrouter", {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey })
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
            xhr.response?.detail ?? xhr.statusText ?? "Upload failed."
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
  clips: (id: string) =>
    request<TimestampClip[]>(`/api/projects/${id}/clips`),
  importClips: (id: string, text: string) =>
    request<TimestampClip[]>(`/api/projects/${id}/clips`, {
      method: "PUT",
      body: JSON.stringify({ text })
    }),
  selectClip: (projectId: string, clipId: string, selected: boolean) =>
    request<TimestampClip>(`/api/projects/${projectId}/clips/${clipId}`, {
      method: "PATCH",
      body: JSON.stringify({ selected })
    }),
  patchSegment: (projectId: string, segmentId: string, patch: Partial<Segment>) =>
    request<Segment>(`/api/projects/${projectId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify(patch)
    }),
  startStage: (id: string, stage: "transcribe" | "pass-1" | "pass-2" | "translate") => {
    const path =
      stage === "transcribe"
        ? "transcribe?model=small"
        : stage === "translate"
          ? "translate"
          : `correct/${stage}`;
    return request<Job>(`/api/projects/${id}/${path}`, { method: "POST" });
  },
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) =>
    request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
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
