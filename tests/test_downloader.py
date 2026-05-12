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


def test_extract_poipiku_append_image_urls():
    html = """
    <img src="https://cdn.poipiku.com/a/b.png_640.jpg">
    <img src="https://cdn.poipiku.com/a/b.png_640.jpg">
    <img src="https://cdn.poipiku.com/assets/emoji/1f496.png">
    """

    assert downloader.extract_poipiku_append_image_urls(html) == [
        "https://cdn.poipiku.com/a/b.png_640.jpg",
    ]


def test_load_cookie_header_reads_netscape_cookie_file(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        "poipiku.com\tTRUE\t/\tTRUE\t1813129665\tPOIPIKU_LK\tsecret\n",
        encoding="utf-8",
    )

    assert downloader.load_cookie_header(cookie_file) == "POIPIKU_LK=secret"
