from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi"}
MAX_PHOTO_SIZE = 4 * 1024 * 1024
MAX_PUBLISH_PHOTO_SIZE = MAX_PHOTO_SIZE
MAX_VIDEO_SIZE = 50 * 1024 * 1024


def compress_image(file_path: str | Path, max_size: int = MAX_PHOTO_SIZE) -> BytesIO:
    img = Image.open(file_path)
    img.load()
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    while True:
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=90, optimize=True)
        size = buffer.tell()
        if size <= max_size:
            break
        width, height = img.size
        if width <= 640 or height <= 640:
            break
        img = img.resize((max(1, int(width * 0.85)), max(1, int(height * 0.85))), Image.Resampling.LANCZOS)
    buffer.seek(0)
    return buffer


def media_kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return "document"
