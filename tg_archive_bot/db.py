from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .url_utils import normalize_url, provider_for_url


@dataclass
class Submission:
    id: int
    user_id: int | None
    username: str | None
    url: str
    status: str
    media_paths: list[str]
    created_at: str | None = None
    reviewed_at: str | None = None
    reviewer_id: int | None = None
    message_id: int | None = None
    author_name: str | None = None
    title: str | None = None
    text: str | None = None
    canonical_url: str | None = None
    normalized_url: str | None = None
    provider: str | None = None
    metadata_json: str | None = None
    updated_at: str | None = None


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_legacy(conn)
            conn.executescript(INDEXES)

    def _migrate_legacy(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(submissions)")}
        for name, ddl in {
            "normalized_url": "ALTER TABLE submissions ADD COLUMN normalized_url TEXT",
            "provider": "ALTER TABLE submissions ADD COLUMN provider TEXT",
            "metadata_json": "ALTER TABLE submissions ADD COLUMN metadata_json TEXT",
            "updated_at": "ALTER TABLE submissions ADD COLUMN updated_at TIMESTAMP",
        }.items():
            if name not in existing:
                conn.execute(ddl)

        rows = conn.execute(
            "SELECT id, url, author_name, title, text, canonical_url, normalized_url, provider, metadata_json FROM submissions"
        ).fetchall()
        for row in rows:
            normalized = row["normalized_url"] or normalize_url(row["url"] or "")
            provider = row["provider"] or provider_for_url(row["url"] or "")
            metadata = row["metadata_json"]
            if not metadata:
                metadata = json.dumps(
                    {
                        "author_name": row["author_name"] or "",
                        "title": row["title"] or "",
                        "text": row["text"] or "",
                        "canonical_url": row["canonical_url"] or row["url"] or "",
                    },
                    ensure_ascii=False,
                )
            conn.execute(
                "UPDATE submissions SET normalized_url = ?, provider = ?, metadata_json = COALESCE(metadata_json, ?), updated_at = COALESCE(updated_at, created_at) WHERE id = ?",
                (normalized, provider, metadata, row["id"]),
            )

    def get_config_rows(self) -> list[tuple[str, str]]:
        with self.connect() as conn:
            return [(row["key"], row["value"]) for row in conn.execute("SELECT key, value FROM config")]

    def set_config(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))

    def get_submission(self, submission_id: int) -> Submission | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        return row_to_submission(row) if row else None

    def find_by_url(self, normalized_url: str) -> Submission | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM submissions WHERE normalized_url = ? OR url = ? LIMIT 1",
                (normalized_url, normalized_url),
            ).fetchone()
        return row_to_submission(row) if row else None

    def find_by_message_id(self, message_id: int) -> Submission | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM submissions WHERE message_id = ? LIMIT 1", (message_id,)).fetchone()
        return row_to_submission(row) if row else None

    def create_submission(
        self,
        *,
        user_id: int,
        username: str,
        url: str,
        status: str,
        media_paths: list[str],
        metadata: dict[str, Any],
        now: datetime,
    ) -> int:
        normalized = normalize_url(url)
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO submissions (
                  user_id, username, url, status, media_paths, created_at,
                  author_name, title, text, canonical_url, normalized_url,
                  provider, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    normalized,
                    status,
                    json.dumps(media_paths, ensure_ascii=False),
                    now,
                    metadata.get("author_name", ""),
                    metadata.get("title", ""),
                    metadata.get("text", ""),
                    metadata.get("canonical_url", normalized),
                    normalized,
                    provider_for_url(normalized),
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                ),
            )
            return int(cur.lastrowid)

    def update_status(self, submission_id: int, status: str, reviewer_id: int | None, now: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE submissions SET status = ?, reviewed_at = ?, reviewer_id = ?, updated_at = ? WHERE id = ?",
                (status, now, reviewer_id, now, submission_id),
            )

    def update_message_id(self, submission_id: int, message_id: int, now: datetime | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE submissions SET message_id = ?, updated_at = COALESCE(?, updated_at) WHERE id = ?",
                (message_id, now, submission_id),
            )

    def pending_submissions(self) -> list[Submission]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM submissions WHERE status = 'pending'").fetchall()
        return [row_to_submission(row) for row in rows]

    def count_pixiv_downloads(self, hours: int) -> tuple[int, str | None, str | None]:
        with self.connect() as conn:
            conn.execute(f"DELETE FROM pixiv_downloads WHERE request_time < datetime('now', '-{int(hours)} hours')")
            row = conn.execute("SELECT COUNT(*) count, MIN(request_time) first_time, MAX(request_time) last_time FROM pixiv_downloads").fetchone()
        return int(row["count"]), row["first_time"], row["last_time"]

    def record_pixiv_download(self, url: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO pixiv_downloads (request_time, url) VALUES (datetime('now'), ?)", (url,))


def row_to_submission(row: sqlite3.Row) -> Submission:
    data = dict(row)
    try:
        media_paths = json.loads(data.get("media_paths") or "[]")
    except json.JSONDecodeError:
        media_paths = []
    return Submission(
        id=data["id"],
        user_id=data.get("user_id"),
        username=data.get("username"),
        url=data.get("url") or "",
        status=data.get("status") or "",
        media_paths=media_paths,
        created_at=str(data.get("created_at")) if data.get("created_at") is not None else None,
        reviewed_at=str(data.get("reviewed_at")) if data.get("reviewed_at") is not None else None,
        reviewer_id=data.get("reviewer_id"),
        message_id=data.get("message_id"),
        author_name=data.get("author_name"),
        title=data.get("title"),
        text=data.get("text"),
        canonical_url=data.get("canonical_url"),
        normalized_url=data.get("normalized_url"),
        provider=data.get("provider"),
        metadata_json=data.get("metadata_json"),
        updated_at=str(data.get("updated_at")) if data.get("updated_at") is not None else None,
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  username TEXT,
  url TEXT,
  status TEXT,
  media_paths TEXT,
  created_at TIMESTAMP,
  reviewed_at TIMESTAMP,
  reviewer_id INTEGER,
  message_id INTEGER,
  author_name TEXT,
  title TEXT,
  text TEXT,
  canonical_url TEXT,
  normalized_url TEXT,
  provider TEXT,
  metadata_json TEXT,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS pixiv_downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_time TIMESTAMP,
  url TEXT
);

"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_message_id ON submissions(message_id);
CREATE INDEX IF NOT EXISTS idx_submissions_normalized_url ON submissions(normalized_url);
CREATE INDEX IF NOT EXISTS idx_pixiv_downloads_request_time ON pixiv_downloads(request_time);
"""
