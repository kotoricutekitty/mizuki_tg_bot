# Telegram Archive Bot

Python-only Telegram content archive bot for self-hosted channels.

The bot runs in polling mode by default, stores metadata in SQLite, keeps original media on local disk, and publishes approved submissions to a Telegram channel.

## Features

- X/Twitter, Pixiv, and Poipiku link submission
- Admin submissions publish directly; regular user submissions go to review
- Local SQLite database and local media archive
- Original media retrieval by forwarding a channel post back to the bot
- Optional HTTP `/submit` API
- Unified `/bookmark_watch` monitor for configured Twitter, Pixiv, and Poipiku bookmarks
- No Cloudflare Worker, D1, R2, or Queue dependency

## Quick Start

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Required `.env` values:

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
PUBLISH_CHANNEL_ID=@your_channel_username
```

Optional HTTP API is disabled by default. Set `HTTP_API_ENABLED=true` and `POST_TOKEN` to enable it.

Bookmark watching is also optional. `/bookmark_watch` or HTTP `POST /bookmarks/start` starts every configured source together. Twitter uses OAuth credentials, Pixiv uses `PIXIV_BOOKMARKS_USER_ID` plus a Netscape cookies file, and Poipiku uses a Netscape cookies file. Existing bookmarks present when watching starts are treated as candidates and are submitted after the stability window if they are still bookmarked.

## Testing

Tests simulate Telegram conversations with fake update/message/bot objects. They do not call Telegram APIs.

```bash
pip install -r requirements-dev.txt
pytest
```

## Fixture Export

To inspect an existing legacy SQLite database without leaking user IDs, tokens, or local paths:

```bash
python tests/tools/export_fixtures.py --db /path/to/db.sqlite --out fixtures.json
```
