from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi"}
MAX_PHOTO_SIZE = 4 * 1024 * 1024
MAX_VIDEO_SIZE = 50 * 1024 * 1024


def compress_image(file_path: str | Path, max_size: int = MAX_PHOTO_SIZE) -> BytesIO:
    img = Image.open(file_path)
    original_format = img.format or "JPEG"
    quality = 95
    while True:
        buffer = BytesIO()
        if original_format == "PNG" and img.mode in ("RGBA", "RGB"):
            if img.mode == "RGBA":
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
        else:
            img.save(buffer, format=original_format, quality=quality, optimize=True)
        size = buffer.tell()
        if size <= max_size or quality <= 10:
            break
        quality -= 5
    buffer.seek(0)
    return buffer


def media_kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return "document"
