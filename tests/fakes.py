from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image
from tg_archive_bot.twitter_bookmarks import BookmarkPost


@dataclass
class FakeUser:
    id: int
    username: str | None = None


@dataclass
class FakeChat:
    id: int
    username: str | None = None


@dataclass
class FakeEntity:
    type: str
    url: str | None = None


@dataclass
class FakeForwardOrigin:
    chat: FakeChat | None = None
    message_id: int | None = None
    message: Any | None = None


@dataclass
class FakeSentMessage:
    message_id: int
    caption: str = ""
    replies: list[dict[str, Any]] = field(default_factory=list)

    async def reply_text(self, text: str, **kwargs: Any) -> "FakeSentMessage":
        self.replies.append({"method": "reply_text", "text": text, **kwargs})
        return FakeSentMessage(self.message_id + 1000, caption=text)


@dataclass
class FakeMessage:
    text: str | None = None
    caption: str | None = None
    forward_origin: FakeForwardOrigin | None = None
    entities: list[FakeEntity] = field(default_factory=list)
    replies: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)

    async def reply_text(self, text: str, **kwargs: Any) -> FakeSentMessage:
        self.replies.append({"method": "reply_text", "text": text, **kwargs})
        return FakeSentMessage(len(self.replies), caption=text)

    async def reply_document(self, document: Any, filename: str | None = None, **kwargs: Any) -> FakeSentMessage:
        self.documents.append({"method": "reply_document", "document": document, "filename": filename, **kwargs})
        return FakeSentMessage(len(self.documents))


@dataclass
class FakeUpdate:
    effective_user: FakeUser
    message: FakeMessage | None = None
    callback_query: Any | None = None


@dataclass
class FakeContext:
    args: list[str] = field(default_factory=list)


@dataclass
class FakeCallbackQuery:
    data: str
    message: FakeSentMessage
    answered: bool = False
    edited_caption: str | None = None
    edited_text: str | None = None

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_caption(self, caption: str, **kwargs: Any) -> None:
        self.edited_caption = caption
        self.message.caption = caption

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.edited_text = text
        self.message.caption = text


class FakeBot:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._message_id = 100

    def _next(self, caption: str = "") -> FakeSentMessage:
        self._message_id += 1
        return FakeSentMessage(self._message_id, caption=caption)

    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any) -> FakeSentMessage:
        self.calls.append({"method": "send_message", "chat_id": chat_id, "text": text, **kwargs})
        return self._next(text)

    async def send_photo(self, chat_id: int | str, photo: Any, **kwargs: Any) -> FakeSentMessage:
        self.calls.append({"method": "send_photo", "chat_id": chat_id, "photo": photo, **kwargs})
        return self._next(kwargs.get("caption", ""))

    async def send_video(self, chat_id: int | str, video: Any, **kwargs: Any) -> FakeSentMessage:
        self.calls.append({"method": "send_video", "chat_id": chat_id, "video": video, **kwargs})
        return self._next(kwargs.get("caption", ""))

    async def send_document(self, chat_id: int | str, document: Any, **kwargs: Any) -> FakeSentMessage:
        self.calls.append({"method": "send_document", "chat_id": chat_id, "document": document, **kwargs})
        return self._next(kwargs.get("caption", ""))

    async def send_media_group(self, chat_id: int | str, media: list[dict[str, Any]]) -> list[FakeSentMessage]:
        self.calls.append({"method": "send_media_group", "chat_id": chat_id, "media": media})
        return [self._next(item.get("caption", "")) for item in media]

    async def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        self.calls.append({"method": "delete_message", "chat_id": chat_id, "message_id": message_id})
        return True


class FakeDownloader:
    def __init__(self, mapping: dict[str, tuple[list[str], dict]] | None = None):
        self.mapping = mapping or {}
        self.calls: list[str] = []

    async def download_media(self, url: str) -> tuple[list[str], dict]:
        self.calls.append(url)
        return self.mapping.get(url, ([], {}))


class FakeClock:
    def __init__(self, value: datetime | None = None):
        self.value = value or datetime(2026, 5, 11, 12, 0, 0)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.value = self.value + timedelta(seconds=seconds)


class FakeBookmarkClient:
    def __init__(self, snapshots: list[list[BookmarkPost]] | None = None):
        self.snapshots = snapshots or []
        self.calls = 0

    async def fetch_bookmarks(self) -> list[BookmarkPost]:
        if not self.snapshots:
            self.calls += 1
            return []
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]


class FakeSafetyDetector:
    def __init__(self, scores: list[float | None]):
        self.scores = scores
        self.calls: list[list[str]] = []

    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int]:
        self.calls.append(list(image_paths))
        if not self.scores:
            return None, 0
        score = self.scores.pop(0)
        return score, len(image_paths)


def make_image(path: Path, fmt: str = "JPEG") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (4, 4), color=(255, 255, 255))
    image.save(path, fmt)
    return str(path)


def make_bytes(path: Path, content: bytes = b"fake") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)
