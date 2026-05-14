from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import BotConfig
from .media import media_kind
from .url_utils import provider_for_url


@dataclass(frozen=True)
class SafetyDecision:
    rating: str
    score: float | None = None
    class_name: str | None = None
    reason: str = ""
    checked_count: int = 0


class ImageSafetyDetector(Protocol):
    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int, str | None]:
        ...


class NoopImageSafetyDetector:
    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int, str | None]:
        return None, 0, None


class AnimeRatingImageSafetyDetector:
    def __init__(self, model_name: str = "mobilenetv3_sce_dist") -> None:
        try:
            from imgutils.validate import anime_rating_score
        except Exception as exc:  # pragma: no cover - depends on optional runtime package
            raise RuntimeError("dghs-imgutils is not installed") from exc
        self.model_name = model_name
        self.anime_rating_score: Callable[..., dict[str, float]] = anime_rating_score

    async def score_images(self, image_paths: list[str]) -> tuple[float | None, int, str | None]:
        if not image_paths:
            return None, 0, None
        results = await asyncio.to_thread(self._score_images_sync, image_paths)
        if not results:
            return None, 0, None
        score, class_name = max(results, key=lambda item: item[0])
        return score, len(results), class_name

    def _score_images_sync(self, image_paths: list[str]) -> list[tuple[float, str]]:
        scores: list[tuple[float, str]] = []
        for image_path in image_paths:
            try:
                rating_scores = self.anime_rating_score(image_path, model_name=self.model_name)
            except Exception as exc:  # pragma: no cover - model/runtime specific
                logging.warning("Anime rating detection failed for %s: %s", image_path, exc)
                continue
            r18_score = float(rating_scores.get("r18") or 0.0)
            predicted_label, _ = max(rating_scores.items(), default=("none", 0.0), key=lambda item: float(item[1]))
            scores.append((r18_score, str(predicted_label)))
        return scores


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

    score, checked_count, class_name = await detector.score_images(image_paths)
    if score is None:
        return SafetyDecision("uncertain", reason="anime rating detector returned no score", checked_count=checked_count)
    if score >= config.nsfw_high_threshold:
        return SafetyDecision("r18", score=score, class_name=class_name, reason="anime rating r18 score above high threshold", checked_count=checked_count)
    if score <= config.nsfw_low_threshold:
        return SafetyDecision("safe", score=score, class_name=class_name, reason="anime rating r18 score below low threshold", checked_count=checked_count)
    return SafetyDecision("uncertain", score=score, class_name=class_name, reason="anime rating r18 score is uncertain", checked_count=checked_count)


def metadata_r18_reason(metadata: dict[str, Any]) -> str | None:
    for key in ("possibly_sensitive", "sensitive", "nsfw", "adult"):
        if boolish(metadata.get(key)):
            return f"metadata {key}=true"
    x_restrict = metadata.get("x_restrict")
    if str(x_restrict) in {"1", "2"}:
        return f"pixiv x_restrict={x_restrict}"
    danbooru_rating = str(metadata.get("rating") or "").lower()
    if danbooru_rating in {"q", "e"}:
        return f"danbooru rating={danbooru_rating}"
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
        return AnimeRatingImageSafetyDetector(config.anime_rating_model)
    except RuntimeError as exc:
        logging.warning("Anime rating detection enabled but unavailable: %s", exc)
        return None
