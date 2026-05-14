from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Protocol

POIPIKU_PLACEHOLDER_SHA256 = {
    "6397640b9ca8675c94c9357e68dc4c159dce0aba9120e8808e303916f4dc9f37",
}


class Downloader(Protocol):
    async def download_media(self, url: str) -> tuple[list[str], dict]:
        ...


class GalleryDownloader:
    def __init__(
        self,
        media_dir: Path,
        cookies_path: Path | None = None,
        danbooru_username: str = "",
        danbooru_password: str = "",
    ):
        self.media_dir = media_dir
        self.cookies_path = cookies_path
        self.danbooru_username = danbooru_username
        self.danbooru_password = danbooru_password

    async def download_media(self, url: str) -> tuple[list[str], dict]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = self.media_dir / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata: dict = {}

        twitter_pattern = re.compile(r"https?://(?:www\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/\w+/status/\d+")
        if twitter_pattern.match(url):
            return await self._download_twitter(url, output_dir, metadata)
        return await self._download_gallery_dl(url, output_dir, metadata)

    async def _download_twitter(self, url: str, output_dir: Path, metadata: dict) -> tuple[list[str], dict]:
        logging.info("使用fxtwitter处理Twitter链接: %s", url)
        match = re.search(r"/status/(\d+)", url)
        if not match:
            return [], metadata
        api_url = f"https://api.fxtwitter.com/i/status/{match.group(1)}"
        try:
            data = await asyncio.to_thread(read_json_url, api_url)
            tweet = data.get("tweet", {})
            metadata["author_name"] = tweet.get("author", {}).get("name", "")
            metadata["text"] = tweet.get("text", "")
            metadata["canonical_url"] = url
            for key in ("possibly_sensitive", "sensitive", "nsfw", "adult"):
                if key in tweet:
                    metadata[key] = tweet.get(key)
            media_files: list[str] = []
            media = tweet.get("media", {})
            all_media = media.get("photos", []) + media.get("videos", [])
            for i, item in enumerate(all_media):
                media_url = item.get("url") or item.get("variant_url")
                if not media_url:
                    continue
                ext = media_url.split("?")[0].split(".")[-1].lower()
                if ext not in ["jpg", "jpeg", "png", "gif", "mp4", "webm"]:
                    ext = "jpg"
                filepath = output_dir / f"{i + 1}.{ext}"
                await asyncio.to_thread(download_url, media_url, filepath)
                media_files.append(str(filepath))
                logging.info("下载成功: %s", filepath.name)
            return media_files, metadata
        except Exception as exc:
            logging.error("fxtwitter处理失败: %s", exc)
            return [], metadata

    async def _download_gallery_dl(self, url: str, output_dir: Path, metadata: dict) -> tuple[list[str], dict]:
        try:
            command = [
                "gallery-dl",
                "-d",
                str(output_dir),
                "--write-metadata",
            ]
            if self.cookies_path and self.cookies_path.exists():
                command.extend(["--cookies", str(self.cookies_path)])
            if "danbooru.donmai.us" in url and self.danbooru_username and self.danbooru_password:
                command.extend([
                    "-o",
                    f"extractor.danbooru.username={self.danbooru_username}",
                    "-o",
                    f"extractor.danbooru.password={self.danbooru_password}",
                ])
            command.append(url)
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logging.error("gallery-dl error: %s", stderr.decode(errors="ignore"))
                return [], metadata

            media_files: list[str] = []
            for root, dirs, files in os.walk(output_dir):
                dirs.sort()
                for file in sorted(files):
                    file_path = Path(root) / file
                    ext = file.lower().split(".")[-1]
                    if file.endswith("_metadata.json") or file.endswith(".json"):
                        self._merge_metadata(file_path, url, metadata)
                    elif ext in ["jpg", "jpeg", "png", "gif", "mp4", "webm"]:
                        media_files.append(str(file_path))
                    elif ext == "zip":
                        converted = await self._convert_ugoira(file_path)
                        media_files.append(str(converted or file_path))
            media_files = sorted(media_files)
            if "poipiku.com" in url:
                media_files = filter_poipiku_placeholders(media_files)
                if not media_files:
                    media_files = await self._download_poipiku_append_files(url, output_dir)
            if metadata and "canonical_url" not in metadata:
                metadata["canonical_url"] = url
            return media_files, metadata
        except Exception as exc:
            logging.error("Download error for %s: %s", url, exc)
            return [], metadata

    async def _download_poipiku_append_files(self, url: str, output_dir: Path) -> list[str]:
        if not self.cookies_path or not self.cookies_path.exists():
            return []
        match = re.search(r"poipiku\.com/(\d+)/(\d+)\.html", url)
        if not match:
            return []
        user_id, post_id = match.groups()
        try:
            return await asyncio.to_thread(
                download_poipiku_append_files,
                url,
                user_id,
                post_id,
                self.cookies_path,
                output_dir,
            )
        except Exception as exc:
            logging.error("Poipiku fallback download failed for %s: %s", url, exc)
            return []

    def _merge_metadata(self, file_path: Path, url: str, metadata: dict) -> None:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("读取元数据文件失败: %s", exc)
            return
        if "pixiv" in url:
            metadata["author_name"] = data.get("user", {}).get("name", "")
            metadata["title"] = data.get("title", "")
            metadata["text"] = data.get("description", data.get("caption", ""))
            for key in ("x_restrict", "rating", "tags"):
                if key in data:
                    metadata[key] = data.get(key)
        elif "poipiku" in url:
            metadata["author_name"] = data.get("user_name", "")
            metadata["title"] = data.get("title", "")
            metadata["text"] = data.get("description", "")
            for key in ("age_limit", "rating", "tags", "nsfw", "adult"):
                if key in data:
                    metadata[key] = data.get(key)
        elif "danbooru.donmai.us" in url:
            metadata["author_name"] = danbooru_artist_name(data)
            metadata["title"] = ""
            metadata["text"] = ""
            post_id = data.get("id") or data.get("post_id")
            if post_id:
                metadata["canonical_url"] = f"https://danbooru.donmai.us/posts/{post_id}"
            for key in (
                "rating",
                "tag_string",
                "tag_string_artist",
                "tag_string_character",
                "tag_string_copyright",
                "tag_string_general",
                "tag_string_meta",
                "source",
                "pixiv_id",
                "md5",
                "file_ext",
                "image_width",
                "image_height",
            ):
                if key in data:
                    metadata[key] = data.get(key)
        metadata.setdefault("canonical_url", url)

    async def _convert_ugoira(self, file_path: Path) -> Path | None:
        metadata_file = Path(str(file_path) + ".json")
        if not metadata_file.exists():
            return None
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            is_ugoira = "ugoira_metadata" in data or "frames" in data or data.get("type") == "ugoira"
            if not is_ugoira:
                return None
            temp_dir = file_path.parent / "temp_ugoira"
            temp_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(file_path, "r") as zip_ref:
                zip_ref.extractall(temp_dir)
            delays = [frame["delay"] for frame in data.get("ugoira_metadata", {}).get("frames", data.get("frames", [])) if "delay" in frame]
            avg_delay = sum(delays) / len(delays) if delays else 50
            fps = 1000 / avg_delay
            output_mp4 = file_path.with_suffix(".mp4")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-framerate",
                str(fps),
                "-pattern_type",
                "sequence",
                "-i",
                str(temp_dir / "%06d.jpg"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "23",
                "-movflags",
                "+faststart",
                "-y",
                str(output_mp4),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0 and output_mp4.exists() and output_mp4.stat().st_size > 0:
                shutil.rmtree(temp_dir)
                file_path.unlink(missing_ok=True)
                metadata_file.unlink(missing_ok=True)
                return output_mp4
            logging.error("❌ 动图合成失败，ffmpeg返回码: %s", proc.returncode)
            logging.error("ffmpeg错误: %s", stderr.decode(errors="ignore")[:500])
            return None
        except Exception as exc:
            logging.error("❌ 处理动图异常: %s", exc, exc_info=True)
            return None


def read_json_url(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_url(url: str, filepath: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            filepath.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"download failed: {exc.code}") from exc


def danbooru_artist_name(data: dict) -> str:
    tag_string_artist = str(data.get("tag_string_artist") or "").strip()
    if tag_string_artist:
        return tag_string_artist.replace("_", " ")
    tags = data.get("tags")
    if isinstance(tags, dict):
        artists = tags.get("artist") or tags.get("artists")
        if isinstance(artists, list) and artists:
            return str(artists[0]).replace("_", " ")
    return ""


def download_poipiku_append_files(
    page_url: str,
    user_id: str,
    post_id: str,
    cookies_path: Path,
    output_dir: Path,
) -> list[str]:
    cookie_header = load_cookie_header(cookies_path)
    if not cookie_header:
        return []
    media_urls: list[str] = []
    for mode in ("0", "1"):
        response = post_poipiku_append_request(page_url, user_id, post_id, mode, cookie_header)
        html = str(response.get("html") or "")
        media_urls.extend(extract_poipiku_append_image_urls(html))
        if media_urls:
            break
        logging.warning("Poipiku fallback returned no media: result=%s html=%s", response.get("result_num"), html[:120])
    saved_files: list[str] = []
    fallback_dir = output_dir / "poipiku_fallback" / user_id
    fallback_dir.mkdir(parents=True, exist_ok=True)
    for index, media_url in enumerate(dedupe(media_urls), start=1):
        suffix = suffix_for_url(media_url)
        file_path = fallback_dir / f"{post_id}_{index}{suffix}"
        download_binary(media_url, file_path, referer=page_url)
        if file_path.stat().st_size <= 0 or is_poipiku_placeholder(file_path):
            file_path.unlink(missing_ok=True)
            continue
        saved_files.append(str(file_path))
        logging.info("Poipiku fallback downloaded: %s", file_path.name)
    return saved_files


def post_poipiku_append_request(
    page_url: str,
    user_id: str,
    post_id: str,
    mode: str,
    cookie_header: str,
) -> dict:
    data = urllib.parse.urlencode(
        {
            "UID": user_id,
            "IID": post_id,
            "PAS": "",
            "MD": mode,
            "TWF": "-1",
        }
    ).encode()
    req = urllib.request.Request(
        "https://poipiku.com/f/ShowAppendFileF.jsp",
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": page_url,
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": cookie_header,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_poipiku_append_image_urls(html: str) -> list[str]:
    urls = re.findall(r"https://cdn\.poipiku\.com[^\"' <)]+", unescape(html))
    media_urls: list[str] = []
    for url in urls:
        lowered = url.lower()
        if "/assets/" in lowered:
            continue
        if not any(ext in lowered for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
            continue
        media_urls.append(url)
    return dedupe(media_urls)


def load_cookie_header(cookies_path: Path) -> str:
    parts: list[str] = []
    try:
        content = cookies_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    stripped = content.strip()
    if stripped.startswith("["):
        try:
            cookies = json.loads(stripped)
        except json.JSONDecodeError:
            cookies = []
        for cookie in cookies:
            if isinstance(cookie, dict) and cookie.get("name") and cookie.get("value") is not None:
                parts.append(f"{cookie['name']}={cookie['value']}")
        return "; ".join(parts)
    lines = content.splitlines()
    for line in lines:
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            parts.append(f"{fields[5]}={fields[6]}")
            continue
        if "=" in line:
            parts.append(line.strip())
    return "; ".join(parts)


def download_binary(url: str, filepath: Path, referer: str | None = None) -> None:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            content_type = response.headers.get("content-type", "")
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"download failed: {exc.code}") from exc
    if content_type and not content_type.startswith("image/"):
        raise RuntimeError(f"download returned non-image content: {content_type}")
    filepath.write_bytes(data)


def suffix_for_url(url: str) -> str:
    filename = url.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    for suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        if suffix in filename:
            return suffix
    return ".jpg"


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def filter_poipiku_placeholders(media_files: list[str]) -> list[str]:
    filtered: list[str] = []
    for media_file in media_files:
        if is_poipiku_placeholder(Path(media_file)):
            logging.warning("Poipiku returned a placeholder image, ignoring it: %s", media_file)
            continue
        filtered.append(media_file)
    return filtered


def is_poipiku_placeholder(path: Path) -> bool:
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif"}:
        return False
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest in POIPIKU_PLACEHOLDER_SHA256
