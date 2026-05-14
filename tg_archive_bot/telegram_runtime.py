from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .config import BotConfig
from .db import Database
from .downloader import GalleryDownloader
from .http_api import run_http_api
from .safety import create_image_safety_detector
from .service import ArchiveBot
from .twitter_bookmarks import TwitterBookmarkMonitor, XBookmarksClient, XOAuth2TokenRefresher
from .web_bookmarks import BookmarkMonitorGroup, DanbooruFavoritesClient, PixivBookmarksClient, PoipikuBookmarksClient


class TelegramBotClient:
    def __init__(self, bot: Any):
        self.bot = bot

    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any) -> Any:
        return await self.bot.send_message(chat_id=chat_id, text=text, **convert_reply_markup(kwargs))

    async def send_photo(self, chat_id: int | str, photo: Any, **kwargs: Any) -> Any:
        return await self.bot.send_photo(chat_id=chat_id, photo=open_if_path(photo), **convert_reply_markup(kwargs))

    async def send_video(self, chat_id: int | str, video: Any, **kwargs: Any) -> Any:
        return await self.bot.send_video(chat_id=chat_id, video=open_if_path(video), **convert_reply_markup(kwargs))

    async def send_document(self, chat_id: int | str, document: Any, **kwargs: Any) -> Any:
        return await self.bot.send_document(chat_id=chat_id, document=open_if_path(document), **convert_reply_markup(kwargs))

    async def send_media_group(self, chat_id: int | str, media: list[dict[str, Any]]) -> Any:
        from telegram import InputMediaDocument, InputMediaPhoto, InputMediaVideo

        opened = []
        tg_media = []
        try:
            for item in media:
                fh = open_if_path(item["media"])
                opened.append(fh)
                kwargs = {"caption": item.get("caption") or None}
                if item.get("parse_mode"):
                    kwargs["parse_mode"] = item["parse_mode"]
                if item["type"] == "photo":
                    tg_media.append(InputMediaPhoto(fh, **kwargs))
                elif item["type"] == "video":
                    tg_media.append(InputMediaVideo(fh, **kwargs))
                else:
                    tg_media.append(InputMediaDocument(fh, **kwargs))
            return await self.bot.send_media_group(
                chat_id=chat_id,
                media=tg_media,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )
        finally:
            for fh in opened:
                if hasattr(fh, "close"):
                    fh.close()

    async def delete_message(self, chat_id: int | str, message_id: int) -> Any:
        return await self.bot.delete_message(chat_id=chat_id, message_id=message_id)

    async def edit_message_text(self, chat_id: int | str, message_id: int, text: str, **kwargs: Any) -> Any:
        return await self.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            **convert_reply_markup(kwargs),
        )

    async def edit_message_caption(self, chat_id: int | str, message_id: int, caption: str, **kwargs: Any) -> Any:
        return await self.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            **convert_reply_markup(kwargs),
        )


def open_if_path(value: Any) -> Any:
    if isinstance(value, (str, Path)) and Path(value).exists():
        return open(value, "rb")
    return value


def convert_reply_markup(kwargs: dict[str, Any]) -> dict[str, Any]:
    reply_markup = kwargs.get("reply_markup")
    if isinstance(reply_markup, dict):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = []
        for row in reply_markup.get("inline_keyboard", []):
            keyboard.append([InlineKeyboardButton(**button) for button in row])
        kwargs = dict(kwargs)
        kwargs["reply_markup"] = InlineKeyboardMarkup(keyboard)
    return kwargs


def setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(log_dir / "bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_bookmark_token_refresher(config: BotConfig, env_path: Path) -> XOAuth2TokenRefresher | None:
    if not config.twitter_bookmarks_refresh_token or not config.twitter_oauth_client_id:
        return None
    return XOAuth2TokenRefresher(
        token_url=config.twitter_oauth_token_url,
        client_id=config.twitter_oauth_client_id,
        client_secret=config.twitter_oauth_client_secret,
        refresh_token=config.twitter_bookmarks_refresh_token,
        env_path=env_path,
    )


async def error_handler(update: object, context: Any, archive_bot: ArchiveBot | None = None) -> None:
    logging.error("Exception while handling an update:", exc_info=context.error)
    if archive_bot is not None:
        await archive_bot.notify_admin_error(
            "Telegram update handler failed",
            context.error,
            detail=f"update={update!r}",
            throttle_key=f"telegram_handler:{type(context.error).__name__}:{str(context.error)[:120]}",
        )


async def main() -> None:
    env_path = Path(".env")
    load_dotenv(env_path)
    setup_logging()
    config = BotConfig.from_env(Path(__file__).resolve().parents[1])
    config.validate_runtime()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.media_dir.mkdir(parents=True, exist_ok=True)
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    db = Database(config.database_path)
    db.init()
    apply_runtime_config_from_db(config, db)
    application = ApplicationBuilder().token(config.bot_token).build()
    archive_bot = ArchiveBot(
        config,
        db,
        GalleryDownloader(
            config.media_dir,
            cookies_path=config.gallery_dl_cookies,
            danbooru_username=config.danbooru_username,
            danbooru_password=config.danbooru_password,
        ),
        TelegramBotClient(application.bot),
        safety_detector=create_image_safety_detector(config),
    )

    async def notify_error_handler(update: object, context: Any) -> None:
        await error_handler(update, context, archive_bot)

    application.add_error_handler(notify_error_handler)
    application.add_handler(CommandHandler("start", archive_bot.start))
    application.add_handler(CommandHandler("help", archive_bot.help_command))
    application.add_handler(CommandHandler("admin_help", archive_bot.admin_help_command))
    application.add_handler(CommandHandler("original", archive_bot.original_command))
    application.add_handler(CommandHandler("config", archive_bot.config_command))
    application.add_handler(CommandHandler("set", archive_bot.set_command))
    application.add_handler(CommandHandler("pending", archive_bot.pending_command))
    application.add_handler(CommandHandler("find", archive_bot.find_command))
    application.add_handler(CommandHandler("select", archive_bot.select_command))
    application.add_handler(CommandHandler("retry", archive_bot.retry_command))
    application.add_handler(CommandHandler("delete", archive_bot.delete_command))
    application.add_handler(CommandHandler("stats", archive_bot.stats_command))
    application.add_handler(CommandHandler("pixiv_status", archive_bot.pixiv_status_command))
    application.add_handler(CommandHandler("rating_threshold", archive_bot.nsfw_threshold_command))
    application.add_handler(CommandHandler("nsfw_threshold", archive_bot.nsfw_threshold_command))
    application.add_handler(CommandHandler("bookmark_watch", archive_bot.bookmark_watch_command))
    application.add_handler(MessageHandler(~filters.COMMAND, archive_bot.handle_message))
    application.add_handler(CallbackQueryHandler(archive_bot.handle_callback))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    runner = None
    bookmark_tasks: list[asyncio.Task] = []
    bookmark_monitors: list[TwitterBookmarkMonitor] = []
    if config.http_api_enabled:
        runner = await run_http_api(archive_bot, config.http_api_host, config.http_api_port)
        logging.info("HTTP API listening on %s:%s", config.http_api_host, config.http_api_port)
    if config.twitter_bookmarks_user_id and config.twitter_bookmarks_access_token:
        bookmark_client = XBookmarksClient(
            api_base=config.twitter_bookmarks_api_base,
            user_id=config.twitter_bookmarks_user_id,
            access_token=config.twitter_bookmarks_access_token,
            max_results=config.twitter_bookmarks_max_results,
            token_refresher=create_bookmark_token_refresher(config, env_path),
        )
        bookmark_monitor = TwitterBookmarkMonitor(
            config=config,
            db=db,
            archive_bot=archive_bot,
            client=bookmark_client,
        )
        bookmark_monitors.append(bookmark_monitor)
    if config.pixiv_bookmarks_user_id and config.pixiv_bookmarks_cookies:
        pixiv_client = PixivBookmarksClient(
            user_id=config.pixiv_bookmarks_user_id,
            cookies_path=config.pixiv_bookmarks_cookies,
            max_results=config.web_bookmarks_max_results,
        )
        bookmark_monitors.append(
            TwitterBookmarkMonitor(
                config=config,
                db=db,
                archive_bot=archive_bot,
                client=pixiv_client,
                provider="pixiv",
                label="Pixiv",
                configured=lambda: bool(
                    config.pixiv_bookmarks_user_id
                    and config.pixiv_bookmarks_cookies
                    and config.pixiv_bookmarks_cookies.exists()
                ),
            )
        )
    if config.poipiku_bookmarks_cookies:
        poipiku_client = PoipikuBookmarksClient(
            cookies_path=config.poipiku_bookmarks_cookies,
            max_results=config.web_bookmarks_max_results,
        )
        bookmark_monitors.append(
            TwitterBookmarkMonitor(
                config=config,
                db=db,
                archive_bot=archive_bot,
                client=poipiku_client,
                provider="poipiku",
                label="Poipiku",
                configured=lambda: bool(
                    config.poipiku_bookmarks_cookies
                    and config.poipiku_bookmarks_cookies.exists()
                ),
            )
        )
    if config.danbooru_username and config.danbooru_password:
        danbooru_client = DanbooruFavoritesClient(
            username=config.danbooru_username,
            password=config.danbooru_password,
            max_results=config.web_bookmarks_max_results,
        )
        bookmark_monitors.append(
            TwitterBookmarkMonitor(
                config=config,
                db=db,
                archive_bot=archive_bot,
                client=danbooru_client,
                provider="danbooru",
                label="Danbooru",
                configured=lambda: bool(config.danbooru_username and config.danbooru_password),
            )
        )
    if bookmark_monitors:
        archive_bot.bookmark_monitor = BookmarkMonitorGroup(tuple(bookmark_monitors))
        bookmark_tasks = [asyncio.create_task(monitor.run_forever()) for monitor in bookmark_monitors]
        if config.bookmarks_enabled or config.twitter_bookmarks_enabled:
            archive_bot.bookmark_monitor.activate()
            await archive_bot.notify_bookmark_watch_started()
            await archive_bot.poll_bookmark_watch_once()
    logging.info("Bot started successfully!")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for bookmark_task in bookmark_tasks:
            bookmark_task.cancel()
        for bookmark_task in bookmark_tasks:
            try:
                await bookmark_task
            except asyncio.CancelledError:
                pass
        if runner:
            await runner.cleanup()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def apply_runtime_config_from_db(config: BotConfig, db: Database) -> None:
    rows = dict(db.get_config_rows())
    for key, attr in {
        "NSFW_LOW_THRESHOLD": "nsfw_low_threshold",
        "NSFW_HIGH_THRESHOLD": "nsfw_high_threshold",
    }.items():
        value = rows.get(key)
        if value is None:
            continue
        try:
            object.__setattr__(config, attr, float(value))
        except ValueError:
            continue
