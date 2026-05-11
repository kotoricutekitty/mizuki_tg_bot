#!/usr/bin/env python3
"""Runtime entrypoint for the Python-only Telegram archive bot."""

import asyncio

from tg_archive_bot.telegram_runtime import main


if __name__ == "__main__":
    asyncio.run(main())
