from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import FakeBookmarkClient, FakeClock
from tg_archive_bot.twitter_bookmarks import BookmarkPost, TwitterBookmarkMonitor
from tg_archive_bot.web_bookmarks import BookmarkMonitorGroup, PixivBookmarksClient, PoipikuBookmarksClient
import tg_archive_bot.web_bookmarks as web_bookmarks


@pytest.mark.asyncio
async def test_pixiv_bookmark_client_reads_public_and_private_pages(monkeypatch, tmp_path: Path):
    cookies = tmp_path / "pixiv.txt"
    cookies.write_text("www.pixiv.net\tTRUE\t/\tTRUE\t0\tPHPSESSID\tcookie\n", encoding="utf-8")

    def fake_json(url: str, cookies_path: Path, *, referer: str):
        assert cookies_path == cookies
        if "rest=hide" in url:
            return {"error": False, "body": {"works": [{"id": "22"}]}}
        return {"error": False, "body": {"works": [{"id": "11"}, {"illustId": "12"}]}}

    monkeypatch.setattr(web_bookmarks, "read_json", fake_json)
    client = PixivBookmarksClient(user_id="1234", cookies_path=cookies, max_results=20)

    posts = await client.fetch_bookmarks_until(set(), max_pages=1)

    assert posts == [
        BookmarkPost("11", "https://www.pixiv.net/artworks/11"),
        BookmarkPost("12", "https://www.pixiv.net/artworks/12"),
        BookmarkPost("22", "https://www.pixiv.net/artworks/22"),
    ]


@pytest.mark.asyncio
async def test_poipiku_bookmark_client_dedupes_thumbnail_links(monkeypatch, tmp_path: Path):
    cookies = tmp_path / "poipiku.txt"
    cookies.write_text("poipiku.com\tTRUE\t/\tTRUE\t0\tPOIPIKU_LK\tcookie\n", encoding="utf-8")
    html = """
    <title>Illustration&more Box [POIPIKU] - favorite</title>
    <a href="/1978861/11424450.html">open</a>
    <a href="/1978861/11424450.html">thumb</a>
    <a href="/7387205/11741398.html?from=favo">open</a>
    """
    monkeypatch.setattr(web_bookmarks, "read_text", lambda *args, **kwargs: html)
    client = PoipikuBookmarksClient(cookies_path=cookies, max_results=20)

    posts = await client.fetch_bookmarks_until(set(), max_pages=1)

    assert posts == [
        BookmarkPost("1978861:11424450", "https://poipiku.com/1978861/11424450.html"),
        BookmarkPost("7387205:11741398", "https://poipiku.com/7387205/11741398.html"),
    ]


@pytest.mark.asyncio
async def test_poipiku_monitor_uses_separate_state_and_submits_existing_bookmarks(app_factory, sample_media):
    url = "https://poipiku.com/1978861/11424450.html"
    service, db, _, downloader = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url})})
    clock = FakeClock()
    client = FakeBookmarkClient([[BookmarkPost("1978861:11424450", url)], [BookmarkPost("1978861:11424450", url)]])
    monitor = TwitterBookmarkMonitor(
        config=service.config,
        db=db,
        archive_bot=service,
        client=client,
        clock=clock,
        provider="poipiku",
        label="Poipiku",
        configured=lambda: True,
    )

    await monitor.poll_once()
    clock.advance(10)
    await monitor.poll_once()

    assert downloader.calls == [url]
    assert db.bookmark_item_count(provider="poipiku") == 1
    assert db.bookmark_item_count(provider="twitter") == 0
    assert db.get_submission(1).username == "poipiku_bookmark_monitor"


def test_bookmark_monitor_group_activates_all_configured_monitors():
    class StubMonitor:
        def __init__(self, label: str, configured: bool):
            self.label = label
            self.configured = configured
            self.activations = 0

        def is_configured(self) -> bool:
            return self.configured

        def activate(self) -> None:
            self.activations += 1

    twitter = StubMonitor("Twitter", True)
    pixiv = StubMonitor("Pixiv", False)
    poipiku = StubMonitor("Poipiku", True)
    group = BookmarkMonitorGroup((twitter, pixiv, poipiku))

    group.activate()

    assert twitter.activations == 1
    assert pixiv.activations == 0
    assert poipiku.activations == 1
