from __future__ import annotations

import pytest

from tests.fakes import FakeBookmarkClient, FakeClock
from tg_archive_bot.config import BotConfig
from tg_archive_bot.twitter_bookmarks import BookmarkPost, TwitterBookmarkMonitor


def with_bookmark_config(config: BotConfig) -> BotConfig:
    return BotConfig(
        bot_token=config.bot_token,
        admin_ids=config.admin_ids,
        publish_channel_id=config.publish_channel_id,
        post_token=config.post_token,
        data_dir=config.data_dir,
        media_dir=config.media_dir,
        database_path=config.database_path,
        temp_dir=config.temp_dir,
        http_api_enabled=config.http_api_enabled,
        http_api_host=config.http_api_host,
        http_api_port=config.http_api_port,
        pixiv_limit_count=config.pixiv_limit_count,
        pixiv_limit_hours=config.pixiv_limit_hours,
        twitter_bookmarks_enabled=True,
        twitter_bookmarks_user_id="123",
        twitter_bookmarks_access_token="token",
        twitter_bookmarks_poll_seconds=5,
        twitter_bookmarks_grace_seconds=10,
    )


def monitor_for(service, db, client, clock):
    return TwitterBookmarkMonitor(
        config=with_bookmark_config(service.config),
        db=db,
        archive_bot=service,
        client=client,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_bookmark_monitor_initial_poll_baselines_without_submission(app_factory):
    service, db, bot, downloader = app_factory()
    clock = FakeClock()
    client = FakeBookmarkClient([[BookmarkPost("1", "https://twitter.com/i/status/1")]])
    monitor = monitor_for(service, db, client, clock)

    await monitor.poll_once()

    assert downloader.calls == []
    assert bot.calls == []
    assert db.bookmark_item_count() == 1
    assert db.active_bookmark_items()[0].status == "baseline"


@pytest.mark.asyncio
async def test_bookmark_removed_within_grace_is_not_submitted(app_factory):
    service, db, bot, downloader = app_factory()
    clock = FakeClock()
    client = FakeBookmarkClient([
        [],
        [BookmarkPost("2", "https://twitter.com/i/status/2")],
        [],
    ])
    monitor = monitor_for(service, db, client, clock)

    await monitor.poll_once()
    await monitor.poll_once()
    clock.advance(5)
    await monitor.poll_once()

    assert downloader.calls == []
    assert bot.calls == []
    assert db.active_bookmark_items() == []


@pytest.mark.asyncio
async def test_bookmark_stable_after_grace_submits_as_admin(app_factory, sample_media):
    url = "https://twitter.com/i/status/3"
    service, db, bot, downloader = app_factory({url: ([sample_media["jpg"]], {"author_name": "artist", "text": "hello", "canonical_url": url})})
    clock = FakeClock()
    client = FakeBookmarkClient([
        [],
        [BookmarkPost("3", url)],
        [BookmarkPost("3", url)],
    ])
    monitor = monitor_for(service, db, client, clock)

    await monitor.poll_once()
    await monitor.poll_once()
    clock.advance(10)
    await monitor.poll_once()

    assert downloader.calls == [url]
    assert db.get_submission(1).username == "bookmark_monitor"
    assert db.get_submission(1).status == "approved"
    assert any(call["method"] == "send_photo" and call["chat_id"] == "@archive" for call in bot.calls)
    item = db.active_bookmark_items()
    assert item == []


@pytest.mark.asyncio
async def test_bookmark_duplicate_is_marked_without_resubmitting(app_factory, sample_media):
    url = "https://twitter.com/i/status/4"
    service, db, bot, downloader = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url})})
    db.create_submission(
        user_id=1,
        username="admin",
        url=url,
        status="approved",
        media_paths=[sample_media["jpg"]],
        metadata={},
        now=service.clock.now(),
    )
    clock = FakeClock()
    client = FakeBookmarkClient([
        [],
        [BookmarkPost("4", url)],
        [BookmarkPost("4", url)],
    ])
    monitor = monitor_for(service, db, client, clock)

    await monitor.poll_once()
    await monitor.poll_once()
    clock.advance(10)
    await monitor.poll_once()

    assert downloader.calls == []
    assert bot.calls == []
    assert db.bookmark_item_count() == 1
    assert db.pending_bookmark_items() == []
