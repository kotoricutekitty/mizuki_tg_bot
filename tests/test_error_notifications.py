from __future__ import annotations

import sys
import types

import pytest

from tg_archive_bot import messages

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))

from tg_archive_bot.telegram_runtime import error_handler


class ErrorContext:
    def __init__(self, error: Exception):
        self.error = error


@pytest.mark.asyncio
async def test_telegram_error_handler_notifies_admins(app_factory):
    service, _, bot, _ = app_factory()

    await error_handler({"update_id": 1}, ErrorContext(RuntimeError("handler exploded")), service)

    assert bot.calls[0]["method"] == "send_message"
    assert bot.calls[0]["chat_id"] == 1
    assert messages.ADMIN_ERROR_PREFIX in bot.calls[0]["text"]
    assert "Telegram update handler failed" in bot.calls[0]["text"]
    assert "RuntimeError: handler exploded" in bot.calls[0]["text"]


@pytest.mark.asyncio
async def test_admin_error_notifications_are_throttled(app_factory):
    service, _, bot, _ = app_factory()

    await service.notify_admin_error("same source", RuntimeError("same"))
    await service.notify_admin_error("same source", RuntimeError("same"))

    assert len(bot.calls) == 1

    service.clock.advance(301)
    await service.notify_admin_error("same source", RuntimeError("same"))

    assert len(bot.calls) == 2
