from __future__ import annotations

import pytest

from tests.fakes import FakeCallbackQuery, FakeSafetyDetector, FakeSentMessage, FakeUpdate, FakeUser, make_image


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
    assert bot.calls[-2]["method"] == "send_photo"
    assert bot.calls[-2]["chat_id"] == "@r18"
    assert query.edited_caption == "review\n\n✅ 已经通过啦喵 by @admin"
