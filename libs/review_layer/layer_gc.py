"""TTL garbage collection for cortex review-layer directories."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

from review_layer.layer_paths import PARTIAL_SUFFIX, REVIEW_LAYERS_PREFIX

_STALE_PARTIAL_AGE_S = 3600
_MANIFEST_FALLBACK_AGE = timedelta(hours=24)


def gc_expired_layers(root: Path | None = None, *, now: datetime | None = None) -> int:
    """Delete expired review layers and stale partial dirs."""
    base = (root or cortex_files_root() / REVIEW_LAYERS_PREFIX).resolve()
    if not base.is_dir():
        return 0
    now = now or datetime.now(UTC)
    now_ts = now.timestamp()
    removed = 0
    for child in base.iterdir():
        name = child.name
        if name.endswith(PARTIAL_SUFFIX):
            try:
                age = now_ts - child.stat().st_mtime
            except OSError:
                continue
            if age >= _STALE_PARTIAL_AGE_S:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            mtime_age = now_ts - child.stat().st_mtime
        except OSError:
            mtime_age = _MANIFEST_FALLBACK_AGE.total_seconds() + 1
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        expired = False
        if isinstance(payload, dict):
            expires_raw = str(payload.get("expires_at") or "")
            if expires_raw:
                try:
                    expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=UTC)
                    expired = expires < now
                except ValueError:
                    expired = mtime_age >= _MANIFEST_FALLBACK_AGE.total_seconds()
            else:
                expired = mtime_age >= _MANIFEST_FALLBACK_AGE.total_seconds()
        else:
            expired = mtime_age >= _MANIFEST_FALLBACK_AGE.total_seconds()
        if expired:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
