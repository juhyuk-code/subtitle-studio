export type MediaPreparation = {
  phase: "uploading" | "processing";
  progress: number;
  filename?: string;
};

export function describeMediaPreparation(state: MediaPreparation) {
  if (state.phase === "uploading") {
    return {
      title: `Uploading video · ${Math.round(state.progress * 100)}%`,
      detail: "Keep Subtitle Studio open while the file is copied."
    };
  }
  return {
    title: "Preparing audio locally…",
    detail: "Long videos can take several minutes. Subtitle Studio is still working."
  };
}
