from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .downloader import load_cookie_header
from .twitter_bookmarks import BookmarkPost


class ActivatableBookmarkMonitor(Protocol):
    label: str
    active: bool

    def is_configured(self) -> bool:
        ...

    def activate(self) -> None:
        ...

    async def poll_once(self) -> None:
        ...


@dataclass
class BookmarkMonitorGroup:
    monitors: tuple[ActivatableBookmarkMonitor, ...]

    def is_configured(self) -> bool:
        return any(monitor.is_configured() for monitor in self.monitors)

    def activate(self) -> None:
        activated: list[str] = []
        for monitor in self.monitors:
            if not monitor.is_configured():
                continue
            monitor.activate()
            activated.append(monitor.label)
        if not activated:
            raise RuntimeError("No bookmark monitor is configured")
        logging.info("Activated bookmark monitors: %s", ", ".join(activated))

    async def poll_once(self) -> None:
        for monitor in self.monitors:
            if monitor.is_configured() and monitor.active:
                await monitor.poll_once()


class PixivBookmarksClient:
    def __init__(self, *, user_id: str, cookies_path: Path, max_results: int = 20):
        self.user_id = user_id
        self.cookies_path = cookies_path
        self.max_results = max_results

    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_until_sync, set(), 1)

    async def fetch_bookmarks_until(self, stop_ids: set[str], max_pages: int = 4) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_until_sync, stop_ids, max_pages)

    def _fetch_bookmarks_until_sync(self, stop_ids: set[str], max_pages: int) -> list[BookmarkPost]:
        posts: list[BookmarkPost] = []
        seen: set[str] = set()
        for rest in ("show", "hide"):
            for page in range(max(1, max_pages)):
                page_posts = self._fetch_page(rest=rest, offset=page * self.max_results)
                for post in page_posts:
                    if post.tweet_id in seen:
                        continue
                    seen.add(post.tweet_id)
                    posts.append(post)
                if not page_posts or any(post.tweet_id in stop_ids for post in page_posts):
                    break
        return posts

    def _fetch_page(self, *, rest: str, offset: int) -> list[BookmarkPost]:
        query = urllib.parse.urlencode(
            {"tag": "", "offset": str(offset), "limit": str(self.max_results), "rest": rest, "lang": "en"}
        )
        url = f"https://www.pixiv.net/ajax/user/{urllib.parse.quote(self.user_id)}/illusts/bookmarks?{query}"
        payload = read_json(url, self.cookies_path, referer=f"https://www.pixiv.net/users/{self.user_id}/bookmarks/artworks")
        if payload.get("error"):
            raise RuntimeError(f"Pixiv bookmarks request failed: {payload.get('message') or 'unknown error'}")
        works = payload.get("body", {}).get("works", [])
        if not isinstance(works, list):
            return []
        posts: list[BookmarkPost] = []
        for work in works:
            if not isinstance(work, dict):
                continue
            illust_id = str(work.get("id") or work.get("illustId") or "").strip()
            if not illust_id:
                continue
            posts.append(BookmarkPost(illust_id, f"https://www.pixiv.net/artworks/{illust_id}"))
        return posts


class PoipikuBookmarksClient:
    BOOKMARK_URL = "https://poipiku.com/MyBookmarkListPcV.jsp"
    POST_PATH_RE = re.compile(r'href=["\'](?P<path>/\d+/\d+\.html)(?:\?[^"\']*)?["\']')
    FALLBACK_PATH_RE = re.compile(r"(?P<path>/\d+/\d+\.html)")

    def __init__(self, *, cookies_path: Path, max_results: int = 20):
        self.cookies_path = cookies_path
        self.max_results = max_results

    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_until_sync, set(), 1)

    async def fetch_bookmarks_until(self, stop_ids: set[str], max_pages: int = 4) -> list[BookmarkPost]:
        return await asyncio.to_thread(self._fetch_bookmarks_until_sync, stop_ids, max_pages)

    def _fetch_bookmarks_until_sync(self, stop_ids: set[str], max_pages: int) -> list[BookmarkPost]:
        posts: list[BookmarkPost] = []
        seen: set[str] = set()
        for page in range(max(1, max_pages)):
            page_posts = self._fetch_page(page)
            for post in page_posts:
                if post.tweet_id in seen:
                    continue
                seen.add(post.tweet_id)
                posts.append(post)
            if not page_posts or any(post.tweet_id in stop_ids for post in page_posts):
                break
            if len(page_posts) < self.max_results:
                break
        return posts

    def _fetch_page(self, page: int) -> list[BookmarkPost]:
        query = urllib.parse.urlencode({"PG": str(page), "ID": "-1"})
        html = read_text(f"{self.BOOKMARK_URL}?{query}", self.cookies_path, referer=self.BOOKMARK_URL)
        if re.search(r"<title>\s*(?:Sign in|ログイン)", html, re.IGNORECASE):
            raise RuntimeError("Poipiku bookmark cookies are not logged in")
        paths = [match.group("path") for match in self.POST_PATH_RE.finditer(html)]
        if not paths:
            paths = [match.group("path") for match in self.FALLBACK_PATH_RE.finditer(html)]
        posts: list[BookmarkPost] = []
        seen: set[str] = set()
        for path in paths:
            parts = path.strip("/").removesuffix(".html").split("/")
            if len(parts) != 2:
                continue
            item_id = ":".join(parts)
            if item_id in seen:
                continue
            seen.add(item_id)
            posts.append(BookmarkPost(item_id, f"https://poipiku.com{path}"))
        return posts


def read_json(url: str, cookies_path: Path, *, referer: str) -> dict:
    return json.loads(read_text(url, cookies_path, referer=referer))


def read_text(url: str, cookies_path: Path, *, referer: str) -> str:
    cookie_header = load_cookie_header(cookies_path)
    if not cookie_header:
        raise RuntimeError(f"Missing bookmark cookies for {urllib.parse.urlparse(url).netloc}")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Cookie": cookie_header,
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 tg-archive-bot/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")
