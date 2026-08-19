import type { ShortformIdea, Segment } from "./types";

interface ShortformIdeasListProps {
  ideas: ShortformIdea[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

export function ShortformIdeasList({
  ideas,
  selectedId,
  onSelect,
}: ShortformIdeasListProps) {
  if (ideas.length === 0) {
    return (
      <div
        className="shortform-empty"
        style={{ padding: "16px", color: "var(--muted-foreground)", fontSize: "13px" }}
      >
        <p>No shortform ideas yet.</p>
        <p style={{ marginTop: 8 }}>
          Run the full transcription pipeline (Transcribe Korean → Conversational
          English) and ideas will appear here automatically.
        </p>
      </div>
    );
  }

  return (
    <div className="shortform-ideas-list">
      {ideas.map((idea) => {
        const active = idea.idea_id === selectedId;
        return (
          <div
            key={idea.idea_id}
            className={`shortform-idea-card ${active ? "selected" : ""}`}
            style={{
              padding: "8px 12px",
              borderBottom: "1px solid var(--border)",
              cursor: "pointer",
              background: active ? "var(--accent-muted, rgba(0,0,0,0.06))" : "transparent",
              borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
            }}
            onClick={() => onSelect(active ? null : idea.idea_id)}
          >
            <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: 2 }}>
              {idea.title}
            </div>
            <div
              style={{
                fontSize: "11px",
                color: "var(--muted-foreground)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {idea.hook}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// --- Main area detail panel ---

interface ShortformIdeaDetailProps {
  idea: ShortformIdea;
  segments: Segment[];
}

export function ShortformIdeaDetail({
  idea,
  segments,
}: ShortformIdeaDetailProps) {
  const segmentById = new Map(segments.map((s) => [s.segment_id, s]));

  return (
    <div
      style={{
        padding: "24px",
        maxWidth: 860,
        margin: "0 auto",
        height: "100%",
        overflow: "auto",
      }}
    >
      <h2 style={{ fontSize: "18px", fontWeight: 700, marginBottom: 8 }}>
        {idea.title}
      </h2>
      <p
        style={{
          fontSize: "14px",
          color: "var(--muted-foreground)",
          marginBottom: 6,
        }}
      >
        {idea.hook}
      </p>
      {idea.rationale ? (
        <p
          style={{
            fontSize: "13px",
            fontStyle: "italic",
            marginBottom: 16,
            color: "var(--muted-foreground)",
          }}
        >
          {idea.rationale}
        </p>
      ) : null}

      <div
        style={{
          fontSize: "12px",
          color: "var(--muted-foreground)",
          marginBottom: 16,
        }}
      >
        {idea.parts.length} part{idea.parts.length !== 1 ? "s" : ""} ·{" "}
        {Math.round(idea.total_duration_ms / 1000)}s total
      </div>

      {idea.parts.map((part, idx) => {
        const startStr = fmtMs(part.start_ms);
        const endStr = fmtMs(part.end_ms);
        const duration = Math.round((part.end_ms - part.start_ms) / 1000);

        const lines: string[] = [];
        for (const sid of part.segment_ids) {
          const seg = segmentById.get(sid);
          if (!seg) continue;
          const text =
            seg.pass_2_korean?.trim() ||
            seg.pass_1_korean?.trim() ||
            seg.raw_korean?.trim() ||
            seg.english?.trim();
          if (text) lines.push(text);
        }

        return (
          <div
            key={idx}
            style={{
              marginBottom: 16,
              padding: "12px 14px",
              background: "var(--card)",
              borderRadius: 8,
              border: "1px solid var(--border)",
            }}
          >
            <div
              style={{
                fontWeight: 700,
                marginBottom: 4,
                color: "var(--accent)",
                fontSize: "13px",
              }}
            >
              Part {idx + 1} — {startStr} → {endStr} ({duration}s)
            </div>
            {part.note ? (
              <div
                style={{
                  fontStyle: "italic",
                  marginBottom: 8,
                  color: "var(--muted-foreground)",
                  fontSize: "12px",
                }}
              >
                {part.note}
              </div>
            ) : null}
            {lines.length > 0 ? (
              <div style={{ lineHeight: 1.6, fontSize: "13px" }}>
                {lines.map((line, i) => (
                  <div key={i} style={{ marginBottom: 4 }}>
                    {line}
                  </div>
                ))}
              </div>
            ) : (
              <div
                style={{ color: "var(--muted-foreground)", fontSize: "12px" }}
              >
                (Transcript not yet loaded for these segments)
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function fmtMs(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const remain = minutes % 60;
    return `${hours}:${String(remain).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}