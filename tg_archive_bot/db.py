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


@dataclass
class BookmarkItem:
    tweet_id: str
    url: str
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    submitted_at: datetime | None = None
    removed_at: datetime | None = None
    submission_id: int | None = None
    error: str | None = None


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

    def find_by_url(self, url: str) -> Submission | None:
        candidates = sorted({url, normalize_url(url)})
        placeholders = ", ".join("?" for _ in candidates)
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT *
                FROM submissions
                WHERE normalized_url IN ({placeholders})
                   OR url IN ({placeholders})
                   OR canonical_url IN ({placeholders})
                ORDER BY id
                LIMIT 1
                """,
                (*candidates, *candidates, *candidates),
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

    def bookmark_item_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) count FROM twitter_bookmark_items").fetchone()
        return int(row["count"])

    def get_bookmark_monitor_state(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM twitter_bookmark_monitor_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_bookmark_monitor_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO twitter_bookmark_monitor_state (key, value) VALUES (?, ?)",
                (key, value),
            )

    def mark_bookmark_seen(self, tweet_id: str, url: str, now: datetime, initial_status: str = "pending") -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM twitter_bookmark_items WHERE tweet_id = ?", (tweet_id,)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO twitter_bookmark_items (
                      tweet_id, url, status, first_seen_at, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tweet_id, url, initial_status, now, now, now),
                )
                return
            if row["status"] == "removed":
                conn.execute(
                    """
                    UPDATE twitter_bookmark_items
                    SET url = ?, status = 'pending', first_seen_at = ?, last_seen_at = ?,
                        removed_at = NULL, error = NULL, updated_at = ?
                    WHERE tweet_id = ?
                    """,
                    (url, now, now, now, tweet_id),
                )
                return
            conn.execute(
                "UPDATE twitter_bookmark_items SET url = ?, last_seen_at = ?, updated_at = ? WHERE tweet_id = ?",
                (url, now, now, tweet_id),
            )

    def active_bookmark_items(self) -> list[BookmarkItem]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM twitter_bookmark_items WHERE status IN ('baseline', 'pending')"
            ).fetchall()
        return [row_to_bookmark_item(row) for row in rows]

    def pending_bookmark_items(self) -> list[BookmarkItem]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM twitter_bookmark_items WHERE status = 'pending'").fetchall()
        return [row_to_bookmark_item(row) for row in rows]

    def mark_bookmark_removed(self, tweet_id: str, now: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE twitter_bookmark_items
                SET status = 'removed', removed_at = ?, updated_at = ?
                WHERE tweet_id = ? AND status IN ('baseline', 'pending')
                """,
                (now, now, tweet_id),
            )

    def mark_bookmark_submitted(self, tweet_id: str, submission_id: int | None, now: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE twitter_bookmark_items
                SET status = 'submitted', submitted_at = ?, submission_id = ?, updated_at = ?, error = NULL
                WHERE tweet_id = ?
                """,
                (now, submission_id, now, tweet_id),
            )

    def mark_bookmark_duplicate(self, tweet_id: str, submission_id: int | None, now: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE twitter_bookmark_items
                SET status = 'duplicate', submitted_at = ?, submission_id = ?, updated_at = ?, error = NULL
                WHERE tweet_id = ?
                """,
                (now, submission_id, now, tweet_id),
            )

    def mark_bookmark_failed(self, tweet_id: str, error: str, now: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE twitter_bookmark_items SET status = 'failed', error = ?, updated_at = ? WHERE tweet_id = ?",
                (error[:1000], now, tweet_id),
            )


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


def row_to_bookmark_item(row: sqlite3.Row) -> BookmarkItem:
    data = dict(row)
    return BookmarkItem(
        tweet_id=data["tweet_id"],
        url=data["url"],
        status=data["status"],
        first_seen_at=parse_db_datetime(data["first_seen_at"]),
        last_seen_at=parse_db_datetime(data["last_seen_at"]),
        submitted_at=parse_optional_db_datetime(data.get("submitted_at")),
        removed_at=parse_optional_db_datetime(data.get("removed_at")),
        submission_id=data.get("submission_id"),
        error=data.get("error"),
    )


def parse_optional_db_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return parse_db_datetime(value)


def parse_db_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


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

CREATE TABLE IF NOT EXISTS twitter_bookmark_items (
  tweet_id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  status TEXT NOT NULL,
  first_seen_at TIMESTAMP NOT NULL,
  last_seen_at TIMESTAMP NOT NULL,
  submitted_at TIMESTAMP,
  removed_at TIMESTAMP,
  submission_id INTEGER,
  error TEXT,
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS twitter_bookmark_monitor_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_message_id ON submissions(message_id);
CREATE INDEX IF NOT EXISTS idx_submissions_normalized_url ON submissions(normalized_url);
CREATE INDEX IF NOT EXISTS idx_submissions_canonical_url ON submissions(canonical_url);
CREATE INDEX IF NOT EXISTS idx_pixiv_downloads_request_time ON pixiv_downloads(request_time);
CREATE INDEX IF NOT EXISTS idx_twitter_bookmark_items_status ON twitter_bookmark_items(status);
CREATE INDEX IF NOT EXISTS idx_twitter_bookmark_items_first_seen ON twitter_bookmark_items(first_seen_at);
"""
