# Telegram Archive Bot Maintenance

## Overview

This is a local Python deployment of a Telegram archive bot. It uses polling, SQLite, local media storage, `gallery-dl`, and `ffmpeg`. It does not require Cloudflare services.

## Deployment

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Example systemd service is provided in `tg-archive-bot.service`. Replace the service user and working directory before installing it.

## Configuration

Required:

| Key | Description |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token from BotFather |
| `ADMIN_IDS` | Comma-separated Telegram user IDs |
| `PUBLISH_CHANNEL_ID` | Channel ID or `@username` |

Optional:

| Key | Description |
| --- | --- |
| `HTTP_API_ENABLED` | Enables the optional `/submit` API when `true` |
| `POST_TOKEN` | API token for `/submit` |
| `DATA_DIR` | Local data directory |
| `DATABASE_PATH` | SQLite database path |
| `MEDIA_DIR` | Local media archive path |
| `TEMP_DIR` | Temporary working directory |
| `TWITTER_BOOKMARKS_ENABLED` | Enables X/Twitter bookmark monitoring |
| `TWITTER_BOOKMARKS_USER_ID` | X user ID whose bookmarks are monitored |
| `TWITTER_BOOKMARKS_ACCESS_TOKEN` | OAuth 2.0 user access token with bookmark read scopes |
| `TWITTER_BOOKMARKS_POLL_SECONDS` | Bookmark polling interval |
| `TWITTER_BOOKMARKS_GRACE_SECONDS` | Stability window before a new bookmark becomes a submission |
| `TWITTER_BOOKMARKS_IDLE_SECONDS` | Auto-stop window when no bookmark changes are seen |
| `TWITTER_BOOKMARKS_MAX_RESULTS` | Number of recent bookmarks fetched per poll |

## Behavior Notes

- Existing user-facing messages and channel captions are intentionally preserved during refactors.
- Channel caption format remains: `作者「内容」\n链接`.
- Review buttons remain `✅ 通过` and `❌ 拒绝`.
- Tests use fake Telegram conversation objects and must not call Telegram APIs.

## Database

The code can initialize a fresh SQLite database and migrate legacy single-table databases. Legacy `media_paths` JSON is preserved for compatibility; newer fields such as `normalized_url`, `provider`, and `metadata_json` are added when missing.
