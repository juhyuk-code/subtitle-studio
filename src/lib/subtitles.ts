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

export function wrapCaptionByWords(
  text: string,
  maxWordsPerLine: number
): string {
  return text
    .split(/\r?\n/)
    .flatMap((paragraph) => {
      const words = paragraph.trim().split(/\s+/).filter(Boolean);
      if (words.length === 0) return [""];
      const lines: string[] = [];
      for (let index = 0; index < words.length; index += maxWordsPerLine) {
        lines.push(words.slice(index, index + maxWordsPerLine).join(" "));
      }
      return lines;
    })
    .join("\n");
}

export function paginateCaptionByWords(
  text: string,
  maxWordsPerLine: number,
  maxLines: number
): string[] {
  const safeWordsPerLine = Math.max(1, Math.floor(maxWordsPerLine));
  const safeMaxLines = Math.max(1, Math.floor(maxLines));
  const pages: string[] = [];
  const capacity = safeWordsPerLine * safeMaxLines;
  for (const paragraph of text.split(/\r?\n/)) {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      pages.push("");
      continue;
    }
    for (let index = 0; index < words.length; index += capacity) {
      pages.push(
        balanceCaptionWords(
          words.slice(index, index + capacity),
          safeMaxLines
        ).join("\n")
      );
    }
  }
  return pages.length > 0 ? pages : [""];
}

export function balanceCaptionWords(
  words: string[],
  maxLines: number
): string[] {
  if (words.length === 0) return [""];
  const lineCount = Math.min(
    words.length,
    Math.max(1, Math.floor(maxLines))
  );
  const baseLineSize = Math.floor(words.length / lineCount);
  const longerLineCount = words.length % lineCount;
  const lines: string[] = [];
  let offset = 0;
  for (let lineIndex = 0; lineIndex < lineCount; lineIndex += 1) {
    const lineSize =
      baseLineSize + (lineIndex < longerLineCount ? 1 : 0);
    lines.push(words.slice(offset, offset + lineSize).join(" "));
    offset += lineSize;
  }
  return lines;
}

export function paginateCaptionToWidth(
  text: string,
  maxWordsPerLine: number,
  maxLines: number,
  maxWidth: number,
  measureLine: (line: string) => number
): string[] {
  const safeWordsPerLine = Math.max(1, Math.floor(maxWordsPerLine));
  const safeMaxLines = Math.max(1, Math.floor(maxLines));
  const pages: string[] = [];
  for (const paragraph of text.split(/\r?\n/)) {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      pages.push("");
      continue;
    }
    let currentPage: string[] = [];
    for (const word of words) {
      const candidatePage = [...currentPage, word];
      const candidateLines = balanceCaptionWords(
        candidatePage,
        safeMaxLines
      );
      const exceedsCapacity =
        candidatePage.length > safeWordsPerLine * safeMaxLines;
      const exceedsWidth = candidateLines.some(
        (line) => measureLine(line) > maxWidth
      );
      const exceedsWordLimit = candidateLines.some(
        (line) => line.split(/\s+/).filter(Boolean).length > safeWordsPerLine
      );
      if (
        currentPage.length > 0 &&
        (exceedsCapacity || exceedsWidth || exceedsWordLimit)
      ) {
        pages.push(
          balanceCaptionWords(currentPage, safeMaxLines).join("\n")
        );
        currentPage = [word];
      } else {
        currentPage = candidatePage;
      }
    }
    if (currentPage.length > 0) {
      pages.push(
        balanceCaptionWords(currentPage, safeMaxLines).join("\n")
      );
    }
  }
  return pages.length > 0 ? pages : [""];
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
