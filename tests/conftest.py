from __future__ import annotations

import pytest

from tg_archive_bot.config import BotConfig
from tg_archive_bot.db import Database
from tg_archive_bot.service import ArchiveBot

from .fakes import FakeBot, FakeClock, FakeDownloader, make_image


@pytest.fixture
def app_factory(tmp_path):
    def build(mapping=None, admin_ids=(1,), channel="@archive", r18_channel="", safety_detector=None, **config_overrides):
        db = Database(tmp_path / "db.sqlite")
        db.init()
        bot = FakeBot()
        downloader = FakeDownloader(mapping or {})
        config_values = dict(
            bot_token="test-token",
            admin_ids=tuple(admin_ids),
            publish_channel_id=channel,
            post_token="api-token",
            data_dir=tmp_path,
            media_dir=tmp_path / "media",
            database_path=tmp_path / "db.sqlite",
            temp_dir=tmp_path / "tmp",
            r18_routing_enabled=bool(r18_channel),
            r18_channel_id=r18_channel,
            nsfw_detection_enabled=safety_detector is not None,
        )
        config_values.update(config_overrides)
        config = BotConfig(**config_values)
        service = ArchiveBot(config, db, downloader, bot, FakeClock(), safety_detector=safety_detector)
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
