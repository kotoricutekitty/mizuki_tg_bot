from __future__ import annotations

import pytest

from tg_archive_bot.config import BotConfig
from tg_archive_bot.db import Database
from tg_archive_bot.service import ArchiveBot

from .fakes import FakeBot, FakeClock, FakeDownloader, make_image


@pytest.fixture
def app_factory(tmp_path):
    def build(mapping=None, admin_ids=(1,), channel="@archive"):
        db = Database(tmp_path / "db.sqlite")
        db.init()
        bot = FakeBot()
        downloader = FakeDownloader(mapping or {})
        config = BotConfig(
            bot_token="test-token",
            admin_ids=tuple(admin_ids),
            publish_channel_id=channel,
            post_token="api-token",
            data_dir=tmp_path,
            media_dir=tmp_path / "media",
            database_path=tmp_path / "db.sqlite",
            temp_dir=tmp_path / "tmp",
        )
        service = ArchiveBot(config, db, downloader, bot, FakeClock())
        return service, db, bot, downloader

    return build


@pytest.fixture
def sample_media(tmp_path):
    one = make_image(tmp_path / "media" / "one.jpg")
    two = make_image(tmp_path / "media" / "two.png", "PNG")
    video = tmp_path / "media" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"0" * 1024)
    return {"jpg": one, "png": two, "mp4": str(video)}
