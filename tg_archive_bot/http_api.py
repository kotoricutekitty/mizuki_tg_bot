from __future__ import annotations

from typing import Any

from .service import ArchiveBot, SubmitResult


async def submit_payload(bot: ArchiveBot, payload: dict[str, Any], token: str | None, client_ip: str = "") -> SubmitResult:
    return await bot.api_submit(payload.get("url"), token, client_ip)


async def run_http_api(bot: ArchiveBot, host: str, port: int):
    from aiohttp import web

    async def handle_submit(request: web.Request) -> web.Response:
        token = request.headers.get("X-Post-Token", "")
        payload = await request.json()
        result = await submit_payload(bot, payload, token, request.remote or "")
        return web.json_response(result.body, status=result.status)

    app = web.Application()
    app.add_routes([web.post("/submit", handle_submit)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
