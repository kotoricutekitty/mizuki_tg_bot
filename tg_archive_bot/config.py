from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    admin_ids: tuple[int, ...]
    publish_channel_id: str
    post_token: str | None
    data_dir: Path
    media_dir: Path
    database_path: Path
    temp_dir: Path
    http_api_enabled: bool = False
    http_api_host: str = "0.0.0.0"
    http_api_port: int = 8080
    pixiv_limit_count: int = 100
    pixiv_limit_hours: int = 5
    twitter_bookmarks_enabled: bool = False
    twitter_bookmarks_user_id: str = ""
    twitter_bookmarks_access_token: str = ""
    twitter_bookmarks_poll_seconds: float = 5.0
    twitter_bookmarks_grace_seconds: float = 10.0
    twitter_bookmarks_api_base: str = "https://api.x.com"

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "BotConfig":
        root = base_dir or Path.cwd()
        data_dir = Path(os.getenv("DATA_DIR", root / "data"))
        database_path = Path(os.getenv("DATABASE_PATH", data_dir / "db.sqlite"))
        media_dir = Path(os.getenv("MEDIA_DIR", data_dir / "media"))
        temp_dir = Path(os.getenv("TEMP_DIR", data_dir / "tmp"))
        return cls(
            bot_token=os.getenv("BOT_TOKEN", ""),
            admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
            publish_channel_id=os.getenv("PUBLISH_CHANNEL_ID", os.getenv("CHANNEL_ID", "")),
            post_token=os.getenv("POST_TOKEN") or None,
            data_dir=data_dir,
            media_dir=media_dir,
            database_path=database_path,
            temp_dir=temp_dir,
            http_api_enabled=parse_bool(os.getenv("HTTP_API_ENABLED", "false")),
            http_api_host=os.getenv("HTTP_API_HOST", "0.0.0.0"),
            http_api_port=int(os.getenv("HTTP_API_PORT", "8080")),
            twitter_bookmarks_enabled=parse_bool(os.getenv("TWITTER_BOOKMARKS_ENABLED", "false")),
            twitter_bookmarks_user_id=os.getenv("TWITTER_BOOKMARKS_USER_ID", ""),
            twitter_bookmarks_access_token=os.getenv("TWITTER_BOOKMARKS_ACCESS_TOKEN", ""),
            twitter_bookmarks_poll_seconds=float(os.getenv("TWITTER_BOOKMARKS_POLL_SECONDS", "5")),
            twitter_bookmarks_grace_seconds=float(os.getenv("TWITTER_BOOKMARKS_GRACE_SECONDS", "10")),
            twitter_bookmarks_api_base=os.getenv("TWITTER_BOOKMARKS_API_BASE", "https://api.x.com"),
        )

    def validate_runtime(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.admin_ids:
            missing.append("ADMIN_IDS")
        if not self.publish_channel_id:
            missing.append("PUBLISH_CHANNEL_ID")
        if missing:
            raise ValueError("Missing required config: " + ", ".join(missing))
        if self.twitter_bookmarks_enabled:
            bookmark_missing = []
            if not self.twitter_bookmarks_user_id:
                bookmark_missing.append("TWITTER_BOOKMARKS_USER_ID")
            if not self.twitter_bookmarks_access_token:
                bookmark_missing.append("TWITTER_BOOKMARKS_ACCESS_TOKEN")
            if bookmark_missing:
                raise ValueError("Missing required Twitter bookmarks config: " + ", ".join(bookmark_missing))


def parse_admin_ids(value: str) -> tuple[int, ...]:
    ids: list[int] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return tuple(ids)


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
