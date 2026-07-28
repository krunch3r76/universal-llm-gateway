"""Select operator-facing CLOSEOUT payload for cursor-auto relay.

Proxy §2 wants ``ac_verdict`` / ``deltas_to_spec`` etc. GIW's cursor-sdk bus
turn is a machine capture/manifest that shares the name "closeout". Prefer an
authored §2 body (usually the repo sidecar) when present; otherwise synthesize
§2 from the wrapper manifest so the operator never receives raw JSON alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
    _as_str_list,
    _order_preserving_dedup,
    _table_cell,
    is_wrapper_manifest,
    looks_section2,
    status_from_section2,
    strip_machine_tail,
    wrapper_status,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex import (
    apply_write_fence,
    run_cortex_scan,
)
from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
    amend_effects_underclaim,
    machine_write_uris,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    enforce_synthesized_partial,
)

_SIDECAR_REL_DIR = "tmp/reviews/closeouts"
_MAX_EXECUTOR_EXCERPT_CHARS = 1500


def _finalize(
    payload: CloseoutRelayPayload,
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str] | None = None,
) -> CloseoutRelayPayload:
    """Run honesty amend and optional confer write-fence on every selector exit."""
    amended = amend_effects_underclaim(
        payload.body,
        wrapper_text=wrapper_text,
        status=payload.status,
        source=payload.source,
    )
    if not guard_uris:
        return amended
    return apply_write_fence(
        amended,
        wrapper_text=wrapper_text,
        guard_uris=guard_uris,
    )


def synthesize_section2(
    *,
    wrapper_text: str | None,
    sidecar_text: str | None,
    dispatch_id: str,
) -> str | None:
    """Build operator-facing §2 markdown from a machine wrapper manifest."""
    del dispatch_id  # reserved for future trace headers; selection uses sdk metadata
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return None
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    status = data.get("status", "partial")
    files_created = _as_str_list(data.get("files_created"))
    files_modified = _as_str_list(data.get("files_modified"))
    files_deleted = _as_str_list(data.get("files_deleted"))
    files_offgit_produced = _as_str_list(data.get("files_offgit_produced"))
    effects = _as_str_list(data.get("effects"))
    deviations = _as_str_list(data.get("deviations"))
    capture_status = data.get("capture_status")
    evidence_uris = data.get("evidence_uris")
    artifact_paths: list[str] = []
    if isinstance(evidence_uris, dict):
        artifact_paths = _as_str_list(evidence_uris.get("artifact_paths"))

    effects_union = machine_write_uris(wrapper_text)
    if not effects_union:
        effects_union = _order_preserving_dedup(
            effects,
            files_created,
            files_modified,
            files_deleted,
            files_offgit_produced,
        )

    ac_verdict = (
        "unauthored — executor emitted no §2 body; machine-derived envelope below. "
        "Not a pass."
    )
    if sidecar_text:
        excerpt = strip_machine_tail(sidecar_text).strip()
        if excerpt:
            if len(excerpt) > _MAX_EXECUTOR_EXCERPT_CHARS:
                excerpt = excerpt[:_MAX_EXECUTOR_EXCERPT_CHARS] + "…"
            ac_verdict = f"{ac_verdict}<br><br>{excerpt}"

    if effects_union:
        effects_cell = "<br>".join(f"- {item}" for item in effects_union)
    else:
        effects_cell = (
            f"none captured — capture_status={capture_status}; "
            'per §4.7 a schema-only read of "none" is not authority'
        )

    evidence_parts: list[str] = []
    if artifact_paths:
        evidence_parts.append("artifact_paths: " + ", ".join(artifact_paths))
    if deviations:
        evidence_parts.append("deviations: " + "; ".join(deviations))
    if capture_status is not None:
        evidence_parts.append(f"capture_status={capture_status}")
    evidence_cell = "; ".join(evidence_parts) if evidence_parts else "none"

    rows = (
        ("status", str(status)),
        ("ac_verdict", ac_verdict),
        ("deltas_to_spec", "unauthored — not reported by executor"),
        ("decisions_taken", "unauthored — not reported by executor"),
        ("effects", effects_cell),
        ("evidence", evidence_cell),
        ("next", "unauthored — operator must derive from effects above"),
        ("open forks", "unknown — executor emitted no §2"),
    )
    lines = [
        "TYPE: CLOSEOUT",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_table_cell(value)} |")
    return "\n".join(lines)


def ledger_status_to_closeout(terminal_status: str) -> str:
    """Map ledger terminal status → operator CLOSEOUT status line."""
    if terminal_status == "completed":
        return "complete"
    if terminal_status == "failed":
        return "blocked"
    return "partial"


def read_repo_closeout_sidecar(
    dispatch_id: str,
    *,
    source_repo: Path | None = None,
) -> str | None:
    """Read ``tmp/reviews/closeouts/{dispatch_id}.md`` when present."""
    root = source_repo if source_repo is not None else load_config().source_repo
    path = root / _SIDECAR_REL_DIR / f"{dispatch_id}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text or None


def select_closeout_relay_payload(
    *,
    sdk_body: str | None,
    sidecar_text: str | None,
    ledger_status: str,
    dispatch_id: str = "",
    cortex_root: Path | None = None,
    guard_uris: frozenset[str] | None = None,
) -> CloseoutRelayPayload:
    """Prefer authored §2; synthesize from wrapper manifest when absent.

    Selection order:
    1. Repo sidecar with §2 markers (machine tail stripped)
    2. Bus body when it itself looks like §2 (rare)
    3. Cortex promote or field-fill from wrapper ``cortex://`` URIs
    4. Synthesized §2 from wrapper manifest JSON
    5. Non-manifest bus body (plain prose)
    6. Empty placeholder

    All exits pass through ``_finalize`` for machine-over-prose honesty amend
    and optional confer write-fence when *guard_uris* is supplied.

    ``cortex_root`` defaults to ``cortex_files_root()`` when omitted. URIs with
    absolute paths, ``..`` segments, or targets outside ``cortex_root`` are
    skipped without raising. Cortex promote requires §2 markers and a dispatch
    bind; relayed status is clamped via ``enforce_synthesized_partial`` while
    the synthesized trust gate remains disabled.
    """
    fallback_status = ledger_status_to_closeout(ledger_status)
    wrapper_text = sdk_body

    if sidecar_text:
        prose = strip_machine_tail(sidecar_text)
        if looks_section2(prose):
            return _finalize(
                CloseoutRelayPayload(
                    body=prose,
                    status=status_from_section2(prose) or fallback_status,
                    source="section2_sidecar",
                ),
                wrapper_text=wrapper_text,
                guard_uris=guard_uris,
            )

    if sdk_body and looks_section2(sdk_body) and not is_wrapper_manifest(sdk_body):
        prose = strip_machine_tail(sdk_body)
        return _finalize(
            CloseoutRelayPayload(
                body=prose,
                status=status_from_section2(prose) or fallback_status,
                source="section2_bus",
            ),
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    root = cortex_root if cortex_root is not None else cortex_files_root()
    cortex_payload = run_cortex_scan(
        wrapper_text=sdk_body or "",
        dispatch_id=dispatch_id,
        cortex_root=root,
        fallback_status=fallback_status,
    )
    if cortex_payload is not None:
        return _finalize(
            cortex_payload,
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    synthesized = synthesize_section2(
        wrapper_text=sdk_body,
        sidecar_text=sidecar_text,
        dispatch_id=dispatch_id,
    )
    if synthesized is not None:
        source = "section2_synthesized"
        raw_status = wrapper_status(sdk_body or "") or fallback_status
        return _finalize(
            CloseoutRelayPayload(
                body=synthesized,
                status=enforce_synthesized_partial(
                    raw_status,
                    closeout_source=source,
                ),
                source=source,
            ),
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    if sdk_body and sdk_body.strip():
        return _finalize(
            CloseoutRelayPayload(
                body=sdk_body.strip(),
                status=fallback_status,
                source="wrapper",
            ),
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    return _finalize(
        CloseoutRelayPayload(
            body="(no cursor-sdk closeout body captured)",
            status=fallback_status,
            source="empty",
        ),
        wrapper_text=wrapper_text,
        guard_uris=guard_uris,
    )


__all__ = [
    "CloseoutRelayPayload",
    "is_wrapper_manifest",
    "ledger_status_to_closeout",
    "looks_section2",
    "machine_write_uris",
    "read_repo_closeout_sidecar",
    "select_closeout_relay_payload",
    "status_from_section2",
    "strip_machine_tail",
    "synthesize_section2",
    "wrapper_status",
]
