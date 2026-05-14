from __future__ import annotations

import json

import pytest

from tests.fakes import FakeCallbackQuery, FakeMessage, FakeSafetyDetector, FakeSentMessage, FakeUpdate, FakeUser, make_image


@pytest.mark.asyncio
async def test_pixiv_metadata_r18_routes_to_r18_channel_without_image_detection(app_factory, sample_media):
    url = "https://pixiv.net/artworks/123"
    detector = FakeSafetyDetector([0.0])
    service, db, bot, _ = app_factory(
        {url: ([sample_media["jpg"]], {"canonical_url": url, "x_restrict": 1})},
        r18_channel="@r18",
        safety_detector=detector,
    )

    result = await service.api_submit(url, "api-token", "127.0.0.1")

    assert result.status == 200
    assert db.get_submission(result.body["submission_id"]).status == "approved"
    assert detector.calls == []
    assert bot.calls[0]["method"] == "send_photo"
    assert bot.calls[0]["chat_id"] == "@r18"


@pytest.mark.asyncio
async def test_twitter_sensitive_metadata_routes_to_r18_without_image_detection(app_factory, sample_media):
    url = "https://twitter.com/u/status/200"
    detector = FakeSafetyDetector([0.0])
    service, _, bot, _ = app_factory(
        {url: ([sample_media["jpg"]], {"canonical_url": url, "possibly_sensitive": True})},
        r18_channel="@r18",
        safety_detector=detector,
    )

    result = await service.api_submit(url, "api-token", "127.0.0.1")

    assert result.status == 200
    assert detector.calls == []
    assert bot.calls[0]["chat_id"] == "@r18"


@pytest.mark.asyncio
async def test_danbooru_explicit_rating_routes_to_r18_without_image_detection(app_factory, sample_media):
    url = "https://danbooru.donmai.us/posts/1234567"
    detector = FakeSafetyDetector([0.0])
    service, db, bot, _ = app_factory(
        {url: ([sample_media["jpg"]], {"canonical_url": url, "rating": "e", "author_name": "artist"})},
        r18_channel="@r18",
        safety_detector=detector,
    )

    result = await service.api_submit(url, "api-token", "127.0.0.1")

    assert result.status == 200
    assert db.get_submission(result.body["submission_id"]).provider == "danbooru"
    assert detector.calls == []
    assert bot.calls[0]["method"] == "send_photo"
    assert bot.calls[0]["chat_id"] == "@r18"


@pytest.mark.asyncio
async def test_twitter_image_detection_checks_at_most_four_images(app_factory, tmp_path):
    url = "https://twitter.com/u/status/201"
    media = [make_image(tmp_path / "twitter" / f"{index}.jpg") for index in range(5)]
    detector = FakeSafetyDetector([0.95])
    service, _, bot, _ = app_factory(
        {url: (media, {"canonical_url": url})},
        r18_channel="@r18",
        safety_detector=detector,
    )

    result = await service.api_submit(url, "api-token", "127.0.0.1")

    assert result.status == 200
    assert len(detector.calls[0]) == 4
    assert bot.calls[0]["method"] == "send_media_group"
    assert bot.calls[0]["chat_id"] == "@r18"


@pytest.mark.asyncio
async def test_admin_moderation_notice_after_auto_publish(app_factory, sample_media):
    url = "https://poipiku.com/123/321.html"
    detector = FakeSafetyDetector([(0.10, "safe")])
    service, db, bot, _ = app_factory(
        {url: ([sample_media["jpg"]], {"canonical_url": url})},
        r18_channel="@r18",
        safety_detector=detector,
    )

    result = await service.api_submit(url, "api-token", "127.0.0.1")

    assert result.status == 200
    submission = db.get_submission(result.body["submission_id"])
    metadata = json.loads(submission.metadata_json)
    assert metadata["safety_score"] == 0.10
    assert metadata["safety_class"] == "safe"
    moderation = bot.calls[1]
    assert moderation["method"] == "send_photo"
    assert moderation["chat_id"] == 1
    assert moderation["caption"] == f"投稿 #{submission.id}\n色图分数: 0.10, safe\n发到频道：@archive\n{url}"
    buttons = moderation["reply_markup"]["inline_keyboard"][0]
    assert [button["text"] for button in buttons] == [
        "转到色图频道",
        "转到不色频道",
        "删除推文",
    ]
    assert len(bot.calls) == 2


@pytest.mark.asyncio
async def test_admin_manual_submit_leaves_single_moderation_message(app_factory, sample_media):
    url = "https://poipiku.com/123/654.html"
    detector = FakeSafetyDetector([(0.10, "safe")])
    service, _, bot, _ = app_factory(
        {url: ([sample_media["jpg"]], {"canonical_url": url})},
        r18_channel="@r18",
        safety_detector=detector,
    )
    message = FakeMessage(text=url)

    await service.handle_message(FakeUpdate(FakeUser(1, "admin"), message=message))

    assert len(message.replies) == 1
    assert bot.calls[0]["method"] == "send_photo"
    assert bot.calls[0]["chat_id"] == "@archive"
    assert bot.calls[1]["method"] == "send_photo"
    assert bot.calls[1]["chat_id"] == 1
    assert bot.calls[2] == {"method": "delete_message", "chat_id": 1, "message_id": 1}


@pytest.mark.asyncio
async def test_poipiku_uncertain_detection_checks_all_images_and_goes_pending(app_factory, tmp_path):
    url = "https://poipiku.com/123/456.html"
    media = [make_image(tmp_path / "poipiku" / f"{index}.jpg") for index in range(3)]
    detector = FakeSafetyDetector([0.50])
    service, db, bot, _ = app_factory(
        {url: (media, {"canonical_url": url})},
        r18_channel="@r18",
        safety_detector=detector,
    )

    result = await service.api_submit(url, "api-token", "127.0.0.1")

    assert result.status == 202
    assert result.body["status"] == "pending_review"
    assert db.pending_submissions()[0].id == result.body["submission_id"]
    assert len(detector.calls[0]) == 3
    assert bot.calls[0]["method"] == "send_media_group"
    assert bot.calls[0]["chat_id"] == 1


@pytest.mark.asyncio
async def test_admin_can_approve_uncertain_submission_as_r18(app_factory, sample_media):
    url = "https://poipiku.com/123/789.html"
    detector = FakeSafetyDetector([0.50])
    service, db, bot, _ = app_factory(
        {url: ([sample_media["jpg"]], {"canonical_url": url})},
        r18_channel="@r18",
        safety_detector=detector,
    )
    result = await service.api_submit(url, "api-token", "127.0.0.1")
    query = FakeCallbackQuery(f"approve_r18:{result.body['submission_id']}", FakeSentMessage(1, caption="review"))

    await service.handle_callback(FakeUpdate(FakeUser(1, "admin"), callback_query=query))

    submission = db.get_submission(result.body["submission_id"])
    assert submission.status == "approved"
    published = [call for call in bot.calls if call["method"] == "send_photo" and call["chat_id"] == "@r18"]
    assert published
    assert "发到频道：@r18" in query.edited_caption
    assert "✅ 已经通过啦喵 by @admin" in query.edited_caption
