"""Store epoch helpers — best-effort file identity (no store UUID without actuator edits)."""

from __future__ import annotations

from pathlib import Path


def file_store_epoch(path: Path) -> str:
    """Inode/size snapshot; mtime excluded so unrelated writes do not block idempotent replay."""
    if not path.exists():
        return "missing"
    st = path.stat()
    return f"{st.st_ino}:{st.st_size}"


def intent_tag_body(identity_key: str) -> str:
    """Raw intent line stored in bus turn bodies (case-sensitive, unescaped)."""
    return f"intent:{identity_key}\n"


def intent_tag_length(identity_key: str) -> int:
    return len(intent_tag_body(identity_key))
