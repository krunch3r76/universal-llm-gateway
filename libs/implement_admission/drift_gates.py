"""Warn→enforce drift gates for unified implement admission (Step 5)."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

from implement_admission.admission_read import read_packet
from implement_admission.closeout import verify_evidence_uris
from implement_admission.closeout_models import (
    AdapterResult,
    ImplementCloseout,
)
from implement_admission.closeout_runtime import get_runtime
from implement_admission.normalize import normalize
from implement_admission.review_attestation import review_attestation_findings
from implement_admission.routing import classify_risk_tier
from implement_admission.source_ref import SourceRefError
from implement_admission.spec import (
    ImplementSpec,
    Source,
    implement_spec_hash,
)

logger = get_logger(__name__)

_CONFIG_ENTITY = "config:unified-admission-drift-gates"
_GATE_ATTR = {
    "a": "gate_a",
    "a2": "gate_a2",
    "b": "gate_b",
    "c": "gate_c",
    "d": "gate_d",
    "ra": "gate_ra",
}
_ENV_KEYS = {
    "a": "UA_DRIFT_GATE_A",
    "a2": "UA_DRIFT_GATE_A2",
    "b": "UA_DRIFT_GATE_B",
    "c": "UA_DRIFT_GATE_C",
    "d": "UA_DRIFT_GATE_D",
    "ra": "UA_DRIFT_GATE_RA",
}
_CACHE_TTL_S = 30.0

_cache: dict[str, Any] = {"loaded_at": 0.0, "values": {}}


class DriftGateState(StrEnum):
    OFF = "off"
    WARN = "warn"
    ENFORCE = "enforce"


DriftGateAction = Literal["noop", "warn", "reject"]


@dataclass(frozen=True, slots=True)
class DriftGateResult:
    gate_id: str
    tripped: bool
    action: DriftGateAction
    reason: str | None = None
    detail: str | None = None
    stored: str | None = None
    recomputed: str | None = None


def clear_gate_state_cache() -> None:
    """Test hook — bust the config TTL cache."""
    _cache["loaded_at"] = 0.0
    _cache["values"] = {}


def _parse_state(raw: str | None) -> DriftGateState:
    if raw is None:
        return DriftGateState.WARN
    normalized = raw.strip().lower()
    for state in DriftGateState:
        if normalized == state.value:
            return state
    return DriftGateState.WARN


def _load_config_values() -> dict[str, str]:
    now = time.monotonic()
    if now - float(_cache["loaded_at"]) < _CACHE_TTL_S and _cache["values"]:
        return dict(_cache["values"])

    values: dict[str, str] = {}
    try:
        rt = get_runtime()
        resp = rt.dispatch(
            "entity_get", {"entity_id": _CONFIG_ENTITY, "intent": "full"}
        )
        attrs = resp.get("attributes") if isinstance(resp, dict) else None
        if isinstance(attrs, dict):
            for gate_id, attr in _GATE_ATTR.items():
                raw = attrs.get(attr)
                if isinstance(raw, str):
                    values[gate_id] = raw
    except Exception:
        values = {}

    for gate_id, env_key in _ENV_KEYS.items():
        if gate_id not in values:
            env_val = os.environ.get(env_key)
            if env_val:
                values[gate_id] = env_val

    _cache["loaded_at"] = now
    _cache["values"] = values
    return dict(values)


def gate_state(gate_id: str) -> DriftGateState:
    """Read tri-state for gate ``a``/``a2``/``b``/``c``/``ra`` (config + env fallback)."""
    key = gate_id.removeprefix("gate_").lower()
    values = _load_config_values()
    return _parse_state(values.get(key))


def evaluate_drift_gate(
    gate_id: str,
    state: DriftGateState,
    *,
    tripped: bool,
    reason: str | None = None,
    detail: str | None = None,
    stored: str | None = None,
    recomputed: str | None = None,
) -> DriftGateResult:
    if state == DriftGateState.OFF or not tripped:
        return DriftGateResult(
            gate_id=gate_id,
            tripped=tripped,
            action="noop",
            reason=reason,
            detail=detail,
            stored=stored,
            recomputed=recomputed,
        )

    action: DriftGateAction = "reject" if state == DriftGateState.ENFORCE else "warn"
    event = f"drift_gate.{gate_id}.{reason or 'trip'}"
    logger.warning(
        "%s action=%s detail=%s stored=%s recomputed=%s",
        event,
        action,
        detail,
        stored,
        recomputed,
    )
    return DriftGateResult(
        gate_id=gate_id,
        tripped=True,
        action=action,
        reason=reason,
        detail=detail or event,
        stored=stored,
        recomputed=recomputed,
    )


def check_bound_source_ref(
    *,
    source_ref: str | None,
    packet_frontmatter_source_ref: str | None = None,
) -> DriftGateResult:
    state = gate_state("a")
    param_present = bool(source_ref and source_ref.strip())
    fm_present = bool(
        packet_frontmatter_source_ref and packet_frontmatter_source_ref.strip()
    )
    missing = not (param_present or fm_present)
    return evaluate_drift_gate(
        "a",
        state,
        tripped=missing,
        reason="miss" if missing else None,
        detail="bound implement handoff missing source_ref",
    )


def check_frontmatter_source_ref(
    *,
    packet_frontmatter_source_ref: str | None,
) -> DriftGateResult:
    state = gate_state("a2")
    missing = not (
        packet_frontmatter_source_ref and packet_frontmatter_source_ref.strip()
    )
    return evaluate_drift_gate(
        "a2",
        state,
        tripped=missing,
        reason="miss" if missing else None,
        detail="packet frontmatter lacks source_ref",
    )


def detect_hash_drift(
    spec: ImplementSpec,
    *,
    on_disk_sha256: str | None = None,
) -> tuple[bool, str | None, str | None]:
    stored = spec.provenance.implement_spec_hash
    recomputed = implement_spec_hash(spec)
    if stored and recomputed != stored:
        return True, stored, recomputed
    expected_packet = spec.source.source_version.packet_sha256
    if expected_packet and on_disk_sha256 and expected_packet != on_disk_sha256:
        return True, expected_packet, on_disk_sha256
    return False, stored, recomputed


def check_packet_hash_drift(
    spec: ImplementSpec,
    *,
    on_disk_sha256: str | None = None,
) -> DriftGateResult:
    drift, stored, recomputed = detect_hash_drift(spec, on_disk_sha256=on_disk_sha256)
    return evaluate_drift_gate(
        "b",
        gate_state("b"),
        tripped=drift,
        reason="drift" if drift else None,
        detail="implement_spec_hash or packet_sha256 mismatch",
        stored=stored,
        recomputed=recomputed,
    )


def _resolve_materialized_spec(
    source_ref: str,
    *,
    workspaces_root: Path | None = None,
) -> ImplementSpec:
    rt = get_runtime()

    class _Reader:
        def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
            return rt.dispatch("entity_get", {"entity_id": entity_id, **kwargs})

    return normalize(
        source_ref,
        cortex=_Reader(),
        workspaces_root=workspaces_root,
    )


def _is_packet_closeout(source_ref: str) -> bool:
    return source_ref.lower().startswith("packet:")


def check_closeout_hash_drift(
    closeout: ImplementCloseout,
    *,
    workspaces_root: Path | None = None,
) -> DriftGateResult:
    if _is_packet_closeout(closeout.source_ref):
        return DriftGateResult(
            gate_id="b",
            tripped=False,
            action="noop",
            detail="closeout hash gate skipped: packet lane is pass-through",
        )
    try:
        spec = _resolve_materialized_spec(
            closeout.source_ref, workspaces_root=workspaces_root
        )
    except SourceRefError:
        return DriftGateResult(
            gate_id="b",
            tripped=False,
            action="noop",
            detail="closeout hash gate skipped: source_ref not re-resolvable",
        )
    on_disk_sha256 = closeout.packet_sha256
    if on_disk_sha256 is None:
        try:
            packet = read_packet(
                f"packet:{closeout.source_ref}",
                workspaces_root=workspaces_root,
            )
            on_disk_sha256 = packet.packet_sha256
        except Exception:
            materialized = (
                Path(workspaces_root or Path("/mnt/torus/projects"))
                / "universal-llm-gateway"
                / "tmp"
                / "implement-admission"
                / "materialized"
            )
            slug = closeout.source_ref.replace(":", "-").replace("/", "-")[:80]
            candidate = materialized / f"implement-{slug}.md"
            if candidate.is_file():
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                on_disk_sha256 = f"sha256:{digest}"
    return check_packet_hash_drift(spec, on_disk_sha256=on_disk_sha256)


def evaluate_gate_c_checks(
    results: list[AdapterResult],
    *,
    source: Source,
    closeout: ImplementCloseout,
) -> tuple[bool, str | None]:
    primary_kind = (
        source.source_kind.value
        if hasattr(source.source_kind, "value")
        else str(source.source_kind)
    )
    primary = next((ar for ar in results if ar.adapter == primary_kind), None)
    verified = verify_evidence_uris(closeout.evidence_uris)
    checks = {
        "empty_results": len(results) == 0,
        "primary_missing": primary is None,
        "primary_failed": primary is not None and primary.status == "failed",
        "evidence_digest_mismatch": bool(verified.mismatch),
        "no_evidence": primary is None
        or primary.mutation is None
        or not verified.admitted,
    }
    for reason, tripped in checks.items():
        if tripped:
            return True, reason
    return False, None


def check_closeout_evidence(
    results: list[AdapterResult],
    *,
    source: Source,
    closeout: ImplementCloseout,
) -> DriftGateResult:
    tripped, reason = evaluate_gate_c_checks(results, source=source, closeout=closeout)
    return evaluate_drift_gate(
        "c",
        gate_state("c"),
        tripped=tripped,
        reason=reason,
        detail=f"closeout evidence gate: {reason}" if reason else None,
    )


def apply_closeout_gate_c(
    closeout: ImplementCloseout,
    results: list[AdapterResult],
    *,
    source: Source,
) -> ImplementCloseout:
    from implement_admission.spec import CloseoutStatus

    result = check_closeout_evidence(results, source=source, closeout=closeout)
    if result.action == "reject":
        deviation = result.detail or result.reason or "drift_gate_c"
        return closeout.model_copy(
            update={
                "status": CloseoutStatus.FAILED,
                "deviations": [*closeout.deviations, deviation],
            }
        )
    return closeout


def apply_closeout_gate_b(
    closeout: ImplementCloseout,
    *,
    workspaces_root: Path | None = None,
) -> ImplementCloseout:
    from implement_admission.spec import CloseoutStatus

    result = check_closeout_hash_drift(closeout, workspaces_root=workspaces_root)
    if result.action != "reject":
        return closeout
    deviation = result.detail or "drift_gate_b_closeout"
    status = closeout.status
    if status == CloseoutStatus.COMPLETE:
        status = CloseoutStatus.PARTIAL
    return closeout.model_copy(
        update={
            "status": status,
            "deviations": [*closeout.deviations, deviation],
        }
    )


def check_review_attestation(
    spec: ImplementSpec,
    *,
    headless_vs_human: Literal["headless", "human"] = "human",
) -> DriftGateResult:
    """Gate ``ra`` — tripped when any rejectable finding is present."""
    findings = review_attestation_findings(spec)
    rejectable = [f for f in findings if f.rejectable_under_enforce]
    tripped = bool(rejectable)
    state = gate_state("ra")
    would_reject = tripped and state == DriftGateState.ENFORCE

    att = spec.provenance.review_attestation
    req_tier = classify_risk_tier(spec)
    spec_hash = implement_spec_hash(spec)
    att_spec_hash = att.spec_hash if att else None
    codes = [f.code.value for f in findings]

    logger.warning(
        "review_attestation.eval codes=%s risk_tier=%s spec_hash=%s "
        "attestation_spec_hash=%s would_reject=%s gate_state=%s headless_vs_human=%s",
        codes,
        req_tier,
        spec_hash,
        att_spec_hash,
        would_reject,
        state.value,
        headless_vs_human,
    )

    reason = rejectable[0].code.value if rejectable else None
    return evaluate_drift_gate(
        "ra",
        state,
        tripped=tripped,
        reason=reason,
        detail=f"review attestation gate: {reason}" if reason else None,
    )
