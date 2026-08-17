import {
  ArrowsClockwiseIcon,
  CalendarBlankIcon,
  CheckCircleIcon,
  ClockIcon,
  FilmSlateIcon,
  PaperPlaneTiltIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
  WarningCircleIcon,
  XCircleIcon,
  XIcon
} from "@phosphor-icons/react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState
} from "react";
import { api } from "./api";
import type {
  PostCopy,
  Project,
  ScheduledPost,
  TimestampClip,
  XAccountSettingsStatus
} from "./types";

type EnrichedPost = {
  post: ScheduledPost;
  project: Project | null;
  clip: TimestampClip | null;
  postCopy: PostCopy | null;
  videoUrl: string | null;
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function toLocalInputValue(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function statusMeta(status: ScheduledPost["status"]) {
  switch (status) {
    case "posted":
      return { label: "Posted", className: "ok" };
    case "posting":
      return { label: "Posting…", className: "busy" };
    case "failed":
      return { label: "Failed", className: "bad" };
    case "cancelled":
      return { label: "Cancelled", className: "muted" };
    default:
      return { label: "Scheduled", className: "pending" };
  }
}

export function ScheduledPostsPanel({ onClose }: { onClose: () => void }) {
  const [posts, setPosts] = useState<EnrichedPost[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [xSettings, setXSettings] = useState<XAccountSettingsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showConnect, setShowConnect] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [publishing, setPublishing] = useState(false);

  const enrich = useCallback(
    async (allPosts: ScheduledPost[], allProjects: Project[]) => {
      const projectById = new Map(allProjects.map((p) => [p.project_id, p]));
      return Promise.all(
        allPosts.map(async (post): Promise<EnrichedPost> => {
          const project = projectById.get(post.project_id) ?? null;
          let clip: TimestampClip | null = null;
          let postCopy: PostCopy | null = null;
          let videoUrl: string | null = null;
          try {
            const [clips, copies, exports] = await Promise.all([
              api.clips(post.project_id).catch(() => [] as TimestampClip[]),
              api.postCopies(post.project_id).catch(() => [] as PostCopy[]),
              api.videoExports(post.project_id).catch(() => [])
            ]);
            clip = clips.find((c) => c.clip_id === post.clip_id) ?? null;
            postCopy = copies.find((c) => c.clip_id === post.clip_id) ?? null;
            for (const job of exports) {
              const match = job.outputs.find(
                (o) => o.kind === "video" && (!post.clip_id || o.clip_id === post.clip_id)
              );
              if (match) {
                videoUrl = match.output_url;
                break;
              }
            }
          } catch {
            // enrichment is best-effort; the post row still renders
          }
          return { post, project, clip, postCopy, videoUrl };
        })
      );
    },
    []
  );

  const refresh = useCallback(async () => {
    const [allPosts, allProjects, settings] = await Promise.all([
      api.scheduledPosts(),
      api.projects(),
      api.xSettings().catch(() => null)
    ]);
    setProjects(allProjects);
    setXSettings(settings);
    setPosts(await enrich(allPosts, allProjects));
  }, [enrich]);

  useEffect(() => {
    let mounted = true;
    refresh()
      .catch((reason: Error) => mounted && setError(reason.message))
      .finally(() => mounted && setLoading(false));
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 15000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [refresh]);

  const grouped = useMemo(() => {
    const upcoming = posts
      .filter((p) => p.post.status === "pending" || p.post.status === "posting")
      .sort((a, b) => a.post.scheduled_at.localeCompare(b.post.scheduled_at));
    const history = posts
      .filter(
        (p) =>
          p.post.status === "posted" ||
          p.post.status === "failed" ||
          p.post.status === "cancelled"
      )
      .sort((a, b) => b.post.scheduled_at.localeCompare(a.post.scheduled_at));
    return { upcoming, history };
  }, [posts]);

  async function handlePublishNow() {
    setPublishing(true);
    setError(null);
    try {
      await api.publishDueScheduledPosts();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publish failed.");
    } finally {
      setPublishing(false);
    }
  }

  async function handleCancel(post: ScheduledPost) {
    try {
      await api.cancelScheduledPost(post.post_id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not cancel.");
    }
  }

  async function handleDelete(post: ScheduledPost) {
    try {
      await api.deleteScheduledPost(post.post_id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete.");
    }
  }

  return (
    <div className="sched-shell">
      <header className="sched-header">
        <div className="sched-title">
          <PaperPlaneTiltIcon size={22} weight="bold" />
          <div>
            <h2>Scheduled posts</h2>
            <p>
              {xSettings?.configured
                ? "X account connected"
                : "Connect your X account to start posting"}
            </p>
          </div>
        </div>
        <div className="sched-actions">
          <button className="ghost" onClick={() => setShowConnect(true)}>
            <XIcon size={15} weight="bold" />
            {xSettings?.configured ? "X settings" : "Connect X"}
          </button>
          <button
            className="ghost"
            onClick={handlePublishNow}
            disabled={publishing}
            title="Post everything that is due right now"
          >
            <ArrowsClockwiseIcon size={15} weight="bold" />
            {publishing ? "Posting…" : "Post due now"}
          </button>
          <button className="primary" onClick={() => setShowNew(true)}>
            <PlusIcon size={15} weight="bold" />
            Schedule post
          </button>
          <button className="ghost icon-only" onClick={onClose} title="Back">
            <XCircleIcon size={20} />
          </button>
        </div>
      </header>

      {error ? <div className="inline-error">{error}</div> : null}

      {loading ? (
        <div className="sched-empty">Loading scheduled posts…</div>
      ) : posts.length === 0 ? (
        <div className="sched-empty">
          <CalendarBlankIcon size={40} />
          <strong>No posts scheduled yet</strong>
          <p>Schedule a clip to post to X and it will show up here.</p>
          <button className="primary" onClick={() => setShowNew(true)}>
            <PlusIcon size={15} weight="bold" />
            Schedule your first post
          </button>
        </div>
      ) : (
        <div className="sched-body">
          <section>
            <div className="sched-section-heading">
              <ClockIcon size={15} weight="bold" />
              Upcoming ({grouped.upcoming.length})
            </div>
            {grouped.upcoming.length === 0 ? (
              <p className="sched-none">Nothing queued.</p>
            ) : (
              grouped.upcoming.map((item) => (
                <PostCard
                  key={item.post.post_id}
                  item={item}
                  onCancel={handleCancel}
                  onDelete={handleDelete}
                  onChanged={refresh}
                />
              ))
            )}
          </section>
          <section>
            <div className="sched-section-heading">
              <CheckCircleIcon size={15} weight="bold" />
              History ({grouped.history.length})
            </div>
            {grouped.history.length === 0 ? (
              <p className="sched-none">No posts yet.</p>
            ) : (
              grouped.history.map((item) => (
                <PostCard
                  key={item.post.post_id}
                  item={item}
                  onCancel={handleCancel}
                  onDelete={handleDelete}
                  onChanged={refresh}
                />
              ))
            )}
          </section>
        </div>
      )}

      {showConnect ? (
        <ConnectXModal
          onClose={() => setShowConnect(false)}
          onSaved={async () => {
            setShowConnect(false);
            await refresh();
          }}
        />
      ) : null}
      {showNew ? (
        <NewPostModal
          projects={projects}
          onClose={() => setShowNew(false)}
          onSaved={async () => {
            setShowNew(false);
            await refresh();
          }}
        />
      ) : null}
    </div>
  );
}

function PostCard({
  item,
  onCancel,
  onDelete,
  onChanged
}: {
  item: EnrichedPost;
  onCancel: (post: ScheduledPost) => void;
  onDelete: (post: ScheduledPost) => void;
  onChanged: () => Promise<void>;
}) {
  const { post, project, clip, postCopy, videoUrl } = item;
  const meta = statusMeta(post.status);
  const [editing, setEditing] = useState(false);
  const editable = post.status === "pending";

  return (
    <article className={`post-card ${meta.className}`}>
      <div className="post-media">
        {videoUrl ? (
          <video src={videoUrl} controls preload="metadata" />
        ) : (
          <div className="post-media-placeholder">
            <FilmSlateIcon size={28} />
            <span>No clip video</span>
          </div>
        )}
      </div>
      <div className="post-detail">
        <div className="post-top">
          <span className={`post-status ${meta.className}`}>{meta.label}</span>
          <span className="post-time">
            <ClockIcon size={13} />
            {formatDateTime(post.scheduled_at)}
          </span>
        </div>
        <div className="post-source">
          <strong>{project?.name ?? "Unknown project"}</strong>
          {clip ? <span> · {clip.title}</span> : null}
        </div>
        {postCopy?.headline ? (
          <div className="post-headline">{postCopy.headline}</div>
        ) : null}
        <p className="post-text">{post.text}</p>
        {post.error ? (
          <div className="post-error">
            <WarningCircleIcon size={14} /> {post.error}
          </div>
        ) : null}
        {post.result_url ? (
          <a className="post-link" href={post.result_url} target="_blank" rel="noreferrer">
            View on X
          </a>
        ) : null}
        <div className="post-actions">
          {editable ? (
            <button className="ghost small" onClick={() => setEditing(true)}>
              <PencilSimpleIcon size={14} /> Edit
            </button>
          ) : null}
          {post.status === "pending" ? (
            <button className="ghost small" onClick={() => onCancel(post)}>
              Cancel
            </button>
          ) : null}
          {post.status !== "posted" ? (
            <button className="ghost small danger" onClick={() => onDelete(post)}>
              <TrashIcon size={14} /> Delete
            </button>
          ) : null}
        </div>
      </div>
      {editing ? (
        <EditPostModal
          post={post}
          onClose={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false);
            await onChanged();
          }}
        />
      ) : null}
    </article>
  );
}

function ConnectXModal({
  onClose,
  onSaved
}: {
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [accessSecret, setAccessSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.saveXSettings({
        method: "api",
        api_key: apiKey,
        api_secret: apiSecret,
        access_token: accessToken,
        access_secret: accessSecret
      });
      await onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal sched-modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>Connect X (Twitter)</h3>
        <p className="modal-sub">
          From <strong>developer.x.com</strong> → your app →{" "}
          <strong>Keys and tokens</strong>, paste the 4{" "}
          <strong>OAuth 1.0a</strong> keys below (not the Bearer Token).
        </p>
        {error ? <div className="inline-error">{error}</div> : null}
        <label>
          Consumer Key (API Key)
          <input
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="소비자 키"
            autoComplete="off"
            required
          />
        </label>
        <label>
          Consumer Secret (API Secret)
          <input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder="소비자 비밀 키"
            autoComplete="off"
            required
          />
        </label>
        <label>
          Access Token
          <input
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder="액세스 토큰"
            autoComplete="off"
            required
          />
        </label>
        <label>
          Access Token Secret
          <input
            type="password"
            value={accessSecret}
            onChange={(e) => setAccessSecret(e.target.value)}
            placeholder="액세스 토큰 비밀 키"
            autoComplete="off"
            required
          />
        </label>
        <p className="modal-note">
          ⚠️ Your Access Token must have <strong>Read and Write</strong>{" "}
          permission to post. If it says "Read" only, set App permissions to
          Read and Write, then regenerate the token.
        </p>
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={saving}>
            {saving ? "Saving…" : "Save & connect"}
          </button>
        </div>
      </form>
    </div>
  );
}

function NewPostModal({
  projects,
  onClose,
  onSaved
}: {
  projects: Project[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [projectId, setProjectId] = useState(projects[0]?.project_id ?? "");
  const [clips, setClips] = useState<TimestampClip[]>([]);
  const [clipId, setClipId] = useState<string>("");
  const [text, setText] = useState("");
  const [when, setWhen] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let mounted = true;
    Promise.all([
      api.clips(projectId).catch(() => [] as TimestampClip[]),
      api.videoExports(projectId).catch(() => [])
    ]).then(([nextClips, exports]) => {
      if (!mounted) return;
      setClips(nextClips);
      if (nextClips.length && !clipId) setClipId(nextClips[0].clip_id);
      for (const job of exports) {
        const video = job.outputs.find((o) => o.kind === "video");
        if (video) {
          setVideoUrl(video.output_url);
          break;
        }
      }
    });
    return () => {
      mounted = false;
    };
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!when) {
      setError("Pick a date and time.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const scheduledAt = new Date(when).toISOString();
      // The backend resolves the exported video file for this clip itself.
      await api.createScheduledPost({
        project_id: projectId,
        clip_id: clipId || undefined,
        text,
        scheduled_at: scheduledAt
      });
      await onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not schedule.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal sched-modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>Schedule a post</h3>
        {error ? <div className="inline-error">{error}</div> : null}
        <label>
          Project
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {clips.length ? (
          <label>
            Clip
            <select value={clipId} onChange={(e) => setClipId(e.target.value)}>
              {clips.map((c) => (
                <option key={c.clip_id} value={c.clip_id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {videoUrl ? (
          <div className="sched-preview">
            <video src={videoUrl} controls preload="metadata" />
          </div>
        ) : null}
        <label>
          Post text
          <textarea
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What should this post say?"
            required
          />
        </label>
        <label>
          When
          <input
            type="datetime-local"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
            required
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={saving}>
            {saving ? "Scheduling…" : "Schedule"}
          </button>
        </div>
      </form>
    </div>
  );
}

function EditPostModal({
  post,
  onClose,
  onSaved
}: {
  post: ScheduledPost;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [text, setText] = useState(post.text);
  const [when, setWhen] = useState(toLocalInputValue(post.scheduled_at));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.updateScheduledPost(post.post_id, {
        text,
        scheduled_at: new Date(when).toISOString()
      });
      await onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal sched-modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>Edit scheduled post</h3>
        {error ? <div className="inline-error">{error}</div> : null}
        <label>
          Post text
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} required />
        </label>
        <label>
          When
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} required />
        </label>
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </form>
    </div>
  );
}
