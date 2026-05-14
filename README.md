# Telegram Archive Bot

Python-only Telegram archive bot for self-hosted channels. It runs in polling mode by default, stores metadata in SQLite, keeps original media on local disk, and publishes approved submissions to one or two Telegram channels.

## Features

- X/Twitter, Pixiv, Poipiku, and Danbooru post submission
- Admin submissions publish directly; regular user submissions go to review
- Optional R-18 routing to a second channel
- Anime image rating with `deepghs/anime_rating` model `mobilenetv3_sce_dist`
- Original media retrieval by forwarding a channel post or sending an already submitted link
- Optional HTTP `POST /submit` API protected by `X-Post-Token`
- Unified `/bookmark_watch` monitor for Twitter, Pixiv, and Poipiku bookmarks
- Local SQLite database and local media archive
- No Cloudflare Worker, D1, R2, Queue, or webhook dependency

## Create a Telegram Bot and Channel

1. Open Telegram and talk to `@BotFather`.
2. Run `/newbot`, choose a display name and username, then copy the bot token into `BOT_TOKEN`.
3. Create a Telegram channel. Public channels can use `@channel_username` as `PUBLISH_CHANNEL_ID`.
4. Add the bot to the channel as an administrator.
5. Give the bot permission to post messages. If you want the bot to move/delete previously published posts, also allow message deletion.
6. Send `/start` to the bot from your admin account, then get your numeric Telegram user id from a bot such as `@userinfobot`; put it in `ADMIN_IDS`.
7. Optional: create a second R-18 channel, add the same bot as admin, and set `R18_ROUTING_ENABLED=true` plus `R18_CHANNEL_ID=@your_r18_channel`.

## Install

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Install external tools used by downloads:

```bash
pip install gallery-dl
# optional, needed for Pixiv ugoira conversion
brew install ffmpeg
```

On Linux, install `ffmpeg` with your system package manager instead of `brew`.

## Configure

Minimum `.env`:

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
PUBLISH_CHANNEL_ID=@your_channel_username
```

Recommended local paths:

```env
DATA_DIR=./data
DATABASE_PATH=./data/db.sqlite
MEDIA_DIR=./data/media
TEMP_DIR=./data/tmp
```

Run the bot:

```bash
python bot.py
```

For a long-running server, create a systemd service or run it under your process manager. The bot uses Telegram polling, so it does not need a public webhook URL.

## HTTP Posting API

The HTTP API is disabled by default. Enable it with:

```env
HTTP_API_ENABLED=true
HTTP_API_HOST=0.0.0.0
HTTP_API_PORT=8080
POST_TOKEN=replace_with_a_long_random_token
```

Submit a link:

```bash
curl -X POST http://127.0.0.1:8080/submit \
  -H 'Content-Type: application/json' \
  -H 'X-Post-Token: replace_with_a_long_random_token' \
  -d '{"url":"https://x.com/user/status/123"}'
```

Start bookmark monitoring through HTTP:

```bash
curl -X POST http://127.0.0.1:8080/bookmarks/start \
  -H 'X-Post-Token: replace_with_a_long_random_token'
```

If you expose the API to the internet, put it behind a reverse proxy and keep `POST_TOKEN` secret.

## R-18 Routing

Pixiv and site metadata are checked first. If metadata does not clearly mark the post, X/Twitter checks up to four images and Poipiku checks all downloaded images.

Image scoring uses `deepghs/anime_rating` through `dghs-imgutils`:

```env
R18_ROUTING_ENABLED=true
R18_CHANNEL_ID=@your_r18_channel
NSFW_DETECTION_ENABLED=true
ANIME_RATING_MODEL=mobilenetv3_sce_dist
NSFW_LOW_THRESHOLD=0.25
NSFW_HIGH_THRESHOLD=0.80
NSFW_TWITTER_MAX_IMAGES=4
```

The saved `safety_score` is the model's R-18 score. Scores below `NSFW_LOW_THRESHOLD` are treated as safe, scores above `NSFW_HIGH_THRESHOLD` route to the R-18 channel, and the middle range goes to admin review. Admins can tune this live:

```text
/rating_threshold 0.20 0.75
```

`/nsfw_threshold` remains accepted as a compatibility alias.

## Twitter Bookmarks

Twitter bookmark polling requires OAuth 2.0 user context, not only an app bearer token. Your X developer app needs at least:

- `bookmark.read`
- `tweet.read`
- `users.read`
- `offline.access` if you want refresh tokens

Configure:

```env
BOOKMARKS_ENABLED=true
TWITTER_BOOKMARKS_ENABLED=true
TWITTER_BOOKMARKS_USER_ID=your_x_user_id
TWITTER_BOOKMARKS_ACCESS_TOKEN=your_oauth2_user_access_token
TWITTER_BOOKMARKS_REFRESH_TOKEN=your_oauth2_refresh_token
TWITTER_OAUTH_CLIENT_ID=your_oauth2_client_id
TWITTER_OAUTH_CLIENT_SECRET=your_oauth2_client_secret
TWITTER_BOOKMARKS_POLL_SECONDS=30
TWITTER_BOOKMARKS_GRACE_SECONDS=10
TWITTER_BOOKMARKS_IDLE_SECONDS=120
TWITTER_BOOKMARKS_MAX_RESULTS=5
TWITTER_BOOKMARKS_MAX_PAGES=4
```

When monitoring starts, the bot fetches immediately, waits for the grace window, and submits bookmarks that are still present. If monitoring is already running, another `/bookmark_watch` or `POST /bookmarks/start` only resets the idle timer and does not immediately call the X API again.

## Pixiv Bookmarks

Pixiv bookmark polling needs your Pixiv user id and a Netscape-format cookie file:

```env
BOOKMARKS_ENABLED=true
PIXIV_BOOKMARKS_USER_ID=your_pixiv_user_id
PIXIV_BOOKMARKS_COOKIES=./secrets/pixiv-cookies.txt
```

Export cookies from your browser with a cookie export extension and save only the Netscape cookie file on the server. Do not commit this file.

## Poipiku Bookmarks and R-18 Posts

Poipiku downloads often require login cookies, especially for R-18, follower-only, or age-confirmed posts:

```env
BOOKMARKS_ENABLED=true
POIPIKU_BOOKMARKS_COOKIES=./secrets/poipiku-cookies.txt
GALLERY_DL_COOKIES=./secrets/poipiku-cookies.txt
WEB_BOOKMARKS_MAX_RESULTS=20
WEB_BOOKMARKS_MAX_PAGES=4
```

Use an account that can view the target posts. If the downloaded image is only a placeholder, refresh the cookie file and retry the submission with `/retry <id|url>`.

## Danbooru Posts

Danbooru single post URLs are supported through `gallery-dl`:

```text
https://danbooru.donmai.us/posts/1234567
```

Optional login:

```env
DANBOORU_USERNAME=your_danbooru_username
DANBOORU_PASSWORD=your_danbooru_api_key_or_password
```

Only single post URLs are supported by the bot by default. Search and pool URLs are intentionally not matched to avoid accidentally posting a large batch. Danbooru `rating=q` and `rating=e` route as R-18 when R-18 routing is enabled.

## Bot Commands

User commands:

```text
start - Start the bot
help - Show user help
original - Explain how to get original media back
admin_help - Show admin help if you are an admin
```

Admin commands:

```text
pending - Show pending review submissions
find <id|url> - Query a submission
select <url> <1,2,3> - Publish only selected media indexes from a multi-image post
retry <id|url> - Re-download or re-publish a submission
delete <id|url> - Soft-delete a submission and release duplicate detection
stats - Show submission statistics
config - Show runtime config stored in the database
set <key> <value> - Set runtime config
pixiv_status - Show Pixiv download rate-limit status
rating_threshold <low> <high> - Tune anime rating thresholds
bookmark_watch - Start Twitter/Pixiv/Poipiku bookmark watching
```

## Testing

Tests simulate Telegram conversations with fake update/message/bot objects. They do not call Telegram APIs.

```bash
pip install -r requirements-dev.txt
python3 -m pytest
```

## Fixture Export

To inspect an existing legacy SQLite database without leaking user IDs, tokens, or local paths:

```bash
python tests/tools/export_fixtures.py --db /path/to/db.sqlite --out fixtures.json
```

## Keep Secrets Out of Git

Never commit:

- `.env`
- `data/`
- `secrets/`
- cookie files
- SQLite databases
- downloaded media
- logs

Before publishing, scan for token-like strings:

```bash
rg --hidden -n -g '!*.zip' -g '!.git/**' -g '!data/**' -g '!secrets/**' -g '!venv/**' \
  -e '[0-9]{8,}:[A-Za-z0-9_-]{30,}' \
  -e 'AAAA[A-Za-z0-9_%.-]{20,}' \
  -e 'PHPSESSID|POIPIKU_LK|TWITTER_BOOKMARKS_ACCESS_TOKEN|BOT_TOKEN='
```
