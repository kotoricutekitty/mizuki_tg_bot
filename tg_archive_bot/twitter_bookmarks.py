from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from .config import BotConfig
from .db import Database
from .service import ArchiveBot, Clock, SystemClock


@dataclass(frozen=True)
class BookmarkPost:
    tweet_id: str
    url: str


@dataclass(frozen=True)
class BookmarkPage:
    posts: list[BookmarkPost]
    next_token: str | None = None


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


@dataclass(frozen=True)
class OAuthRefreshResult:
    access_token: str
    refresh_token: str


class OAuthTokenRefresher(Protocol):
    def refresh_access_token(self) -> OAuthRefreshResult:
        ...


class BookmarkClient(Protocol):
    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        ...


class XBookmarksClient:
    def __init__(
        self,
        *,
        api_base: str,
        user_id: str,
        access_token: str,
        max_results: int = 5,
        token_refresher: OAuthTokenRefresher | None = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.user_id = user_id
        self.access_token = access_token
        self.max_results = max_results
        self.token_refresher = token_refresher

    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_sync)

    async def fetch_bookmarks_until(self, stop_ids: set[str], max_pages: int = 4) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_until_sync, stop_ids, max_pages)

    def _fetch_bookmarks_sync(self) -> list[BookmarkPost]:
        return self._fetch_bookmarks_with_refresh(lambda: self._fetch_bookmarks_page().posts)

    def _fetch_bookmarks_until_sync(self, stop_ids: set[str], max_pages: int = 4) -> list[BookmarkPost]:
        def fetch_pages() -> list[BookmarkPost]:
            all_posts: list[BookmarkPost] = []
            pagination_token: str | None = None
            page_size = self.max_results
            for _ in range(max(1, max_pages)):
                page = self._fetch_bookmarks_page(max_results=page_size, pagination_token=pagination_token)
                all_posts.extend(page.posts)
                if any(post.tweet_id in stop_ids for post in page.posts):
                    break
                if not page.next_token:
                    break
                pagination_token = page.next_token
                page_size = min(max(page_size * 2, page_size + 1), 100)
            return all_posts

        return self._fetch_bookmarks_with_refresh(fetch_pages)

    def _fetch_bookmarks_with_refresh(self, fetcher):
        try:
            return fetcher()
        except XBookmarksAPIError as exc:
            if exc.status != 401 or not self.token_refresher:
                raise
            refreshed = self.token_refresher.refresh_access_token()
            self.access_token = refreshed.access_token
            logging.info("Refreshed X OAuth access token after bookmarks API returned 401")
            return fetcher()

    def _fetch_bookmarks_page(self, max_results: int | None = None, pagination_token: str | None = None) -> BookmarkPage:
        query_args = {
            "max_results": str(max_results or self.max_results),
            "tweet.fields": "created_at,author_id",
        }
        if pagination_token:
            query_args["pagination_token"] = pagination_token
        query = urllib.parse.urlencode(query_args)
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
        posts = [
            BookmarkPost(tweet_id=str(item["id"]), url=f"https://twitter.com/i/status/{item['id']}")
            for item in payload.get("data", [])
            if item.get("id")
        ]
        next_token = payload.get("meta", {}).get("next_token")
        return BookmarkPage(posts=posts, next_token=str(next_token) if next_token else None)


class XOAuth2TokenRefresher:
    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str = "",
        refresh_token: str,
        env_path: Path | None = None,
    ):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.env_path = env_path

    def refresh_access_token(self) -> OAuthRefreshResult:
        if not self.client_id or not self.refresh_token:
            raise RuntimeError("Missing X OAuth client id or refresh token")
        payload = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
            }
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "tg-archive-bot/0.1",
        }
        if self.client_secret:
            credential = f"{self.client_id}:{self.client_secret}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(credential).decode("ascii")
        request = urllib.request.Request(self.token_url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise parse_x_bookmarks_http_error(exc) from exc
        access_token = str(data.get("access_token") or "")
        refresh_token = str(data.get("refresh_token") or self.refresh_token)
        if not access_token:
            raise RuntimeError("X OAuth refresh response did not include an access token")
        self.refresh_token = refresh_token
        if self.env_path:
            update_env_file(
                self.env_path,
                {
                    "TWITTER_BOOKMARKS_ACCESS_TOKEN": access_token,
                    "TWITTER_BOOKMARKS_REFRESH_TOKEN": refresh_token,
                },
            )
        return OAuthRefreshResult(access_token=access_token, refresh_token=refresh_token)


class TwitterBookmarkMonitor:
    def __init__(
        self,
        *,
        config: BotConfig,
        db: Database,
        archive_bot: ArchiveBot,
        client: BookmarkClient,
        clock: Clock | None = None,
        provider: str = "twitter",
        label: str = "Twitter",
        configured: Callable[[], bool] | None = None,
    ):
        self.config = config
        self.db = db
        self.archive_bot = archive_bot
        self.client = client
        self.clock = clock or SystemClock()
        self.provider = provider
        self.label = label
        self.configured = configured
        self.active = False
        self.last_activity_at: datetime | None = None
        self.last_seen_ids: set[str] | None = None
        self.last_fetch_adaptive = False

    def is_configured(self) -> bool:
        if self.configured:
            return self.configured()
        return bool(self.config.twitter_bookmarks_user_id and self.config.twitter_bookmarks_access_token)

    def activate(self) -> None:
        if not self.is_configured():
            raise RuntimeError(f"{self.label} bookmark monitor is not configured")
        now = self.clock.now()
        self.active = True
        self.last_activity_at = now
        self.last_seen_ids = None
        self.db.set_bookmark_monitor_state("last_error_code", "", provider=self.provider)
        self.db.set_bookmark_monitor_state("last_error", "", provider=self.provider)
        logging.info(
            "%s bookmark monitor activated; poll=%ss grace=%ss idle=%ss",
            self.label,
            self.config.twitter_bookmarks_poll_seconds,
            self.config.twitter_bookmarks_grace_seconds,
            self.config.twitter_bookmarks_idle_seconds,
        )

    async def run_forever(self) -> None:
        logging.info(
            "%s bookmark monitor started; poll=%ss grace=%ss",
            self.label,
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
                logging.exception("%s bookmark monitor poll failed: %s", self.label, exc)
                await self.archive_bot.notify_admin_error(
                    f"{self.label} bookmark monitor poll failed",
                    exc,
                    throttle_key=f"{self.provider}_bookmark_run_forever:{type(exc).__name__}:{str(exc)[:120]}",
                )
            await asyncio.sleep(self.config.twitter_bookmarks_poll_seconds)

    async def poll_once(self) -> None:
        now = self.clock.now()
        try:
            posts = await self.fetch_bookmark_posts()
        except XCreditsDepletedError as exc:
            self.active = False
            self.db.set_bookmark_monitor_state("last_error_code", "credits_depleted", provider=self.provider)
            self.db.set_bookmark_monitor_state("last_error", str(exc), provider=self.provider)
            self.db.set_bookmark_monitor_state("credits_depleted_at", now.isoformat(), provider=self.provider)
            logging.error("Twitter bookmark monitor stopped because X API credits are depleted: %s", exc)
            await self.archive_bot.notify_bookmark_watch_stopped("credits_depleted")
            await self.archive_bot.notify_admin_error(
                "Twitter bookmark monitor stopped",
                exc,
                throttle_key="bookmark_credits_depleted",
            )
            return
        except Exception as exc:
            self.db.set_bookmark_monitor_state("last_error_code", "poll_failed", provider=self.provider)
            self.db.set_bookmark_monitor_state("last_error", str(exc), provider=self.provider)
            logging.exception("%s bookmark monitor poll failed: %s", self.label, exc)
            await self.archive_bot.notify_admin_error(
                f"{self.label} bookmark monitor poll failed",
                exc,
                throttle_key=f"{self.provider}_bookmark_poll:{type(exc).__name__}:{str(exc)[:120]}",
            )
            return
        previous_seen_ids = self.last_seen_ids
        current_ids = {post.tweet_id for post in posts}
        changed = self.last_seen_ids is None or current_ids != self.last_seen_ids
        if changed:
            self.last_activity_at = now
        self.last_seen_ids = set(current_ids)
        is_initial_bootstrap = self.db.get_bookmark_monitor_state("bootstrapped", provider=self.provider) != "1"
        initial_status = "pending"

        for post in posts:
            self.db.mark_bookmark_seen(post.tweet_id, post.url, now, initial_status=initial_status, provider=self.provider)

        for item in self.db.active_bookmark_items(provider=self.provider):
            if self.last_fetch_adaptive and previous_seen_ids is not None and item.tweet_id not in previous_seen_ids:
                continue
            if item.tweet_id not in current_ids:
                self.db.mark_bookmark_removed(item.tweet_id, now, provider=self.provider)
                changed = True
                self.last_activity_at = now

        if is_initial_bootstrap:
            self.db.set_bookmark_monitor_state("bootstrapped", "1", provider=self.provider)
            logging.info("%s bookmark monitor loaded %s existing bookmarks for submission", self.label, len(posts))

        grace = timedelta(seconds=self.config.twitter_bookmarks_grace_seconds)
        for item in self.db.pending_bookmark_items(provider=self.provider):
            if item.tweet_id not in current_ids:
                continue
            if now - item.first_seen_at < grace:
                continue
            try:
                status, submission_id = await self.archive_bot.submit_url_as_admin(
                    item.url,
                    username="bookmark_monitor" if self.provider == "twitter" else f"{self.provider}_bookmark_monitor",
                )
                if status == "duplicate":
                    self.db.mark_bookmark_duplicate(item.tweet_id, submission_id, now, provider=self.provider)
                    changed = True
                elif status in {"submitted", "pending_review"}:
                    self.db.mark_bookmark_submitted(item.tweet_id, submission_id, now, provider=self.provider)
                    changed = True
                else:
                    self.db.mark_bookmark_failed(item.tweet_id, status, now, provider=self.provider)
                    changed = True
                if changed:
                    self.last_activity_at = now
            except Exception as exc:
                logging.exception("Failed to submit %s bookmark %s: %s", self.label, item.tweet_id, exc)
                self.db.mark_bookmark_failed(item.tweet_id, str(exc), now, provider=self.provider)
                self.last_activity_at = now
                await self.archive_bot.notify_admin_error(
                    f"{self.label} bookmark submit failed",
                    exc,
                    detail=f"bookmark_id={item.tweet_id}\nurl={item.url}",
                    throttle_key=f"{self.provider}_bookmark_submit:{item.tweet_id}",
                )

        if self.last_activity_at and now - self.last_activity_at >= timedelta(seconds=self.config.twitter_bookmarks_idle_seconds):
            self.active = False
            logging.info("%s bookmark monitor auto-stopped after idle timeout", self.label)
            await self.archive_bot.notify_bookmark_watch_stopped("idle")

    async def fetch_bookmark_posts(self) -> list[BookmarkPost]:
        fetch_until = getattr(self.client, "fetch_bookmarks_until", None)
        if not callable(fetch_until):
            self.last_fetch_adaptive = False
            return await self.client.fetch_bookmarks()
        known_ids = self.db.known_bookmark_ids(provider=self.provider)
        if self.last_seen_ids:
            known_ids.update(self.last_seen_ids)
        max_pages = (
            self.config.twitter_bookmarks_max_pages
            if self.provider == "twitter"
            else self.config.web_bookmarks_max_pages
        )
        posts = await fetch_until(known_ids, max_pages)
        self.last_fetch_adaptive = True
        logging.info("%s bookmark monitor fetched %s bookmarks with adaptive pagination", self.label, len(posts))
        return posts


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


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n")
    path.chmod(0o600)
