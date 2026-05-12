from __future__ import annotations

import logging
import os
import json
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import messages
from .config import BotConfig
from .db import Database, Submission
from .downloader import Downloader
from .media import MAX_PHOTO_SIZE, MAX_VIDEO_SIZE, media_kind
from .safety import ImageSafetyDetector, SafetyDecision, classify_safety
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

    async def delete_message(self, chat_id: int | str, message_id: int) -> Any:
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
    def __init__(
        self,
        config: BotConfig,
        db: Database,
        downloader: Downloader,
        bot: BotClient,
        clock: Clock | None = None,
        safety_detector: ImageSafetyDetector | None = None,
    ):
        self.config = config
        self.db = db
        self.downloader = downloader
        self.bot = bot
        self.clock = clock or SystemClock()
        self.safety_detector = safety_detector
        self.bookmark_monitor: BookmarkActivator | None = None
        self._last_error_notifications: dict[str, datetime] = {}

    async def start(self, update: Any, context: Any = None) -> None:
        await update.message.reply_text(messages.START_TEXT)

    async def help_command(self, update: Any, context: Any = None) -> None:
        await update.message.reply_text(messages.HELP_TEXT)

    async def admin_help_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        await update.message.reply_text(messages.ADMIN_HELP_TEXT)

    async def original_command(self, update: Any, context: Any = None) -> None:
        await update.message.reply_text(messages.ORIGINAL_HELP_TEXT)

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

    async def nsfw_threshold_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        args = getattr(context, "args", [])
        if not args:
            await update.message.reply_text(
                messages.nsfw_threshold_status(self.config.nsfw_low_threshold, self.config.nsfw_high_threshold)
            )
            return
        if len(args) != 2:
            await update.message.reply_text(messages.nsfw_threshold_usage())
            return
        try:
            low = float(args[0])
            high = float(args[1])
        except ValueError:
            await update.message.reply_text(messages.nsfw_threshold_usage())
            return
        if not 0 <= low < high <= 1:
            await update.message.reply_text(messages.nsfw_threshold_usage())
            return
        object.__setattr__(self.config, "nsfw_low_threshold", low)
        object.__setattr__(self.config, "nsfw_high_threshold", high)
        self.db.set_config("NSFW_LOW_THRESHOLD", f"{low:.2f}")
        self.db.set_config("NSFW_HIGH_THRESHOLD", f"{high:.2f}")
        await update.message.reply_text(messages.nsfw_threshold_updated(low, high))

    async def bookmark_watch_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.BOOKMARK_WATCH_FORBIDDEN)
            return
        if not self.bookmark_monitor or not self.bookmark_monitor.is_configured():
            await update.message.reply_text(messages.BOOKMARK_WATCH_UNAVAILABLE)
            return
        self.bookmark_monitor.activate()
        await update.message.reply_text(messages.BOOKMARK_WATCH_STARTED)
        await self.notify_bookmark_watch_started(exclude_user_id=update.effective_user.id)

    async def find_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        target = command_target(context)
        if not target:
            await update.message.reply_text(messages.admin_lookup_usage("find"))
            return
        submission = self.resolve_submission_target(target)
        if not submission:
            await update.message.reply_text(messages.submission_not_found(target))
            return
        summary = messages.submission_summary(submission, submission_metadata(submission))
        preview = first_existing_photo(submission.media_paths)
        if preview:
            await self.bot.send_photo(chat_id=update.effective_user.id, photo=preview, caption=summary)
        else:
            await update.message.reply_text(summary)

    async def stats_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        await update.message.reply_text(messages.stats_summary(self.db.submission_stats()))

    async def delete_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        target = command_target(context)
        if not target:
            await update.message.reply_text(messages.admin_lookup_usage("delete"))
            return
        submission = self.resolve_submission_target(target)
        if not submission:
            await update.message.reply_text(messages.submission_not_found(target))
            return
        await self.admin_delete_submission(submission, update.effective_user.id)
        await update.message.reply_text(messages.delete_success(submission.id))

    async def retry_command(self, update: Any, context: Any = None) -> None:
        if update.effective_user.id not in self.config.admin_ids:
            await update.message.reply_text(messages.PERMISSION_DENIED)
            return
        target = command_target(context)
        if not target:
            await update.message.reply_text(messages.admin_lookup_usage("retry"))
            return
        submission = self.resolve_submission_target(target)
        if not submission:
            await update.message.reply_text(messages.submission_not_found(target))
            return
        await update.message.reply_text(messages.retry_started(submission.id, submission.url))
        result = await self.retry_submission(submission, update.effective_user.id)
        if result == "download_failed":
            await update.message.reply_text(messages.retry_failed(submission.id, submission.url))
        elif result == "pending":
            await update.message.reply_text(messages.retry_pending(submission.id))
        else:
            await update.message.reply_text(messages.retry_published(submission.id))

    def activate_bookmark_watch(self) -> SubmitResult:
        if not self.bookmark_monitor or not self.bookmark_monitor.is_configured():
            return SubmitResult(503, {"status": "unavailable", "message": "Twitter bookmark monitor is not configured"})
        self.bookmark_monitor.activate()
        return SubmitResult(200, {"status": "started", "message": "Twitter bookmark monitor started"})

    async def notify_bookmark_watch_started(self, exclude_user_id: int | None = None) -> None:
        await self._notify_admins(messages.BOOKMARK_WATCH_STARTED, exclude_user_id=exclude_user_id)

    async def notify_bookmark_watch_stopped(self, reason: str) -> None:
        if reason == "credits_depleted":
            text = messages.BOOKMARK_WATCH_STOPPED_CREDITS
        else:
            text = messages.BOOKMARK_WATCH_STOPPED_IDLE
        await self._notify_admins(text)

    async def _notify_admins(self, text: str, exclude_user_id: int | None = None) -> None:
        for admin_id in self.config.admin_ids:
            if exclude_user_id is not None and admin_id == exclude_user_id:
                continue
            try:
                await self.bot.send_message(admin_id, text)
            except Exception as exc:
                logging.exception("Failed to notify admin %s: %s", admin_id, exc)

    async def notify_admin_error(
        self,
        source: str,
        exc: BaseException | None = None,
        *,
        detail: str = "",
        throttle_key: str | None = None,
        throttle_seconds: int = 300,
    ) -> None:
        key = throttle_key or source
        now = self.clock.now()
        last_sent = self._last_error_notifications.get(key)
        if last_sent and now - last_sent < timedelta(seconds=throttle_seconds):
            return
        self._last_error_notifications[key] = now
        error_detail = detail.strip()
        if exc is not None:
            exception_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            error_detail = f"{error_detail}\n{exception_text}".strip()
        if not error_detail:
            error_detail = "没有更多错误信息。"
        await self._notify_admins(messages.admin_error(source, error_detail[:1500]))

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
                await self.return_original_submission(update.message, existing)
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
            safety_decision = await self.classify_downloaded_content(url, media_files, metadata)
            status = "pending" if not is_admin or safety_decision.rating == "uncertain" else "approved"
            try:
                submission_id = self.db.create_submission(
                    user_id=user_id,
                    username=username,
                    url=url,
                    status=status,
                    media_paths=media_files,
                    metadata=metadata,
                    now=self.clock.now(),
                )
            except Exception:
                existing = self.db.find_by_url(url)
                if existing:
                    await self.return_original_submission(update.message, existing)
                else:
                    await update.message.reply_text(messages.duplicate_insert_failed())
                continue

            logging.info("创建投稿 #%s: %s (用户: %s(%s), 状态: %s)", submission_id, url, username, user_id, status)
            if status == "approved":
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
        source_channel = self.forward_source_channel(origin.chat)
        if source_channel:
            submission = self.db.find_by_message_id(origin.message_id)
            if not submission:
                return False
            allow_relocate = update.effective_user.id in self.config.admin_ids
            await self.return_original_submission(
                update.message,
                submission,
                relocate_source=source_channel if allow_relocate else None,
            )
            return True
        return False

    def forward_source_channel(self, chat: Any) -> str | None:
        for channel_id in self.publish_channel_ids():
            if is_forward_from_channel(chat, channel_id):
                return channel_id
        return None

    def publish_channel_ids(self) -> list[str]:
        channel_ids = [self.config.publish_channel_id]
        if self.config.r18_routing_enabled and self.config.r18_channel_id:
            channel_ids.append(self.config.r18_channel_id)
        return channel_ids

    async def return_original_submission(
        self,
        message: Any,
        submission: Submission,
        relocate_source: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if relocate_source:
            source_key = "r18" if relocate_source == self.config.r18_channel_id else "safe"
            kwargs["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {
                            "text": messages.MOVE_TO_R18_BUTTON,
                            "callback_data": f"move_r18:{submission.id}:{source_key}",
                        },
                        {
                            "text": messages.MOVE_TO_SAFE_BUTTON,
                            "callback_data": f"move_safe:{submission.id}:{source_key}",
                        },
                        {
                            "text": messages.DELETE_POST_BUTTON,
                            "callback_data": f"delete_post:{submission.id}:{source_key}",
                        },
                    ]
                ]
            }
        await message.reply_text(messages.original_found(submission.url), **kwargs)
        for media_path in submission.media_paths:
            if os.path.exists(media_path):
                await message.reply_document(document=media_path, filename=os.path.basename(media_path))
            else:
                logging.warning("原图不存在: %s", media_path)

    async def send_to_review(self, submission_id: int, url: str, username: str, media_files: list[str], metadata: dict) -> Any | None:
        buttons = [
            {"text": messages.APPROVE_BUTTON, "callback_data": f"approve:{submission_id}"},
            {"text": messages.REJECT_BUTTON, "callback_data": f"reject:{submission_id}"},
        ]
        if metadata.get("safety_rating") == "uncertain" and self.config.r18_routing_enabled and self.config.r18_channel_id:
            buttons.insert(1, {"text": "🔞 R-18", "callback_data": f"approve_r18:{submission_id}"})
        reply_markup = {
            "inline_keyboard": [
                buttons
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
        target_channel = self.publish_channel_for_submission(submission)
        caption = messages.publish_caption(
            submission.url,
            author_name=submission.author_name,
            text=submission.text,
            canonical_url=submission.canonical_url,
        )
        channel_message_id: int | None = None
        channel_message_ids: list[int] = []
        media_files = submission.media_paths
        if not media_files:
            logging.error("发布失败: 投稿 #%s 没有媒体", submission_id)
            return None
        if len(media_files) <= 10:
            if len(media_files) == 1:
                sent = await self._send_single_media(
                    target_channel,
                    media_files[0],
                    caption,
                    parse_mode="HTML",
                )
                channel_message_id = getattr(sent, "message_id", None)
                if channel_message_id:
                    channel_message_ids.append(int(channel_message_id))
            else:
                sent_media = await self.bot.send_media_group(
                    chat_id=target_channel,
                    media=build_media_group(media_files[:10], caption, parse_mode="HTML"),
                )
                if sent_media:
                    channel_message_id = getattr(sent_media[0], "message_id", None)
                    channel_message_ids.extend(
                        int(message_id)
                        for message_id in (getattr(message, "message_id", None) for message in sent_media)
                        if message_id
                    )
        else:
            reply_markup = {"inline_keyboard": [[{"text": "🖼️ 打开完整画廊", "url": submission.url}]]}
            sent = await self.bot.send_photo(
                chat_id=target_channel,
                photo=media_files[0],
                caption=f"{caption}\n\n📸 共 {len(media_files)} 张图",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            channel_message_id = getattr(sent, "message_id", None)
            if channel_message_id:
                channel_message_ids.append(int(channel_message_id))
            total_groups = (len(media_files) + 9) // 10
            work_id = pixiv_work_id(submission.url)
            for group_idx in range(total_groups):
                group_files = media_files[group_idx * 10 : group_idx * 10 + 10]
                group_caption = f"Full set {group_idx + 1}/{total_groups}"
                if work_id:
                    group_caption += f" — Pixiv {work_id}"
                sent_media = await self.bot.send_media_group(
                    chat_id=target_channel,
                    media=build_media_group(group_files, group_caption),
                )
                channel_message_ids.extend(
                    int(message_id)
                    for message_id in (getattr(message, "message_id", None) for message in sent_media or [])
                    if message_id
                )
        if channel_message_id:
            self.db.update_message_id(submission_id, int(channel_message_id), self.clock.now())
        if channel_message_ids:
            metadata = submission_metadata(submission)
            metadata["channel_message_ids"] = channel_message_ids
            metadata["channel_id"] = str(target_channel)
            self.db.update_metadata(submission_id, metadata, self.clock.now())
            published_submission = self.db.get_submission(submission_id)
            if published_submission:
                await self.notify_moderation_submission(published_submission, target_channel)
        logging.info("发布成功: 投稿 #%s 已发送到频道 %s", submission_id, target_channel)
        return channel_message_id

    async def notify_moderation_submission(self, submission: Submission, target_channel: int | str) -> None:
        if not self.config.r18_routing_enabled:
            return
        metadata = submission_metadata(submission)
        caption = messages.moderation_caption(submission.id, submission.url, metadata)
        reply_markup = moderation_reply_markup(
            submission.id,
            "r18" if str(target_channel) == self.config.r18_channel_id else "safe",
        )
        preview = first_existing_photo(submission.media_paths)
        for admin_id in self.config.admin_ids:
            try:
                if preview:
                    await self.bot.send_photo(
                        chat_id=admin_id,
                        photo=preview,
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                else:
                    await self.bot.send_message(admin_id, caption, reply_markup=reply_markup)
            except Exception as exc:
                logging.warning("无法给管理员%s发送投稿检测消息: %s", admin_id, exc)

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
        parts = query.data.split(":")
        action, submission_id_text = parts[0], parts[1]
        submission_id = int(submission_id_text)
        submission = self.db.get_submission(submission_id)
        if not submission:
            await query.edit_message_caption(caption=messages.callback_not_found(existing_caption))
            return
        if action in {"move_r18", "move_safe"}:
            source_key = parts[2] if len(parts) > 2 else ""
            await self.move_published_submission(submission, action, source_key, user_id)
            await edit_callback_message(query, messages.callback_approved(existing_caption, username))
            return
        if action == "delete_post":
            source_key = parts[2] if len(parts) > 2 else ""
            await self.delete_published_submission(submission, source_key, user_id)
            await edit_callback_message(query, messages.callback_deleted(existing_caption, username))
            return
        if submission.status != "pending":
            await query.edit_message_caption(caption=messages.callback_already_done(existing_caption, submission.status))
            return
        if action == "approve_r18":
            metadata = submission_metadata(submission)
            metadata["safety_rating"] = "r18"
            metadata["safety_reason"] = metadata.get("safety_reason") or "admin selected r18"
            self.db.update_metadata(submission_id, metadata, self.clock.now())
            await self.publish_submission(submission_id, user_id)
            await query.edit_message_caption(caption=messages.callback_approved(existing_caption, username))
            try:
                await self.bot.send_message(submission.user_id, messages.submitter_approved(submission.url))
            except Exception as exc:
                logging.warning("无法通知投稿者 %s: %s", submission.user_id, exc)
        elif action == "approve":
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
            return api_duplicate_result(existing)
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
        existing = self.find_existing_submission_from_metadata(normalized_url, metadata)
        if existing:
            return api_duplicate_result(existing)
        safety_decision = await self.classify_downloaded_content(normalized_url, media_files, metadata)
        status = "pending" if safety_decision.rating == "uncertain" else "approved"
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else 0
        submission_id = self.db.create_submission(
            user_id=admin_id,
            username="api_submit",
            url=normalized_url,
            status=status,
            media_paths=media_files,
            metadata=metadata,
            now=self.clock.now(),
        )
        if status == "pending":
            await self.send_to_review(submission_id, normalized_url, "api_submit", media_files, metadata)
            return SubmitResult(
                202,
                {
                    "status": "pending_review",
                    "message": "投稿需要管理员审核",
                    "submission_id": submission_id,
                    "url": normalized_url,
                    "safety_reason": metadata.get("safety_reason", ""),
                    "safety_score": metadata.get("safety_score"),
                },
            )
        await self.publish_submission(submission_id, admin_id)
        await self.notify_api_submission(submission_id, normalized_url, metadata)
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
        existing = self.find_existing_submission_from_metadata(normalized_url, metadata)
        if existing:
            return "duplicate", existing.id
        safety_decision = await self.classify_downloaded_content(normalized_url, media_files, metadata)
        status = "pending" if safety_decision.rating == "uncertain" else "approved"
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else 0
        submission_id = self.db.create_submission(
            user_id=admin_id,
            username=username,
            url=normalized_url,
            status=status,
            media_paths=media_files,
            metadata=metadata,
            now=self.clock.now(),
        )
        if status == "pending":
            await self.send_to_review(submission_id, normalized_url, username, media_files, metadata)
            return "pending_review", submission_id
        await self.publish_submission(submission_id, admin_id)
        await self.notify_api_submission(submission_id, normalized_url, metadata)
        return "submitted", submission_id

    async def notify_api_submission(self, submission_id: int, normalized_url: str, metadata: dict[str, Any]) -> None:
        for admin in self.config.admin_ids:
            try:
                await self.bot.send_message(admin, messages.api_notify(submission_id, normalized_url, metadata))
            except Exception as exc:
                logging.warning("无法通知管理员 %s: %s", admin, exc)

    async def move_published_submission(
        self,
        submission: Submission,
        action: str,
        source_key: str,
        reviewer_id: int,
    ) -> None:
        if not submission.message_id:
            raise RuntimeError(f"Submission #{submission.id} has no channel message id")
        source_channel = self.source_channel_from_key(source_key) or self.publish_channel_for_submission(submission)
        metadata = submission_metadata(submission)
        message_ids = published_message_ids(submission)
        if action == "move_r18":
            metadata["safety_rating"] = "r18"
            metadata["safety_reason"] = "admin moved to r18 channel"
            log_action = "move_r18"
        else:
            metadata["safety_rating"] = "safe"
            metadata["safety_reason"] = "admin moved to safe channel"
            log_action = "move_safe"
        metadata.pop("channel_message_ids", None)
        metadata.pop("channel_id", None)
        for message_id in message_ids:
            try:
                await self.bot.delete_message(source_channel, message_id)
            except Exception as exc:
                logging.warning("删除频道消息失败: channel=%s message_id=%s error=%s", source_channel, message_id, exc)
        self.db.update_metadata(submission.id, metadata, self.clock.now())
        await self.publish_submission(submission.id, reviewer_id)
        self.db.log_moderation_action(
            submission_id=submission.id,
            action=log_action,
            admin_id=reviewer_id,
            detail=submission.url,
            now=self.clock.now(),
        )

    async def delete_published_submission(
        self,
        submission: Submission,
        source_key: str,
        reviewer_id: int,
    ) -> None:
        if not submission.message_id:
            raise RuntimeError(f"Submission #{submission.id} has no channel message id")
        source_channel = self.source_channel_from_key(source_key) or self.publish_channel_for_submission(submission)
        for message_id in published_message_ids(submission):
            try:
                await self.bot.delete_message(source_channel, message_id)
            except Exception as exc:
                logging.warning("删除频道消息失败: channel=%s message_id=%s error=%s", source_channel, message_id, exc)
        metadata = submission_metadata(submission)
        metadata["deleted_by"] = reviewer_id
        metadata["deleted_at"] = self.clock.now().isoformat()
        self.db.update_metadata(submission.id, metadata, self.clock.now())
        self.db.update_status(submission.id, "deleted", reviewer_id, self.clock.now())
        self.db.log_moderation_action(
            submission_id=submission.id,
            action="delete_callback",
            admin_id=reviewer_id,
            detail=submission.url,
            now=self.clock.now(),
        )

    async def admin_delete_submission(self, submission: Submission, admin_id: int) -> None:
        if submission.message_id:
            source_channel = str(submission_metadata(submission).get("channel_id") or self.publish_channel_for_submission(submission))
            for message_id in published_message_ids(submission):
                try:
                    await self.bot.delete_message(source_channel, message_id)
                except Exception as exc:
                    logging.warning("删除频道消息失败: channel=%s message_id=%s error=%s", source_channel, message_id, exc)
        metadata = submission_metadata(submission)
        metadata["deleted_by"] = admin_id
        metadata["deleted_at"] = self.clock.now().isoformat()
        self.db.update_metadata(submission.id, metadata, self.clock.now())
        self.db.update_status(submission.id, "deleted", admin_id, self.clock.now())
        self.db.log_moderation_action(
            submission_id=submission.id,
            action="delete_command",
            admin_id=admin_id,
            detail=submission.url,
            now=self.clock.now(),
        )

    async def retry_submission(self, submission: Submission, admin_id: int) -> str:
        if submission.message_id and submission.status == "approved":
            await self.admin_delete_submission(submission, admin_id)
        media_files, metadata = await self.downloader.download_media(submission.url)
        if not media_files:
            self.db.log_moderation_action(
                submission_id=submission.id,
                action="retry_failed",
                admin_id=admin_id,
                detail=submission.url,
                now=self.clock.now(),
            )
            return "download_failed"
        safety_decision = await self.classify_downloaded_content(submission.url, media_files, metadata)
        status = "pending" if safety_decision.rating == "uncertain" else "approved"
        self.db.update_submission_content(
            submission.id,
            status=status,
            media_paths=media_files,
            metadata=metadata,
            reviewer_id=admin_id,
            now=self.clock.now(),
        )
        if status == "pending":
            review_message = await self.send_to_review(submission.id, submission.url, submission.username or "retry", media_files, metadata)
            if review_message:
                self.db.update_message_id(submission.id, int(getattr(review_message, "message_id", 0)), self.clock.now())
            result = "pending"
        else:
            await self.publish_submission(submission.id, admin_id)
            result = "published"
        self.db.log_moderation_action(
            submission_id=submission.id,
            action=f"retry_{result}",
            admin_id=admin_id,
            detail=submission.url,
            now=self.clock.now(),
        )
        return result

    def resolve_submission_target(self, target: str) -> Submission | None:
        stripped = target.strip()
        if stripped.startswith("#"):
            stripped = stripped[1:]
        if stripped.isdigit():
            return self.db.get_submission(int(stripped))
        urls = extract_urls_from_text(stripped)
        url = urls[0] if urls else stripped
        return self.db.find_by_url_any_status(url, include_deleted=True)

    def source_channel_from_key(self, source_key: str) -> str | None:
        if source_key == "r18" and self.config.r18_channel_id:
            return self.config.r18_channel_id
        if source_key == "safe":
            return self.config.publish_channel_id
        return None

    def find_existing_submission_from_metadata(self, url: str, metadata: dict[str, Any]) -> Submission | None:
        for candidate in (metadata.get("canonical_url"), url):
            if not candidate:
                continue
            existing = self.db.find_by_url(str(candidate))
            if existing:
                return existing
        return None

    async def classify_downloaded_content(self, url: str, media_files: list[str], metadata: dict[str, Any]) -> SafetyDecision:
        decision = await classify_safety(
            config=self.config,
            url=url,
            media_paths=media_files,
            metadata=metadata,
            detector=self.safety_detector,
        )
        metadata["safety_rating"] = decision.rating
        metadata["safety_reason"] = decision.reason
        metadata["safety_checked_count"] = decision.checked_count
        if decision.score is not None:
            metadata["safety_score"] = decision.score
        if decision.class_name is not None:
            metadata["safety_class"] = decision.class_name
        return decision

    def publish_channel_for_submission(self, submission: Submission) -> str:
        if not self.config.r18_routing_enabled or not self.config.r18_channel_id:
            return self.config.publish_channel_id
        metadata = submission_metadata(submission)
        if metadata.get("safety_rating") == "r18":
            return self.config.r18_channel_id
        return self.config.publish_channel_id


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


def command_target(context: Any) -> str:
    args = getattr(context, "args", []) if context is not None else []
    return " ".join(str(arg) for arg in args).strip()


def build_media_group(media_files: list[str], caption: str, parse_mode: str | None = None) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for i, file_path in enumerate(media_files):
        kind = media_kind(file_path)
        item: dict[str, Any] = {"type": kind, "media": file_path, "caption": caption if i == 0 else ""}
        if i == 0 and parse_mode is not None:
            item["parse_mode"] = parse_mode
        media.append(item)
    return media


def moderation_reply_markup(submission_id: int, source_key: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": messages.MOVE_TO_R18_BUTTON,
                    "callback_data": f"move_r18:{submission_id}:{source_key}",
                },
                {
                    "text": messages.MOVE_TO_SAFE_BUTTON,
                    "callback_data": f"move_safe:{submission_id}:{source_key}",
                },
                {
                    "text": messages.DELETE_POST_BUTTON,
                    "callback_data": f"delete_post:{submission_id}:{source_key}",
                },
            ]
        ]
    }


def first_existing_photo(media_files: list[str]) -> str | None:
    for media_file in media_files:
        if media_kind(media_file) == "photo" and os.path.exists(media_file):
            return media_file
    return None


def api_duplicate_result(existing: Submission) -> SubmitResult:
    return SubmitResult(
        409,
        {
            "status": "already_exists",
            "message": f"链接已投稿，ID #{existing.id}，状态：{existing.status}",
            "submission_id": existing.id,
            "current_status": existing.status,
        },
    )


async def edit_callback_message(query: Any, text: str) -> None:
    message = getattr(query, "message", None)
    if getattr(message, "caption", None) is not None and hasattr(query, "edit_message_caption"):
        await query.edit_message_caption(caption=text)
        return
    if hasattr(query, "edit_message_text"):
        await query.edit_message_text(text=text)
        return
    await query.edit_message_caption(caption=text)


def submission_metadata(submission: Submission) -> dict[str, Any]:
    try:
        metadata = json.loads(submission.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault("canonical_url", submission.canonical_url or submission.url)
    metadata.setdefault("author_name", submission.author_name or "")
    metadata.setdefault("title", submission.title or "")
    metadata.setdefault("text", submission.text or "")
    return metadata


def published_message_ids(submission: Submission) -> list[int]:
    metadata = submission_metadata(submission)
    stored_ids = metadata.get("channel_message_ids")
    if isinstance(stored_ids, list):
        ids = parse_message_ids(stored_ids)
        if ids:
            return ids
    if not submission.message_id:
        return []
    if 1 < len(submission.media_paths) <= 10:
        return [int(submission.message_id) + index for index in range(len(submission.media_paths))]
    return [int(submission.message_id)]


def parse_message_ids(values: list[Any]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id in seen:
            continue
        seen.add(message_id)
        ids.append(message_id)
    return ids


def is_forward_from_channel(chat: Any, channel_id: str) -> bool:
    if channel_id.startswith("@"):
        forward_chat_username = getattr(chat, "username", "")
        return f"@{forward_chat_username}" == channel_id
    return str(getattr(chat, "id", "")) == channel_id


def pixiv_work_id(url: str) -> str:
    import re

    match = re.search(r"pixiv\.net/(?:en/)?artworks/(\d+)", url)
    return match.group(1) if match else ""
