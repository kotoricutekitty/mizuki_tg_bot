from __future__ import annotations

import asyncio

from .telegram_runtime import main


def run() -> None:
    asyncio.run(main())
