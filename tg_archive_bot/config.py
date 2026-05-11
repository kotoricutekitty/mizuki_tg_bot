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
