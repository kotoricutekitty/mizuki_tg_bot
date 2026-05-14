from __future__ import annotations

import re

URL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/\w+/status/\d+"),
    re.compile(r"https?://(?:www\.)?pixiv\.net/(?:en/)?artworks/\d+"),
    re.compile(r"https?://(?:www\.)?poipiku\.com/(?:UserIllustShow\.jsp\?illust_id=\d+|\d+/\d+\.html)"),
    re.compile(r"https?://(?:www\.)?danbooru\.donmai\.us/posts/\d+(?:[?#][^\s]*)?"),
]


def normalize_url(url: str) -> str:
    normalized = re.sub(
        r"https?://(?:www\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/",
        "https://twitter.com/",
        url,
    )
    if "twitter.com/" in normalized and "/status/" in normalized:
        normalized = re.sub(r"(/status/\d+).*", r"\1", normalized)
    normalized = re.sub(
        r"https?://(?:www\.)?danbooru\.donmai\.us/posts/(\d+).*",
        r"https://danbooru.donmai.us/posts/\1",
        normalized,
    )
    return normalized


def twitter_status_id(url: str) -> str | None:
    match = re.search(
        r"https?://(?:www\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/(?:[^/?#]+|i)/status/(\d+)",
        url,
    )
    return match.group(1) if match else None


def extract_urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    for pattern in URL_PATTERNS:
        urls.extend(pattern.findall(text))
    return [normalize_url(url) for url in urls]


def provider_for_url(url: str) -> str:
    lowered = url.lower()
    if "danbooru.donmai.us" in lowered:
        return "danbooru"
    if "pixiv.net" in lowered:
        return "pixiv"
    if "poipiku.com" in lowered:
        return "poipiku"
    if any(host in lowered for host in ("twitter.com", "x.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com")):
        return "x"
    return "unknown"
