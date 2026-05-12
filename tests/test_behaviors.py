from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tg_archive_bot import messages
from tests.fakes import (
    FakeCallbackQuery,
    FakeChat,
    FakeEntity,
    FakeForwardOrigin,
    FakeMessage,
    FakeSentMessage,
    FakeUpdate,
    FakeUser,
    make_image,
)


@pytest.mark.asyncio
async def test_normal_user_submit_single_link(app_factory, sample_media):
    url = "https://pixiv.net/artworks/123456"
    metadata = {"author_name": "artist", "title": "title", "text": "desc", "canonical_url": url}
    service, db, bot, downloader = app_factory({url: ([sample_media["jpg"]], metadata)})
    message = FakeMessage(text=url)
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))

    pending = db.pending_submissions()
    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert downloader.calls == [url]
    assert message.replies[-1]["text"] == messages.submitted_for_review(url)
    assert bot.calls[0]["method"] == "send_photo"
    assert bot.calls[0]["chat_id"] == 1
    assert bot.calls[0]["caption"] == messages.review_caption(1, "normal", url, metadata)


@pytest.mark.asyncio
async def test_admin_submit_auto_publish(app_factory, sample_media):
    url = "https://twitter.com/user/status/123"
    metadata = {"author_name": "artist", "text": "hello", "canonical_url": "https://x.com/user/status/123"}
    service, db, bot, _ = app_factory({"https://twitter.com/user/status/123": ([sample_media["jpg"]], metadata)})
    message = FakeMessage(text=url)
    await service.handle_message(FakeUpdate(FakeUser(1, "admin"), message=message))

    submission = db.get_submission(1)
    assert submission is not None
    assert submission.status == "approved"
    assert submission.message_id is not None
    assert message.replies[-1]["text"] == messages.admin_published("https://twitter.com/user/status/123")
    assert bot.calls[0]["method"] == "send_photo"
    assert bot.calls[0]["chat_id"] == "@archive"
    assert bot.calls[0]["caption"] == "<b>artist</b>: 「hello」\nhttps://x.com/user/status/123"
    assert bot.calls[0]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_batch_links_with_duplicate(app_factory, sample_media):
    existing_url = "https://twitter.com/user/status/1"
    new_url = "https://twitter.com/user/status/2"
    service, db, _, _ = app_factory({new_url: ([sample_media["jpg"]], {"canonical_url": new_url})})
    db.create_submission(user_id=2, username="u", url=existing_url, status="approved", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())

    message = FakeMessage(text=f"{existing_url} {new_url}")
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    texts = [reply["text"] for reply in message.replies]
    assert messages.original_found(existing_url) in texts
    assert message.documents[0]["filename"] == Path(sample_media["jpg"]).name
    assert messages.found_links(1) in texts
    assert messages.submitted_for_review(new_url) in texts


@pytest.mark.asyncio
async def test_existing_link_returns_originals_without_forward(app_factory, sample_media):
    url = "https://twitter.com/user/status/1"
    service, db, _, downloader = app_factory()
    db.create_submission(
        user_id=2,
        username="u",
        url=url,
        status="approved",
        media_paths=[sample_media["jpg"]],
        metadata={},
        now=service.clock.now(),
    )

    message = FakeMessage(text=url)
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))

    assert downloader.calls == []
    assert message.replies[0]["text"] == messages.original_found(url)
    assert message.documents[0]["filename"] == Path(sample_media["jpg"]).name


@pytest.mark.asyncio
async def test_download_failed_dialog(app_factory):
    url = "https://pixiv.net/artworks/987"
    service, db, _, _ = app_factory({url: ([], {})})
    message = FakeMessage(text=url)
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    assert message.replies[-1]["text"] == messages.download_failed(url)
    assert db.pending_submissions() == []


@pytest.mark.asyncio
async def test_pending_command_lists_and_empty_state(app_factory, sample_media):
    service, db, _, _ = app_factory()
    empty = FakeMessage()
    await service.pending_command(FakeUpdate(FakeUser(1, "admin"), message=empty))
    assert empty.replies[0]["text"] == messages.NO_PENDING

    db.create_submission(
        user_id=2,
        username="normal",
        url="https://twitter.com/u/status/44",
        status="pending",
        media_paths=[sample_media["jpg"]],
        metadata={},
        now=service.clock.now(),
    )
    listed = FakeMessage()
    await service.pending_command(FakeUpdate(FakeUser(1, "admin"), message=listed))
    assert listed.replies[0]["text"] == "📝 待审核列表喵：\n#1 - normal (2) - https://twitter.com/u/status/44\n"


@pytest.mark.asyncio
async def test_pixiv_status_command_output(app_factory):
    service, _, _, _ = app_factory()
    service.db.record_pixiv_download("https://pixiv.net/artworks/1")
    message = FakeMessage()
    await service.pixiv_status_command(FakeUpdate(FakeUser(1, "admin"), message=message))
    text = message.replies[0]["text"]
    assert text.startswith("📊 Pixiv下载频率使用情况喵：\n当前5小时周期内已使用：1/100次\n剩余可用次数：99次\n")
    assert "最近一次请求：" in text


@pytest.mark.asyncio
async def test_set_usage_and_non_admin_command_permissions(app_factory):
    service, *_ = app_factory()
    short = FakeMessage()
    await service.set_command(FakeUpdate(FakeUser(1, "admin"), message=short), type("Ctx", (), {"args": ["only"]})())
    assert short.replies[0]["text"] == messages.SET_USAGE

    for command in (service.set_command, service.pending_command, service.pixiv_status_command):
        message = FakeMessage()
        ctx = type("Ctx", (), {"args": ["key", "value"]})()
        await command(FakeUpdate(FakeUser(2, "normal"), message=message), ctx)
        assert message.replies[0]["text"] == messages.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_extracts_urls_from_caption_forward_and_entities(app_factory, sample_media):
    caption_url = "https://twitter.com/caption/status/1"
    forward_url = "https://pixiv.net/artworks/222"
    entity_url = "https://twitter.com/entity/status/3"
    service, db, _, downloader = app_factory(
        {
            caption_url: ([sample_media["jpg"]], {"canonical_url": caption_url}),
            forward_url: ([sample_media["jpg"]], {"canonical_url": forward_url}),
            entity_url: ([sample_media["jpg"]], {"canonical_url": entity_url}),
        }
    )
    forwarded_message = FakeMessage(text=forward_url)
    message = FakeMessage(
        caption=caption_url,
        forward_origin=FakeForwardOrigin(message=forwarded_message),
        entities=[FakeEntity("url", entity_url)],
    )
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    assert downloader.calls == [caption_url, entity_url, forward_url]
    assert len(db.pending_submissions()) == 3


@pytest.mark.asyncio
async def test_poipiku_submission(app_factory, sample_media):
    url = "https://poipiku.com/123/456.html"
    service, db, _, downloader = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url, "author_name": "poipiku user"})})
    message = FakeMessage(text=url)
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    assert downloader.calls == [url]
    submission = db.pending_submissions()[0]
    assert submission.provider == "poipiku"
    assert message.replies[-1]["text"] == messages.submitted_for_review(url)


@pytest.mark.asyncio
async def test_pixiv_rate_limit(app_factory):
    service, _, _, _ = app_factory()
    for _ in range(100):
        service.db.record_pixiv_download("https://pixiv.net/artworks/1")
    message = FakeMessage(text="https://pixiv.net/artworks/2")
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    assert message.replies[0]["text"] == messages.PIXIV_RATE_LIMITED


@pytest.mark.asyncio
async def test_review_approve(app_factory, sample_media):
    service, db, bot, _ = app_factory()
    sub_id = db.create_submission(
        user_id=2,
        username="normal",
        url="https://twitter.com/u/status/1",
        status="pending",
        media_paths=[sample_media["jpg"]],
        metadata={"author_name": "a", "text": "t", "canonical_url": "https://x.com/u/status/1"},
        now=service.clock.now(),
    )
    query = FakeCallbackQuery(f"approve:{sub_id}", FakeSentMessage(7, caption="review"))
    await service.handle_callback(FakeUpdate(FakeUser(1, "admin"), callback_query=query))
    assert query.answered
    assert query.edited_caption == "review\n\n✅ 已经通过啦喵 by @admin"
    assert db.get_submission(sub_id).status == "approved"
    assert any(call["method"] == "send_message" and call["chat_id"] == 2 for call in bot.calls)


@pytest.mark.asyncio
async def test_review_reject(app_factory, sample_media):
    service, db, bot, _ = app_factory()
    sub_id = db.create_submission(user_id=2, username="normal", url="https://twitter.com/u/status/2", status="pending", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())
    query = FakeCallbackQuery(f"reject:{sub_id}", FakeSentMessage(8, caption="review"))
    await service.handle_callback(FakeUpdate(FakeUser(1, "admin"), callback_query=query))
    assert query.edited_caption == "review\n\n❌ 已经被拒绝啦喵 by @admin"
    assert db.get_submission(sub_id).status == "rejected"
    assert bot.calls[-1]["text"] == messages.submitter_rejected("https://twitter.com/u/status/2")


@pytest.mark.asyncio
async def test_review_permission_denied(app_factory):
    service, *_ = app_factory()
    query = FakeCallbackQuery("approve:1", FakeSentMessage(1, caption="review"))
    await service.handle_callback(FakeUpdate(FakeUser(2, "normal"), callback_query=query))
    assert query.edited_caption == "review\n\n😾 呜喵...你没有权限审核哦！"


@pytest.mark.asyncio
async def test_review_already_processed_and_missing_submission(app_factory, sample_media):
    service, db, *_ = app_factory()
    sub_id = db.create_submission(
        user_id=2,
        username="normal",
        url="https://twitter.com/u/status/5",
        status="approved",
        media_paths=[sample_media["jpg"]],
        metadata={},
        now=service.clock.now(),
    )
    already = FakeCallbackQuery(f"approve:{sub_id}", FakeSentMessage(1, caption="review"))
    await service.handle_callback(FakeUpdate(FakeUser(1, "admin"), callback_query=already))
    assert already.edited_caption == "review\n\n✅ 已经被approved啦喵！"

    missing = FakeCallbackQuery("approve:999", FakeSentMessage(2, caption="review"))
    await service.handle_callback(FakeUpdate(FakeUser(1, "admin"), callback_query=missing))
    assert missing.edited_caption == "review\n\n😿 呜喵...投稿不存在哦！"


@pytest.mark.asyncio
async def test_forward_channel_message_returns_originals(app_factory, sample_media):
    service, db, *_ = app_factory(channel="@archive")
    db.create_submission(user_id=2, username="u", url="https://twitter.com/u/status/1", status="approved", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())
    db.update_message_id(1, 555, service.clock.now())
    message = FakeMessage(forward_origin=FakeForwardOrigin(chat=FakeChat(-100, "archive"), message_id=555))
    await service.handle_message(FakeUpdate(FakeUser(2, "u"), message=message))
    assert message.replies[0]["text"] == messages.original_found("https://twitter.com/u/status/1")
    assert message.documents[0]["filename"] == Path(sample_media["jpg"]).name


@pytest.mark.asyncio
async def test_forward_channel_message_returns_originals_with_numeric_channel_id(app_factory, sample_media):
    service, db, *_ = app_factory(channel="-100123")
    db.create_submission(user_id=2, username="u", url="https://twitter.com/u/status/9", status="approved", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())
    db.update_message_id(1, 777, service.clock.now())
    message = FakeMessage(forward_origin=FakeForwardOrigin(chat=FakeChat(-100123, "ignored"), message_id=777))
    await service.handle_message(FakeUpdate(FakeUser(2, "u"), message=message))
    assert message.replies[0]["text"] == messages.original_found("https://twitter.com/u/status/9")
    assert message.documents[0]["filename"] == Path(sample_media["jpg"]).name


@pytest.mark.asyncio
async def test_publish_media_variants(app_factory, tmp_path):
    jpgs = [make_image(tmp_path / "many" / f"{i}.jpg") for i in range(12)]
    service, db, bot, _ = app_factory()
    sub_id = db.create_submission(user_id=1, username="admin", url="https://pixiv.net/artworks/999", status="approved", media_paths=jpgs, metadata={"canonical_url": "https://pixiv.net/artworks/999"}, now=service.clock.now())
    await service.publish_submission(sub_id, 1)
    assert bot.calls[0]["method"] == "send_photo"
    assert "📸 共 12 张图" in bot.calls[0]["caption"]
    groups = [call for call in bot.calls if call["method"] == "send_media_group"]
    assert len(groups) == 2
    assert db.get_submission(sub_id).message_id is not None


@pytest.mark.asyncio
async def test_publish_two_to_ten_images_uses_media_group(app_factory, sample_media):
    service, db, bot, _ = app_factory()
    sub_id = db.create_submission(
        user_id=1,
        username="admin",
        url="https://twitter.com/u/status/10",
        status="approved",
        media_paths=[sample_media["jpg"], sample_media["png"]],
        metadata={"author_name": "artist", "text": "hello", "canonical_url": "https://x.com/u/status/10"},
        now=service.clock.now(),
    )
    await service.publish_submission(sub_id, 1)
    assert bot.calls[0]["method"] == "send_media_group"
    assert bot.calls[0]["media"][0]["caption"] == "<b>artist</b>: 「hello」\nhttps://x.com/u/status/10"
    assert bot.calls[0]["media"][0]["parse_mode"] == "HTML"
    assert db.get_submission(sub_id).message_id is not None


@pytest.mark.asyncio
async def test_publish_video_and_document_branches(app_factory, sample_media, tmp_path):
    service, db, bot, _ = app_factory()
    video_id = db.create_submission(user_id=1, username="admin", url="https://twitter.com/u/status/11", status="approved", media_paths=[sample_media["mp4"]], metadata={}, now=service.clock.now())
    await service.publish_submission(video_id, 1)
    assert bot.calls[-1]["method"] == "send_video"

    doc = tmp_path / "media" / "archive.zip"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b"fake")
    doc_id = db.create_submission(user_id=1, username="admin", url="https://twitter.com/u/status/12", status="approved", media_paths=[str(doc)], metadata={}, now=service.clock.now())
    await service.publish_submission(doc_id, 1)
    assert bot.calls[-1]["method"] == "send_document"


def test_publish_caption_without_author():
    assert messages.publish_caption(
        "https://twitter.com/u/status/13",
        text="hello",
        canonical_url="https://x.com/u/status/13",
    ) == "「hello」\nhttps://x.com/u/status/13"


@pytest.mark.asyncio
async def test_http_api_submit_without_tg(app_factory, sample_media):
    url = "https://twitter.com/u/status/3"
    service, db, bot, _ = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url})})
    result = await service.api_submit(url, "api-token", "127.0.0.1")
    assert result.status == 200
    assert result.body["status"] == "success"
    assert db.get_submission(result.body["submission_id"]).status == "approved"
    assert any(call["method"] == "send_message" and call["chat_id"] == 1 for call in bot.calls)


@pytest.mark.asyncio
async def test_http_api_error_branches_without_tg(app_factory, sample_media):
    url = "https://twitter.com/u/status/20"
    service, db, _, _ = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url})})
    unauthorized = await service.api_submit(url, "wrong", "127.0.0.1")
    assert unauthorized.status == 401
    assert unauthorized.body == {"status": "unauthorized", "message": "无效的Token"}

    missing_url = await service.api_submit(None, "api-token", "127.0.0.1")
    assert missing_url.status == 400
    assert missing_url.body == {"status": "error", "message": "缺少url参数"}

    db.create_submission(user_id=1, username="admin", url=url, status="approved", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())
    duplicate = await service.api_submit(url, "api-token", "127.0.0.1")
    assert duplicate.status == 409
    assert duplicate.body["status"] == "already_exists"

    failed = await service.api_submit("https://twitter.com/u/status/21", "api-token", "127.0.0.1")
    assert failed.status == 400
    assert failed.body == {"status": "download_failed", "message": "媒体下载失败，请检查链接是否有效"}


@pytest.mark.asyncio
async def test_http_api_pixiv_rate_limit_branch_without_user_flow(app_factory):
    service, _, _, _ = app_factory()
    for _ in range(100):
        service.db.record_pixiv_download("https://pixiv.net/artworks/1")
    limited = await service.api_submit("https://pixiv.net/artworks/2", "api-token", "127.0.0.1")
    assert limited.status == 429
    assert limited.body["status"] == "rate_limit"
    assert limited.body["used"] == 100


def test_legacy_database_migration_compatibility(tmp_path, sample_media):
    from tg_archive_bot.db import Database

    path = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            url TEXT,
            status TEXT,
            media_paths TEXT,
            created_at TIMESTAMP,
            reviewed_at TIMESTAMP,
            reviewer_id INTEGER,
            message_id INTEGER,
            author_name TEXT,
            title TEXT,
            text TEXT,
            canonical_url TEXT
        )
        """
    )
    con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE pixiv_downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, request_time TIMESTAMP, url TEXT)")
    con.execute(
        "INSERT INTO submissions (user_id, username, url, status, media_paths, created_at, author_name, text, canonical_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (2, "legacy", "https://x.com/u/status/33?s=20", "approved", json.dumps([sample_media["jpg"]]), "2026-05-11", "artist", "hello", "https://x.com/u/status/33?s=20"),
    )
    con.commit()
    con.close()

    db = Database(path)
    db.init()
    sub = db.get_submission(1)
    assert sub is not None
    assert sub.normalized_url == "https://twitter.com/u/status/33"
    assert sub.provider == "x"
    assert json.loads(sub.metadata_json)["author_name"] == "artist"
