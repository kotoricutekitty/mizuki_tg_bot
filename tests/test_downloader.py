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


def test_load_cookie_header_reads_browser_json_cookie_file(tmp_path: Path):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        '[{"domain": ".pixiv.net", "name": "PHPSESSID", "value": "user_session"},'
        '{"domain": ".pixiv.net", "name": "device_token", "value": "device"}]',
        encoding="utf-8",
    )

    assert downloader.load_cookie_header(cookie_file) == "PHPSESSID=user_session; device_token=device"


def test_danbooru_metadata_mapping(tmp_path: Path):
    metadata_file = tmp_path / "post_metadata.json"
    metadata_file.write_text(
        """
        {
          "id": 1234567,
          "rating": "e",
          "tag_string_artist": "artist_name",
          "tag_string_character": "character_name",
          "tag_string_copyright": "copyright_name",
          "source": "https://example.test/source",
          "md5": "abc"
        }
        """,
        encoding="utf-8",
    )
    metadata = {}
    gallery_downloader = downloader.GalleryDownloader(tmp_path)

    gallery_downloader._merge_metadata(metadata_file, "https://danbooru.donmai.us/posts/1234567?foo=bar", metadata)

    assert metadata["author_name"] == "artist name"
    assert metadata["text"] == ""
    assert metadata["rating"] == "e"
    assert metadata["tag_string_character"] == "character_name"
    assert metadata["canonical_url"] == "https://danbooru.donmai.us/posts/1234567"
