"""Todo-keyed consult-provenance record — sole writer and gate SoT.

Charter-root JSON (``charter-consult-provenance/{root_id}.json``) stays a
sibling home. This module owns the todo-keyed record the implement-ready
gate reads. The record carries the (model identity, effort rung) independence
key written at consult harvest. Display-cache attrs are stamped here and have
no gate effect.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text
from effort_vocabulary.core import PROVIDER_EXTENDED, WIRE_LADDER

from implement_admission.check_review_substrate import (
    UNKNOWN_MODEL_IDENTITY,
    model_identity,
)
from implement_admission.closeout_helpers import cortex_files_root, workspaces_root

logger = logging.getLogger(__name__)

TODO_CONSULT_PROVENANCE_DIR = "notes/system/threads/todo-consult-provenance"
WRITER_ID = "implement_admission.commit_todo_consult_provenance"

REQUIRED_FIELDS: tuple[str, ...] = (
    "todo",
    "consult_thread",
    "verdict",
    "adjudication_assertion_id",
    "consultant_model",
    "consultant_substrate",
    "archive_uri",
    "archive_sha256",
    "satellite_execution_id",
    "stargate_execution_id",
    "written_by",
    "written_at",
)

VERDICT_ENUM: frozenset[str] = frozenset(
    {
        "ADMIT",
        "ADMIT_WITH_AMENDMENTS",
        "RATIFY",
        "RATIFY_WITH_CONDITIONS",
        "ADOPT",
        "REJECT",
        "REVISE",
    }
)

_THREAD_TURN_RE_PREFIX = "agent-bus:"
DISPLAY_CACHE_KEYS: tuple[str, ...] = (
    "consult_thread",
    "verdict",
    "consultant_model",
    "consultant_effort",
    "consultant_substrate",
)

_EFFORT_TOKENS = frozenset(WIRE_LADDER) | PROVIDER_EXTENDED


def todo_slug(todo_id: str) -> str:
    """Return the filesystem key for a todo-keyed provenance record path."""
    return (todo_id or "").strip().removeprefix("todo:")


def todo_consult_provenance_uri(todo_id: str) -> str:
    """Return the cortex:// URI for the todo-keyed record the gate reads."""
    return f"cortex://{TODO_CONSULT_PROVENANCE_DIR}/{todo_slug(todo_id)}.json"


def todo_consult_provenance_path(todo_id: str, *, root: Path | None = None) -> Path:
    """Return the on-disk path under the cortex files root for ``todo_id``."""
    files_root = root if root is not None else cortex_files_root()
    return files_root / TODO_CONSULT_PROVENANCE_DIR / f"{todo_slug(todo_id)}.json"


def _consult_thread_has_turn(value: str) -> bool:
    text = (value or "").strip()
    if not text.startswith(_THREAD_TURN_RE_PREFIX) or "#" not in text:
        return False
    _, turn = text.split("#", 1)
    return turn.isdigit()


def _archive_bytes(uri: str, *, files_root: Path | None = None) -> bytes | None:
    text = (uri or "").strip()
    if not text:
        return None
    if text.startswith("cortex://"):
        path = (files_root or cortex_files_root()) / text.removeprefix("cortex://")
    elif text.startswith("workspaces://"):
        rest = text.removeprefix("workspaces://")
        projects = workspaces_root()
        if projects.name == "universal-llm-gateway":
            projects = projects.parent
        path = projects / rest
    else:
        path = Path(text)
    if not path.is_file():
        return None
    return path.read_bytes()


def archive_sha256_hex(data: bytes) -> str:
    """Return the hex digest stored as ``archive_sha256`` on the todo record."""
    return hashlib.sha256(data).hexdigest()


def structural_gaps(
    record: Mapping[str, Any] | None,
    *,
    expected_todo: str | None = None,
    files_root: Path | None = None,
) -> list[str]:
    """Return field or sha problems that make a present record unverifiable."""
    if not isinstance(record, Mapping):
        return ["record"]
    gaps: list[str] = []
    for key in REQUIRED_FIELDS:
        value = record.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            gaps.append(key)
    todo = str(record.get("todo") or "").strip()
    if expected_todo and todo and todo != expected_todo:
        gaps.append("todo_mismatch")
    thread = str(record.get("consult_thread") or "").strip()
    if thread and not _consult_thread_has_turn(thread):
        gaps.append("consult_thread_turn")
    verdict = str(record.get("verdict") or "").strip().upper()
    if record.get("verdict") is not None and verdict not in VERDICT_ENUM:
        gaps.append("verdict_enum")
    adj = record.get("adjudication_assertion_id")
    if adj is not None:
        try:
            if int(adj) <= 0:
                gaps.append("adjudication_assertion_id")
        except (TypeError, ValueError):
            gaps.append("adjudication_assertion_id")
    uri = str(record.get("archive_uri") or "").strip()
    expected_sha = str(record.get("archive_sha256") or "").strip().lower()
    if uri and expected_sha:
        body = _archive_bytes(uri, files_root=files_root)
        if body is None or archive_sha256_hex(body) != expected_sha:
            gaps.append("archive_sha256")
    model = str(record.get("consultant_model") or "").strip()
    if model and model == UNKNOWN_MODEL_IDENTITY:
        gaps.append("consultant_model_unknown")
    elif model and model_identity(model) != model:
        gaps.append("consultant_model_unfolded")
    if "consultant_effort" not in record:
        gaps.append("consultant_effort")
    else:
        effort = record["consultant_effort"]
        if effort is not None and str(effort) not in _EFFORT_TOKENS:
            gaps.append("consultant_effort_enum")
    return gaps


def load_todo_consult_provenance(
    todo_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Load the todo-keyed provenance record, or return ``None`` when absent."""
    path = todo_consult_provenance_path(todo_id, root=root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _stamp_display_cache(todo_id: str, record: Mapping[str, Any]) -> bool:
    """Best-effort display-cache attrs. Never a second writer of the record."""
    cache = {key: record.get(key) for key in DISPLAY_CACHE_KEYS}
    try:
        from cortex_store.dispatch_ops.ops_entities import (
            _op_entity_get,
            _op_entity_update,
        )
    except ImportError:
        logger.debug("display-cache stamp skipped; cortex_store unavailable")
        return False
    entity = _op_entity_get(entity_id=todo_id, intent="full")
    if not isinstance(entity, dict) or entity.get("error"):
        logger.debug("display-cache stamp skipped; entity %s unreadable", todo_id)
        return False
    raw = entity.get("attributes")
    attrs = dict(raw) if isinstance(raw, dict) else {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            attrs = parsed
    attrs.update({k: v for k, v in cache.items() if v is not None})
    result = _op_entity_update(entity_id=todo_id, attributes=attrs)
    return isinstance(result, dict) and "error" not in result


def _cross_link_charter_record(record: Mapping[str, Any]) -> None:
    """Stamp ``source_ref`` on the charter-root JSON when a root id is present."""
    root_id = str(record.get("charter_root_id") or "").strip()
    todo = str(record.get("todo") or "").strip()
    if not root_id or not todo:
        return
    rel = Path("notes/system/threads/charter-consult-provenance") / f"{root_id}.json"
    path = cortex_files_root() / rel
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("source_ref") == todo:
        return
    payload["source_ref"] = todo
    durable_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True),
        retain_store_root=cortex_files_root(),
    )


def commit_todo_consult_provenance(
    record: Mapping[str, Any],
    *,
    stamp_cache: bool = True,
    files_root: Path | None = None,
) -> str | None:
    """Persist the todo-keyed record when every required field is in hand.

    Sole production writer of ``todo-consult-provenance/{slug}.json``. Refuses
    when thread turn, archive sha, or adjudication id is missing. Emits
    ``consult.provenance.recorded`` after a successful write. Optionally stamps
    the four display-cache attrs and cross-links a charter-root sibling.
    """
    gaps = structural_gaps(record, files_root=files_root)
    if gaps:
        logger.info("todo consult provenance refused gaps=%s", ",".join(gaps))
        return None
    todo = str(record["todo"]).strip()
    payload = dict(record)
    payload.setdefault("written_by", WRITER_ID)
    payload.setdefault("written_at", datetime.now(UTC).isoformat())
    content = json.dumps(payload, indent=2, sort_keys=True)
    path = todo_consult_provenance_path(todo, root=files_root)
    durable_write_text(
        path, content, retain_store_root=files_root or cortex_files_root()
    )
    uri = todo_consult_provenance_uri(todo)
    if stamp_cache:
        _stamp_display_cache(todo, payload)
    _cross_link_charter_record(payload)
    try:
        from implement_admission.consult_provenance_events import (
            emit_consult_provenance_recorded,
        )

        emit_consult_provenance_recorded(
            todo=todo,
            consult_thread=str(payload["consult_thread"]),
            archive_sha256=str(payload["archive_sha256"]),
            adjudication_assertion_id=int(payload["adjudication_assertion_id"]),
            written_by=str(payload["written_by"]),
        )
    except Exception:  # noqa: BLE001 — advisory emit must not roll back the write
        logger.debug("consult.provenance.recorded emit failed", exc_info=True)
    return uri


__all__ = [
    "DISPLAY_CACHE_KEYS",
    "REQUIRED_FIELDS",
    "TODO_CONSULT_PROVENANCE_DIR",
    "VERDICT_ENUM",
    "WRITER_ID",
    "archive_sha256_hex",
    "commit_todo_consult_provenance",
    "load_todo_consult_provenance",
    "structural_gaps",
    "todo_consult_provenance_path",
    "todo_consult_provenance_uri",
    "todo_slug",
]
