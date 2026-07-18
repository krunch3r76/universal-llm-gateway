"""Atomic Gate-2 distillation: implement-ready assertion + admission gate fields."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.density_triage_gate import (
    JUDGMENT_REQUIRED,
    MECHANICAL,
    RECON_PENDING,
    check_requested_bool,
    format_implement_triage_unknown_reason,
)
from implement_admission.gate_distillation import (
    GateDistillationInputs,
    prepare_gate_distillation,
    read_dense_spec_text,
)
from implement_admission.implement_ready import (
    ImplementReadyVerdict,
    evaluate_implement_ready,
)
from implement_admission.implement_ready_gate_resolve import (
    SkepticRatificationOutcome,
    resolve_skeptic_ratification,
)
from implement_admission.recon_waiver import (
    WaiverInfo,
    build_structured_waiver,
    parse_recon_waiver,
    resolve_effective_recon_waived,
    validate_recon_waive_reason_code,
)
from universal_logging import get_logger

from ..db import cortex_conn, query
from ..entity_aliases import resolve_entity_reference
from ..event_publisher import cortex_implement_recon_waived
from ..routes.assertions import _create_assertion_impl
from ._shared import record
from .ops_assertions import _op_assertions
from .ops_assertions_update import _op_assertion_get, _op_assertion_update
from .ops_entities import _op_entity_get, _op_entity_update

logger = get_logger("cortex-api.dispatch_ops.todo_gate_distillation")


class _DistillImplementReadyCortex:
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return _op_entity_get(entity_id=entity_id, **kwargs)

    def assertion_get(self, assertion_id: int) -> dict[str, Any]:
        return _op_assertion_get(assertion_id=assertion_id)

    def assertions(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return _op_assertions(entity_id=entity_id, **kwargs)


_DEFAULT_CLAIM = (
    "Implement-ready: Gate-2 attribute distillation wired — dense spec path and "
    "spec_sha256 cited; implement_ready_assertion_id stamped."
)


def _decode_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _nonempty_str_list(raw: Any, *, field: str) -> list[str] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out = [str(x).strip() for x in raw if str(x).strip()]
    if not out or len(out) != len(raw):
        return None
    return out


def _coerce_assertion_id(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _assertion_inactive(assertion: dict[str, Any], *, now_iso: str) -> bool:
    if assertion.get("superseded_by"):
        return True
    valid_until = assertion.get("valid_until")
    return bool(valid_until and str(valid_until) <= now_iso)


def _retract_assertion(assertion_id: int) -> dict[str, Any]:
    now_iso = datetime.now(UTC).isoformat()
    return _op_assertion_update(assertion_id=assertion_id, valid_until=now_iso)


def _load_assertion(conn: Any, assertion_id: int) -> dict[str, Any] | None:
    rows = query(
        conn,
        "SELECT id, entity_id, superseded_by, valid_until, evidence_uris "
        "FROM assertions WHERE id = ?",
        (assertion_id,),
    )
    if not rows:
        return None
    row = rows[0]
    evidence = row.get("evidence_uris")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence) if evidence else []
        except json.JSONDecodeError:
            evidence = []
    return {
        "entity_id": row["entity_id"],
        "superseded_by": row.get("superseded_by"),
        "valid_until": row.get("valid_until"),
        "evidence_uris": evidence if isinstance(evidence, list) else [],
    }


def _existing_gate_assertion_id(
    conn: Any,
    *,
    row: dict[str, Any],
    prior_attrs: dict[str, Any],
    prepared: GateDistillationInputs,
    expected: list[str],
    acs: list[str],
    triage: str,
    incoming_waiver: WaiverInfo | None,
) -> int | None:
    aid = _coerce_assertion_id(prior_attrs.get("implement_ready_assertion_id"))
    if aid is None:
        return None
    if str(row.get("source_uri") or "") != prepared.spec_path:
        return None
    if prior_attrs.get("density_triage") != triage:
        return None
    if prior_attrs.get("files_expected") != expected:
        return None
    if prior_attrs.get("acceptance_criteria") != acs:
        return None

    prior_waiver = parse_recon_waiver(prior_attrs.get("recon_waived"))
    if not (incoming_waiver or WaiverInfo(waived=False)).equivalent_to(prior_waiver):
        return None

    assertion = _load_assertion(conn, aid)
    if assertion is None:
        return None
    if _assertion_inactive(assertion, now_iso=datetime.now(UTC).isoformat()):
        return None
    evidence = assertion.get("evidence_uris") or []
    if prepared.spec_path not in evidence:
        return None
    if prepared.evidence_uris[-1] not in evidence:
        return None
    return aid


def _evaluate_from_persisted(
    *,
    entity_id: str,
    prepared: GateDistillationInputs,
    persisted_attrs: dict[str, Any] | None = None,
    persisted_source_uri: str | None = None,
    persisted_name: str | None = None,
) -> ImplementReadyVerdict:
    if persisted_attrs is not None:
        attrs = persisted_attrs
        source_uri = persisted_source_uri or prepared.spec_path
        entity_name = persisted_name
        spec_text = prepared.spec_text
    else:
        entity = _op_entity_get(entity_id=entity_id, intent="full")
        if "error" in entity:
            return ImplementReadyVerdict(
                admitted=False,
                code="entity_missing",
                reason=str(entity["error"]),
            )

        attrs = _decode_attributes(entity.get("attributes"))
        source_uri = entity.get("source_uri")
        entity_name = entity.get("name")
        spec_text = read_dense_spec_text(prepared.spec_path)

    aid = _coerce_assertion_id(attrs.get("implement_ready_assertion_id"))
    assertion: dict[str, Any] | None = None
    if aid is not None:
        loaded = _op_assertion_get(assertion_id=aid)
        if isinstance(loaded, dict) and "error" not in loaded:
            assertion = loaded

    raw_files = attrs.get("files_expected")
    files_expected = raw_files if isinstance(raw_files, list) else []
    raw_acs = attrs.get("acceptance_criteria")
    acceptance_criteria = raw_acs if isinstance(raw_acs, list) else []

    now_iso = datetime.now(UTC).isoformat()
    triage = (attrs.get("density_triage") or "").strip()
    spec_hash_uri = dense_spec_hash_uri(spec_text) if spec_text else None
    check_requested = check_requested_bool(attrs.get("check_requested"))
    if triage == "judgment_required" and check_requested:
        skeptic_outcome = resolve_skeptic_ratification(
            todo_id=entity_id,
            cortex=_DistillImplementReadyCortex(),
            now_iso=now_iso,
            spec_hash_uri=spec_hash_uri,
        )
    else:
        skeptic_outcome = SkepticRatificationOutcome(ratified=False)

    raw_waived = attrs.get("recon_waived")
    recon_waived, recon_waiver, stale_discarded = resolve_effective_recon_waived(
        raw_waived,
        spec_hash_uri,
    )
    if stale_discarded and recon_waiver is not None:
        cortex_implement_recon_waived(
            todo_id=entity_id,
            stale=True,
            stale_reason="spec_sha256_mismatch",
            **recon_waiver.event_payload(),
        )

    return evaluate_implement_ready(
        todo_id=entity_id,
        density_triage=attrs.get("density_triage"),
        source_uri=source_uri,
        implement_ready_assertion_id=aid,
        assertion=assertion,
        now_iso=now_iso,
        dense_spec_uri=prepared.spec_path,
        dense_spec_text=spec_text,
        files_expected=files_expected,
        acceptance_criteria=acceptance_criteria,
        entity_name=entity_name,
        skeptic_ratified=skeptic_outcome.ratified,
        recon_waived=recon_waived,
        check_requested=check_requested,
        skeptic_evidence_grounded=skeptic_outcome.evidence_grounded,
        skeptic_evidence_unresolved=skeptic_outcome.evidence_unresolved,
        skeptic_evidence_mode=skeptic_outcome.evidence_mode,
    )


def _success_payload(
    *,
    entity_id: str,
    prepared: GateDistillationInputs,
    assertion_id: int,
    expected: list[str],
    acs: list[str],
    idempotent: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "todo_id": entity_id,
        "source_uri": prepared.spec_path,
        "implement_ready_assertion_id": assertion_id,
        "evidence_uris": prepared.evidence_uris,
        "spec_sha256": prepared.evidence_uris[-1],
        "files_expected": expected,
        "acceptance_criteria": acs,
    }
    if idempotent:
        payload["idempotent"] = True
    return payload


def _incoming_waiver_from_params(
    *,
    recon_waive_reason_code: str | None,
    recon_waive_reason: str | None,
    waived_by: str,
    spec_sha256: str,
) -> WaiverInfo | None:
    code = (recon_waive_reason_code or "").strip()
    if not code:
        return None
    return build_structured_waiver(
        reason_code=code,
        reason=recon_waive_reason,
        waived_by=waived_by,
        spec_sha256=spec_sha256,
    )


def distill_todo_implement_gate(
    *,
    todo_id: str | None = None,
    files_expected: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    required_skills: list[str] | None = None,
    claim: str | None = None,
    evidence: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    seeded_by: str | None = None,
    density_triage: str | None = None,
    source_uri: str | None = None,
    recon_waive_reason_code: str | None = None,
    recon_waive_reason: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Atomically wire implement-admission gate fields at Gate-2 close."""
    if not todo_id:
        return {"error": "todo_id is required"}

    expected = _nonempty_str_list(files_expected, field="files_expected")
    if expected is None:
        return {
            "error": "files_expected must be a non-empty list[str] of non-empty strings"
        }
    acs = _nonempty_str_list(acceptance_criteria, field="acceptance_criteria")
    if acs is None:
        return {
            "error": "acceptance_criteria must be a non-empty list[str] of non-empty strings"
        }

    triage = (density_triage or JUDGMENT_REQUIRED).strip()
    if triage == RECON_PENDING:
        return {
            "error": (
                f"{todo_id}: recon_pending — re-triage to {JUDGMENT_REQUIRED} or "
                f"{MECHANICAL} before gate distillation"
            ),
            "code": "implement_blocked_recon_pending",
        }
    if triage not in {MECHANICAL, JUDGMENT_REQUIRED}:
        return {
            "error": format_implement_triage_unknown_reason(todo_id, density_triage),
            "code": "implement_triage_unknown",
        }

    reason_code_error = validate_recon_waive_reason_code(recon_waive_reason_code)
    if reason_code_error is not None:
        return reason_code_error

    with cortex_conn() as conn:
        try:
            resolved = resolve_entity_reference(conn, todo_id, label="entity")
        except HTTPException as exc:
            return {"error": exc.detail, "status_code": exc.status_code}

        rows = query(
            conn,
            "SELECT id, type, name, source_uri, attributes FROM entities WHERE id = ?",
            (resolved.entity_id,),
        )
        if not rows:
            return {"error": f"Entity not found: {resolved.entity_id}"}
        row = rows[0]
        if row["type"] != "todo":
            return {"error": f"{resolved.entity_id} is type {row['type']!r}, not todo"}

        prior_attrs = _decode_attributes(row.get("attributes"))
        spec_source = source_uri if source_uri is not None else row.get("source_uri")
        prepared = prepare_gate_distillation(
            todo_id=resolved.entity_id,
            source_uri=str(spec_source) if spec_source else None,
        )
        if isinstance(prepared, tuple):
            code, reason = prepared
            return {"error": reason, "code": code}

        waived_by = seeded_by or agent or "todo_distill_implement_gate"
        incoming_waiver = _incoming_waiver_from_params(
            recon_waive_reason_code=recon_waive_reason_code,
            recon_waive_reason=recon_waive_reason,
            waived_by=waived_by,
            spec_sha256=prepared.evidence_uris[-1],
        )

        existing_id = _existing_gate_assertion_id(
            conn,
            row=row,
            prior_attrs=prior_attrs,
            prepared=prepared,
            expected=expected,
            acs=acs,
            triage=triage,
            incoming_waiver=incoming_waiver,
        )
        if existing_id is not None:
            verdict = _evaluate_from_persisted(
                entity_id=resolved.entity_id,
                prepared=prepared,
            )
            if not verdict.admitted:
                return {
                    "ok": False,
                    "todo_id": resolved.entity_id,
                    "source_uri": prepared.spec_path,
                    "implement_ready_assertion_id": existing_id,
                    "gate_code": verdict.code,
                    "gate_reason": verdict.reason,
                }
            record(
                "cortex.todo_distill_implement_gate.completed",
                todo_id=resolved.entity_id,
                assertion_id=existing_id,
                idempotent=True,
            )
            return _success_payload(
                entity_id=resolved.entity_id,
                prepared=prepared,
                assertion_id=existing_id,
                expected=expected,
                acs=acs,
                idempotent=True,
            )

        skills = required_skills
        if skills is None:
            raw_skills = prior_attrs.get("required_skills")
            skills = raw_skills if isinstance(raw_skills, list) else None

        assert_body: dict[str, Any] = {
            "entity_id": resolved.entity_id,
            "claim": (claim or _DEFAULT_CLAIM).strip(),
            "confidence": "confirmed",
            "evidence": evidence or (claim or _DEFAULT_CLAIM),
            "derivation_type": "agent_observation",
            "evidence_uris": prepared.evidence_uris,
            "seeded_by": seeded_by or agent or "todo_distill_implement_gate",
        }
        try:
            assert_result = _create_assertion_impl(assert_body)
        except HTTPException as exc:
            return {"error": f"Assertion write failed: {exc.detail}", "step": "assert"}

        assertion_id = (assert_result.get("item") or {}).get("id")
        if not assertion_id:
            return {"error": "Assertion write returned no id", "step": "assert"}

        attr_patch: dict[str, Any] = {
            "density_triage": triage,
            "implement_ready_assertion_id": assertion_id,
            "files_expected": expected,
            "acceptance_criteria": acs,
        }
        if skills:
            attr_patch["required_skills"] = skills
        if incoming_waiver is not None:
            attr_patch["recon_waived"] = incoming_waiver.to_attr_json()

        update = _op_entity_update(
            entity_id=resolved.entity_id,
            source_uri=prepared.spec_path,
            attributes=attr_patch,
        )
        if "error" in update:
            retract = _retract_assertion(assertion_id)
            if "error" in retract:
                return {
                    "error": (
                        f"entity_update failed ({update['error']}) and assertion "
                        f"{assertion_id} retraction failed ({retract['error']})"
                    ),
                    "step": "retract",
                }
            record(
                "cortex.todo_distill_implement_gate.retracted",
                todo_id=resolved.entity_id,
                assertion_id=assertion_id,
                error=update["error"],
            )
            return {"error": update["error"], "step": "entity_update"}

        merged_attrs = {**prior_attrs, **attr_patch}
        verdict = _evaluate_from_persisted(
            entity_id=resolved.entity_id,
            prepared=prepared,
            persisted_attrs=merged_attrs,
            persisted_source_uri=prepared.spec_path,
            persisted_name=row.get("name"),
        )
        if not verdict.admitted:
            logger.warning(
                "todo_distill_implement_gate post-check rejected %s: %s",
                resolved.entity_id,
                verdict.reason,
            )
            return {
                "ok": False,
                "todo_id": resolved.entity_id,
                "source_uri": prepared.spec_path,
                "implement_ready_assertion_id": assertion_id,
                "evidence_uris": prepared.evidence_uris,
                "gate_code": verdict.code,
                "gate_reason": verdict.reason,
            }

        record(
            "cortex.todo_distill_implement_gate.completed",
            todo_id=resolved.entity_id,
            assertion_id=assertion_id,
        )
        if incoming_waiver is not None:
            cortex_implement_recon_waived(
                todo_id=resolved.entity_id,
                **incoming_waiver.event_payload(),
            )
        return _success_payload(
            entity_id=resolved.entity_id,
            prepared=prepared,
            assertion_id=assertion_id,
            expected=expected,
            acs=acs,
        )


__all__ = ["distill_todo_implement_gate"]
