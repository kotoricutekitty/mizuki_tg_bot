from __future__ import annotations

from io import BytesIO
import urllib.error
import json

import pytest

from tests.fakes import FakeBookmarkClient, FakeClock
from tests.fakes import FakeMessage, FakeUpdate, FakeUser
from tg_archive_bot.config import BotConfig
from tg_archive_bot import messages
from tg_archive_bot.http_api import start_bookmarks, start_bookmarks_payload
from tg_archive_bot.twitter_bookmarks import (
    BookmarkPost,
    OAuthRefreshResult,
    TwitterBookmarkMonitor,
    XBookmarksAPIError,
    XBookmarksClient,
    XCreditsDepletedError,
    parse_x_bookmarks_http_error,
)


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
        twitter_bookmarks_poll_seconds=30,
        twitter_bookmarks_grace_seconds=10,
        twitter_bookmarks_idle_seconds=2 * 60,
        twitter_bookmarks_max_results=5,
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
async def test_bookmark_monitor_initial_poll_queues_existing_bookmarks(app_factory):
    service, db, bot, downloader = app_factory()
    clock = FakeClock()
    client = FakeBookmarkClient([[BookmarkPost("1", "https://twitter.com/i/status/1")]])
    monitor = monitor_for(service, db, client, clock)

    await monitor.poll_once()

    assert downloader.calls == []
    assert bot.calls == []
    assert db.bookmark_item_count() == 1
    assert db.active_bookmark_items()[0].status == "pending"


@pytest.mark.asyncio
async def test_existing_bookmark_from_activation_submits_after_grace(app_factory, sample_media):
    url = "https://twitter.com/i/status/101"
    service, db, bot, downloader = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url})})
    clock = FakeClock()
    client = FakeBookmarkClient([[BookmarkPost("101", url)], [BookmarkPost("101", url)]])
    monitor = monitor_for(service, db, client, clock)

    await monitor.poll_once()
    clock.advance(10)
    await monitor.poll_once()

    assert downloader.calls == [url]
    assert db.get_submission(1).url == url
    assert db.pending_bookmark_items() == []


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


@pytest.mark.asyncio
async def test_bookmark_monitor_activation_and_idle_shutdown(app_factory):
    service, db, bot, _ = app_factory()
    clock = FakeClock()
    client = FakeBookmarkClient([[], [], []])
    monitor = monitor_for(service, db, client, clock)
    assert not monitor.active

    monitor.activate()
    assert monitor.active
    await monitor.poll_once()
    assert monitor.active

    clock.advance(2 * 60)
    await monitor.poll_once()
    assert not monitor.active
    assert bot.calls[-1]["method"] == "send_message"
    assert bot.calls[-1]["chat_id"] == 1
    assert bot.calls[-1]["text"] == messages.BOOKMARK_WATCH_STOPPED_IDLE


@pytest.mark.asyncio
async def test_duplicate_bookmark_watch_command_only_refreshes_timer(app_factory):
    service, db, bot, _ = app_factory(admin_ids=(1, 2))
    clock = FakeClock()
    client = FakeBookmarkClient([[], []])
    monitor = monitor_for(service, db, client, clock)
    service.bookmark_monitor = monitor
    first_message = FakeMessage()
    second_message = FakeMessage()

    await service.bookmark_watch_command(FakeUpdate(FakeUser(1, "admin"), message=first_message))
    clock.advance(60)
    await service.bookmark_watch_command(FakeUpdate(FakeUser(1, "admin"), message=second_message))

    assert monitor.active
    assert client.calls == 1
    assert first_message.replies[0]["text"] == messages.BOOKMARK_WATCH_STARTED
    assert second_message.replies[0]["text"] == messages.BOOKMARK_WATCH_RESTARTED
    assert bot.calls == [{"method": "send_message", "chat_id": 2, "text": messages.BOOKMARK_WATCH_STARTED}]

    clock.advance(119)
    await monitor.poll_once()
    assert monitor.active
    clock.advance(1)
    await monitor.poll_once()
    assert not monitor.active


@pytest.mark.asyncio
async def test_admin_command_starts_bookmark_watch(app_factory):
    service, db, bot, _ = app_factory(admin_ids=(1, 2))
    clock = FakeClock()
    client = FakeBookmarkClient([[]])
    monitor = monitor_for(service, db, client, clock)
    service.bookmark_monitor = monitor
    message = FakeMessage()

    await service.bookmark_watch_command(FakeUpdate(FakeUser(1, "admin"), message=message))

    assert monitor.active
    assert client.calls == 1
    assert message.replies[0]["text"] == messages.BOOKMARK_WATCH_STARTED
    assert bot.calls == [{"method": "send_message", "chat_id": 2, "text": messages.BOOKMARK_WATCH_STARTED}]


@pytest.mark.asyncio
async def test_bookmark_watch_command_requires_admin_and_config(app_factory):
    service, *_ = app_factory()
    user_message = FakeMessage()
    await service.bookmark_watch_command(FakeUpdate(FakeUser(2, "normal"), message=user_message))
    assert user_message.replies[0]["text"] == messages.BOOKMARK_WATCH_FORBIDDEN

    admin_message = FakeMessage()
    await service.bookmark_watch_command(FakeUpdate(FakeUser(1, "admin"), message=admin_message))
    assert admin_message.replies[0]["text"] == messages.BOOKMARK_WATCH_UNAVAILABLE


def test_http_bookmark_start_uses_post_token(app_factory):
    service, db, _, _ = app_factory()
    monitor = monitor_for(service, db, FakeBookmarkClient([[]]), FakeClock())
    service.bookmark_monitor = monitor

    unauthorized = start_bookmarks_payload(service, "wrong")
    assert unauthorized.status == 401

    started = start_bookmarks_payload(service, "api-token")
    assert started.status == 200
    assert started.body["status"] == "started"
    assert monitor.active

    restarted = start_bookmarks_payload(service, "api-token")
    assert restarted.status == 200
    assert restarted.body["status"] == "restarted"
    assert monitor.active


@pytest.mark.asyncio
async def test_http_bookmark_start_notifies_admins(app_factory):
    service, db, bot, _ = app_factory(admin_ids=(1, 2))
    client = FakeBookmarkClient([[]])
    monitor = monitor_for(service, db, client, FakeClock())
    service.bookmark_monitor = monitor

    result = await start_bookmarks(service, "api-token")
    restarted = await start_bookmarks(service, "api-token")

    assert result.status == 200
    assert restarted.status == 200
    assert restarted.body["status"] == "restarted"
    assert monitor.active
    assert client.calls == 1
    assert bot.calls == [
        {"method": "send_message", "chat_id": 1, "text": messages.BOOKMARK_WATCH_STARTED},
        {"method": "send_message", "chat_id": 2, "text": messages.BOOKMARK_WATCH_STARTED},
    ]


@pytest.mark.asyncio
async def test_bookmark_monitor_stops_when_x_credits_are_depleted(app_factory):
    service, db, bot, downloader = app_factory()
    clock = FakeClock()

    class CreditDepletedClient:
        async def fetch_bookmarks(self):
            raise XCreditsDepletedError(
                status=402,
                title="CreditsDepleted",
                detail="Your enrolled account does not have any credits to fulfill this request.",
                problem_type="https://api.twitter.com/2/problems/credits",
            )

    monitor = monitor_for(service, db, CreditDepletedClient(), clock)
    monitor.activate()

    await monitor.poll_once()

    assert not monitor.active
    assert downloader.calls == []
    assert bot.calls[0] == {"method": "send_message", "chat_id": 1, "text": messages.BOOKMARK_WATCH_STOPPED_CREDITS}
    assert bot.calls[1]["method"] == "send_message"
    assert bot.calls[1]["chat_id"] == 1
    assert messages.ADMIN_ERROR_PREFIX in bot.calls[1]["text"]
    assert db.bookmark_item_count() == 0
    assert db.get_bookmark_monitor_state("last_error_code") == "credits_depleted"
    assert "CreditsDepleted" in db.get_bookmark_monitor_state("last_error")
    assert db.get_bookmark_monitor_state("credits_depleted_at") == clock.now().isoformat()


@pytest.mark.asyncio
async def test_multiple_idle_monitors_send_one_stop_notice(app_factory):
    service, db, bot, _ = app_factory()
    clock = FakeClock()
    twitter = monitor_for(service, db, FakeBookmarkClient([[], []]), clock)
    pixiv = TwitterBookmarkMonitor(
        config=with_bookmark_config(service.config),
        db=db,
        archive_bot=service,
        client=FakeBookmarkClient([[], []]),
        clock=clock,
        provider="pixiv",
        label="Pixiv",
        configured=lambda: True,
    )
    twitter.activate()
    pixiv.activate()
    await twitter.poll_once()
    await pixiv.poll_once()
    clock.advance(2 * 60)

    await twitter.poll_once()
    await pixiv.poll_once()

    stop_calls = [call for call in bot.calls if call.get("text") == messages.BOOKMARK_WATCH_STOPPED_IDLE]
    assert len(stop_calls) == 1


def test_x_bookmarks_client_maps_credits_depleted_http_error():
    payload = (
        b'{"title":"CreditsDepleted","detail":"Your enrolled account does not have any credits",'
        b'"type":"https://api.twitter.com/2/problems/credits"}'
    )
    error = urllib.error.HTTPError(
        url="https://api.x.com/2/users/123/bookmarks",
        code=402,
        msg="Payment Required",
        hdrs={},
        fp=BytesIO(payload),
    )

    parsed = parse_x_bookmarks_http_error(error)

    assert isinstance(parsed, XCreditsDepletedError)
    assert parsed.status == 402
    assert parsed.title == "CreditsDepleted"


def test_bookmark_config_defaults_use_requested_poll_window(monkeypatch):
    monkeypatch.delenv("TWITTER_BOOKMARKS_POLL_SECONDS", raising=False)
    monkeypatch.delenv("TWITTER_BOOKMARKS_IDLE_SECONDS", raising=False)
    monkeypatch.delenv("TWITTER_BOOKMARKS_MAX_RESULTS", raising=False)

    config = BotConfig.from_env()

    assert config.twitter_bookmarks_poll_seconds == 30
    assert config.twitter_bookmarks_idle_seconds == 2 * 60
    assert config.twitter_bookmarks_max_results == 5
    assert config.twitter_bookmarks_max_pages == 4


def test_x_bookmarks_client_refreshes_access_token_after_401(monkeypatch):
    calls = []

    class Refresher:
        def refresh_access_token(self):
            return OAuthRefreshResult(access_token="fresh-token", refresh_token="fresh-refresh")

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout):
        calls.append(request.headers["Authorization"])
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=BytesIO(b'{"title":"Unauthorized","detail":"Unauthorized"}'),
            )
        return Response(json.dumps({"data": [{"id": "42"}]}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = XBookmarksClient(
        api_base="https://api.x.com",
        user_id="123",
        access_token="expired-token",
        token_refresher=Refresher(),
    )

    posts = client._fetch_bookmarks_sync()

    assert posts == [BookmarkPost("42", "https://twitter.com/i/status/42")]
    assert calls == ["Bearer expired-token", "Bearer fresh-token"]


def test_x_bookmarks_client_adaptive_pagination_stops_at_known_id(monkeypatch):
    requested_urls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if len(requested_urls) == 1:
            payload = {"data": [{"id": "100"}, {"id": "99"}], "meta": {"next_token": "next-1"}}
        else:
            payload = {"data": [{"id": "98"}, {"id": "97"}]}
        return Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = XBookmarksClient(api_base="https://api.x.com", user_id="123", access_token="token", max_results=5)

    posts = client._fetch_bookmarks_until_sync({"98"}, max_pages=4)

    assert [post.tweet_id for post in posts] == ["100", "99", "98", "97"]
    assert "max_results=5" in requested_urls[0]
    assert "pagination_token=next-1" in requested_urls[1]
    assert "max_results=10" in requested_urls[1]


@pytest.mark.asyncio
async def test_bookmark_monitor_uses_adaptive_pagination_with_known_ids(app_factory):
    service, db, _, _ = app_factory()
    clock = FakeClock()
    db.mark_bookmark_seen("98", "https://twitter.com/i/status/98", clock.now(), initial_status="baseline")

    class AdaptiveClient:
        def __init__(self):
            self.stop_ids = None
            self.max_pages = None

        async def fetch_bookmarks_until(self, stop_ids, max_pages):
            self.stop_ids = set(stop_ids)
            self.max_pages = max_pages
            return [
                BookmarkPost("100", "https://twitter.com/i/status/100"),
                BookmarkPost("98", "https://twitter.com/i/status/98"),
            ]

        async def fetch_bookmarks(self):
            raise AssertionError("adaptive client should be used")

    client = AdaptiveClient()
    monitor = monitor_for(service, db, client, clock)
    monitor.activate()
    db.set_bookmark_monitor_state("bootstrapped", "1")

    await monitor.poll_once()

    assert client.stop_ids == {"98"}
    assert client.max_pages == 4


def test_x_bookmarks_client_raises_401_without_refresh(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(b'{"title":"Unauthorized","detail":"Unauthorized"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = XBookmarksClient(api_base="https://api.x.com", user_id="123", access_token="expired-token")

    with pytest.raises(XBookmarksAPIError) as exc:
        client._fetch_bookmarks_sync()

    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_bookmark_poll_error_notifies_admins_with_throttle(app_factory):
    service, db, bot, _ = app_factory()
    clock = FakeClock()

    class BrokenClient:
        async def fetch_bookmarks(self):
            raise RuntimeError("boom")

    monitor = monitor_for(service, db, BrokenClient(), clock)
    monitor.activate()

    await monitor.poll_once()
    await monitor.poll_once()

    error_calls = [call for call in bot.calls if call["method"] == "send_message" and messages.ADMIN_ERROR_PREFIX in call["text"]]
    assert len(error_calls) == 1
    assert "Twitter bookmark monitor poll failed" in error_calls[0]["text"]
    assert "RuntimeError: boom" in error_calls[0]["text"]
    assert db.get_bookmark_monitor_state("last_error_code") == "poll_failed"

    service.clock.advance(301)
    await monitor.poll_once()
    error_calls = [call for call in bot.calls if call["method"] == "send_message" and messages.ADMIN_ERROR_PREFIX in call["text"]]
    assert len(error_calls) == 2


@pytest.mark.asyncio
async def test_bookmark_submit_timeout_stays_pending_for_retry(app_factory):
    service, db, bot, _ = app_factory()
    clock = FakeClock()
    url = "https://www.pixiv.net/artworks/129104622"
    client = FakeBookmarkClient([[BookmarkPost("129104622", url)]])
    monitor = TwitterBookmarkMonitor(
        config=with_bookmark_config(service.config),
        db=db,
        archive_bot=service,
        client=client,
        clock=clock,
        provider="pixiv",
        label="Pixiv",
        configured=lambda: True,
    )
    monitor.activate()

    async def timeout_submit(url, username="bookmark_monitor"):
        raise TimeoutError("Timed out")

    service.submit_url_as_admin = timeout_submit
    await monitor.poll_once()
    clock.advance(10)
    await monitor.poll_once()

    item = db.pending_bookmark_items(provider="pixiv")[0]
    assert item.tweet_id == "129104622"
    assert item.error == "Timed out"
    assert db.bookmark_item_count(provider="pixiv") == 1
    error_calls = [call for call in bot.calls if call["method"] == "send_message" and messages.ADMIN_ERROR_PREFIX in call["text"]]
    assert len(error_calls) == 1
