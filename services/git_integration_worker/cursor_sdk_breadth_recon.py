"""Breadth-recon Explore-default closeout verification (fold 3 / friction a:27477 sibling)."""

from __future__ import annotations

import json
import re
from typing import Any

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
    merge_relay_notes,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
)

_BREADTH_RECON_OWED_CONTRACTS = frozenset({"investigate", "light-bounded"})
_RECON_METHOD_RE = re.compile(
    r"(?im)^(?:\*\*)?recon[_ ]method(?:\*\*)?\s*[:|]\s*(?P<value>.+)$"
)
_IN_SEAT_ANTI_TRIGGER_RE = re.compile(
    r"(?im)(?:recon[_ ]method.*in-seat|anti-trigger|loci known|latency-sensitive)"
)
_DEVIATION = "recon:breadth_explore_not_used"


def _contract_from_wrapper(wrapper_text: str | None) -> str | None:
    if not wrapper_text:
        return None
    match = re.search(r"(?im)^contract:\s*(\S+)", wrapper_text)
    if match:
        return match.group(1).strip().lower()
    try:
        data = json.loads(wrapper_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("contract", "handoff_contract", "resolved_contract"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def packet_owes_breadth_recon(
    *,
    wrapper_text: str | None = None,
    contract: str | None = None,
) -> bool:
    """True when dispatch contract class typically owes breadth recon before implement."""
    resolved = (contract or _contract_from_wrapper(wrapper_text) or "").lower()
    if resolved in {"implement", "pure-mechanical", "propagate", "execute", "answer"}:
        return False
    if resolved in _BREADTH_RECON_OWED_CONTRACTS:
        return True
    if not wrapper_text:
        return False
    lowered = wrapper_text.casefold()
    if "recon_waived" in lowered or "breadth_recon: false" in lowered:
        return False
    recon_signals = (
        "breadth recon",
        "unknown loci",
        "recon front-half",
        "recon_pending",
        "density_triage: recon",
    )
    return any(signal in lowered for signal in recon_signals)


def _parse_effects_manifest(wrapper_text: str | None) -> EffectsManifest | None:
    if not wrapper_text:
        return None
    try:
        data = json.loads(wrapper_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("effects_manifest")
    if not isinstance(raw, dict):
        return None
    try:
        return EffectsManifest.model_validate(raw)
    except Exception:
        return None


def _subagent_types(manifest: EffectsManifest | None) -> set[str]:
    if manifest is None:
        return set()
    section = manifest.surfaces.get(SUBAGENTS_SURFACE)
    if section is None:
        return set()
    types: set[str] = set()
    for entry in section.entries:
        target = (entry.target or "").strip().casefold()
        if target:
            types.add(target)
        detail = entry.detail
        if isinstance(detail, dict):
            subagent_type = detail.get("subagent_type")
            if isinstance(subagent_type, str) and subagent_type.strip():
                types.add(subagent_type.strip().casefold())
    return types


def _closeout_documents_recon_method(body: str) -> bool:
    if _RECON_METHOD_RE.search(body):
        return True
    section = extract_field_section(body, "recon_method")
    if section and section.strip():
        return True
    if _IN_SEAT_ANTI_TRIGGER_RE.search(body):
        return True
    return False


def breadth_recon_deviation(
    *,
    body: str,
    wrapper_text: str | None,
    contract: str | None = None,
) -> str | None:
    """Return advisory deviation token when breadth recon likely owed but Explore unused."""
    if not packet_owes_breadth_recon(wrapper_text=wrapper_text, contract=contract):
        return None
    if _closeout_documents_recon_method(body):
        return None
    manifest = _parse_effects_manifest(wrapper_text)
    if "explore" in _subagent_types(manifest):
        return None
    return _DEVIATION


def amend_breadth_recon_gaps(
    body: str,
    *,
    status: str,
    source: str,
    wrapper_text: str | None,
    contract: str | None = None,
) -> CloseoutRelayPayload:
    """Advisory-only: append deviation when Explore-default likely violated."""
    deviation = breadth_recon_deviation(
        body=body,
        wrapper_text=wrapper_text,
        contract=contract,
    )
    if deviation is None:
        return CloseoutRelayPayload(body=body, status=status, source=source)
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _append_deviation_tokens,
    )

    amended_body = _append_deviation_tokens(body, [deviation])
    relay_note = merge_relay_notes(
        deviation,
        "recon:advisory_only — anti-triggers (loci known, latency loop) are valid; "
        "document recon_method:in-seat + reason to suppress",
    )
    return CloseoutRelayPayload(
        body=amended_body,
        status=status,
        source=source,
        relay_note=relay_note,
    )


__all__ = [
    "amend_breadth_recon_gaps",
    "breadth_recon_deviation",
    "packet_owes_breadth_recon",
]
