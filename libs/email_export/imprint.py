"""Sparse correspondence imprint from matter-tree .eml files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from document_text.eml import parse_eml_file
from implement_admission.closeout_helpers import cortex_files_root
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from email_export.sink import content_hash

ImprintStatus = Literal["created", "already_present", "imprint_failed"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CORTEX_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class ImprintResult:
    status: ImprintStatus
    entity_id: str | None = None
    error: str | None = None
    planned_payload: dict[str, Any] | None = None


def correspondence_id_for_message(message_id: str) -> str:
    """Deterministic correspondence entity id derived from a Message-ID."""
    slug = _SLUG_RE.sub("-", message_id.lower()).strip("-")[:120] or "unknown"
    return f"correspondence:{slug}"


def source_uri_for_eml(eml_path: Path) -> str:
    """Prefer cortex:// when under cortex files root; else file:// absolute."""
    resolved = eml_path.expanduser().resolve()
    cortex_root = cortex_files_root().resolve()
    try:
        rel = resolved.relative_to(cortex_root)
        return f"cortex://{rel.as_posix()}"
    except ValueError:
        return f"file://{resolved}"


def _build_payload(
    *,
    entity_id: str,
    message_id: str,
    subject: str,
    sender: str,
    email_date: str,
    eml_path: Path,
    digest: str,
    link_to: str | None,
) -> dict[str, Any]:
    attributes: dict[str, str] = {
        "message_id": message_id,
        "sender": sender,
        "date": email_date,
        "subject": subject,
        "content_hash": digest,
        "eml_path": str(eml_path),
    }
    if link_to:
        attributes["link_to"] = link_to
    return {
        "id": entity_id,
        "type": "correspondence",
        "name": subject or message_id,
        "source_uri": source_uri_for_eml(eml_path),
        "attributes": attributes,
    }


def _entity_exists(client: Any, entity_id: str) -> bool:
    encoded = quote(entity_id, safe="")
    resp = client.get(f"/entities/{encoded}?intent=card")
    return resp.status_code == 200


def imprint_eml(
    eml_path: Path | str,
    *,
    matter_id: str | None = None,
    link_to: str | None = None,
    dry_run: bool = False,
    timeout: float = _CORTEX_TIMEOUT,
) -> ImprintResult:
    """Create or find sparse correspondence:* from a sink .eml file."""
    path = Path(eml_path).expanduser().resolve()
    if not path.is_file():
        return ImprintResult(
            status="imprint_failed",
            error=f"eml not found: {path}",
        )

    try:
        parsed = parse_eml_file(path)
    except OSError as exc:
        return ImprintResult(status="imprint_failed", error=str(exc))

    message_id = (parsed.message_id or "").strip()
    if not message_id:
        return ImprintResult(
            status="imprint_failed",
            error="missing Message-ID header",
        )

    entity_id = correspondence_id_for_message(message_id)
    digest = content_hash(path.read_bytes())
    resolved_link = link_to or matter_id
    payload = _build_payload(
        entity_id=entity_id,
        message_id=message_id,
        subject=parsed.subject,
        sender=parsed.sender,
        email_date=parsed.email_date,
        eml_path=path,
        digest=digest,
        link_to=resolved_link,
    )

    if dry_run:
        return ImprintResult(
            status="created",
            entity_id=entity_id,
            planned_payload=payload,
        )

    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=timeout) as client:
            if _entity_exists(client, entity_id):
                return ImprintResult(
                    status="already_present",
                    entity_id=entity_id,
                )
            resp = client.post("/entities", json=payload)
            if resp.status_code == 409:
                return ImprintResult(
                    status="already_present",
                    entity_id=entity_id,
                )
            if resp.status_code >= 400:
                detail = resp.text.strip() or f"HTTP {resp.status_code}"
                return ImprintResult(
                    status="imprint_failed",
                    entity_id=entity_id,
                    error=detail,
                )
            resp.raise_for_status()
    except Exception as exc:
        return ImprintResult(
            status="imprint_failed",
            entity_id=entity_id,
            error=str(exc),
        )

    return ImprintResult(status="created", entity_id=entity_id)
