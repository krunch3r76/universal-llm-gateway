"""Model-aware vision image resizing for OCR.

Given a raw image and a target vision model, selects a per-provider resize
profile, classifies the image as text-dominant or photo-dominant, and
re-encodes JPEG at a dimension/quality that fits within a token budget.

Design notes (agent-bus thread 557):
- Profiles are keyed on ``ModelId.provider`` — provider determines tokenizer
  and sweet-spot dimension, not the model variant.
- Classifier is PIL-only (no OpenCV, no LLM classifier). Saturation is the
  primary signal; edge density is the tie-breaker in the ambiguous band.
- Token estimators are local formulas (no provider API pre-flight). The xAI
  estimator is a proxy borrowed from Anthropic shape — callers should
  compare the returned estimate against real ``prompt_tokens`` at WARN
  level until we have ≥50 calibration calls.

This module is the single source of truth for vision resize across
mcp-server (``extract_document`` / ``extract_directory``) and cortex-api (documents/ocr).
Originally extracted from ``services/mcp-server/tools/_vision_resize.py``.
"""

from __future__ import annotations

import io
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from model_id import ModelId
from PIL import Image as PILImage
from PIL import ImageChops, ImageFilter, ImageStat

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classifier thresholds (agent-bus thread 557)
# Tune here if text/photo classification drifts on realistic inputs.
# ---------------------------------------------------------------------------
_SAT_TEXT_MAX = 12.0
"""Mean per-pixel saturation below this → text-dominant (scan/screenshot)."""

_SAT_PHOTO_MIN = 40.0
"""Mean per-pixel saturation above this → photo-dominant (camera/mixed)."""

_EDGE_TEXT_MIN = 25.0
"""In the ambiguous saturation band, mean edge intensity above this → text."""

_MIN_DIM_FLOOR = 640
"""Absolute minimum long-side dimension after step-down (legibility floor)."""

_STEP_DOWN_RATIO = 0.85
"""Per-iteration shrink when the estimated budget is exceeded."""


# ---------------------------------------------------------------------------
# Vision profiles (agent-bus thread 557)
# Hardcoded defaults. Tune via assertion-based feedback after ≥50 real calls.
# ---------------------------------------------------------------------------
def _openai_tokens(w: int, h: int) -> int:
    """OpenAI high-detail: 85 base + 170 per 512×512 tile."""
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return 85 + 170 * tiles


def _anthropic_tokens(w: int, h: int) -> int:
    """Anthropic: ≈ w·h / 750."""
    return (w * h) // 750


def _xai_tokens_proxy(w: int, h: int) -> int:
    """xAI: Anthropic-shaped proxy. Unverified — calibrate from real usage."""
    return (w * h) // 750


@dataclass(frozen=True)
class VisionProfile:
    """Per-provider vision resize profile.

    Attributes:
        provider: Cloud provider string (matches ``ModelId.provider``).
        sweet_spot_dim: Efficient long-side dimension — balance of cost and fidelity.
        hard_max_dim: Absolute cap. Beyond this the provider downscales internally.
        jpeg_quality_text: JPEG quality floor for text-dominant content.
        jpeg_quality_photo: JPEG quality floor for photo-dominant content.
        estimate_tokens: ``(w, h) -> int`` — image-only token estimator.
        default_budget: Fallback budget when caller passes ``token_budget=None``.
            Sized as ``estimate_tokens(sweet_spot_dim, sweet_spot_dim) * 1.1`` so
            it tracks profile edits automatically (agent-bus thread 557).
        is_proxy_estimator: True when the estimator is a placeholder awaiting
            calibration. Callers should WARN on estimate-vs-actual delta.
    """

    provider: str
    sweet_spot_dim: int
    hard_max_dim: int
    jpeg_quality_text: int
    jpeg_quality_photo: int
    estimate_tokens: Callable[[int, int], int]
    default_budget: int
    is_proxy_estimator: bool = False


def _profile_with_default_budget(
    *,
    provider: str,
    sweet_spot_dim: int,
    hard_max_dim: int,
    jpeg_quality_text: int,
    jpeg_quality_photo: int,
    estimate_tokens: Callable[[int, int], int],
    is_proxy_estimator: bool = False,
) -> VisionProfile:
    """Build a profile whose default_budget derives from the estimator.

    ``default_budget = ceil(estimate_tokens(sweet_spot²) * 1.1)`` — when the
    sweet spot moves, the default tracks it (agent-bus thread 557).
    """
    sweet_tokens = estimate_tokens(sweet_spot_dim, sweet_spot_dim)
    default_budget = math.ceil(sweet_tokens * 1.1)
    return VisionProfile(
        provider=provider,
        sweet_spot_dim=sweet_spot_dim,
        hard_max_dim=hard_max_dim,
        jpeg_quality_text=jpeg_quality_text,
        jpeg_quality_photo=jpeg_quality_photo,
        estimate_tokens=estimate_tokens,
        default_budget=default_budget,
        is_proxy_estimator=is_proxy_estimator,
    )


_OPENAI_PROFILE = _profile_with_default_budget(
    provider="openai",
    sweet_spot_dim=1536,
    hard_max_dim=2048,
    jpeg_quality_text=78,
    jpeg_quality_photo=88,
    estimate_tokens=_openai_tokens,
)
_ANTHROPIC_PROFILE = _profile_with_default_budget(
    provider="anthropic",
    sweet_spot_dim=1568,
    hard_max_dim=1568,
    jpeg_quality_text=80,
    jpeg_quality_photo=90,
    estimate_tokens=_anthropic_tokens,
)
_XAI_PROFILE = _profile_with_default_budget(
    provider="xai",
    sweet_spot_dim=1600,
    hard_max_dim=2048,
    jpeg_quality_text=82,
    jpeg_quality_photo=88,
    estimate_tokens=_xai_tokens_proxy,
    is_proxy_estimator=True,
)

_PROFILES: dict[str, VisionProfile] = {
    "openai": _OPENAI_PROFILE,
    "anthropic": _ANTHROPIC_PROFILE,
    "xai": _XAI_PROFILE,
}

_DEFAULT_PROFILE = _OPENAI_PROFILE
"""Fallback when the model's provider isn't in the registry."""


def profile_for_model(model: str) -> VisionProfile:
    """Return the vision profile for *model*.

    Parses via :class:`ModelId`; falls back to the OpenAI profile with a
    warning log when the provider is unknown.
    """
    try:
        parsed = ModelId.parse(model)
    except (ValueError, TypeError):
        logger.warning("Could not parse model %r; using default profile", model)
        return _DEFAULT_PROFILE

    provider = parsed.provider
    if provider is None:
        logger.warning(
            "Model %r has no provider (local model?); using default vision profile",
            model,
        )
        return _DEFAULT_PROFILE

    profile = _PROFILES.get(provider)
    if profile is None:
        logger.warning(
            "No vision profile for provider %r (model %r); using default",
            provider,
            model,
        )
        return _DEFAULT_PROFILE
    return profile


ContentType = Literal["text", "photo"]


def _mean_saturation(rgb: PILImage.Image) -> float:
    """Mean per-pixel saturation of *rgb* in the range [0, 255].

    Computes ``max(R,G,B) - min(R,G,B)`` per pixel via ``ImageChops`` (pure
    C path) and averages. Deterministic and faster than a Python pixel loop.
    """
    r, g, b = rgb.split()
    max_rgb = ImageChops.lighter(ImageChops.lighter(r, g), b)
    min_rgb = ImageChops.darker(ImageChops.darker(r, g), b)
    sat = ImageChops.subtract(max_rgb, min_rgb)
    return ImageStat.Stat(sat).mean[0]


def classify_content(img: PILImage.Image) -> ContentType:
    """Classify *img* as text-dominant or photo-dominant.

    Algorithm (agent-bus thread 557):
    1. Downsample to 512px thumb — classification only needs coarse stats.
    2. Compute mean per-pixel saturation = mean of ``max(R,G,B) - min(R,G,B)``.
    3. saturation < ``_SAT_TEXT_MAX`` → "text" (B&W scan, screenshot).
    4. saturation > ``_SAT_PHOTO_MIN`` → "photo" (camera/mixed content).
    5. Otherwise: edge density on grayscale decides
       (high edge mean → "text"; else "photo").

    Deterministic, ~3–8 ms on a 2000px image. Pillow-only, no OpenCV.
    """
    rgb = img.convert("RGB") if img.mode != "RGB" else img

    thumb = rgb.copy()
    thumb.thumbnail((512, 512), PILImage.Resampling.BILINEAR)

    sat_mean = _mean_saturation(thumb)
    if sat_mean < _SAT_TEXT_MAX:
        return "text"
    if sat_mean > _SAT_PHOTO_MIN:
        return "photo"

    gray = thumb.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]
    return "text" if edge_mean > _EDGE_TEXT_MIN else "photo"


def resize_to_budget(
    raw_bytes: bytes,
    *,
    model: str,
    token_budget: int | None = None,
) -> tuple[bytes, str, int]:
    """Resize and re-encode *raw_bytes* to fit *token_budget* for *model*.

    Returns ``(jpeg_bytes, "image/jpeg", estimated_tokens)``. The estimate is
    the *image-only* token prediction from the provider's profile — callers
    should compare it against actual ``prompt_tokens`` (minus small text
    overhead) at WARN level when ``profile.is_proxy_estimator`` is true.

    Algorithm:
    1. Select profile by ``ModelId.provider``.
    2. Classify content (text vs photo) → pick JPEG quality floor.
    3. Start at ``min(current_long_side, sweet_spot_dim)``.
    4. Iterate: estimate tokens at current (w, h); if > budget, shrink by
       ``_STEP_DOWN_RATIO`` and retry. Stop at ``_MIN_DIM_FLOOR``.
    5. Re-encode JPEG at the chosen dimension and quality.

    When budget=None, uses ``profile.default_budget`` (sweet-spot-derived,
    agent-bus thread 557).
    """
    profile = profile_for_model(model)
    budget = token_budget if token_budget is not None else profile.default_budget

    img = PILImage.open(io.BytesIO(raw_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    content_type = classify_content(img)
    jpeg_quality = (
        profile.jpeg_quality_text
        if content_type == "text"
        else profile.jpeg_quality_photo
    )

    orig_w, orig_h = img.size
    target_long = min(max(orig_w, orig_h), profile.sweet_spot_dim)

    # Iterative step-down to fit budget.
    while target_long >= _MIN_DIM_FLOOR:
        scale = target_long / max(orig_w, orig_h)
        w = max(1, int(orig_w * scale))
        h = max(1, int(orig_h * scale))
        est = profile.estimate_tokens(w, h)
        if est <= budget:
            break
        target_long = int(target_long * _STEP_DOWN_RATIO)
    else:
        # Loop exited without break: floor the dimensions.
        scale = _MIN_DIM_FLOOR / max(orig_w, orig_h)
        w = max(1, int(orig_w * scale))
        h = max(1, int(orig_h * scale))
        est = profile.estimate_tokens(w, h)
        logger.warning(
            "resize_to_budget hit floor %dpx for %s; estimated %d tokens "
            "exceeds budget %d (provider=%s, content=%s)",
            _MIN_DIM_FLOOR,
            model,
            est,
            budget,
            profile.provider,
            content_type,
        )

    if (w, h) != (orig_w, orig_h):
        img = img.resize((w, h), PILImage.Resampling.LANCZOS)
        logger.info(
            "Resized %dx%d → %dx%d for %s (provider=%s, content=%s, "
            "quality=%d, est_tokens=%d, budget=%d)",
            orig_w,
            orig_h,
            w,
            h,
            model,
            profile.provider,
            content_type,
            jpeg_quality,
            est,
            budget,
        )

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    return buf.getvalue(), "image/jpeg", est
