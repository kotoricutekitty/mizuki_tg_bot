from __future__ import annotations

import pytest

from tg_archive_bot import telegram_runtime
from tg_archive_bot.telegram_runtime import TelegramBotClient


@pytest.mark.asyncio
async def test_telegram_bot_client_retries_retry_after(monkeypatch):
    RetryAfter = type("RetryAfter", (Exception,), {"__module__": "telegram.error"})

    class Bot:
        def __init__(self):
            self.calls = 0

        async def send_message(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                exc = RetryAfter("Flood control exceeded")
                exc.retry_after = 2
                raise exc
            return {"ok": True, **kwargs}

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    bot = Bot()
    monkeypatch.setattr(telegram_runtime.asyncio, "sleep", fake_sleep)

    result = await TelegramBotClient(bot).send_message(1, "hello")

    assert result == {"ok": True, "chat_id": 1, "text": "hello"}
    assert bot.calls == 2
    assert sleeps == [3]


@pytest.mark.asyncio
async def test_telegram_bot_client_ignores_message_not_modified():
    class Bot:
        async def edit_message_caption(self, **kwargs):
            raise RuntimeError("Message is not modified: specified new message content is exactly the same")

    result = await TelegramBotClient(Bot()).edit_message_caption("@archive", 123, "same caption")

    assert result is None
