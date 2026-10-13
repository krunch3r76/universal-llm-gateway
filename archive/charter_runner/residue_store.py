"""On-disk last-residue persistence for the charter admission thrash gate.

Extracted from ``residue_fingerprint`` so the gate evaluator stays under the
modularization red band. Callers keep importing load/save via that module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .residue_witness import ResidueRecord

logger = get_logger(__name__)


def _default_store_dir() -> Path:
    return Path.home() / ".local" / "share" / "charter-runner" / "last-residue"


def store_path(root_id: str, *, store_dir: Path | None = None) -> Path:
    """Return the on-disk JSON path for a root's last-residue record."""
    base = store_dir if store_dir is not None else _default_store_dir()
    return base / f"{root_id}.json"


def load_residue_record(
    root_id: str, *, store_dir: Path | None = None
) -> ResidueRecord | None:
    """Load last residue for ``root_id``; corrupt/missing store ⇒ first-window None."""
    from .residue_witness import ResidueRecord

    path = store_path(root_id, store_dir=store_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.warning(
            "charter-runner residue store unreadable root=%s path=%s — first-window",
            root_id,
            path,
        )
        return None
    if not isinstance(raw, dict):
        return None
    record = ResidueRecord.from_dict(raw)
    if record is None:
        logger.warning(
            "charter-runner residue store corrupt root=%s path=%s — first-window",
            root_id,
            path,
        )
    return record


def save_residue_record(
    root_id: str,
    record: ResidueRecord,
    *,
    store_dir: Path | None = None,
) -> None:
    """Persist residue after a skip or W10 consume so the next tick can compare."""
    path = store_path(root_id, store_dir=store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clear_residue_record(root_id: str, *, store_dir: Path | None = None) -> None:
    """Drop last-residue so a recovery CHECKPOINT can admit without thrash skip."""
    store_path(root_id, store_dir=store_dir).unlink(missing_ok=True)


__all__ = [
    "clear_residue_record",
    "load_residue_record",
    "save_residue_record",
    "store_path",
]
