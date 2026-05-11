from __future__ import annotations

import json
from pathlib import Path

import pytest

from tg_archive_bot import messages
from tests.fakes import (
    FakeCallbackQuery,
    FakeChat,
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
    assert bot.calls[0]["caption"] == "artist「hello」\nhttps://x.com/user/status/123"


@pytest.mark.asyncio
async def test_batch_links_with_duplicate(app_factory, sample_media):
    existing_url = "https://twitter.com/user/status/1"
    new_url = "https://twitter.com/user/status/2"
    service, db, _, _ = app_factory({new_url: ([sample_media["jpg"]], {"canonical_url": new_url})})
    db.create_submission(user_id=2, username="u", url=existing_url, status="approved", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())

    message = FakeMessage(text=f"{existing_url} {new_url}")
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    texts = [reply["text"] for reply in message.replies]
    assert messages.duplicate_submission(1, "approved") in texts
    assert messages.found_links(1) in texts
    assert messages.submitted_for_review(new_url) in texts


@pytest.mark.asyncio
async def test_download_failed_dialog(app_factory):
    url = "https://pixiv.net/artworks/987"
    service, db, _, _ = app_factory({url: ([], {})})
    message = FakeMessage(text=url)
    await service.handle_message(FakeUpdate(FakeUser(2, "normal"), message=message))
    assert message.replies[-1]["text"] == messages.download_failed(url)
    assert db.pending_submissions() == []


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
async def test_forward_channel_message_returns_originals(app_factory, sample_media):
    service, db, *_ = app_factory(channel="@archive")
    db.create_submission(user_id=2, username="u", url="https://twitter.com/u/status/1", status="approved", media_paths=[sample_media["jpg"]], metadata={}, now=service.clock.now())
    db.update_message_id(1, 555, service.clock.now())
    message = FakeMessage(forward_origin=FakeForwardOrigin(chat=FakeChat(-100, "archive"), message_id=555))
    await service.handle_message(FakeUpdate(FakeUser(2, "u"), message=message))
    assert message.replies[0]["text"] == messages.original_found("https://twitter.com/u/status/1")
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
async def test_http_api_submit_without_tg(app_factory, sample_media):
    url = "https://twitter.com/u/status/3"
    service, db, bot, _ = app_factory({url: ([sample_media["jpg"]], {"canonical_url": url})})
    result = await service.api_submit(url, "api-token", "127.0.0.1")
    assert result.status == 200
    assert result.body["status"] == "success"
    assert db.get_submission(result.body["submission_id"]).status == "approved"
    assert any(call["method"] == "send_message" and call["chat_id"] == 1 for call in bot.calls)
