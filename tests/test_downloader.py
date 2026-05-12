from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from tg_archive_bot import downloader


def test_poipiku_placeholder_is_filtered(tmp_path: Path, monkeypatch):
    placeholder = tmp_path / "placeholder.png"
    real = tmp_path / "real.png"
    Image.new("RGB", (960, 320), "white").save(placeholder)
    Image.new("RGB", (32, 32), "red").save(real)
    placeholder_hash = hashlib.sha256(placeholder.read_bytes()).hexdigest()
    monkeypatch.setattr(downloader, "POIPIKU_PLACEHOLDER_SHA256", {placeholder_hash})

    assert downloader.is_poipiku_placeholder(placeholder)
    assert not downloader.is_poipiku_placeholder(real)
    assert downloader.filter_poipiku_placeholders([str(placeholder), str(real)]) == [str(real)]
