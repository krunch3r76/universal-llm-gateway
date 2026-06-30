"""Cortex dispatch op: doc_validate — aggregate implement-ready preflight report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from implement_admission.closeout_helpers import workspaces_root
from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.gate_distillation import read_dense_spec_text
from implement_admission.implement_ready_preflight import preflight_implement_ready

from ._doc_validate_skeptic import evaluate_skeptic_grounding, find_skeptic_assertion
from ._doc_validate_support import (
    authoring_preflight_kwargs,
    derive_status,
    enrich_gates,
    err422,
    extract_spec_sha256_token,
    resolve_todo_preflight_kwargs,
)
from .ops_doc_template import _SUPPORTED_DOC_TYPES


def _op_doc_validate(
    doc_type: str = "implement_dense_spec",
    text: str | None = None,
    path: str | None = None,
    source_ref: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Aggregate implement-ready gate report over resolved dense-spec bytes."""
    normalized = (doc_type or "").strip()
    if normalized not in _SUPPORTED_DOC_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_DOC_TYPES))
        return err422(f"unknown doc_type {doc_type!r}; supported: {supported}")

    provided = sum(1 for value in (text, path, source_ref) if value)
    if provided != 1:
        return err422("exactly one of text, path, or source_ref is required")

    now_iso = datetime.now(UTC).isoformat()
    has_todo_context = source_ref is not None
    todo_id: str | None = None
    preflight_kwargs: dict[str, Any]

    if text is not None:
        spec_text = text
        preflight_kwargs = authoring_preflight_kwargs(
            spec_text=spec_text, spec_uri=None
        )
    elif path is not None:
        spec_text = read_dense_spec_text(path.strip())
        if spec_text is None:
            return err422(f"could not read dense spec at {path!r}")
        preflight_kwargs = authoring_preflight_kwargs(
            spec_text=spec_text, spec_uri=path.strip()
        )
    else:
        assert source_ref is not None
        resolved = resolve_todo_preflight_kwargs(source_ref, now_iso=now_iso)
        if "error" in resolved:
            return resolved
        todo_id = resolved["todo_id"]
        spec_text = resolved.get("dense_spec_text")
        if spec_text is None:
            return err422(
                f"could not resolve dense spec bytes for {source_ref!r} — "
                "ensure the implement-ready assertion cites a readable spec path"
            )
        preflight_kwargs = {**resolved, "now_iso": now_iso}

    spec_sha256 = dense_spec_hash_uri(spec_text)
    schema = validate_dense_spec(spec_text)
    preflight_kwargs["now_iso"] = now_iso
    report = preflight_implement_ready(**preflight_kwargs)
    gates = enrich_gates([g.to_dict() for g in report.gates], spec_text)

    assertion = preflight_kwargs.get("assertion") if has_todo_context else None
    evidence = assertion.get("evidence_uris") if isinstance(assertion, dict) else None
    pinned_sha256 = (
        extract_spec_sha256_token(evidence if isinstance(evidence, list) else None)
        if has_todo_context
        else None
    )
    attested = bool(has_todo_context and pinned_sha256 and pinned_sha256 == spec_sha256)

    gate10_failed = any(
        g.get("gate") == 10 and g.get("status") == "failed" for g in gates
    )
    gate13_failed = any(
        g.get("gate") == 13 and g.get("status") == "failed" for g in gates
    )

    if (
        has_todo_context
        and todo_id
        and preflight_kwargs.get("density_triage") == "judgment_required"
    ):
        skeptic_assertion = find_skeptic_assertion(
            todo_id=todo_id,
            spec_hash_uri=spec_sha256,
            now_iso=now_iso,
        )
        skeptic = evaluate_skeptic_grounding(
            skeptic_assertion=skeptic_assertion,
            ws_root=workspaces_root(),
        )
        skeptic["ratified"] = bool(preflight_kwargs.get("skeptic_ratified"))
    else:
        skeptic = {
            "ratified": None,
            "evidence_grounded": None,
            "evidence_unresolved": None,
            "evidence_mode": None,
            "deferred_to_stargate": True,
        }

    status = derive_status(
        preflight_admitted=report.admitted,
        schema_passed=schema.passed,
        pinned_sha256=pinned_sha256,
        spec_sha256=spec_sha256,
        has_todo_context=has_todo_context,
        gate10_failed=gate10_failed,
        gate13_failed=gate13_failed,
        skeptic=skeptic,
    )

    return {
        "ok": status == "pass",
        "status": status,
        "doc_type": normalized,
        "spec_sha256": spec_sha256,
        "attested": attested,
        "pinned_sha256": pinned_sha256,
        "gates": gates,
        "skeptic": skeptic,
        "admitted": report.admitted,
        "summary": report.summary,
        **({"first_failure": report.first_failure} if report.first_failure else {}),
        **(
            {"resolution": preflight_kwargs.get("resolution")}
            if preflight_kwargs.get("resolution")
            else {}
        ),
    }


__all__ = ["_op_doc_validate"]
