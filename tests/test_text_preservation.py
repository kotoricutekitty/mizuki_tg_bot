from __future__ import annotations

import pytest

from tg_archive_bot import messages
from tests.fakes import FakeContext, FakeMessage, FakeUpdate, FakeUser


@pytest.mark.asyncio
async def test_start_help_commands(app_factory):
    service, *_ = app_factory()
    start_message = FakeMessage()
    await service.start(FakeUpdate(FakeUser(2, "user"), message=start_message))
    assert start_message.replies[0]["text"] == messages.START_TEXT

    help_message = FakeMessage()
    await service.help_command(FakeUpdate(FakeUser(2, "user"), message=help_message))
    assert help_message.replies[0]["text"] == messages.HELP_TEXT


@pytest.mark.asyncio
async def test_admin_config_commands_preserve_text(app_factory):
    service, db, *_ = app_factory()
    db.set_config("image_mode", "png")
    admin_message = FakeMessage()
    await service.config_command(FakeUpdate(FakeUser(1, "admin"), message=admin_message))
    assert admin_message.replies[0]["text"] == "📋 当前配置喵：\nimage_mode: png\n"

    set_message = FakeMessage()
    await service.set_command(FakeUpdate(FakeUser(1, "admin"), message=set_message), FakeContext(["foo", "bar baz"]))
    assert set_message.replies[0]["text"] == "好哒！已经设置 foo = bar baz 喵✅"

    user_message = FakeMessage()
    await service.config_command(FakeUpdate(FakeUser(2, "user"), message=user_message))
    assert user_message.replies[0]["text"] == messages.PERMISSION_DENIED


def test_publish_caption_preserved():
    assert messages.publish_caption(
        "https://twitter.com/a/status/1",
        author_name="作者",
        text="hello\nworld",
        canonical_url="https://x.com/a/status/1",
    ) == "作者「hello world」\nhttps://x.com/a/status/1"
