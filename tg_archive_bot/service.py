from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from . import messages
from .config import BotConfig
from .db import Database, Submission
from .downloader import Downloader
from .media import MAX_PHOTO_SIZE, MAX_VIDEO_SIZE, media_kind
from .url_utils import extract_urls_from_text, normalize_url


class Clock(Protocol):
    def now(self) -> datetime:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now()


class BotClient(Protocol):
    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any) -> Any:
        ...

    async def send_photo(self, chat_id: int | str, photo: Any, **kwargs: Any) -> Any:
        ...

    async def send_video(self, chat_id: int | str, video: Any, **kwargs: Any) -> Any:
        ...

    async def send_document(self, chat_id: int | str, document: Any, **kwargs: Any) -> Any:
        ...

    async def send_media_group(self, chat_id: int | str, media: list[dict[str, Any]]) -> Any:
        ...


class BookmarkActivator(Protocol):
    def is_configured(self) -> bool:
        ...

    def activate(self) -> None:
        ...


@dataclass
class SubmitResult:
    status: int
    body: dict[str, Any]


class ArchiveBot:
    def __init__(self, config: BotConfig, db: Database, downloader: Downloader, bot: BotClient, clock: Clock | None = None):
        self.config = config
        self.db = db
        self.downloader = downloader
        self.bot = bot
        self.clock = clock or SystemClock()
        self.bookmark_monitor: BookmarkActivator | None = None

    async def start(self, update: Any, context: Any = None) -> None:
        await update.message.reply_text(messages.START_TEXT)

    async def help_command(self, update: Any, context: Any = None) -> None:
        await update.message.reply_text(messages.HELP_TEXT)

    async def config_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        text = messages.config_header()
        for key, value in self.db.get_config_rows():
            text += f"{key}: {value}\n"
        await update.message.reply_text(text)

    async def set_command(self, update: Any, context: Any) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        args = getattr(context, "args", [])
        if len(args) < 2:
            await update.message.reply_text(messages.SET_USAGE)
            return
        key = args[0]
        value = " ".join(args[1:])
        self.db.set_config(key, value)
        await update.message.reply_text(messages.set_success(key, value))

    async def pending_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        pending = self.db.pending_submissions()
        if not pending:
            await update.message.reply_text(messages.NO_PENDING)
            return
        text = messages.pending_header()
        for sub in pending:
            text += f"#{sub.id} - {sub.username} ({sub.user_id}) - {sub.url}\n"
        await update.message.reply_text(text)

    async def pixiv_status_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        count, first_time, last_time = self.db.count_pixiv_downloads(self.config.pixiv_limit_hours)
        await update.message.reply_text(messages.pixiv_status(count, first_time, last_time))

    async def bookmark_watch_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.BOOKMARK_WATCH_FORBIDDEN)
            return
        if not self.bookmark_monitor or not self.bookmark_monitor.is_configured():
            await update.message.reply_text(messages.BOOKMARK_WATCH_UNAVAILABLE)
            return
        self.bookmark_monitor.activate()
        await update.message.reply_text(messages.BOOKMARK_WATCH_STARTED)

    def activate_bookmark_watch(self) -> SubmitResult:
        if not self.bookmark_monitor or not self.bookmark_monitor.is_configured():
            return SubmitResult(503, {"status": "unavailable", "message": "Twitter bookmark monitor is not configured"})
        self.bookmark_monitor.activate()
        return SubmitResult(200, {"status": "started", "message": "Twitter bookmark monitor started"})

    async def handle_message(self, update: Any, context: Any = None) -> None:
        if not update.message:
            return
        user_id = update.effective_user.id
        username = update.effective_user.username or str(user_id)

        if await self._maybe_return_original(update):
            return

        text = collect_message_text(update.message)
        logging.info("收到来自用户 %s(%s) 的消息，提取到文本: %s", username, user_id, text[:200])
        urls = extract_urls_from_text(text)
        if not urls:
            await update.message.reply_text(messages.NO_SUPPORTED_LINK)
            return

        is_admin = user_id in self.config.admin_ids
        valid_urls: list[str] = []
        for url in urls:
            existing = self.db.find_by_url(url)
            if existing:
                await update.message.reply_text(messages.duplicate_submission(existing.id, existing.status))
                continue
            if "pixiv.net" in url:
                count, _, _ = self.db.count_pixiv_downloads(self.config.pixiv_limit_hours)
                if count >= self.config.pixiv_limit_count:
                    await update.message.reply_text(messages.PIXIV_RATE_LIMITED)
                    continue
                self.db.record_pixiv_download(url)
            valid_urls.append(url)

        if not valid_urls:
            return

        await update.message.reply_text(messages.found_links(len(valid_urls)))
        for i, url in enumerate(valid_urls, start=1):
            await update.message.reply_text(messages.processing_link(i, len(valid_urls), url))
            media_files, metadata = await self.downloader.download_media(url)
            if not media_files:
                logging.error("下载失败: %s (用户: %s(%s))", url, username, user_id)
                await update.message.reply_text(messages.download_failed(url))
                continue
            try:
                submission_id = self.db.create_submission(
                    user_id=user_id,
                    username=username,
                    url=url,
                    status="approved" if is_admin else "pending",
                    media_paths=media_files,
                    metadata=metadata,
                    now=self.clock.now(),
                )
            except Exception:
                existing = self.db.find_by_url(url)
                if existing:
                    await update.message.reply_text(messages.duplicate_submission(existing.id, existing.status))
                else:
                    await update.message.reply_text(messages.duplicate_insert_failed())
                continue

            logging.info("创建投稿 #%s: %s (用户: %s(%s), 状态: %s)", submission_id, url, username, user_id, "approved" if is_admin else "pending")
            if is_admin:
                await self.publish_submission(submission_id, user_id)
                await update.message.reply_text(messages.admin_published(url))
            else:
                review_message = await self.send_to_review(submission_id, url, username, media_files, metadata)
                if review_message:
                    self.db.update_message_id(submission_id, int(getattr(review_message, "message_id", 0)), self.clock.now())
                    await update.message.reply_text(messages.submitted_for_review(url))
                else:
                    await update.message.reply_text(messages.review_submit_failed(url))

    async def _maybe_return_original(self, update: Any) -> bool:
        origin = getattr(update.message, "forward_origin", None)
        if not origin or not hasattr(origin, "chat") or not hasattr(origin, "message_id"):
            return False
        if origin.chat is None or origin.message_id is None:
            return False
        forward_chat_id = origin.chat.id
        our_channel_id = self.config.publish_channel_id
        if our_channel_id.startswith("@"):
            forward_chat_username = getattr(origin.chat, "username", "")
            is_our_channel = f"@{forward_chat_username}" == our_channel_id
        else:
            is_our_channel = str(forward_chat_id) == our_channel_id
        if not is_our_channel:
            return False
        submission = self.db.find_by_message_id(origin.message_id)
        if not submission:
            return False
        await update.message.reply_text(messages.original_found(submission.url))
        for media_path in submission.media_paths:
            if os.path.exists(media_path):
                await update.message.reply_document(document=media_path, filename=os.path.basename(media_path))
            else:
                logging.warning("原图不存在: %s", media_path)
        return True

    async def send_to_review(self, submission_id: int, url: str, username: str, media_files: list[str], metadata: dict) -> Any | None:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": messages.APPROVE_BUTTON, "callback_data": f"approve:{submission_id}"},
                    {"text": messages.REJECT_BUTTON, "callback_data": f"reject:{submission_id}"},
                ]
            ]
        }
        text = messages.review_caption(submission_id, username, url, metadata)
        sent_messages: list[Any] = []
        for admin_id in self.config.admin_ids:
            try:
                if len(media_files) == 1:
                    sent = await self._send_single_media(admin_id, media_files[0], text, reply_markup)
                    sent_messages.append(sent)
                else:
                    media = build_media_group(media_files[:10], text)
                    sent_media = await self.bot.send_media_group(chat_id=admin_id, media=media)
                    target = sent_media[-1] if sent_media else None
                    if target and hasattr(target, "reply_text"):
                        sent_messages.append(await target.reply_text("请审核：", reply_markup=reply_markup))
                    elif target:
                        sent_messages.append(target)
            except Exception as exc:
                logging.error("无法给管理员%s发送审核消息: %s", admin_id, exc)
        return sent_messages[0] if sent_messages else None

    async def publish_submission(self, submission_id: int, reviewer_id: int) -> int | None:
        submission = self.db.get_submission(submission_id)
        if not submission:
            logging.error("发布失败: 投稿 #%s 不存在", submission_id)
            return None
        self.db.update_status(submission_id, "approved", reviewer_id, self.clock.now())
        caption = messages.publish_caption(
            submission.url,
            author_name=submission.author_name,
            text=submission.text,
            canonical_url=submission.canonical_url,
        )
        channel_message_id: int | None = None
        media_files = submission.media_paths
        if not media_files:
            logging.error("发布失败: 投稿 #%s 没有媒体", submission_id)
            return None
        if len(media_files) <= 10:
            if len(media_files) == 1:
                sent = await self._send_single_media(
                    self.config.publish_channel_id,
                    media_files[0],
                    caption,
                    parse_mode="HTML",
                )
                channel_message_id = getattr(sent, "message_id", None)
            else:
                sent_media = await self.bot.send_media_group(
                    chat_id=self.config.publish_channel_id,
                    media=build_media_group(media_files[:10], caption, parse_mode="HTML"),
                )
                if sent_media:
                    channel_message_id = getattr(sent_media[0], "message_id", None)
        else:
            reply_markup = {"inline_keyboard": [[{"text": "🖼️ 打开完整画廊", "url": submission.url}]]}
            sent = await self.bot.send_photo(
                chat_id=self.config.publish_channel_id,
                photo=media_files[0],
                caption=f"{caption}\n\n📸 共 {len(media_files)} 张图",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            channel_message_id = getattr(sent, "message_id", None)
            total_groups = (len(media_files) + 9) // 10
            work_id = pixiv_work_id(submission.url)
            for group_idx in range(total_groups):
                group_files = media_files[group_idx * 10 : group_idx * 10 + 10]
                group_caption = f"Full set {group_idx + 1}/{total_groups}"
                if work_id:
                    group_caption += f" — Pixiv {work_id}"
                await self.bot.send_media_group(
                    chat_id=self.config.publish_channel_id,
                    media=build_media_group(group_files, group_caption),
                )
        if channel_message_id:
            self.db.update_message_id(submission_id, int(channel_message_id), self.clock.now())
        logging.info("发布成功: 投稿 #%s 已发送到频道 %s", submission_id, self.config.publish_channel_id)
        return channel_message_id

    async def _send_single_media(
        self,
        chat_id: int | str,
        file_path: str,
        caption: str,
        reply_markup: Any | None = None,
        parse_mode: str | None = None,
    ) -> Any:
        kind = media_kind(file_path)
        file_size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
        kwargs = {"caption": caption}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
        if kind == "photo":
            if file_size <= MAX_PHOTO_SIZE:
                return await self.bot.send_photo(chat_id=chat_id, photo=file_path, **kwargs)
            return await self.bot.send_photo(chat_id=chat_id, photo=file_path, **kwargs)
        if kind == "video":
            if file_size <= MAX_VIDEO_SIZE:
                return await self.bot.send_video(chat_id=chat_id, video=file_path, **kwargs)
            return await self.bot.send_document(chat_id=chat_id, document=file_path, **kwargs)
        return await self.bot.send_document(chat_id=chat_id, document=file_path, **kwargs)

    async def handle_callback(self, update: Any, context: Any = None) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        username = update.effective_user.username or str(user_id)
        existing_caption = getattr(query.message, "caption", None) or ""
        if user_id not in self.config.admin_ids:
            await query.edit_message_caption(caption=messages.callback_no_permission(existing_caption))
            return
        action, submission_id_text = query.data.split(":")
        submission_id = int(submission_id_text)
        submission = self.db.get_submission(submission_id)
        if not submission:
            await query.edit_message_caption(caption=messages.callback_not_found(existing_caption))
            return
        if submission.status != "pending":
            await query.edit_message_caption(caption=messages.callback_already_done(existing_caption, submission.status))
            return
        if action == "approve":
            await self.publish_submission(submission_id, user_id)
            await query.edit_message_caption(caption=messages.callback_approved(existing_caption, username))
            try:
                await self.bot.send_message(submission.user_id, messages.submitter_approved(submission.url))
            except Exception as exc:
                logging.warning("无法通知投稿者 %s: %s", submission.user_id, exc)
        elif action == "reject":
            self.db.update_status(submission_id, "rejected", user_id, self.clock.now())
            await query.edit_message_caption(caption=messages.callback_rejected(existing_caption, username))
            try:
                await self.bot.send_message(submission.user_id, messages.submitter_rejected(submission.url))
            except Exception as exc:
                logging.warning("无法通知投稿者 %s: %s", submission.user_id, exc)

    async def api_submit(self, url: str | None, token: str | None, client_ip: str = "") -> SubmitResult:
        if token != self.config.post_token:
            logging.warning("API投稿token验证失败，IP: %s", client_ip)
            return SubmitResult(401, {"status": "unauthorized", "message": "无效的Token"})
        if not url:
            return SubmitResult(400, {"status": "error", "message": "缺少url参数"})
        normalized_url = normalize_url(url)
        existing = self.db.find_by_url(normalized_url)
        if existing:
            return SubmitResult(
                409,
                {
                    "status": "already_exists",
                    "message": f"链接已投稿，ID #{existing.id}，状态：{existing.status}",
                    "submission_id": existing.id,
                    "current_status": existing.status,
                },
            )
        if "pixiv.net" in normalized_url:
            count, _, _ = self.db.count_pixiv_downloads(self.config.pixiv_limit_hours)
            if count >= self.config.pixiv_limit_count:
                return SubmitResult(
                    429,
                    {
                        "status": "rate_limit",
                        "message": "Pixiv下载频率达到上限（5小时100次）",
                        "limit": self.config.pixiv_limit_count,
                        "used": count,
                        "reset_in": "5小时",
                    },
                )
            self.db.record_pixiv_download(normalized_url)
        media_files, metadata = await self.downloader.download_media(normalized_url)
        if not media_files:
            return SubmitResult(400, {"status": "download_failed", "message": "媒体下载失败，请检查链接是否有效"})
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else 0
        submission_id = self.db.create_submission(
            user_id=admin_id,
            username="api_submit",
            url=normalized_url,
            status="approved",
            media_paths=media_files,
            metadata=metadata,
            now=self.clock.now(),
        )
        await self.publish_submission(submission_id, admin_id)
        for admin in self.config.admin_ids:
            try:
                await self.bot.send_message(admin, messages.api_notify(submission_id, normalized_url, metadata))
            except Exception as exc:
                logging.warning("无法通知管理员 %s: %s", admin, exc)
        return SubmitResult(
            200,
            {
                "status": "success",
                "message": "投稿成功并已发布到频道",
                "submission_id": submission_id,
                "url": normalized_url,
                "author": metadata.get("author_name", ""),
                "title": metadata.get("title", ""),
            },
        )

    async def submit_url_as_admin(self, url: str, username: str = "bookmark_monitor") -> tuple[str, int | None]:
        normalized_url = normalize_url(url)
        existing = self.db.find_by_url(normalized_url)
        if existing:
            return "duplicate", existing.id
        media_files, metadata = await self.downloader.download_media(normalized_url)
        if not media_files:
            return "download_failed", None
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else 0
        submission_id = self.db.create_submission(
            user_id=admin_id,
            username=username,
            url=normalized_url,
            status="approved",
            media_paths=media_files,
            metadata=metadata,
            now=self.clock.now(),
        )
        await self.publish_submission(submission_id, admin_id)
        for admin in self.config.admin_ids:
            try:
                await self.bot.send_message(admin, messages.api_notify(submission_id, normalized_url, metadata))
            except Exception as exc:
                logging.warning("无法通知管理员 %s: %s", admin, exc)
        return "submitted", submission_id


def collect_message_text(message: Any) -> str:
    text = ""
    if getattr(message, "text", None):
        text += message.text + "\n"
    if getattr(message, "caption", None):
        text += message.caption + "\n"
    origin = getattr(message, "forward_origin", None)
    if origin and hasattr(origin, "message"):
        if hasattr(origin.message, "text") and origin.message.text:
            text += origin.message.text + "\n"
        if hasattr(origin.message, "caption") and origin.message.caption:
            text += origin.message.caption + "\n"
    if getattr(message, "entities", None):
        for entity in message.entities:
            if getattr(entity, "type", None) == "url" and getattr(entity, "url", None):
                text += entity.url + "\n"
    if origin and hasattr(origin, "message") and hasattr(origin.message, "entities"):
        for entity in origin.message.entities:
            if getattr(entity, "type", None) == "url" and getattr(entity, "url", None):
                text += entity.url + "\n"
    return text


def build_media_group(media_files: list[str], caption: str, parse_mode: str | None = None) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for i, file_path in enumerate(media_files):
        kind = media_kind(file_path)
        item: dict[str, Any] = {"type": kind, "media": file_path, "caption": caption if i == 0 else ""}
        if i == 0 and parse_mode is not None:
            item["parse_mode"] = parse_mode
        media.append(item)
    return media


def pixiv_work_id(url: str) -> str:
    import re

    match = re.search(r"pixiv\.net/(?:en/)?artworks/(\d+)", url)
    return match.group(1) if match else ""
