from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .config import BotConfig
from .db import Database
from .service import ArchiveBot, Clock, SystemClock


@dataclass(frozen=True)
class BookmarkPost:
    tweet_id: str
    url: str


class BookmarkClient(Protocol):
    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        ...


class XBookmarksClient:
    def __init__(self, *, api_base: str, user_id: str, access_token: str):
        self.api_base = api_base.rstrip("/")
        self.user_id = user_id
        self.access_token = access_token

    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_sync)

    def _fetch_bookmarks_sync(self) -> list[BookmarkPost]:
        query = urllib.parse.urlencode(
            {
                "max_results": "100",
                "tweet.fields": "created_at,author_id",
            }
        )
        url = f"{self.api_base}/2/users/{urllib.parse.quote(self.user_id)}/bookmarks?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "tg-archive-bot/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [
            BookmarkPost(tweet_id=str(item["id"]), url=f"https://twitter.com/i/status/{item['id']}")
            for item in payload.get("data", [])
            if item.get("id")
        ]


class TwitterBookmarkMonitor:
    def __init__(
        self,
        *,
        config: BotConfig,
        db: Database,
        archive_bot: ArchiveBot,
        client: BookmarkClient,
        clock: Clock | None = None,
    ):
        self.config = config
        self.db = db
        self.archive_bot = archive_bot
        self.client = client
        self.clock = clock or SystemClock()

    async def run_forever(self) -> None:
        logging.info(
            "Twitter bookmark monitor started; poll=%ss grace=%ss",
            self.config.twitter_bookmarks_poll_seconds,
            self.config.twitter_bookmarks_grace_seconds,
        )
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.exception("Twitter bookmark monitor poll failed: %s", exc)
            await asyncio.sleep(self.config.twitter_bookmarks_poll_seconds)

    async def poll_once(self) -> None:
        posts = await self.client.fetch_bookmarks()
        now = self.clock.now()
        current_ids = {post.tweet_id for post in posts}
        is_initial_bootstrap = self.db.get_bookmark_monitor_state("bootstrapped") != "1"
        initial_status = "baseline" if is_initial_bootstrap else "pending"

        for post in posts:
            self.db.mark_bookmark_seen(post.tweet_id, post.url, now, initial_status=initial_status)

        for item in self.db.active_bookmark_items():
            if item.tweet_id not in current_ids:
                self.db.mark_bookmark_removed(item.tweet_id, now)

        if is_initial_bootstrap:
            self.db.set_bookmark_monitor_state("bootstrapped", "1")
            logging.info("Twitter bookmark monitor initialized baseline with %s bookmarks", len(posts))
            return

        grace = timedelta(seconds=self.config.twitter_bookmarks_grace_seconds)
        for item in self.db.pending_bookmark_items():
            if item.tweet_id not in current_ids:
                continue
            if now - item.first_seen_at < grace:
                continue
            try:
                status, submission_id = await self.archive_bot.submit_url_as_admin(
                    item.url,
                    username="bookmark_monitor",
                )
                if status == "duplicate":
                    self.db.mark_bookmark_duplicate(item.tweet_id, submission_id, now)
                elif status == "submitted":
                    self.db.mark_bookmark_submitted(item.tweet_id, submission_id, now)
                else:
                    self.db.mark_bookmark_failed(item.tweet_id, status, now)
            except Exception as exc:
                logging.exception("Failed to submit bookmark %s: %s", item.tweet_id, exc)
                self.db.mark_bookmark_failed(item.tweet_id, str(exc), now)
