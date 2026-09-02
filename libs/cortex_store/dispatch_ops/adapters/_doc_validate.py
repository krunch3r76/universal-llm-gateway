"""Cortex dispatch op: doc_validate — aggregate implement-ready preflight report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from implement_admission.closeout_helpers import workspaces_root
from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.gate_distillation import read_dense_spec_text
from implement_admission.implement_ready_gate import doc_validate_attestation_tokens

from .._doc_type_resolve import resolve_doc_type
from .._doc_validate_skeptic import evaluate_skeptic_grounding, find_skeptic_assertion
from .._doc_validate_support import (
    authoring_preflight_kwargs,
    derive_status,
    enrich_gates,
    err422,
    extract_spec_sha256_token,
    resolve_todo_preflight_kwargs,
)
from .._session_close_validate import (
    merge_session_close_payload,
    validate_session_close_payload,
)
from ._doc_template import _DOC_TYPE_REGISTRY


def _validate_dense_spec_doc(
    *,
    record: Any,
    requested_doc_type: str,
    text: str | None,
    path: str | None,
    source_ref: str | None,
    now_iso: str,
    **_: object,
) -> dict[str, Any]:
    provided = sum(1 for value in (text, path, source_ref) if value)
    if provided != 1:
        return err422("exactly one of text, path, or source_ref is required")

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
    schema = record.validator(spec_text)
    preflight_kwargs["now_iso"] = now_iso
    from implement_admission.implement_ready_preflight import preflight_implement_ready

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

    payload: dict[str, Any] = {
        "ok": status == "pass",
        "status": status,
        "doc_type": requested_doc_type,
        "spec_sha256": spec_sha256,
        "attested": attested,
        "pinned_sha256": pinned_sha256,
        "gates": gates,
        "skeptic": skeptic,
        "admitted": report.admitted,
        "summary": report.summary,
        "template_version": record.template_version,
        **({"first_failure": report.first_failure} if report.first_failure else {}),
        **(
            {"resolution": preflight_kwargs.get("resolution")}
            if preflight_kwargs.get("resolution")
            else {}
        ),
    }
    if status == "pass":
        payload["attestation_tokens"] = doc_validate_attestation_tokens(
            doc_type="implement_dense_spec",
            spec_text=spec_text,
        )
    return payload


def _validate_session_close_doc(
    *,
    record: Any,
    resolved: Any,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    from .._session_summary_path import resolve_session_summary_md

    payload = merge_session_close_payload(kwargs)
    resolved_md, path_err = resolve_session_summary_md(
        session_summary_md=payload.get("session_summary_md")
        if isinstance(payload.get("session_summary_md"), str)
        else None,
        session_summary_md_path=payload.get("session_summary_md_path")
        if isinstance(payload.get("session_summary_md_path"), str)
        else None,
    )
    if path_err is not None:
        return err422(str(path_err.get("error") or path_err))
    if resolved_md is not None:
        payload["session_summary_md"] = resolved_md

    missing = [
        field
        for field in ("session_id", "agent", "session_summary_md", "summary")
        if not payload.get(field)
    ]
    if missing:
        return err422(
            f"session_close doc_validate requires close payload fields: {missing} — "
            "pass them as flat top-level kwargs alongside doc_type "
            "(session_id, agent, session_summary_md|session_summary_md_path, "
            "summary, …); text/path/source_ref apply only to implement_dense_spec; "
            "for session_close, text may be a JSON object with the same keys"
        )

    verdict = validate_session_close_payload(payload)
    status = "pass" if verdict.passed else "preflight_failed"
    result: dict[str, Any] = {
        "ok": verdict.passed,
        "status": status,
        "doc_type": resolved.requested,
        "gates": verdict.gates,
        "audit": verdict.preflight.get("audit"),
        "warnings": verdict.preflight.get("warnings") or [],
        "preflight": {
            key: verdict.preflight.get(key)
            for key in ("turn_count", "byte_count", "transcript_depth")
            if key in verdict.preflight
        },
        "template_version": record.template_version,
        **({"reason": verdict.reason} if verdict.reason else {}),
        **({"variant": resolved.variant} if resolved.variant else {}),
    }
    return result


def _op_doc_validate(
    doc_type: str = "implement_dense_spec",
    text: str | None = None,
    path: str | None = None,
    source_ref: str | None = None,
    **kwargs: object,
) -> dict[str, Any]:
    """Aggregate implement-ready gate report over resolved dense-spec bytes."""
    resolved = resolve_doc_type(doc_type, _DOC_TYPE_REGISTRY)
    if resolved is None:
        supported = ", ".join(sorted(_DOC_TYPE_REGISTRY))
        return err422(f"unknown doc_type {doc_type!r}; supported: {supported}")

    record = resolved.record
    if record.side_effect_binding == "session_close":
        return _validate_session_close_doc(
            record=record,
            resolved=resolved,
            kwargs=dict(kwargs),
        )

    now_iso = datetime.now(UTC).isoformat()
    return _validate_dense_spec_doc(
        record=record,
        requested_doc_type=resolved.requested,
        text=text,
        path=path,
        source_ref=source_ref,
        now_iso=now_iso,
    )


__all__ = ["_op_doc_validate"]
