"""Store epoch helpers for owner lookup basis checks."""

from __future__ import annotations

from pathlib import Path


def file_store_epoch(path: Path) -> str:
    if not path.exists():
        return "missing"
    st = path.stat()
    return f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"


def escape_like_prefix(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def intent_tag_prefix(identity_key: str) -> str:
    return f"intent:{escape_like_prefix(identity_key)}\n"
