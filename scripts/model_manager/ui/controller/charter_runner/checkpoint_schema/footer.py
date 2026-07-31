"""``charter-state`` footer: emit fenced JSON, parse, and validate.

Phase 0 coordination channel — footer JSON only; zero regex over CHECKPOINT prose
for machine facts. ``append_footer_to_packet`` is the sole footer author for
materializer dual-write until ``packets/`` cutover (Phase 3).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypedDict

FOOTER_FENCE = "charter-state"

_VALID_STATUSES = frozenset(
    {"CHECKPOINT", "CONSULT_PENDING", "BLOCKED", "WORKER_FAILED", "CLOSED"}
)

_FENCE_RE = re.compile(
    rf"```\s*{re.escape(FOOTER_FENCE)}\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

_FENCE_TAIL_RE = re.compile(
    rf"\n?```\s*{re.escape(FOOTER_FENCE)}\s*\n.*?\n```\s*$",
    re.DOTALL | re.IGNORECASE,
)

# Worker CHECKPOINTs with empty gated Next-pickup prose must still emit non-null
# strings — null fails ``_validate_next_pickup`` (live 6518 w1 / 6489 hold).
EMPTY_GATED_PICKUP_SENTINEL: dict[str, str] = {
    "gid": "none",
    "lane": "none",
    "executor": "none",
}


class NextPickupFields(TypedDict):
    """Gated pickup row identity for the charter-state footer."""

    gid: str
    lane: str
    executor: str


class ConsultFields(TypedDict, total=False):
    """Consult lane state when CONSULT_PENDING or consult queue is active."""

    role: str | None
    poll_hint: str | None
    from_: str | None  # JSON key is "from"


class EvidenceItem(TypedDict):
    """One harvest evidence pointer (URI + content hash)."""

    uri: str
    sha256: str


class FooterFields(TypedDict):
    """Full ``charter-state`` footer payload per spec §C.3 (+ window correlation)."""

    schema_version: int
    status: str
    next_pickup: NextPickupFields
    wip: dict[str, Any] | None
    consult: dict[str, Any]
    revise_count: int
    evidence: list[EvidenceItem]
    window_id: str
    transition_id: str | None


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of ``validate_checkpoint_footer``."""

    ok: bool
    errors: tuple[str, ...] = field(default_factory=tuple)


def output_format_footer_requirement(*, window_id: str = "") -> str:
    """Return-contract text for packet ``<output_format>`` and materializer output."""
    wid_clause = (
        f"window_id must be `{window_id}`."
        if window_id
        else "window_id must match this window (charter-{root_id}-w{window_index})."
    )
    return (
        "Append exactly one ```charter-state``` fenced JSON block at the end of the "
        "CHECKPOINT body. Required fields (identical to the inbound packet footer "
        "schema §C.3): schema_version, status, next_pickup {gid, lane, executor}, "
        "wip, consult {role, poll_hint, from}, revise_count, evidence "
        "[{uri, sha256}], window_id, transition_id. Populate status, next_pickup, "
        f"wip, consult, and evidence from the CHECKPOINT you post; {wid_clause} "
        "Phase-0 rule: set wip to null (never a bare string or cross-root window "
        "id); cross-root dependencies belong in next_pickup / Next pickup — invalid "
        "wip fail-closes harvest; remedy is author reseed, not runner heal. "
        "When gated Next-pickup prose is empty, set next_pickup to "
        '{\"gid\":\"none\",\"lane\":\"none\",\"executor\":\"none\"} — '
        "never JSON null for gid/lane/executor."
    )


def footer_kwargs_for_window(
    root_id: str,
    window_index: int,
    *,
    status: str = "CHECKPOINT",
) -> dict[str, Any]:
    """Build default Phase-0 footer kwargs for a charter window dispatch packet."""
    return {
        "schema_version": 1,
        "status": status,
        "next_pickup": {
            "gid": "pending",
            "lane": "judgment",
            "executor": "pending",
        },
        "wip": None,
        "consult": {"role": None, "poll_hint": None, "from": None},
        "revise_count": 0,
        "evidence": [],
        "window_id": f"charter-{root_id}-w{window_index}",
        "transition_id": None,
    }


def emit_footer(
    *,
    schema_version: int = 1,
    status: str,
    next_pickup: dict[str, str],
    wip: dict[str, Any] | None,
    consult: dict[str, Any],
    revise_count: int,
    evidence: list[dict[str, str]],
    window_id: str,
    transition_id: str | None,
) -> str:
    """Return a markdown fenced ``charter-state`` block ready to append to a packet."""
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "status": status,
        "next_pickup": next_pickup,
        "wip": wip,
        "consult": consult,
        "revise_count": revise_count,
        "evidence": evidence,
        "window_id": window_id,
        "transition_id": transition_id,
    }
    body = json.dumps(payload, indent=2, sort_keys=True)
    return f"```{FOOTER_FENCE}\n{body}\n```\n"


def append_footer_to_packet(packet_body: str, **footer_kwargs: Any) -> str:
    """Strip any existing footer and append exactly one ``charter-state`` fence."""
    trimmed = _FENCE_TAIL_RE.sub("", packet_body.rstrip())
    footer = emit_footer(**footer_kwargs)
    return f"{trimmed}\n\n{footer}"


def _extract_footer_json(body: str) -> tuple[dict[str, Any] | None, str | None]:
    match = _FENCE_RE.search(body)
    if not match:
        return None, "charter-state fence missing"
    raw = match.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"charter-state JSON malformed: {exc.msg}"
    if not isinstance(parsed, dict):
        return None, "charter-state root must be object"
    return parsed, None


def _require_str(data: dict[str, Any], key: str, errors: list[str]) -> str | None:
    val = data.get(key)
    if not isinstance(val, str) or not val.strip():
        errors.append(f"{key}")
        return None
    return val


def _require_int(data: dict[str, Any], key: str, errors: list[str]) -> int | None:
    val = data.get(key)
    if not isinstance(val, int) or isinstance(val, bool):
        errors.append(f"{key}")
        return None
    return val


def _validate_next_pickup(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("next_pickup")
        return
    for sub in ("gid", "lane", "executor"):
        if not isinstance(value.get(sub), str) or not str(value.get(sub)).strip():
            errors.append(f"next_pickup.{sub}")


def _validate_consult(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("consult")
        return
    for sub in ("role", "poll_hint", "from"):
        if sub not in value:
            errors.append(f"consult.{sub}")
        elif value[sub] is not None and not isinstance(value[sub], str):
            errors.append(f"consult.{sub}")


def _validate_evidence(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("evidence")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"evidence[{idx}]")
            continue
        for sub in ("uri", "sha256"):
            if not isinstance(item.get(sub), str) or not str(item.get(sub)).strip():
                errors.append(f"evidence[{idx}].{sub}")


def _next_pickup_is_empty_sentinel(next_pickup: Any) -> bool:
    if not isinstance(next_pickup, dict):
        return False
    return all(
        str(next_pickup.get(key) or "").strip().lower() == "none"
        for key in ("gid", "lane", "executor")
    )


def is_exhausted_hopper_footer(body: str) -> bool:
    """True when a valid footer marks arc exhaustion (none/none/none sentinel).

    Caller must still require ungated Next-pickup prose, no live WIP, and no
    ledger ``wip_window_id`` before treating the root as close-eligible.
    """
    if not validate_checkpoint_footer(body).ok:
        return False
    data, _ = _extract_footer_json(body)
    if data is None:
        return False
    status = data.get("status")
    if status not in {"CHECKPOINT", "CLOSED"}:
        return False
    if not _next_pickup_is_empty_sentinel(data.get("next_pickup")):
        return False
    return data.get("wip") is None


def validate_checkpoint_footer(body: str) -> ValidationResult:
    """Extract and validate the fenced ``charter-state`` JSON; name field paths."""
    data, err = _extract_footer_json(body)
    if data is None:
        return ValidationResult(ok=False, errors=(err or "charter-state invalid",))

    errors: list[str] = []
    version = _require_int(data, "schema_version", errors)
    if version is not None and version != 1:
        errors.append("schema_version")

    status = data.get("status")
    if not isinstance(status, str) or status not in _VALID_STATUSES:
        errors.append("status")

    _validate_next_pickup(data.get("next_pickup"), errors)

    wip = data.get("wip")
    if wip is not None and not isinstance(wip, dict):
        errors.append("wip")

    _validate_consult(data.get("consult"), errors)
    _require_int(data, "revise_count", errors)
    _validate_evidence(data.get("evidence"), errors)
    _require_str(data, "window_id", errors)

    transition_id = data.get("transition_id")
    if transition_id is not None and not isinstance(transition_id, str):
        errors.append("transition_id")

    return ValidationResult(ok=not errors, errors=tuple(errors))


__all__ = [
    "EMPTY_GATED_PICKUP_SENTINEL",
    "FOOTER_FENCE",
    "FooterFields",
    "ValidationResult",
    "append_footer_to_packet",
    "emit_footer",
    "footer_kwargs_for_window",
    "is_exhausted_hopper_footer",
    "output_format_footer_requirement",
    "validate_checkpoint_footer",
]
