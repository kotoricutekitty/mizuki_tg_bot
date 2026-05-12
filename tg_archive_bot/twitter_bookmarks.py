from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
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


class XBookmarksAPIError(RuntimeError):
    def __init__(self, *, status: int, title: str = "", detail: str = "", problem_type: str = "", body: str = ""):
        self.status = status
        self.title = title
        self.detail = detail
        self.problem_type = problem_type
        self.body = body
        message = f"X bookmarks API returned HTTP {status}"
        if title:
            message += f" ({title})"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class XCreditsDepletedError(XBookmarksAPIError):
    pass


class BookmarkClient(Protocol):
    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        ...


class XBookmarksClient:
    def __init__(self, *, api_base: str, user_id: str, access_token: str, max_results: int = 5):
        self.api_base = api_base.rstrip("/")
        self.user_id = user_id
        self.access_token = access_token
        self.max_results = max_results

    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_sync)

    def _fetch_bookmarks_sync(self) -> list[BookmarkPost]:
        query = urllib.parse.urlencode(
            {
                "max_results": str(self.max_results),
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
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise parse_x_bookmarks_http_error(exc) from exc
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
        self.active = False
        self.last_activity_at: datetime | None = None
        self.last_seen_ids: set[str] | None = None

    def is_configured(self) -> bool:
        return bool(self.config.twitter_bookmarks_user_id and self.config.twitter_bookmarks_access_token)

    def activate(self) -> None:
        if not self.is_configured():
            raise RuntimeError("Twitter bookmark monitor is not configured")
        now = self.clock.now()
        self.active = True
        self.last_activity_at = now
        self.last_seen_ids = None
        self.db.set_bookmark_monitor_state("last_error_code", "")
        self.db.set_bookmark_monitor_state("last_error", "")
        logging.info(
            "Twitter bookmark monitor activated; poll=%ss grace=%ss idle=%ss",
            self.config.twitter_bookmarks_poll_seconds,
            self.config.twitter_bookmarks_grace_seconds,
            self.config.twitter_bookmarks_idle_seconds,
        )

    async def run_forever(self) -> None:
        logging.info(
            "Twitter bookmark monitor started; poll=%ss grace=%ss",
            self.config.twitter_bookmarks_poll_seconds,
            self.config.twitter_bookmarks_grace_seconds,
        )
        while True:
            if not self.active:
                await asyncio.sleep(1)
                continue
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.exception("Twitter bookmark monitor poll failed: %s", exc)
            await asyncio.sleep(self.config.twitter_bookmarks_poll_seconds)

    async def poll_once(self) -> None:
        now = self.clock.now()
        try:
            posts = await self.client.fetch_bookmarks()
        except XCreditsDepletedError as exc:
            self.active = False
            self.db.set_bookmark_monitor_state("last_error_code", "credits_depleted")
            self.db.set_bookmark_monitor_state("last_error", str(exc))
            self.db.set_bookmark_monitor_state("credits_depleted_at", now.isoformat())
            logging.error("Twitter bookmark monitor stopped because X API credits are depleted: %s", exc)
            return
        current_ids = {post.tweet_id for post in posts}
        changed = self.last_seen_ids is None or current_ids != self.last_seen_ids
        if changed:
            self.last_activity_at = now
        self.last_seen_ids = set(current_ids)
        is_initial_bootstrap = self.db.get_bookmark_monitor_state("bootstrapped") != "1"
        initial_status = "baseline" if is_initial_bootstrap else "pending"

        for post in posts:
            self.db.mark_bookmark_seen(post.tweet_id, post.url, now, initial_status=initial_status)

        for item in self.db.active_bookmark_items():
            if item.tweet_id not in current_ids:
                self.db.mark_bookmark_removed(item.tweet_id, now)
                changed = True
                self.last_activity_at = now

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
                    changed = True
                elif status == "submitted":
                    self.db.mark_bookmark_submitted(item.tweet_id, submission_id, now)
                    changed = True
                else:
                    self.db.mark_bookmark_failed(item.tweet_id, status, now)
                    changed = True
                if changed:
                    self.last_activity_at = now
            except Exception as exc:
                logging.exception("Failed to submit bookmark %s: %s", item.tweet_id, exc)
                self.db.mark_bookmark_failed(item.tweet_id, str(exc), now)
                self.last_activity_at = now

        if self.last_activity_at and now - self.last_activity_at >= timedelta(seconds=self.config.twitter_bookmarks_idle_seconds):
            self.active = False
            logging.info("Twitter bookmark monitor auto-stopped after idle timeout")


def parse_x_bookmarks_http_error(exc: urllib.error.HTTPError) -> XBookmarksAPIError:
    raw_body = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        payload = {}
    title = str(payload.get("title") or "")
    detail = str(payload.get("detail") or "")
    problem_type = str(payload.get("type") or "")
    error_cls = XCreditsDepletedError if is_credits_depleted(exc.code, title, detail, problem_type) else XBookmarksAPIError
    return error_cls(status=exc.code, title=title, detail=detail, problem_type=problem_type, body=raw_body)


def is_credits_depleted(status: int, title: str, detail: str, problem_type: str) -> bool:
    if status != 402:
        return False
    haystack = " ".join([title, detail, problem_type]).lower()
    return "creditsdepleted" in haystack or "credits" in haystack
