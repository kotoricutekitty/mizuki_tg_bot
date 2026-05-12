from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import BotConfig
from .media import media_kind
from .url_utils import provider_for_url


@dataclass(frozen=True)
class SafetyDecision:
    rating: str
    score: float | None = None
    reason: str = ""
    checked_count: int = 0


class ImageSafetyDetector(Protocol):
    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int]:
        ...


class NoopImageSafetyDetector:
    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int]:
        return None, 0


class NudeNetImageSafetyDetector:
    def __init__(self) -> None:
        try:
            from nudenet import NudeDetector
        except Exception as exc:  # pragma: no cover - depends on optional runtime package
            raise RuntimeError("nudenet is not installed") from exc
        self.detector = NudeDetector()

    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int]:
        if not image_paths:
            return None, 0
        scores = await asyncio.to_thread(self._score_images_sync, image_paths)
        if not scores:
            return None, 0
        return max(scores), len(scores)

    def _score_images_sync(self, image_paths: list[str]) -> list[float]:
        scores: list[float] = []
        for image_path in image_paths:
            try:
                detections = self.detector.detect(image_path)
            except Exception as exc:  # pragma: no cover - model/runtime specific
                logging.warning("NSFW image detection failed for %s: %s", image_path, exc)
                continue
            scores.append(max((detection_score(item) for item in detections), default=0.0))
        return scores


def detection_score(item: dict[str, Any]) -> float:
    label = str(item.get("class") or item.get("label") or "").upper()
    score = float(item.get("score") or item.get("confidence") or 0.0)
    adult_labels = (
        "FEMALE_BREAST_EXPOSED",
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "BUTTOCKS_EXPOSED",
        "ANUS_EXPOSED",
    )
    if label in adult_labels:
        return score
    if "EXPOSED" in label and not any(part in label for part in ("FACE", "FEET")):
        return score
    return 0.0


async def classify_safety(
    *,
    config: BotConfig,
    url: str,
    media_paths: list[str],
    metadata: dict[str, Any],
    detector: ImageSafetyDetector | None,
) -> SafetyDecision:
    if not config.r18_routing_enabled:
        return SafetyDecision("safe", reason="r18 routing disabled")

    provider = provider_for_url(url)
    metadata_reason = metadata_r18_reason(metadata)
    if metadata_reason:
        return SafetyDecision("r18", reason=metadata_reason)

    if provider == "pixiv":
        return SafetyDecision("safe", reason="pixiv metadata has no r18 marker")

    if not config.nsfw_detection_enabled:
        return SafetyDecision("safe", reason="image detection disabled")

    image_paths = [path for path in media_paths if media_kind(path) == "photo" and Path(path).exists()]
    if provider == "x":
        image_paths = image_paths[: config.nsfw_twitter_max_images]
    elif provider == "poipiku":
        image_paths = image_paths
    else:
        return SafetyDecision("safe", reason=f"{provider} does not use image detection")

    if not image_paths:
        return SafetyDecision("uncertain", reason="no images available for nsfw detection")
    if detector is None:
        return SafetyDecision("uncertain", reason="nsfw detector unavailable")

    score, checked_count = await detector.score_images(image_paths)
    if score is None:
        return SafetyDecision("uncertain", reason="nsfw detector returned no score", checked_count=checked_count)
    if score >= config.nsfw_high_threshold:
        return SafetyDecision("r18", score=score, reason="nsfw score above high threshold", checked_count=checked_count)
    if score <= config.nsfw_low_threshold:
        return SafetyDecision("safe", score=score, reason="nsfw score below low threshold", checked_count=checked_count)
    return SafetyDecision("uncertain", score=score, reason="nsfw score is uncertain", checked_count=checked_count)


def metadata_r18_reason(metadata: dict[str, Any]) -> str | None:
    for key in ("possibly_sensitive", "sensitive", "nsfw", "adult"):
        if boolish(metadata.get(key)):
            return f"metadata {key}=true"
    x_restrict = metadata.get("x_restrict")
    if str(x_restrict) in {"1", "2"}:
        return f"pixiv x_restrict={x_restrict}"
    lowered = " ".join(flatten_metadata_values(metadata)).lower()
    if any(marker in lowered for marker in ("r-18g", "r18g", "r-18", "r18", "nsfw", "adult")):
        return "metadata contains r18 marker"
    return None


def boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "sensitive", "nsfw", "adult"}
    return False


def flatten_metadata_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(flatten_metadata_values(item))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(flatten_metadata_values(item))
        return values
    return [str(value)]


def create_image_safety_detector(config: BotConfig) -> ImageSafetyDetector | None:
    if not config.nsfw_detection_enabled:
        return NoopImageSafetyDetector()
    try:
        return NudeNetImageSafetyDetector()
    except RuntimeError as exc:
        logging.warning("NSFW detection enabled but unavailable: %s", exc)
        return None
