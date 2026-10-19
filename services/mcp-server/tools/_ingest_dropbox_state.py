"""Ingest state JSON paths, flock, and persistence."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._extract_document_helpers import atomic_write
from ._file_helpers import FILES_ROOT, resolve_files_path
from ._sidecar_schema import SIDECAR_SUFFIX, parse_leading_frontmatter

CONFIG_REL = "configs/ocr-review-v1.yaml"
STATE_DIR_REL = "notes/system/ingest-state"
LOCKS_DIR_REL = ".locks"


def config_path() -> Path:
    return FILES_ROOT / CONFIG_REL


def state_dir() -> Path:
    return FILES_ROOT / STATE_DIR_REL


def state_path(source_sha256: str) -> Path:
    return state_dir() / f"{source_sha256}.json"


def lock_path(source_sha256: str) -> Path:
    return FILES_ROOT / LOCKS_DIR_REL / f"ingest-{source_sha256[:16]}.lock"


def entity_id_from_path(rel: str) -> str:
    import re

    from pathlib import Path

    slug = re.sub(r"[^a-z0-9]+", "-", Path(rel).stem.lower()).strip("-")
    return f"document:{slug or 'document'}"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_record(*, source_path: str, source_sha256: str) -> dict[str, Any]:
    return {
        "source_path": source_path,
        "source_sha256": source_sha256,
        "stage": "extract",
        "method": None,
        "extract_attempts": 0,
        "check_attempts": 0,
        "promote_attempts": 0,
        "terminal": None,
        "last_error": None,
        "crosscheck": None,
        "entity_id": None,
        "duplicate_paths": [],
        "updated_at": utc_now_iso(),
    }


def load_state(source_sha256: str) -> dict[str, Any] | None:
    path = state_path(source_sha256)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(source_sha256: str, record: dict[str, Any]) -> None:
    record["updated_at"] = utc_now_iso()
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    atomic_write(state_path(source_sha256), payload)


def canonical_sidecar_path(pdf_rel: str) -> Path:
    pdf = resolve_files_path(pdf_rel, root=FILES_ROOT)
    return pdf.parent / f"{pdf.name}{SIDECAR_SUFFIX}"


def read_sidecar(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    fm = parse_leading_frontmatter(text) or {}
    end = text.find("\n---\n", 4)
    body = text[end + 5 :].lstrip("\n") if end >= 0 else text
    return fm, body


def is_canonical_sidecar(path: Path, source_sha256: str) -> bool:
    if not path.is_file() or ".pages-" in path.name:
        return False
    fm, _body = read_sidecar(path)
    return (
        fm.get("canonical") is True
        and fm.get("partial") is False
        and fm.get("page_spec") == "all"
        and fm.get("source_sha256") == source_sha256
    )


def catch_all_failure(
    record: dict[str, Any],
    sha: str,
    stage: str,
    exc: Exception,
    *,
    max_err: int = 200,
) -> str:
    from mcp_events import record as emit

    key = f"{stage}_attempts"
    record[key] = int(record.get(key, 0)) + 1
    record["last_error"] = f"{exc.__class__.__name__}: {str(exc)[:max_err]}"
    emit(
        "mcp.ingest.file.failed",
        path=record.get("source_path"),
        stage=stage,
        error_class=exc.__class__.__name__,
    )
    if record[key] >= 3:
        record["terminal"] = "needs_human"
    save_state(sha, record)
    return stage


def append_duplicate_path(record: dict[str, Any], rel: str) -> None:
    paths = list(record.get("duplicate_paths") or [])
    if rel not in paths:
        paths.append(rel)
    record["duplicate_paths"] = paths


def iter_state_records() -> Iterator[dict[str, Any]]:
    directory = state_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


@contextmanager
def try_ingest_lock(source_sha256: str) -> Iterator[bool]:
    """Non-blocking exclusive flock; yields False when already held."""
    lp = lock_path(source_sha256)
    lp.parent.mkdir(parents=True, exist_ok=True)
    handle = lp.open("a+", encoding="utf-8")
    acquired = False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield True
    except BlockingIOError:
        yield False
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
