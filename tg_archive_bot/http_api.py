from __future__ import annotations

from typing import Any

from .service import ArchiveBot, SubmitResult


async def submit_payload(bot: ArchiveBot, payload: dict[str, Any], token: str | None, client_ip: str = "") -> SubmitResult:
    return await bot.api_submit(payload.get("url"), token, client_ip)


def start_bookmarks_payload(bot: ArchiveBot, token: str | None) -> SubmitResult:
    if token != bot.config.post_token:
        return SubmitResult(401, {"status": "unauthorized", "message": "无效的Token"})
    return bot.activate_bookmark_watch()


async def start_bookmarks(bot: ArchiveBot, token: str | None) -> SubmitResult:
    result = start_bookmarks_payload(bot, token)
    if result.status == 200:
        await bot.notify_bookmark_watch_started()
    return result


async def run_http_api(bot: ArchiveBot, host: str, port: int):
    from aiohttp import web

    async def handle_submit(request: web.Request) -> web.Response:
        try:
            token = request.headers.get("X-Post-Token", "")
            payload = await request.json()
            result = await submit_payload(bot, payload, token, request.remote or "")
            return web.json_response(result.body, status=result.status)
        except Exception as exc:
            await bot.notify_admin_error(
                "HTTP /submit failed",
                exc,
                detail=f"remote={request.remote or ''}",
                throttle_key=f"http_submit:{type(exc).__name__}:{str(exc)[:120]}",
            )
            raise

    async def handle_bookmarks_start(request: web.Request) -> web.Response:
        try:
            token = request.headers.get("X-Post-Token", "")
            result = await start_bookmarks(bot, token)
            return web.json_response(result.body, status=result.status)
        except Exception as exc:
            await bot.notify_admin_error(
                "HTTP /bookmarks/start failed",
                exc,
                detail=f"remote={request.remote or ''}",
                throttle_key=f"http_bookmarks_start:{type(exc).__name__}:{str(exc)[:120]}",
            )
            raise

    app = web.Application()
    app.add_routes([
        web.post("/submit", handle_submit),
        web.post("/bookmarks/start", handle_bookmarks_start),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
