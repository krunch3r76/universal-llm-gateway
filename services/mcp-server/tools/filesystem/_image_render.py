"""Image thumbnail rendering and shared-image write utilities."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from ._paths import _SANDBOX_ROOT, _SHARED_IMAGE_DIR, _SHARED_IMAGE_HOST_ROOT


def _render_thumbnail_bytes(
    src: Path, *, max_dimension: int, quality: int
) -> tuple[bytes, str, str]:
    from PIL import Image as PILImage

    with PILImage.open(src) as opened:
        original_size = f"{opened.width}x{opened.height}"
        opened.thumbnail((max_dimension, max_dimension), PILImage.Resampling.LANCZOS)
        if opened.mode in ("RGBA", "P"):
            rendered = opened.convert("RGB")
        else:
            rendered = opened

    thumb_size = f"{rendered.width}x{rendered.height}"
    buffer = io.BytesIO()
    rendered.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue(), original_size, thumb_size


def _shared_image_name(src: Path, *, max_dimension: int, quality: int) -> str:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", src.stem).strip("-.") or "image"
    rel_path = src.relative_to(_SANDBOX_ROOT).as_posix()
    stat = src.stat()
    fingerprint = hashlib.sha256(
        f"{rel_path}:{stat.st_mtime_ns}:{stat.st_size}:{max_dimension}:{quality}".encode()
    ).hexdigest()[:16]
    return f"{safe_stem}-{fingerprint}.jpg"


def _write_shared_image(filename: str, jpeg_bytes: bytes) -> tuple[Path, Path]:
    _SHARED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    shared_path = _SHARED_IMAGE_DIR / filename
    shared_path.write_bytes(jpeg_bytes)
    return shared_path, _SHARED_IMAGE_HOST_ROOT / filename
