"""Validate email-sync-intent/v0 YAML sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from implement_admission.scheme_resolve import resolve_schemed_packet_file

SCHEMA_VERSION = "email-sync-intent/v0"
FETCH_PATH_HEADLESS = "headless_export"
FETCH_PATH_BRIDGE = "bridge_pull"

BRIDGE_PULL_MESSAGE = (
    "fetch_path: bridge_pull is not supported by headless_export. "
    "Use email-bridge IMAP capture for HE accounts "
    "(see /mnt/torus/projects/email-bridge/). "
    "For M365 mailboxes, set fetch_path: headless_export."
)

SELECTOR_KINDS = frozenset(
    {"message_id", "graph_item_id", "conversation_id", "fingerprint"}
)


@dataclass(frozen=True, slots=True)
class Selector:
    kind: str
    value: str
    expand: bool = False
    fingerprint_from: str | None = None
    fingerprint_subject: str | None = None
    fingerprint_date: str | None = None


@dataclass(frozen=True, slots=True)
class EmailSyncIntent:
    schema_version: str
    idempotency_key: str
    matter_id: str
    sink_uri: str
    account: str
    fetch_path: str
    selectors: tuple[Selector, ...]
    source_path: str


def _resolve_intent_path(path_or_uri: str) -> Path:
    resolved = resolve_schemed_packet_file(path_or_uri)
    if resolved is not None:
        return resolved
    candidate = Path(path_or_uri).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"intent not found: {path_or_uri}")


def _parse_selector(raw: dict[str, Any]) -> Selector:
    kind = str(raw.get("kind", "")).strip()
    if kind not in SELECTOR_KINDS:
        raise ValueError(f"unsupported selector kind: {kind!r}")
    if kind == "fingerprint":
        return Selector(
            kind=kind,
            value="",
            fingerprint_from=str(raw.get("from", "") or ""),
            fingerprint_subject=str(raw.get("subject", "") or ""),
            fingerprint_date=str(raw.get("date", "") or ""),
        )
    value = str(raw.get("value", "") or "").strip()
    if not value:
        raise ValueError(f"selector {kind!r} requires non-empty value")
    expand = bool(raw.get("expand", False))
    return Selector(kind=kind, value=value, expand=expand)


def _validate_fetch_path(fetch_path: str) -> None:
    if fetch_path == FETCH_PATH_BRIDGE:
        raise ValueError(BRIDGE_PULL_MESSAGE)
    if fetch_path != FETCH_PATH_HEADLESS:
        raise ValueError(
            f"unsupported fetch_path {fetch_path!r}; expected {FETCH_PATH_HEADLESS!r}"
        )


def load_intent(path_or_uri: str) -> EmailSyncIntent:
    """Load and validate an email-sync intent sidecar."""
    source = _resolve_intent_path(path_or_uri)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("intent YAML must be a mapping")

    schema_version = str(data.get("schema_version", "") or "").strip()
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {schema_version!r}"
        )

    fetch_path = str(data.get("fetch_path", "") or "").strip()
    _validate_fetch_path(fetch_path)

    raw_selectors = data.get("selectors")
    if not isinstance(raw_selectors, list) or not raw_selectors:
        raise ValueError("selectors must be a non-empty list")

    selectors = tuple(_parse_selector(item) for item in raw_selectors)

    for field_name in ("idempotency_key", "matter_id", "sink_uri", "account"):
        if not str(data.get(field_name, "") or "").strip():
            raise ValueError(f"{field_name} is required")

    return EmailSyncIntent(
        schema_version=schema_version,
        idempotency_key=str(data["idempotency_key"]).strip(),
        matter_id=str(data["matter_id"]).strip(),
        sink_uri=str(data["sink_uri"]).strip(),
        account=str(data["account"]).strip(),
        fetch_path=fetch_path,
        selectors=selectors,
        source_path=str(source),
    )
