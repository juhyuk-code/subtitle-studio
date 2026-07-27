import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from .models import GlossaryEntry, Job, Project, Segment, TimestampClip


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_root = self.root / "projects"
        self.media_root.mkdir(exist_ok=True)
        self.db_path = self.root / "subtitle_studio.sqlite3"
        self.lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS records (
                  kind TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  record_id TEXT NOT NULL,
                  sort_key INTEGER NOT NULL DEFAULT 0,
                  payload TEXT NOT NULL,
                  PRIMARY KEY (kind, record_id)
                );
                CREATE INDEX IF NOT EXISTS records_project
                  ON records(kind, project_id, sort_key);
                """
            )

    def put(self, kind: str, project_id: str, record_id: str, payload: Any, sort_key: int = 0) -> None:
        data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        with self.lock, self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO records
                   (kind, project_id, record_id, sort_key, payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (kind, project_id, record_id, sort_key, json.dumps(data, ensure_ascii=False)),
            )

    def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE kind = ? AND record_id = ?",
                (kind, record_id),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, kind: str, project_id: str = "*") -> list[dict[str, Any]]:
        with self._connect() as db:
            if project_id == "*":
                rows = db.execute(
                    "SELECT payload FROM records WHERE kind = ? ORDER BY sort_key, record_id",
                    (kind,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT payload FROM records
                       WHERE kind = ? AND project_id = ?
                       ORDER BY sort_key, record_id""",
                    (kind, project_id),
                ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def delete_project(self, project_id: str) -> None:
        with self.lock, self._connect() as db:
            db.execute("DELETE FROM records WHERE project_id = ?", (project_id,))

    def delete_kind(self, kind: str, project_id: str) -> None:
        with self.lock, self._connect() as db:
            db.execute(
                "DELETE FROM records WHERE kind = ? AND project_id = ?",
                (kind, project_id),
            )

    def save_project(self, project: Project) -> None:
        self.put("project", project.project_id, project.project_id, project)

    def save_segment(self, project_id: str, segment: Segment) -> None:
        self.put("segment", project_id, segment.segment_id, segment, segment.start_ms)

    def save_job(self, job: Job) -> None:
        self.put("job", job.project_id, job.job_id, job)

    def save_glossary(self, project_id: str, entry: GlossaryEntry) -> None:
        self.put("glossary", project_id, entry.entry_id, entry)

    def save_clip(self, project_id: str, clip: TimestampClip) -> None:
        self.put("clip", project_id, clip.clip_id, clip, clip.start_ms)

    def save_setting(self, key: str, value: str) -> None:
        self.put("setting", "__app__", key, {"value": value})

    def get_setting(self, key: str) -> str | None:
        record = self.get("setting", key)
        return record.get("value") if record else None
