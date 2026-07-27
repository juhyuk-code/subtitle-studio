export function formatSrtTimestamp(milliseconds: number): string {
  const safeMs = Math.max(0, Math.round(milliseconds));
  const hours = Math.floor(safeMs / 3_600_000);
  const minutes = Math.floor((safeMs % 3_600_000) / 60_000);
  const seconds = Math.floor((safeMs % 60_000) / 1_000);
  const ms = safeMs % 1_000;
  return [hours, minutes, seconds]
    .map((part) => String(part).padStart(2, "0"))
    .join(":")
    .concat(",", String(ms).padStart(3, "0"));
}

export interface ExportCue {
  id: string;
  startMs: number;
  endMs: number;
  lines: string[];
}

export function toSrt(cues: ExportCue[]): string {
  let previousEnd = -1;
  return cues
    .map((cue, index) => {
      if (cue.endMs <= cue.startMs) {
        throw new Error(`Cue ${cue.id} must end after it starts.`);
      }
      if (cue.startMs < previousEnd) {
        throw new Error(`Cue ${cue.id} overlaps the previous cue.`);
      }
      previousEnd = cue.endMs;
      return `${index + 1}\n${formatSrtTimestamp(cue.startMs)} --> ${formatSrtTimestamp(cue.endMs)}\n${cue.lines.join("\n")}\n`;
    })
    .join("\n");
}
